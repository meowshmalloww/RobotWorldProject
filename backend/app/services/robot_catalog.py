"""Durable robot registrations and activation commands."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..contracts import FrankaRegistrationRequest
from ..db import SessionLocal
from ..models import AuditEvent, RobotRegistrationRecord
from . import command_store, franka


class RobotCatalogError(RuntimeError):
    pass


class RobotConflict(RobotCatalogError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def robot_view(row: RobotRegistrationRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "revision": row.revision,
        "displayName": row.display_name,
        "sourceFormat": row.source_format,
        "sourcePath": row.source_path,
        "sourceSha256": row.source_sha256,
        "sourceRevision": row.source_revision,
        "definition": dict(row.definition or {}),
        "lifecycleState": row.lifecycle_state,
        "validationErrors": list(row.validation_errors or []),
        "active": row.active,
        "licenseMetadata": dict(row.license_metadata or {}),
        "createdBy": row.created_by,
        "source": row.source,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


async def list_registered() -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(select(RobotRegistrationRecord).order_by(RobotRegistrationRecord.created_at.desc()))
        ).scalars().all()
    return [robot_view(row) for row in rows]


async def register_franka(
    request: FrankaRegistrationRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", by_alias=True)
    try:
        command, reused = await command_store.start_command(
            kind="robot.franka.register",
            target_type="robot",
            target_id=None,
            payload=payload,
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except command_store.CommandConflict as exc:
        raise RobotConflict(str(exc)) from exc
    if reused:
        return command_store.command_view(command, reused=True)
    try:
        manifest = await asyncio.to_thread(franka.build_and_validate, request)
    except Exception as exc:
        await command_store.finish_command(command.id, error=str(exc))
        raise

    definition = dict(manifest["definition"])
    lifecycle = str(definition["lifecycleState"])
    async with SessionLocal() as session:
        existing = await session.get(RobotRegistrationRecord, manifest["id"])
        rows = (await session.execute(select(RobotRegistrationRecord))).scalars().all()
        for row in rows:
            row.active = False
            row.updated_at = _now()
        if existing is None:
            existing = RobotRegistrationRecord(
                id=manifest["id"],
                revision=int(definition["revision"]),
                display_name=str(definition["displayName"]),
                source_format=str(definition["sourceFormat"]),
                source_path=str(definition.get("sourcePath") or "") or None,
                source_sha256=definition.get("sourceSha256"),
                source_revision=definition.get("sourceRevision"),
                definition=definition,
                lifecycle_state=lifecycle,
                validation_errors=list(definition.get("validationErrors") or []),
                active=bool(manifest["physicsReady"]),
                license_metadata=dict(definition["licenseMetadata"]),
                created_by=actor,
                source="mujoco_menagerie",
            )
            session.add(existing)
        else:
            existing.active = bool(manifest["physicsReady"])
            existing.updated_at = _now()
        session.add(
            AuditEvent(
                command_id=command.id,
                entity_type="robot",
                entity_id=manifest["id"],
                action="robot.franka.register" if existing.created_at is None else "robot.franka.activate_existing",
                from_state=None,
                to_state=lifecycle,
                detail={
                    "sourceRevision": manifest["sourceRevision"],
                    "runtimeSha256": manifest["runtimeSha256"],
                    "physicsReady": manifest["physicsReady"],
                },
                actor=actor,
            )
        )
        await session.commit()
        registered = robot_view(existing)
    output = {"robot": manifest, "registration": registered}
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_store.command_view(command)


async def activate_robot(
    robot_id: str,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    try:
        command, reused = await command_store.start_command(
            kind="robot.activate",
            target_type="robot",
            target_id=robot_id,
            payload={"robotId": robot_id},
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except command_store.CommandConflict as exc:
        raise RobotConflict(str(exc)) from exc
    if reused:
        return command_store.command_view(command, reused=True)
    async with SessionLocal() as session:
        row = await session.get(RobotRegistrationRecord, robot_id)
        if row is None:
            await command_store.finish_command(command.id, error="Robot registration not found.")
            raise KeyError(robot_id)
        if row.lifecycle_state != "AVAILABLE":
            message = f"Robot must be AVAILABLE before activation; current state is {row.lifecycle_state}."
            await command_store.finish_command(command.id, error=message)
            raise RobotConflict(message)
    try:
        probe = await asyncio.to_thread(franka.probe_registered_runtime, robot_id)
    except Exception as exc:
        await command_store.finish_command(command.id, error=str(exc))
        raise
    async with SessionLocal() as session:
        rows = (await session.execute(select(RobotRegistrationRecord))).scalars().all()
        active = next(row for row in rows if row.id == robot_id)
        for row in rows:
            row.active = row.id == robot_id
            row.updated_at = _now()
        session.add(
            AuditEvent(
                command_id=command.id,
                entity_type="robot",
                entity_id=robot_id,
                action="robot.activate",
                from_state=active.lifecycle_state,
                to_state=active.lifecycle_state,
                detail={"runtimeSha256": probe["runtimeSha256"], "resident": probe["resident"]},
                actor=actor,
            )
        )
        await session.commit()
        registered = robot_view(active)
    output = {"registration": registered, "loadProbe": probe}
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_store.command_view(command)

