"""Durable, replay-safe command envelopes shared by UI and agent tools."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import SessionLocal
from ..models import CommandExecution
from ..util import new_id


class CommandConflict(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item)))


def payload_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf8")).hexdigest()


def command_view(row: CommandExecution, *, reused: bool = False) -> dict[str, Any]:
    return {
        "commandId": row.id,
        "status": row.status,
        "reused": reused,
        "result": dict(row.output or {}),
        "error": row.error,
    }


async def _existing(idempotency_key: str | None) -> CommandExecution | None:
    if not idempotency_key:
        return None
    async with SessionLocal() as session:
        return (
            await session.execute(select(CommandExecution).where(CommandExecution.idempotency_key == idempotency_key))
        ).scalar_one_or_none()


async def start_command(
    *,
    kind: str,
    target_type: str,
    target_id: str | None,
    payload: dict[str, Any],
    idempotency_key: str | None,
    actor: str,
) -> tuple[CommandExecution, bool]:
    expected_hash = payload_hash(payload)
    existing = await _existing(idempotency_key)
    if existing is not None:
        actual_hash = str((existing.input or {}).get("payloadSha256") or "")
        if existing.kind != kind or (actual_hash and actual_hash != expected_hash):
            raise CommandConflict("Idempotency-Key was already used with a different command or input.")
        return existing, True
    command = CommandExecution(
        id=new_id("cmd"),
        kind=kind,
        target_type=target_type,
        target_id=target_id,
        idempotency_key=idempotency_key,
        status="RUNNING",
        input={"payloadSha256": expected_hash, "payload": json_safe(payload)},
        actor=actor,
    )
    async with SessionLocal() as session:
        session.add(command)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = await _existing(idempotency_key)
            if existing is None:
                raise
            actual_hash = str((existing.input or {}).get("payloadSha256") or "")
            if existing.kind != kind or (actual_hash and actual_hash != expected_hash):
                raise CommandConflict("Idempotency-Key was concurrently used with a different command or input.")
            return existing, True
    return command, False


async def finish_command(command_id: str, *, output: dict[str, Any] | None = None, error: str | None = None) -> None:
    async with SessionLocal() as session:
        row = await session.get(CommandExecution, command_id)
        if row is None:
            return
        row.output = json_safe(output or {})
        row.error = error
        row.status = "FAILED" if error else "SUCCEEDED"
        row.updated_at = _now()
        await session.commit()

