"""Persisted acceptance scenarios for the two requested end-to-end demos.

These builders create and validate real MuJoCo environments. They never mark a
robot task successful without a compatible learned-policy execution and state-
based success predicates. Until that policy is configured, runs stop in the
explicit ``awaiting_policy`` state after producing reproducible world evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import mujoco
import numpy as np

from ..config import DEMOS_DIR
from ..db import SessionLocal
from ..models import Job
from . import events, settings_store
from .remote_policy import PolicyClient, PolicyConfig, PolicyError
from .vulkan_renderer import probe as probe_vulkan


SCENARIOS: dict[str, dict[str, Any]] = {
    "kitchen-juice": {
        "id": "kitchen-juice",
        "name": "Kitchen · prepare blender ingredients",
        "world": "kitchen",
        "engine": "MuJoCo",
        "description": "Random fruit pickup, sink wash, blender loading, cup retrieval, device activation, and pour.",
        "hierarchy": [
            {"id": "kitchen-root", "name": "Kitchen Acceptance World", "icon": "worlds", "children": [
                {"id": "robot", "name": "Robot embodiment", "icon": "robot", "children": []},
                {"id": "fruit-set", "name": "Randomized fruit set", "icon": "cube", "children": []},
                {"id": "sink", "name": "Sink wash region", "icon": "cube", "children": []},
                {"id": "blender", "name": "Blender", "icon": "cube", "children": [
                    {"id": "blender-base", "name": "Base / switch", "icon": "cube", "children": []},
                    {"id": "blender-jar", "name": "Jar", "icon": "cube", "children": []},
                    {"id": "blender-lid", "name": "Lid", "icon": "cube", "children": []},
                ]},
                {"id": "cup-cabinet", "name": "Cup cabinet", "icon": "cube", "children": []},
            ]},
        ],
        "disclosure": "Liquid transformation is not inferred from animation. Completion requires measured object/device states; fluid appearance is outside this acceptance gate.",
        "steps": [
            "Detect every fruit in the randomized work area",
            "Transport each fruit to the sink wash region",
            "Place washed fruit inside the blender jar",
            "Open the cabinet and retrieve one cup",
            "Close the blender lid and activate the blender",
            "Pour into the cup and place the cup in the serving region",
        ],
        "successPredicates": [
            "all fruit bodies entered the sink region before entering the blender",
            "all fruit bodies are contained by the blender jar",
            "cabinet joint opened and a cup was grasped",
            "blender lid is closed and switch state is active",
            "cup is in the serving region before the step limit",
            "no dropped object, forbidden collision, force-limit breach, invalid action, or policy timeout",
        ],
    },
    "factory-sort": {
        "id": "factory-sort",
        "name": "Logistics · route parcels to trucks",
        "world": "factory",
        "engine": "MuJoCo",
        "description": "Random parcel poses and route labels; sort and place every parcel fully inside the correct truck bay.",
        "hierarchy": [
            {"id": "factory-root", "name": "Logistics Acceptance World", "icon": "worlds", "children": [
                {"id": "robot", "name": "Robot embodiment", "icon": "robot", "children": []},
                {"id": "parcel-set", "name": "Randomized parcel set", "icon": "cube", "children": []},
                {"id": "input-table", "name": "Input table", "icon": "cube", "children": []},
                {"id": "truck-postal", "name": "Postal truck bay", "icon": "cube", "children": []},
                {"id": "truck-ups", "name": "UPS truck bay", "icon": "cube", "children": []},
                {"id": "truck-amazon", "name": "Amazon truck bay", "icon": "cube", "children": []},
            ]},
        ],
        "disclosure": "Route labels are episode inputs. Ground-truth routes are used only by the evaluator, never as hidden policy observations.",
        "steps": [
            "Read the visible route or carrier label",
            "Plan a collision-checked grasp from the current pose",
            "Lift and transport the parcel",
            "Place the parcel fully inside its matching truck bay",
            "Reobserve and repeat until the input area is empty",
        ],
        "successPredicates": [
            "every parcel has a measured grasp and release contact sequence",
            "every parcel center and bounds finish inside its assigned truck bay",
            "no parcel enters an incorrect bay",
            "no parcel drop, forbidden collision, force-limit breach, invalid action, or policy timeout",
            "completion occurs before the step limit on an unseen randomized seed",
        ],
    },
}


def definitions() -> list[dict[str, Any]]:
    return [dict(item) for item in SCENARIOS.values()]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _geom(name: str, kind: str, size: str, pos: str, rgba: str, *, body: bool = False, mass: float = 0.0) -> str:
    geom = f'<geom name="{escape(name)}" type="{kind}" size="{size}" rgba="{rgba}" friction="0.9 0.02 0.002"'
    if mass > 0:
        geom += f' mass="{mass:.5f}"'
    geom += "/>"
    if not body:
        return f'<geom name="{escape(name)}" type="{kind}" size="{size}" pos="{pos}" rgba="{rgba}" friction="0.9 0.02 0.002"/>'
    return f'<body name="{escape(name)}" pos="{pos}"><freejoint/>{geom}</body>'


def _kitchen_manifest(rng: np.random.Generator, seed: int) -> dict[str, Any]:
    kinds = [("apple", 0.085, 0.18, "0.62 0.16 0.10 1"), ("orange", 0.078, 0.16, "0.85 0.36 0.08 1"), ("lemon", 0.068, 0.12, "0.78 0.72 0.12 1"), ("lime", 0.06, 0.10, "0.25 0.55 0.18 1")]
    fruits = []
    for index in range(int(rng.integers(3, 7))):
        kind, radius, mass, rgba = kinds[int(rng.integers(0, len(kinds)))]
        fruits.append({
            "id": f"fruit-{index + 1}",
            "kind": kind,
            "radiusM": radius,
            "massKg": mass,
            "rgba": rgba,
            "positionM": [round(float(rng.uniform(0.8, 2.8)), 4), round(float(rng.uniform(-0.55, 0.55)), 4), round(1.03 + radius, 4)],
        })
    return {
        "schemaVersion": "robotworld.acceptance.v1",
        "scenarioId": "kitchen-juice",
        "seed": seed,
        "engine": {"name": "MuJoCo", "version": mujoco.__version__, "timestepS": 0.002},
        "randomization": {"fruitCount": len(fruits), "fruitKinds": [item["kind"] for item in fruits], "poses": True},
        "objects": fruits,
        "regions": {
            "sink": {"centerM": [-1.8, -4.42, 1.05], "sizeM": [1.2, 0.55, 0.12]},
            "blenderJar": {"centerM": [1.05, -4.2, 1.78], "sizeM": [0.46, 0.46, 0.72]},
            "serving": {"centerM": [2.7, -1.4, 1.15], "sizeM": [1.2, 0.8, 0.25]},
        },
        "instruction": "Wash every fruit, load and close the blender, retrieve a cup, activate the blender, and serve the drink.",
        "maxSteps": 1800,
        "successPredicates": SCENARIOS["kitchen-juice"]["successPredicates"],
    }


def _factory_manifest(rng: np.random.Generator, seed: int) -> dict[str, Any]:
    carriers = ("postal", "ups", "amazon")
    parcels = []
    for index in range(int(rng.integers(5, 10))):
        carrier = carriers[int(rng.integers(0, len(carriers)))]
        sx, sy, sz = [round(float(value), 4) for value in rng.uniform([0.18, 0.16, 0.11], [0.34, 0.28, 0.24])]
        parcels.append({
            "id": f"parcel-{index + 1}",
            "visibleRouteLabel": carrier,
            "targetBay": f"truck-{carrier}",
            "halfSizeM": [sx, sy, sz],
            "massKg": round(float(rng.uniform(0.35, 2.4)), 4),
            "positionM": [round(float(rng.uniform(-2.2, 1.1)), 4), round(float(rng.uniform(-0.45, 0.45)), 4), round(1.04 + sz, 4)],
        })
    return {
        "schemaVersion": "robotworld.acceptance.v1",
        "scenarioId": "factory-sort",
        "seed": seed,
        "engine": {"name": "MuJoCo", "version": mujoco.__version__, "timestepS": 0.002},
        "randomization": {"parcelCount": len(parcels), "routes": True, "poses": True, "dimensions": True, "masses": True},
        "objects": parcels,
        "truckBays": {carrier: {"centerM": [x, -4.2, 0.65], "sizeM": [2.4, 1.7, 1.2]} for carrier, x in zip(carriers, (-5.2, 0.0, 5.2), strict=True)},
        "instruction": "Read each parcel label and place the parcel fully inside the matching truck bay.",
        "maxSteps": 2200,
        "successPredicates": SCENARIOS["factory-sort"]["successPredicates"],
    }


def _mjcf(manifest: dict[str, Any]) -> str:
    dynamic: list[str] = []
    fixed: list[str] = [
        '<geom name="floor" type="plane" size="9 6 0.1" rgba="0.20 0.20 0.20 1" friction="1 0.02 0.002"/>',
        _geom("robot-base", "box", "0.35 0.35 0.18", "0 1.6 0.18", "0.45 0.45 0.45 1"),
    ]
    if manifest["scenarioId"] == "kitchen-juice":
        fixed += [
            _geom("worktable", "box", "2.2 0.85 0.45", "1.0 0 0.45", "0.42 0.40 0.37 1"),
            # The sink and blender jar are open collision vessels, not solid
            # proxy boxes that would make their containment predicates impossible.
            _geom("sink-floor", "box", "0.60 0.42 0.04", "-1.8 -4.42 0.94", "0.42 0.46 0.48 1"),
            _geom("sink-left", "box", "0.04 0.42 0.16", "-2.36 -4.42 1.06", "0.42 0.46 0.48 1"),
            _geom("sink-right", "box", "0.04 0.42 0.16", "-1.24 -4.42 1.06", "0.42 0.46 0.48 1"),
            _geom("sink-front", "box", "0.60 0.04 0.16", "-1.8 -4.04 1.06", "0.42 0.46 0.48 1"),
            _geom("sink-back", "box", "0.60 0.04 0.16", "-1.8 -4.80 1.06", "0.42 0.46 0.48 1"),
            _geom("blender-base", "box", "0.28 0.28 0.22", "1.05 -4.2 1.18", "0.12 0.13 0.14 1"),
            _geom("blender-jar-floor", "box", "0.24 0.24 0.025", "1.05 -4.2 1.425", "0.58 0.64 0.66 0.75"),
            _geom("blender-jar-left", "box", "0.025 0.24 0.36", "0.835 -4.2 1.76", "0.58 0.64 0.66 0.55"),
            _geom("blender-jar-right", "box", "0.025 0.24 0.36", "1.265 -4.2 1.76", "0.58 0.64 0.66 0.55"),
            _geom("blender-jar-front", "box", "0.24 0.025 0.36", "1.05 -3.985 1.76", "0.58 0.64 0.66 0.55"),
            _geom("blender-jar-back", "box", "0.24 0.025 0.36", "1.05 -4.415 1.76", "0.58 0.64 0.66 0.55"),
            # Cabinet frame remains open at the front.  The door, lid and
            # switch below are independently jointed, named task parts.
            _geom("cabinet-back", "box", "0.90 0.04 0.62", "0.8 -4.75 2.05", "0.40 0.38 0.34 1"),
            _geom("cabinet-top", "box", "0.90 0.34 0.04", "0.8 -4.47 2.63", "0.40 0.38 0.34 1"),
            _geom("cabinet-bottom", "box", "0.90 0.34 0.04", "0.8 -4.47 1.47", "0.40 0.38 0.34 1"),
            _geom("cabinet-left", "box", "0.04 0.34 0.62", "-0.06 -4.47 2.05", "0.40 0.38 0.34 1"),
            _geom("cabinet-right", "box", "0.04 0.34 0.62", "1.66 -4.47 2.05", "0.40 0.38 0.34 1"),
        ]
        dynamic += [
            '''<body name="blender-lid" pos="1.05 -4.44 2.145">
                 <joint name="j_blender_lid" type="hinge" axis="1 0 0" range="0 1.92" damping="0.25"/>
                 <geom name="blender-lid-geom" type="box" size="0.26 0.24 0.04" pos="0 0.24 0" mass="0.32" rgba="0.10 0.11 0.12 1"/>
               </body>''',
            '''<body name="cabinet-door" pos="-0.10 -4.10 2.05">
                 <joint name="j_cabinet_door" type="hinge" axis="0 0 1" range="0 1.92" damping="0.45" frictionloss="0.12"/>
                 <geom name="cabinet-door-panel" type="box" size="0.90 0.035 0.58" pos="0.90 0 0" mass="4.0" rgba="0.45 0.42 0.37 1"/>
                 <geom name="cabinet-door-handle" type="capsule" size="0.025 0.12" pos="1.65 0.09 0" euler="90 0 0" mass="0.15" rgba="0.72 0.72 0.70 1"/>
               </body>''',
            '''<body name="blender-switch" pos="1.05 -3.90 1.18">
                 <joint name="j_blender_switch" type="slide" axis="0 -1 0" range="0 0.04" damping="0.3" stiffness="8"/>
                 <geom name="blender-switch-geom" type="box" size="0.055 0.025 0.04" mass="0.04" rgba="0.65 0.65 0.62 1"/>
               </body>''',
            _geom("cup", "cylinder", "0.075 0.11", "0.8 -4.34 1.64", "0.75 0.75 0.72 1", body=True, mass=0.16),
        ]
        for item in manifest["objects"]:
            pos = " ".join(str(value) for value in item["positionM"])
            dynamic.append(_geom(item["id"], "sphere", str(item["radiusM"]), pos, item["rgba"], body=True, mass=item["massKg"]))
    else:
        fixed.append(_geom("input-table", "box", "2.8 0.8 0.45", "-0.7 0 0.45", "0.32 0.33 0.34 1"))
        for carrier, bay in manifest["truckBays"].items():
            x, y, z = bay["centerM"]
            # Open-front physical bay: a parcel can actually enter it, and the
            # manifest bounds match the collision shell used by evaluation.
            fixed += [
                _geom(f"truck-{carrier}-floor", "box", "1.2 0.85 0.05", f"{x} {y} 0.05", "0.28 0.33 0.31 1"),
                _geom(f"truck-{carrier}-left", "box", "0.05 0.85 0.60", f"{x - 1.15} {y} 0.65", "0.28 0.33 0.31 1"),
                _geom(f"truck-{carrier}-right", "box", "0.05 0.85 0.60", f"{x + 1.15} {y} 0.65", "0.28 0.33 0.31 1"),
                _geom(f"truck-{carrier}-back", "box", "1.2 0.05 0.60", f"{x} {y - 0.80} 0.65", "0.28 0.33 0.31 1"),
            ]
        for item in manifest["objects"]:
            pos = " ".join(str(value) for value in item["positionM"])
            size = " ".join(str(value) for value in item["halfSizeM"])
            dynamic.append(_geom(item["id"], "box", size, pos, "0.52 0.42 0.30 1", body=True, mass=item["massKg"]))
    return f'''<mujoco model="{escape(manifest['scenarioId'])}">
  <compiler angle="radian" inertiafromgeom="true"/>
  <option timestep="0.002" gravity="0 0 -9.81" integrator="implicitfast"/>
  <worldbody>
    <light pos="2 2 7" dir="-0.2 -0.2 -1"/>
    {''.join(fixed)}
    {''.join(dynamic)}
  </worldbody>
</mujoco>'''


async def _stage(job_id: str, name: str, status: str, detail: str) -> None:
    async with SessionLocal() as db:
        row = await db.get(Job, job_id)
        if row is None:
            return
        stages = list(row.detail.get("stages", []))
        stages.append({"name": name, "status": status, "detail": detail, "at": datetime.now(timezone.utc).isoformat()})
        row.detail = {**row.detail, "stages": stages}
        await db.commit()
    events.publish("ok" if status == "passed" else "info", name, detail, jobId=job_id)


async def run(job_id: str, scenario_id: str, requested_seed: int | None = None) -> dict[str, Any]:
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        raise KeyError("Acceptance scenario not found")
    seed = requested_seed if requested_seed is not None else secrets.randbelow(2**31 - 1)
    rng = np.random.default_rng(seed)
    manifest = _kitchen_manifest(rng, seed) if scenario_id == "kitchen-juice" else _factory_manifest(rng, seed)

    renderer = probe_vulkan()
    await _stage(job_id, "Vulkan renderer", "passed", f"{renderer['device']} · {renderer['backend']}")

    xml = _mjcf(manifest)
    model = mujoco.MjModel.from_xml_string(xml)
    joint_names = {mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, index) for index in range(model.njnt)}
    if scenario_id == "kitchen-juice":
        required = {"j_blender_lid", "j_cabinet_door", "j_blender_switch"}
        if not required.issubset(joint_names):
            raise RuntimeError(f"Kitchen articulation gate is missing joints: {sorted(required - joint_names)}")
    free_joint_count = sum(model.jnt_type[index] == mujoco.mjtJoint.mjJNT_FREE for index in range(model.njnt))
    expected_free = len(manifest["objects"]) + (1 if scenario_id == "kitchen-juice" else 0)
    if free_joint_count != expected_free:
        raise RuntimeError(f"Manipulable-body gate expected {expected_free} free joints, found {free_joint_count}.")
    data = mujoco.MjData(model)
    for _ in range(750):
        mujoco.mj_step(model, data)
    if not (np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all() and np.isfinite(data.qacc).all()):
        raise RuntimeError("MuJoCo stability gate produced non-finite state.")
    await _stage(job_id, "MuJoCo compile and stability", "passed", f"{model.nbody} bodies · {model.ngeom} geoms · {model.njnt} joints · 750 steps finite")

    run_dir = DEMOS_DIR / f"{scenario_id}-{job_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    xml_bytes = xml.encode("utf-8")
    manifest["artifacts"] = {"world.mjcf.xml": _sha256(xml_bytes)}
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    (run_dir / "world.mjcf.xml").write_bytes(xml_bytes)
    (run_dir / "manifest.json").write_bytes(manifest_bytes)
    await _stage(job_id, "Evidence persisted", "passed", f"seed {seed} · manifest {_sha256(manifest_bytes)[:16]}…")

    flat = await settings_store.get_flat()
    if not str(flat.get("models.policyEndpoint") or "").strip():
        message = "Environment passed. Learned-policy execution is blocked until a compatible VLA gateway and pinned checkpoint are configured. No success was fabricated."
        await _stage(job_id, "Learned-policy gate", "blocked", message)
        return {
            "outcome": "awaiting_policy",
            "taskSuccess": None,
            "reason": "policy_not_configured",
            "message": message,
            "scenarioId": scenario_id,
            "seed": seed,
            "manifestSha256": _sha256(manifest_bytes),
            "mjcfSha256": _sha256(xml_bytes),
        }

    try:
        config = PolicyConfig.from_settings(flat)
        client = PolicyClient(config)
        try:
            await __import__("asyncio").to_thread(client.probe)
        finally:
            client.close()
    except PolicyError as exc:
        message = f"Environment passed, but the configured learned policy failed compatibility: {exc}"
        await _stage(job_id, "Learned-policy gate", "blocked", message)
        return {"outcome": "awaiting_policy", "taskSuccess": None, "reason": exc.code, "message": message, "scenarioId": scenario_id, "seed": seed}

    message = "The configured policy implements the door-task contract, not the acceptance-scenario action/observation contract. A task-specific adapter is required before execution."
    await _stage(job_id, "Scenario policy adapter", "blocked", message)
    return {"outcome": "awaiting_policy_adapter", "taskSuccess": None, "reason": "scenario_policy_adapter_missing", "message": message, "scenarioId": scenario_id, "seed": seed}
