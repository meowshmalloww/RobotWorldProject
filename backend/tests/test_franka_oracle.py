from __future__ import annotations

import hashlib
import base64
from pathlib import Path

import mujoco
import numpy as np
from fastapi.testclient import TestClient

from app.config import DATA_DIR
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


def test_worlds_live_stream_is_continuous_and_persists_same_evaluation() -> None:
    with TestClient(app) as client:
        robot_id = _registered_franka(client)
        preview = client.get("/api/worlds/scene/robot-preview", params={"robot_id": robot_id})
        assert preview.status_code == 200, preview.text
        preview_body = preview.json()
        assert preview_body["poseSource"] == "mujoco_home_keyframe_forward_kinematics"
        assert preview_body["mountValidatedForExecution"] is False
        assert preview_body["spawnPositionM"] == [-0.15, -0.28, 0.9]
        assert preview_body["spawnQuaternionWxyz"] == [0.707106781187, 0.0, 0.0, 0.707106781187]
        assert len(preview_body["geometries"]) > 50
        assert all(item["kind"] == "mesh" and item["meshName"] for item in preview_body["geometries"])
        compiled_mesh = client.get("/api/runtime/franka-compiled-meshes/link1.obj")
        assert compiled_mesh.status_code == 200
        vertex = next(line for line in compiled_mesh.text.splitlines() if line.startswith("v "))
        actual_vertex = np.asarray([float(value) for value in vertex.split()[1:]], dtype=float)
        source_model = mujoco.MjModel.from_xml_path(str(next((DATA_DIR / "robots" / robot_id / "runtime").glob("*.xml")).resolve()))
        mesh_id = mujoco.mj_name2id(source_model, mujoco.mjtObj.mjOBJ_MESH, "link1")
        first_vertex = int(source_model.mesh_vertadr[mesh_id])
        assert np.allclose(actual_vertex, source_model.mesh_vert[first_vertex], atol=1e-8)
        assert np.linalg.norm(source_model.mesh_pos[mesh_id]) > 0.01

        persisted_mount = client.patch(
            "/api/worlds/robot-spawn",
            json={
                "positionM": preview_body["spawnPositionM"],
                "quaternionWxyz": preview_body["spawnQuaternionWxyz"],
            },
        )
        assert persisted_mount.status_code == 422, persisted_mount.text
        assert "measured counter support surface" in persisted_mount.text
        persisted_preview = client.get("/api/worlds/scene/robot-preview", params={"robot_id": robot_id}).json()
        assert persisted_preview["spawnPositionM"] == preview_body["spawnPositionM"]
        assert persisted_preview["mountValidatedForExecution"] is False
        invalid_mount = client.patch(
            "/api/worlds/robot-spawn",
            json={
                "positionM": [preview_body["spawnPositionM"][0], preview_body["spawnPositionM"][1], 1.2],
                "quaternionWxyz": preview_body["spawnQuaternionWxyz"],
            },
        )
        assert invalid_mount.status_code == 422

        created = client.post(
            "/api/worlds/live-sessions",
            json={
                "robotId": robot_id,
                "instruction": "Pick up the object and place it in the target.",
                "backend": "mujoco",
                "controller": "oracle",
                "task": "pick_place",
                "seed": 6203,
            },
        )
        assert created.status_code == 201, created.text
        session = created.json()
        assert session["authoritative"] is True
        assert session["physicsHz"] == 500
        assert session["streamHz"] == 25

        frames: list[dict] = []
        evaluation: dict | None = None
        with client.websocket_connect(f"/ws/worlds/live/{session['sessionId']}") as socket:
            meta = socket.receive_json()
            assert meta["type"] == "meta"
            for _ in range(600):
                message = socket.receive_json()
                if message["type"] == "frame":
                    frames.append(message)
                    assert message["authoritative"] is True
                    assert message["state"]["finite"] is True
                    geometries = message["state"]["renderGeometries"]
                    assert any(item["meshName"] == "link1" for item in geometries)
                    assert any(item["name"] == "pick_object_geom" for item in geometries)
                    assert all(len(item["quaternionWxyz"]) == 4 for item in geometries)
                    assert all(len(item["bodyPositionM"]) == 3 for item in geometries)
                    assert all(len(item["bodyQuaternionWxyz"]) == 4 for item in geometries)
                elif message["type"] == "end":
                    evaluation = message["evaluation"]
                    break
                else:
                    raise AssertionError(message)

        assert evaluation is not None
        assert evaluation["success"] is True
        assert evaluation["status"] == "SUCCEEDED"
        assert len(frames) >= 30
        assert len({frame["phase"] for frame in frames}) >= 5
        assert frames[-1]["sequence"] > frames[0]["sequence"]
        assert frames[-1]["simTimeSeconds"] > frames[0]["simTimeSeconds"]
        jpeg = base64.b64decode(frames[len(frames) // 2]["jpegBase64"])
        assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")
        persisted = client.get(f"/api/evaluations/{evaluation['id']}")
        assert persisted.status_code == 200
        assert persisted.json()["result"]["predicate"]["contained"] is True
        assert persisted.json()["result"]["trajectory"]
        assert all("renderGeometries" not in sample for sample in persisted.json()["result"]["trajectory"])
