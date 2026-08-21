"""Durable Bright Data collection runs feeding the canonical evidence catalog.

Provider jobs are not owned by a browser request.  The provider snapshot ID is
persisted before polling, and non-terminal runs resume after API restart.  A
crash during the trigger request is deliberately marked uncertain rather than
silently issuing a second potentially billable collection.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from ..contracts import BrightDataCollectionRequest, RecordedEvidenceImport
from ..db import SessionLocal
from ..models import AuditEvent, CommandExecution, EvidenceBundleRecord, EvidenceCollectionRunRecord, ObjectRequestRecord
from ..util import new_id
from . import brightdata, command_store, evidence_catalog

log = logging.getLogger(__name__)

ACTIVE_STATES = {"QUEUED", "STARTING", "RUNNING"}
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
_tasks: dict[str, asyncio.Task[None]] = {}
_shutting_down = False


class EvidenceCollectionConflict(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def collection_view(row: EvidenceCollectionRunRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "requestId": row.request_id,
        "revision": row.revision,
        "collectorId": row.collector_id,
        "collectorVersion": row.collector_version,
        "inputUrls": list(row.input_urls or []),
        "lifecycleState": row.lifecycle_state,
        "snapshotId": row.snapshot_id,
        "bundleId": row.bundle_id,
        "commandId": row.command_id,
        "providerAttempt": row.provider_attempt,
        "normalizationAttempt": row.normalization_attempt,
        "timeoutSeconds": row.timeout_seconds,
        "cancellationRequested": row.cancellation_requested,
        "error": row.error,
        "createdBy": row.created_by,
        "startedAt": row.started_at,
        "heartbeatAt": row.heartbeat_at,
        "finishedAt": row.finished_at,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def _command_response(row: EvidenceCollectionRunRecord, *, reused: bool = False) -> dict[str, Any]:
    return {
        "commandId": row.command_id,
        "status": "RUNNING" if row.lifecycle_state in ACTIVE_STATES else row.lifecycle_state,
        "reused": reused,
        "result": {"collectionRun": collection_view(row)},
        "error": row.error,
    }


async def _get_row(run_id: str) -> EvidenceCollectionRunRecord:
    async with SessionLocal() as session:
        row = await session.get(EvidenceCollectionRunRecord, run_id)
        if row is None:
            raise KeyError(run_id)
        return row


def _schedule(run_id: str) -> None:
    current = _tasks.get(run_id)
    if current is not None and not current.done():
        return
    task = asyncio.create_task(_run(run_id), name=f"evidence-collection:{run_id}")
    _tasks[run_id] = task

    def done(completed: asyncio.Task[None]) -> None:
        _tasks.pop(run_id, None)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            log.error("Evidence collection task %s escaped its failure guard: %s", run_id, error)

    task.add_done_callback(done)


async def create_run(
    request_id: str,
    payload: BrightDataCollectionRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    for value in payload.input_urls:
        evidence_catalog.validate_external_source_url(value)
    wire = payload.model_dump(mode="json", by_alias=True)
    command, reused = await command_store.start_command(
        kind="evidence.brightdata.collect",
        target_type="object_request",
        target_id=request_id,
        payload={"requestId": request_id, **wire},
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(EvidenceCollectionRunRecord).where(EvidenceCollectionRunRecord.command_id == command.id)
                )
            ).scalar_one_or_none()
        return _command_response(row, reused=True) if row is not None else command_store.command_view(command, reused=True)

    try:
        async with SessionLocal() as session:
            request = await session.get(ObjectRequestRecord, request_id)
            if request is None:
                raise KeyError(request_id)
            if request.lifecycle_state not in {"REQUESTED", "DISCOVERING"}:
                raise EvidenceCollectionConflict(
                    f"Object request is {request.lifecycle_state}; create a new request revision before collecting again."
                )
            active = (
                await session.execute(
                    select(EvidenceCollectionRunRecord).where(
                        EvidenceCollectionRunRecord.request_id == request_id,
                        EvidenceCollectionRunRecord.lifecycle_state.in_(ACTIVE_STATES),
                    )
                )
            ).scalar_one_or_none()
            if active is not None:
                raise EvidenceCollectionConflict(f"Collection run {active.id} is already active for this request.")
            count = (
                await session.execute(
                    select(func.count()).select_from(EvidenceCollectionRunRecord).where(EvidenceCollectionRunRecord.request_id == request_id)
                )
            ).scalar_one()
            row = EvidenceCollectionRunRecord(
                id=new_id("evcollect"),
                request_id=request_id,
                revision=int(count) + 1,
                collector_id=payload.collector_id,
                collector_version=payload.collector_version,
                input_urls=list(payload.input_urls),
                lifecycle_state="QUEUED",
                command_id=command.id,
                timeout_seconds=payload.timeout_seconds,
                created_by=actor,
            )
            session.add(row)
            session.add(
                AuditEvent(
                    command_id=command.id,
                    entity_type="evidence_collection",
                    entity_id=row.id,
                    action="evidence.collection.queue",
                    from_state=None,
                    to_state="QUEUED",
                    detail={"requestId": request_id, "collectorId": payload.collector_id, "inputCount": len(payload.input_urls)},
                    actor=actor,
                )
            )
            if request.lifecycle_state == "REQUESTED":
                request.lifecycle_state = "DISCOVERING"
                request.updated_at = _now()
                session.add(
                    AuditEvent(
                        command_id=command.id,
                        entity_type="object_request",
                        entity_id=request.id,
                        action="evidence.discovery.queue",
                        from_state="REQUESTED",
                        to_state="DISCOVERING",
                        detail={"collectionRunId": row.id},
                        actor=actor,
                    )
                )
            await session.commit()
    except Exception as exc:
        await command_store.finish_command(command.id, error=str(exc))
        raise
    _schedule(row.id)
    return _command_response(row)


async def _transition_start(run_id: str) -> tuple[EvidenceCollectionRunRecord, bool]:
    """Return the row and whether this process owns the initial trigger."""
    async with SessionLocal() as session:
        row = await session.get(EvidenceCollectionRunRecord, run_id)
        if row is None:
            raise KeyError(run_id)
        if row.cancellation_requested:
            return row, False
        if row.lifecycle_state == "QUEUED":
            previous = row.lifecycle_state
            row.lifecycle_state = "STARTING"
            row.provider_attempt += 1
            row.started_at = row.started_at or _now()
            row.heartbeat_at = _now()
            session.add(
                AuditEvent(
                    command_id=row.command_id,
                    entity_type="evidence_collection",
                    entity_id=row.id,
                    action="evidence.collection.trigger",
                    from_state=previous,
                    to_state="STARTING",
                    detail={"providerAttempt": row.provider_attempt},
                    actor="evidence-worker",
                )
            )
            await session.commit()
            return row, True
        return row, False


async def _persist_snapshot(run_id: str, snapshot_id: str) -> EvidenceCollectionRunRecord:
    async with SessionLocal() as session:
        row = await session.get(EvidenceCollectionRunRecord, run_id)
        if row is None:
            raise KeyError(run_id)
        previous = row.lifecycle_state
        row.snapshot_id = snapshot_id
        row.lifecycle_state = "RUNNING"
        row.heartbeat_at = _now()
        session.add(
            AuditEvent(
                command_id=row.command_id,
                entity_type="evidence_collection",
                entity_id=row.id,
                action="evidence.collection.snapshot",
                from_state=previous,
                to_state="RUNNING",
                detail={"snapshotId": snapshot_id},
                actor="evidence-worker",
            )
        )
        await session.commit()
        return row


async def _heartbeat(run_id: str) -> EvidenceCollectionRunRecord:
    async with SessionLocal() as session:
        row = await session.get(EvidenceCollectionRunRecord, run_id)
        if row is None:
            raise KeyError(run_id)
        row.heartbeat_at = _now()
        await session.commit()
        return row


async def _mark_terminal(
    run_id: str,
    state: str,
    *,
    error: str | None = None,
    bundle_id: str | None = None,
) -> None:
    if state not in TERMINAL_STATES:
        raise ValueError(f"Invalid terminal state {state}")
    async with SessionLocal() as session:
        row = await session.get(EvidenceCollectionRunRecord, run_id)
        if row is None:
            return
        previous = row.lifecycle_state
        row.lifecycle_state = state
        row.error = error
        row.bundle_id = bundle_id or row.bundle_id
        row.heartbeat_at = _now()
        row.finished_at = _now()
        session.add(
            AuditEvent(
                command_id=row.command_id,
                entity_type="evidence_collection",
                entity_id=row.id,
                action="evidence.collection.finish",
                from_state=previous,
                to_state=state,
                detail={"snapshotId": row.snapshot_id, "bundleId": row.bundle_id, "error": error},
                actor="evidence-worker",
            )
        )
        await session.commit()
        output = {"collectionRun": collection_view(row)}
        command_id = row.command_id
    await command_store.finish_command(command_id, output=output, error=error if state != "SUCCEEDED" else None)


async def _recover_completed_bundle(row: EvidenceCollectionRunRecord) -> bool:
    async with SessionLocal() as session:
        request = await session.get(ObjectRequestRecord, row.request_id)
        if request is None or request.lifecycle_state != "IDENTITY_VALIDATED":
            return False
        bundle = (
            await session.execute(
                select(EvidenceBundleRecord)
                .where(EvidenceBundleRecord.request_id == row.request_id)
                .order_by(EvidenceBundleRecord.revision.desc())
            )
        ).scalars().first()
    if bundle is None or bundle.lifecycle_state != "QUALITY_PASSED":
        return False
    await _mark_terminal(row.id, "SUCCEEDED", bundle_id=bundle.id)
    return True


async def _run(run_id: str) -> None:
    try:
        row, owns_trigger = await _transition_start(run_id)
        if row.lifecycle_state in TERMINAL_STATES:
            return
        if row.cancellation_requested:
            await _mark_terminal(run_id, "CANCELLED", error="Collection cancelled before provider execution.")
            return
        if await _recover_completed_bundle(row):
            return
        if row.lifecycle_state == "STARTING" and not row.snapshot_id and not owns_trigger:
            await _mark_terminal(
                run_id,
                "FAILED",
                error="Provider trigger outcome is uncertain after restart; no automatic duplicate collection was issued.",
            )
            return
        if owns_trigger:
            snapshot_id = await brightdata.dca_trigger(row.collector_id, [{"url": value} for value in row.input_urls])
            row = await _persist_snapshot(run_id, snapshot_id)
        elif row.lifecycle_state == "STARTING" and row.snapshot_id:
            row = await _persist_snapshot(run_id, row.snapshot_id)
        if not row.snapshot_id:
            raise brightdata.BrightDataError("Bright Data did not return a collection snapshot ID.")

        while True:
            row = await _heartbeat(run_id)
            if row.cancellation_requested:
                await _mark_terminal(run_id, "CANCELLED", error="Collection cancelled by user policy.")
                return
            started = _aware(row.started_at) or _now()
            if (_now() - started).total_seconds() > row.timeout_seconds:
                raise brightdata.BrightDataError(
                    f"Collector {row.collector_id} timed out after {row.timeout_seconds:.0f}s; snapshot {row.snapshot_id} is preserved."
                )
            ready, provider_payload = await brightdata.dca_dataset(row.snapshot_id)
            if ready:
                rows = provider_payload if isinstance(provider_payload, list) else []
                if not rows:
                    raise brightdata.BrightDataError(f"Collector snapshot {row.snapshot_id} returned zero records.")
                break
            await asyncio.sleep(5.0)

        async with SessionLocal() as session:
            current = await session.get(EvidenceCollectionRunRecord, run_id)
            if current is None:
                raise KeyError(run_id)
            current.normalization_attempt += 1
            normalization_attempt = current.normalization_attempt
            current.heartbeat_at = _now()
            await session.commit()
        normalized = await evidence_catalog.normalize_recorded(
            row.request_id,
            RecordedEvidenceImport(
                rows=rows,
                collector_id=row.collector_id,
                collector_version=row.collector_version,
                source="recorded_brightdata",
                retrieved_at=_now(),
            ),
            idempotency_key=f"evidence-collection:{run_id}:normalize:{normalization_attempt}",
            actor="evidence-worker",
        )
        bundle = dict((normalized.get("result") or {}).get("bundle") or {})
        bundle_id = str(bundle.get("id") or "") or None
        if bundle.get("lifecycleState") != "QUALITY_PASSED":
            errors = list(bundle.get("validationErrors") or [])
            raise evidence_catalog.EvidenceCatalogError(
                "Provider collection completed but semantic quality failed: " + (errors[0] if errors else "no passing bundle was produced")
            )
        await _mark_terminal(run_id, "SUCCEEDED", bundle_id=bundle_id)
    except asyncio.CancelledError:
        if not _shutting_down:
            await _mark_terminal(run_id, "CANCELLED", error="Collection cancelled by user policy.")
        raise
    except Exception as exc:
        await _mark_terminal(run_id, "FAILED", error=str(exc))


async def get_run(run_id: str) -> dict[str, Any]:
    return collection_view(await _get_row(run_id))


async def list_runs(*, request_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        query = select(EvidenceCollectionRunRecord)
        if request_id:
            query = query.where(EvidenceCollectionRunRecord.request_id == request_id)
        rows = (
            await session.execute(query.order_by(EvidenceCollectionRunRecord.created_at.desc()).limit(limit))
        ).scalars().all()
    return [collection_view(row) for row in rows]


async def cancel_run(run_id: str, *, actor: str = "user") -> dict[str, Any]:
    async with SessionLocal() as session:
        row = await session.get(EvidenceCollectionRunRecord, run_id)
        if row is None:
            raise KeyError(run_id)
        if row.lifecycle_state in TERMINAL_STATES:
            return collection_view(row)
        row.cancellation_requested = True
        row.updated_at = _now()
        session.add(
            AuditEvent(
                command_id=row.command_id,
                entity_type="evidence_collection",
                entity_id=row.id,
                action="evidence.collection.cancel_request",
                from_state=row.lifecycle_state,
                to_state=row.lifecycle_state,
                detail={},
                actor=actor,
            )
        )
        await session.commit()
    task = _tasks.get(run_id)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    else:
        _schedule(run_id)
        await asyncio.sleep(0)
    return await get_run(run_id)


async def resume_incomplete() -> int:
    global _shutting_down
    _shutting_down = False
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(EvidenceCollectionRunRecord).where(EvidenceCollectionRunRecord.lifecycle_state.in_(ACTIVE_STATES))
            )
        ).scalars().all()
        terminals = (
            await session.execute(
                select(EvidenceCollectionRunRecord).where(EvidenceCollectionRunRecord.lifecycle_state.in_(TERMINAL_STATES))
            )
        ).scalars().all()
        command_states = {
            command.id: command.status
            for command in (
                await session.execute(
                    select(CommandExecution).where(CommandExecution.id.in_([row.command_id for row in terminals] or [""]))
                )
            ).scalars().all()
        }
    for row in terminals:
        if command_states.get(row.command_id) == "RUNNING":
            await command_store.finish_command(
                row.command_id,
                output={"collectionRun": collection_view(row)},
                error=row.error if row.lifecycle_state != "SUCCEEDED" else None,
            )
    for row in rows:
        _schedule(row.id)
    return len(rows)


async def shutdown() -> None:
    global _shutting_down
    _shutting_down = True
    tasks = [task for task in _tasks.values() if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
