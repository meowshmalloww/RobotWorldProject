"""Durable, fail-closed VLA-JEPA fine-tuning preflight catalog."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import BASE_DIR, DATASETS_DIR, TRAINING_RUNS_DIR
from ..contracts import VlaJepaFineTuneExecuteRequest, VlaJepaFineTuneValidationRequest
from ..db import SessionLocal
from ..models import ModelRegistrationRecord, PolicyTrainingRunRecord
from ..util import new_id
from . import command_store


WORKER_SCRIPT = (BASE_DIR / "workers" / "lerobot_training_worker.py").resolve()
EXECUTE_WORKER_SCRIPT = (BASE_DIR / "workers" / "lerobot_training_execute_worker.py").resolve()
DEFAULT_VLA_PYTHON = Path(r"D:\RobotWorldRuntimes\vla-env\Scripts\python.exe")
DEFAULT_LEROBOT_ROOT = Path(r"D:\LeRobot")
DEFAULT_QWEN_METADATA = Path(r"D:\RobotWorldRuntimes\model-metadata\Qwen3-VL-2B-Instruct")
RESULT_PREFIX = "ROBOTWORLD_TRAINING_PREFLIGHT_RESULT="
EXECUTE_RESULT_PREFIX = "ROBOTWORLD_TRAINING_RESULT="


class TrainingPreflightError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8", errors="replace")
    temporary.replace(path)


def _run_view(row: PolicyTrainingRunRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "revision": row.revision,
        "lifecycleState": row.lifecycle_state,
        "datasetId": row.dataset_id,
        "baseModelId": row.base_model_id,
        "configuration": dict(row.configuration or {}),
        "inputSha256": row.input_sha256,
        "artifactDir": row.artifact_dir,
        "candidateCheckpointPath": row.candidate_checkpoint_path,
        "candidateCheckpointSha256": row.candidate_checkpoint_sha256,
        "metrics": dict(row.metrics or {}),
        "validation": dict(row.validation or {}),
        "error": row.error,
        "createdBy": row.created_by,
        "startedAt": row.started_at,
        "finishedAt": row.finished_at,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def _dataset_record(dataset_id: str) -> dict[str, Any]:
    manifest_path = (DATASETS_DIR / dataset_id / "dataset_manifest.json").resolve()
    if DATASETS_DIR.resolve() not in manifest_path.parents or not manifest_path.is_file():
        raise KeyError(dataset_id)
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    if record.get("datasetId") != dataset_id:
        raise TrainingPreflightError("Dataset manifest identity mismatch.")
    if record.get("lifecycleState") != "VALIDATED" or record.get("readbackValidated") is not True:
        raise TrainingPreflightError("Fine-tuning accepts only readback-validated LeRobot datasets.")
    root = Path(str(record.get("root") or "")).resolve(strict=True)
    if DATASETS_DIR.resolve() not in root.parents:
        raise TrainingPreflightError("Dataset artifact path is outside the RobotWorld dataset store.")
    info = root / "meta" / "info.json"
    if not info.is_file() or _sha256(info) != record.get("infoSha256"):
        raise TrainingPreflightError("LeRobot dataset info hash mismatch.")
    for item in list(record.get("dataFiles") or []):
        path = (root / str(item.get("path") or "")).resolve()
        if root != path and root not in path.parents:
            raise TrainingPreflightError("Dataset data path escapes its artifact root.")
        if not path.is_file() or _sha256(path) != item.get("sha256"):
            raise TrainingPreflightError(f"Dataset data hash mismatch: {path.name}")
    return record


def _python_path() -> Path:
    path = Path(os.environ.get("VLA_JEPA_PYTHON") or DEFAULT_VLA_PYTHON).resolve()
    if not path.is_file():
        raise TrainingPreflightError(f"VLA-JEPA worker Python is missing: {path}")
    return path


def _run_worker(manifest_path: Path) -> dict[str, Any]:
    if not WORKER_SCRIPT.is_file():
        raise TrainingPreflightError(f"Training worker is missing: {WORKER_SCRIPT}")
    environment = os.environ.copy()
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    environment["WANDB_MODE"] = "disabled"
    environment["WANDB_SILENT"] = "true"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    completed = subprocess.run(
        [str(_python_path()), "-u", str(WORKER_SCRIPT), "--manifest", str(manifest_path)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=environment,
        creationflags=creation_flags,
    )
    result_line = next((line for line in reversed(completed.stdout.splitlines()) if line.startswith(RESULT_PREFIX)), None)
    if completed.returncode != 0 or result_line is None:
        detail = (completed.stderr or completed.stdout or "training worker returned no result").strip()[-6000:]
        raise TrainingPreflightError(f"LeRobot training preflight failed ({completed.returncode}): {detail}")
    return json.loads(result_line.removeprefix(RESULT_PREFIX))


def _run_execute_worker(manifest_path: Path) -> dict[str, Any]:
    if not EXECUTE_WORKER_SCRIPT.is_file():
        raise TrainingPreflightError(f"Training execution worker is missing: {EXECUTE_WORKER_SCRIPT}")
    environment = os.environ.copy()
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    environment["WANDB_MODE"] = "disabled"
    environment["WANDB_SILENT"] = "true"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    completed = subprocess.run(
        [str(_python_path()), "-u", str(EXECUTE_WORKER_SCRIPT), "--manifest", str(manifest_path)],
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
        env=environment,
        creationflags=creation_flags,
    )
    root = manifest_path.parent
    _write_text(root / "training_stdout.log", completed.stdout[-2_000_000:])
    _write_text(root / "training_stderr.log", completed.stderr[-2_000_000:])
    result_path = root / "training_result.json"
    result_line = next(
        (line for line in reversed(completed.stdout.splitlines()) if line.startswith(EXECUTE_RESULT_PREFIX)),
        None,
    )
    if completed.returncode != 0 or (result_line is None and not result_path.is_file()):
        detail = (completed.stderr or completed.stdout or "training worker returned no result").strip()[-6000:]
        raise TrainingPreflightError(f"LeRobot training execution failed ({completed.returncode}): {detail}")
    result = (
        json.loads(result_path.read_text(encoding="utf-8"))
        if result_path.is_file()
        else json.loads(result_line.removeprefix(EXECUTE_RESULT_PREFIX))
    )
    if result.get("runId") != json.loads(manifest_path.read_text(encoding="utf-8")).get("runId"):
        raise TrainingPreflightError("Training result identity does not match the approved run.")
    return result


def _recover_completed_candidate(row: PolicyTrainingRunRecord) -> dict[str, Any] | None:
    """Recover a completed direct worker run using only immutable checkpoint evidence."""

    root = Path(row.artifact_dir).resolve()
    pointer = root / "candidate" / "checkpoints" / "last_checkpoint.txt"
    if not pointer.is_file():
        return None
    checkpoint_name = pointer.read_text(encoding="utf-8").strip()
    if not checkpoint_name or Path(checkpoint_name).name != checkpoint_name:
        raise TrainingPreflightError("Candidate completion pointer is invalid.")
    checkpoint_root = root / "candidate" / "checkpoints" / checkpoint_name
    candidate = checkpoint_root / "pretrained_model"
    weights = candidate / "model.safetensors"
    config_path = candidate / "config.json"
    step_path = checkpoint_root / "training_state" / "training_step.json"
    if not weights.is_file() or not config_path.is_file() or not step_path.is_file():
        raise TrainingPreflightError("Candidate completion pointer references incomplete artifacts.")
    step_state = json.loads(step_path.read_text(encoding="utf-8"))
    result = {
        "schemaVersion": "robotworld.vla-jepa-training-result.v1",
        "runId": row.id,
        "trainingExecuted": True,
        "steps": int(step_state.get("step") or 0),
        "batchSize": int(step_state.get("batch_size") or 0),
        "durationSeconds": None,
        "candidateCheckpointPath": str(candidate.resolve()),
        "candidateWeightsBytes": weights.stat().st_size,
        "candidateWeightsSha256": _sha256(weights),
        "candidateConfigSha256": _sha256(config_path),
        "baseCheckpointPath": str((row.configuration or {}).get("baseCheckpointPath") or ""),
        "datasetRepoId": str((row.configuration or {}).get("datasetRepoId") or ""),
        "device": "unavailable-from-direct-run",
        "peakAllocatedBytes": None,
        "lastCheckpointPointer": str(pointer.resolve()),
        "pushedToHub": False,
        "activeCheckpointOverwritten": False,
        "recoveredFromCompletedArtifacts": True,
    }
    _write_json(root / "training_result_recovered.json", result)
    return result


async def _persist_success(run_id: str, result: dict[str, Any]) -> dict[str, Any]:
    async with SessionLocal() as session:
        row = await session.get(PolicyTrainingRunRecord, run_id)
        if row is None:
            raise KeyError(run_id)
        row.lifecycle_state = "SUCCEEDED"
        row.candidate_checkpoint_path = str(result["candidateCheckpointPath"])
        row.candidate_checkpoint_sha256 = str(result["candidateWeightsSha256"])
        row.metrics = {
            "stepsCompleted": int(result.get("steps") or 0),
            "batchSize": int(result.get("batchSize") or 0),
            "durationSeconds": result.get("durationSeconds"),
            "peakAllocatedBytes": result.get("peakAllocatedBytes"),
            "device": result.get("device"),
            "candidateWeightsBytes": int(result.get("candidateWeightsBytes") or 0),
            "recoveredFromCompletedArtifacts": bool(result.get("recoveredFromCompletedArtifacts")),
        }
        row.validation = {**dict(row.validation or {}), "execution": result}
        row.error = None
        row.finished_at = _now()
        await session.commit()
        return _run_view(row)


async def validate_candidate(
    request: VlaJepaFineTuneValidationRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", by_alias=True)
    command, reused = await command_store.start_command(
        kind="training.vla_jepa.preflight",
        target_type="dataset",
        target_id=request.dataset_id,
        payload=payload,
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        return command_store.command_view(command, reused=True)
    run_id = new_id("trainrun")
    root = (TRAINING_RUNS_DIR / run_id).resolve()
    try:
        dataset = _dataset_record(request.dataset_id)
        async with SessionLocal() as session:
            model = await session.get(ModelRegistrationRecord, request.base_model_id)
        if model is None:
            raise KeyError(request.base_model_id)
        if "vla_policy" not in list(model.roles or []) or (model.capabilities or {}).get("configType") != "vla_jepa":
            raise TrainingPreflightError("The selected base model is not a registered VLA-JEPA policy.")
        if model.lifecycle_state not in {"AVAILABLE", "LOADED"} or not model.enabled:
            raise TrainingPreflightError("The selected base model must be enabled and AVAILABLE or LOADED.")
        checkpoint_root = Path(str(model.local_path or "")).resolve(strict=True)
        lerobot_root = Path(os.environ.get("LEROBOT_REPO_PATH") or DEFAULT_LEROBOT_ROOT).resolve(strict=True)
        qwen_metadata = Path(os.environ.get("QWEN3_VL_METADATA_PATH") or DEFAULT_QWEN_METADATA).resolve(strict=True)
        root.mkdir(parents=True, exist_ok=False)
        input_hash = _json_sha256(
            {
                "request": payload,
                "datasetManifest": dataset,
                "baseModelManifestSha256": model.manifest_sha256,
                "baseModelRevision": model.model_revision,
            }
        )
        configuration = {
            **payload,
            "baseCheckpointPath": str(checkpoint_root),
            "baseModelRevision": model.model_revision,
            "datasetRepoId": dataset.get("repoId"),
            "datasetSourceEvaluationId": dataset.get("sourceEvaluationId"),
            "pushToHub": False,
            "wandbEnabled": False,
            "mixedPrecision": "bf16",
            "candidateOnly": True,
        }
        row = PolicyTrainingRunRecord(
            id=run_id,
            revision=1,
            lifecycle_state="VALIDATING",
            dataset_id=request.dataset_id,
            base_model_id=request.base_model_id,
            configuration=configuration,
            input_sha256=input_hash,
            artifact_dir=str(root),
            created_by=actor,
            started_at=_now(),
        )
        async with SessionLocal() as session:
            session.add(row)
            await session.commit()
        manifest = {
            "schemaVersion": "robotworld.vla-jepa-training-preflight.v1",
            "runId": run_id,
            "artifactRoot": str(root),
            "allowedDatasetRoot": str(DATASETS_DIR.resolve()),
            "allowedModelRoot": str(checkpoint_root),
            "datasetRoot": str(Path(str(dataset["root"])).resolve(strict=True)),
            "datasetRepoId": dataset["repoId"],
            "checkpointRoot": str(checkpoint_root),
            "lerobotRepoRoot": str(lerobot_root),
            "qwenMetadataRoot": str(qwen_metadata),
            "candidateOutput": str(root / "candidate"),
            "steps": request.steps,
            "batchSize": request.batch_size,
            "seed": request.seed,
            "freezeQwen": request.freeze_qwen,
            "enableWorldModel": request.enable_world_model,
            "inputSha256": input_hash,
        }
        manifest_path = root / "preflight_input.json"
        _write_json(manifest_path, manifest)
        result = await asyncio.to_thread(_run_worker, manifest_path)
        result["inputManifestSha256"] = _sha256(manifest_path)
        _write_json(root / "preflight_result.json", result)
        async with SessionLocal() as session:
            stored = await session.get(PolicyTrainingRunRecord, run_id)
            if stored is None:
                raise TrainingPreflightError("Training run disappeared during validation.")
            stored.lifecycle_state = "READY"
            stored.validation = result
            stored.finished_at = _now()
            await session.commit()
            view = _run_view(stored)
        output = {"trainingRun": view}
        await command_store.finish_command(command.id, output=output)
        command.output = command_store.json_safe(output)
        command.status = "SUCCEEDED"
        return command_store.command_view(command)
    except Exception as exc:
        async with SessionLocal() as session:
            stored = await session.get(PolicyTrainingRunRecord, run_id)
            if stored is not None:
                stored.lifecycle_state = "REJECTED"
                stored.error = str(exc)
                stored.finished_at = _now()
                await session.commit()
        await command_store.finish_command(command.id, error=str(exc))
        raise


async def execute_candidate(
    request: VlaJepaFineTuneExecuteRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", by_alias=True)
    command, reused = await command_store.start_command(
        kind="training.vla_jepa.execute",
        target_type="training_run",
        target_id=request.run_id,
        payload=payload,
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        return command_store.command_view(command, reused=True)
    try:
        async with SessionLocal() as session:
            row = await session.get(PolicyTrainingRunRecord, request.run_id)
            if row is None:
                raise KeyError(request.run_id)
            if row.lifecycle_state == "SUCCEEDED":
                output = {"trainingRun": _run_view(row)}
                await command_store.finish_command(command.id, output=output)
                command.output = command_store.json_safe(output)
                command.status = "SUCCEEDED"
                return command_store.command_view(command)
            if row.lifecycle_state != "READY":
                raise TrainingPreflightError(
                    f"Training run must be READY, not {row.lifecycle_state}. Create a fresh preflight candidate."
                )
            configuration = dict(row.configuration or {})
            if int(configuration.get("steps") or 0) > 10:
                raise TrainingPreflightError("The verified execution profile is bounded to 1-10 steps per approval.")
            if int(configuration.get("batchSize") or 0) != 1:
                raise TrainingPreflightError("The verified execution profile requires batchSize=1.")
            if configuration.get("freezeQwen") is not True or configuration.get("enableWorldModel") is not False:
                raise TrainingPreflightError(
                    "The verified 12 GiB execution profile requires freezeQwen=true and enableWorldModel=false."
                )
            root = Path(row.artifact_dir).resolve(strict=True)
            manifest_path = root / "preflight_input.json"
            if not manifest_path.is_file() or _sha256(manifest_path) != (row.validation or {}).get(
                "inputManifestSha256"
            ):
                raise TrainingPreflightError("READY training manifest hash mismatch.")
            if (root / "candidate").exists():
                recovered = await asyncio.to_thread(_recover_completed_candidate, row)
                if recovered is None:
                    row.lifecycle_state = "FAILED"
                    row.error = "Incomplete candidate artifacts exist without a completion pointer."
                    row.finished_at = _now()
                    await session.commit()
                    raise TrainingPreflightError(row.error)
                await session.rollback()
                view = await _persist_success(request.run_id, recovered)
                output = {"trainingRun": view, "recovered": True}
                await command_store.finish_command(command.id, output=output)
                command.output = command_store.json_safe(output)
                command.status = "SUCCEEDED"
                return command_store.command_view(command)
            row.lifecycle_state = "RUNNING"
            row.error = None
            row.started_at = _now()
            row.finished_at = None
            await session.commit()
        result = await asyncio.to_thread(_run_execute_worker, manifest_path)
        view = await _persist_success(request.run_id, result)
        output = {"trainingRun": view, "recovered": False}
        await command_store.finish_command(command.id, output=output)
        command.output = command_store.json_safe(output)
        command.status = "SUCCEEDED"
        return command_store.command_view(command)
    except Exception as exc:
        async with SessionLocal() as session:
            row = await session.get(PolicyTrainingRunRecord, request.run_id)
            if row is not None and row.lifecycle_state not in {"SUCCEEDED", "FAILED"}:
                row.lifecycle_state = "FAILED"
                row.error = str(exc)
                row.finished_at = _now()
                await session.commit()
        await command_store.finish_command(command.id, error=str(exc))
        raise


async def reconcile_direct_runs() -> None:
    """Catalog direct smoke-worker artifacts without treating partial checkpoints as success."""

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(PolicyTrainingRunRecord).where(PolicyTrainingRunRecord.lifecycle_state == "READY")
            )
        ).scalars().all()
    for row in rows:
        candidate_root = Path(row.artifact_dir) / "candidate"
        if not candidate_root.exists():
            continue
        try:
            result = await asyncio.to_thread(_recover_completed_candidate, row)
            if result is not None:
                await _persist_success(row.id, result)
                continue
            async with SessionLocal() as session:
                stored = await session.get(PolicyTrainingRunRecord, row.id)
                if stored is not None and stored.lifecycle_state == "READY":
                    stored.lifecycle_state = "FAILED"
                    stored.error = "Incomplete direct-worker candidate: no valid completion pointer."
                    stored.finished_at = _now()
                    await session.commit()
        except Exception as exc:
            async with SessionLocal() as session:
                stored = await session.get(PolicyTrainingRunRecord, row.id)
                if stored is not None and stored.lifecycle_state == "READY":
                    stored.lifecycle_state = "FAILED"
                    stored.error = f"Direct-worker candidate reconciliation failed: {exc}"
                    stored.finished_at = _now()
                    await session.commit()


async def list_runs(limit: int = 100) -> list[dict[str, Any]]:
    await reconcile_direct_runs()
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(PolicyTrainingRunRecord)
                .order_by(PolicyTrainingRunRecord.created_at.desc())
                .limit(max(1, min(limit, 500)))
            )
        ).scalars().all()
    return [_run_view(row) for row in rows]
