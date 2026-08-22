from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import model_registry, vla_policy_worker
from workers import vla_policy_worker as isolated_vla_worker


def _checkpoint(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "config.json").write_text(
        json.dumps(
            {
                "type": "vla_jepa",
                "state_dim": 8,
                "action_dim": 7,
                "n_action_steps": 7,
                "input_features": {
                    "observation.images.exterior_1_left": {"shape": [3, 224, 224]},
                    "observation.images.exterior_2_left": {"shape": [3, 224, 224]},
                },
                "output_features": {"action": {"shape": [7]}},
                "enable_world_model": True,
                "pre_snap_gripper_action": True,
                "binarize_gripper_action": True,
            }
        ),
        encoding="utf8",
    )
    for name in (
        "model.safetensors",
        "policy_preprocessor.json",
        "policy_preprocessor_step_3_normalizer_processor.safetensors",
        "policy_postprocessor.json",
        "policy_postprocessor_step_2_unnormalizer_processor.safetensors",
    ):
        (root / name).write_bytes(b"worker-protocol-test")
    return root


def _isolate(monkeypatch: pytest.MonkeyPatch, checkpoint: Path) -> None:
    vla_policy_worker.stop()
    monkeypatch.setenv("VLA_JEPA_PYTHON", sys.executable)
    monkeypatch.setenv("ROBOTWORLD_DISABLE_LOCAL_RUNTIME_DEFAULTS", "1")
    monkeypatch.delenv("LEROBOT_REPO_PATH", raising=False)
    monkeypatch.delenv("ROBOTWORLD_ALLOW_MODEL_DOWNLOADS", raising=False)
    for name in model_registry.MODEL_PATH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ROBOTWORLD_MODEL_ROOTS", str(checkpoint.parent))


def test_worker_probe_is_a_real_isolated_process_and_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkpoint = _checkpoint(tmp_path / "checkpoint")
    _isolate(monkeypatch, checkpoint)
    try:
        result = vla_policy_worker.probe_checkpoint(str(checkpoint), "cpu")
        assert result["protocolVersion"] == "robotworld.vla-worker.v1"
        assert result["readyForLoad"] is False
        assert result["offlineMode"] is True
        assert result["checkpoint"]["weightBytes"] == len(b"worker-protocol-test")
        assert result["checkpoint"]["stateFeaturePresent"] is False
        assert result["inferenceDependencies"]["qwen"]["required"] is True
        assert result["inferenceDependencies"]["qwen"]["availableLocally"] is False
        assert result["inferenceDependencies"]["vJepa2"]["required"] is False
        assert result["worker"]["running"] is True
        assert result["worker"]["pid"] != os.getpid()
        assert any("LEROBOT_REPO_PATH" in blocker for blocker in result["blockers"])
        assert any("lerobot" in blocker.lower() for blocker in result["blockers"])
    finally:
        vla_policy_worker.stop()
    assert vla_policy_worker.status()["running"] is False


def test_inference_request_keeps_artifacts_server_side_and_identifies_normalized_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, dict, float]] = []

    class FakeClient:
        def request(self, operation: str, payload: dict, *, timeout: float):
            requests.append((operation, payload, timeout))
            return {
                "normalizedAction": [0.0] * 7,
                "checkpointAction": [0.0] * 6 + [1.0],
                "actionDimension": 7,
                "outputStage": "checkpoint_postprocessed_droid_relative_action",
            }

        def status(self):
            return {"running": True, "loaded": True, "pid": 123}

    monkeypatch.setattr(vla_policy_worker, "_get_client", lambda: FakeClient())
    result = vla_policy_worker.infer_action(
        images={"observation.images.front": "D:/artifact/front.png"},
        state=None,
        instruction="Pick up the object",
        adapter_revision="franka-cartesian-delta-v1",
        normalization_revision="a" * 64,
    )
    assert result["normalizedAction"] == [0.0] * 7
    operation, payload, _ = requests[0]
    assert operation == "infer"
    assert payload["images"] == {"observation.images.front": "D:/artifact/front.png"}
    assert payload["allowedArtifactRoots"]
    assert "imageBytes" not in payload


def test_checkpoint_declared_normalized_action_clamp_is_bounded_and_auditable() -> None:
    bounded, clipped, maximum_delta = isolated_vla_worker._clip_normalized_action(
        [0.03, -0.1, 0.2, 0.0, 0.24, -0.01, 1.0016577243804932]
    )
    assert bounded == [0.03, -0.1, 0.2, 0.0, 0.24, -0.01, 1.0]
    assert clipped is True
    assert maximum_delta == pytest.approx(0.001657724380493164)
    with pytest.raises(RuntimeError, match="invalid normalized action"):
        isolated_vla_worker._clip_normalized_action([0.0] * 6 + [float("nan")])


def test_transformers_dependency_does_not_treat_metadata_only_directory_as_full_weights(tmp_path: Path) -> None:
    metadata = tmp_path / "qwen-metadata"
    metadata.mkdir()
    (metadata / "config.json").write_text("{}", encoding="utf8")
    result = isolated_vla_worker._local_transformers_dependency(str(metadata))
    assert result["availableLocally"] is False
    assert result["reason"] == "configured_metadata_only_directory"

    (metadata / "model.safetensors").write_bytes(b"weights")
    result = isolated_vla_worker._local_transformers_dependency(str(metadata))
    assert result["availableLocally"] is True
    assert result["reason"] == "configured_local_directory"


def test_worker_environment_does_not_inherit_provider_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-copy")
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "do-not-copy")
    monkeypatch.setenv("ROBOTWORLD_TEST_SECRET", "do-not-copy")
    worker_env = vla_policy_worker._worker_environment()
    assert "OPENAI_API_KEY" not in worker_env
    assert "BRIGHTDATA_API_TOKEN" not in worker_env
    assert "ROBOTWORLD_TEST_SECRET" not in worker_env
    assert worker_env["HF_HUB_OFFLINE"] == "1"
    assert worker_env["TRANSFORMERS_OFFLINE"] == "1"


def test_local_vla_load_reports_exact_worker_blockers_and_remains_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = _checkpoint(tmp_path / "models" / "checkpoint")
    _isolate(monkeypatch, checkpoint)
    try:
        with TestClient(app) as client:
            registered = client.post(
                "/api/models",
                json={
                    "displayName": "Worker boundary fixture",
                    "roles": ["vla_policy"],
                    "providerType": "local_path",
                    "localPath": str(checkpoint),
                    "expectedDevice": "cpu",
                },
            ).json()["result"]["model"]
            model_id = registered["id"]
            validated = client.post(f"/api/models/{model_id}/validate", json={})
            assert validated.status_code == 200
            assert validated.json()["result"]["model"]["lifecycleState"] == "AVAILABLE"

            probe = client.get(f"/api/models/{model_id}/worker-probe")
            assert probe.status_code == 200, probe.text
            assert probe.json()["readyForLoad"] is False
            assert any("LEROBOT_REPO_PATH" in blocker for blocker in probe.json()["blockers"])

            load = client.post(f"/api/models/{model_id}/load", json={})
            assert load.status_code == 409
            assert "isolated worker load failed" in str(load.json())
            assert "LEROBOT_REPO_PATH" in str(load.json())
            model = client.get(f"/api/models/{model_id}").json()
            assert model["lifecycleState"] == "AVAILABLE"
            assert model["healthStatus"] == "worker_unavailable"
    finally:
        vla_policy_worker.stop()
