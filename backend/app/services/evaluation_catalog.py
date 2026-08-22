"""Durable Franka oracle evaluation command and world-template catalog."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import WORLDS_DIR
from ..contracts import CompiledAssetOracleRequest, CompiledAssetVlaEvaluationRequest, EvaluationResultContract, OracleEvaluationRequest
from ..db import SessionLocal
from ..models import AuditEvent, CompiledAssetVersionRecord, EvaluationRunRecord, ModelRegistrationRecord, RobotRegistrationRecord, WorldTemplateRecord
from ..telemetry import span
from ..util import new_id
from . import command_store, franka_articulation, franka_pick_place, franka_vla_evaluation, rigid_asset_compiler, vla_bridge, vla_policy_worker


class EvaluationConflict(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def evaluation_view(row: EvaluationRunRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "status": row.status,
        "robotId": row.robot_id,
        "worldTemplateId": row.world_template_id,
        "policy": row.policy,
        "seed": row.seed,
        "success": row.success,
        "failureCode": row.failure_code,
        "failureDetail": row.failure_detail,
        "result": dict(row.result or {}),
        "artifactDir": row.artifact_dir,
        "traceId": row.trace_id,
        "startedAt": row.started_at,
        "finishedAt": row.finished_at,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


async def _audit_transition(
    session,
    row: EvaluationRunRecord,
    target: str,
    *,
    command_id: str,
    actor: str = "user",
    detail: dict[str, Any] | None = None,
) -> None:
    source = row.status
    allowed = {
        "QUEUED": {"STARTING", "CANCELLED"},
        "STARTING": {"RUNNING", "CRASHED", "CANCELLED"},
        "RUNNING": {"SUCCEEDED", "FAILED", "CRASHED", "CANCELLED"},
    }
    if target not in allowed.get(source, set()):
        raise EvaluationConflict(f"Invalid evaluation transition {source} -> {target}.")
    row.status = target
    row.updated_at = _now()
    session.add(
        AuditEvent(
            command_id=command_id,
            entity_type="evaluation",
            entity_id=row.id,
            action="evaluation.transition",
            from_state=source,
            to_state=target,
            detail=detail or {},
            actor=actor,
        )
    )


async def ensure_world_template(robot_id: str) -> dict[str, Any]:
    template = await asyncio.to_thread(franka_pick_place.compile_world_template, robot_id)
    record_id = f"{template['id']}:{robot_id}"
    async with SessionLocal() as session:
        row = await session.get(WorldTemplateRecord, record_id)
        if row is None:
            row = WorldTemplateRecord(
                id=record_id,
                revision=int(template["revision"]),
                name=str(template["name"]),
                backend=str(template["runtimeBackend"]),
                robot_id=robot_id,
                manifest=template,
                runtime_sha256=str(template["runtimeSha256"]),
                lifecycle_state="AVAILABLE",
                validation_errors=[],
            )
            session.add(row)
        elif row.runtime_sha256 != template["runtimeSha256"]:
            raise EvaluationConflict("World runtime changed for an immutable template/robot revision; create a new template revision.")
        await session.commit()
    return template


async def ensure_articulation_world_template(robot_id: str) -> dict[str, Any]:
    template = await asyncio.to_thread(franka_articulation.compile_world_template, robot_id)
    record_id = f"{template['id']}:{robot_id}"
    async with SessionLocal() as session:
        row = await session.get(WorldTemplateRecord, record_id)
        if row is None:
            row = WorldTemplateRecord(
                id=record_id,
                revision=int(template["revision"]),
                name=str(template["name"]),
                backend=str(template["runtimeBackend"]),
                robot_id=robot_id,
                manifest=template,
                runtime_sha256=str(template["runtimeSha256"]),
                lifecycle_state="AVAILABLE",
                validation_errors=[],
            )
            session.add(row)
        elif row.runtime_sha256 != template["runtimeSha256"]:
            raise EvaluationConflict(
                "Controlled articulation world changed for an immutable template/robot revision."
            )
        await session.commit()
    return template


async def ensure_compiled_asset_world_template(
    robot_id: str,
    asset_version: dict[str, Any],
    *,
    placement_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    template = await asyncio.to_thread(
        franka_pick_place.compile_compiled_asset_world_template,
        robot_id,
        asset_version,
        placement_request=placement_request,
    )
    record_id = f"{template['id']}:{robot_id}"
    if len(record_id) > 100:
        raise EvaluationConflict("Compiled-asset world-template identity exceeds the catalog limit.")
    async with SessionLocal() as session:
        row = await session.get(WorldTemplateRecord, record_id)
        if row is None:
            row = WorldTemplateRecord(
                id=record_id,
                revision=int(template["revision"]),
                name=str(template["name"]),
                backend=str(template["runtimeBackend"]),
                robot_id=robot_id,
                manifest=template,
                runtime_sha256=str(template["runtimeSha256"]),
                lifecycle_state="AVAILABLE",
                validation_errors=[],
            )
            session.add(row)
        elif row.runtime_sha256 != template["runtimeSha256"]:
            raise EvaluationConflict("Compiled-asset world changed for an immutable robot/asset revision.")
        await session.commit()
    return template


async def ensure_authored_scene_world_template(
    robot_id: str,
    asset_version: dict[str, Any],
    scene_spec: dict[str, Any],
) -> dict[str, Any]:
    template = await asyncio.to_thread(
        franka_pick_place.compile_authored_scene_asset_world,
        robot_id,
        asset_version,
        world_id=scene_spec["worldId"],
        source_placement=scene_spec["sourcePlacement"],
        target_placement=scene_spec["targetPlacement"],
        counter_placement=scene_spec["counterPlacement"],
        robot_spawn=scene_spec.get("robotSpawn"),
        task_kind=str(scene_spec.get("taskKind") or "pick_place"),
        relation=str(scene_spec.get("relation") or "on_top_of"),
    )
    record_id = f"authored:{scene_spec['worldId'][:20]}:{asset_version['id']}:{template['runtimeSha256'][:12]}"
    async with SessionLocal() as session:
        row = await session.get(WorldTemplateRecord, record_id)
        if row is None:
            row = WorldTemplateRecord(
                id=record_id,
                revision=int(template["revision"]),
                name=str(template["name"]),
                backend=str(template["runtimeBackend"]),
                robot_id=robot_id,
                manifest=template,
                runtime_sha256=str(template["runtimeSha256"]),
                lifecycle_state="AVAILABLE",
                validation_errors=[],
            )
            session.add(row)
        elif row.runtime_sha256 != template["runtimeSha256"]:
            raise EvaluationConflict("Authored scene runtime changed for an immutable template identity.")
        await session.commit()
    return template


async def list_world_templates() -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(WorldTemplateRecord).order_by(WorldTemplateRecord.created_at.desc()))).scalars().all()
    return [
        {
            "id": row.id,
            "revision": row.revision,
            "name": row.name,
            "backend": row.backend,
            "robotId": row.robot_id,
            "manifest": row.manifest,
            "runtimeSha256": row.runtime_sha256,
            "lifecycleState": row.lifecycle_state,
            "validationErrors": row.validation_errors,
            "createdAt": row.created_at,
            "updatedAt": row.updated_at,
        }
        for row in rows
    ]


async def run_pick_place_oracle(
    request: OracleEvaluationRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
    live_frame_callback: Any | None = None,
    realtime: bool = False,
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    try:
        command, reused = await command_store.start_command(
            kind="evaluation.oracle.pick_place",
            target_type="robot",
            target_id=request.robot_id,
            payload=payload,
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except command_store.CommandConflict as exc:
        raise EvaluationConflict(str(exc)) from exc
    if reused:
        return command_store.command_view(command, reused=True)
    async with SessionLocal() as session:
        robot = await session.get(RobotRegistrationRecord, request.robot_id)
        if robot is None:
            await command_store.finish_command(command.id, error="Robot registration not found.")
            raise KeyError(request.robot_id)
        if robot.lifecycle_state != "AVAILABLE" or not robot.active:
            message = "The requested robot must be AVAILABLE and active before evaluation."
            await command_store.finish_command(command.id, error=message)
            raise EvaluationConflict(message)
    template = await ensure_world_template(request.robot_id)
    run_id = new_id("eval")
    artifact_dir = (WORLDS_DIR / franka_pick_place.TEMPLATE_ID / "evaluations" / run_id).resolve()
    row = EvaluationRunRecord(
        id=run_id,
        status="QUEUED",
        robot_id=request.robot_id,
        world_template_id=str(template["id"]),
        policy="deterministic_differential_ik_oracle_v1",
        seed=request.seed,
        artifact_dir=str(artifact_dir),
    )
    async with SessionLocal() as session:
        session.add(row)
        session.add(
            AuditEvent(
                command_id=command.id,
                entity_type="evaluation",
                entity_id=run_id,
                action="evaluation.create",
                from_state=None,
                to_state="QUEUED",
                detail={"robotId": request.robot_id, "seed": request.seed},
                actor=actor,
            )
        )
        await session.commit()
    try:
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            await _audit_transition(session, active, "STARTING", command_id=command.id, actor=actor)
            active.started_at = _now()
            await session.commit()
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            await _audit_transition(session, active, "RUNNING", command_id=command.id, actor=actor)
            await session.commit()
        with span(
            "robot.oracle_evaluate",
            run_id=run_id,
            robot_id=request.robot_id,
            world_template=franka_pick_place.TEMPLATE_ID,
            seed=request.seed,
        ):
            raw_result = await asyncio.to_thread(
                franka_pick_place.run_oracle,
                request.robot_id,
                run_id,
                request.seed,
                live_frame_callback=live_frame_callback,
                realtime=realtime,
            )
        result = EvaluationResultContract.model_validate(raw_result).model_dump(mode="json", by_alias=True)
        terminal = "SUCCEEDED" if result["success"] else "FAILED"
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            active.success = bool(result["success"])
            active.failure_code = result.get("failureCode")
            active.failure_detail = result.get("failureDetail")
            active.result = result
            active.finished_at = _now()
            await _audit_transition(
                session,
                active,
                terminal,
                command_id=command.id,
                actor=actor,
                detail={"success": result["success"], "failureCode": result.get("failureCode")},
            )
            await session.commit()
            view = evaluation_view(active)
    except Exception as exc:
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            if active is not None and active.status in {"STARTING", "RUNNING"}:
                active.failure_code = "worker_crash"
                active.failure_detail = str(exc)
                active.finished_at = _now()
                await _audit_transition(session, active, "CRASHED", command_id=command.id, actor=actor, detail={"error": str(exc)})
                await session.commit()
        await command_store.finish_command(command.id, error=str(exc))
        raise
    output = {"evaluation": view, "worldTemplate": template}
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_store.command_view(command)


async def run_franka_drawer_oracle(
    request: OracleEvaluationRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    try:
        command, reused = await command_store.start_command(
            kind="evaluation.oracle.franka_drawer_open",
            target_type="robot",
            target_id=request.robot_id,
            payload=payload,
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except command_store.CommandConflict as exc:
        raise EvaluationConflict(str(exc)) from exc
    if reused:
        return command_store.command_view(command, reused=True)
    async with SessionLocal() as session:
        robot = await session.get(RobotRegistrationRecord, request.robot_id)
        if robot is None:
            await command_store.finish_command(command.id, error="Robot registration not found.")
            raise KeyError(request.robot_id)
        if robot.lifecycle_state != "AVAILABLE" or not robot.active:
            message = "The requested robot must be AVAILABLE and active before evaluation."
            await command_store.finish_command(command.id, error=message)
            raise EvaluationConflict(message)
    template = await ensure_articulation_world_template(request.robot_id)
    run_id = new_id("eval")
    artifact_dir = (WORLDS_DIR / franka_articulation.TEMPLATE_ID / "runs" / run_id).resolve()
    row = EvaluationRunRecord(
        id=run_id,
        status="QUEUED",
        robot_id=request.robot_id,
        world_template_id=str(template["id"]),
        policy=franka_articulation.ORACLE_POLICY,
        seed=request.seed,
        artifact_dir=str(artifact_dir),
    )
    async with SessionLocal() as session:
        session.add(row)
        session.add(
            AuditEvent(
                command_id=command.id,
                entity_type="evaluation",
                entity_id=run_id,
                action="evaluation.create",
                from_state=None,
                to_state="QUEUED",
                detail={
                    "robotId": request.robot_id,
                    "seed": request.seed,
                    "taskFamily": "open_drawer",
                    "truthMode": template["truthMode"],
                },
                actor=actor,
            )
        )
        await session.commit()
    try:
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            await _audit_transition(session, active, "STARTING", command_id=command.id, actor=actor)
            active.started_at = _now()
            await session.commit()
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            await _audit_transition(session, active, "RUNNING", command_id=command.id, actor=actor)
            await session.commit()
        with span(
            "robot.oracle_evaluate",
            run_id=run_id,
            robot_id=request.robot_id,
            world_template=franka_articulation.TEMPLATE_ID,
            task_family="open_drawer",
            seed=request.seed,
        ):
            raw_result = await asyncio.to_thread(
                franka_articulation.run_oracle, request.robot_id, run_id, request.seed
            )
        result = EvaluationResultContract.model_validate(raw_result).model_dump(mode="json", by_alias=True)
        terminal = "SUCCEEDED" if result["success"] else "FAILED"
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            active.success = bool(result["success"])
            active.failure_code = result.get("failureCode")
            active.failure_detail = result.get("failureDetail")
            active.result = result
            active.finished_at = _now()
            await _audit_transition(
                session,
                active,
                terminal,
                command_id=command.id,
                actor=actor,
                detail={
                    "success": result["success"],
                    "failureCode": result.get("failureCode"),
                    "drawerDisplacementM": result["predicate"].get("drawerDisplacementM"),
                },
            )
            await session.commit()
            view = evaluation_view(active)
    except Exception as exc:
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            if active is not None and active.status in {"STARTING", "RUNNING"}:
                active.failure_code = "worker_crash"
                active.failure_detail = str(exc)
                active.finished_at = _now()
                await _audit_transition(
                    session,
                    active,
                    "CRASHED",
                    command_id=command.id,
                    actor=actor,
                    detail={"error": str(exc)},
                )
                await session.commit()
        await command_store.finish_command(command.id, error=str(exc))
        raise
    output = {"evaluation": view, "worldTemplate": template}
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_store.command_view(command)


async def run_compiled_asset_pick_place_oracle(
    request: CompiledAssetOracleRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
    live_frame_callback: Any | None = None,
    realtime: bool = False,
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    try:
        command, reused = await command_store.start_command(
            kind="evaluation.oracle.compiled_asset_pick_place",
            target_type="asset_version",
            target_id=request.asset_version_id,
            payload=payload,
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except command_store.CommandConflict as exc:
        raise EvaluationConflict(str(exc)) from exc
    if reused:
        return command_store.command_view(command, reused=True)
    async with SessionLocal() as session:
        robot = await session.get(RobotRegistrationRecord, request.robot_id)
        asset_row = await session.get(CompiledAssetVersionRecord, request.asset_version_id)
        if robot is None:
            await command_store.finish_command(command.id, error="Robot registration not found.")
            raise KeyError(request.robot_id)
        if asset_row is None:
            await command_store.finish_command(command.id, error="Compiled asset version not found.")
            raise KeyError(request.asset_version_id)
        if robot.lifecycle_state != "AVAILABLE" or not robot.active:
            message = "The requested robot must be AVAILABLE and active before evaluation."
            await command_store.finish_command(command.id, error=message)
            raise EvaluationConflict(message)
        if asset_row.lifecycle_state not in {"PHYSICS_VALIDATED", "ORACLE_VALIDATED"}:
            message = "The compiled asset must be PHYSICS_VALIDATED before robot evaluation."
            await command_store.finish_command(command.id, error=message)
            raise EvaluationConflict(message)
    asset_version = await rigid_asset_compiler.get_version(request.asset_version_id)
    placement_request = (
        request.placement_request.model_dump(mode="json", by_alias=True)
        if request.placement_request is not None
        else None
    )
    template = await ensure_compiled_asset_world_template(
        request.robot_id,
        asset_version,
        placement_request=placement_request,
    )
    run_id = new_id("eval")
    artifact_dir = (WORLDS_DIR / franka_pick_place.TEMPLATE_ID / "evaluations" / run_id).resolve()
    row = EvaluationRunRecord(
        id=run_id,
        status="QUEUED",
        robot_id=request.robot_id,
        world_template_id=str(template["id"]),
        policy=franka_pick_place.COMPILED_ASSET_ORACLE_POLICY,
        seed=request.seed,
        artifact_dir=str(artifact_dir),
    )
    async with SessionLocal() as session:
        session.add(row)
        session.add(
            AuditEvent(
                command_id=command.id,
                entity_type="evaluation",
                entity_id=run_id,
                action="evaluation.create",
                from_state=None,
                to_state="QUEUED",
                detail={
                    "robotId": request.robot_id,
                    "assetVersionId": request.asset_version_id,
                    "assetManifestSha256": asset_version["manifestSha256"],
                    "seed": request.seed,
                },
                actor=actor,
            )
        )
        await session.commit()
    try:
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            await _audit_transition(session, active, "STARTING", command_id=command.id, actor=actor)
            active.started_at = _now()
            await session.commit()
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            await _audit_transition(session, active, "RUNNING", command_id=command.id, actor=actor)
            await session.commit()
        with span(
            "robot.oracle_evaluate",
            run_id=run_id,
            robot_id=request.robot_id,
            world_template=template["id"],
            asset_version_id=request.asset_version_id,
            seed=request.seed,
        ):
            raw_result, executed_template = await asyncio.to_thread(
                franka_pick_place.run_compiled_asset_oracle,
                request.robot_id,
                asset_version,
                run_id,
                request.seed,
                placement_request,
                request.record_observations,
                live_frame_callback,
                realtime,
            )
        result = EvaluationResultContract.model_validate(raw_result).model_dump(mode="json", by_alias=True)
        terminal = "SUCCEEDED" if result["success"] else "FAILED"
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            asset_row = await session.get(CompiledAssetVersionRecord, request.asset_version_id)
            assert active is not None and asset_row is not None
            active.success = bool(result["success"])
            active.failure_code = result.get("failureCode")
            active.failure_detail = result.get("failureDetail")
            active.result = result
            active.finished_at = _now()
            await _audit_transition(
                session,
                active,
                terminal,
                command_id=command.id,
                actor=actor,
                detail={
                    "success": result["success"],
                    "failureCode": result.get("failureCode"),
                    "assetVersionId": request.asset_version_id,
                },
            )
            previous_asset_state = asset_row.lifecycle_state
            action = "asset.scenario_oracle_observe" if placement_request is not None else "asset.oracle_validate"
            if placement_request is None:
                blockers = [
                    item
                    for item in (asset_row.promotion_blockers or [])
                    if item != "deterministic_oracle_validation_pending"
                    and not item.startswith("deterministic_oracle_validation_failed")
                ]
                if result["success"]:
                    asset_row.lifecycle_state = "ORACLE_VALIDATED"
                else:
                    blockers.append(f"deterministic_oracle_validation_failed:{result.get('failureCode') or 'unknown'}")
                report = dict(asset_row.validation_report or {})
                report["oracleValidation"] = {
                    "evaluationId": run_id,
                    "success": result["success"],
                    "failureCode": result.get("failureCode"),
                    "failureDetail": result.get("failureDetail"),
                    "robotId": request.robot_id,
                    "robotWorldRuntimeSha256": executed_template["runtimeSha256"],
                    "seed": request.seed,
                    "predicate": result["predicate"],
                    "contactSummary": result["contactSummary"],
                }
                asset_row.validation_report = report
                asset_row.promotion_blockers = blockers
                asset_row.promotion_eligible = False
                asset_row.updated_at = _now()
            session.add(
                AuditEvent(
                    command_id=command.id,
                    entity_type="asset_version",
                    entity_id=asset_row.id,
                    action=action,
                    from_state=previous_asset_state,
                    to_state=asset_row.lifecycle_state,
                    detail={
                        "evaluationId": run_id,
                        "success": result["success"],
                        "failureCode": result.get("failureCode"),
                        "placementFingerprint": executed_template.get("placementFingerprint"),
                    },
                    actor=actor,
                )
            )
            await session.commit()
            view = evaluation_view(active)
            asset_view = rigid_asset_compiler.asset_version_view(asset_row)
    except Exception as exc:
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            if active is not None and active.status in {"STARTING", "RUNNING"}:
                active.failure_code = "worker_crash"
                active.failure_detail = str(exc)
                active.finished_at = _now()
                await _audit_transition(session, active, "CRASHED", command_id=command.id, actor=actor, detail={"error": str(exc)})
                await session.commit()
        await command_store.finish_command(command.id, error=str(exc))
        raise
    output = {"evaluation": view, "worldTemplate": template, "assetVersion": asset_view}
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_store.command_view(command)


async def run_authored_scene_pick_place_oracle(
    *,
    robot_id: str,
    asset_version_id: str,
    seed: int,
    scene_spec: dict[str, Any],
    idempotency_key: str | None,
    actor: str = "user",
    live_frame_callback: Any | None = None,
    realtime: bool = False,
    task_kind: str = "pick_place",
) -> dict[str, Any]:
    if task_kind not in {"pick_place", "drop_off_table"}:
        raise EvaluationConflict(f"Unsupported authored-scene oracle task: {task_kind}")
    payload = {
        "robotId": robot_id,
        "assetVersionId": asset_version_id,
        "seed": seed,
        "worldId": scene_spec["worldId"],
        "sourceAssetId": scene_spec["sourcePlacement"]["assetId"],
        "targetAssetId": (scene_spec.get("targetPlacement") or {}).get("assetId"),
        "taskKind": task_kind,
    }
    try:
        command, reused = await command_store.start_command(
            kind=f"evaluation.oracle.authored_scene_{task_kind}",
            target_type="world",
            target_id=scene_spec["worldId"],
            payload=payload,
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except command_store.CommandConflict as exc:
        raise EvaluationConflict(str(exc)) from exc
    if reused:
        return command_store.command_view(command, reused=True)
    async with SessionLocal() as session:
        robot = await session.get(RobotRegistrationRecord, robot_id)
        asset_row = await session.get(CompiledAssetVersionRecord, asset_version_id)
        if robot is None or asset_row is None:
            await command_store.finish_command(command.id, error="Robot or compiled asset version was not found.")
            raise KeyError(robot_id if robot is None else asset_version_id)
        if robot.lifecycle_state != "AVAILABLE" or not robot.active:
            message = "The requested robot must be AVAILABLE and active before authored-world evaluation."
            await command_store.finish_command(command.id, error=message)
            raise EvaluationConflict(message)
        if asset_row.lifecycle_state not in {"PHYSICS_VALIDATED", "ORACLE_VALIDATED"}:
            message = "The selected authored object requires a PHYSICS_VALIDATED compiled asset version."
            await command_store.finish_command(command.id, error=message)
            raise EvaluationConflict(message)
    asset_version = await rigid_asset_compiler.get_version(asset_version_id)
    template = await ensure_authored_scene_world_template(robot_id, asset_version, scene_spec)
    run_id = new_id("eval")
    artifact_dir = (Path(template["runtimePath"]).parent.parent / "evaluations" / run_id).resolve()
    row = EvaluationRunRecord(
        id=run_id,
        status="QUEUED",
        robot_id=robot_id,
        world_template_id=str(template["id"]),
        policy=(
            franka_pick_place.AUTHORED_SCENE_ORACLE_POLICY
            if task_kind == "pick_place"
            else franka_pick_place.AUTHORED_SCENE_DROP_ORACLE_POLICY
        ),
        seed=seed,
        artifact_dir=str(artifact_dir),
    )
    async with SessionLocal() as session:
        session.add(row)
        session.add(AuditEvent(
            command_id=command.id,
            entity_type="evaluation",
            entity_id=run_id,
            action="evaluation.create",
            from_state=None,
            to_state="QUEUED",
            detail=payload,
            actor=actor,
        ))
        await session.commit()
    try:
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            await _audit_transition(session, active, "STARTING", command_id=command.id, actor=actor)
            active.started_at = _now()
            await session.commit()
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            await _audit_transition(session, active, "RUNNING", command_id=command.id, actor=actor)
            await session.commit()
        with span(
            "robot.oracle_evaluate",
            run_id=run_id,
            robot_id=robot_id,
            world_template=template["id"],
            asset_version_id=asset_version_id,
            authored_world_id=scene_spec["worldId"],
            seed=seed,
        ):
            raw_result, executed_template = await asyncio.to_thread(
                franka_pick_place.run_authored_scene_oracle,
                robot_id,
                asset_version,
                run_id,
                seed,
                scene_spec["worldId"],
                scene_spec["sourcePlacement"],
                scene_spec.get("targetPlacement"),
                scene_spec["counterPlacement"],
                robot_spawn=scene_spec.get("robotSpawn"),
                task_kind=task_kind,
                relation=str(scene_spec.get("relation") or "on_top_of"),
                live_frame_callback=live_frame_callback,
                realtime=realtime,
            )
        result = EvaluationResultContract.model_validate(raw_result).model_dump(mode="json", by_alias=True)
        terminal = "SUCCEEDED" if result["success"] else "FAILED"
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            active.success = bool(result["success"])
            active.failure_code = result.get("failureCode")
            active.failure_detail = result.get("failureDetail")
            active.result = result
            active.finished_at = _now()
            await _audit_transition(
                session,
                active,
                terminal,
                command_id=command.id,
                actor=actor,
                detail={"success": result["success"], "failureCode": result.get("failureCode"), **payload},
            )
            await session.commit()
            view = evaluation_view(active)
    except Exception as exc:
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            if active is not None and active.status in {"STARTING", "RUNNING"}:
                active.failure_code = "worker_crash"
                active.failure_detail = str(exc)
                active.finished_at = _now()
                await _audit_transition(session, active, "CRASHED", command_id=command.id, actor=actor, detail={"error": str(exc)})
                await session.commit()
        await command_store.finish_command(command.id, error=str(exc))
        raise
    output = {"evaluation": view, "worldTemplate": executed_template}
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_store.command_view(command)


async def run_compiled_asset_pick_place_vla(
    request: CompiledAssetVlaEvaluationRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
    scene_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    if scene_spec is not None:
        payload["worldId"] = scene_spec["worldId"]
    try:
        command, reused = await command_store.start_command(
            kind="evaluation.vla.authored_scene_pick_place" if scene_spec else "evaluation.vla.compiled_asset_pick_place",
            target_type="world" if scene_spec else "asset_version",
            target_id=scene_spec["worldId"] if scene_spec else request.asset_version_id,
            payload=payload,
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except command_store.CommandConflict as exc:
        raise EvaluationConflict(str(exc)) from exc
    if reused:
        return command_store.command_view(command, reused=True)

    async with SessionLocal() as session:
        robot = await session.get(RobotRegistrationRecord, request.robot_id)
        asset_row = await session.get(CompiledAssetVersionRecord, request.asset_version_id)
        model_row = await session.get(ModelRegistrationRecord, request.model_id)
        if robot is None:
            await command_store.finish_command(command.id, error="Robot registration not found.")
            raise KeyError(request.robot_id)
        if asset_row is None:
            await command_store.finish_command(command.id, error="Compiled asset version not found.")
            raise KeyError(request.asset_version_id)
        if model_row is None:
            await command_store.finish_command(command.id, error="Model registration not found.")
            raise KeyError(request.model_id)
        if robot.lifecycle_state != "AVAILABLE" or not robot.active:
            message = "The requested robot must be AVAILABLE and active before VLA evaluation."
            await command_store.finish_command(command.id, error=message)
            raise EvaluationConflict(message)
        allowed_asset_states = {"PHYSICS_VALIDATED", "ORACLE_VALIDATED"} if scene_spec else {"ORACLE_VALIDATED"}
        if asset_row.lifecycle_state not in allowed_asset_states:
            message = "The compiled asset must pass the required physical/oracle validation before VLA evaluation."
            await command_store.finish_command(command.id, error=message)
            raise EvaluationConflict(message)
        if model_row.lifecycle_state != "LOADED" or model_row.health_status != "healthy" or not model_row.enabled:
            message = "The requested VLA policy must be enabled, healthy, and LOADED in its isolated worker."
            await command_store.finish_command(command.id, error=message)
            raise EvaluationConflict(message)

    bridge = await vla_bridge.bridge_status(request.model_id, request.robot_id)
    if not bridge["executable"]:
        message = "VLA bridge is not executable: " + "; ".join(bridge["blockers"])
        await command_store.finish_command(command.id, error=message)
        raise EvaluationConflict(message)
    asset_version = await rigid_asset_compiler.get_version(request.asset_version_id)
    placement_request = (
        request.placement_request.model_dump(mode="json", by_alias=True)
        if request.placement_request is not None
        else None
    )
    template = (
        await ensure_authored_scene_world_template(request.robot_id, asset_version, scene_spec)
        if scene_spec is not None
        else await ensure_compiled_asset_world_template(
            request.robot_id, asset_version, placement_request=placement_request,
        )
    )
    if scene_spec is not None:
        async with SessionLocal() as session:
            oracle_pass = (
                await session.execute(
                    select(EvaluationRunRecord).where(
                        EvaluationRunRecord.world_template_id == str(template["id"]),
                        EvaluationRunRecord.policy == franka_pick_place.AUTHORED_SCENE_ORACLE_POLICY,
                        EvaluationRunRecord.success.is_(True),
                    ).limit(1)
                )
            ).scalar_one_or_none()
        if oracle_pass is None:
            message = "The exact authored world must pass its deterministic oracle before VLA evaluation."
            await command_store.finish_command(command.id, error=message)
            raise EvaluationConflict(message)
    async with SessionLocal() as session:
        model_row = await session.get(ModelRegistrationRecord, request.model_id)
        assert model_row is not None
        model_view = {
            "id": model_row.id,
            "revision": model_row.revision,
            "modelRevision": model_row.model_revision,
            "contentSha256": model_row.content_sha256,
            "capabilities": dict(model_row.capabilities or {}),
        }

    run_id = new_id("eval")
    artifact_dir = (
        (Path(template["runtimePath"]).resolve().parent.parent / "evaluations" / run_id).resolve()
        if scene_spec is not None
        else (WORLDS_DIR / franka_pick_place.TEMPLATE_ID / "evaluations" / run_id).resolve()
    )
    policy_name = f"vla-jepa:{request.model_id}:r{model_view['revision']}"
    row = EvaluationRunRecord(
        id=run_id,
        status="QUEUED",
        robot_id=request.robot_id,
        world_template_id=str(template["id"]),
        policy=policy_name,
        seed=request.seed,
        artifact_dir=str(artifact_dir),
    )
    async with SessionLocal() as session:
        session.add(row)
        session.add(
            AuditEvent(
                command_id=command.id,
                entity_type="evaluation",
                entity_id=run_id,
                action="evaluation.create",
                from_state=None,
                to_state="QUEUED",
                detail={
                    "robotId": request.robot_id,
                    "modelId": request.model_id,
                    "assetVersionId": request.asset_version_id,
                    "seed": request.seed,
                    "maxPolicySteps": request.max_policy_steps,
                },
                actor=actor,
            )
        )
        await session.commit()
    try:
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            await _audit_transition(session, active, "STARTING", command_id=command.id, actor=actor)
            active.started_at = _now()
            await session.commit()
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            await _audit_transition(session, active, "RUNNING", command_id=command.id, actor=actor)
            await session.commit()
        with span(
            "robot.vla_evaluate",
            run_id=run_id,
            robot_id=request.robot_id,
            model_id=request.model_id,
            asset_version_id=request.asset_version_id,
            seed=request.seed,
        ):
            raw_result, executed_template = await asyncio.to_thread(
                franka_vla_evaluation.run_compiled_asset_policy,
                robot_id=request.robot_id,
                asset_version=asset_version,
                model=model_view,
                bridge=bridge,
                run_id=run_id,
                seed=request.seed,
                instruction=request.instruction,
                max_policy_steps=request.max_policy_steps,
                infer_action=vla_policy_worker.infer_action,
                placement_request=placement_request,
                template_override=template if scene_spec is not None else None,
                artifact_dir_override=artifact_dir if scene_spec is not None else None,
            )
        result = EvaluationResultContract.model_validate(raw_result).model_dump(mode="json", by_alias=True)
        terminal = "SUCCEEDED" if result["success"] else "FAILED"
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            assert active is not None
            active.success = bool(result["success"])
            active.failure_code = result.get("failureCode")
            active.failure_detail = result.get("failureDetail")
            active.result = result
            active.finished_at = _now()
            await _audit_transition(
                session,
                active,
                terminal,
                command_id=command.id,
                actor=actor,
                detail={
                    "success": result["success"],
                    "failureCode": result.get("failureCode"),
                    "modelId": request.model_id,
                    "assetVersionId": request.asset_version_id,
                },
            )
            await session.commit()
            view = evaluation_view(active)
    except Exception as exc:
        async with SessionLocal() as session:
            active = await session.get(EvaluationRunRecord, run_id)
            if active is not None and active.status in {"STARTING", "RUNNING"}:
                active.failure_code = "worker_crash"
                active.failure_detail = str(exc)
                active.finished_at = _now()
                await _audit_transition(session, active, "CRASHED", command_id=command.id, actor=actor, detail={"error": str(exc)})
                await session.commit()
        await command_store.finish_command(command.id, error=str(exc))
        raise
    output = {
        "evaluation": view,
        "worldTemplate": executed_template,
        "bridge": bridge,
        "model": model_view,
        "assetVersion": asset_version,
    }
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_store.command_view(command)


async def list_evaluations(limit: int = 100) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(EvaluationRunRecord).order_by(EvaluationRunRecord.created_at.desc()).limit(max(1, min(limit, 500)))
            )
        ).scalars().all()
    return [evaluation_view(row) for row in rows]


async def get_evaluation(run_id: str) -> dict[str, Any]:
    async with SessionLocal() as session:
        row = await session.get(EvaluationRunRecord, run_id)
        if row is None:
            raise KeyError(run_id)
        return evaluation_view(row)


def frame_path(run_id: str, phase: str, camera: str) -> Path:
    if not all(value and all(char.isalnum() or char in "._-" for char in value) for value in (run_id, phase, camera)):
        raise FileNotFoundError(run_id)
    roots = (
        (WORLDS_DIR / franka_pick_place.TEMPLATE_ID / "evaluations").resolve(),
        (WORLDS_DIR / franka_articulation.TEMPLATE_ID / "runs").resolve(),
    )
    for base in roots:
        path = (base / run_id / "frames" / f"{phase}-{camera}.png").resolve()
        if base in path.parents and path.is_file():
            return path
    raise FileNotFoundError(run_id)
