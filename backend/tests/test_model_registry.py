from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import model_registry


def _isolate_model_roots(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    for name in model_registry.MODEL_PATH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ROBOTWORLD_MODEL_ROOTS", str(root))


def _vla_fixture(root: Path) -> Path:
    checkpoint = root / "vla-jepa-checkpoint"
    checkpoint.mkdir(parents=True)
    config = {
        "type": "vla_jepa",
        "state_dim": 8,
        "action_dim": 7,
        "n_action_steps": 7,
        "gripper_dim": 6,
        "pre_snap_gripper_action": True,
        "binarize_gripper_action": True,
        "input_features": {
            "observation.images.exterior_1_left": {"shape": [3, 256, 256]},
            "observation.images.exterior_2_left": {"shape": [3, 256, 256]},
            "observation.state": {"shape": [8]},
        },
        "output_features": {"action": {"shape": [7]}},
    }
    (checkpoint / "config.json").write_text(json.dumps(config), encoding="utf8")
    for name in (
        "model.safetensors",
        "policy_preprocessor.json",
        "policy_preprocessor_step_3_normalizer_processor.safetensors",
        "policy_postprocessor.json",
        "policy_postprocessor_step_2_unnormalizer_processor.safetensors",
    ):
        (checkpoint / name).write_bytes(b"robotworld-test-fixture")
    metadata = checkpoint / ".cache" / "huggingface" / "download"
    metadata.mkdir(parents=True)
    revision = "1" * 40
    (metadata / "config.json.metadata").write_text(f"{revision}\nconfig-etag\n", encoding="utf8")
    (metadata / "model.safetensors.metadata").write_text(f"{revision}\nmodel-etag\n", encoding="utf8")
    return checkpoint


def test_local_model_lifecycle_is_durable_and_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "models"
    checkpoint = _vla_fixture(root)
    _isolate_model_roots(monkeypatch, root)
    registration = {
        "displayName": "Fixture VLA-JEPA",
        "roles": ["vla_policy"],
        "providerType": "local_path",
        "localPath": str(checkpoint),
        "modelRevision": "unrecorded",
        "expectedDevice": "cuda",
        "precision": "bfloat16",
    }

    with TestClient(app) as client:
        created = client.post("/api/models", json=registration, headers={"Idempotency-Key": "model-create-fixture"})
        assert created.status_code == 201, created.text
        model = created.json()["result"]["model"]
        model_id = model["id"]
        assert model["lifecycleState"] == "REGISTERED"

        replay = client.post("/api/models", json=registration, headers={"Idempotency-Key": "model-create-fixture"})
        assert replay.status_code == 201
        assert replay.json()["reused"] is True
        assert replay.json()["commandId"] == created.json()["commandId"]

        conflicting = client.post(
            "/api/models",
            json={**registration, "displayName": "Different payload"},
            headers={"Idempotency-Key": "model-create-fixture"},
        )
        assert conflicting.status_code == 409

        validated = client.post(
            f"/api/models/{model_id}/validate",
            json={"computeContentHash": True},
            headers={"Idempotency-Key": "model-validate-fixture"},
        )
        assert validated.status_code == 200, validated.text
        result = validated.json()["result"]
        assert result["validation"]["valid"] is True
        assert result["model"]["lifecycleState"] == "AVAILABLE"
        assert result["model"]["contentSha256"]
        assert result["model"]["modelRevision"] == "1" * 40
        assert result["model"]["capabilities"]["checkpointRepositoryRevision"] == "1" * 40
        assert result["model"]["capabilities"]["normalizationRevision"]
        assert result["model"]["capabilities"]["actionDimension"] == 7
        assert result["model"]["capabilities"]["cameraKeys"] == [
            "observation.images.exterior_1_left",
            "observation.images.exterior_2_left",
        ]

        revalidated = client.post(
            f"/api/models/{model_id}/validate",
            json={"computeContentHash": False},
            headers={"Idempotency-Key": "model-revalidate-fixture-without-rehash"},
        )
        assert revalidated.status_code == 200, revalidated.text
        revalidated_model = revalidated.json()["result"]["model"]
        assert revalidated_model["contentSha256"] == result["model"]["contentSha256"]
        assert revalidated_model["modelRevision"] == "1" * 40

        blocked_load = client.post(f"/api/models/{model_id}/load", json={})
        assert blocked_load.status_code == 409
        assert "isolated worker" in blocked_load.json()["detail"]
        assert client.get(f"/api/models/{model_id}").json()["lifecycleState"] == "AVAILABLE"

        audit = client.get("/api/audit", params={"entity_type": "model", "entity_id": model_id}).json()["events"]
        assert {event["action"] for event in audit} >= {
            "model.register",
            "model.validate.start",
            "model.validate.finish",
        }


def test_local_path_allowlist_rejects_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.safetensors"
    outside.write_bytes(b"not allowed")
    _isolate_model_roots(monkeypatch, allowed)

    with pytest.raises(ValueError, match="outside ROBOTWORLD_MODEL_ROOTS"):
        model_registry.resolve_allowed_local_path(str(outside))


def test_model_endpoint_ssrf_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBOTWORLD_ALLOWED_PRIVATE_MODEL_HOSTS", raising=False)
    assert model_registry.validate_endpoint_url("http://127.0.0.1:8001/v1") == "http://127.0.0.1:8001/v1"
    with pytest.raises(ValueError):
        model_registry.validate_endpoint_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError, match="query parameters"):
        model_registry.validate_endpoint_url("https://example.com/v1?token=secret")


def test_model_registry_never_returns_secret_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOTWORLD_TEST_MODEL_KEY", "must-never-appear")
    payload = {
        "displayName": "Remote model alias",
        "roles": ["platform_agent"],
        "providerType": "openai_compatible",
        "baseUrl": "http://127.0.0.1:65530/v1",
        "modelId": "user-defined-alias",
        "apiKeyEnv": "ROBOTWORLD_TEST_MODEL_KEY",
    }
    with TestClient(app) as client:
        response = client.post("/api/models", json=payload)
        assert response.status_code == 201, response.text
        assert response.json()["result"]["model"]["apiKeyConfigured"] is True
        listing = client.get("/api/models")
        assert listing.status_code == 200
        assert "must-never-appear" not in listing.text
