"""Durable model commands for both the React client and platform-agent tools."""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from ..contracts import ModelLifecycle, ModelProviderType, ModelRegistrationCreate
from ..db import SessionLocal
from ..models import AuditEvent, CommandExecution, ModelRegistrationRecord
from ..util import new_id
from . import command_store, model_registry, vla_policy_worker


class RegistryError(RuntimeError):
    pass


class RegistryConflict(RegistryError, command_store.CommandConflict):
    pass


MODEL_TRANSITIONS: dict[str, set[str]] = {
    ModelLifecycle.REGISTERED: {ModelLifecycle.VALIDATING},
    ModelLifecycle.INVALID: {ModelLifecycle.VALIDATING},
    ModelLifecycle.AVAILABLE: {ModelLifecycle.VALIDATING, ModelLifecycle.LOADED},
    ModelLifecycle.VALIDATING: {ModelLifecycle.AVAILABLE, ModelLifecycle.INVALID},
    ModelLifecycle.LOADED: {ModelLifecycle.UNLOADING},
    ModelLifecycle.UNLOADING: {ModelLifecycle.AVAILABLE},
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def model_view(row: ModelRegistrationRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "revision": row.revision,
        "displayName": row.display_name,
        "roles": list(row.roles or []),
        "providerType": row.provider_type,
        "localPath": row.local_path,
        "baseUrl": row.base_url,
        "modelId": row.model_id,
        "modelRevision": row.model_revision,
        "apiKeyEnv": row.api_key_env,
        "apiKeyConfigured": bool(row.api_key_env and os.environ.get(row.api_key_env)),
        "expectedDevice": row.expected_device,
        "precision": row.precision,
        "inputSchema": dict(row.input_schema or {}),
        "outputSchema": dict(row.output_schema or {}),
        "capabilities": dict(row.capabilities or {}),
        "licenseMetadata": dict(row.license_metadata or {}),
        "lifecycleState": row.lifecycle_state,
        "healthStatus": row.health_status,
        "enabled": row.enabled,
        "manifestSha256": row.manifest_sha256,
        "contentSha256": row.content_sha256,
        "lastError": row.last_error,
        "lastValidatedAt": row.last_validated_at,
        "lastLoadedAt": row.last_loaded_at,
        "createdBy": row.created_by,
        "source": row.source,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def command_view(row: CommandExecution, *, reused: bool = False) -> dict[str, Any]:
    return command_store.command_view(row, reused=reused)


async def _audit(
    session,
    *,
    command_id: str | None,
    entity_id: str,
    action: str,
    from_state: str | None,
    to_state: str | None,
    detail: dict[str, Any] | None = None,
    actor: str = "user",
) -> None:
    session.add(
        AuditEvent(
            command_id=command_id,
            entity_type="model",
            entity_id=entity_id,
            action=action,
            from_state=from_state,
            to_state=to_state,
            detail=detail or {},
            actor=actor,
        )
    )


async def _transition(
    session,
    row: ModelRegistrationRecord,
    target: ModelLifecycle,
    *,
    command_id: str,
    action: str,
    actor: str,
    detail: dict[str, Any] | None = None,
) -> None:
    source = row.lifecycle_state
    if target not in MODEL_TRANSITIONS.get(source, set()):
        raise RegistryConflict(f"Invalid model lifecycle transition {source} -> {target}.")
    row.lifecycle_state = str(target)
    row.updated_at = _now()
    await _audit(
        session,
        command_id=command_id,
        entity_id=row.id,
        action=action,
        from_state=source,
        to_state=str(target),
        detail=detail,
        actor=actor,
    )


async def list_models() -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(select(ModelRegistrationRecord).order_by(ModelRegistrationRecord.created_at.desc()))
        ).scalars().all()
    return [model_view(row) for row in rows]


async def get_model(model_id: str) -> dict[str, Any]:
    async with SessionLocal() as session:
        row = await session.get(ModelRegistrationRecord, model_id)
        if row is None:
            raise KeyError(model_id)
        return model_view(row)


async def register_model(
    payload: ModelRegistrationCreate,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    wire = payload.model_dump(mode="json", by_alias=True)
    command, reused = await command_store.start_command(
        kind="model.register",
        target_type="model",
        target_id=None,
        payload=wire,
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        return command_view(command, reused=True)
    model_id = new_id("mdl")
    row = ModelRegistrationRecord(
        id=model_id,
        revision=1,
        display_name=payload.display_name,
        roles=[str(role) for role in payload.roles],
        provider_type=str(payload.provider_type),
        local_path=payload.local_path,
        base_url=payload.base_url,
        model_id=payload.model_id,
        model_revision=payload.model_revision,
        api_key_env=payload.api_key_env,
        expected_device=payload.expected_device,
        precision=payload.precision,
        input_schema=payload.input_schema,
        output_schema=payload.output_schema,
        license_metadata=payload.license_metadata,
        lifecycle_state=str(ModelLifecycle.REGISTERED),
        health_status="unknown",
        enabled=payload.enabled,
        created_by=actor,
        source="api",
    )
    async with SessionLocal() as session:
        session.add(row)
        await _audit(
            session,
            command_id=command.id,
            entity_id=model_id,
            action="model.register",
            from_state=None,
            to_state=str(ModelLifecycle.REGISTERED),
            detail={"revision": 1, "providerType": row.provider_type},
            actor=actor,
        )
        await session.commit()
    result = model_view(row)
    await command_store.finish_command(command.id, output={"model": result})
    command.output = command_store.json_safe({"model": result})
    command.status = "SUCCEEDED"
    return command_view(command)


async def _probe_endpoint(row: ModelRegistrationRecord) -> dict[str, Any]:
    if not row.base_url:
        raise ValueError("Model endpoint is missing.")
    base = await asyncio.to_thread(model_registry.validate_endpoint_url, row.base_url)
    headers = {"Accept": "application/json"}
    if row.api_key_env:
        secret = os.environ.get(row.api_key_env)
        if not secret:
            raise ValueError(f"Required secret environment variable {row.api_key_env} is not configured.")
        headers["Authorization"] = f"Bearer {secret}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False, headers=headers) as client:
        response = await client.get(f"{base}/models")
    if response.status_code != 200:
        raise ValueError(f"Model endpoint /models returned HTTP {response.status_code}.")
    try:
        value = response.json()
    except ValueError as exc:
        raise ValueError("Model endpoint /models did not return JSON.") from exc
    models = value.get("data") if isinstance(value, dict) else None
    ids = [str(item.get("id")) for item in models if isinstance(item, dict) and item.get("id")] if isinstance(models, list) else []
    if row.model_id and ids and row.model_id not in ids:
        raise ValueError(f"Configured model ID '{row.model_id}' was not advertised by the endpoint.")
    return {"endpoint": base, "advertisedModelIds": ids[:100], "selectedModelAvailable": not row.model_id or row.model_id in ids}


async def _probe_hugging_face(row: ModelRegistrationRecord) -> dict[str, Any]:
    model_id = str(row.model_id or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", model_id):
        raise ValueError("Hugging Face modelId must be in owner/repository form.")
    headers = {"Accept": "application/json"}
    if row.api_key_env:
        secret = os.environ.get(row.api_key_env)
        if not secret:
            raise ValueError(f"Required secret environment variable {row.api_key_env} is not configured.")
        headers["Authorization"] = f"Bearer {secret}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False, headers=headers) as client:
        response = await client.get(f"https://huggingface.co/api/models/{model_id}")
    if response.status_code != 200:
        raise ValueError(f"Hugging Face model metadata returned HTTP {response.status_code}.")
    value = response.json()
    return {
        "repository": model_id,
        "resolvedRevision": value.get("sha"),
        "pipelineTag": value.get("pipeline_tag"),
        "libraryName": value.get("library_name"),
        "license": (value.get("cardData") or {}).get("license") if isinstance(value.get("cardData"), dict) else None,
        "downloaded": False,
    }


async def validate_model(
    model_id: str,
    *,
    compute_content_hash: bool,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    payload = {"modelId": model_id, "computeContentHash": compute_content_hash}
    command, reused = await command_store.start_command(
        kind="model.validate",
        target_type="model",
        target_id=model_id,
        payload=payload,
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        return command_view(command, reused=True)
    async with SessionLocal() as session:
        row = await session.get(ModelRegistrationRecord, model_id)
        if row is None:
            await command_store.finish_command(command.id, error="Model registration not found.")
            raise KeyError(model_id)
        await _transition(session, row, ModelLifecycle.VALIDATING, command_id=command.id, action="model.validate.start", actor=actor)
        await session.commit()

    result: dict[str, Any] = {}
    failure: str | None = None
    try:
        async with SessionLocal() as session:
            row = await session.get(ModelRegistrationRecord, model_id)
            assert row is not None
            provider = row.provider_type
            roles = list(row.roles or [])
            local_path = row.local_path
        if provider == ModelProviderType.LOCAL_PATH:
            resolved = await asyncio.to_thread(model_registry.resolve_allowed_local_path, str(local_path or ""))
            result = await asyncio.to_thread(
                model_registry.inspect_local_model,
                resolved,
                roles,
                compute_content_hash=compute_content_hash,
            )
            if not result["valid"]:
                failure = "; ".join(result["errors"])
        elif provider in {ModelProviderType.OPENAI_COMPATIBLE, ModelProviderType.LOCAL_SERVER}:
            async with SessionLocal() as session:
                row = await session.get(ModelRegistrationRecord, model_id)
                assert row is not None
                result = await _probe_endpoint(row)
        elif provider == ModelProviderType.HUGGING_FACE:
            async with SessionLocal() as session:
                row = await session.get(ModelRegistrationRecord, model_id)
                assert row is not None
                result = await _probe_hugging_face(row)
        else:
            failure = "No native-provider adapter is registered for this model."
    except (OSError, ValueError, httpx.HTTPError) as exc:
        failure = str(exc)

    async with SessionLocal() as session:
        row = await session.get(ModelRegistrationRecord, model_id)
        assert row is not None
        row.last_validated_at = _now()
        row.last_error = failure
        row.health_status = "failed" if failure else "healthy"
        if result:
            row.capabilities = dict(result.get("capabilities") or result)
            row.manifest_sha256 = result.get("manifestSha256")
            if result.get("contentSha256"):
                row.content_sha256 = str(result["contentSha256"])
            row.input_schema = dict(result.get("inputSchema") or row.input_schema or {})
            row.output_schema = dict(result.get("outputSchema") or row.output_schema or {})
            if row.model_revision in {None, "", "unrecorded"} and result.get("modelRevision"):
                row.model_revision = str(result["modelRevision"])
        target = ModelLifecycle.INVALID if failure else ModelLifecycle.AVAILABLE
        await _transition(
            session,
            row,
            target,
            command_id=command.id,
            action="model.validate.finish",
            actor=actor,
            detail={"valid": not failure, "errors": [failure] if failure else []},
        )
        await session.commit()
        view = model_view(row)
    output = {"model": view, "validation": {"valid": not failure, "detail": result, "error": failure}}
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_view(command)


async def load_model(
    model_id: str,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    command, reused = await command_store.start_command(
        kind="model.load",
        target_type="model",
        target_id=model_id,
        payload={"modelId": model_id},
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        return command_view(command, reused=True)
    async with SessionLocal() as session:
        row = await session.get(ModelRegistrationRecord, model_id)
        if row is None:
            await command_store.finish_command(command.id, error="Model registration not found.")
            raise KeyError(model_id)
        if (
            row.lifecycle_state == ModelLifecycle.LOADED
            and row.provider_type == ModelProviderType.LOCAL_PATH
            and "vla_policy" in (row.roles or [])
        ):
            worker_status = await asyncio.to_thread(vla_policy_worker.status)
            resident_path = str((worker_status.get("resident") or {}).get("checkpointPath") or "")
            requested_path = str(row.local_path or "")
            same_resident = bool(
                resident_path
                and requested_path
                and Path(resident_path).resolve() == Path(requested_path).resolve()
            )
            if same_resident:
                row.health_status = "healthy"
                row.last_error = None
                await session.commit()
                output = {"model": model_view(row), "worker": {"loaded": True, "worker": worker_status}}
                await command_store.finish_command(command.id, output=output)
                command.output = command_store.json_safe(output)
                command.status = "SUCCEEDED"
                return command_view(command)
            await _transition(
                session,
                row,
                ModelLifecycle.UNLOADING,
                command_id=command.id,
                action="model.worker_mismatch",
                actor=actor,
                detail={"residentCheckpointPath": resident_path or None},
            )
            await _transition(
                session,
                row,
                ModelLifecycle.AVAILABLE,
                command_id=command.id,
                action="model.worker_mismatch_reconciled",
                actor=actor,
            )
            row.health_status = "worker_stopped"
            row.last_error = "Catalog state was reconciled because a different checkpoint was resident."
        if row.lifecycle_state != ModelLifecycle.AVAILABLE:
            await command_store.finish_command(command.id, error=f"Model must be AVAILABLE, not {row.lifecycle_state}.")
            raise RegistryConflict(f"Model must be AVAILABLE before loading; current state is {row.lifecycle_state}.")
        worker_result: dict[str, Any] | None = None
        if row.provider_type == ModelProviderType.LOCAL_PATH:
            if "vla_policy" not in (row.roles or []) or (row.capabilities or {}).get("configType") != "vla_jepa":
                message = "No isolated worker adapter is registered for this local model role/type."
                await command_store.finish_command(command.id, error=message)
                raise RegistryConflict(message)
            try:
                worker_result = await asyncio.to_thread(
                    vla_policy_worker.load_checkpoint,
                    str(row.local_path or ""),
                    row.expected_device,
                )
            except (OSError, ValueError, vla_policy_worker.VlaWorkerError) as exc:
                message = f"VLA-JEPA isolated worker load failed: {exc}"
                row.last_error = message
                row.health_status = "worker_unavailable"
                await session.commit()
                await command_store.finish_command(command.id, error=message)
                raise RegistryConflict(message) from exc
        elif row.provider_type in {ModelProviderType.OPENAI_COMPATIBLE, ModelProviderType.LOCAL_SERVER}:
            await _probe_endpoint(row)
        else:
            message = "No isolated worker adapter is registered for this model provider."
            await command_store.finish_command(command.id, error=message)
            raise RegistryConflict(message)
        loaded_rows = (
            await session.execute(
                select(ModelRegistrationRecord).where(
                    ModelRegistrationRecord.id != row.id,
                    ModelRegistrationRecord.provider_type == ModelProviderType.LOCAL_PATH,
                    ModelRegistrationRecord.lifecycle_state == ModelLifecycle.LOADED,
                )
            )
        ).scalars().all()
        for previous in loaded_rows:
            if "vla_policy" not in (previous.roles or []):
                continue
            await _transition(
                session,
                previous,
                ModelLifecycle.UNLOADING,
                command_id=command.id,
                action="model.worker_replaced",
                actor=actor,
                detail={"replacedByModelId": row.id},
            )
            await _transition(
                session,
                previous,
                ModelLifecycle.AVAILABLE,
                command_id=command.id,
                action="model.worker_replacement_finished",
                actor=actor,
            )
            previous.health_status = "worker_stopped"
            previous.last_error = f"Isolated worker now hosts {row.id}."
        await _transition(session, row, ModelLifecycle.LOADED, command_id=command.id, action="model.load", actor=actor)
        row.last_loaded_at = _now()
        row.last_error = None
        row.health_status = "healthy"
        await session.commit()
        view = model_view(row)
    output = {"model": view, "worker": worker_result}
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_view(command)


async def unload_model(
    model_id: str,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    command, reused = await command_store.start_command(
        kind="model.unload",
        target_type="model",
        target_id=model_id,
        payload={"modelId": model_id},
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        return command_view(command, reused=True)
    async with SessionLocal() as session:
        row = await session.get(ModelRegistrationRecord, model_id)
        if row is None:
            await command_store.finish_command(command.id, error="Model registration not found.")
            raise KeyError(model_id)
        worker_result: dict[str, Any] | None = None
        if row.provider_type == ModelProviderType.LOCAL_PATH and "vla_policy" in (row.roles or []):
            try:
                worker_result = await asyncio.to_thread(vla_policy_worker.unload_checkpoint)
            except vla_policy_worker.VlaWorkerError as exc:
                await command_store.finish_command(command.id, error=f"VLA-JEPA worker unload failed: {exc}")
                raise RegistryConflict(f"VLA-JEPA worker unload failed: {exc}") from exc
        await _transition(session, row, ModelLifecycle.UNLOADING, command_id=command.id, action="model.unload.start", actor=actor)
        await _transition(session, row, ModelLifecycle.AVAILABLE, command_id=command.id, action="model.unload.finish", actor=actor)
        await session.commit()
        view = model_view(row)
    output = {"model": view, "worker": worker_result}
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_view(command)


async def reconcile_local_worker_state() -> int:
    """A process-local model cannot remain LOADED across an API restart."""
    changed = 0
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ModelRegistrationRecord).where(
                    ModelRegistrationRecord.provider_type == ModelProviderType.LOCAL_PATH,
                    ModelRegistrationRecord.lifecycle_state == ModelLifecycle.LOADED,
                )
            )
        ).scalars().all()
        for row in rows:
            row.lifecycle_state = str(ModelLifecycle.AVAILABLE)
            row.health_status = "worker_stopped"
            row.last_error = "Local worker is not resident after control-plane restart."
            row.updated_at = _now()
            session.add(
                AuditEvent(
                    command_id=None,
                    entity_type="model",
                    entity_id=row.id,
                    action="model.worker_reconcile",
                    from_state=str(ModelLifecycle.LOADED),
                    to_state=str(ModelLifecycle.AVAILABLE),
                    detail={"reason": "control_plane_restart"},
                    actor="system",
                )
            )
            changed += 1
        await session.commit()
    return changed


async def audit_history(*, entity_type: str | None = None, entity_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(max(1, min(limit, 500)))
        if entity_type:
            query = query.where(AuditEvent.entity_type == entity_type)
        if entity_id:
            query = query.where(AuditEvent.entity_id == entity_id)
        rows = (await session.execute(query)).scalars().all()
    return [
        {
            "id": row.id,
            "commandId": row.command_id,
            "entityType": row.entity_type,
            "entityId": row.entity_id,
            "action": row.action,
            "fromState": row.from_state,
            "toState": row.to_state,
            "detail": row.detail,
            "actor": row.actor,
            "createdAt": row.created_at,
        }
        for row in rows
    ]
