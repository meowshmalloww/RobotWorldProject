from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_controlled_franka_drawer_oracle_is_durable_and_physical() -> None:
    with TestClient(app) as client:
        registered = client.post(
            "/api/robots/franka/mujoco",
            json={},
            headers={"Idempotency-Key": "drawer-franka-register"},
        )
        assert registered.status_code == 201, registered.text
        robot_id = registered.json()["result"]["robot"]["id"]

        response = client.post(
            "/api/evaluations/oracle/franka-drawer-open",
            json={"robotId": robot_id, "seed": 6208},
            headers={"Idempotency-Key": "controlled-franka-drawer-6208"},
        )
        assert response.status_code == 201, response.text
        envelope = response.json()
        assert envelope["status"] == "SUCCEEDED"
        evaluation = envelope["result"]["evaluation"]
        assert evaluation["status"] == "SUCCEEDED"
        assert evaluation["success"] is True
        assert evaluation["policy"] == "deterministic_differential_ik_franka_drawer_oracle_v1"
        result = evaluation["result"]
        assert result["predicate"]["drawerDisplacementM"] >= result["predicate"]["minimumDisplacementM"]
        assert result["predicate"]["withinJointLimit"] is True
        assert result["predicate"]["finite"] is True
        assert result["predicate"]["truthMode"] == "authoritative_physics_controlled_fixture"
        assert result["contactSummary"]["bilateralHandleContact"] is True
        assert {phase["phase"] for phase in result["phases"]} >= {
            "pre_grasp",
            "grasp_approach",
            "close_gripper",
            "follow_prismatic_joint",
            "release",
        }
        assert set(result["frameHashes"]["terminal"]) == {"front", "wrist"}
        for camera, expected in result["frameHashes"]["terminal"].items():
            frame = Path(evaluation["artifactDir"]) / "frames" / f"terminal-{camera}.png"
            assert frame.is_file()
            assert hashlib.sha256(frame.read_bytes()).hexdigest() == expected
            streamed = client.get(f"/api/evaluations/{evaluation['id']}/frames/terminal/{camera}.png")
            assert streamed.status_code == 200
            assert streamed.headers["content-type"] == "image/png"
            assert hashlib.sha256(streamed.content).hexdigest() == expected

        template = envelope["result"]["worldTemplate"]
        assert template["jointSweep"]["passed"] is True
        assert template["jointSweep"]["handleAttachedToMovingPart"] is True
        assert template["jointSweep"]["severePenetrationCount"] == 0
        assert template["partGraph"]["joints"][0]["type"] == "prismatic"
        world_xml = Path(template["runtimePath"]).read_text(encoding="utf8")
        assert "drawer_handle_site" in world_xml
        assert '<connect name="drawer' not in world_xml

        replay = client.post(
            "/api/evaluations/oracle/franka-drawer-open",
            json={"robotId": robot_id, "seed": 6208},
            headers={"Idempotency-Key": "controlled-franka-drawer-6208"},
        )
        assert replay.status_code == 201
        assert replay.json()["reused"] is True
        assert replay.json()["commandId"] == envelope["commandId"]

        persisted = client.get(f"/api/evaluations/{evaluation['id']}")
        assert persisted.status_code == 200
        assert persisted.json()["status"] == "SUCCEEDED"


def test_agent_tool_exposes_same_franka_drawer_command() -> None:
    with TestClient(app) as client:
        registered = client.post(
            "/api/robots/franka/mujoco",
            json={},
            headers={"Idempotency-Key": "drawer-agent-franka-register"},
        )
        robot_id = registered.json()["result"]["robot"]["id"]
        tools = client.get("/api/agent/tools").json()["tools"]
        assert any(tool["name"] == "evaluations.run_oracle_franka_drawer" for tool in tools)
        executed = client.post(
            "/api/agent/tools/invoke",
            json={
                "toolName": "evaluations.run_oracle_franka_drawer",
                "arguments": {"robotId": robot_id, "seed": 6209},
                "autonomyMode": "AUTONOMOUS_WITH_BUDGETS",
                "idempotencyKey": "agent-controlled-franka-drawer-6209",
            },
        )
        assert executed.status_code == 403, executed.text
        assert "approval" in str(executed.json()).lower()
