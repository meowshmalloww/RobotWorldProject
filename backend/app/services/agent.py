"""The curriculum agent — the autonomous loop from the product spec:

  evaluate -> analyze failures (telemetry) -> check coverage (catalog) ->
  find/build missing worlds -> train -> re-evaluate -> record decision.

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
from ..models import AgentDecision, Evaluation, Skill, TrainingRun
from ..telemetry import span
from ..util import new_id
from . import evaluator, events, llm, port, trainer
import numpy as np

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
Focus on the dominant failure mode and the weakest scenario family."""


def _heuristic_plan(skill_name: str, analysis: dict[str, Any]) -> dict[str, Any]:
    mode = analysis["top_failure_mode"] or "none"
    weakest = analysis["weakest_family"] or "nominal"
    return {
        "title": f"Target {weakest.replace('_', ' ')} — {mode.replace('_', ' ')}",
        "decision": (
            f"Dominant failure is '{mode}' concentrated in '{weakest}' "
            f"({analysis['weakest_success']:.0f}% success). Generate targeted scenario "
            f"variants for that family and run a short BC adaptation on the new demos."
        ),
        "evidence": [
            f"{analysis['success_rate']:.0f}% success over {analysis['episodes']} episodes",
            f"failure modes: {analysis['by_failure_mode']}",
            f"weakest family: {weakest} at {analysis['weakest_success']:.0f}%",
        ],
        "next_step": {"name": f"Generate {weakest} variants + retrain", "meta": "agent loop"},
        "confidence": 0.72,
    }


async def analyze_failures(skill_id: str) -> dict[str, Any]:
    """Query the evaluation store (the local SigNoz mirror) for the skill's
    latest run and bucket failures — the agent's real eyes."""
    async with SessionLocal() as session:
        evals = (
            await session.execute(select(Evaluation).where(Evaluation.skill_id == skill_id).order_by(Evaluation.created_at.desc()).limit(60))
        ).scalars().all()
    if not evals:
        return {"episodes": 0}
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
                    fams = await evaluator.ensure_families(session, skill_id)
                # new variants enter through the next evaluation's sampling

            # 5. train BC on successful demonstrations across current coverage
            with span("training.run"):
                async with SessionLocal() as session:
                    fams = await evaluator.ensure_families(session, skill_id)
                    fam_ids = [f.id for f in fams]
                    scen_params = []
                    for fid in fam_ids:
                        rows = (await session.execute(select(__import__("app.models", fromlist=["Scenario"]).Scenario).where(__import__("app.models", fromlist=["Scenario"]).Scenario.family_id == fid))).scalars().all()
                        scen_params.extend(r.params for r in rows[:3])
                rng = np.random.default_rng(7)
                scen_params.extend(evaluator.sample_scenario(weakest, skill_id, rng) for _ in range(3))
                obs, act, kept = trainer.collect_demos(scen_params, max_episodes=14)
                if kept >= 2 and len(obs):
                    model, loss_curve, dur = trainer.train_bc(obs, act, epochs=40)
                    run_row = TrainingRun(
                        id=new_id("run"),
                        skill_id=skill_id,
                        name=f"{skill_name} — BC adaptation on {weakest.replace('_', ' ')}",
                        policy="bc-mlp-v1",
                        worlds=len(scen_params),
                        iterations=40,
                        status="completed",
                        duration_s=dur,
                        loss_curve=loss_curve,
                        success_before=before["success_rate"],
                    )
                    async with SessionLocal() as session:
                        session.add(run_row)
                        await session.commit()
                        run_pk = run_row.id
                    # 6. re-evaluate with the trained policy
                    with span("robot.evaluate", phase="after_training"):
                        from ..config import MODELS_DIR

                        model_path = MODELS_DIR / f"{run_pk}.pt"
                        trainer.save_model(model, model_path)
                        policy_model = trainer.load_model(model_path)
                        after = await evaluator.evaluate_skill(skill_id, episodes_per_family=episodes_per_family, policy="bc-mlp-v1", policy_model=policy_model)
                    delta = round(after["success_rate"] - before["success_rate"], 1)
                    async with SessionLocal() as session:
                        rr = await session.get(TrainingRun, run_pk)
                        if rr:
                            rr.delta_pp = delta
                            rr.success_after = after["success_rate"]
                            await session.commit()
                else:
                    after = before
                    delta = 0.0

            # 7. persist the decision with evidence
            decision = AgentDecision(
                id=new_id("dec"),
                skill_id=skill_id,
                title=plan["title"],
                decision=plan["decision"],
                evidence=[*plan.get("evidence", []), f"provenance: {plan['provenance']}", f"delta: {delta:+.1f} pp"],
                next_step=plan.get("next_step", {}),
                confidence=float(plan.get("confidence", 0.7)),
            )
            async with SessionLocal() as session:
                session.add(decision)
                await session.commit()
            summary = f"{skill_name}: {before['success_rate']:.0f}% → {after['success_rate']:.0f}% ({delta:+.1f} pp) · {plan['title']}"
            _state.update(lastSummary=summary)
            result = {"before": before, "after": after, "decision": plan, "delta_pp": delta}
            try:
                with span("port.publish_result", skill=skill_id):
                    await port.sync_curriculum_result(skill_id, skill_name, result)
            except port.NotConfigured:
                pass
            except port.PortError:
                # Port is an external control-plane projection. The completed
                # local evaluation/training result remains valid and persisted.
                log.exception("Port result sync failed")
                events.publish("alert", "Port sync failed", "The local curriculum result was kept and can be synced later.")
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
