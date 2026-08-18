"""Catalog queries — compute every frontend payload from the live database.

No fabricated numbers: rates/trends/deltas come from recorded evaluations,
training runs, asset rows and telemetry. Empty DB yields honest zero states.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    AgentDecision,
    Artifact,
    Asset,
    CompileStage,
    Evaluation,
    Job,
    RepairEvent,
    Scenario,
    ScenarioFamily,
    Skill,
    Source,
    TrainingRun,
    Variant,
    World,
)
from ..util import fmt_duration, fmt_size, rel_time


# ---------- skills ----------------------------------------------------------

async def _skill_metrics(session: AsyncSession, skill_id: str) -> dict[str, Any]:
    evals = (
        (await session.execute(select(Evaluation).where(Evaluation.skill_id == skill_id).order_by(Evaluation.created_at))).scalars().all()
    )
    fams = (await session.execute(select(ScenarioFamily).where(ScenarioFamily.skill_id == skill_id))).scalars().all()
    fam_success: dict[str, list[bool]] = {f.family: [] for f in fams}
    fam_count = {f.family: 0 for f in fams}
    for e in evals:
        fam = next((f for f in fams if f.id == e.family_id), None)
        if fam:
            fam_success[fam.family].append(e.success)
    n_eval = len(evals)
    success = 100.0 * sum(1 for e in evals if e.success) / n_eval if n_eval else 0.0
    # delta = last 25% of runs vs first 25%
    q = max(1, n_eval // 4)
    if n_eval >= 4:
        first = sum(1 for e in evals[:q] if e.success) / q
        last = sum(1 for e in evals[-q:] if e.success) / q
        delta = round(100 * (last - first), 1)
    else:
        delta = 0.0
    # coverage: families with >=1 success / total families, blended with scenario count
    covered = sum(1 for f in fams if any(fam_success[f.family]))
    coverage = 100.0 * covered / len(fams) if fams else 0.0
    avg_col = sum(e.collisions for e in evals) / n_eval if n_eval else 0.0
    return {
        "success": round(success, 1),
        "successDelta": delta,
        "coverage": round(coverage, 1),
        "avgCollisions": round(avg_col, 2),
        "n_eval": n_eval,
        "fams": fams,
        "fam_success": fam_success,
        "evals": evals,
    }


async def skill_summary(session: AsyncSession, skill: Skill) -> dict:
    m = await _skill_metrics(session, skill.id)
    last_train = (
        await session.execute(
            select(TrainingRun.created_at).where(TrainingRun.skill_id == skill.id).order_by(TrainingRun.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    status = (
        "not_started"
        if m["n_eval"] == 0
        else "ready"
        if m["success"] >= skill.target
        else "weak"
        if m["success"] < 40
        else "improving"
        if m["successDelta"] > 0
        else "improving"
    )
    return {
        "id": skill.id,
        "name": skill.name,
        "category": skill.category,
        "description": skill.description,
        "success": m["success"],
        "successDelta": m["successDelta"],
        "coverage": m["coverage"],
        "lastTrained": rel_time(last_train),
        "status": status,
        "icon": skill.icon,
    }


async def skill_detail(session: AsyncSession, skill: Skill) -> dict:
    base = await skill_summary(session, skill)
    m = await _skill_metrics(session, skill.id)
    evals = m["evals"]

    # weaknesses: failure-mode histogram with example counts (real telemetry)
    modes: dict[str, list[Evaluation]] = {}
    for e in evals:
        if not e.success and e.failure_mode:
            modes.setdefault(e.failure_mode, []).append(e)
    total_fail = sum(len(v) for v in modes.values()) or 1
    details = {
        "no_contact": "Gripper never reaches the handle — approach or workspace failure",
        "no_grasp": "Handle contacted but the pinch never locks",
        "insufficient_pull": "Grasped but hinge resistance beats actuator authority",
        "drop_early": "Door opened then fell back before settling",
        "path_blocked": "No collision-free path to the handle exists",
        "plan_infeasible": "Handle geometry out of the arm's workspace",
        "collision": "Excessive unintended contact during the episode",
        "timeout": "Episode ended before the door reached 60°",
    }
    weaknesses = [
        {
            "mode": mode,
            "detail": details.get(mode, "Unclassified failure"),
            "contribution": round(100.0 * len(v) / total_fail, 1),
            "examples": len(v),
        }
        for mode, v in sorted(modes.items(), key=lambda kv: -len(kv[1]))
    ]

    families = []
    for f in m["fams"]:
        scens = (await session.execute(select(func.count(Scenario.id)).where(Scenario.family_id == f.id))).scalar() or 0
        fam_evals = [e for e in evals if e.family_id == f.id]
        fsucc = 100.0 * sum(1 for e in fam_evals if e.success) / len(fam_evals) if fam_evals else 0.0
        families.append(
            {
                "id": f.id,
                "family": f.family.replace("_", " ").title(),
                "count": scens,
                "success": round(fsucc, 1),
                "coverage": round(100.0 * min(1, len(fam_evals) / max(scens, 1)), 0),
                "source": f.source,
                "status": "healthy" if fsucc >= 70 else "at_risk" if fsucc >= 40 else "needs_data" if not fam_evals else "needs_attention",
                "updated": rel_time(f.created_at),
            }
        )

    # curriculum plan: weakest families first
    weak_sorted = sorted(families, key=lambda x: x["success"])
    curriculum = [
        {
            "rank": i + 1,
            "name": f"Target {f['family']}",
            "desc": f"Success {f['success']:.0f}% over {f['count']} scenarios — generate variants and retrain",
            "impact": "high" if f["success"] < 40 else "medium" if f["success"] < 70 else "low",
            "scenarios": f["count"],
        }
        for i, f in enumerate(weak_sorted[:3])
    ]

    # trends: success% per evaluation batch of 5
    def trend(vals: list[float]) -> list[float]:
        out = []
        for i in range(0, len(vals), 5):
            chunk = vals[i : i + 5]
            if chunk:
                out.append(round(100 * sum(chunk) / len(chunk), 1))
        return out

    succ_series = [1.0 if e.success else 0.0 for e in evals]
    col_series = [float(e.collisions) for e in evals]
    cov_series = []
    seen_fams = set()
    for e in evals:
        if e.family_id:
            seen_fams.add(e.family_id)
        cov_series.append(100.0 * len(seen_fams) / max(len(m["fams"]), 1))

    return {
        **base,
        "target": skill.target,
        "avgCollisions": m["avgCollisions"],
        "collisionsDelta": 0.0,
        "lastGain": "—",
        "scenarioCount": f"{sum(f['count'] for f in families)} across {len(families)} families",
        "weaknesses": weaknesses,
        "families": families,
        "curriculum": curriculum,
        "beforeAfter": {"before": [], "after": [], "labels": []},
        "successTrend": trend(succ_series),
        "coverageTrend": [round(x, 1) for x in cov_series][::5],
        "collisionTrend": [round(sum(chunk) / len(chunk), 1) for chunk in _chunks(col_series, 5)],
        "promoted": skill.promoted,
    }


def _chunks(xs: list, n: int):
    for i in range(0, len(xs), n):
        yield xs[i : i + n]


# ---------- assets ----------------------------------------------------------

async def asset_out(session: AsyncSession, a: Asset) -> dict:
    artifacts = (await session.execute(select(Artifact).where(Artifact.asset_id == a.id).order_by(Artifact.id))).scalars().all()
    stages = (
        await session.execute(select(CompileStage).where(CompileStage.asset_id == a.id).order_by(CompileStage.idx))
    ).scalars().all()
    readiness = round(0.4 * a.physics_validity + 0.3 * a.scale_confidence + 0.3 * a.articulation, 1)
    return {
        "id": a.id,
        "name": a.name,
        "kind": a.kind,
        "status": a.status,
        "readiness": readiness,
        "physicsValidity": a.physics_validity,
        "scaleConfidence": a.scale_confidence,
        "articulation": a.articulation,
        "lastEval": rel_time(a.last_eval_at),
        "lastEvalResult": a.last_eval_result,
        "source": a.source,
        "parts": a.parts,
        "artifacts": [
            {"type": ar.type, "file": ar.file, "size": fmt_size(ar.size_bytes), "generated": rel_time(ar.created_at)}
            for ar in artifacts
        ],
        "compile": [{"name": s.name, "duration": fmt_duration(s.duration_s), "status": s.status} for s in stages],
        "properties": a.properties,
        "tags": a.tags,
    }


# ---------- sources ----------------------------------------------------------

async def source_out(s: Source) -> dict:
    return {
        "id": s.id,
        "domain": s.domain,
        "category": s.category,
        "collector": s.collector or "—",
        "items": s.items,
        "completeness": s.completeness,
        "lastRun": rel_time(s.last_run_at),
        "health": s.health,
        "brand": s.brand,
    }


async def source_detail(session: AsyncSession, s: Source) -> dict:
    repairs = (
        await session.execute(select(RepairEvent).where(RepairEvent.source_id == s.id).order_by(RepairEvent.created_at))
    ).scalars().all()
    det = s.detail or {}
    return {
        "product": det.get("product", s.query or s.domain),
        "model": det.get("model", "—"),
        "imageSeed": det.get("imageSeed", 1),
        "specs": det.get("specs", []),
        "provenance": det.get("provenance", []),
        "photos": det.get("photos", []),
        "repairs": [{"time": r.time, "title": r.title, "desc": r.desc, "kind": r.kind} for r in repairs],
    }


# ---------- training ----------------------------------------------------------

async def training_run_out(r: TrainingRun) -> dict:
    return {
        "id": r.id,
        "runId": r.id,
        "name": r.name,
        "policy": r.policy,
        "worlds": r.worlds,
        "duration": fmt_duration(r.duration_s),
        "delta": r.delta_pp or 0.0,
        "status": r.status,
        "when": rel_time(r.created_at),
    }


# ---------- observability helpers ---------------------------------------------

TRACE_ICONS = {
    "robot.evaluate": ("target", "#4C8DFF"),
    "failure.analyze": ("brain", "#B46AFF"),
    "brightdata.search": ("search", "#2FBF8F"),
    "brightdata.scrape": ("globe", "#2FBF8F"),
    "brightdata.lens": ("eye", "#2FBF8F"),
    "asset.generate": ("box", "#FF9F4C"),
    "usd.compile": ("layers", "#4C8DFF"),
    "simulation.run": ("cpu", "#59C2FF"),
    "training.run": ("flask", "#FF6B81"),
    "agent.plan": ("brain", "#B46AFF"),
    "port.query": ("book", "#8A94A6"),
}
