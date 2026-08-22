from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


MODEL_ARGUMENTS = {
    "displayName": "Agent-approved local endpoint",
    "roles": ["platform_agent"],
    "providerType": "local_server",
    "baseUrl": "http://127.0.0.1:65529/v1",
    "modelId": "user-defined-agent-alias",
}


def test_tool_registry_publishes_strict_versioned_schemas_and_persists_queries() -> None:
    with TestClient(app) as client:
        response = client.get("/api/agent/tools")
        assert response.status_code == 200
        tools = {row["name"]: row for row in response.json()["tools"]}
        assert {
            "models.list",
            "models.register",
            "robots.register_default_franka",
            "evaluations.run_oracle_pick_place",
            "evaluations.run_oracle_franka_drawer",
            "evaluations.run_vla_compiled_asset",
            "evaluations.analyze_failure",
            "failures.list",
            "coverage.get",
            "curriculum.plan_next",
            "scenarios.oracle_validate",
            "curriculum.runs.start",
            "curriculum.runs.list",
            "curriculum.runs.cancel",
            "scrapers.collector_versions.list",
            "scrapers.repairs.list",
            "scrapers.repairs.request",
            "scrapers.self_heal.request",
            "scrapers.repairs.test",
            "scrapers.repairs.decide",
            "scrapers.repairs.rollback",
            "vla.bridge_status",
            "vla.attach_franka_zero_shot_bridge",
            "training.datasets.create_from_evaluation",
            "training.vla_jepa.validate_fine_tune",
            "training.vla_jepa.execute_fine_tune",
            "training.policy_candidates.decide",
            "training.policy_candidates.rollback",
            "telemetry.signoz.search_traces",
            "telemetry.signoz.metric_timeseries",
        } <= set(tools)
        assert len(tools) == 56
        assert tools["models.list"]["schemaVersion"] == "robotworld.agent-tool-definition.v1"
        assert tools["models.list"]["effect"] == "QUERY"
        assert tools["models.register"]["effect"] == "MUTATION"
        assert tools["models.register"]["approvalRequired"] is True
        assert tools["models.register"]["inputSchema"]["additionalProperties"] is False
        assert tools["evaluations.run_vla_compiled_asset"]["approvalRequired"] is True
        assert tools["evaluations.run_vla_compiled_asset"]["autonomousAllowed"] is False
        assert tools["evaluations.analyze_failure"]["approvalRequired"] is True
        assert tools["curriculum.plan_next"]["approvalRequired"] is True
        assert tools["curriculum.plan_next"]["autonomousAllowed"] is False
        assert tools["scenarios.oracle_validate"]["approvalRequired"] is True
        assert tools["scenarios.oracle_validate"]["autonomousAllowed"] is False
        assert tools["scenarios.oracle_validate"]["inputSchema"]["additionalProperties"] is False
        assert tools["curriculum.runs.start"]["effect"] == "MUTATION"
        assert tools["curriculum.runs.start"]["approvalRequired"] is True
        assert tools["curriculum.runs.start"]["autonomousAllowed"] is False
        assert tools["curriculum.runs.list"]["effect"] == "QUERY"
        assert tools["curriculum.runs.cancel"]["approvalRequired"] is True
        assert tools["curriculum.runs.cancel"]["autonomousAllowed"] is True
        assert tools["scrapers.repairs.request"]["approvalRequired"] is True
        assert tools["scrapers.self_heal.request"]["approvalRequired"] is True
        assert tools["scrapers.repairs.decide"]["autonomousAllowed"] is False
        assert tools["scrapers.repairs.rollback"]["inputSchema"]["additionalProperties"] is False
        assert tools["vla.attach_franka_zero_shot_bridge"]["approvalRequired"] is True
        assert tools["vla.attach_franka_zero_shot_bridge"]["autonomousAllowed"] is False
        dataset_tool = tools["training.datasets.create_from_evaluation"]
        assert dataset_tool["effect"] == "MUTATION"
        assert dataset_tool["approvalRequired"] is True
        assert dataset_tool["autonomousAllowed"] is False
        assert dataset_tool["inputSchema"]["additionalProperties"] is False
        assert "evaluationId" in dataset_tool["inputSchema"]["required"]
        preflight_tool = tools["training.vla_jepa.validate_fine_tune"]
        assert preflight_tool["effect"] == "MUTATION"
        assert preflight_tool["approvalRequired"] is True
        assert preflight_tool["autonomousAllowed"] is False
        assert {"datasetId", "baseModelId"} <= set(preflight_tool["inputSchema"]["required"])
        execute_tool = tools["training.vla_jepa.execute_fine_tune"]
        assert execute_tool["effect"] == "MUTATION"
        assert execute_tool["approvalRequired"] is True
        assert execute_tool["autonomousAllowed"] is False
        assert {"runId", "acknowledgeCandidateOnly"} <= set(execute_tool["inputSchema"]["required"])

        invoked = client.post(
            "/api/agent/tools/invoke",
            json={"toolName": "models.list", "arguments": {}, "autonomyMode": "OBSERVE_ONLY"},
        )
        assert invoked.status_code == 200, invoked.text
        result = invoked.json()
        assert result["status"] == "SUCCEEDED"
        assert result["schemaVersion"] == "robotworld.agent-tool-call-result.v1"
        assert isinstance(result["data"]["models"], list)

        calls = client.get("/api/agent/tool-calls", params={"limit": 20}).json()["toolCalls"]
        persisted = next(row for row in calls if row["id"] == result["toolCallId"])
        assert persisted["toolName"] == "models.list"
        assert persisted["status"] == "SUCCEEDED"
        assert len(persisted["argumentsSha256"]) == 64


def test_mutations_are_denied_in_observe_mode_and_bad_arguments_fail_closed() -> None:
    with TestClient(app) as client:
        denied = client.post(
            "/api/agent/tools/invoke",
            json={
                "toolName": "models.register",
                "arguments": MODEL_ARGUMENTS,
                "autonomyMode": "OBSERVE_ONLY",
            },
        )
        assert denied.status_code == 403
        tool_call_id = denied.json()["detail"]["toolCallId"]
        persisted = client.get("/api/agent/tool-calls").json()["toolCalls"]
        row = next(value for value in persisted if value["id"] == tool_call_id)
        assert row["status"] == "DENIED"
        assert "cannot execute mutation" in row["error"]

        invalid = client.post(
            "/api/agent/tools/invoke",
            json={"toolName": "models.list", "arguments": {"unexpected": True}},
        )
        assert invalid.status_code == 422
        assert "extra_forbidden" in str(invalid.json())


def test_one_use_approval_is_bound_to_exact_normalized_arguments() -> None:
    with TestClient(app) as client:
        approval = client.post(
            "/api/agent/approvals",
            json={
                "toolName": "models.register",
                "arguments": MODEL_ARGUMENTS,
                "approved": True,
                "reason": "Register this local endpoint alias for the platform agent.",
            },
        )
        assert approval.status_code == 201, approval.text
        approval_id = approval.json()["id"]

        mismatched = client.post(
            "/api/agent/tools/invoke",
            json={
                "toolName": "models.register",
                "arguments": {**MODEL_ARGUMENTS, "displayName": "Different model"},
                "autonomyMode": "EXECUTE_WITH_APPROVAL",
                "approvalDecisionId": approval_id,
                "idempotencyKey": "agent-tool-model-mismatch",
            },
        )
        assert mismatched.status_code == 403
        assert "does not match" in str(mismatched.json())

        executed = client.post(
            "/api/agent/tools/invoke",
            json={
                "toolName": "models.register",
                "arguments": MODEL_ARGUMENTS,
                "autonomyMode": "EXECUTE_WITH_APPROVAL",
                "approvalDecisionId": approval_id,
                "idempotencyKey": "agent-tool-model-approved",
            },
        )
        assert executed.status_code == 200, executed.text
        result = executed.json()
        assert result["status"] == "SUCCEEDED"
        assert result["commandId"] == result["data"]["commandId"]
        assert result["data"]["result"]["model"]["createdBy"] == "platform-agent"

        reused_approval = client.post(
            "/api/agent/tools/invoke",
            json={
                "toolName": "models.register",
                "arguments": MODEL_ARGUMENTS,
                "autonomyMode": "EXECUTE_WITH_APPROVAL",
                "approvalDecisionId": approval_id,
                "idempotencyKey": "agent-tool-model-second-use",
            },
        )
        assert reused_approval.status_code == 403
        assert "already been consumed" in str(reused_approval.json())


def test_unbudgeted_autonomous_evaluation_is_not_enabled() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/agent/tools/invoke",
            json={
                "toolName": "evaluations.run_oracle_pick_place",
                "arguments": {"robotId": "franka-not-loaded", "seed": 2},
                "autonomyMode": "AUTONOMOUS_WITH_BUDGETS",
                "idempotencyKey": "agent-unbudgeted-eval",
            },
        )
        assert response.status_code == 403
        assert "not enabled for autonomous execution" in str(response.json())
