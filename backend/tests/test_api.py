from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import app


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


def test_world_mutations_and_checks() -> None:
    with TestClient(app) as client:
        scene = client.get("/api/worlds/scene").json()
        assert scene["sceneTree"]
        assert client.put("/api/worlds/scene", json={"sceneTree": scene["sceneTree"], "variants": scene["variants"]}).status_code == 200
        checks = client.post("/api/worlds/checks/run", json={}).json()["physicsChecks"]
        assert checks and all(item["status"] in {"pass", "warn", "fail"} for item in checks)
        variant = client.post("/api/worlds/variants", json={"name": "Test clearance", "desc": "API contract test"})
        assert variant.status_code == 200
        assert client.post(f"/api/worlds/variants/{variant.json()['id']}/activate", json={}).status_code == 200


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
        assert asset["status"] in {"ready", "testing"}, asset
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
