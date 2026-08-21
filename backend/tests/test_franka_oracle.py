from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from app.main import app
from app.services.franka_pick_place import (
    INITIAL_OBJECT_XYZ,
    MujocoFrankaBackend,
    compile_world_template,
)


def _registered_franka(client: TestClient) -> str:
    response = client.post(
        "/api/robots/franka/mujoco",
        json={},
        headers={"Idempotency-Key": "oracle-test-franka-registration"},
    )
    assert response.status_code == 201, response.text
    return response.json()["result"]["robot"]["id"]


def test_authoritative_pick_place_oracle_repeats_and_persists() -> None:
    with TestClient(app) as client:
        robot_id = _registered_franka(client)
        run_ids: list[str] = []
        final_positions: list[list[float]] = []
        for seed in (0, 1, 2):
            response = client.post(
                "/api/evaluations/oracle/pick-place",
                json={"robotId": robot_id, "seed": seed},
                headers={"Idempotency-Key": f"oracle-pick-place-seed-{seed}"},
            )
            assert response.status_code == 201, response.text
            evaluation = response.json()["result"]["evaluation"]
            result = evaluation["result"]
            run_ids.append(evaluation["id"])
            final_positions.append(result["predicate"]["finalObjectPositionM"])
            assert evaluation["status"] == "SUCCEEDED"
            assert evaluation["success"] is True
            assert result["policy"] == "deterministic_differential_ik_oracle_v1"
            assert result["physicsHz"] == 500
            assert result["controlHz"] == 50
            assert result["predicate"]["contained"] is True
            assert result["predicate"]["onSupportSurface"] is True
            assert result["predicate"]["settled"] is True
            assert result["predicate"]["targetErrorM"] < 0.01
            pairs = result["contactSummary"]["sampledPairs"]
            assert pairs["left_finger|pick_object"] > 0
            assert pairs["pick_object|right_finger"] > 0
            assert result["trajectory"]
            assert all(state["finite"] for state in result["trajectory"])

        assert len(set(run_ids)) == 3
        assert np.allclose(final_positions[0], final_positions[1], atol=1e-9)
        assert np.allclose(final_positions[1], final_positions[2], atol=1e-9)

        replay = client.post(
            "/api/evaluations/oracle/pick-place",
            json={"robotId": robot_id, "seed": 0},
            headers={"Idempotency-Key": "oracle-pick-place-seed-0"},
        )
        assert replay.status_code == 201
        assert replay.json()["reused"] is True
        assert replay.json()["result"]["evaluation"]["id"] == run_ids[0]

        persisted = client.get(f"/api/evaluations/{run_ids[0]}")
        assert persisted.status_code == 200
        assert persisted.json()["status"] == "SUCCEEDED"
        assert persisted.json()["result"]["seed"] == 0
        templates = client.get("/api/world-templates").json()["worldTemplates"]
        assert any(row["manifest"]["id"] == "franka-tabletop-pick-place-v1" for row in templates)

        for camera in ("front", "wrist"):
            frame = client.get(f"/api/evaluations/{run_ids[0]}/frames/settle/{camera}.png")
            assert frame.status_code == 200
            assert frame.headers["content-type"] == "image/png"
            assert hashlib.sha256(frame.content).hexdigest() == persisted.json()["result"]["frameHashes"]["settle"][camera]


def test_released_free_body_falls_and_settles() -> None:
    with TestClient(app) as client:
        robot_id = _registered_franka(client)
    template = compile_world_template(robot_id)
    backend = MujocoFrankaBackend(Path(template["runtimePath"]))
    try:
        backend.reset(7)
        assert backend.data is not None
        backend.data.qpos[backend.object_qpos : backend.object_qpos + 3] = [0.48, -0.12, 0.62]
        backend.data.qpos[backend.object_qpos + 3 : backend.object_qpos + 7] = [1, 0, 0, 0]
        backend.data.qvel[-6:] = 0
        import mujoco

        mujoco.mj_forward(backend.model, backend.data)
        released_z = backend.state()["objectPositionM"][2]
        backend.step(600)
        settled = backend.state()
        assert released_z > 0.6
        assert settled["objectPositionM"][2] < released_z - 0.25
        assert abs(settled["objectPositionM"][2] - INITIAL_OBJECT_XYZ[2]) < 0.01
        assert np.linalg.norm(settled["objectVelocityMps"]) < 0.01
        assert any("workspace_calibration" in {contact.body_a, contact.body_b} for contact in backend.contacts())
        front = backend.render_rgb("front", width=160, height=120)
        wrist = backend.render_rgb("wrist", width=160, height=120)
        assert front.shape == (120, 160, 3)
        assert wrist.shape == (120, 160, 3)
        assert hashlib.sha256(front.tobytes()).hexdigest() != hashlib.sha256(wrist.tobytes()).hexdigest()
    finally:
        backend.close()

