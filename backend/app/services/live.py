"""Real-time MuJoCo evaluation sessions streamed over WebSocket."""
from __future__ import annotations

import asyncio
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..db import SessionLocal
from ..models import Evaluation
from ..util import new_id
from . import events, simcore
from .remote_policy import PolicyConfig, RemotePolicyController


STEPS = ["Initialize physics", "Approach handle", "Close gripper", "Pull articulated door", "Settle and score"]
CONDITIONS = [
    {"name": "Door open angle", "target": "≥ 60°"},
    {"name": "Handle contact", "target": "observed"},
    {"name": "Episode time", "target": "≤ 20 s"},
]


@dataclass
class LiveSession:
    session_id: str
    run_id: str
    scenario: dict[str, Any]
    evaluation_type: str = "asset_validation"
    policy_name: str = "scripted-oracle-v1"
    policy_config: PolicyConfig | None = None
    created_at: float = field(default_factory=time.time)
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=128))
    pause: threading.Event = field(default_factory=threading.Event)
    stop: threading.Event = field(default_factory=threading.Event)
    reset_requested: threading.Event = field(default_factory=threading.Event)
    speed: float = 1.0
    replay: list[dict[str, Any]] = field(default_factory=list)
    started: bool = False
    completed: bool = False


_sessions: dict[str, LiveSession] = {}


def create(*, evaluation_type: str = "asset_validation", policy_config: PolicyConfig | None = None) -> LiveSession:
    rng = np.random.default_rng()
    # A live session must be streamable: sample inside the domain-randomized
    # band until the kinematic planner confirms a feasible approach. Hard
    # scenarios still fail honestly during physics; they just do not end on
    # the first control tick before the viewer receives a frame.
    scenario: dict[str, Any] | None = None
    if evaluation_type == "asset_validation":
        for _ in range(12):
            candidate = simcore.default_scenario_family(rng)
            controller = simcore.ScriptedController(simcore.World(candidate))
            if not controller.path_blocked and controller.plan_error <= 0.06:
                scenario = candidate
                break
    else:
        # Never use the oracle to select easy seeds for policy evaluation.
        scenario = simcore.default_scenario_family(rng)
    if scenario is None:
        scenario = simcore.default_scenario_family(np.random.default_rng(0))
    policy_name = "scripted-oracle-v1" if evaluation_type == "asset_validation" else policy_config.policy_id if policy_config else "remote-vla"
    session = LiveSession(
        session_id=new_id("ses"),
        run_id=new_id("run"),
        scenario=scenario,
        evaluation_type=evaluation_type,
        policy_name=policy_name,
        policy_config=policy_config,
    )
    _sessions[session.session_id] = session
    return session


def get(session_id: str) -> LiveSession | None:
    return _sessions.get(session_id)


def info(session: LiveSession) -> dict[str, Any]:
    return {
        "sessionId": session.session_id,
        "runId": session.run_id,
        "scenario": {
            "name": "Open refrigerator — nominal randomized evaluation",
            "desc": (
                "MuJoCo asset-solvability check using a privileged scripted oracle"
                if session.evaluation_type == "asset_validation"
                else "Closed-loop VLA test using MuJoCo RGB, proprioception, and language"
            ),
            "world": "Articulated Door Validation Lab",
            "policy": session.policy_name,
            "evaluationType": session.evaluation_type,
            "variations": 1,
            "randomization": True,
        },
        "durationS": 20.0,
    }


def meta() -> dict[str, Any]:
    return {
        "type": "meta",
        "durationS": 20.0,
        "steps": [{"name": name} for name in STEPS],
        "conditions": CONDITIONS,
        "events": [
            {"t": 0.0, "time": "00:00", "name": "Physics initialized", "sub": "MJCF model loaded"},
            {"t": 1.0, "time": "00:01", "name": "Approach started", "sub": "IK trajectory executing"},
            {"t": 4.0, "time": "00:04", "name": "Grasp phase", "sub": "Contact and closure measured"},
            {"t": 7.0, "time": "00:07", "name": "Pull phase", "sub": "Hinge state measured"},
        ],
    }


def control(session: LiveSession, action: str, value: float | None = None) -> None:
    if action == "pause":
        session.pause.set()
    elif action == "resume":
        session.pause.clear()
    elif action == "speed":
        session.speed = min(max(float(value or 1.0), 0.25), 4.0)
    elif action == "reset":
        session.reset_requested.set()
        session.stop.set()
    elif action in {"stop", "end"}:
        session.stop.set()


def _decorate(frame: dict[str, Any]) -> dict[str, Any]:
    t = float(frame["t"])
    angle = float(frame["doorAngleDeg"])
    contact = bool(frame["inContact"])
    steps_done = 1 + int(t >= 1.0) + int(contact) + int(angle >= 5.0) + int(angle >= 60.0)
    return {
        "type": "frame",
        **frame,
        "success": round(min(max(angle / 60.0, 0.0), 1.0) * 100.0, 1),
        "stepsDone": min(steps_done, len(STEPS)),
        "conditions": [angle >= 60.0, contact, t <= 20.0],
        "eventsFired": [i for i, threshold in enumerate((0.0, 1.0, 4.0, 7.0)) if t >= threshold],
        "done": False,
    }


async def _persist(session: LiveSession, result: simcore.RolloutResult) -> None:
    async with SessionLocal() as db:
        db.add(
            Evaluation(
                id=new_id("ev"),
                run_id=session.run_id,
                skill_id="open-refrigerator",
                policy=session.policy_name,
                success=result.success,
                door_angle_deg=result.door_angle_deg,
                collisions=result.collisions,
                duration_s=result.duration_s,
                failure_mode=result.failure_mode,
                failure_detail=result.failure_detail,
            )
        )
        await db.commit()


async def run(session: LiveSession) -> None:
    """Execute rollouts until completion or a requested reset."""
    if session.started:
        return
    session.started = True
    loop = asyncio.get_running_loop()
    session.replay = [meta()]
    await session.queue.put(meta())

    while True:
        session.stop.clear()
        session.reset_requested.clear()
        session.completed = False

        def on_frame(raw: dict[str, Any]) -> bool:
            while session.pause.is_set() and not session.stop.is_set():
                time.sleep(0.04)
            if session.stop.is_set():
                return False
            decorated = _decorate(raw)
            session.replay.append(decorated)

            def enqueue() -> None:
                if session.queue.full():
                    try:
                        session.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                session.queue.put_nowait(decorated)

            loop.call_soon_threadsafe(enqueue)
            time.sleep(max(0.0, 1.0 / (20.0 * session.speed)))
            return True

        world = simcore.World(session.scenario)
        controller_factory = (
            simcore.ScriptedController
            if session.evaluation_type == "asset_validation"
            else lambda w: RemotePolicyController(w, session.policy_config)  # type: ignore[arg-type]
        )
        result = await asyncio.to_thread(
            simcore.run_rollout,
            world,
            controller_factory,
            on_frame=on_frame,
            frame_hz=20.0,
            record=False,
            should_stop=session.stop.is_set,
        )
        if session.reset_requested.is_set():
            session.replay = [meta()]
            await session.queue.put(meta())
            continue

        await _persist(session, result)
        session.completed = True
        summary = (
            f"Door reached {result.door_angle_deg:.1f}° with {result.collisions} unintended contacts."
            if result.success
            else f"{result.failure_mode or 'ended'}: {result.failure_detail or 'episode stopped'}"
        )
        end = {"type": "end", "success": result.success, "summary": summary}
        session.replay.append(end)
        await session.queue.put(end)
        events.publish("ok" if result.success else "info", "Evaluation completed", summary, runId=session.run_id)
        break


def replay(session: LiveSession) -> dict[str, Any]:
    return {"session": info(session), "messages": session.replay, "completed": session.completed}
