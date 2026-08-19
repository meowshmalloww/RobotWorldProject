"""Batch evaluation service: runs real MuJoCo rollouts over a skill's scenario
families, stores Evaluation rows, emits telemetry spans/metrics/logs."""
from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
from sqlalchemy import select

from ..db import SessionLocal
from ..models import Evaluation, Scenario, ScenarioFamily, Skill
from ..telemetry import emit_metric, span
from ..util import new_id
from . import simcore

log = logging.getLogger(__name__)

# Scenario families per skill — the curriculum's coverage dimensions. Each
# family generates real randomized scenario parameter sets.
FAMILY_GENERATORS: dict[str, dict[str, dict[str, Any]]] = {
    "open-refrigerator": {
        "nominal": {"door_mass": (9.0, 16.0), "hinge_friction": (1.5, 5.0), "handle_height": (1.0, 1.25), "handle_orientation": "vertical"},
        "heavy_door": {"door_mass": (20.0, 34.0), "hinge_friction": (8.0, 16.0), "handle_height": (1.0, 1.25), "handle_orientation": "vertical"},
        "low_handle": {"door_mass": (9.0, 16.0), "hinge_friction": (1.5, 5.0), "handle_height": (0.82, 0.97), "handle_orientation": "vertical"},
        "horizontal_handle": {"door_mass": (9.0, 16.0), "hinge_friction": (1.5, 5.0), "handle_height": (1.05, 1.3), "handle_orientation": "horizontal"},
    }
}


def sample_scenario(family_name: str, skill_id: str, rng: np.random.Generator) -> dict[str, Any]:
    gen = FAMILY_GENERATORS.get(skill_id, FAMILY_GENERATORS["open-refrigerator"])[family_name]
    return {
        "family": family_name,
        "door_mass": float(rng.uniform(*gen["door_mass"])),
        "hinge_friction": float(rng.uniform(*gen["hinge_friction"])),
        "handle_height": float(rng.uniform(*gen["handle_height"])),
        "handle_orientation": gen["handle_orientation"],
        "max_open_deg": 110.0,
        "robot_base": simcore.robot_base_for_asset(simcore.FRIDGE_SPEC),
    }


async def ensure_families(session, skill_id: str, per_family: int = 6) -> list[ScenarioFamily]:
    """Create the skill's scenario families + scenario instances if missing."""
    existing = (await session.execute(select(ScenarioFamily).where(ScenarioFamily.skill_id == skill_id))).scalars().all()
    if existing:
        return existing
    gens = FAMILY_GENERATORS.get(skill_id, FAMILY_GENERATORS["open-refrigerator"])
    out = []
    for fam_name in gens:
        fam = ScenarioFamily(id=new_id("fam"), skill_id=skill_id, family=fam_name, source="generated", status="in_progress")
        session.add(fam)
        out.append(fam)
        rng = np.random.default_rng(abs(hash((skill_id, fam_name))) % (2**32))
        for _ in range(per_family):
            session.add(Scenario(id=new_id("scn"), family_id=fam.id, params=sample_scenario(fam_name, skill_id, rng)))
    await session.commit()
    return out


async def evaluate_skill(
    skill_id: str,
    *,
    policy: str = "scripted-v1",
    episodes_per_family: int = 4,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Run real rollouts across all families; persist Evaluation rows."""
    async with SessionLocal() as session:
        skill = await session.get(Skill, skill_id)
        if skill is None:
            raise KeyError(f"unknown skill {skill_id}")
        families = await ensure_families(session, skill_id)
        run_id = new_id("run")
        results: list[dict] = []
        with span("robot.evaluate", skill=skill_id, policy=policy, episodes=episodes_per_family * len(families)):
            for fam in families:
                scen_rows = (
                    (await session.execute(select(Scenario).where(Scenario.family_id == fam.id))).scalars().all()
                )[:episodes_per_family]
                for scen in scen_rows:
                    t0 = time.time()
                    with span(
                        "robot.evaluation.episode",
                        skill=skill_id,
                        family=fam.family,
                        scenario=scen.id,
                        policy=policy,
                    ) as episode_span:
                        world = simcore.World(scen.params)
                        r = simcore.run_rollout(world, simcore.ScriptedController)
                        episode_span.set_attribute("robot.success", r.success)
                        episode_span.set_attribute("robot.failure_mode", r.failure_mode or "none")
                        episode_span.set_attribute("robot.door_angle_deg", r.door_angle_deg)
                        episode_span.set_attribute("robot.collisions", r.collisions)
                    ev = Evaluation(
                        id=new_id("ev"),
                        run_id=run_id,
                        skill_id=skill_id,
                        family_id=fam.id,
                        scenario_id=scen.id,
                        policy=policy,
                        success=r.success,
                        door_angle_deg=r.door_angle_deg,
                        collisions=r.collisions,
                        duration_s=r.duration_s,
                        failure_mode=r.failure_mode,
                        failure_detail=r.failure_detail,
                    )
                    session.add(ev)
                    emit_metric("robot.evaluation", 1.0, skill=skill_id, success=str(r.success), policy=policy)
                    if r.success:
                        emit_metric("skill.success", 1.0, skill=skill_id)
                    else:
                        emit_metric("skill.failure", 1.0, skill=skill_id, mode=r.failure_mode or "unknown")
                    results.append(
                        {
                            "family": fam.family,
                            "scenario": scen.id,
                            "success": r.success,
                            "door_deg": round(r.door_angle_deg, 1),
                            "peak_deg": round(r.door_peak_deg, 1),
                            "collisions": r.collisions,
                            "failure_mode": r.failure_mode,
                            "duration_s": round(time.time() - t0, 2),
                        }
                    )
            await session.commit()
        n = len(results)
        succ = sum(1 for r in results if r["success"])
        by_family: dict[str, list[bool]] = {}
        for r in results:
            by_family.setdefault(r["family"], []).append(r["success"])
        summary = {
            "run_id": run_id,
            "skill_id": skill_id,
            "episodes": n,
            "success": succ,
            "success_rate": round(100 * succ / max(n, 1), 1),
            "by_family": {k: round(100 * sum(v) / len(v), 1) for k, v in by_family.items()},
            "by_failure_mode": {},
        }
        for r in results:
            if not r["success"] and r["failure_mode"]:
                summary["by_failure_mode"][r["failure_mode"]] = summary["by_failure_mode"].get(r["failure_mode"], 0) + 1
        log.info("evaluation run %s: %d/%d success (%.1f%%)", run_id, succ, n, summary["success_rate"])
        return summary
