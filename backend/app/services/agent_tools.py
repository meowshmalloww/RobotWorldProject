"""Schema-validated platform-agent tools over RobotWorld's command/query layer.

This registry is the server-side control surface shared with autonomous agents.
It deliberately exposes product operations, never arbitrary Python or shell
execution. Mutation approvals are one-use and bound to the exact normalized
argument hash so scraped or model-generated text cannot widen their scope.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from pydantic import ValidationError
from sqlalchemy import select, update

from ..contracts import (
    AgentToolCall,
    AgentToolCallResult,
    AgentToolDefinition,
    AgentToolEffect,
    ApprovalDecision,
    AssetVersionTargetToolInput,
    AuditListToolInput,
    AutonomyMode,
    AutonomousCurriculumRunRequest,
    AutonomousRunTargetToolInput,
    BrightDataCollectionRequest,
    BrightDataCollectionToolInput,
    CompiledAssetOracleRequest,
    CompiledAssetVlaEvaluationRequest,
    ContractModel,
    CoverageStateToolInput,
    CurriculumPlanRequest,
    EmptyToolInput,
    EvidenceBundleTargetToolInput,
    EvidenceCollectionTargetToolInput,
    EvaluationListToolInput,
    EvaluationAnalysisRequest,
    EvaluationTargetToolInput,
    FrankaRegistrationRequest,
    LeRobotDatasetExportRequest,
    ModelRegistrationCreate,
    ModelTargetToolInput,
    NormalizeRecordedEvidenceToolInput,
    ObjectRequest,
    ObjectRequestTargetToolInput,
    OracleEvaluationRequest,
    PolicyCandidateDecisionRequest,
    PolicyCandidateRollbackRequest,
    RobotTargetToolInput,
    RigidAssetCompileRequest,
    ScenarioTargetToolInput,
    SigNozMetricQueryToolInput,
    SigNozTraceSearchToolInput,
    ScraperCollectorVersionsListToolInput,
    ScraperRepairCreate,
    ScraperRepairDecision,
    ScraperRepairDecisionToolInput,
    ScraperRepairRollback,
    ScraperRepairRollbackToolInput,
    ScraperRepairTargetToolInput,
    ValidateModelToolInput,
    VlaBridgeStatusToolInput,
    VlaFrankaZeroShotBridgeToolInput,
    VlaJepaFineTuneExecuteRequest,
    VlaJepaFineTuneValidationRequest,
)
from ..db import SessionLocal
from ..models import AgentToolCallRecord, ApprovalDecisionRecord, AuditEvent
from ..util import new_id
from . import autonomous_curriculum, command_store, control_catalog, curriculum_catalog, evaluation_catalog, evidence_catalog, evidence_collection, lerobot_dataset, lerobot_training, policy_lifecycle, rigid_asset_compiler, robot_catalog, scraper_repair, signoz, vla_bridge, vla_policy_worker


class AgentToolError(RuntimeError):
    def __init__(self, message: str, *, tool_call_id: str | None = None):
        super().__init__(message)
        self.tool_call_id = tool_call_id


class UnknownAgentTool(AgentToolError):
    pass


class AgentToolAuthorizationError(AgentToolError):
    pass


class AgentToolExecutionError(AgentToolError):
    pass


Handler = Callable[[ContractModel, AgentToolCall], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    description: str
    effect: AgentToolEffect
    permission: str
    input_model: type[ContractModel]
    handler: Handler
    autonomous_allowed: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _command_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "commandId": value.get("commandId"),
        "status": value.get("status"),
        "reused": bool(value.get("reused")),
        "error": value.get("error"),
    }


def _robot_summary(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    validation = dict(value.get("validation") or {})
    return {
        "id": value.get("id"),
        "name": value.get("name"),
        "format": value.get("format"),
        "sourceRevision": value.get("sourceRevision"),
        "sourceSha256": value.get("sha256"),
        "runtimeSha256": value.get("runtimeSha256"),
        "armDof": value.get("armDof"),
        "gripperJoints": value.get("gripperJoints"),
        "cameraNames": list(value.get("cameraNames") or []),
        "physicsReady": bool(value.get("physicsReady")),
        "wristCameraCalibrated": bool(value.get("wristCameraCalibrated")),
        "validation": {
            "passed": validation.get("passed"),
            "errors": list(validation.get("errors") or []),
            "severeInitialContacts": validation.get("severeInitialContacts"),
            "maxHomeDrift": validation.get("maxHomeDrift"),
            "closedWidthM": validation.get("closedWidthM"),
            "openWidthM": validation.get("openWidthM"),
        },
        "readiness": dict(value.get("readiness") or {}),
    }


def _trajectory_sample(trajectory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not trajectory:
        return []
    indexes = sorted({0, len(trajectory) // 2, len(trajectory) - 1})
    fields = (
        "timeSeconds",
        "phase",
        "gripperWidthM",
        "endEffectorPositionM",
        "objectPositionM",
        "objectVelocityMps",
        "contactCount",
        "finite",
    )
    return [{key: trajectory[index].get(key) for key in fields} for index in indexes]


def _evaluation_summary(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    result = dict(value.get("result") or {})
    trajectory = list(result.get("trajectory") or [])
    return {
        "id": value.get("id"),
        "status": value.get("status"),
        "robotId": value.get("robotId"),
        "worldTemplateId": value.get("worldTemplateId"),
        "policy": value.get("policy"),
        "seed": value.get("seed"),
        "success": value.get("success"),
        "failureCode": value.get("failureCode"),
        "failureDetail": value.get("failureDetail"),
        "traceId": value.get("traceId"),
        "durationSeconds": result.get("durationSeconds"),
        "physicsHz": result.get("physicsHz"),
        "controlHz": result.get("controlHz"),
        "worldRuntimeSha256": result.get("worldRuntimeSha256"),
        "predicate": dict(result.get("predicate") or {}),
        "contactSummary": dict(result.get("contactSummary") or {}),
        "phases": list(result.get("phases") or []),
        "frameHashes": dict(result.get("frameHashes") or {}),
        "trajectoryCount": len(trajectory),
        "trajectorySample": _trajectory_sample(trajectory),
        "startedAt": value.get("startedAt"),
        "finishedAt": value.get("finishedAt"),
    }


async def _list_models(_: ContractModel, __: AgentToolCall) -> dict[str, Any]:
    return {"models": await control_catalog.list_models()}


async def _get_model(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ModelTargetToolInput)
    return {"model": await control_catalog.get_model(payload.model_id)}


async def _register_model(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ModelRegistrationCreate)
    return await control_catalog.register_model(payload, idempotency_key=call.idempotency_key, actor=call.actor)


async def _validate_model(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ValidateModelToolInput)
    return await control_catalog.validate_model(
        payload.model_id,
        compute_content_hash=payload.compute_content_hash,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _load_model(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ModelTargetToolInput)
    return await control_catalog.load_model(payload.model_id, idempotency_key=call.idempotency_key, actor=call.actor)


async def _unload_model(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ModelTargetToolInput)
    return await control_catalog.unload_model(payload.model_id, idempotency_key=call.idempotency_key, actor=call.actor)


async def _probe_model_worker(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ModelTargetToolInput)
    model = await control_catalog.get_model(payload.model_id)
    if model["providerType"] != "local_path" or "vla_policy" not in model["roles"]:
        raise ValueError("Worker probe currently supports local VLA policy registrations only.")
    return {
        "workerProbe": await asyncio.to_thread(
            vla_policy_worker.probe_checkpoint,
            str(model["localPath"] or ""),
            str(model["expectedDevice"] or "cuda"),
        )
    }


async def _stop_vla_worker(_: ContractModel, __: AgentToolCall) -> dict[str, Any]:
    await asyncio.to_thread(vla_policy_worker.kill)
    reconciled = await control_catalog.reconcile_local_worker_state()
    return {"stopped": True, "reconciledModelRegistrations": reconciled, "worker": vla_policy_worker.status()}


async def _list_robots(_: ContractModel, __: AgentToolCall) -> dict[str, Any]:
    return {"robots": await robot_catalog.list_registered()}


async def _get_robot(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, RobotTargetToolInput)
    rows = await robot_catalog.list_registered()
    try:
        return {"robot": next(row for row in rows if row["id"] == payload.robot_id)}
    except StopIteration as exc:
        raise KeyError(payload.robot_id) from exc


async def _register_franka(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, FrankaRegistrationRequest)
    value = await robot_catalog.register_franka(payload, idempotency_key=call.idempotency_key, actor=call.actor)
    nested = dict(value.get("result") or {})
    return {
        "command": _command_metadata(value),
        "robot": _robot_summary(nested.get("robot")),
        "registration": nested.get("registration"),
    }


async def _activate_robot(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, RobotTargetToolInput)
    return await robot_catalog.activate_robot(payload.robot_id, idempotency_key=call.idempotency_key, actor=call.actor)


async def _list_world_templates(_: ContractModel, __: AgentToolCall) -> dict[str, Any]:
    rows = await evaluation_catalog.list_world_templates()
    return {
        "worldTemplates": [
            {
                "id": row.get("id"),
                "revision": row.get("revision"),
                "name": row.get("name"),
                "backend": row.get("backend"),
                "robotId": row.get("robotId"),
                "runtimeSha256": row.get("runtimeSha256"),
                "lifecycleState": row.get("lifecycleState"),
                "validationErrors": row.get("validationErrors"),
                "supportSurfaces": (row.get("manifest") or {}).get("supportSurfaces", []),
                "targetVolumes": (row.get("manifest") or {}).get("targetVolumes", []),
            }
            for row in rows
        ]
    }


async def _list_evaluations(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, EvaluationListToolInput)
    rows = await evaluation_catalog.list_evaluations(payload.limit)
    return {"evaluations": [_evaluation_summary(row) for row in rows]}


async def _get_evaluation(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, EvaluationTargetToolInput)
    return {"evaluation": _evaluation_summary(await evaluation_catalog.get_evaluation(payload.run_id))}


async def _run_oracle(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, OracleEvaluationRequest)
    value = await evaluation_catalog.run_pick_place_oracle(
        payload,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )
    nested = dict(value.get("result") or {})
    template = dict(nested.get("worldTemplate") or {})
    return {
        "command": _command_metadata(value),
        "evaluation": _evaluation_summary(nested.get("evaluation")),
        "worldTemplate": {
            "id": template.get("id"),
            "revision": template.get("revision"),
            "runtimeBackend": template.get("runtimeBackend"),
            "runtimeSha256": template.get("runtimeSha256"),
            "robotId": template.get("robotId"),
        },
    }


async def _run_drawer_oracle(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, OracleEvaluationRequest)
    value = await evaluation_catalog.run_franka_drawer_oracle(
        payload,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )
    nested = dict(value.get("result") or {})
    return {
        "command": _command_metadata(value),
        "evaluation": _evaluation_summary(nested.get("evaluation")),
        "worldTemplate": nested.get("worldTemplate"),
    }


async def _run_compiled_asset_oracle(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, CompiledAssetOracleRequest)
    value = await evaluation_catalog.run_compiled_asset_pick_place_oracle(
        payload,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )
    nested = dict(value.get("result") or {})
    return {
        "command": _command_metadata(value),
        "evaluation": _evaluation_summary(nested.get("evaluation")),
        "assetVersion": nested.get("assetVersion"),
        "worldTemplate": nested.get("worldTemplate"),
    }


async def _run_compiled_asset_vla(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, CompiledAssetVlaEvaluationRequest)
    value = await evaluation_catalog.run_compiled_asset_pick_place_vla(
        payload,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )
    nested = dict(value.get("result") or {})
    return {
        "command": _command_metadata(value),
        "evaluation": _evaluation_summary(nested.get("evaluation")),
        "assetVersion": nested.get("assetVersion"),
        "worldTemplate": nested.get("worldTemplate"),
        "model": nested.get("model"),
        "bridge": nested.get("bridge"),
    }


async def _analyze_evaluation(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, EvaluationAnalysisRequest)
    value = await curriculum_catalog.analyze_evaluation(
        payload.evaluation_id,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )
    nested = dict(value.get("result") or {})
    return {
        "command": _command_metadata(value),
        "classification": nested.get("classification"),
        "coverageObservation": nested.get("coverageObservation"),
    }


async def _list_failure_events(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, EvaluationListToolInput)
    return {"failureEvents": await curriculum_catalog.list_failure_events(payload.limit)}


async def _coverage_state(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, CoverageStateToolInput)
    return {
        "coverage": await curriculum_catalog.coverage_state(
            robot_id=payload.robot_id,
            model_id=payload.model_id,
            task_family=payload.task_family,
            limit=payload.limit,
        )
    }


async def _plan_next_scenario(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, CurriculumPlanRequest)
    value = await curriculum_catalog.plan_next(
        payload,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )
    nested = dict(value.get("result") or {})
    return {
        "command": _command_metadata(value),
        "plan": nested.get("plan"),
        "scenario": nested.get("scenario"),
        "coverage": nested.get("coverage"),
    }


async def _execute_scenario_oracle(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ScenarioTargetToolInput)
    value = await curriculum_catalog.execute_scenario_oracle(
        payload.scenario_id,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )
    nested = dict(value.get("result") or {})
    return {
        "command": _command_metadata(value),
        "scenario": nested.get("scenario"),
        "execution": nested.get("execution"),
        "evaluation": _evaluation_summary(nested.get("evaluation")),
        "worldTemplate": nested.get("worldTemplate"),
        "analysis": nested.get("analysis"),
    }


async def _start_autonomous_run(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, AutonomousCurriculumRunRequest)
    value = await autonomous_curriculum.start_run(
        payload,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )
    return {
        "command": _command_metadata(value),
        "run": (value.get("result") or {}).get("run"),
    }


async def _list_autonomous_runs(_: ContractModel, __: AgentToolCall) -> dict[str, Any]:
    return {"runs": await autonomous_curriculum.list_runs(100)}


async def _cancel_autonomous_run(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, AutonomousRunTargetToolInput)
    return {"run": await autonomous_curriculum.cancel_run(payload.run_id, actor=call.actor)}


async def _bridge_status(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, VlaBridgeStatusToolInput)
    return {"bridge": await vla_bridge.bridge_status(payload.model_id, payload.robot_id)}


async def _attach_zero_shot_bridge(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, VlaFrankaZeroShotBridgeToolInput)
    return await vla_bridge.attach_zero_shot_bridge(
        payload.model_id,
        payload.robot_id,
        camera_mapping=payload.camera_mapping,
        policy_control_hz=payload.policy_control_hz,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _list_audit(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, AuditListToolInput)
    return {
        "events": await control_catalog.audit_history(
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            limit=payload.limit,
        )
    }


async def _list_evidence_requests(_: ContractModel, __: AgentToolCall) -> dict[str, Any]:
    return {"objectRequests": await evidence_catalog.list_requests()}


async def _get_evidence_request(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ObjectRequestTargetToolInput)
    return await evidence_catalog.get_request(payload.request_id)


async def _create_evidence_request(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ObjectRequest)
    return await evidence_catalog.create_request(payload, idempotency_key=call.idempotency_key, actor=call.actor)


async def _normalize_recorded_evidence(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, NormalizeRecordedEvidenceToolInput)
    return await evidence_catalog.normalize_recorded(
        payload.request_id,
        payload.evidence,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _get_evidence_bundle(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, EvidenceBundleTargetToolInput)
    return await evidence_catalog.get_bundle(payload.bundle_id)


async def _start_brightdata_collection(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, BrightDataCollectionToolInput)
    provider_request = BrightDataCollectionRequest.model_validate(
        payload.model_dump(exclude={"request_id"}, mode="json", by_alias=False)
    )
    return await evidence_collection.create_run(
        payload.request_id,
        provider_request,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _list_evidence_collections(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ObjectRequestTargetToolInput)
    return {"collectionRuns": await evidence_collection.list_runs(request_id=payload.request_id)}


async def _get_evidence_collection(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, EvidenceCollectionTargetToolInput)
    return {"collectionRun": await evidence_collection.get_run(payload.collection_run_id)}


async def _cancel_evidence_collection(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, EvidenceCollectionTargetToolInput)
    return {"collectionRun": await evidence_collection.cancel_run(payload.collection_run_id, actor=call.actor)}


async def _list_scraper_versions(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ScraperCollectorVersionsListToolInput)
    return {
        "collectorVersions": await scraper_repair.list_collector_versions(
            collector_id=payload.collector_id,
            limit=200,
        )
    }


async def _list_scraper_repairs(_: ContractModel, __: AgentToolCall) -> dict[str, Any]:
    return {"repairRuns": await scraper_repair.list_repair_runs(100)}


async def _request_scraper_repair(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ScraperRepairCreate)
    return await scraper_repair.create_repair_run(
        payload,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _request_provider_self_heal(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ScraperRepairTargetToolInput)
    return await scraper_repair.trigger_provider_repair(
        payload.repair_run_id,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _test_scraper_repair(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ScraperRepairTargetToolInput)
    return await scraper_repair.run_quality_tests(
        payload.repair_run_id,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _decide_scraper_repair(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ScraperRepairDecisionToolInput)
    decision = ScraperRepairDecision.model_validate(
        payload.model_dump(exclude={"repair_run_id"}, mode="json", by_alias=False)
    )
    return await scraper_repair.decide(
        payload.repair_run_id,
        decision,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _rollback_scraper_repair(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, ScraperRepairRollbackToolInput)
    rollback = ScraperRepairRollback.model_validate(
        payload.model_dump(exclude={"repair_run_id"}, mode="json", by_alias=False)
    )
    return await scraper_repair.rollback(
        payload.repair_run_id,
        rollback,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _list_asset_versions(_: ContractModel, __: AgentToolCall) -> dict[str, Any]:
    return {"assetVersions": await rigid_asset_compiler.list_versions()}


async def _get_asset_version(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, AssetVersionTargetToolInput)
    return {"assetVersion": await rigid_asset_compiler.get_version(payload.version_id)}


async def _compile_rigid_asset(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, RigidAssetCompileRequest)
    value = await rigid_asset_compiler.compile_rigid(
        payload,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )
    if value.get("status") != "SUCCEEDED":
        version = (value.get("result") or {}).get("assetVersion") or {}
        raise rigid_asset_compiler.AssetCompileError(
            f"Rigid asset candidate {version.get('id') or 'unknown'} was rejected: {value.get('error') or 'validation failed'}"
        )
    return value


async def _export_lerobot_dataset(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, LeRobotDatasetExportRequest)
    return await lerobot_dataset.export_evaluation(
        payload,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _validate_vla_jepa_fine_tune(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, VlaJepaFineTuneValidationRequest)
    return await lerobot_training.validate_candidate(
        payload,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _execute_vla_jepa_fine_tune(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, VlaJepaFineTuneExecuteRequest)
    return await lerobot_training.execute_candidate(
        payload,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _decide_policy_candidate(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, PolicyCandidateDecisionRequest)
    return await policy_lifecycle.decide(
        payload,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _rollback_policy_candidate(payload: ContractModel, call: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, PolicyCandidateRollbackRequest)
    return await policy_lifecycle.rollback(
        payload,
        idempotency_key=call.idempotency_key,
        actor=call.actor,
    )


async def _search_signoz_traces(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, SigNozTraceSearchToolInput)
    return await signoz.search_traces(
        minutes=payload.minutes,
        filter_expr=payload.filter_expression,
        limit=payload.limit,
    )


async def _query_signoz_metric(payload: ContractModel, _: AgentToolCall) -> dict[str, Any]:
    assert isinstance(payload, SigNozMetricQueryToolInput)
    return await signoz.metric_timeseries(
        payload.metric,
        minutes=payload.minutes,
        step=payload.step_seconds,
        agg=payload.aggregation,
    )


_SPECS = (
    ToolSpec("models.list", "1.0.0", "List configured model registrations and real lifecycle health.", AgentToolEffect.QUERY, "catalog.read", EmptyToolInput, _list_models),
    ToolSpec("models.get", "1.0.0", "Inspect one configured model registration and capabilities.", AgentToolEffect.QUERY, "catalog.read", ModelTargetToolInput, _get_model),
    ToolSpec("models.register", "1.0.0", "Register a model reference without storing raw secrets.", AgentToolEffect.MUTATION, "models.manage", ModelRegistrationCreate, _register_model),
    ToolSpec("models.validate", "1.0.0", "Run the configured provider/path health and capability probe.", AgentToolEffect.MUTATION, "models.manage", ValidateModelToolInput, _validate_model, autonomous_allowed=True),
    ToolSpec("models.worker_probe", "1.0.0", "Probe the isolated policy process, dependencies, offline mode, CUDA, and exact LeRobot source.", AgentToolEffect.QUERY, "models.read", ModelTargetToolInput, _probe_model_worker),
    ToolSpec("models.load", "1.0.0", "Load an AVAILABLE model through its configured adapter; never fall back to a mock.", AgentToolEffect.MUTATION, "models.manage", ModelTargetToolInput, _load_model),
    ToolSpec("models.unload", "1.0.0", "Unload a LOADED model through its configured adapter.", AgentToolEffect.MUTATION, "models.manage", ModelTargetToolInput, _unload_model),
    ToolSpec("robots.list", "1.0.0", "List canonical robot and embodiment registrations.", AgentToolEffect.QUERY, "catalog.read", EmptyToolInput, _list_robots),
    ToolSpec("robots.get", "1.0.0", "Inspect robot links, joints, sensors, controllers, and lifecycle.", AgentToolEffect.QUERY, "catalog.read", RobotTargetToolInput, _get_robot),
    ToolSpec("robots.register_default_franka", "1.0.0", "Compile and validate the pinned MuJoCo Menagerie Franka without implicit downloads.", AgentToolEffect.MUTATION, "robots.manage", FrankaRegistrationRequest, _register_franka),
    ToolSpec("robots.activate", "1.0.0", "Activate an AVAILABLE robot after reloading its immutable runtime artifact.", AgentToolEffect.MUTATION, "robots.manage", RobotTargetToolInput, _activate_robot),
    ToolSpec("world_templates.list", "1.0.0", "List reusable validated semantic world templates.", AgentToolEffect.QUERY, "catalog.read", EmptyToolInput, _list_world_templates),
    ToolSpec("evaluations.list", "1.0.0", "List bounded structured evaluation summaries.", AgentToolEffect.QUERY, "evaluations.read", EvaluationListToolInput, _list_evaluations),
    ToolSpec("evaluations.get", "1.0.0", "Retrieve bounded predicates, contacts, phase frames, and trajectory samples for one run.", AgentToolEffect.QUERY, "evaluations.read", EvaluationTargetToolInput, _get_evaluation),
    ToolSpec("evaluations.run_oracle_pick_place", "1.0.0", "Run one real deterministic Franka pick/place episode.", AgentToolEffect.MUTATION, "evaluations.run", OracleEvaluationRequest, _run_oracle),
    ToolSpec("evaluations.run_oracle_franka_drawer", "1.0.0", "Run the controlled Franka drawer oracle with real bilateral handle contact and prismatic-joint displacement predicates.", AgentToolEffect.MUTATION, "evaluations.run", OracleEvaluationRequest, _run_drawer_oracle),
    ToolSpec("evaluations.run_oracle_compiled_asset", "1.0.0", "Compose one PHYSICS_VALIDATED asset version into the Franka world and run real contact/lift/place predicates.", AgentToolEffect.MUTATION, "evaluations.run", CompiledAssetOracleRequest, _run_compiled_asset_oracle),
    ToolSpec("evaluations.run_vla_compiled_asset", "1.0.0", "Run a loaded VLA-JEPA policy against an ORACLE_VALIDATED asset in authoritative MuJoCo; never substitute scripted or random actions.", AgentToolEffect.MUTATION, "evaluations.run", CompiledAssetVlaEvaluationRequest, _run_compiled_asset_vla),
    ToolSpec("evaluations.analyze_failure", "1.0.0", "Persist a structured failure event and coverage observation from one terminal authoritative evaluation.", AgentToolEffect.MUTATION, "evaluations.analyze", EvaluationAnalysisRequest, _analyze_evaluation),
    ToolSpec("failures.list", "1.0.0", "List bounded structured failure events and evidence-derived repair routes.", AgentToolEffect.QUERY, "evaluations.read", EvaluationListToolInput, _list_failure_events),
    ToolSpec("coverage.get", "1.0.0", "Read configured pick/place coverage-bin counts without inventing unobserved success scores.", AgentToolEffect.QUERY, "coverage.read", CoverageStateToolInput, _coverage_state),
    ToolSpec("curriculum.plan_next", "1.0.0", "Persist a budget-bounded next-scenario or stop decision that reuses ORACLE_VALIDATED assets before requesting new evidence.", AgentToolEffect.MUTATION, "curriculum.plan", CurriculumPlanRequest, _plan_next_scenario),
    ToolSpec("scenarios.oracle_validate", "1.0.0", "Materialize one supported persisted pose/orientation scenario, execute the authoritative deterministic Franka oracle, and record real failure evidence.", AgentToolEffect.MUTATION, "scenarios.execute", ScenarioTargetToolInput, _execute_scenario_oracle),
    ToolSpec("curriculum.runs.start", "1.0.0", "Start a persisted budget-bounded curriculum run over canonical plan, oracle, VLA, and analysis commands.", AgentToolEffect.MUTATION, "curriculum.execute", AutonomousCurriculumRunRequest, _start_autonomous_run),
    ToolSpec("curriculum.runs.list", "1.0.0", "List persisted curriculum-run budgets, real phase progress, blockers, and terminal reasons.", AgentToolEffect.QUERY, "curriculum.read", EmptyToolInput, _list_autonomous_runs),
    ToolSpec("curriculum.runs.cancel", "1.0.0", "Request the cooperative kill switch for a non-terminal curriculum run.", AgentToolEffect.MUTATION, "curriculum.stop", AutonomousRunTargetToolInput, _cancel_autonomous_run, autonomous_allowed=True),
    ToolSpec("vla.bridge_status", "1.0.0", "Check checkpoint/robot/adapter compatibility and explicit blockers.", AgentToolEffect.QUERY, "models.read", VlaBridgeStatusToolInput, _bridge_status),
    ToolSpec("vla.attach_franka_zero_shot_bridge", "1.0.0", "Explicitly bind a loaded two-view seven-dimensional checkpoint to this exact Franka definition for uncalibrated evaluation. cameraMapping must be {observation.images.exterior_1_left: front, observation.images.exterior_2_left: wrist}; never claims training compatibility.", AgentToolEffect.MUTATION, "models.manage", VlaFrankaZeroShotBridgeToolInput, _attach_zero_shot_bridge),
    ToolSpec("audit.list", "1.0.0", "Read bounded immutable state-transition and command audit events.", AgentToolEffect.QUERY, "audit.read", AuditListToolInput, _list_audit),
    ToolSpec("telemetry.signoz.search_traces", "1.0.0", "Query bounded trace evidence from the configured self-hosted SigNoz Community instance.", AgentToolEffect.QUERY, "telemetry.read", SigNozTraceSearchToolInput, _search_signoz_traces),
    ToolSpec("telemetry.signoz.metric_timeseries", "1.0.0", "Query one bounded metric time series from the configured self-hosted SigNoz Community instance.", AgentToolEffect.QUERY, "telemetry.read", SigNozMetricQueryToolInput, _query_signoz_metric),
    ToolSpec("evidence.requests.list", "1.0.0", "List exact-object requests and evidence lifecycle state.", AgentToolEffect.QUERY, "evidence.read", EmptyToolInput, _list_evidence_requests),
    ToolSpec("evidence.requests.get", "1.0.0", "Inspect one object request, its immutable bundles, and normalized records.", AgentToolEffect.QUERY, "evidence.read", ObjectRequestTargetToolInput, _get_evidence_request),
    ToolSpec("evidence.requests.create", "1.0.0", "Create an exact or category-level object evidence request.", AgentToolEffect.MUTATION, "evidence.manage", ObjectRequest, _create_evidence_request),
    ToolSpec("evidence.recorded.normalize", "1.0.0", "Normalize a bounded recorded Bright Data/controlled collector response and run semantic identity gates.", AgentToolEffect.MUTATION, "evidence.manage", NormalizeRecordedEvidenceToolInput, _normalize_recorded_evidence),
    ToolSpec("evidence.bundles.get", "1.0.0", "Read one immutable evidence bundle with property provenance and conflicts.", AgentToolEffect.QUERY, "evidence.read", EvidenceBundleTargetToolInput, _get_evidence_bundle),
    ToolSpec("evidence.brightdata.collect", "1.0.0", "Start a durable Scraper Studio collection; provider rows must pass exact-identity quality gates.", AgentToolEffect.MUTATION, "evidence.collect", BrightDataCollectionToolInput, _start_brightdata_collection),
    ToolSpec("evidence.collections.list", "1.0.0", "List durable provider collection state for one object request.", AgentToolEffect.QUERY, "evidence.read", ObjectRequestTargetToolInput, _list_evidence_collections),
    ToolSpec("evidence.collections.get", "1.0.0", "Read one provider snapshot, heartbeat, bundle result, and terminal error.", AgentToolEffect.QUERY, "evidence.read", EvidenceCollectionTargetToolInput, _get_evidence_collection),
    ToolSpec("evidence.collections.cancel", "1.0.0", "Cancel a running provider collection without issuing a replacement request.", AgentToolEffect.MUTATION, "evidence.collect", EvidenceCollectionTargetToolInput, _cancel_evidence_collection),
    ToolSpec("scrapers.collector_versions.list", "1.0.0", "Inspect active, candidate, superseded, rejected, and rolled-back collector versions.", AgentToolEffect.QUERY, "scrapers.read", ScraperCollectorVersionsListToolInput, _list_scraper_versions),
    ToolSpec("scrapers.repairs.list", "1.0.0", "Inspect bounded repair prompts, state, golden/canary reports, schema diffs, and rollback history.", AgentToolEffect.QUERY, "scrapers.read", EmptyToolInput, _list_scraper_repairs),
    ToolSpec("scrapers.repairs.request", "1.0.0", "Create a governed repair from a real QUALITY_FAILED bundle while preserving the last-known-good collector.", AgentToolEffect.MUTATION, "scrapers.repair", ScraperRepairCreate, _request_scraper_repair),
    ToolSpec("scrapers.self_heal.request", "1.0.0", "Issue the precise stored repair prompt to Bright Data for a brightdata_live candidate; provider usage may be billable.", AgentToolEffect.MUTATION, "scrapers.provider", ScraperRepairTargetToolInput, _request_provider_self_heal),
    ToolSpec("scrapers.repairs.test", "1.0.0", "Run canonical exact-identity golden and canary gates against captured candidate output.", AgentToolEffect.MUTATION, "scrapers.repair", ScraperRepairTargetToolInput, _test_scraper_repair),
    ToolSpec("scrapers.repairs.decide", "1.0.0", "Explicitly promote or reject a candidate only after all configured gates pass.", AgentToolEffect.MUTATION, "scrapers.promote", ScraperRepairDecisionToolInput, _decide_scraper_repair),
    ToolSpec("scrapers.repairs.rollback", "1.0.0", "Restore the recorded last-known-good collector version after a governed promotion.", AgentToolEffect.MUTATION, "scrapers.rollback", ScraperRepairRollbackToolInput, _rollback_scraper_repair),
    ToolSpec("assets.versions.list", "1.0.0", "List immutable compiler versions, validation evidence, and promotion blockers.", AgentToolEffect.QUERY, "assets.read", EmptyToolInput, _list_asset_versions),
    ToolSpec("assets.versions.get", "1.0.0", "Inspect one canonical asset manifest and bounded validation report.", AgentToolEffect.QUERY, "assets.read", AssetVersionTargetToolInput, _get_asset_version),
    ToolSpec("assets.rigid.compile", "1.0.0", "Compile an allowlisted immutable GLB into separately validated OpenUSD and MuJoCo physical artifacts.", AgentToolEffect.MUTATION, "assets.manage", RigidAssetCompileRequest, _compile_rigid_asset),
    ToolSpec("training.datasets.create_from_evaluation", "1.0.0", "Export one successful deterministic-oracle evaluation with synchronized two-camera observations into a locally validated LeRobot dataset; never launches training or pushes to Hub.", AgentToolEffect.MUTATION, "training.datasets.manage", LeRobotDatasetExportRequest, _export_lerobot_dataset),
    ToolSpec("training.vla_jepa.validate_fine_tune", "1.0.0", "Create a durable local-only VLA-JEPA fine-tuning candidate and validate its exact dataset, checkpoint, dependency, device, and output contracts without executing optimization.", AgentToolEffect.MUTATION, "training.runs.manage", VlaJepaFineTuneValidationRequest, _validate_vla_jepa_fine_tune),
    ToolSpec("training.vla_jepa.execute_fine_tune", "1.0.0", "Execute an explicitly approved READY VLA-JEPA candidate for its bounded 1-10 step local optimizer profile; writes a new immutable checkpoint and never replaces the active policy.", AgentToolEffect.MUTATION, "training.runs.execute", VlaJepaFineTuneExecuteRequest, _execute_vla_jepa_fine_tune),
    ToolSpec("training.policy_candidates.decide", "1.0.0", "Promote or reject an immutable policy candidate from exact terminal evaluation IDs. Promotion requires the configured passing multi-seed gate and preserves a rollback model.", AgentToolEffect.MUTATION, "training.policies.promote", PolicyCandidateDecisionRequest, _decide_policy_candidate),
    ToolSpec("training.policy_candidates.rollback", "1.0.0", "Restore the recorded previous policy for a PROMOTED candidate and append an immutable rollback audit event.", AgentToolEffect.MUTATION, "training.policies.rollback", PolicyCandidateRollbackRequest, _rollback_policy_candidate),
    ToolSpec("workers.vla_jepa.stop", "1.0.0", "Immediately terminate the local VLA-JEPA worker and reconcile resident model state.", AgentToolEffect.MUTATION, "workers.stop", EmptyToolInput, _stop_vla_worker),
)
REGISTRY = {spec.name: spec for spec in _SPECS}


def definitions() -> list[dict[str, Any]]:
    output_schema = AgentToolCallResult.model_json_schema(by_alias=True)
    return [
        AgentToolDefinition(
            name=spec.name,
            version=spec.version,
            description=spec.description,
            effect=spec.effect,
            permission=spec.permission,
            input_schema=spec.input_model.model_json_schema(by_alias=True),
            output_schema=output_schema,
            idempotency_supported=spec.effect == AgentToolEffect.MUTATION,
            approval_required=spec.effect == AgentToolEffect.MUTATION,
            autonomous_allowed=spec.autonomous_allowed,
        ).model_dump(mode="json", by_alias=True)
        for spec in _SPECS
    ]


async def create_approval(payload: ApprovalDecision) -> dict[str, Any]:
    spec = REGISTRY.get(payload.tool_name)
    if spec is None:
        raise UnknownAgentTool(f"Unknown agent tool '{payload.tool_name}'.")
    if spec.effect != AgentToolEffect.MUTATION:
        raise AgentToolAuthorizationError("Query tools do not require approval decisions.")
    try:
        parsed = spec.input_model.model_validate(payload.arguments)
    except ValidationError as exc:
        raise AgentToolError(f"Arguments failed the {spec.name} schema: {exc}") from exc
    arguments = parsed.model_dump(mode="json", by_alias=True)
    digest = command_store.payload_hash(arguments)
    row = ApprovalDecisionRecord(
        id=new_id("approval"),
        tool_name=spec.name,
        arguments_sha256=digest,
        approved=payload.approved,
        reason=payload.reason,
        decided_by=payload.decided_by,
        expires_at=_now() + timedelta(seconds=payload.expires_in_seconds),
    )
    async with SessionLocal() as session:
        session.add(row)
        session.add(
            AuditEvent(
                command_id=None,
                entity_type="approval",
                entity_id=row.id,
                action="approval.decide",
                from_state=None,
                to_state="APPROVED" if row.approved else "REJECTED",
                detail={"toolName": row.tool_name, "argumentsSha256": digest, "expiresAt": row.expires_at.isoformat()},
                actor=row.decided_by,
            )
        )
        await session.commit()
    return approval_view(row)


def approval_view(row: ApprovalDecisionRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "toolName": row.tool_name,
        "argumentsSha256": row.arguments_sha256,
        "approved": row.approved,
        "reason": row.reason,
        "decidedBy": row.decided_by,
        "expiresAt": row.expires_at,
        "consumedAt": row.consumed_at,
        "createdAt": row.created_at,
    }


async def _consume_approval(decision_id: str | None, spec: ToolSpec, digest: str) -> None:
    if not decision_id:
        raise AgentToolAuthorizationError("This mutation requires a matching one-use approvalDecisionId.")
    async with SessionLocal() as session:
        row = await session.get(ApprovalDecisionRecord, decision_id)
        if row is None:
            raise AgentToolAuthorizationError("Approval decision was not found.")
        expiry = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        if not row.approved:
            raise AgentToolAuthorizationError("Approval decision rejected this operation.")
        if expiry <= _now():
            raise AgentToolAuthorizationError("Approval decision has expired.")
        if row.tool_name != spec.name or row.arguments_sha256 != digest:
            raise AgentToolAuthorizationError("Approval decision does not match this exact tool and normalized arguments.")
        result = await session.execute(
            update(ApprovalDecisionRecord)
            .where(ApprovalDecisionRecord.id == decision_id, ApprovalDecisionRecord.consumed_at.is_(None))
            .values(consumed_at=_now())
        )
        if result.rowcount != 1:
            raise AgentToolAuthorizationError("Approval decision has already been consumed.")
        await session.commit()


async def _start_call(spec: ToolSpec, call: AgentToolCall, arguments: dict[str, Any], digest: str) -> AgentToolCallRecord:
    row = AgentToolCallRecord(
        id=new_id("toolcall"),
        tool_name=spec.name,
        tool_version=spec.version,
        effect=str(spec.effect),
        autonomy_mode=str(call.autonomy_mode),
        status="RUNNING",
        arguments_sha256=digest,
        arguments=command_store.json_safe(arguments),
        approval_decision_id=call.approval_decision_id,
        actor=call.actor,
    )
    async with SessionLocal() as session:
        session.add(row)
        await session.commit()
    return row


async def _finish_call(
    tool_call_id: str,
    *,
    status: str,
    output: dict[str, Any] | None = None,
    error: str | None = None,
) -> AgentToolCallRecord:
    async with SessionLocal() as session:
        row = await session.get(AgentToolCallRecord, tool_call_id)
        assert row is not None
        safe_output = command_store.json_safe(output or {})
        row.status = status
        row.output = safe_output
        row.error = error
        row.command_id = str(safe_output.get("commandId") or (safe_output.get("command") or {}).get("commandId") or "") or None
        row.finished_at = _now()
        session.add(
            AuditEvent(
                command_id=row.command_id,
                entity_type="agent_tool_call",
                entity_id=row.id,
                action="agent.tool.invoke",
                from_state="RUNNING",
                to_state=status,
                detail={
                    "toolName": row.tool_name,
                    "toolVersion": row.tool_version,
                    "argumentsSha256": row.arguments_sha256,
                    "autonomyMode": row.autonomy_mode,
                },
                actor=row.actor,
            )
        )
        await session.commit()
        return row


async def invoke(call: AgentToolCall) -> dict[str, Any]:
    spec = REGISTRY.get(call.tool_name)
    if spec is None:
        raise UnknownAgentTool(f"Unknown agent tool '{call.tool_name}'.")
    try:
        parsed = spec.input_model.model_validate(call.arguments)
    except ValidationError as exc:
        raise AgentToolError(f"Arguments failed the {spec.name} schema: {exc}") from exc
    arguments = parsed.model_dump(mode="json", by_alias=True)
    digest = command_store.payload_hash(arguments)
    row = await _start_call(spec, call, arguments, digest)
    try:
        if spec.effect == AgentToolEffect.MUTATION:
            if call.autonomy_mode in {AutonomyMode.OBSERVE_ONLY, AutonomyMode.PLAN_ONLY}:
                raise AgentToolAuthorizationError(f"{call.autonomy_mode} cannot execute mutation tool {spec.name}.")
            if call.autonomy_mode == AutonomyMode.EXECUTE_WITH_APPROVAL:
                await _consume_approval(call.approval_decision_id, spec, digest)
            elif call.autonomy_mode == AutonomyMode.AUTONOMOUS_WITH_BUDGETS and not spec.autonomous_allowed:
                raise AgentToolAuthorizationError(
                    f"{spec.name} is not enabled for autonomous execution; a matching approval is required."
                )
        data = await spec.handler(parsed, call)
    except AgentToolAuthorizationError as exc:
        await _finish_call(row.id, status="DENIED", error=str(exc))
        raise AgentToolAuthorizationError(str(exc), tool_call_id=row.id) from exc
    except Exception as exc:
        await _finish_call(row.id, status="FAILED", error=str(exc))
        raise AgentToolExecutionError(str(exc), tool_call_id=row.id) from exc
    finished = await _finish_call(row.id, status="SUCCEEDED", output=data)
    result = AgentToolCallResult(
        tool_call_id=finished.id,
        tool_name=spec.name,
        tool_version=spec.version,
        status=finished.status,
        data=dict(finished.output or {}),
        command_id=finished.command_id,
    )
    return result.model_dump(mode="json", by_alias=True)


def tool_call_view(row: AgentToolCallRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "toolName": row.tool_name,
        "toolVersion": row.tool_version,
        "effect": row.effect,
        "autonomyMode": row.autonomy_mode,
        "status": row.status,
        "argumentsSha256": row.arguments_sha256,
        "output": dict(row.output or {}),
        "error": row.error,
        "commandId": row.command_id,
        "approvalDecisionId": row.approval_decision_id,
        "actor": row.actor,
        "startedAt": row.started_at,
        "finishedAt": row.finished_at,
    }


async def list_calls(limit: int = 100) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(AgentToolCallRecord).order_by(AgentToolCallRecord.started_at.desc()).limit(max(1, min(limit, 200)))
            )
        ).scalars().all()
    return [tool_call_view(row) for row in rows]
