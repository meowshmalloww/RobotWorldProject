"""Persisted, budget-bounded orchestration over canonical curriculum commands.

This service does not ask an LLM for coordinates and does not simulate work
with timers. Each phase delegates to the same durable command layer used by
the UI and platform-agent tools. Cooperative cancellation is checked between
activities; process shutdown leaves non-terminal rows for startup recovery.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..contracts import (
    AutonomousCurriculumRunRequest,
    CompiledAssetVlaEvaluationRequest,
    CurriculumPlanRequest,
)
from ..db import SessionLocal
from ..models import (
    AuditEvent,
    AutonomousCurriculumRunRecord,
    CompiledAssetVersionRecord,
    ModelRegistrationRecord,
    RobotRegistrationRecord,
    ScenarioExecutionRecord,
    ScenarioSpecRecord,
)
from ..telemetry import span
from ..util import new_id
from . import command_store, curriculum_catalog, evaluation_catalog, vla_bridge


ACTIVE_STATES = {"QUEUED", "STARTING", "RUNNING"}
TERMINAL_STATES = {"SUCCEEDED", "STOPPED", "BLOCKED", "CANCELLED", "CRASHED"}
TRANSITIONS = {
    "QUEUED": {"STARTING", "CANCELLED"},
    "STARTING": {"RUNNING", "CANCELLED", "CRASHED"},
    "RUNNING": TERMINAL_STATES,
}


class AutonomousCurriculumError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_view(row: AutonomousCurriculumRunRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "revision": row.revision,
        "lifecycleState": row.lifecycle_state,
        "autonomyMode": row.autonomy_mode,
        "robotId": row.robot_id,
        "modelId": row.model_id,
        "taskFamily": row.task_family,
        "instruction": row.instruction,
        "request": dict(row.request or {}),
        "budgets": dict(row.budgets or {}),
        "state": dict(row.state or {}),
        "cancellationRequested": row.cancellation_requested,
        "commandId": row.command_id,
        "error": row.error,
        "stopReason": row.stop_reason,
        "createdBy": row.created_by,
        "startedAt": row.started_at,
        "heartbeatAt": row.heartbeat_at,
        "finishedAt": row.finished_at,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


async def _transition(
    session,
    row: AutonomousCurriculumRunRecord,
    target: str,
    *,
    detail: dict[str, Any] | None = None,
    actor: str | None = None,
) -> None:
    source = row.lifecycle_state
    if target not in TRANSITIONS.get(source, set()):
        raise AutonomousCurriculumError(f"Invalid autonomous-run transition {source} -> {target}.")
    row.lifecycle_state = target
    row.updated_at = _now()
    row.heartbeat_at = _now()
    if target == "STARTING":
        row.started_at = _now()
    if target in TERMINAL_STATES:
        row.finished_at = _now()
    session.add(
        AuditEvent(
            command_id=row.command_id,
            entity_type="autonomous_curriculum_run",
            entity_id=row.id,
            action="autonomous_run.transition",
            from_state=source,
            to_state=target,
            detail=detail or {},
            actor=actor or row.created_by,
        )
    )


async def _persist_state(run_id: str, state: dict[str, Any]) -> None:
    async with SessionLocal() as session:
        row = await session.get(AutonomousCurriculumRunRecord, run_id)
        if row is None:
            raise KeyError(run_id)
        row.state = command_store.json_safe(state)
        row.heartbeat_at = _now()
        row.updated_at = _now()
        await session.commit()


async def _finish(run_id: str, target: str, reason: str, *, error: str | None = None) -> None:
    async with SessionLocal() as session:
        row = await session.get(AutonomousCurriculumRunRecord, run_id)
        if row is None or row.lifecycle_state in TERMINAL_STATES:
            return
        await _transition(
            session,
            row,
            target,
            detail={"reason": reason, "error": error},
        )
        row.stop_reason = reason
        row.error = error
        await session.commit()


async def _row(run_id: str) -> AutonomousCurriculumRunRecord:
    async with SessionLocal() as session:
        row = await session.get(AutonomousCurriculumRunRecord, run_id)
        if row is None:
            raise KeyError(run_id)
        session.expunge(row)
        return row


async def _check_cancelled(run_id: str) -> bool:
    row = await _row(run_id)
    if not row.cancellation_requested:
        return False
    await _finish(run_id, "CANCELLED", "kill_switch_requested")
    return True


def _activity_attempt(state: dict[str, Any]) -> int:
    key = f"{state.get('iteration', 0)}:{state.get('phase', 'PLAN_NEXT')}"
    return int((state.get("activityRetries") or {}).get(key, 0))


async def _record_retry(
    run_id: str,
    state: dict[str, Any],
    request: AutonomousCurriculumRunRequest,
    error: Exception,
) -> bool:
    phase = str(state.get("phase") or "PLAN_NEXT")
    iteration = int(state.get("iteration") or 0)
    key = f"{iteration}:{phase}"
    retries = dict(state.get("activityRetries") or {})
    attempt = int(retries.get(key, 0)) + 1
    retries[key] = attempt
    state["activityRetries"] = retries
    history = list(state.get("history") or [])
    history.append(
        {
            "iteration": iteration,
            "phase": phase,
            "outcome": "RETRY" if attempt <= request.budgets.max_retries else "RETRIES_EXHAUSTED",
            "attempt": attempt,
            "error": str(error)[:500],
            "at": _now().isoformat(),
        }
    )
    state["history"] = history[-200:]
    if phase in {"ORACLE", "VLA"}:
        consumed = dict(state.get("consumed") or {})
        consumed["evaluationEpisodes"] = int(consumed.get("evaluationEpisodes") or 0) + 1
        state["consumed"] = consumed
    await _persist_state(run_id, state)
    return attempt <= request.budgets.max_retries


async def _advance_plan(
    run_id: str,
    request: AutonomousCurriculumRunRequest,
    state: dict[str, Any],
) -> bool:
    iteration = int(state.get("iteration") or 0)
    consumed = dict(state.get("consumed") or {})
    remaining_worlds = request.budgets.max_worlds - int(consumed.get("worlds") or 0)
    if iteration >= request.budgets.max_iterations:
        await _finish(run_id, "STOPPED", "iteration_budget_exhausted")
        return True
    if int(consumed.get("evaluationEpisodes") or 0) >= request.budgets.max_evaluation_episodes:
        await _finish(run_id, "STOPPED", "evaluation_budget_exhausted")
        return True
    if remaining_worlds <= 0:
        await _finish(run_id, "STOPPED", "world_budget_exhausted")
        return True
    attempt = _activity_attempt(state)
    with span("curriculum.autonomous.plan", run_id=run_id, iteration=iteration):
        envelope = await curriculum_catalog.plan_next(
            CurriculumPlanRequest(
                robotId=request.robot_id,
                modelId=request.model_id,
                taskFamily=request.task_family,
                targetSuccessRate=request.target_success_rate,
                minimumAttempts=request.minimum_attempts,
                maxEvaluationEpisodes=request.budgets.max_evaluation_episodes,
                maxNewScenarios=remaining_worlds,
                lookbackLimit=request.lookback_limit,
                allowedAssetVersionIds=request.allowed_asset_version_ids,
                seed=request.seed + iteration,
            ),
            idempotency_key=f"autonomous:{run_id}:iteration:{iteration}:plan:attempt:{attempt}",
            actor="autonomous-curriculum",
        )
    if envelope.get("status") != "SUCCEEDED":
        raise AutonomousCurriculumError(str(envelope.get("error") or "Curriculum planning command failed."))
    result = dict(envelope.get("result") or {})
    plan = dict(result.get("plan") or {})
    scenario = dict(result.get("scenario") or {})
    decision = dict(plan.get("decision") or {})
    history = list(state.get("history") or [])
    history.append(
        {
            "iteration": iteration,
            "phase": "PLAN_NEXT",
            "planId": plan.get("id"),
            "planStatus": plan.get("status"),
            "decision": decision,
            "scenarioId": scenario.get("id"),
            "at": _now().isoformat(),
        }
    )
    state["history"] = history[-200:]
    state["current"] = {
        "planId": plan.get("id"),
        "scenarioId": scenario.get("id"),
        "scenarioFingerprint": scenario.get("scenarioFingerprint"),
    }
    plan_status = str(plan.get("status") or "")
    if plan_status == "STOPPED":
        await _persist_state(run_id, state)
        reason = str(decision.get("reason") or "planner_stop")
        terminal = "SUCCEEDED" if reason == "target_success_rate_reached" else "STOPPED"
        await _finish(run_id, terminal, reason)
        return True
    if plan_status in {"BLOCKED", "ACTION_REQUIRED"}:
        await _persist_state(run_id, state)
        await _finish(run_id, "BLOCKED", str(decision.get("reason") or plan_status.lower()))
        return True
    if not scenario.get("id"):
        raise AutonomousCurriculumError("A PLANNED curriculum decision returned no durable scenario ID.")
    if decision.get("scenarioReused") is not True:
        consumed["worlds"] = int(consumed.get("worlds") or 0) + 1
    state["consumed"] = consumed
    state["phase"] = "ORACLE"
    await _persist_state(run_id, state)
    return False


async def _existing_successful_oracle(scenario_id: str) -> str | None:
    async with SessionLocal() as session:
        execution = await session.scalar(
            select(ScenarioExecutionRecord)
            .where(
                ScenarioExecutionRecord.scenario_id == scenario_id,
                ScenarioExecutionRecord.stage == "DETERMINISTIC_ORACLE",
                ScenarioExecutionRecord.status == "SUCCEEDED",
            )
            .order_by(ScenarioExecutionRecord.created_at.desc())
        )
    return execution.evaluation_id if execution is not None else None


async def _advance_oracle(
    run_id: str,
    request: AutonomousCurriculumRunRequest,
    state: dict[str, Any],
) -> bool:
    consumed = dict(state.get("consumed") or {})
    if int(consumed.get("evaluationEpisodes") or 0) >= request.budgets.max_evaluation_episodes:
        await _finish(run_id, "STOPPED", "evaluation_budget_exhausted_before_oracle")
        return True
    current = dict(state.get("current") or {})
    scenario_id = str(current.get("scenarioId") or "")
    async with SessionLocal() as session:
        scenario = await session.get(ScenarioSpecRecord, scenario_id)
        if scenario is None:
            raise AutonomousCurriculumError("Current scenario record is missing.")
        scenario_state = scenario.lifecycle_state
    evaluation_id = str(current.get("oracleEvaluationId") or "") or None
    reused_validated = False
    if not evaluation_id and scenario_state == "ORACLE_VALIDATED":
        evaluation_id = await _existing_successful_oracle(scenario_id)
        reused_validated = bool(evaluation_id)
    if not evaluation_id:
        if scenario_state != "PLANNED":
            raise AutonomousCurriculumError(
                f"Scenario {scenario_id} cannot enter its oracle gate from {scenario_state}."
            )
        iteration = int(state.get("iteration") or 0)
        attempt = _activity_attempt(state)
        with span("curriculum.autonomous.oracle", run_id=run_id, iteration=iteration):
            envelope = await curriculum_catalog.execute_scenario_oracle(
                scenario_id,
                idempotency_key=f"autonomous:{run_id}:iteration:{iteration}:oracle:attempt:{attempt}",
                actor="autonomous-curriculum",
            )
        if envelope.get("status") != "SUCCEEDED":
            raise AutonomousCurriculumError(str(envelope.get("error") or "Scenario oracle command failed."))
        result = dict(envelope.get("result") or {})
        evaluation = dict(result.get("evaluation") or {})
        evaluation_id = str(evaluation.get("id") or "")
        if not evaluation_id:
            raise AutonomousCurriculumError("Scenario oracle returned no durable evaluation ID.")
        consumed["evaluationEpisodes"] = int(consumed.get("evaluationEpisodes") or 0) + 1
        current["oracleSuccess"] = bool(evaluation.get("success"))
        current["oracleFailureCode"] = evaluation.get("failureCode")
    else:
        evaluation = await evaluation_catalog.get_evaluation(evaluation_id)
        current["oracleSuccess"] = bool(evaluation.get("success"))
        current["oracleFailureCode"] = evaluation.get("failureCode")
    current["oracleEvaluationId"] = evaluation_id
    history = list(state.get("history") or [])
    history.append(
        {
            "iteration": int(state.get("iteration") or 0),
            "phase": "ORACLE",
            "scenarioId": scenario_id,
            "evaluationId": evaluation_id,
            "success": current["oracleSuccess"],
            "failureCode": current.get("oracleFailureCode"),
            "reusedValidatedScenario": reused_validated,
            "at": _now().isoformat(),
        }
    )
    state["current"] = current
    state["consumed"] = consumed
    state["history"] = history[-200:]
    if not current["oracleSuccess"]:
        await _persist_state(run_id, state)
        await _finish(run_id, "BLOCKED", "deterministic_oracle_failed")
        return True
    if not request.execute_vla:
        await _persist_state(run_id, state)
        await _finish(run_id, "SUCCEEDED", "oracle_gate_complete")
        return True
    state["phase"] = "VLA"
    await _persist_state(run_id, state)
    return False


async def _advance_vla(
    run_id: str,
    request: AutonomousCurriculumRunRequest,
    state: dict[str, Any],
) -> bool:
    assert request.model_id is not None
    consumed = dict(state.get("consumed") or {})
    if int(consumed.get("evaluationEpisodes") or 0) >= request.budgets.max_evaluation_episodes:
        await _finish(run_id, "STOPPED", "evaluation_budget_exhausted_before_vla")
        return True
    if float(consumed.get("gpuMinutes") or 0.0) >= request.budgets.max_gpu_minutes:
        await _finish(run_id, "STOPPED", "gpu_budget_exhausted_before_vla")
        return True
    current = dict(state.get("current") or {})
    scenario_id = str(current.get("scenarioId") or "")
    async with SessionLocal() as session:
        scenario = await session.get(ScenarioSpecRecord, scenario_id)
        if scenario is None or not scenario.asset_version_id:
            raise AutonomousCurriculumError("Current scenario or asset reference is missing before VLA evaluation.")
        specification = dict(scenario.specification or {})
        _, seed, placement_request = curriculum_catalog.placement_request_for_scenario(
            specification,
            scenario.scenario_fingerprint,
        )
        asset_version_id = scenario.asset_version_id
    bridge = await vla_bridge.bridge_status(request.model_id, request.robot_id)
    if not bridge.get("executable"):
        state["blockers"] = list(bridge.get("blockers") or [])
        await _persist_state(run_id, state)
        await _finish(run_id, "BLOCKED", "vla_bridge_unavailable", error="; ".join(state["blockers"])[:2000])
        return True
    iteration = int(state.get("iteration") or 0)
    attempt = _activity_attempt(state)
    with span("curriculum.autonomous.vla", run_id=run_id, iteration=iteration):
        envelope = await evaluation_catalog.run_compiled_asset_pick_place_vla(
            CompiledAssetVlaEvaluationRequest(
                robotId=request.robot_id,
                assetVersionId=asset_version_id,
                modelId=request.model_id,
                instruction=request.instruction,
                maxPolicySteps=request.max_policy_steps,
                seed=seed,
                placementRequest=placement_request,
            ),
            idempotency_key=f"autonomous:{run_id}:iteration:{iteration}:vla:attempt:{attempt}",
            actor="autonomous-curriculum",
        )
    if envelope.get("status") != "SUCCEEDED":
        raise AutonomousCurriculumError(str(envelope.get("error") or "VLA evaluation command failed."))
    evaluation = dict((envelope.get("result") or {}).get("evaluation") or {})
    evaluation_id = str(evaluation.get("id") or "")
    if not evaluation_id:
        raise AutonomousCurriculumError("VLA command returned no durable evaluation ID.")
    consumed["evaluationEpisodes"] = int(consumed.get("evaluationEpisodes") or 0) + 1
    duration_seconds = float((evaluation.get("result") or {}).get("durationSeconds") or 0.0)
    consumed["gpuMinutes"] = float(consumed.get("gpuMinutes") or 0.0) + duration_seconds / 60.0
    analysis = await curriculum_catalog.analyze_evaluation(
        evaluation_id,
        idempotency_key=f"autonomous:{run_id}:iteration:{iteration}:analyze-vla",
        actor="autonomous-curriculum",
    )
    success = bool(evaluation.get("success"))
    consecutive_failures = 0 if success else int(state.get("consecutiveFailures") or 0) + 1
    history = list(state.get("history") or [])
    history.append(
        {
            "iteration": iteration,
            "phase": "VLA",
            "evaluationId": evaluation_id,
            "success": success,
            "failureCode": evaluation.get("failureCode"),
            "analysisCommandId": analysis.get("commandId"),
            "durationSeconds": duration_seconds,
            "at": _now().isoformat(),
        }
    )
    state["history"] = history[-200:]
    state["consumed"] = consumed
    state["consecutiveFailures"] = consecutive_failures
    state["iteration"] = iteration + 1
    state["phase"] = "PLAN_NEXT"
    state["current"] = {}
    await _persist_state(run_id, state)
    if consecutive_failures >= request.budgets.max_consecutive_failures:
        await _finish(run_id, "STOPPED", "consecutive_failure_stop")
        return True
    if int(consumed["evaluationEpisodes"]) >= request.budgets.max_evaluation_episodes:
        await _finish(run_id, "STOPPED", "evaluation_budget_exhausted")
        return True
    if float(consumed["gpuMinutes"]) >= request.budgets.max_gpu_minutes:
        await _finish(run_id, "STOPPED", "gpu_budget_exhausted")
        return True
    return False


async def _execute(run_id: str) -> None:
    row = await _row(run_id)
    request = AutonomousCurriculumRunRequest.model_validate(row.request)
    async with SessionLocal() as session:
        active = await session.get(AutonomousCurriculumRunRecord, run_id)
        assert active is not None
        if active.lifecycle_state == "QUEUED":
            if active.cancellation_requested:
                await _transition(session, active, "CANCELLED", detail={"reason": "kill_switch_requested"})
                active.stop_reason = "kill_switch_requested"
                await session.commit()
                return
            await _transition(session, active, "STARTING")
            await session.commit()
        if active.lifecycle_state == "STARTING":
            await _transition(session, active, "RUNNING")
            await session.commit()
        elif active.lifecycle_state != "RUNNING":
            return

    while True:
        if await _check_cancelled(run_id):
            return
        row = await _row(run_id)
        state = dict(row.state or {})
        phase = str(state.get("phase") or "PLAN_NEXT")
        try:
            if phase == "PLAN_NEXT":
                terminal = await _advance_plan(run_id, request, state)
            elif phase == "ORACLE":
                terminal = await _advance_oracle(run_id, request, state)
            elif phase == "VLA":
                terminal = await _advance_vla(run_id, request, state)
            else:
                raise AutonomousCurriculumError(f"Unknown persisted autonomous phase: {phase}")
            if terminal:
                return
        except asyncio.CancelledError:
            # Process shutdown is not a user cancellation. Leave durable state
            # non-terminal so startup can resume the exact phase.
            raise
        except (curriculum_catalog.CurriculumError, evaluation_catalog.EvaluationConflict, AutonomousCurriculumError) as exc:
            await _finish(run_id, "BLOCKED", "activity_blocked", error=str(exc)[:2000])
            return
        except Exception as exc:
            if await _record_retry(run_id, state, request, exc):
                continue
            await _finish(run_id, "CRASHED", "activity_retries_exhausted", error=str(exc)[:2000])
            return


_tasks: dict[str, asyncio.Task[None]] = {}


def _schedule(run_id: str) -> None:
    existing = _tasks.get(run_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(_execute(run_id), name=f"autonomous-curriculum:{run_id}")
    _tasks[run_id] = task

    def done(completed: asyncio.Task[None]) -> None:
        _tasks.pop(run_id, None)
        if not completed.cancelled():
            completed.exception()

    task.add_done_callback(done)


async def start_run(
    request: AutonomousCurriculumRunRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", by_alias=True)
    try:
        command, reused = await command_store.start_command(
            kind="curriculum.autonomous.start",
            target_type="robot",
            target_id=request.robot_id,
            payload=payload,
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except command_store.CommandConflict as exc:
        raise AutonomousCurriculumError(str(exc)) from exc
    if reused:
        result = command_store.command_view(command, reused=True)
        run_id = str((result.get("result") or {}).get("run", {}).get("id") or "")
        if run_id:
            row = await _row(run_id)
            if row.lifecycle_state in ACTIVE_STATES:
                _schedule(run_id)
        return result

    run_id = new_id("autorun")
    try:
        async with SessionLocal() as session:
            robot = await session.get(RobotRegistrationRecord, request.robot_id)
            if robot is None:
                raise KeyError(request.robot_id)
            if robot.lifecycle_state != "AVAILABLE" or not robot.active:
                raise AutonomousCurriculumError("Autonomous execution requires an active AVAILABLE robot.")
            if request.model_id:
                model = await session.get(ModelRegistrationRecord, request.model_id)
                if model is None:
                    raise KeyError(request.model_id)
                if "vla_policy" not in (model.roles or []):
                    raise AutonomousCurriculumError("Selected model does not have the vla_policy role.")
            if request.allowed_asset_version_ids:
                assets = (
                    await session.execute(
                        select(CompiledAssetVersionRecord).where(
                            CompiledAssetVersionRecord.id.in_(request.allowed_asset_version_ids)
                        )
                    )
                ).scalars().all()
                if len(assets) != len(request.allowed_asset_version_ids):
                    raise AutonomousCurriculumError("One or more allowed asset versions do not exist.")
                if any(asset.lifecycle_state != "ORACLE_VALIDATED" for asset in assets):
                    raise AutonomousCurriculumError("Allowed assets must be ORACLE_VALIDATED before autonomous use.")
            existing = await session.scalar(
                select(AutonomousCurriculumRunRecord).where(
                    AutonomousCurriculumRunRecord.robot_id == request.robot_id,
                    AutonomousCurriculumRunRecord.lifecycle_state.in_(ACTIVE_STATES),
                )
            )
            if existing is not None:
                raise AutonomousCurriculumError(f"Robot already has active autonomous run {existing.id}.")
            budgets = request.budgets.model_dump(mode="json", by_alias=True)
            row = AutonomousCurriculumRunRecord(
                id=run_id,
                lifecycle_state="QUEUED",
                autonomy_mode=str(request.autonomy_mode),
                robot_id=request.robot_id,
                model_id=request.model_id,
                task_family=request.task_family,
                instruction=request.instruction,
                request=payload,
                budgets=budgets,
                state={
                    "phase": "PLAN_NEXT",
                    "iteration": 0,
                    "consumed": {
                        "worlds": 0,
                        "scrapeRequests": 0,
                        "gpuMinutes": 0.0,
                        "evaluationEpisodes": 0,
                    },
                    "consecutiveFailures": 0,
                    "activityRetries": {},
                    "current": {},
                    "history": [],
                },
                command_id=command.id,
                created_by=actor,
            )
            session.add(row)
            session.add(
                AuditEvent(
                    command_id=command.id,
                    entity_type="autonomous_curriculum_run",
                    entity_id=run_id,
                    action="autonomous_run.create",
                    from_state=None,
                    to_state="QUEUED",
                    detail={"budgets": budgets, "executeVla": request.execute_vla},
                    actor=actor,
                )
            )
            await session.commit()
            output = {"run": run_view(row)}
    except Exception as exc:
        await command_store.finish_command(command.id, error=str(exc))
        raise
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    _schedule(run_id)
    return command_store.command_view(command)


async def get_run(run_id: str) -> dict[str, Any]:
    return run_view(await _row(run_id))


async def list_runs(limit: int = 100) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(AutonomousCurriculumRunRecord)
                .order_by(AutonomousCurriculumRunRecord.created_at.desc())
                .limit(max(1, min(limit, 500)))
            )
        ).scalars().all()
    return [run_view(row) for row in rows]


async def cancel_run(run_id: str, *, actor: str = "user") -> dict[str, Any]:
    async with SessionLocal() as session:
        row = await session.get(AutonomousCurriculumRunRecord, run_id)
        if row is None:
            raise KeyError(run_id)
        if row.lifecycle_state in TERMINAL_STATES:
            return run_view(row)
        if not row.cancellation_requested:
            row.cancellation_requested = True
            row.updated_at = _now()
            session.add(
                AuditEvent(
                    command_id=row.command_id,
                    entity_type="autonomous_curriculum_run",
                    entity_id=row.id,
                    action="autonomous_run.cancel_request",
                    from_state=row.lifecycle_state,
                    to_state=row.lifecycle_state,
                    detail={"cooperative": True},
                    actor=actor,
                )
            )
            await session.commit()
        view = run_view(row)
    return view


async def resume_incomplete() -> int:
    async with SessionLocal() as session:
        ids = list(
            (
                await session.execute(
                    select(AutonomousCurriculumRunRecord.id).where(
                        AutonomousCurriculumRunRecord.lifecycle_state.in_(ACTIVE_STATES)
                    )
                )
            ).scalars()
        )
    for run_id in ids:
        _schedule(run_id)
    return len(ids)


async def shutdown() -> None:
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
