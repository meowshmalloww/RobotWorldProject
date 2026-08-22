"""Continuous authoritative Franka evaluation stream for the Worlds operator."""
from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
import time
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..contracts import CompiledAssetOracleRequest, OracleEvaluationRequest
from ..util import new_id
from . import evaluation_catalog, franka_pick_place


STREAM_HZ = 25


@dataclass
class FrankaLiveSession:
    session_id: str
    operation: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=4))
    started: bool = False
    completed: bool = False
    frame_count: int = 0
    latest_frame: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None = None
    error: str | None = None
    task: asyncio.Task[None] | None = None
    mode: str = "oracle"
    backend: franka_pick_place.MujocoFrankaBackend | None = None
    controller: franka_pick_place.AuthoredScenePickPlaceOracle | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    executor: ThreadPoolExecutor | None = None


_sessions: dict[str, FrankaLiveSession] = {}


def create(operation: dict[str, Any]) -> FrankaLiveSession:
    session = FrankaLiveSession(session_id=new_id("live"), operation=dict(operation))
    _sessions[session.session_id] = session
    return session


async def create_manual(operation: dict[str, Any], template: dict[str, Any]) -> FrankaLiveSession:
    session_id = new_id("manual")
    artifact_dir = (Path(template["runtimePath"]).parent.parent / "manual-sessions" / session_id).resolve()
    session = FrankaLiveSession(
        session_id=session_id,
        operation=dict(operation),
        started=True,
        mode="manual",
        executor=ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"franka-{session_id}"),
    )
    frame_callback = _make_frame_callback(session)

    def initialize() -> None:
        backend = franka_pick_place.MujocoFrankaBackend(Path(template["runtimePath"]))
        backend.reset(int(operation.get("seed", 6203)))
        backend.step(150)
        controller = franka_pick_place.AuthoredScenePickPlaceOracle(
            backend,
            artifact_dir,
            template,
            live_frame_callback=frame_callback,
            realtime=True,
        )
        assert backend.data is not None
        controller.desired_rotation = backend.data.site_xmat[backend.ee_site].reshape(3, 3).copy()
        session.backend = backend
        session.controller = controller
        controller._record("manual_ready")

    assert session.executor is not None
    await asyncio.get_running_loop().run_in_executor(session.executor, initialize)
    _sessions[session_id] = session
    return session


def get(session_id: str) -> FrankaLiveSession | None:
    return _sessions.get(session_id)


def info(session: FrankaLiveSession) -> dict[str, Any]:
    operation = session.operation
    authored = dict(operation.get("authoredScene") or {})
    operation_view = {
        key: operation.get(key)
        for key in ("robotId", "instruction", "backend", "controller", "task", "seed", "executionScope", "worldId", "assetVersionId")
        if operation.get(key) is not None
    }
    if authored:
        operation_view["authoredScene"] = {
            "worldId": authored.get("worldId"),
            "taskKind": authored.get("taskKind"),
            "sourcePlacement": {"assetId": (authored.get("sourcePlacement") or {}).get("assetId")},
            "targetPlacement": {"assetId": (authored.get("targetPlacement") or {}).get("assetId")} if authored.get("targetPlacement") else None,
            "counterPlacement": {"assetId": (authored.get("counterPlacement") or {}).get("assetId")},
            "robotSpawn": authored.get("robotSpawn"),
            "relation": authored.get("relation"),
        }
    if operation.get("compiledGoal"):
        operation_view["compiledGoal"] = dict(operation["compiledGoal"])
    return {
        "schemaVersion": "robotworld.franka-live-session.v1",
        "sessionId": session.session_id,
        "lifecycleState": "MANUAL_READY" if session.mode == "manual" and not session.completed else "CLOSED" if session.mode == "manual" and session.completed else "SUCCEEDED" if session.completed and session.evaluation and session.evaluation.get("success") else (
            "FAILED" if session.completed else "RUNNING" if session.started else "READY"
        ),
        "authoritative": True,
        "mode": session.mode,
        "backend": "mujoco",
        "physicsHz": franka_pick_place.PHYSICS_HZ,
        "controlHz": franka_pick_place.CONTROL_HZ,
        "streamHz": STREAM_HZ,
        "operation": operation_view,
        "frameCount": session.frame_count,
        "evaluation": session.evaluation,
        "error": session.error,
    }


def _composite_jpeg(front: np.ndarray, wrist: np.ndarray) -> str:
    canvas = Image.fromarray(front, mode="RGB")
    inset = Image.fromarray(wrist, mode="RGB")
    border = 3
    x = canvas.width - inset.width - 14
    y = 14
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(
        (x - border, y - border, x + inset.width + border, y + inset.height + border),
        fill=(8, 10, 14),
    )
    canvas.paste(inset, (x, y))
    output = BytesIO()
    canvas.save(output, format="JPEG", quality=84, optimize=False, subsampling=1)
    return base64.b64encode(output.getvalue()).decode("ascii")


def _enqueue(session: FrankaLiveSession, message: dict[str, Any]) -> None:
    if session.queue.full():
        try:
            session.queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    session.queue.put_nowait(message)


def _make_frame_callback(session: FrankaLiveSession):
    """Create the one authoritative frame encoder used by oracle and manual sessions."""

    loop = asyncio.get_running_loop()

    def on_frame(state: dict[str, Any], front: np.ndarray, wrist: np.ndarray) -> None:
        session.frame_count += 1
        render_geometries = [dict(item) for item in (state.get("renderGeometries") or [])]
        asset_version_id = session.operation.get("assetVersionId")
        source_pbr_transform = session.operation.get("sourcePbrTransform")
        if asset_version_id:
            for geometry in render_geometries:
                if geometry.get("name") == "pick_object_visual":
                    geometry["assetVersionId"] = asset_version_id
                    geometry["sourcePbrTransform"] = source_pbr_transform
        message = {
            "type": "frame",
            "sequence": session.frame_count,
            "authoritative": True,
            "simTimeSeconds": state.get("timeSeconds"),
            "phase": state.get("phase"),
            "jpegBase64": _composite_jpeg(front, wrist),
            "state": {
                "jointPosition": state.get("jointPosition"),
                "gripperWidthM": state.get("gripperWidthM"),
                "endEffectorPositionM": state.get("endEffectorPositionM"),
                "objectPositionM": state.get("objectPositionM"),
                "contactCount": state.get("contactCount"),
                "objectContacts": state.get("objectContacts"),
                "finite": state.get("finite"),
                "renderGeometries": render_geometries,
            },
        }
        session.latest_frame = message
        loop.call_soon_threadsafe(_enqueue, session, message)

    return on_frame


async def manual_jog(session: FrankaLiveSession, delta_m: tuple[float, float, float]) -> dict[str, Any]:
    """Move the real simulated end effector by one bounded Cartesian increment."""

    if session.mode != "manual" or session.backend is None or session.controller is None:
        raise ValueError("Session is not an active manual MuJoCo session.")
    async with session.lock:
        assert session.backend.data is not None
        current = session.backend.data.site_xpos[session.backend.ee_site].copy()
        target = current + np.asarray(delta_m, dtype=float)
        bounds = session.controller.template.get("workspaceSafetyBoundsM") or []
        if len(bounds) != 3 or any(not (float(bounds[index][0]) <= target[index] <= float(bounds[index][1])) for index in range(3)):
            raise ValueError(f"Jog target {target.tolist()} is outside the compiled workspace safety bounds {bounds}.")
        assert session.executor is not None
        reached = await asyncio.get_running_loop().run_in_executor(
            session.executor,
            lambda: session.controller._move(
                target,
                "manual_jog",
                max_ticks=120,
                position_tolerance_m=0.007,
            ),
        )
        state = session.backend.state()
        return {
            "reached": bool(reached),
            "targetM": target.tolist(),
            "endEffectorPositionM": state["endEffectorPositionM"],
            "finite": state["finite"],
            "session": info(session),
        }


async def manual_gripper(session: FrankaLiveSession, command: str) -> dict[str, Any]:
    """Open or close the physical Panda gripper actuator in the active session."""

    if session.mode != "manual" or session.backend is None or session.controller is None:
        raise ValueError("Session is not an active manual MuJoCo session.")
    if command not in {"open", "close"}:
        raise ValueError("Gripper command must be open or close.")
    async with session.lock:
        assert session.executor is not None
        await asyncio.get_running_loop().run_in_executor(
            session.executor,
            lambda: session.controller._gripper(
                255.0 if command == "open" else 0.0,
                f"manual_gripper_{command}",
                120,
            ),
        )
        state = session.backend.state()
        return {
            "command": command,
            "gripperWidthM": state["gripperWidthM"],
            "finite": state["finite"],
            "session": info(session),
        }


async def close_manual(session: FrankaLiveSession) -> None:
    async with session.lock:
        backend = session.backend
        executor = session.executor
        if backend is not None and executor is not None:
            await asyncio.get_running_loop().run_in_executor(executor, backend.close)
        session.completed = True
        session.backend = None
        session.controller = None
        session.executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        _enqueue(session, {"type": "end", "session": info(session)})


def evaluation_summary(evaluation: dict[str, Any]) -> dict[str, Any]:
    """Keep socket messages bounded; the full trajectory remains in the catalog."""

    result = dict(evaluation.get("result") or {})
    return {
        key: evaluation.get(key)
        for key in (
            "id",
            "status",
            "robotId",
            "worldTemplateId",
            "policy",
            "seed",
            "success",
            "failureCode",
            "failureDetail",
            "startedAt",
            "finishedAt",
        )
    } | {
        "result": {
            "predicate": result.get("predicate"),
            "contactSummary": result.get("contactSummary"),
        }
    }


async def run(session: FrankaLiveSession) -> None:
    """Run the same persisted oracle used by evaluations and publish sampled frames."""

    if session.started:
        return
    session.started = True
    on_frame = _make_frame_callback(session)

    try:
        operation = session.operation
        asset_version_id = operation.get("assetVersionId")
        authored_scene = operation.get("authoredScene")
        if authored_scene:
            envelope = await evaluation_catalog.run_authored_scene_pick_place_oracle(
                robot_id=operation["robotId"],
                asset_version_id=asset_version_id,
                seed=int(operation.get("seed", 6203)),
                scene_spec=authored_scene,
                idempotency_key=f"franka-live:{session.session_id}",
                actor="worlds-live",
                live_frame_callback=on_frame,
                realtime=True,
                task_kind=str(operation.get("task") or "pick_place"),
            )
        elif asset_version_id:
            envelope = await evaluation_catalog.run_compiled_asset_pick_place_oracle(
                CompiledAssetOracleRequest(
                    robotId=operation["robotId"],
                    assetVersionId=asset_version_id,
                    seed=int(operation.get("seed", 6203)),
                ),
                idempotency_key=f"franka-live:{session.session_id}",
                actor="worlds-live",
                live_frame_callback=on_frame,
                realtime=True,
            )
        else:
            envelope = await evaluation_catalog.run_pick_place_oracle(
                OracleEvaluationRequest(
                    robotId=operation["robotId"],
                    seed=int(operation.get("seed", 6203)),
                ),
                idempotency_key=f"franka-live:{session.session_id}",
                actor="worlds-live",
                live_frame_callback=on_frame,
                realtime=True,
            )
        session.evaluation = evaluation_summary(
            dict((envelope.get("result") or {}).get("evaluation") or {})
        )
        session.completed = True
        await session.queue.put(
            {
                "type": "end",
                "session": info(session),
                "evaluation": session.evaluation,
            }
        )
    except Exception as exc:
        session.error = str(exc)
        session.completed = True
        await session.queue.put({"type": "error", "message": session.error, "session": info(session)})
