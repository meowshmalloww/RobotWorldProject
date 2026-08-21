from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contracts import AutonomousCurriculumRunRequest, PlacementRequest
from app.db import SessionLocal
from app.main import app
from app.models import (
    CommandExecution,
    AutonomousCurriculumRunRecord,
    EvaluationRunRecord,
    ModelRegistrationRecord,
    RobotRegistrationRecord,
    ScenarioExecutionRecord,
    ScenarioSpecRecord,
)
from app.services import autonomous_curriculum, curriculum_catalog


def test_placement_request_is_semantic_and_rejects_unchecked_coordinates() -> None:
    request = PlacementRequest(
        semanticSupportSurface="workspace_surface",
        seed=31,
        varyPosition=True,
        varyOrientation=True,
        scenarioFingerprint="a" * 64,
    )
    payload = request.model_dump(mode="json", by_alias=True)
    assert payload["semanticSupportSurface"] == "workspace_surface"
    assert payload["varyPosition"] is True
    assert payload["varyOrientation"] is True
    assert "xyz" not in payload and "pose" not in payload
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PlacementRequest.model_validate({**payload, "xyz": [0.5, 0.0, 0.3]})


def test_autonomous_run_requires_explicit_executable_budgets_and_model_binding() -> None:
    with pytest.raises(ValidationError, match="executeVla requires a selected modelId"):
        AutonomousCurriculumRunRequest(
            robotId="robot-budget-fixture",
            executeVla=True,
            budgets={"maxWorlds": 1, "maxEvaluationEpisodes": 2, "maxGpuMinutes": 1},
        )
    with pytest.raises(ValidationError, match="requires maxWorlds greater than zero"):
        AutonomousCurriculumRunRequest(
            robotId="robot-budget-fixture",
            executeVla=False,
            budgets={"maxWorlds": 0, "maxEvaluationEpisodes": 1},
        )
    request = AutonomousCurriculumRunRequest(
        autonomyMode="AUTONOMOUS_WITH_BUDGETS",
        robotId="robot-budget-fixture",
        executeVla=False,
        budgets={"maxWorlds": 1, "maxEvaluationEpisodes": 1, "maxIterations": 1},
    )
    assert request.budgets.max_scrape_requests == 0
    assert request.budgets.max_gpu_minutes == 0


def test_autonomous_kill_switch_cancels_queued_run_without_work() -> None:
    request = AutonomousCurriculumRunRequest(
        autonomyMode="AUTONOMOUS_WITH_BUDGETS",
        robotId="robot-kill-switch-fixture",
        executeVla=False,
        budgets={"maxWorlds": 1, "maxEvaluationEpisodes": 1, "maxIterations": 1},
    )

    async def exercise() -> dict:
        async with SessionLocal() as session:
            session.add(
                AutonomousCurriculumRunRecord(
                    id="autorun_kill_switch_fixture",
                    lifecycle_state="QUEUED",
                    autonomy_mode="AUTONOMOUS_WITH_BUDGETS",
                    robot_id="robot-kill-switch-fixture",
                    task_family="pick_place",
                    instruction=request.instruction,
                    request=request.model_dump(mode="json", by_alias=True),
                    budgets=request.budgets.model_dump(mode="json", by_alias=True),
                    state={
                        "phase": "PLAN_NEXT",
                        "iteration": 0,
                        "consumed": {"worlds": 0, "scrapeRequests": 0, "gpuMinutes": 0, "evaluationEpisodes": 0},
                        "history": [],
                    },
                    command_id="cmd_kill_switch_fixture",
                )
            )
            await session.commit()
        requested = await autonomous_curriculum.cancel_run("autorun_kill_switch_fixture", actor="kill-test")
        assert requested["cancellationRequested"] is True
        await autonomous_curriculum._execute("autorun_kill_switch_fixture")
        return await autonomous_curriculum.get_run("autorun_kill_switch_fixture")

    with TestClient(app):
        result = asyncio.run(exercise())
    assert result["lifecycleState"] == "CANCELLED"
    assert result["stopReason"] == "kill_switch_requested"
    assert result["state"]["history"] == []


def test_autonomous_restart_reschedules_persisted_nonterminal_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    request = AutonomousCurriculumRunRequest(
        autonomyMode="AUTONOMOUS_WITH_BUDGETS",
        robotId="robot-autonomous-resume-fixture",
        executeVla=False,
        budgets={"maxWorlds": 1, "maxEvaluationEpisodes": 1, "maxIterations": 1},
    )
    scheduled: list[str] = []

    async def seed_and_resume() -> int:
        async with SessionLocal() as session:
            session.add(
                AutonomousCurriculumRunRecord(
                    id="autorun_resume_fixture",
                    lifecycle_state="RUNNING",
                    autonomy_mode="AUTONOMOUS_WITH_BUDGETS",
                    robot_id="robot-autonomous-resume-fixture",
                    task_family="pick_place",
                    instruction=request.instruction,
                    request=request.model_dump(mode="json", by_alias=True),
                    budgets=request.budgets.model_dump(mode="json", by_alias=True),
                    state={
                        "phase": "ORACLE",
                        "iteration": 0,
                        "consumed": {
                            "worlds": 1,
                            "scrapeRequests": 0,
                            "gpuMinutes": 0.0,
                            "evaluationEpisodes": 0,
                        },
                        "current": {"scenarioId": "scenario-resume-fixture"},
                        "history": [{"phase": "PLAN_NEXT"}],
                    },
                    command_id="cmd_autonomous_resume_fixture",
                )
            )
            await session.commit()
        count = await autonomous_curriculum.resume_incomplete()
        async with SessionLocal() as session:
            row = await session.get(AutonomousCurriculumRunRecord, "autorun_resume_fixture")
            assert row is not None
            await session.delete(row)
            await session.commit()
        return count

    with TestClient(app):
        monkeypatch.setattr(autonomous_curriculum, "_schedule", scheduled.append)
        count = asyncio.run(seed_and_resume())

    assert count >= 1
    assert "autorun_resume_fixture" in scheduled


def test_restart_reconciliation_makes_interrupted_scenario_retryable() -> None:
    async def exercise() -> tuple[int, str, str, str, str | None]:
        async with SessionLocal() as session:
            session.add(
                CommandExecution(
                    id="cmd_scenario_restart_fixture",
                    kind="scenario.oracle_validate",
                    target_type="scenario_spec",
                    target_id="scenario_restart_fixture",
                    idempotency_key="scenario-restart-fixture-key",
                    status="RUNNING",
                    input={"payload": {"scenarioId": "scenario_restart_fixture"}},
                    actor="restart-test",
                )
            )
            session.add(
                ScenarioSpecRecord(
                    id="scenario_restart_fixture",
                    lifecycle_state="ORACLE_VALIDATING",
                    task_family="pick_place",
                    robot_id="robot_restart_fixture",
                    asset_version_id="asset_restart_fixture",
                    scenario_fingerprint="f" * 64,
                    specification={"schemaVersion": "robotworld.scenario-spec.v1"},
                )
            )
            session.add(
                ScenarioExecutionRecord(
                    id="scenarioexec_restart_fixture",
                    scenario_id="scenario_restart_fixture",
                    stage="DETERMINISTIC_ORACLE",
                    status="RUNNING",
                    command_id="cmd_scenario_restart_fixture",
                )
            )
            await session.commit()
        count = await curriculum_catalog.reconcile_incomplete_executions(actor="restart-test")
        async with SessionLocal() as session:
            command = await session.get(CommandExecution, "cmd_scenario_restart_fixture")
            scenario = await session.get(ScenarioSpecRecord, "scenario_restart_fixture")
            execution = await session.get(ScenarioExecutionRecord, "scenarioexec_restart_fixture")
            assert command is not None and scenario is not None and execution is not None
            return count, command.status, scenario.lifecycle_state, execution.status, execution.error

    with TestClient(app) as client:
        count, command_state, scenario_state, execution_state, error = asyncio.run(exercise())
        assert count == 1
        assert command_state == "FAILED"
        assert scenario_state == "PLANNED"
        assert execution_state == "CRASHED"
        assert error and "backend restart" in error
        audit = client.get(
            "/api/audit",
            params={"entity_type": "scenario_execution", "entity_id": "scenarioexec_restart_fixture"},
        ).json()["events"]
        assert any(event["fromState"] == "RUNNING" and event["toState"] == "CRASHED" for event in audit)


def test_scenario_validator_rejects_physical_shape_changes_during_asset_reuse() -> None:
    base = {
        "assetReuseRequired": True,
        "oracleBeforeVla": True,
        "placementConstraints": {"semanticSupportSurface": "workspace_surface"},
    }
    assert curriculum_catalog._scenario_validation_errors(
        {**base, "variationDimensions": ["size", "aspectRatio"]}
    ) == ["immutable asset reuse cannot vary size, aspectRatio, or shapeFamily"]
    assert curriculum_catalog._scenario_validation_errors(
        {**base, "variationDimensions": ["baseline_policy_evaluation"]}
    ) == []


def test_failure_classifier_derives_only_from_structured_terminal_signals() -> None:
    async def seed() -> None:
        async with SessionLocal() as session:
            session.add_all(
                [
                    EvaluationRunRecord(
                        id="eval_classifier_success",
                        status="SUCCEEDED",
                        robot_id="robot_classifier_fixture",
                        world_template_id="world_classifier_fixture",
                        policy="deterministic_oracle",
                        seed=1,
                        success=True,
                        result={
                            "worldRuntimeSha256": "a" * 64,
                            "trajectory": [{"finite": True}],
                            "contactSummary": {"samples": 1},
                            "predicate": {"contained": True, "settled": True},
                        },
                    ),
                    EvaluationRunRecord(
                        id="eval_classifier_nonfinite",
                        status="FAILED",
                        robot_id="robot_classifier_fixture",
                        world_template_id="world_classifier_fixture",
                        policy="vla-jepa:mdl_classifier_fixture:r1",
                        seed=2,
                        success=False,
                        failure_code="backend_specific_unknown",
                        failure_detail="A backend emitted an unknown code after a non-finite state.",
                        result={
                            "worldRuntimeSha256": "a" * 64,
                            "trajectory": [{"finite": False}],
                            "contactSummary": {"samples": 0},
                            "predicate": {"contained": False, "settled": False},
                        },
                    ),
                    EvaluationRunRecord(
                        id="eval_classifier_crash",
                        status="CRASHED",
                        robot_id="robot_classifier_fixture",
                        world_template_id="world_classifier_fixture",
                        policy="vla-jepa:mdl_classifier_fixture:r1",
                        seed=3,
                        success=False,
                        failure_detail="Worker exited before producing a result.",
                        result={},
                    ),
                    EvaluationRunRecord(
                        id="eval_classifier_running",
                        status="RUNNING",
                        robot_id="robot_classifier_fixture",
                        world_template_id="world_classifier_fixture",
                        policy="deterministic_oracle",
                        seed=4,
                    ),
                ]
            )
            await session.commit()

    with TestClient(app) as client:
        asyncio.run(seed())
        success = client.post("/api/evaluations/eval_classifier_success/analyze")
        assert success.status_code == 201, success.text
        assert success.json()["result"]["classification"]["outcome"] == "SUCCESS"
        assert success.json()["result"]["classification"]["failureEvent"] is None

        nonfinite = client.post("/api/evaluations/eval_classifier_nonfinite/analyze")
        assert nonfinite.status_code == 201, nonfinite.text
        nonfinite_event = nonfinite.json()["result"]["classification"]["failureEvent"]
        assert nonfinite_event["code"] == "policy_instability"
        assert nonfinite_event["certainty"] == "derived_signal"
        assert nonfinite_event["evidence"]["nonFiniteSteps"] == 1
        assert nonfinite_event["recommendedAction"]["action"] == "REPAIR_POLICY_RUNTIME"

        crashed = client.post("/api/evaluations/eval_classifier_crash/analyze")
        assert crashed.status_code == 201, crashed.text
        crash_event = crashed.json()["result"]["classification"]["failureEvent"]
        assert crash_event["code"] == "worker_crash"
        assert crash_event["certainty"] == "derived_signal"
        assert crash_event["evidence"]["failureDetail"] == "Worker exited before producing a result."

        running = client.post("/api/evaluations/eval_classifier_running/analyze")
        assert running.status_code == 409
        assert "only terminal runs" in running.text

        failures = client.get("/api/failure-events").json()["failureEvents"]
        fixture_failures = {row["evaluationId"]: row for row in failures if row["evaluationId"].startswith("eval_classifier_")}
        assert set(fixture_failures) == {"eval_classifier_nonfinite", "eval_classifier_crash"}


def test_curriculum_stops_at_explicit_measured_target_with_wilson_interval() -> None:
    robot_id = "robot_curriculum_target_fixture"
    model_id = "mdl_curriculum_target_fixture"

    async def seed() -> None:
        async with SessionLocal() as session:
            session.add(
                RobotRegistrationRecord(
                    id=robot_id,
                    display_name="Curriculum target fixture",
                    source_format="mjcf",
                    definition={},
                    lifecycle_state="AVAILABLE",
                    active=False,
                )
            )
            session.add(
                ModelRegistrationRecord(
                    id=model_id,
                    display_name="Curriculum policy fixture",
                    roles=["vla_policy"],
                    provider_type="local_server",
                    base_url="http://127.0.0.1:65530/v1",
                    model_revision="fixture-r1",
                    lifecycle_state="AVAILABLE",
                    health_status="healthy",
                )
            )
            for index in range(5):
                session.add(
                    EvaluationRunRecord(
                        id=f"eval_curriculum_target_{index}",
                        status="SUCCEEDED",
                        robot_id=robot_id,
                        world_template_id="world_curriculum_target_fixture",
                        policy=f"vla-jepa:{model_id}:r1",
                        seed=index,
                        success=True,
                        result={
                            "worldRuntimeSha256": "b" * 64,
                            "trajectory": [{"finite": True}],
                            "contactSummary": {"samples": 2},
                            "predicate": {
                                "modelRegistrationId": model_id,
                                "contained": True,
                                "onSupportSurface": True,
                                "settled": True,
                                "released": True,
                            },
                        },
                    )
                )
            await session.commit()

    with TestClient(app) as client:
        asyncio.run(seed())
        response = client.post(
            "/api/curriculum/plan-next",
            json={
                "robotId": robot_id,
                "modelId": model_id,
                "targetSuccessRate": 0.8,
                "minimumAttempts": 5,
                "maxEvaluationEpisodes": 20,
                "maxNewScenarios": 1,
                "lookbackLimit": 20,
            },
        )
        assert response.status_code == 201, response.text
        result = response.json()["result"]
        assert result["plan"]["status"] == "STOPPED"
        assert result["plan"]["decision"] == {
            "action": "STOP",
            "reason": "target_success_rate_reached",
            "scenarioReused": False,
        }
        analysis = result["plan"]["analysis"]
        assert analysis["sampleCount"] == 5
        assert analysis["successCount"] == 5
        assert analysis["successRate"] == 1.0
        assert analysis["wilson95"][0] < 1.0
        assert analysis["wilson95"][1] == 1.0
        assert result["scenario"] is None

        unsupported = client.post(
            "/api/curriculum/plan-next",
            json={"robotId": robot_id, "modelId": model_id, "taskFamily": "open_cabinet"},
        )
        assert unsupported.status_code == 409
        assert "taskFamily=pick_place" in unsupported.text
