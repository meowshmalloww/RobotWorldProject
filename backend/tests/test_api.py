from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import app
from app.services import brightdata, llm


def test_primary_read_contract() -> None:
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["database"] == "healthy"
        assert health.json()["simulation"]["engine"] == "MuJoCo"

        paths = [
            "/api/overview",
            "/api/skills",
            "/api/skills/open-refrigerator",
            "/api/assets",
            "/api/worlds/scene",
            "/api/sources",
            "/api/training",
            "/api/observability/stats",
            "/api/observability/services",
            "/api/observability/traces",
            "/api/observability/metrics",
            "/api/observability/logs",
            "/api/observability/alerts",
            "/api/settings",
        ]
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)


def test_write_only_secrets_survive_section_save() -> None:
    with TestClient(app) as client:
        assert client.put("/api/settings/keys/openai", json={"key": "test-secret-value"}).status_code == 200
        settings = client.get("/api/settings").json()
        assert "test-secret-value" not in settings["models"]["openaiKey"]
        models = settings["models"]
        models["planner"] = "local-model"
        response = client.put("/api/settings/models", json=models)
        assert response.status_code == 200
        assert response.json()["models"]["planner"] == "local-model"
        assert client.get("/api/health").json()["openai"] in {"not_configured", "checking", "degraded"}


def test_brightdata_probe_returns_only_sanitized_evidence(monkeypatch) -> None:
    async def search(query: str):
        return {
            "general": {"search_engine": "google", "query": query},
            "organic": [
                {"title": "One", "link": "https://example.com/product", "secret": "raw-provider-field"},
                {"title": "Two", "link": "https://docs.example.org/item"},
            ],
        }

    monkeypatch.setattr(brightdata, "google_search", search)
    with TestClient(app) as client:
        response = client.post("/api/integrations/brightdata/probe", json={})
        assert response.status_code == 200
        assert response.json() == {
            "connected": True,
            "provider": "Bright Data SERP API",
            "searchEngine": "google",
            "queryMatched": True,
            "organicCount": 2,
            "sampleDomains": ["example.com", "docs.example.org"],
        }
        assert "raw-provider-field" not in response.text


def test_world_mutations_and_checks() -> None:
    with TestClient(app) as client:
        scene = client.get("/api/worlds/scene").json()
        assert scene["sceneTree"]
        for placement in scene["placements"]:
            assert isinstance(placement["rotationZDeg"], (int, float))
            assert placement["mobility"] in {"movable", "fixed"}
            assert placement["collisionApproximation"] in {"convexHull", "none"}
            assert placement["massKg"] > 0
            actual = [placement["worldBounds"][1][i] - placement["worldBounds"][0][i] for i in range(3)]
            assert max(abs(actual[i] - placement["targetDimensions"][i]) for i in range(3)) < 0.002
            assert placement["anchor"]["surface"] in {"world floor", "countertop"}
        assert client.put("/api/worlds/scene", json={"sceneTree": scene["sceneTree"], "variants": scene["variants"]}).status_code == 200
        checks = client.post("/api/worlds/checks/run", json={}).json()["physicsChecks"]
        assert checks and all(item["status"] in {"pass", "warn", "fail"} for item in checks)
        if scene["placements"]:
            assert any(item["check"].startswith("Measured mesh fit") for item in checks)
            first = scene["placements"][0]
            moved = [first["translation"][0] + 0.01, first["translation"][1], first["translation"][2]]
            assert client.patch(f"/api/worlds/placements/{first['assetId']}", json={"translation": moved, "rotationZDeg": 15}).status_code == 200
            updated = client.get("/api/worlds/scene").json()
            actual = next(item for item in updated["placements"] if item["assetId"] == first["assetId"])["translation"]
            assert actual == moved
            assert next(item for item in updated["placements"] if item["assetId"] == first["assetId"])["rotationZDeg"] == 15
        camera_probe = client.post("/api/worlds/cameras/probe", json={})
        assert camera_probe.status_code == 200
        for camera in camera_probe.json()["cameras"].values():
            assert camera["shape"] == [256, 256, 3]
            assert camera["dtype"] == "uint8"
            assert camera["nonzero"] > 0
        variant = client.post("/api/worlds/variants", json={"name": "Test clearance", "desc": "API contract test"})
        assert variant.status_code == 200
        assert client.post(f"/api/worlds/variants/{variant.json()['id']}/activate", json={}).status_code == 200


def test_frontend_diagnostics_and_isaac_franka_fail_closed() -> None:
    with TestClient(app) as client:
        recorded = client.post("/api/diagnostics/frontend-errors", json={
            "source": "react",
            "message": "Cannot read properties of undefined (reading 'toFixed')",
            "stack": "GeneratedAssetInspector",
            "componentStack": "Worlds",
            "route": "/#/worlds",
            "userAgent": "pytest",
        })
        assert recorded.status_code == 202
        diagnostics = client.get("/api/diagnostics/runtime")
        assert diagnostics.status_code == 200
        assert any(row["service"] == "frontend.react" for row in diagnostics.json()["events"])

        status = client.get("/api/simulation/isaac")
        assert status.status_code == 200
        assert status.json()["version"] == "6.0"
        assert status.json()["franka"]["armDof"] == 7
        assert status.json()["franka"]["fingerJoints"] == 2

        franka = client.post("/api/robots/franka/isaac", json={})
        assert franka.status_code == 201
        assert franka.json()["armDof"] == 7
        assert franka.json()["readiness"]["executable"] is False

        scene = client.get("/api/worlds/scene").json()
        if scene["placements"]:
            prepared = client.post("/api/simulation/isaac/prepare", json={})
            assert prepared.status_code == 200, prepared.text
            assert prepared.json()["prepared"] is True
            assert prepared.json()["runtimeReady"] is status.json()["ready"]


def test_robot_import_camera_mapping_and_world_command_gate(monkeypatch) -> None:
    async def no_provider(*args, **kwargs):
        return None, "heuristic:test"

    monkeypatch.setattr(llm, "plan", no_provider)
    urdf = b'''<robot name="test_arm"><link name="base"/><link name="tool"/><joint name="j1" type="revolute"><parent link="base"/><child link="tool"/><limit lower="-1" upper="1" effort="10" velocity="1"/></joint><gazebo reference="tool"><sensor name="wrist"><camera name="wrist_cam"/></sensor></gazebo></robot>'''
    with TestClient(app) as client:
        imported = client.post(
            "/api/robots/import?filename=test_arm.urdf",
            content=urdf,
            headers={"content-type": "application/octet-stream"},
        )
        assert imported.status_code == 201, imported.text
        robot = imported.json()
        assert robot["format"] == "urdf" and robot["joints"] == 1
        assert robot["readiness"]["executable"] is False
        mapped = client.put(f"/api/robots/{robot['id']}", json={"cameraMappings": {
            "observation.images.exterior_1_left": "wrist_cam",
            "observation.images.exterior_2_left": "wrist_cam",
        }})
        assert mapped.status_code == 200
        assert any("fine-tuned checkpoint" in item for item in mapped.json()["readiness"]["blockers"])
        plan = client.post("/api/worlds/commands", json={
            "instruction": "grab the apple and put it in the blender",
            "robotId": robot["id"],
            "mode": "plan",
        })
        assert plan.status_code == 200, plan.text
        assert plan.json()["executionAllowed"] is False
        assert plan.json()["plan"]["steps"]


def test_native_vulkan_frame_and_acceptance_run_fail_closed_without_policy() -> None:
    with TestClient(app) as client:
        probe = client.get("/api/render/vulkan/probe")
        assert probe.status_code == 200, probe.text
        assert probe.json()["backend"] == "Vulkan"
        assert probe.json()["browser3dApi"] == "none"

        frame = client.get("/api/render/vulkan/frame?scene=kitchen&width=480&height=270")
        assert frame.status_code == 200, frame.text
        assert frame.headers["content-type"] == "image/png"
        assert frame.headers["x-robotworld-renderer"] == "Vulkan"
        assert frame.content.startswith(b"\x89PNG\r\n\x1a\n")

        catalog = client.get("/api/demo-scenarios")
        assert catalog.status_code == 200
        assert {item["id"] for item in catalog.json()["scenarios"]} == {"kitchen-juice", "factory-sort"}
        assert catalog.json()["readiness"]["trainingEnabled"] is False

        for scenario_id, seed in (("kitchen-juice", 1048576), ("factory-sort", 2097152)):
            queued = client.post(f"/api/demo-scenarios/{scenario_id}/runs", json={"seed": seed})
            assert queued.status_code == 202, queued.text
            job_id = queued.json()["jobId"]
            job = None
            for _ in range(120):
                response = client.get(f"/api/jobs/{job_id}")
                assert response.status_code == 200, response.text
                job = response.json()
                if job["status"] in {"success", "failed", "blocked"}:
                    break
                time.sleep(0.1)
            assert job is not None
            assert job["status"] == "blocked", job
            assert job["detail"]["result"]["taskSuccess"] is None
            assert job["detail"]["result"]["reason"] == "policy_not_configured"
            assert [stage["status"] for stage in job["detail"]["stages"][:3]] == ["passed", "passed", "passed"]
            assert "joints" in job["detail"]["stages"][1]["detail"]


def test_real_asset_compile_and_openusd_download() -> None:
    with TestClient(app) as client:
        queued = client.post(
            "/api/assets/build",
            json={"query": "Samsung RF28T5001SR refrigerator", "kind": "articulated", "generator": "parametric", "families": []},
        )
        assert queued.status_code == 202, queued.text
        asset_id = queued.json()["assetId"]
        asset = None
        for _ in range(120):
            response = client.get(f"/api/assets/{asset_id}")
            assert response.status_code == 200
            asset = response.json()
            if asset["status"] != "building":
                break
            time.sleep(0.1)
        assert asset is not None
        assert asset["status"] == "ready", asset
        assert asset["lastEvalResult"] == "passed", asset
        assert any(item["file"] == "asset.usda" for item in asset["artifacts"])
        usd = client.get(f"/api/assets/{asset_id}/files/asset.usda")
        assert usd.status_code == 200
        assert b"PhysicsRevoluteJoint" in usd.content


def test_live_mujoco_websocket_can_end_and_replay() -> None:
    with TestClient(app) as client:
        created = client.post("/api/eval/sessions", json={})
        assert created.status_code == 201
        session_id = created.json()["sessionId"]
        with client.websocket_connect(f"/ws/live/{session_id}") as socket:
            assert socket.receive_json()["type"] == "meta"
            frame = socket.receive_json()
            assert frame["type"] == "frame"
            assert "doorAngleDeg" in frame
            socket.send_json({"type": "control", "action": "end"})
            message = frame
            for _ in range(20):
                message = socket.receive_json()
                if message["type"] == "end":
                    break
            assert message["type"] == "end"
        replay = client.get(f"/api/eval/sessions/{session_id}/replay")
        assert replay.status_code == 200
        assert replay.json()["messages"]
