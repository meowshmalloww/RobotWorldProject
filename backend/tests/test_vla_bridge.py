from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.contracts import VlaNormalizedAction
from app.main import app
from app.services import model_registry, vla_bridge


def _checkpoint(root: Path) -> Path:
    path = root / "checkpoint"
    path.mkdir(parents=True)
    (path / "config.json").write_text(
        json.dumps(
            {
                "type": "vla_jepa",
                "state_dim": 8,
                "action_dim": 7,
                "n_action_steps": 7,
                "gripper_dim": 6,
                "pre_snap_gripper_action": True,
                "binarize_gripper_action": True,
                "input_features": {
                    "observation.images.exterior_1_left": {"shape": [3, 224, 224]},
                    "observation.images.exterior_2_left": {"shape": [3, 224, 224]},
                    "observation.state": {"shape": [8]},
                },
                "output_features": {"action": {"shape": [7]}},
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
        (path / name).write_bytes(b"bridge-test")
    return path


def _checkpoint_without_state_feature(root: Path) -> Path:
    path = _checkpoint(root)
    config_path = path / "config.json"
    config = json.loads(config_path.read_text(encoding="utf8"))
    del config["input_features"]["observation.state"]
    config_path.write_text(json.dumps(config), encoding="utf8")
    return path


def test_vla_action_bridge_round_trip_and_bounds() -> None:
    normalized = VlaNormalizedAction(
        values=(-1.0, -0.5, 0.0, 0.5, 1.0, -1.0, 0.25),
        adapterRevision=vla_bridge.ADAPTER_REVISION,
    )
    decoded = vla_bridge.decode_action(normalized)
    assert decoded["physical"] == pytest.approx([-0.05, -0.025, 0.0, 0.1, 0.2, -0.2, 0.625])
    assert decoded["additionalBinarizationApplied"] is False
    assert vla_bridge.encode_action(decoded["physical"]) == pytest.approx(normalized.values)
    with pytest.raises(ValueError, match="safety limits"):
        vla_bridge.encode_action([0.051, 0, 0, 0, 0, 0, 1])
    with pytest.raises(ValueError, match="revision"):
        vla_bridge.decode_action(VlaNormalizedAction(values=(0, 0, 0, 0, 0, 0, 0), adapterRevision="wrong"))


def test_unmodified_droid_checkpoint_is_shape_compatible_but_not_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = _checkpoint(tmp_path / "models")
    for name in model_registry.MODEL_PATH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ROBOTWORLD_MODEL_ROOTS", str(checkpoint.parent))
    with TestClient(app) as client:
        model_response = client.post(
            "/api/models",
            json={
                "displayName": "DROID VLA-JEPA fixture",
                "roles": ["vla_policy"],
                "providerType": "local_path",
                "localPath": str(checkpoint),
            },
            headers={"Idempotency-Key": "bridge-model-registration"},
        )
        model_id = model_response.json()["result"]["model"]["id"]
        validated = client.post(f"/api/models/{model_id}/validate", json={"computeContentHash": False})
        assert validated.status_code == 200
        assert validated.json()["result"]["validation"]["valid"] is True

        robot_response = client.post(
            "/api/robots/franka/mujoco",
            json={},
            headers={"Idempotency-Key": "bridge-franka-registration"},
        )
        robot_id = robot_response.json()["result"]["robot"]["id"]
        status = client.get(f"/api/models/{model_id}/bridges/franka/{robot_id}")
        assert status.status_code == 200
        bridge = status.json()
        assert bridge["shapeCompatible"] is True
        assert bridge["executable"] is False
        assert bridge["observationContract"]["stateDimension"] == 8
        assert bridge["actionContract"]["actionDimension"] == 7
        assert bridge["actionContract"]["checkpointBinarizeGripper"] is True
        assert bridge["actionContract"]["postBridgeBinarization"] is False
        assert any("franka-cartesian-delta-v1" in blocker for blocker in bridge["blockers"])
        assert any("end_effector_local_delta" in blocker for blocker in bridge["blockers"])
        assert any("not explicitly bound" in blocker for blocker in bridge["blockers"])

        decoded = client.post(
            "/api/vla/bridges/franka/actions/decode",
            json={"values": [0.2, 0, 0, 0, 0, 0, -0.4], "adapterRevision": vla_bridge.ADAPTER_REVISION},
        )
        assert decoded.status_code == 200
        assert np.allclose(decoded.json()["physical"], [0.01, 0, 0, 0, 0, 0, 0.3])
        rejected = client.post(
            "/api/vla/bridges/franka/actions/decode",
            json={"values": [1.01, 0, 0, 0, 0, 0, 0], "adapterRevision": vla_bridge.ADAPTER_REVISION},
        )
        assert rejected.status_code == 422


def test_declared_state_dim_without_input_feature_is_optional_at_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = _checkpoint_without_state_feature(tmp_path / "models")
    for name in model_registry.MODEL_PATH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ROBOTWORLD_MODEL_ROOTS", str(checkpoint.parent))
    with TestClient(app) as client:
        model = client.post(
            "/api/models",
            json={
                "displayName": "Image-only DROID fixture",
                "roles": ["vla_policy"],
                "providerType": "local_path",
                "localPath": str(checkpoint),
            },
        ).json()["result"]["model"]
        validated = client.post(f"/api/models/{model['id']}/validate", json={}).json()["result"]["model"]
        assert validated["capabilities"]["stateDimension"] == 8
        assert validated["capabilities"]["stateFeaturePresent"] is False

        robot = client.post("/api/robots/franka/mujoco", json={}).json()["result"]["robot"]
        bridge = client.get(f"/api/models/{model['id']}/bridges/franka/{robot['id']}").json()
        assert bridge["shapeCompatible"] is True
        assert bridge["observationContract"]["checkpointStateFeaturePresent"] is False
        assert bridge["observationContract"]["stateRequired"] is False
        assert bridge["observationContract"]["stateDimension"] == 0
        assert not any("observation.state" in blocker for blocker in bridge["blockers"])
