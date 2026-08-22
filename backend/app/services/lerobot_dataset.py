"""Versioned LeRobot demonstration exports from successful oracle evaluations."""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

from ..config import BASE_DIR, DATASETS_DIR, WORLDS_DIR
from ..contracts import LeRobotDatasetExportRequest
from ..db import SessionLocal
from ..models import EvaluationRunRecord
from ..util import new_id
from . import command_store


WORKER_SCRIPT = (BASE_DIR / "workers" / "lerobot_dataset_worker.py").resolve()
DEFAULT_VLA_PYTHON = Path(r"D:\RobotWorldRuntimes\vla-env\Scripts\python.exe")
RESULT_PREFIX = "ROBOTWORLD_DATASET_RESULT="


class DatasetExportError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _quat_to_matrix(value: list[float]) -> np.ndarray:
    quat = np.asarray(value, dtype=np.float64)
    if quat.shape != (4,) or not np.isfinite(quat).all():
        raise DatasetExportError("Oracle trajectory contains an invalid end-effector quaternion.")
    norm = float(np.linalg.norm(quat))
    if norm < 1e-8:
        raise DatasetExportError("Oracle trajectory contains a zero end-effector quaternion.")
    w, x, y, z = quat / norm
    return np.asarray(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def _matrix_to_intrinsic_xyz(rotation: np.ndarray) -> np.ndarray:
    pitch = math.asin(float(np.clip(rotation[0, 2], -1.0, 1.0)))
    cosine = math.cos(pitch)
    if abs(cosine) > 1e-6:
        roll = math.atan2(float(-rotation[1, 2]), float(rotation[2, 2]))
        yaw = math.atan2(float(-rotation[0, 1]), float(rotation[0, 0]))
    else:
        roll = math.atan2(float(rotation[2, 1]), float(rotation[1, 1]))
        yaw = 0.0
    return np.asarray([roll, pitch, yaw], dtype=np.float64)


def _resample(trajectory: list[dict[str, Any]], fps: int) -> list[dict[str, Any]]:
    candidates = [item for item in trajectory if isinstance(item.get("observationFrames"), dict)]
    if len(candidates) < 2:
        raise DatasetExportError(
            "Evaluation has no synchronized demonstration frames. Re-run the oracle with recordObservations=true."
        )
    period = 1.0 / fps
    selected = [candidates[0]]
    for item in candidates[1:]:
        if float(item["timeSeconds"]) - float(selected[-1]["timeSeconds"]) >= period - 1e-6:
            selected.append(item)
    if len(selected) < 2:
        raise DatasetExportError("Evaluation is too short for the requested dataset frame rate.")
    return selected


def _frame_contract(
    current: dict[str, Any],
    following: dict[str, Any],
    *,
    artifact_root: Path,
    instruction: str,
) -> dict[str, Any]:
    current_position = np.asarray(current.get("endEffectorPositionM"), dtype=np.float64)
    following_position = np.asarray(following.get("endEffectorPositionM"), dtype=np.float64)
    current_quaternion = list(current.get("endEffectorQuaternionWxyz") or [])
    following_quaternion = list(following.get("endEffectorQuaternionWxyz") or [])
    if current_position.shape != (3,) or following_position.shape != (3,):
        raise DatasetExportError("Oracle trajectory contains an invalid end-effector position.")
    current_rotation = _quat_to_matrix(current_quaternion)
    following_rotation = _quat_to_matrix(following_quaternion)
    local_translation = current_rotation.T @ (following_position - current_position)
    local_rotation = _matrix_to_intrinsic_xyz(current_rotation.T @ following_rotation)
    width = float(following.get("gripperWidthM"))
    gripper = float(np.clip((width / 0.08) * 2.0 - 1.0, -1.0, 1.0))
    action = np.concatenate((local_translation, local_rotation, [gripper]))
    state = np.asarray([*current_position, *current_quaternion, float(current.get("gripperWidthM"))], dtype=np.float64)
    if action.shape != (7,) or state.shape != (8,) or not np.isfinite(action).all() or not np.isfinite(state).all():
        raise DatasetExportError("Derived demonstration state/action is non-finite or has the wrong shape.")
    observations = current["observationFrames"]
    images: dict[str, str] = {}
    for camera, feature in (("front", "exterior_1_left"), ("wrist", "exterior_2_left")):
        record = observations.get(camera) or {}
        path = (artifact_root / str(record.get("path") or "")).resolve()
        if artifact_root != path and artifact_root not in path.parents:
            raise DatasetExportError("Demonstration frame path escapes its evaluation artifact root.")
        if not path.is_file() or path.suffix.lower() != ".png":
            raise DatasetExportError(f"Demonstration frame is missing: {path}")
        if str(record.get("sha256") or "") != _sha256(path):
            raise DatasetExportError(f"Demonstration frame hash mismatch: {path.name}")
        images[feature] = str(path)
    return {
        "timeSeconds": float(current["timeSeconds"]),
        "phase": str(current.get("phase") or "oracle"),
        "task": instruction,
        "state": [float(value) for value in state],
        "action": [float(value) for value in action],
        "images": images,
    }


def _python_path() -> Path:
    configured = os.environ.get("VLA_JEPA_PYTHON")
    path = Path(configured).resolve() if configured else DEFAULT_VLA_PYTHON.resolve()
    if not path.is_file():
        raise DatasetExportError(f"LeRobot worker Python is missing: {path}")
    return path


def _run_worker(manifest_path: Path, output_root: Path) -> dict[str, Any]:
    if not WORKER_SCRIPT.is_file():
        raise DatasetExportError(f"LeRobot dataset worker is missing: {WORKER_SCRIPT}")
    environment = os.environ.copy()
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    completed = subprocess.run(
        [str(_python_path()), "-u", str(WORKER_SCRIPT), "--manifest", str(manifest_path), "--output", str(output_root)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=environment,
        creationflags=creation_flags,
    )
    result_line = next((line for line in reversed(completed.stdout.splitlines()) if line.startswith(RESULT_PREFIX)), None)
    if completed.returncode != 0 or result_line is None:
        detail = (completed.stderr or completed.stdout or "dataset worker returned no result").strip()[-4000:]
        raise DatasetExportError(f"LeRobot dataset worker failed ({completed.returncode}): {detail}")
    return json.loads(result_line.removeprefix(RESULT_PREFIX))


async def export_evaluation(
    request: LeRobotDatasetExportRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", by_alias=True)
    command, reused = await command_store.start_command(
        kind="training.dataset.export_lerobot",
        target_type="evaluation",
        target_id=request.evaluation_id,
        payload=payload,
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        return command_store.command_view(command, reused=True)
    try:
        async with SessionLocal() as session:
            evaluation = await session.get(EvaluationRunRecord, request.evaluation_id)
        if evaluation is None:
            raise KeyError(request.evaluation_id)
        if evaluation.status != "SUCCEEDED" or not evaluation.success:
            raise DatasetExportError("Only successful terminal oracle evaluations may become demonstrations.")
        if "oracle" not in str(evaluation.policy).lower():
            raise DatasetExportError("Only deterministic-oracle trajectories may become approved demonstrations.")
        artifact_root = Path(evaluation.artifact_dir).resolve()
        worlds_root = WORLDS_DIR.resolve()
        if worlds_root != artifact_root and worlds_root not in artifact_root.parents:
            raise DatasetExportError("Evaluation artifact path is outside the RobotWorld world store.")
        result = dict(evaluation.result or {})
        selected = _resample(list(result.get("trajectory") or []), request.fps)
        frames = [
            _frame_contract(current, following, artifact_root=artifact_root, instruction=request.instruction)
            for current, following in zip(selected, selected[1:])
        ]
        dataset_id = new_id("dataset")
        root = (DATASETS_DIR / dataset_id).resolve()
        if DATASETS_DIR.resolve() not in root.parents:
            raise DatasetExportError("Invalid dataset artifact target.")
        root.mkdir(parents=True, exist_ok=False)
        manifest = {
            "schemaVersion": "robotworld.lerobot-export-manifest.v1",
            "datasetId": dataset_id,
            "evaluationId": evaluation.id,
            "robotId": evaluation.robot_id,
            "worldTemplateId": evaluation.world_template_id,
            "policy": evaluation.policy,
            "seed": evaluation.seed,
            "evaluationArtifactRoot": str(artifact_root),
            "fps": request.fps,
            "instruction": request.instruction,
            "frames": frames,
        }
        manifest_path = root / "source_manifest.json"
        _write_json(manifest_path, manifest)
        worker_result = _run_worker(manifest_path, root / "lerobot")
        output = {
            "dataset": {
                **worker_result,
                "sourceEvaluationId": evaluation.id,
                "sourceManifestSha256": _sha256(manifest_path),
                "artifactRoot": str(root),
                "lifecycleState": "VALIDATED",
            }
        }
        _write_json(root / "dataset_manifest.json", output["dataset"])
        await command_store.finish_command(command.id, output=output)
        command.output = command_store.json_safe(output)
        command.status = "SUCCEEDED"
        return command_store.command_view(command)
    except Exception as exc:
        await command_store.finish_command(command.id, error=str(exc))
        raise


async def list_datasets(limit: int = 100) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(DATASETS_DIR.glob("dataset_*/dataset_manifest.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("readbackValidated") is not True:
            record["lifecycleState"] = "LEGACY_UNVERIFIED"
            record["validationErrors"] = [
                "This export predates end-to-end LeRobot dataset readback validation and cannot be used for training."
            ]
        else:
            record["validationErrors"] = []
        rows.append(record)
        if len(rows) >= max(1, min(limit, 500)):
            break
    return rows
