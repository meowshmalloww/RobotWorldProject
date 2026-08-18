"""RobotWorld FastAPI application and complete renderer API contract."""
from __future__ import annotations

import asyncio
import logging
import math
import os
import shutil
import statistics
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import mujoco
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select

from . import __version__
from .bootstrap import seed_definitions
from .config import ASSETS_DIR, BASE_DIR, env
from .db import SessionLocal, init_db
from .models import (
    AgentDecision,
    Artifact,
    Asset,
    CompileStage,
    Evaluation,
    Job,
    LogLine,
    MetricPoint,
    RepairEvent,
    Scenario,
    ScenarioFamily,
    Skill,
    Source,
    Span,
    TrainingRun,
    Variant,
    World,
)
from .telemetry import drain_loop, init_otel, signoz_exporting, span
from .util import fmt_duration, new_id, rel_time
from .services import agent, brightdata, catalog, evaluator, events, live, llm, pipeline, port, settings_store, simcore

log = logging.getLogger(__name__)
STARTED_AT = time.monotonic()
_tasks: set[asyncio.Task] = set()


class AgentRunIn(BaseModel):
    skillId: str = "open-refrigerator"
    episodesPerFamily: int = Field(default=4, ge=1, le=20)


class AssetBuildIn(BaseModel):
    query: str = Field(min_length=2, max_length=240)
    kind: Literal["articulated", "rigid"] = "articulated"
    sourceId: str | None = None
    generator: str = "parametric"
    families: list[str] = []
    manualSpec: dict[str, Any] | None = None


class SourceIn(BaseModel):
    domain: str = Field(min_length=3, max_length=200)
    category: str = Field(default="Product", max_length=80)
    query: str = Field(default="", max_length=300)
    collector: str = Field(default="", max_length=120)

    @field_validator("domain")
    @classmethod
    def clean_domain(cls, value: str) -> str:
        value = value.strip().lower().removeprefix("https://").removeprefix("http://").split("/", 1)[0]
        if "." not in value and value not in {"localhost"}:
            raise ValueError("enter a valid source domain")
        return value


class VariantIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    desc: str = Field(default="", max_length=500)


class SceneIn(BaseModel):
    sceneTree: list[dict[str, Any]]
    variants: list[dict[str, Any]] = []


class KeyIn(BaseModel):
    key: str = Field(min_length=1, max_length=8000)


class TrainingIn(BaseModel):
    policy: str = Field(default="bc-mlp-v1", max_length=120)
    curriculum: str = Field(default="agent recommended", max_length=200)
    worlds: int = Field(default=12, ge=1, le=10000)
    iterations: int = Field(default=40, ge=1, le=10000)
    skillId: str = "open-refrigerator"


async def _integration_config() -> dict[str, Any]:
    flat = await settings_store.get_flat()
    model_base = str(flat.get("models.openaiBaseUrl") or "").lower()
    local_model = model_base.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))
    return {
        "port": bool(
            flat.get("integrations.port.enabled")
            and (
                flat.get("integrations.port.token")
                or (flat.get("integrations.port.clientId") and flat.get("integrations.port.clientSecret"))
            )
        ),
        "brightdata": bool(flat.get("integrations.brightdata.enabled") and flat.get("integrations.brightdata.apiKey")),
        "signoz": bool(flat.get("integrations.signoz.enabled") and flat.get("integrations.signoz.ingestionKey")),
        "signozQuery": bool(flat.get("integrations.signoz.queryEndpoint") and flat.get("integrations.signoz.apiKey")),
        "model": bool(flat.get("models.openaiKey") or local_model),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_definitions()
    flat = await settings_store.get_flat()
    init_otel(
        str(flat.get("integrations.signoz.endpoint") or "") if flat.get("integrations.signoz.enabled") else None,
        str(flat.get("integrations.signoz.ingestionKey") or "") if flat.get("integrations.signoz.enabled") else None,
    )
    stop = asyncio.Event()
    drain_task = asyncio.create_task(drain_loop(stop), name="telemetry-drain")
    app.state.telemetry_stop = stop
    log.info("RobotWorld API %s started on %s:%s", __version__, env.host, env.port)
    try:
        yield
    finally:
        for task in tuple(_tasks):
            task.cancel()
        if _tasks:
            await asyncio.gather(*_tasks, return_exceptions=True)
        stop.set()
        await drain_task


app = FastAPI(
    title="RobotWorld API",
    version=__version__,
    description="Local-first physical-AI world construction, validation, and curriculum API.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def request_telemetry(request, call_next):
    started = time.perf_counter()
    with span("http.request", method=request.method, route=request.url.path) as current:
        try:
            response = await call_next(request)
        except Exception:
            current.set_attribute("http.response.status_code", 500)
            raise
        current.set_attribute("http.response.status_code", response.status_code)
        current.set_attribute("http.server.duration_ms", (time.perf_counter() - started) * 1000)
        return response


async def _health() -> dict[str, Any]:
    configured = await _integration_config()
    provider = llm.status()
    try:
        async with SessionLocal() as session:
            await session.execute(select(func.count(Skill.id)))
        database = "healthy"
    except Exception:
        log.exception("database health check failed")
        database = "failed"
    return {
        "status": "healthy" if database == "healthy" else "degraded",
        "version": __version__,
        "uptimeS": round(time.monotonic() - STARTED_AT, 1),
        "database": database,
        "simulation": {"engine": "MuJoCo", "version": mujoco.__version__, "timestepHz": 500},
        "signoz": "exporting" if signoz_exporting() else "not_configured" if not configured["signoz"] else "restart_required",
        "brightdata": "configured" if configured["brightdata"] else "not_configured",
        "port": "configured" if configured["port"] else "not_configured",
        "openai": provider["status"] if configured["model"] else "not_configured",
        "modelProvider": provider,
    }


@app.get("/health", include_in_schema=False)
@app.get("/api/health")
async def health():
    return await _health()


LOOP_STAGES = [
    {"icon": "gauge", "title": "Evaluate robot", "desc": "Run MuJoCo episodes over persisted physical scenarios"},
    {"icon": "search", "title": "Diagnose weakness", "desc": "Aggregate real evaluation failure modes"},
    {"icon": "sources", "title": "Query sources", "desc": "Retrieve product specifications and provenance"},
    {"icon": "cube", "title": "Compile asset", "desc": "Generate GLB, MJCF, and validated OpenUSD"},
    {"icon": "scale", "title": "Validate physics", "desc": "Load and test articulation in MuJoCo"},
    {"icon": "training", "title": "Train or test", "desc": "Adapt the policy from successful trajectories"},
    {"icon": "refresh", "title": "Re-evaluate", "desc": "Persist measured improvement and repeat"},
]


@app.get("/api/overview")
async def overview():
    async with SessionLocal() as session:
        skills = (await session.execute(select(Skill).order_by(Skill.name))).scalars().all()
        skill_rows = [await catalog.skill_summary(session, row) for row in skills]
        assets = (await session.execute(select(Asset).order_by(Asset.created_at.desc()))).scalars().all()
        sources = (await session.execute(select(Source).order_by(Source.created_at.desc()))).scalars().all()
        runs = (await session.execute(select(TrainingRun).order_by(TrainingRun.created_at.desc()))).scalars().all()
        jobs = (await session.execute(select(Job).order_by(Job.updated_at.desc()).limit(8))).scalars().all()
        evals = (await session.execute(select(Evaluation).order_by(Evaluation.created_at))).scalars().all()

    total_evals = len(evals)
    pass_rate = 100 * sum(e.success for e in evals) / total_evals if total_evals else 0.0
    ready = [a for a in assets if a.status == "ready"]
    blocked = [a for a in assets if a.status == "blocked"]
    stats = [
        {"label": "Skills", "value": str(len(skills)), "icon": "robot", "tint": "blue", "foot": "configured definitions"},
        {"label": "Compiled assets", "value": str(len(assets)), "icon": "cube", "tint": "purple", "foot": f"{len(ready)} validated"},
        {"label": "Evaluation pass rate", "value": f"{pass_rate:.1f}%", "icon": "shield", "tint": "green", "foot": f"{total_evals} recorded episodes", "donut": pass_rate / 100},
        {"label": "Configured sources", "value": str(len(sources)), "icon": "sources", "tint": "teal", "foot": f"{sum(s.items for s in sources)} extracted records"},
        {"label": "Training runs", "value": str(len(runs)), "icon": "training", "tint": "blue", "foot": "persisted checkpoints"},
        {"label": "Active jobs", "value": str(sum(j.status in {"pending", "running"} for j in jobs)), "icon": "clock", "tint": "amber", "foot": "background pipeline work"},
    ]
    gaps = []
    for row in sorted(skill_rows, key=lambda item: (item["success"], item["coverage"])):
        gaps.append({"icon": row["icon"], "name": row["name"], "family": row["category"], "success": row["success"], "coverage": row["coverage"]})
    activity = [
        {
            "pipeline": str(job.detail.get("name") or job.kind),
            "icon": "cube" if "asset" in job.kind else "training",
            "stage": str(job.detail.get("stage") or job.kind.replace("_", " ").title()),
            "stageIcon": "refresh",
            "status": "in_progress" if job.status == "running" else "completed" if job.status == "success" else job.status,
            "started": rel_time(job.created_at),
            "duration": rel_time(job.updated_at),
        }
        for job in jobs
    ]
    configured = await _integration_config()
    integrations = [
        {"key": "port", "name": "Port", "desc": "Catalog and lifecycle control", "status": "Configured" if configured["port"] else "Setup required", "meta": "local results stay durable"},
        {"key": "brightdata", "name": "Bright Data", "desc": "Real-world source collection", "status": "Configured" if configured["brightdata"] else "Setup required", "meta": "no synthetic source fallback"},
        {"key": "signoz", "name": "SigNoz", "desc": "OpenTelemetry export and queries", "status": "Exporting" if signoz_exporting() else "Local mirror", "meta": "SQLite telemetry active"},
    ]
    source_top = sorted(sources, key=lambda item: item.items, reverse=True)[:4]
    return {
        "stats": stats,
        "loopStages": LOOP_STAGES,
        "skillGaps": gaps,
        "pipelineActivity": activity,
        "sourceSummary": {
            "objectsFound": str(sum(s.items for s in sources)),
            "objectsDelta": "0",
            "completeness": f"{statistics.fmean([s.completeness for s in sources]):.1f}%" if sources else "0.0%",
            "completenessDelta": "0.0pp",
            "top": [{"name": s.domain, "objects": str(s.items), "completeness": s.completeness} for s in source_top],
        },
        "readiness": {
            "promoted": len(ready),
            "promotedDelta": "0",
            "blocked": len(blocked),
            "blockedDelta": "0",
            "recent": [{"name": a.name, "status": "promoted" if a.status == "ready" else "blocked"} for a in (ready + blocked)[:5]],
        },
        "integrations": integrations,
    }


@app.get("/api/skills")
async def skills_list():
    async with SessionLocal() as session:
        rows = (await session.execute(select(Skill).order_by(Skill.name))).scalars().all()
        skills = [await catalog.skill_summary(session, row) for row in rows]
        details = [await catalog.skill_detail(session, row) for row in rows]
    avg_success = statistics.fmean([row["success"] for row in skills]) if skills else 0.0
    avg_coverage = statistics.fmean([row["coverage"] for row in skills]) if skills else 0.0
    total_scenarios = sum(int(detail["scenarioCount"].split(" ", 1)[0]) for detail in details)
    recommended = []
    for detail in details:
        for item in detail["curriculum"]:
            recommended.append({"rank": 0, "name": item["name"], "impact": item["impact"], "gaps": item["scenarios"]})
    recommended = sorted(recommended, key=lambda item: {"high": 0, "medium": 1, "low": 2}[item["impact"]])[:4]
    for idx, item in enumerate(recommended):
        item["rank"] = idx + 1
    first = details[0] if details else None
    families = first["families"] if first else []
    coverage_dims = [
        {"dimension": row["family"], "coverage": row["coverage"], "gaps": max(row["count"] - round(row["coverage"] * row["count"] / 100), 0), "bands": [row["coverage"]] * 4}
        for row in families
    ]
    return {
        "skills": skills,
        "band": [
            {"label": "Total skills", "value": str(len(skills)), "foot": "configured", "icon": "skills", "tint": "blue"},
            {"label": "Avg success rate", "value": f"{avg_success:.1f}%", "foot": f"{sum(row['status'] == 'ready' for row in skills)} at target", "icon": "gauge", "tint": "green"},
            {"label": "Avg coverage", "value": f"{avg_coverage:.1f}%", "foot": "measured families", "icon": "grid", "tint": "teal"},
            {"label": "Scenario definitions", "value": str(total_scenarios), "foot": "persisted", "icon": "worlds", "tint": "purple"},
            {"label": "Weak skills", "value": str(sum(row["status"] == "weak" for row in skills)), "foot": "needs evaluation", "icon": "warning", "tint": "amber"},
        ],
        "recommended": recommended,
        "relations": {
            "root": {"name": first["name"] if first else "No skill", "status": first["status"] if first else "not_started"},
            "edges": [{"to": row["family"], "status": row["status"], "kind": "scenario family"} for row in families],
        },
        "coverageDims": coverage_dims,
        "curves": {"best": first["successTrend"] if first else [], "baseline": first["coverageTrend"] if first else []},
    }


@app.get("/api/skills/{skill_id}")
async def skill_detail(skill_id: str):
    async with SessionLocal() as session:
        row = await session.get(Skill, skill_id)
        if row is None:
            raise HTTPException(404, "Skill not found")
        return await catalog.skill_detail(session, row)


async def _job_runner(job_id: str, work) -> None:
    async with SessionLocal() as db:
        row = await db.get(Job, job_id)
        if row:
            row.status = "running"
            await db.commit()
    try:
        result = await work
    except Exception as exc:
        log.exception("background job %s failed", job_id)
        async with SessionLocal() as db:
            row = await db.get(Job, job_id)
            if row:
                row.status = "failed"
                row.detail = {**row.detail, "error": str(exc)[:500]}
                await db.commit()
        events.publish("err", "Background job failed", str(exc)[:240], jobId=job_id)
    else:
        async with SessionLocal() as db:
            row = await db.get(Job, job_id)
            if row:
                row.status = "success"
                row.detail = {**row.detail, "result": result}
                await db.commit()


async def _start_job(kind: str, detail: dict[str, Any], work) -> str:
    job_id = new_id("job")
    async with SessionLocal() as db:
        db.add(Job(id=job_id, kind=kind, status="pending", detail=detail))
        await db.commit()
    task = asyncio.create_task(_job_runner(job_id, work), name=job_id)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job_id


@app.post("/api/agent/run", status_code=202)
async def run_agent(payload: AgentRunIn):
    async with SessionLocal() as session:
        if await session.get(Skill, payload.skillId) is None:
            raise HTTPException(404, "Skill not found")
    try:
        job_id = agent.start(payload.skillId, payload.episodesPerFamily)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"jobId": job_id}


@app.post("/api/skills/{skill_id}/generate-worlds", status_code=202)
async def generate_worlds(skill_id: str):
    async def work():
        async with SessionLocal() as session:
            skill = await session.get(Skill, skill_id)
            if skill is None:
                raise KeyError("Skill not found")
            families = await pipeline_world_variants(session, skill_id, 3)
            return {"generated": families}

    job_id = await _start_job("world_generation", {"name": f"{skill_id} scenario expansion"}, work())
    return {"jobId": job_id}


async def pipeline_world_variants(session, skill_id: str, per_family: int) -> int:
    families = await evaluator.ensure_families(session, skill_id)
    rng = __import__("numpy").random.default_rng()
    added = 0
    for family in families:
        for _ in range(per_family):
            params = evaluator.sample_scenario(family.family, skill_id, rng)
            session.add(Scenario(id=new_id("scn"), family_id=family.id, params=params))
            added += 1
    await session.commit()
    events.publish("ok", "Scenario coverage expanded", f"{added} persisted parameter sets", skill=skill_id)
    return added


@app.get("/api/assets")
async def assets_list():
    async with SessionLocal() as session:
        rows = (await session.execute(select(Asset).order_by(Asset.created_at.desc()))).scalars().all()
        assets = [await catalog.asset_out(session, row) for row in rows]
    ready = [row for row in assets if row["status"] == "ready"]
    avg = statistics.fmean([row["readiness"] for row in assets]) if assets else 0.0
    return {
        "assets": assets,
        "stats": [
            {"label": "Assets", "value": str(len(assets)), "icon": "cube", "tint": "blue", "foot": "compiled records"},
            {"label": "Validated", "value": str(len(ready)), "icon": "shield", "tint": "green", "foot": "passed MuJoCo load and rollout"},
            {"label": "Average readiness", "value": f"{avg:.1f}%", "icon": "gauge", "tint": "teal", "foot": "physics + provenance + articulation", "donut": avg / 100},
            {"label": "Blocked", "value": str(sum(row["status"] == "blocked" for row in assets)), "icon": "warning", "tint": "amber", "foot": "inspect compile output"},
        ],
    }


@app.post("/api/assets/build", status_code=202)
async def build_asset(payload: AssetBuildIn):
    if payload.generator != "parametric":
        raise HTTPException(501, "This build supports the validated parametric compiler only; no external mesh generator is being simulated.")
    asset_id = new_id("ast")
    async with SessionLocal() as session:
        session.add(
            Asset(
                id=asset_id,
                name=payload.query.title().strip()[:80],
                kind=payload.kind,
                status="building",
                source=payload.query,
                tags=[payload.query.split()[0].lower()],
            )
        )
        await session.commit()
    job_id = await _start_job(
        "asset_build",
        {"name": payload.query, "assetId": asset_id, "stage": "source and compile"},
        pipeline.build_asset(payload.query, payload.kind, payload.sourceId, payload.manualSpec, asset_id=asset_id),
    )
    return {"assetId": asset_id, "jobId": job_id, "compiler": "parametric-openusd-mujoco"}


@app.get("/api/assets/{asset_id}")
async def asset_detail(asset_id: str):
    async with SessionLocal() as session:
        row = await session.get(Asset, asset_id)
        if row is None:
            raise HTTPException(404, "Asset not found")
        return await catalog.asset_out(session, row)


@app.post("/api/assets/{asset_id}/reevaluate", status_code=202)
async def asset_reevaluate(asset_id: str):
    async def work():
        async with SessionLocal() as db:
            row = await db.get(Asset, asset_id)
            if row is None:
                raise KeyError("Asset not found")
            raw = row.spec or {}
            spec = {key: value.get("value") if isinstance(value, dict) and "value" in value else value for key, value in raw.items()}
        scenario = {
            "door_mass": float(spec.get("door_mass_kg", 12.0)),
            "hinge_friction": float(spec.get("hinge_friction", 2.5)),
            "handle_height": float(spec.get("handle_height_m", 1.05)),
            "handle_orientation": "vertical",
            "max_open_deg": float(spec.get("max_open_deg", 110.0)),
            "robot_base": (0.68, 1.05),
        }
        result = await asyncio.to_thread(simcore.run_rollout, simcore.World(scenario), simcore.ScriptedController, record=False)
        async with SessionLocal() as db:
            row = await db.get(Asset, asset_id)
            if row:
                row.last_eval_result = "passed" if result.success else "failed"
                row.last_eval_at = datetime.now(timezone.utc)
                row.physics_validity = 100.0 if result.success else 65.0 if result.door_peak_deg > 15 else 35.0
                row.status = "ready" if result.success else "testing"
                await db.commit()
        return {"success": result.success, "doorAngleDeg": result.door_angle_deg}

    job_id = await _start_job("asset_reevaluation", {"name": asset_id}, work())
    return {"jobId": job_id}


@app.get("/api/assets/{asset_id}/files/{filename}")
async def asset_file(asset_id: str, filename: str):
    if filename not in {"model.glb", "asset.usda", "spec.json"}:
        raise HTTPException(404, "Artifact not found")
    path = (ASSETS_DIR / asset_id / filename).resolve()
    if path.parent != (ASSETS_DIR / asset_id).resolve() or not path.is_file():
        raise HTTPException(404, "Artifact not found")
    return FileResponse(path, filename=filename)


@app.delete("/api/assets/{asset_id}", status_code=204)
async def asset_delete(asset_id: str):
    async with SessionLocal() as session:
        row = await session.get(Asset, asset_id)
        if row is None:
            raise HTTPException(404, "Asset not found")
        await session.execute(delete(Artifact).where(Artifact.asset_id == asset_id))
        await session.execute(delete(CompileStage).where(CompileStage.asset_id == asset_id))
        await session.delete(row)
        await session.commit()
    target = (ASSETS_DIR / asset_id).resolve()
    if target.parent == ASSETS_DIR.resolve() and target.is_dir():
        shutil.rmtree(target)


def _physics_checks() -> list[dict[str, Any]]:
    try:
        world = simcore.World(simcore.default_scenario_family(__import__("numpy").random.default_rng(7)))
        body_count = int(world.model.nbody)
        joint_count = int(world.model.njnt)
        hinge_range = world.model.jnt_range[world.j["door"]]
        hit, force, others, other = world.contacts()
        return [
            {"check": "MJCF load", "status": "pass", "details": f"MuJoCo {mujoco.__version__} loaded {body_count} bodies and {joint_count} joints", "impacted": str(body_count), "severity": "Info"},
            {"check": "Door joint limits", "status": "pass" if hinge_range[1] > math.radians(60) else "fail", "details": f"Range {math.degrees(hinge_range[0]):.1f}° to {math.degrees(hinge_range[1]):.1f}°", "impacted": "j_door", "severity": "Info" if hinge_range[1] > math.radians(60) else "High"},
            {"check": "Initial contacts", "status": "warn" if others else "pass", "details": f"{others} unintended contacts at reset; handle force {force:.2f} N", "impacted": other or "none", "severity": "Medium" if others else "Info"},
            {"check": "Control timestep", "status": "pass", "details": f"Physics {1 / world.model.opt.timestep:.0f} Hz · control {simcore.CTRL_HZ:.0f} Hz", "impacted": "world", "severity": "Info"},
        ]
    except Exception as exc:
        return [{"check": "MJCF load", "status": "fail", "details": str(exc)[:240], "impacted": "world", "severity": "High"}]


@app.get("/api/worlds/scene")
async def world_scene():
    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        variants = (await session.execute(select(Variant).where(Variant.world_id == world.id).order_by(Variant.created_at))).scalars().all()
        last = (await session.execute(select(Evaluation).order_by(Evaluation.created_at.desc()).limit(1))).scalar_one_or_none()
    return {
        "worldId": world.id,
        "worldName": world.name,
        "sceneTree": world.scene_tree,
        "variants": [{"id": row.id, "name": row.name, "desc": row.desc, "active": row.active} for row in variants],
        "physicsChecks": _physics_checks(),
        "taskSteps": [],
        "successConditions": ([{"name": "Door angle ≥ 60°", "state": "done" if last.success else "failed", "value": f"{last.door_angle_deg:.1f}°"}] if last else []),
        "eventTimeline": [],
    }


@app.put("/api/worlds/scene")
async def save_world(payload: SceneIn):
    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        world.scene_tree = payload.sceneTree
        existing = {row.id: row for row in (await session.execute(select(Variant).where(Variant.world_id == world.id))).scalars().all()}
        for item in payload.variants:
            row = existing.get(str(item.get("id")))
            if row:
                row.name = str(item.get("name", row.name))[:160]
                row.desc = str(item.get("desc", row.desc))[:500]
        await session.commit()
    return {"saved": True}


@app.post("/api/worlds/checks/run")
async def run_checks():
    return {"physicsChecks": await asyncio.to_thread(_physics_checks)}


@app.post("/api/worlds/variants")
async def create_variant(payload: VariantIn):
    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        row = Variant(id=new_id("var"), world_id=world.id, name=payload.name, desc=payload.desc, active=False)
        session.add(row)
        await session.commit()
        return {"id": row.id, "name": row.name, "desc": row.desc, "active": row.active}


@app.post("/api/worlds/variants/{variant_id}/activate")
async def activate_variant(variant_id: str):
    async with SessionLocal() as session:
        row = await session.get(Variant, variant_id)
        if row is None:
            raise HTTPException(404, "Variant not found")
        variants = (await session.execute(select(Variant).where(Variant.world_id == row.world_id))).scalars().all()
        for item in variants:
            item.active = item.id == variant_id
        await session.commit()
    return {"active": variant_id}


@app.get("/api/sources")
async def sources_list():
    async with SessionLocal() as session:
        rows = (await session.execute(select(Source).order_by(Source.created_at.desc()))).scalars().all()
        sources = [await catalog.source_out(row) for row in rows]
        repair_count = (await session.execute(select(func.count(RepairEvent.id)))).scalar() or 0
    completeness = statistics.fmean([row.completeness for row in rows]) if rows else 0.0
    return {
        "sources": sources,
        "stats": [
            {"label": "Sources", "value": str(len(rows)), "icon": "sources", "tint": "blue", "foot": "registered collectors"},
            {"label": "Healthy", "value": str(sum(row.health == "healthy" for row in rows)), "icon": "shield", "tint": "green", "foot": "last run succeeded"},
            {"label": "Repair events", "value": str(repair_count), "icon": "refresh", "tint": "amber", "foot": "persisted lifecycle events"},
            {"label": "Completeness", "value": f"{completeness:.1f}%", "icon": "gauge", "tint": "teal", "foot": "measured extraction fields", "donut": completeness / 100},
            {"label": "Extracted records", "value": str(sum(row.items for row in rows)), "icon": "database", "tint": "purple", "foot": "collector results"},
        ],
    }


@app.post("/api/sources", status_code=201)
async def add_source(payload: SourceIn):
    row = Source(
        id=new_id("src"),
        domain=payload.domain,
        category=payload.category,
        collector=payload.collector,
        query=payload.query or payload.domain,
        health="degraded",
        brand="generic",
    )
    async with SessionLocal() as session:
        session.add(row)
        await session.commit()
        return await catalog.source_out(row)


@app.get("/api/sources/{source_id}")
async def source_detail(source_id: str):
    async with SessionLocal() as session:
        row = await session.get(Source, source_id)
        if row is None:
            raise HTTPException(404, "Source not found")
        return await catalog.source_detail(session, row)


@app.post("/api/sources/{source_id}/run", status_code=202)
async def run_source(source_id: str):
    async def work():
        async with SessionLocal() as db:
            row = await db.get(Source, source_id)
            if row is None:
                raise KeyError("Source not found")
            collector, domain, query = row.collector, row.domain, row.query
        if collector:
            results = await brightdata.dca_run_and_wait(collector, [{"url": f"https://{domain}"}])
        else:
            results = [await brightdata.google_search(query or domain)]
        count = sum(len(item) if isinstance(item, list) else 1 for item in results)
        async with SessionLocal() as db:
            row = await db.get(Source, source_id)
            if row:
                row.items = count
                row.completeness = 100.0 if count else 0.0
                row.health = "healthy" if count else "degraded"
                row.last_run_at = datetime.now(timezone.utc)
                row.detail = {"product": row.query or row.domain, "model": "—", "specs": [], "provenance": [["Source", row.domain]], "photos": []}
                await db.commit()
        return {"items": count}

    async with SessionLocal() as session:
        if await session.get(Source, source_id) is None:
            raise HTTPException(404, "Source not found")
    job_id = await _start_job("source_collection", {"name": source_id}, work())
    return {"jobId": job_id}


@app.get("/api/training")
async def training_data():
    async with SessionLocal() as session:
        runs = (await session.execute(select(TrainingRun).order_by(TrainingRun.created_at.desc()))).scalars().all()
        decisions = (await session.execute(select(AgentDecision).order_by(AgentDecision.created_at.desc()).limit(1))).scalars().all()
        evals = (await session.execute(select(Evaluation).order_by(Evaluation.created_at))).scalars().all()
    successes = [100.0 if row.success else 0.0 for row in evals]
    collisions = [float(row.collisions) for row in evals]
    best_run = max(runs, key=lambda row: row.success_after or 0.0, default=None)
    avg_delta = statistics.fmean([row.delta_pp for row in runs if row.delta_pp is not None]) if any(row.delta_pp is not None for row in runs) else 0.0
    stats = [
        {"label": "Active runs", "value": str(sum(row.status in {"pending", "in_progress"} for row in runs)), "icon": "play", "tint": "blue", "foot": "persisted jobs"},
        {"label": "Best policy", "value": best_run.policy if best_run else "—", "icon": "trophy", "tint": "amber", "foot": f"{best_run.success_after:.1f}% measured success" if best_run and best_run.success_after is not None else "no completed adaptation"},
        {"label": "Average improvement", "value": f"{avg_delta:+.1f}pp", "icon": "training", "tint": "green", "foot": "completed runs"},
        {"label": "Evaluation success", "value": f"{statistics.fmean(successes):.1f}%" if successes else "0.0%", "icon": "gauge", "tint": "green", "foot": f"{len(evals)} episodes", "donut": statistics.fmean(successes) / 100 if successes else 0},
        {"label": "Current target", "value": "Open Refrigerator", "icon": "target", "tint": "purple", "foot": "configured canonical skill"},
    ]
    decision = decisions[0] if decisions else None
    comparison = []
    if runs:
        latest = runs[0]
        comparison = [{"task": latest.name, "icon": "fridge", "baseline": latest.success_before or 0.0, "candidate": latest.success_after or 0.0}]
    return {
        "stats": stats,
        "runs": [await catalog.training_run_out(row) for row in runs],
        "evalComparison": comparison,
        "successCurve": {"best": successes, "baseline": successes[:-1]},
        "collisionCurve": {"best": collisions, "baseline": collisions[:-1]},
        "agentDecision": ({"title": decision.title, "decision": decision.decision, "evidence": decision.evidence, "nextStep": decision.next_step, "confidence": decision.confidence} if decision else None),
    }


@app.post("/api/training/runs", status_code=202)
async def queue_training(payload: TrainingIn):
    try:
        job_id = agent.start(payload.skillId, max(1, min(payload.worlds // 4, 12)))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"runId": job_id}


async def _obs_stats() -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        spans = (await session.execute(select(Span).order_by(Span.created_at.desc()).limit(1000))).scalars().all()
        evals = (await session.execute(select(Evaluation).order_by(Evaluation.created_at.desc()).limit(200))).scalars().all()
        repairs = (await session.execute(select(func.count(RepairEvent.id)))).scalar() or 0
    errors = sum(row.status == "error" for row in spans)
    durations = [row.duration_ms for row in spans]
    p95 = sorted(durations)[min(int(len(durations) * 0.95), len(durations) - 1)] if durations else 0.0
    sim_health = 100 * sum(row.success for row in evals) / len(evals) if evals else 0.0
    return [
        {"label": "Recorded spans", "value": str(len(spans)), "icon": "observability", "tint": "blue", "foot": "local OpenTelemetry mirror"},
        {"label": "Error rate", "value": f"{100 * errors / len(spans):.2f}%" if spans else "0.00%", "icon": "warning", "tint": "red", "foot": f"{errors} error spans"},
        {"label": "Span latency p95", "value": fmt_duration(p95 / 1000), "icon": "clock", "tint": "amber", "foot": "latest 1,000 spans"},
        {"label": "Evaluation health", "value": f"{sim_health:.1f}%", "icon": "shield", "tint": "green", "foot": f"{len(evals)} episodes", "donut": sim_health / 100},
        {"label": "Repair events", "value": str(repairs), "icon": "refresh", "tint": "teal", "foot": "source lifecycle"},
    ]


@app.get("/api/observability/stats")
async def observability_stats():
    return await _obs_stats()


@app.get("/api/observability/services")
async def observability_services():
    configured = await _integration_config()
    provider = llm.status()
    uptime = fmt_duration(time.monotonic() - STARTED_AT)
    return [
        {"name": "robotworld-api", "kind": "core", "status": "running", "version": __version__, "latency": "local", "uptime": uptime, "restarts": 0},
        {"name": "curriculum-agent", "kind": "agent", "status": "running" if agent.status()["running"] else "stopped", "version": __version__, "latency": "—", "uptime": uptime, "restarts": 0},
        {"name": "mujoco-worker", "kind": "worker", "status": "running", "version": mujoco.__version__, "latency": "in-process", "uptime": uptime, "restarts": 0, "gpu": "CPU physics"},
        {"name": "model-provider", "kind": "integration", "status": "running" if provider["status"] == "healthy" else "degraded" if configured["model"] else "stopped", "version": str(provider.get("model") or "not configured"), "latency": "—", "uptime": uptime, "restarts": 0},
        {"name": "brightdata", "kind": "integration", "status": "running" if configured["brightdata"] else "stopped", "version": "REST", "latency": "external", "uptime": uptime, "restarts": 0},
        {"name": "signoz-exporter", "kind": "integration", "status": "running" if signoz_exporting() else "stopped", "version": "OTLP", "latency": "external", "uptime": uptime, "restarts": 0},
        {"name": "port-catalog", "kind": "integration", "status": "running" if configured["port"] else "stopped", "version": "REST", "latency": "external", "uptime": uptime, "restarts": 0},
    ]


async def _trace_groups(limit: int = 50) -> list[tuple[str, list[Span]]]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Span).order_by(Span.created_at.desc()).limit(3000))).scalars().all()
    groups: dict[str, list[Span]] = defaultdict(list)
    for row in rows:
        groups[row.trace_id].append(row)
    return list(groups.items())[:limit]


@app.get("/api/observability/traces")
async def traces_list():
    groups = await _trace_groups()
    traces = []
    for trace_id, rows in groups:
        start = min(row.start_ms for row in rows)
        end = max(row.start_ms + row.duration_ms for row in rows)
        traces.append({"traceId": trace_id, "iterationId": rows[-1].name, "status": "Error" if any(row.status == "error" for row in rows) else "OK", "duration": fmt_duration((end - start) / 1000), "durationMs": end - start, "startTime": datetime.fromtimestamp(start / 1000, timezone.utc).strftime("%H:%M:%S UTC"), "spans": len(rows), "errors": sum(row.status == "error" for row in rows)})
    return {"traces": traces}


@app.get("/api/observability/traces/{trace_id}")
async def trace_detail(trace_id: str):
    async with SessionLocal() as session:
        rows = (await session.execute(select(Span).where(Span.trace_id == trace_id).order_by(Span.start_ms))).scalars().all()
    if not rows:
        raise HTTPException(404, "Trace not found")
    start = min(row.start_ms for row in rows)
    end = max(row.start_ms + row.duration_ms for row in rows)
    meta_row = {"traceId": trace_id, "iterationId": rows[0].name, "status": "Error" if any(row.status == "error" for row in rows) else "OK", "duration": fmt_duration((end - start) / 1000), "durationMs": end - start, "startTime": datetime.fromtimestamp(start / 1000, timezone.utc).strftime("%H:%M:%S UTC"), "spans": len(rows), "errors": sum(row.status == "error" for row in rows)}
    icons = catalog.TRACE_ICONS
    spans_out = []
    for row in rows:
        icon, color = icons.get(row.name, ("workflow", "#8B9098"))
        spans_out.append({"name": row.name, "service": row.service, "startMs": row.start_ms - start, "durationMs": row.duration_ms, "status": row.status, "icon": icon, "color": color})
    insights = []
    failed = [row for row in rows if row.status == "error"]
    if failed:
        insights.append({"icon": "warning", "title": f"{len(failed)} error span(s)", "body": ", ".join(row.name for row in failed[:5])})
    return {"meta": meta_row, "spans": spans_out, "insights": insights}


@app.get("/api/observability/metrics")
async def observability_metrics():
    async with SessionLocal() as session:
        rows = (await session.execute(select(MetricPoint).order_by(MetricPoint.ts_ms.desc()).limit(500))).scalars().all()
    rows.reverse()
    buckets: dict[str, list[MetricPoint]] = defaultdict(list)
    for row in rows:
        buckets[row.name].append(row)
    names = list(buckets)[:4]
    labels = [datetime.fromtimestamp(row.ts_ms / 1000).strftime("%H:%M:%S") for row in rows[-60:]]
    series = {name: [point.value for point in buckets[name][-60:]] for name in names}
    return {"labels": labels, "series": series, "metrics": names, "latency": series.get("http.server.duration_ms", []), "error": series.get("skill.failure", []), "gpu": [], "throughput": series.get("robot.evaluation", [])}


@app.get("/api/observability/logs")
async def observability_logs(level: str | None = Query(default=None)):
    async with SessionLocal() as session:
        stmt = select(LogLine).order_by(LogLine.time_ms.desc()).limit(500)
        if level:
            stmt = select(LogLine).where(LogLine.level == level.upper()).order_by(LogLine.time_ms.desc()).limit(500)
        rows = (await session.execute(stmt)).scalars().all()
    return [{"time": datetime.fromtimestamp(row.time_ms / 1000).strftime("%H:%M:%S.%f")[:-3], "level": row.level, "service": row.service, "message": row.message} for row in rows]


@app.get("/api/observability/alerts")
async def observability_alerts():
    async with SessionLocal() as session:
        failed_sources = (await session.execute(select(Source).where(Source.health.in_(["failed", "degraded"])))).scalars().all()
        failed_assets = (await session.execute(select(Asset).where(Asset.status == "blocked"))).scalars().all()
    alerts = []
    for row in failed_sources:
        alerts.append({"title": f"Source {row.domain} is {row.health}", "severity": "high" if row.health == "failed" else "medium", "service": "Bright Data", "firingFor": rel_time(row.last_run_at or row.created_at), "meta": [["Source", row.domain], ["Completeness", f"{row.completeness:.1f}%"]], "tags": ["source", row.health]})
    for row in failed_assets:
        alerts.append({"title": f"Asset compile blocked: {row.name}", "severity": "high", "service": "asset-pipeline", "firingFor": rel_time(row.created_at), "meta": [["Asset", row.id], ["Last result", row.last_eval_result]], "tags": ["asset", "compile"]})
    return alerts


@app.get("/api/settings")
async def get_settings():
    return await settings_store.get_settings(masked=True)


@app.put("/api/settings/{section}")
async def put_settings(section: str, patch: dict[str, Any]):
    try:
        updated = await settings_store.put_section(section, patch)
    except KeyError as exc:
        raise HTTPException(422, str(exc)) from exc
    return updated


@app.put("/api/settings/keys/{service}")
async def put_key(service: str, payload: KeyIn):
    try:
        await settings_store.put_key(service, payload.key)
    except KeyError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"stored": True}


@app.post("/api/integrations/port/sync")
async def sync_port_catalog():
    async with SessionLocal() as session:
        skills = (await session.execute(select(Skill))).scalars().all()
        assets = (await session.execute(select(Asset))).scalars().all()
    synced = 0
    try:
        for row in skills:
            await port.upsert_entity("robotworldSkill", row.id, row.name, {"category": row.category, "target": row.target, "promoted": row.promoted})
            synced += 1
        for row in assets:
            await port.upsert_entity("robotworldAsset", row.id, row.name, {"kind": row.kind, "status": row.status, "physicsValidity": row.physics_validity, "scaleConfidence": row.scale_confidence})
            synced += 1
    except port.NotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except port.PortError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"synced": synced}


@app.post("/api/eval/sessions", status_code=201)
async def create_eval_session():
    return live.info(live.create())


@app.get("/api/eval/sessions/{session_id}/replay")
async def eval_replay(session_id: str):
    session = live.get(session_id)
    if session is None:
        raise HTTPException(404, "Evaluation session not found")
    return live.replay(session)


@app.websocket("/ws/events")
async def event_socket(websocket: WebSocket):
    await websocket.accept()
    try:
        for event in events.history()[-50:]:
            await websocket.send_text(events.encode(event))
        async for queue in events.subscribe():
            while True:
                await websocket.send_text(events.encode(await queue.get()))
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/live/{session_id}")
async def live_socket(websocket: WebSocket, session_id: str):
    session = live.get(session_id)
    if session is None:
        await websocket.close(code=4404, reason="session not found")
        return
    await websocket.accept()
    runner = asyncio.create_task(live.run(session), name=f"live-{session_id}")

    async def sender():
        while True:
            message = await session.queue.get()
            await websocket.send_json(message)
            if message.get("type") == "end":
                return

    async def receiver():
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "control":
                live.control(session, str(message.get("action")), message.get("value"))

    send_task = asyncio.create_task(sender())
    recv_task = asyncio.create_task(receiver())
    try:
        done, pending = await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*done, return_exceptions=True)
    except WebSocketDisconnect:
        session.stop.set()
    finally:
        if not runner.done() and session.stop.is_set():
            await runner


# Production web/Electron topology: the API serves the built renderer from the
# same loopback origin, eliminating dev-proxy/file:// differences.
FRONTEND_DIST = Path(os.environ.get("ROBOTWORLD_FRONTEND_DIR", BASE_DIR.parent / "frontend" / "dist")).resolve()
if FRONTEND_DIST.is_dir():
    static_dir = FRONTEND_DIST / "assets"
    if static_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir), name="frontend-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        candidate = (FRONTEND_DIST / path).resolve()
        if candidate.is_file() and FRONTEND_DIST in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=env.host, port=env.port, reload=False)
