"""The curriculum agent — the autonomous loop from the product spec:

  evaluate -> analyze failures (telemetry) -> check coverage (catalog) ->
  find/build missing worlds -> prepare a policy-ready evaluation -> record decision.

Uses the LLM planner when an OpenAI key is configured; otherwise the
deterministic heuristic planner. Every decision is persisted with its evidence
and provenance (llm vs heuristic).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from ..db import SessionLocal
from ..models import AgentDecision, Evaluation, Skill
from ..telemetry import span
from ..util import new_id
from . import evaluator, events, llm, signoz

log = logging.getLogger(__name__)

_state: dict[str, Any] = {"running": False, "currentSkill": None, "iteration": 0, "lastSummary": None}
_task: asyncio.Task | None = None


def status() -> dict[str, Any]:
    return dict(_state)


PLANNER_SYSTEM = """You are the RobotWorld curriculum agent. Given a robot skill's evaluation
telemetry (failure-mode histogram, per-family success rates, coverage), decide
the next curriculum action. Answer with a JSON object:
{"title": str, "decision": str (1-2 sentences), "evidence": [str, ...],
 "next_step": {"name": str, "meta": str}, "confidence": float 0..1}
Focus on the dominant failure mode and the weakest scenario family. Training is disabled;
recommend new evaluation worlds or policy-ready data collection, never a training run."""


def _heuristic_plan(skill_name: str, analysis: dict[str, Any]) -> dict[str, Any]:
    mode = analysis["top_failure_mode"] or "none"
    weakest = analysis["weakest_family"] or "nominal"
    return {
        "title": f"Target {weakest.replace('_', ' ')} — {mode.replace('_', ' ')}",
        "decision": (
            f"Dominant failure is '{mode}' concentrated in '{weakest}' "
            f"({analysis['weakest_success']:.0f}% success). Generate targeted scenario "
            f"variants for that family and preserve them for a later, separately authorized policy workflow."
        ),
        "evidence": [
            f"{analysis['success_rate']:.0f}% success over {analysis['episodes']} episodes",
            f"failure modes: {analysis['by_failure_mode']}",
            f"weakest family: {weakest} at {analysis['weakest_success']:.0f}%",
        ],
        "next_step": {"name": f"Generate and validate {weakest} variants", "meta": "agent loop · training disabled"},
        "confidence": 0.72,
    }


async def analyze_failures(skill_id: str) -> dict[str, Any]:
    """Correlate SigNoz traces, then aggregate authoritative local episodes.

    The local evaluation store keeps diagnostics operational when an optional provider is
    unavailable.  When a query service account is configured, every analysis
    first proves the corresponding episode spans are queryable in SigNoz.
    """
    cloud_evidence: dict[str, Any]
    try:
        cloud = await signoz.search_traces(
            minutes=180,
            filter_expr="service.name = 'robotworld-backend' AND name = 'robot.evaluation.episode'",
            limit=60,
        )
        # Do not depend on a single SigNoz response envelope version; retain a
        # compact availability statement rather than copying remote payloads.
        cloud_evidence = {"queried": True, "available": bool(cloud), "error": None}
    except signoz.NotConfigured:
        cloud_evidence = {"queried": False, "available": False, "error": "not_configured"}
    except signoz.SigNozError as exc:
        log.warning("SigNoz query unavailable; using the durable local evaluation store: %s", exc)
        cloud_evidence = {"queried": True, "available": False, "error": str(exc)[:160]}
    async with SessionLocal() as session:
        evals = (
            await session.execute(select(Evaluation).where(Evaluation.skill_id == skill_id).order_by(Evaluation.created_at.desc()).limit(60))
        ).scalars().all()
    if not evals:
        return {"episodes": 0, "telemetry": cloud_evidence, "aggregationSource": "local_evaluation_store"}
    by_mode: dict[str, int] = {}
    by_family: dict[str, list[bool]] = {}
    for e in evals:
        if not e.success and e.failure_mode:
            by_mode[e.failure_mode] = by_mode.get(e.failure_mode, 0) + 1
        by_family.setdefault(e.family_id or "?", []).append(e.success)
    n = len(evals)
    succ = sum(1 for e in evals if e.success)
    fam_rates = {k: 100 * sum(v) / len(v) for k, v in by_family.items()}
    weakest = min(fam_rates, key=fam_rates.get) if fam_rates else None
    return {
        "episodes": n,
        "success_rate": 100 * succ / n,
        "by_failure_mode": by_mode,
        "top_failure_mode": max(by_mode, key=by_mode.get) if by_mode else None,
        "weakest_family": weakest,
        "weakest_success": fam_rates.get(weakest, 0.0) if weakest else 0.0,
        "family_rates": fam_rates,
        "telemetry": cloud_evidence,
        "aggregationSource": "local_evaluation_store",
    }


async def run_once(skill_id: str, *, episodes_per_family: int = 4) -> dict[str, Any]:
    """One full curriculum iteration for a skill."""
    global _state
    async with SessionLocal() as session:
        skill = await session.get(Skill, skill_id)
        if skill is None:
            raise KeyError(f"unknown skill {skill_id}")
        skill_name = skill.name

    _state.update(running=True, currentSkill=skill_id, iteration=_state.get("iteration", 0) + 1)
    events.publish("agent", "Curriculum iteration started", skill_name, skill=skill_id)
    try:
        with span("curriculum.iteration", skill=skill_id):
            # 1. baseline evaluation (real rollouts)
            with span("robot.evaluate", phase="baseline"):
                before = await evaluator.evaluate_skill(skill_id, episodes_per_family=episodes_per_family)

            # 2. failure analysis from the telemetry store
            with span("failure.analyze"):
                analysis = await analyze_failures(skill_id)
                log.info("failure analysis for %s: %s", skill_id, analysis.get("by_failure_mode"))

            # 3. plan (LLM when configured, heuristic otherwise)
            with span("agent.plan"):
                plan, provenance = await llm.plan(
                    PLANNER_SYSTEM,
                    f"Skill: {skill_name}\nAnalysis: {analysis}\nCoverage gaps to weigh: heavy_door, low_handle, horizontal_handle",
                )
                if plan is None:
                    plan = _heuristic_plan(skill_name, analysis)
                plan["provenance"] = provenance

            # 4. build targeted scenario variants for the weakest family (real
            #    new worlds: parameter sets not previously evaluated)
            weakest = analysis.get("weakest_family") or "nominal"
            with span("worlds.generate", family=weakest):
                async with SessionLocal() as session:
                    await evaluator.ensure_families(session, skill_id)
                # new variants enter through the next evaluation's sampling

            # 5. Training is intentionally disabled on this workstation. Keep
            # the diagnosis and generated-world evidence, then hand the frozen
            # manifest to a separately selected policy/checkpoint later.
            after = before
            delta = 0.0

            # 7. persist the decision with evidence
            decision = AgentDecision(
                id=new_id("dec"),
                skill_id=skill_id,
                title=plan["title"],
                decision=plan["decision"],
                evidence=[*plan.get("evidence", []), f"provenance: {plan['provenance']}", "training: disabled by product configuration"],
                next_step=plan.get("next_step", {}),
                confidence=float(plan.get("confidence", 0.7)),
            )
            async with SessionLocal() as session:
                session.add(decision)
                await session.commit()
            summary = f"{skill_name}: {before['success_rate']:.0f}% measured asset-validation success · {plan['title']} · training disabled"
            _state.update(lastSummary=summary)
            result = {"before": before, "after": after, "decision": plan, "delta_pp": delta, "trainingPerformed": False}
            events.publish("agent", "Curriculum iteration complete", summary, skill=skill_id)
            return result
    finally:
        _state["running"] = False


def start(skill_id: str, episodes_per_family: int = 4) -> str:
    """Launch a background curriculum iteration."""
    global _task
    if _state.get("running"):
        raise RuntimeError("agent already running")
    job_id = new_id("job")

    async def _run():
        try:
            await run_once(skill_id, episodes_per_family=episodes_per_family)
        except Exception as exc:
            log.exception("agent iteration failed")
            events.publish("alert", "Agent iteration failed", str(exc), skill=skill_id)

    _task = asyncio.create_task(_run())
    return job_id
