from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.franka import MENAGERIE_REVISION


def test_pinned_franka_registers_validates_and_activates() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/robots/franka/mujoco",
            json={},
            headers={"Idempotency-Key": "franka-default-register"},
        )
        assert created.status_code == 201, created.text
        command = created.json()
        robot = command["result"]["robot"]
        robot_id = robot["id"]
        assert command["status"] == "SUCCEEDED"
        assert robot["sourceRevision"] == MENAGERIE_REVISION
        assert robot["license"]["spdx"] == "Apache-2.0"
        assert robot["armDof"] == 7
        assert robot["gripperJoints"] == 2
        assert robot["cameraNames"] == ["front", "wrist"]
        assert robot["physicsReady"] is True
        assert robot["readiness"]["physicsExecutable"] is True
        assert robot["readiness"]["policyExecutable"] is False
        assert robot["wristCameraCalibrated"] is False
        assert robot["validation"]["passed"] is True
        assert robot["validation"]["severeInitialContacts"] == 0
        assert robot["validation"]["closedWidthM"] < 0.02
        assert robot["validation"]["openWidthM"] > 0.06
        assert robot["validation"]["cameraCalibration"]["front"]["robotPixels"] > 100
        assert robot["validation"]["cameraCalibration"]["front"]["workspacePixels"] > 100
        assert robot["validation"]["cameraCalibration"]["wrist"]["gripperPixels"] > 20
        assert robot["validation"]["cameraCalibration"]["wrist"]["workspacePixels"] > 20
        assert robot["definition"]["schemaVersion"] == "robotworld.robot.v1"
        assert len(robot["definition"]["joints"]) == 9
        assert len(robot["definition"]["sensors"]) == 2

        replay = client.post(
            "/api/robots/franka/mujoco",
            json={},
            headers={"Idempotency-Key": "franka-default-register"},
        )
        assert replay.status_code == 201
        assert replay.json()["reused"] is True
        assert replay.json()["commandId"] == command["commandId"]

        listing = client.get("/api/robots")
        assert listing.status_code == 200
        assert listing.json()["defaultBackend"] == "isaac_sim"
        assert listing.json()["fallbackBackends"] == ["mujoco"]
        assert any(row["id"] == robot_id and row["active"] for row in listing.json()["registrations"])

        activated = client.post(
            f"/api/robots/{robot_id}/activate",
            json={},
            headers={"Idempotency-Key": "franka-default-activate"},
        )
        assert activated.status_code == 200, activated.text
        probe = activated.json()["result"]["loadProbe"]
        assert probe["loadedIntoValidationProcess"] is True
        assert probe["resident"] is False
        assert probe["homeFinite"] is True
        assert probe["nq"] == 9
        assert probe["actuators"] == 8
        assert set(probe["cameras"]) == {"front", "wrist"}

        audit = client.get("/api/audit", params={"entity_type": "robot", "entity_id": robot_id})
        assert audit.status_code == 200
        assert {event["action"] for event in audit.json()["events"]} >= {
            "robot.franka.register",
            "robot.activate",
        }
