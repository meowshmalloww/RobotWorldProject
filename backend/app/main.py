"""RobotWorld FastAPI application and complete renderer API contract."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import statistics
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import mujoco
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select

from . import __version__
from .bootstrap import seed_definitions
from .config import ASSETS_DIR, BASE_DIR, WORLDS_DIR, env
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
from .services import agent, asset_evidence, brightdata, catalog, demo_scenarios, evaluator, events, isaac_sim, live, llm, local_vla, model_registry, performance, pipeline, port, robot_registry, settings_store, simcore, trellis, usda, vulkan_renderer, world_geometry
from .services.remote_policy import PolicyClient, PolicyConfig, PolicyError

log = logging.getLogger(__name__)
STARTED_AT = time.monotonic()
_tasks: set[asyncio.Task] = set()
HIDDEN_LEGACY_SKILLS = {"open-refrigerator"}


class AgentRunIn(BaseModel):
    skillId: str = "open-refrigerator"
    episodesPerFamily: int = Field(default=4, ge=1, le=20)


class AssetBuildIn(BaseModel):
    query: str = Field(min_length=2, max_length=240)
    kind: Literal["articulated", "rigid"] = "articulated"
    sourceId: str | None = None
    generator: Literal["parametric", "trellis2"] = "parametric"
    families: list[str] = []
    manualSpec: dict[str, Any] | None = None


class EvidenceAnalysisIn(BaseModel):
    """Intentional action guard: never spend API usage as a build side effect."""

    confirmEvidenceReview: Literal[True]


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

    @field_validator("collector")
    @classmethod
    def clean_collector(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith("c_"):
            raise ValueError("Scraper Studio collector IDs start with 'c_'")
        return value


class SourceRepairIn(BaseModel):
    prompt: str = Field(
        default="Repair the extractor so every row contains model, dimensions, source URL, and at least one product image.",
        min_length=12,
        max_length=1000,
    )


class VariantIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    desc: str = Field(default="", max_length=500)


class SceneIn(BaseModel):
    sceneTree: list[dict[str, Any]]
    variants: list[dict[str, Any]] = []


class PlacementIn(BaseModel):
    translation: tuple[float, float, float] | None = None
    rotationZDeg: float | None = Field(default=None, ge=-36000, le=36000)
    scaleMultiplier: tuple[float, float, float] | None = None
    visible: bool | None = None
    mobility: Literal["movable", "fixed"] | None = None

    @field_validator("translation")
    @classmethod
    def finite_translation(cls, value):
        if value is not None and (any(not math.isfinite(item) for item in value) or any(abs(item) > 1000 for item in value)):
            raise ValueError("translation must contain finite world coordinates within 1000 metres")
        return value

    @field_validator("scaleMultiplier")
    @classmethod
    def finite_scale_multiplier(cls, value):
        if value is not None and (
            any(not math.isfinite(item) for item in value)
            or any(item < 0.02 or item > 100 for item in value)
        ):
            raise ValueError("scaleMultiplier must contain finite values between 0.02 and 100")
        return value


class RobotPatchIn(BaseModel):
    cameraMappings: dict[str, str] | None = None
    policyAdapter: str | None = Field(default=None, max_length=500)


class WorldCommandIn(BaseModel):
    instruction: str = Field(min_length=2, max_length=1000)
    robotId: str | None = None
    mode: Literal["plan", "execute"] = "plan"


class KeyIn(BaseModel):
    key: str = Field(min_length=1, max_length=8000)


class FrontendErrorIn(BaseModel):
    source: Literal["react", "window", "promise", "api"]
    message: str = Field(min_length=1, max_length=2000)
    stack: str = Field(default="", max_length=12000)
    componentStack: str = Field(default="", max_length=8000)
    route: str = Field(default="", max_length=500)
    userAgent: str = Field(default="", max_length=500)


class EvalSessionIn(BaseModel):
    evaluationType: Literal["asset_validation", "policy_evaluation"] = "asset_validation"


class DemoRunIn(BaseModel):
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)


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
        "signoz": bool(flat.get("integrations.signoz.enabled") and flat.get("integrations.signoz.endpoint")),
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


@app.get("/api/system/performance")
async def system_performance():
    """Live host metrics for the shell; no fixed hardware strings."""
    return await asyncio.to_thread(performance.snapshot)


LOOP_STAGES = [
    {"icon": "gauge", "title": "Evaluate robot", "desc": "Run MuJoCo episodes over persisted physical scenarios"},
    {"icon": "search", "title": "Diagnose weakness", "desc": "Aggregate real evaluation failure modes"},
    {"icon": "sources", "title": "Query sources", "desc": "Retrieve product specifications and provenance"},
    {"icon": "cube", "title": "Compile asset", "desc": "Generate GLB, MJCF, and validated OpenUSD"},
    {"icon": "scale", "title": "Validate physics", "desc": "Load and test articulation in MuJoCo"},
    {"icon": "training", "title": "Attach policy", "desc": "Pin a compatible external VLA checkpoint; training stays disabled"},
    {"icon": "refresh", "title": "Re-evaluate", "desc": "Persist measured improvement and repeat"},
]


@app.get("/api/overview")
async def overview():
    async with SessionLocal() as session:
        skills = (await session.execute(select(Skill).where(Skill.id.not_in(HIDDEN_LEGACY_SKILLS)).order_by(Skill.name))).scalars().all()
        skill_rows = [await catalog.skill_summary(session, row) for row in skills]
        assets = (await session.execute(select(Asset).order_by(Asset.created_at.desc()))).scalars().all()
        sources = (await session.execute(select(Source).order_by(Source.created_at.desc()))).scalars().all()
        runs = (await session.execute(select(TrainingRun).where((TrainingRun.skill_id.is_(None)) | (TrainingRun.skill_id.not_in(HIDDEN_LEGACY_SKILLS))).order_by(TrainingRun.created_at.desc()))).scalars().all()
        jobs = (await session.execute(select(Job).order_by(Job.updated_at.desc()).limit(8))).scalars().all()
        evals = (await session.execute(select(Evaluation).where(Evaluation.skill_id.not_in(HIDDEN_LEGACY_SKILLS)).order_by(Evaluation.created_at))).scalars().all()

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
        {"key": "brightdata", "name": "Bright Data", "desc": "Real-world source collection", "status": "Configured" if configured["brightdata"] else "Setup required", "meta": "no synthetic source fallback"},
        {"key": "signoz", "name": "SigNoz Community", "desc": "Self-hosted OpenTelemetry and queries", "status": "Enabled" if configured["signoz"] else "Local mirror", "meta": "use the live probe to verify delivery"},
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
            "recent": [
                {"id": a.id, "name": a.name, "status": "promoted" if a.status == "ready" else "blocked"}
                for a in (ready + blocked)[:5]
            ],
        },
        "integrations": integrations,
    }


@app.get("/api/skills")
async def skills_list():
    async with SessionLocal() as session:
        rows = (await session.execute(select(Skill).where(Skill.id.not_in(HIDDEN_LEGACY_SKILLS)).order_by(Skill.name))).scalars().all()
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
        {"dimension": row["family"], "coverage": row["coverage"], "gaps": max(row["count"] - round(row["coverage"] * row["count"] / 100), 0), "bands": row["bands"]}
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
    if skill_id in HIDDEN_LEGACY_SKILLS:
        raise HTTPException(404, "Skill not found")
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
                row.status = "blocked" if isinstance(result, dict) and str(result.get("outcome", "")).startswith("awaiting_") else "success"
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


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    async with SessionLocal() as db:
        row = await db.get(Job, job_id)
        if row is None:
            raise HTTPException(404, "Job not found")
        return {
            "id": row.id,
            "kind": row.kind,
            "status": row.status,
            "detail": row.detail,
            "createdAt": row.created_at.isoformat(),
            "updatedAt": row.updated_at.isoformat(),
        }


@app.get("/api/demo-runs/{job_id}")
async def get_demo_job(job_id: str):
    """Legacy compatibility path for older clients."""
    return await get_job(job_id)


@app.get("/api/demo-scenarios")
async def demo_scenario_definitions():
    flat = await settings_store.get_flat()
    try:
        renderer = await asyncio.to_thread(vulkan_renderer.probe)
    except vulkan_renderer.VulkanUnavailable as exc:
        renderer = {"available": False, "error": str(exc)}
    return {
        "scenarios": demo_scenarios.definitions(),
        "readiness": {
            "vulkan": renderer,
            "policyConfigured": bool(flat.get("models.policyEndpoint")),
            "brightDataConfigured": bool(flat.get("integrations.brightdata.apiKey")),
            "sigNozConfigured": bool(flat.get("integrations.signoz.enabled") and flat.get("integrations.signoz.endpoint")),
            "trainingEnabled": False,
        },
    }


@app.post("/api/demo-scenarios/{scenario_id}/runs", status_code=202)
async def run_demo_scenario(scenario_id: str, payload: DemoRunIn = DemoRunIn()):
    if scenario_id not in demo_scenarios.SCENARIOS:
        raise HTTPException(404, "Acceptance scenario not found")
    job_id = new_id("job")
    scenario = demo_scenarios.SCENARIOS[scenario_id]
    async with SessionLocal() as db:
        db.add(Job(
            id=job_id,
            kind="acceptance_scenario",
            status="pending",
            detail={"name": scenario["name"], "scenarioId": scenario_id, "requestedSeed": payload.seed, "stages": []},
        ))
        await db.commit()
    task = asyncio.create_task(
        _job_runner(job_id, demo_scenarios.run(job_id, scenario_id, payload.seed)),
        name=job_id,
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"jobId": job_id, "scenarioId": scenario_id, "status": "pending"}


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
        pipeline.build_asset(payload.query, payload.kind, payload.sourceId, payload.manualSpec, payload.generator, asset_id=asset_id),
    )
    return {"assetId": asset_id, "jobId": job_id, "generator": payload.generator, "compiler": "openusd-mujoco"}


@app.get("/api/assets/{asset_id}")
async def asset_detail(asset_id: str):
    async with SessionLocal() as session:
        row = await session.get(Asset, asset_id)
        if row is None:
            raise HTTPException(404, "Asset not found")
        return await catalog.asset_out(session, row)


@app.get("/api/assets/{asset_id}/render/vulkan", response_class=Response)
async def render_asset_vulkan(
    asset_id: str,
    width: int = Query(default=960, ge=320, le=1600),
    height: int = Query(default=540, ge=180, le=1000),
    yaw: float = Query(default=34.0, ge=-360.0, le=360.0),
    pitch: float = Query(default=18.0, ge=-45.0, le=75.0),
    zoom: float = Query(default=1.0, ge=0.45, le=4.0),
):
    """Return pixels rendered from the asset's actual GLB through Vulkan."""
    async with SessionLocal() as session:
        if await session.get(Asset, asset_id) is None:
            raise HTTPException(404, "Asset not found")
    model = (ASSETS_DIR / asset_id / "model.glb").resolve()
    if model.parent != (ASSETS_DIR / asset_id).resolve() or not model.is_file():
        raise HTTPException(404, "Generated GLB is not available")
    try:
        png = await asyncio.to_thread(
            vulkan_renderer.render_glb_png,
            model,
            width=width,
            height=height,
            yaw=yaw,
            pitch=pitch,
            zoom=zoom,
        )
    except (vulkan_renderer.VulkanUnavailable, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-RobotWorld-Renderer": "Vulkan", "X-RobotWorld-Asset": asset_id},
    )


@app.get("/api/worlds/render/vulkan", response_class=Response)
async def render_active_world_vulkan(
    width: int = Query(default=960, ge=320, le=1600),
    height: int = Query(default=540, ge=180, le=1000),
    yaw: float = Query(default=34.0, ge=-360.0, le=360.0),
    pitch: float = Query(default=18.0, ge=-45.0, le=75.0),
    zoom: float = Query(default=1.0, ge=0.45, le=4.0),
):
    """Return the active OpenUSD world's actual placed GLBs through Vulkan."""
    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        ids = _placed_asset_ids(world.scene_tree)
        found = (await session.execute(select(Asset).where(Asset.id.in_(ids)))).scalars().all() if ids else []
        rows = _world_assembly_rows(ids, {row.id: row for row in found}, _placement_state(world.scene_tree))

    placements = [
        vulkan_renderer.WorldPlacement(
            asset_id=str(row["asset_id"]),
            model_path=(ASSETS_DIR / str(row["asset_id"]) / "model.glb"),
            translation=tuple(float(value) for value in row["translation"]),
            usd_scale=tuple(float(value) for value in row["scale"]),
            rotation_z_deg=float(row.get("rotation_z_deg") or 0.0),
        )
        for row in rows
        if (ASSETS_DIR / str(row["asset_id"]) / "model.glb").is_file()
    ]
    if not placements:
        raise HTTPException(409, "Active OpenUSD world has no generated GLB placements.")
    try:
        png = await asyncio.to_thread(
            vulkan_renderer.render_world_glb_png,
            placements,
            width=width,
            height=height,
            yaw=yaw,
            pitch=pitch,
            zoom=zoom,
        )
    except (vulkan_renderer.VulkanUnavailable, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-RobotWorld-Renderer": "Vulkan",
            "X-RobotWorld-World": world.id,
            "X-RobotWorld-Assets": str(len(placements)),
        },
    )


@app.post("/api/assets/{asset_id}/evidence-analysis", status_code=202)
async def asset_evidence_analysis(asset_id: str, payload: EvidenceAnalysisIn):
    """Turn existing collected evidence into an auditable structured record.

    This route intentionally does not fetch the web or start a 3D job.  It
    only submits the evidence that is already persisted for the selected asset.
    """
    async with SessionLocal() as session:
        asset = await session.get(Asset, asset_id)
        if asset is None:
            raise HTTPException(404, "Asset not found")
    spec_path = (ASSETS_DIR / asset_id / "spec.json").resolve()
    if spec_path.parent != (ASSETS_DIR / asset_id).resolve() or not spec_path.is_file():
        raise HTTPException(409, "Asset has no persisted source evidence to analyze.")

    async def work():
        report = await asset_evidence.analyze(asset_id)
        output = (ASSETS_DIR / asset_id / "analysis.json").resolve()
        if output.parent != (ASSETS_DIR / asset_id).resolve():
            raise RuntimeError("Invalid asset analysis output path")
        output.write_text(json.dumps(report, indent=2), encoding="utf8")
        async with SessionLocal() as db:
            row = await db.get(Asset, asset_id)
            if row is None:
                raise KeyError("Asset not found")
            props = dict(row.properties or {})
            props["evidenceAnalysis"] = "completed — review required"
            row.properties = props
            existing = (await db.execute(select(Artifact).where(Artifact.asset_id == asset_id, Artifact.file == "analysis.json"))).scalar_one_or_none()
            if existing is None:
                db.add(Artifact(asset_id=asset_id, type="evidence_analysis", file="analysis.json", size_bytes=output.stat().st_size))
            else:
                existing.size_bytes = output.stat().st_size
            await db.commit()
        events.publish("pipeline", "Evidence analysis complete", f"{asset_id} · human review required", asset=asset_id)
        return {"assetId": asset_id, "file": "analysis.json", "humanReviewRequired": True}

    job_id = await _start_job("asset_evidence_analysis", {"assetId": asset_id, "stage": "OpenAI evidence review"}, work())
    return {"jobId": job_id, "assetId": asset_id, "mode": "evidence_bounded", "automatic": False}


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
        }
        asset_spec = {
            "width": float(spec.get("width_m", 0.7)),
            "height": float(spec.get("height_m", 1.7)),
            "depth": float(spec.get("depth_m", 0.65)),
            "door_width": float(spec.get("door_width_m", 0.35)),
            "hinge_side": str(spec.get("hinge_side", "left")),
            "hinge_damping": 1.2,
            "pos": [0.55, 0.0],
            "handle": {
                "height": float(spec.get("handle_height_m", 1.05)),
                "orientation": "vertical",
                "offset_from_edge": 0.06,
                "protrude": 0.09,
            },
        }
        scenario["robot_base"] = simcore.robot_base_for_asset(asset_spec)
        result = await asyncio.to_thread(
            simcore.run_rollout,
            simcore.World(scenario, asset_spec),
            simcore.ScriptedController,
            record=False,
        )
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
    if filename not in {"model.glb", "asset.usda", "visual.usdc", "world.usda", "basecolor.png", "spec.json", "analysis.json"}:
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
        worlds = (await session.execute(select(World))).scalars().all()
        for world in worlds:
            world.scene_tree = _without_asset_placements(world.scene_tree, asset_id)
            await _author_world_assembly(session, world, world.scene_tree)
        await session.execute(delete(Artifact).where(Artifact.asset_id == asset_id))
        await session.execute(delete(CompileStage).where(CompileStage.asset_id == asset_id))
        await session.delete(row)
        await session.commit()
    target = (ASSETS_DIR / asset_id).resolve()
    if target.parent == ASSETS_DIR.resolve() and target.is_dir():
        shutil.rmtree(target)


def _without_asset_placements(nodes: Any, asset_id: str) -> list[dict[str, Any]]:
    """Remove only scene nodes that reference the deleted application asset."""
    cleaned: list[dict[str, Any]] = []
    for value in nodes if isinstance(nodes, list) else []:
        if not isinstance(value, dict) or value.get("assetId") == asset_id:
            continue
        node = dict(value)
        if isinstance(node.get("children"), list):
            node["children"] = _without_asset_placements(node["children"], asset_id)
        cleaned.append(node)
    return cleaned


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


def _generated_world_checks(rows: list[dict[str, Any]], stage_available: bool) -> list[dict[str, Any]]:
    """Validate the active generated world without substituting demo physics."""
    checks: list[dict[str, Any]] = [{
        "check": "OpenUSD composition",
        "status": "pass" if stage_available else "fail",
        "details": "stage.usda resolves the persisted generated-asset references" if stage_available else "stage.usda is missing",
        "impacted": "stage.usda",
        "severity": "Info" if stage_available else "High",
    }]
    if not rows:
        checks.append({"check": "Placed geometry", "status": "fail", "details": "No generated GLB placements are available.", "impacted": "world", "severity": "High"})
        return checks
    for row in rows:
        target = row["target_dimensions"]
        bounds = row["world_bounds"]
        actual = tuple(bounds[1][i] - bounds[0][i] for i in range(3))
        angle = math.radians(float(row.get("rotation_z_deg") or 0.0))
        expected = (abs(math.cos(angle)) * target[0] + abs(math.sin(angle)) * target[1], abs(math.sin(angle)) * target[0] + abs(math.cos(angle)) * target[1], target[2])
        delta = max(abs(actual[i] - expected[i]) for i in range(3))
        checks.append({
            "check": f"Measured mesh fit - {row['asset_name']}",
            "status": "pass" if delta < 0.002 else "fail",
            "details": f"World AABB {actual[0]:.3f} x {actual[1]:.3f} x {actual[2]:.3f} m; physical W/D/H {target[0]:.3f} x {target[1]:.3f} x {target[2]:.3f} m; rotation {float(row.get('rotation_z_deg') or 0.0):.1f}°",
            "impacted": row["asset_id"],
            "severity": "Info" if delta < 0.002 else "High",
        })
        source_ok = row["dimension_source"] != "inferred"
        checks.append({
            "check": f"Dimension evidence - {row['asset_name']}",
            "status": "pass" if source_ok else "warn",
            "details": f"{row['dimension_source']} dimensions; minimum confidence {row['dimension_confidence']:.2f}",
            "impacted": row["asset_id"],
            "severity": "Info" if source_ok else "Medium",
        })
        anchor = row["anchor"]
        checks.append({
            "check": f"Support anchor - {row['asset_name']}",
            "status": "pass",
            "details": f"{anchor['mode']} on {anchor['surface']}; authored gap {anchor['gap_m'] * 1000:.1f} mm",
            "impacted": row["asset_id"],
            "severity": "Info",
        })
    checks.append({
        "check": "Physical simulation readiness",
        "status": "warn",
        "details": "Generated objects are static visual meshes. Measured collision, mass, articulation, and an embodiment-compatible policy are still required before rollout.",
        "impacted": f"{len(rows)} generated assets",
        "severity": "High",
    })
    return checks


@app.get("/api/worlds/scene")
async def world_scene():
    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        variants = (await session.execute(select(Variant).where(Variant.world_id == world.id).order_by(Variant.created_at))).scalars().all()
        ids = _placed_asset_ids(world.scene_tree)
        found = (await session.execute(select(Asset).where(Asset.id.in_(ids)))).scalars().all() if ids else []
        placement_rows = _world_assembly_rows(ids, {row.id: row for row in found}, _placement_state(world.scene_tree))
    return {
        "worldId": world.id,
        "worldName": world.name,
        "sceneTree": world.scene_tree,
        "placedAssets": _placed_asset_ids(world.scene_tree),
        "placements": [_placement_api(row) for row in placement_rows],
        "assembly": {
            "file": "stage.usda",
            "available": (WORLDS_DIR / world.id / "stage.usda").is_file(),
        },
        "variants": [{"id": row.id, "name": row.name, "desc": row.desc, "active": row.active} for row in variants],
        "physicsChecks": _generated_world_checks(
            placement_rows,
            (WORLDS_DIR / world.id / "stage.usda").is_file(),
        ),
        "taskSteps": [],
        "successConditions": [],
        "eventTimeline": [],
    }


def _placed_asset_ids(nodes: list[Any]) -> list[str]:
    """Extract persisted generated-asset placements from the world tree."""
    found: list[str] = []
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        if node.get("visible") is False:
            continue
        asset_id = node.get("assetId")
        if isinstance(asset_id, str) and asset_id and asset_id not in found:
            found.append(asset_id)
        children = node.get("children")
        if isinstance(children, list):
            for child_id in _placed_asset_ids(children):
                if child_id not in found:
                    found.append(child_id)
    return found


def _placement_state(nodes: list[Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        asset_id = node.get("assetId")
        if isinstance(asset_id, str) and asset_id and asset_id not in result:
            result[asset_id] = node
        if isinstance(node.get("children"), list):
            for key, value in _placement_state(node["children"]).items():
                result.setdefault(key, value)
    return result


def _world_assembly_rows(ids: list[str], by_id: dict[str, Asset], state: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Build the canonical placement rows used by both OpenUSD and Vulkan.

    Keeping the semantics in one function prevents the displayed whole world
    from drifting away from the transforms authored into ``stage.usda``.
    """
    candidates: list[dict[str, Any]] = []
    fruit_index = 0
    for index, asset_id in enumerate(ids):
        row = by_id.get(asset_id)
        asset_dir = ASSETS_DIR / asset_id
        layer = asset_dir / "asset.usda"
        model = asset_dir / "model.glb"
        if row is None or not layer.is_file() or not model.is_file():
            continue
        spec = row.spec or {}

        def spec_value(key: str, default: Any) -> Any:
            value = spec.get(key, default)
            return value.get("value", default) if isinstance(value, dict) else value

        category = str(spec_value("category", row.name)).lower()
        width = max(0.01, float(spec_value("width_m", 1.0)))
        height = max(0.01, float(spec_value("height_m", 1.0)))
        depth = max(0.01, float(spec_value("depth_m", 1.0)))
        try:
            fit = world_geometry.measured_fit(model, width, height, depth)
        except (OSError, ValueError):
            # Fail visibly but preserve composition for legacy assets whose
            # raw visual is unavailable. New generated assets always use the
            # measured branch above.
            fit = {
                "raw_bounds": ((-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)),
                "raw_extents": (1.0, 1.0, 1.0),
                "scale": (width, depth, height),
                "local_usd_low": (-width / 2, -depth / 2, -height / 2),
                "local_usd_high": (width / 2, depth / 2, height / 2),
                "target_dimensions": (width, depth, height),
            }
        source_fields = [spec.get(key, {}) for key in ("width_m", "height_m", "depth_m")]
        dimension_sources = [str(value.get("source", "inferred")) for value in source_fields if isinstance(value, dict)]
        confidences = [float(value.get("confidence", 0.0)) for value in source_fields if isinstance(value, dict)]
        evidence = "measured/scraped" if dimension_sources and all(value != "inferred" for value in dimension_sources) else "inferred"
        mass_value = max(0.001, float(spec_value("mass_kg", width * height * depth * 500.0)))
        mass_field = spec.get("mass_kg", {})
        mass_source = str(mass_field.get("source", "volume-density estimate")) if isinstance(mass_field, dict) else "asset specification"
        candidates.append({
            "asset_id": asset_id,
            "asset_name": row.name,
            "asset_kind": row.kind,
            "asset_layer": layer,
            "model_path": model,
            "category": category,
            "fit": fit,
            "dimension_source": evidence,
            "dimension_confidence": min(confidences) if confidences else 0.0,
            "scale_source": f"occupied GLB bounds fitted to {evidence} target dimensions",
            "mass_kg": mass_value,
            "mass_source": mass_source,
            "index": index,
        })

    counter_top = 0.9
    for item in candidates:
        category = item["category"]
        if "counter" in category or "island" in item["asset_name"].lower():
            local_low = item["fit"]["local_usd_low"]
            local_high = item["fit"]["local_usd_high"]
            translation = (0.0, 0.0, -local_low[2])
            counter_top = translation[2] + local_high[2]
            item["translation"] = translation
            item["anchor"] = {"mode": "floor", "surface": "world floor", "gap_m": 0.0}

    rows: list[dict[str, Any]] = []
    for item in candidates:
        category = item["category"]
        name = item["asset_name"].lower()
        fit = item["fit"]
        low = fit["local_usd_low"]
        high = fit["local_usd_high"]
        translation = item.get("translation")
        anchor = item.get("anchor")
        persisted = (state or {}).get(item["asset_id"], {})
        persisted_translation = persisted.get("translation")
        rotation_z_deg = float(persisted.get("rotationZDeg") or 0.0)
        raw_multiplier = persisted.get("scaleMultiplier")
        scale_multiplier = (
            tuple(float(value) for value in raw_multiplier)
            if isinstance(raw_multiplier, list) and len(raw_multiplier) == 3
            else (1.0, 1.0, 1.0)
        )
        fixed_terms = ("counter", "island", "table", "sink", "faucet", "tap", "cabinet", "wall", "blender")
        inferred_mobility = "fixed" if any(term in f"{category} {name}" for term in fixed_terms) else "movable"
        mobility = persisted.get("mobility") if persisted.get("mobility") in {"movable", "fixed"} else inferred_mobility
        if isinstance(persisted_translation, list) and len(persisted_translation) == 3:
            translation = tuple(float(value) for value in persisted_translation)
            stored_anchor = persisted.get("anchor")
            anchor = stored_anchor if isinstance(stored_anchor, dict) else {"mode": "manual", "surface": "user-authored transform", "gap_m": 0.0}
        elif "counter" in category or "island" in name:
            pass
        elif "sink" in category and "faucet" not in name:
            rim_offset = 0.012
            translation = (-0.35, 0.0, counter_top + rim_offset - high[2])
            anchor = {"mode": "integrated", "surface": "countertop", "gap_m": rim_offset}
        elif "faucet" in category or "faucet" in name or "tap" in name:
            translation = (-0.35, 0.18, counter_top - low[2])
            anchor = {"mode": "on_surface", "surface": "countertop", "gap_m": 0.0}
        elif "blender" in category:
            translation = (0.45, 0.02, counter_top - low[2])
            anchor = {"mode": "on_surface", "surface": "countertop", "gap_m": 0.0}
        elif "fruit" in category or any(value in name for value in ("apple", "orange", "banana")):
            translation = (-0.05 + fruit_index * 0.22, -0.28, counter_top - low[2])
            fruit_index += 1
            anchor = {"mode": "on_surface", "surface": "countertop", "gap_m": 0.0}
        else:
            translation = (float(item["index"]) * 0.4, 0.0, -low[2])
            anchor = {"mode": "floor", "surface": "world floor", "gap_m": 0.0}
        final_scale = tuple(float(fit["scale"][index]) * scale_multiplier[index] for index in range(3))
        scaled_dimensions = tuple(float(fit["target_dimensions"][index]) * scale_multiplier[index] for index in range(3))
        bounds = world_geometry.world_bounds(fit, translation, rotation_z_deg, scale_multiplier)
        rows.append({
            **{key: value for key, value in item.items() if key not in {"fit", "category", "index"}},
            "translation": translation,
            "rotation_z_deg": rotation_z_deg,
            "base_scale": fit["scale"],
            "scale_multiplier": scale_multiplier,
            "scale": final_scale,
            "raw_bounds": fit["raw_bounds"],
            "raw_extents": fit["raw_extents"],
            "target_dimensions": scaled_dimensions,
            "world_bounds": bounds,
            "anchor": anchor,
            "mobility": mobility,
            "collision_approximation": "convexHull" if mobility == "movable" else "none",
        })
    return rows


def _placement_api(row: dict[str, Any]) -> dict[str, Any]:
    """Serialize the canonical placement without leaking local asset paths."""
    return {
        "assetId": row["asset_id"],
        "name": row["asset_name"],
        "translation": list(row["translation"]),
        "rotationZDeg": row["rotation_z_deg"],
        "baseScale": list(row["base_scale"]),
        "scaleMultiplier": list(row["scale_multiplier"]),
        "scale": list(row["scale"]),
        "rawBounds": [list(value) for value in row["raw_bounds"]],
        "rawExtents": list(row["raw_extents"]),
        "targetDimensions": list(row["target_dimensions"]),
        "worldBounds": [list(value) for value in row["world_bounds"]],
        "dimensionSource": row["dimension_source"],
        "dimensionConfidence": row["dimension_confidence"],
        "anchor": row["anchor"],
        "mobility": row["mobility"],
        "massKg": row["mass_kg"],
        "massSource": row["mass_source"],
        "collisionApproximation": row["collision_approximation"],
        "physicalStatus": "usd_physics_authored_pending_isaac_validation",
    }


async def _author_world_assembly(session, world: World, tree: list[dict[str, Any]]) -> int:
    """Persist the active scene tree as a validated OpenUSD reference stage."""
    ids = _placed_asset_ids(tree)
    rows: list[dict[str, Any]] = []
    if ids:
        found = (await session.execute(select(Asset).where(Asset.id.in_(ids)))).scalars().all()
        rows = _world_assembly_rows(ids, {row.id: row for row in found}, _placement_state(tree))
    output = WORLDS_DIR / world.id / "stage.usda"
    if not rows:
        if output.is_file():
            output.unlink()
        return 0
    _, count = await asyncio.to_thread(usda.write_world_assembly, rows, output)
    return count


@app.post("/api/worlds/assets/{asset_id}/place")
async def place_asset_in_world(asset_id: str):
    """Persist an existing composed OpenUSD asset as an active world placement."""
    asset_dir = (ASSETS_DIR / asset_id).resolve()
    if asset_dir.parent != ASSETS_DIR.resolve() or not (asset_dir / "world.usda").is_file():
        raise HTTPException(409, "Asset must have a composed world.usda before it can be placed.")
    async with SessionLocal() as session:
        asset = await session.get(Asset, asset_id)
        if asset is None:
            raise HTTPException(404, "Asset not found")
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        world.name = "Kitchen Juice Workspace"
        tree = [node for node in (world.scene_tree or []) if not (isinstance(node, dict) and node.get("assetId") == asset_id)]
        tree.append({
            "id": f"generated-{asset.id}",
            "assetId": asset.id,
            "name": asset.name,
            "icon": "cube",
            "tag": "OpenUSD generated asset",
            "children": [{"id": f"trellis-visual-{asset.id}", "assetId": asset.id, "name": "TRELLIS.2 PBR visual mesh", "icon": "cube", "tag": "mesh"}],
        })
        ids = _placed_asset_ids(tree)
        assets = (await session.execute(select(Asset).where(Asset.id.in_(ids)))).scalars().all()
        defaults = {row["asset_id"]: row for row in _world_assembly_rows(ids, {item.id: item for item in assets})}
        for node in tree:
            if isinstance(node, dict) and node.get("assetId") in defaults and "translation" not in node:
                row = defaults[str(node["assetId"])]
                node["translation"] = list(row["translation"])
                node["anchor"] = row["anchor"]
                node["visible"] = True
        await _author_world_assembly(session, world, tree)
        world.scene_tree = tree
        await session.commit()
        return {"worldId": world.id, "assetId": asset.id, "placedAssets": _placed_asset_ids(tree)}


@app.put("/api/worlds/scene")
async def save_world(payload: SceneIn):
    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        world.scene_tree = payload.sceneTree
        await _author_world_assembly(session, world, payload.sceneTree)
        existing = {row.id: row for row in (await session.execute(select(Variant).where(Variant.world_id == world.id))).scalars().all()}
        for item in payload.variants:
            row = existing.get(str(item.get("id")))
            if row:
                row.name = str(item.get("name", row.name))[:160]
                row.desc = str(item.get("desc", row.desc))[:500]
        await session.commit()
    return {"saved": True}


def _edit_placement(nodes: list[Any], asset_id: str, payload: PlacementIn) -> bool:
    changed = False
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        if node.get("assetId") == asset_id:
            if payload.translation is not None:
                node["translation"] = list(payload.translation)
                node["anchor"] = {"mode": "manual", "surface": "user-authored transform", "gap_m": 0.0}
            if payload.visible is not None:
                node["visible"] = payload.visible
            if payload.rotationZDeg is not None:
                node["rotationZDeg"] = float(payload.rotationZDeg) % 360.0
            if payload.scaleMultiplier is not None:
                node["scaleMultiplier"] = list(payload.scaleMultiplier)
                node["anchor"] = {"mode": "manual", "surface": "user-authored transform", "gap_m": 0.0}
            if payload.mobility is not None:
                node["mobility"] = payload.mobility
            changed = True
        if isinstance(node.get("children"), list):
            changed = _edit_placement(node["children"], asset_id, payload) or changed
    return changed


@app.patch("/api/worlds/placements/{asset_id}")
async def update_world_placement(asset_id: str, payload: PlacementIn):
    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        tree = json.loads(json.dumps(world.scene_tree or []))
        if not _edit_placement(tree, asset_id, payload):
            raise HTTPException(404, "Placed asset not found")
        world.scene_tree = tree
        await _author_world_assembly(session, world, tree)
        await session.commit()
    events.publish("world", "Placement updated", f"{asset_id} · OpenUSD and Vulkan synchronized", asset=asset_id)
    return {"saved": True, "assetId": asset_id}


@app.post("/api/worlds/layout")
async def auto_layout_world():
    """Ask Luna for semantic relationships, then solve metres from measured AABBs."""
    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        ids = _placed_asset_ids(world.scene_tree)
        found = (await session.execute(select(Asset).where(Asset.id.in_(ids)))).scalars().all() if ids else []
        # Auto-layout is an explicit reset of manual transforms. Solve from the
        # measured canonical dimensions rather than feeding prior bad offsets
        # back into the support-surface calculation.
        rows = _world_assembly_rows(ids, {row.id: row for row in found})
        if not rows:
            raise HTTPException(409, "Place generated assets before running auto-layout.")

        context = [{"assetId": row["asset_id"], "name": row["asset_name"], "dimensionsWDH": row["target_dimensions"], "dimensionSource": row["dimension_source"]} for row in rows]
        system = """Arrange a tabletop robotics scene from supplied measured assets. Return JSON {placements:[{assetId,relation,u,v}]}; relation is floor, on_counter, or integrated_counter; u/v are numbers -0.8..0.8 across the support surface. Put the largest counter/island on floor. A sink is integrated_counter. A faucet and appliances are on_counter. Do not invent IDs or dimensions."""
        proposed, provenance = await llm.plan(system, json.dumps(context), span_name="world semantic layout")
        proposals = proposed.get("placements", []) if isinstance(proposed, dict) else []
        valid = {str(item.get("assetId")): item for item in proposals if isinstance(item, dict) and str(item.get("assetId")) in ids}

        counter = max(rows, key=lambda row: float(row["target_dimensions"][0]) * float(row["target_dimensions"][1]))
        cb = counter["world_bounds"]
        counter_top = float(cb[1][2])
        arranged: dict[str, tuple[list[float], dict[str, Any]]] = {}
        arranged[counter["asset_id"]] = (list(counter["translation"]), {"mode": "floor", "surface": "world floor", "gap_m": 0.0})
        others = [row for row in rows if row["asset_id"] != counter["asset_id"]]
        columns = max(1, math.ceil(math.sqrt(len(others))))
        for index, row in enumerate(others):
            proposal = valid.get(row["asset_id"], {})
            name = row["asset_name"].lower()
            relation = str(proposal.get("relation") or ("integrated_counter" if "sink" in name and "faucet" not in name else "on_counter"))
            fallback_u = -0.7 + 1.4 * ((index % columns) + 0.5) / columns
            fallback_v = -0.65 + 1.3 * ((index // columns) + 0.5) / max(1, math.ceil(len(others) / columns))
            u = max(-0.8, min(0.8, float(proposal.get("u", fallback_u))))
            v = max(-0.8, min(0.8, float(proposal.get("v", fallback_v))))
            low = row["world_bounds"][0]
            local_low = [float(low[i]) - float(row["translation"][i]) for i in range(3)]
            x = (float(cb[0][0]) + float(cb[1][0])) / 2 + u * (float(cb[1][0]) - float(cb[0][0])) / 2
            y = (float(cb[0][1]) + float(cb[1][1])) / 2 + v * (float(cb[1][1]) - float(cb[0][1])) / 2
            if relation == "floor":
                z = -local_low[2]
                anchor = {"mode": "floor", "surface": "world floor", "gap_m": 0.0}
            elif relation == "integrated_counter":
                z = counter_top + 0.012 - float(row["target_dimensions"][2]) - local_low[2]
                anchor = {"mode": "integrated", "surface": counter["asset_name"], "gap_m": 0.012}
            else:
                z = counter_top - local_low[2]
                anchor = {"mode": "on_surface", "surface": counter["asset_name"], "gap_m": 0.0}
            arranged[row["asset_id"]] = ([x, y, z], anchor)

        tree = json.loads(json.dumps(world.scene_tree or []))
        for node in tree:
            if isinstance(node, dict) and node.get("assetId") in arranged:
                node["translation"], node["anchor"] = arranged[str(node["assetId"])]
                node["rotationZDeg"] = 0.0
                node["scaleMultiplier"] = [1.0, 1.0, 1.0]
                node["visible"] = True
        world.scene_tree = tree
        await _author_world_assembly(session, world, tree)
        await session.commit()
    events.publish("world", "Constraint layout solved", f"{len(arranged)} measured assets · {provenance}", world=world.id)
    return {"saved": True, "provenance": provenance, "placements": len(arranged), "supportAssetId": counter["asset_id"]}


@app.get("/api/worlds/files/stage.usda")
async def world_stage_file():
    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
    path = (WORLDS_DIR / world.id / "stage.usda").resolve()
    if path.parent != (WORLDS_DIR / world.id).resolve() or not path.is_file():
        raise HTTPException(404, "The active world has no composed OpenUSD stage")
    return FileResponse(path, filename="stage.usda")


@app.post("/api/worlds/checks/run")
async def run_checks():
    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        ids = _placed_asset_ids(world.scene_tree)
        found = (await session.execute(select(Asset).where(Asset.id.in_(ids)))).scalars().all() if ids else []
    rows = await asyncio.to_thread(_world_assembly_rows, ids, {row.id: row for row in found}, _placement_state(world.scene_tree))
    stage_available = (WORLDS_DIR / world.id / "stage.usda").is_file()
    return {"physicsChecks": _generated_world_checks(rows, stage_available)}


@app.post("/api/worlds/cameras/probe")
async def probe_policy_cameras():
    """Render both learned-policy cameras through the packaged MuJoCo stack."""
    def render() -> dict[str, Any]:
        import hashlib

        world = simcore.World(simcore.default_scenario_family(__import__("numpy").random.default_rng(11)))
        try:
            cameras = {}
            for public_name, model_name in (("front", "debug"), ("wrist", "wrist")):
                rgb = world.render_rgb(model_name, width=256, height=256)
                cameras[public_name] = {
                    "shape": list(rgb.shape),
                    "dtype": str(rgb.dtype),
                    "sha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
                    "nonzero": int(__import__("numpy").count_nonzero(rgb)),
                }
            return {"renderer": "MuJoCo offscreen", "cameras": cameras}
        finally:
            world.close()

    return await asyncio.to_thread(render)


@app.get("/api/models/vla-jepa/status")
async def vla_jepa_status():
    """Inspect the local checkpoint without loading weights into RAM/VRAM."""
    try:
        return await asyncio.to_thread(local_vla.inspect_checkpoint)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(500, f"Local VLA-JEPA checkpoint inspection failed: {exc}") from exc


@app.get("/api/robots")
async def robots_list():
    return {"robots": await asyncio.to_thread(robot_registry.list_all), "accepted": sorted(robot_registry.ALLOWED), "maxBytes": robot_registry.MAX_BYTES}


@app.get("/api/simulation/isaac")
async def isaac_status():
    flat = await settings_store.get_flat()
    return await asyncio.to_thread(
        isaac_sim.inspect,
        str(flat.get("simulation.isaacRoot") or ""),
        str(flat.get("simulation.isaacAssetRoot") or ""),
    )


@app.post("/api/robots/franka/isaac", status_code=201)
async def register_isaac_franka():
    flat = await settings_store.get_flat()
    status = await asyncio.to_thread(
        isaac_sim.inspect,
        str(flat.get("simulation.isaacRoot") or ""),
        str(flat.get("simulation.isaacAssetRoot") or ""),
    )
    manifest = await asyncio.to_thread(robot_registry.register_isaac_franka, status)
    events.publish("robot", "Franka reference registered", f"Isaac Sim {status['version']} · {'ready' if status['ready'] else 'runtime missing'}", robot=manifest["id"])
    return manifest


@app.post("/api/simulation/isaac/prepare")
async def prepare_isaac_world():
    flat = await settings_store.get_flat()
    status = await asyncio.to_thread(
        isaac_sim.inspect,
        str(flat.get("simulation.isaacRoot") or ""),
        str(flat.get("simulation.isaacAssetRoot") or ""),
    )
    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        await _author_world_assembly(session, world, world.scene_tree or [])
    stage_path = WORLDS_DIR / world.id / "stage.usda"
    try:
        launch_path = await asyncio.to_thread(isaac_sim.write_launch_manifest, world.id, stage_path, status)
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc)) from exc
    await asyncio.to_thread(robot_registry.register_isaac_franka, status)
    return {
        "prepared": True,
        "runtimeReady": status["ready"],
        "blockers": status["blockers"],
        "stage": str(stage_path),
        "manifest": str(launch_path),
        "bridge": str((BASE_DIR / "isaac_bridge.py").resolve()),
        "command": [status.get("python") or "<isaac-root>/python.bat", str((BASE_DIR / "isaac_bridge.py").resolve()), str(launch_path)],
    }


@app.post("/api/robots/import", status_code=201)
async def robot_import(request: Request, filename: str = Query(min_length=1, max_length=180)):
    """Accept a raw robot file so large URDF/USD/GLB imports stream to disk."""
    if request.headers.get("content-type", "").split(";", 1)[0] not in {"application/octet-stream", "model/gltf-binary", "text/xml", "text/plain"}:
        raise HTTPException(415, "Upload the robot file as application/octet-stream.")
    try:
        manifest = await robot_registry.ingest(filename, request.stream())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    events.publish("robot", "Robot imported", f"{manifest['name']} · {manifest['format']} · readiness inspected", robot=manifest["id"])
    return manifest


@app.put("/api/robots/{robot_id}")
async def robot_update(robot_id: str, payload: RobotPatchIn):
    try:
        return await asyncio.to_thread(robot_registry.update, robot_id, payload.model_dump(exclude_unset=True))
    except FileNotFoundError as exc:
        raise HTTPException(404, "Robot not found") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/worlds/commands")
async def world_command(payload: WorldCommandIn):
    robots = await asyncio.to_thread(robot_registry.list_all)
    robot = next((item for item in robots if item.get("id") == payload.robotId), None)
    vla = await asyncio.to_thread(local_vla.inspect_checkpoint)
    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        ids = _placed_asset_ids(world.scene_tree)
        found = (await session.execute(select(Asset).where(Asset.id.in_(ids)))).scalars().all() if ids else []
        rows = _world_assembly_rows(ids, {row.id: row for row in found}, _placement_state(world.scene_tree))

    blockers: list[str] = []
    if robot is None:
        blockers.append("Select and import a robot embodiment.")
    else:
        blockers.extend((robot.get("readiness") or {}).get("blockers") or [])
    blockers.extend((vla.get("robotWorldContract") or {}).get("blockers") or [])
    if not rows:
        blockers.append("The active world has no generated assets.")
    if rows:
        blockers.append("World assets are visual-only: measured colliders, mass, friction, and movable/articulated bodies are not compiled.")

    context = {
        "instruction": payload.instruction,
        "world": world.name,
        "objects": [{"id": row["asset_id"], "name": row["asset_name"], "positionM": row["translation"], "physicalStatus": "visual_only"} for row in rows],
        "robot": {key: robot.get(key) for key in ("id", "name", "format", "joints", "cameraMappings", "policyAdapter")} if robot else None,
        "checkpoint": vla.get("checkpoint"),
        "executionBlockers": blockers,
    }
    system = """You plan robot manipulation against the supplied verified scene manifest. Return JSON with keys summary, steps, referencedObjectIds, assumptions. Steps must be short strings. Never claim execution, collision safety, graspability, or success. Use only supplied object IDs. If prerequisites are blocked, produce a diagnostic plan that ends before motor commands."""
    planned, provenance = await llm.plan(system, json.dumps(context), span_name="world command plan")
    if not planned:
        planned = {
            "summary": f"Plan '{payload.instruction}' after the embodiment and physics gates pass.",
            "steps": ["Resolve every readiness blocker", "Render the mapped policy cameras", "Validate target and destination IDs", "Run a bounded dry-run evaluation", "Permit motor actions only after the safety checks pass"],
            "referencedObjectIds": [], "assumptions": [],
        }
    result = {
        "instruction": payload.instruction, "mode": payload.mode, "plan": planned,
        "plannerProvenance": provenance, "executionAllowed": not blockers, "blockers": list(dict.fromkeys(blockers)),
        "robotId": payload.robotId, "worldId": world.id,
    }
    events.publish("agent", "World command planned", f"{payload.instruction[:100]} · {'blocked' if blockers else 'ready'}", world=world.id)
    if payload.mode == "execute" and blockers:
        raise HTTPException(409, detail=result)
    if payload.mode == "execute":
        raise HTTPException(501, detail={**result, "blockers": ["A verified real-time robot policy transport is not implemented; RobotWorld will not simulate success."]})
    return result


@app.get("/api/models/status")
async def model_status():
    """Report installed weights, executable readiness, and real build timings."""
    flat = await settings_store.get_flat()
    native_path = str(flat.get("models.trellisNativePath") or r"D:\TRELLIS.2-4B")
    async with SessionLocal() as session:
        assets = (await session.execute(select(Asset).order_by(Asset.created_at.desc()))).scalars().all()
        stages = (await session.execute(select(CompileStage).order_by(CompileStage.asset_id, CompileStage.idx))).scalars().all()
    by_asset: dict[str, list[CompileStage]] = defaultdict(list)
    for stage in stages:
        by_asset[stage.asset_id].append(stage)
    history = []
    for asset in assets:
        geometry = (asset.spec or {}).get("geometry", {}) if isinstance(asset.spec, dict) else {}
        if not geometry:
            try:
                geometry = json.loads((ASSETS_DIR / asset.id / "spec.json").read_text(encoding="utf8")).get("geometry", {})
            except (OSError, json.JSONDecodeError):
                geometry = {}
        if not isinstance(geometry, dict) or geometry.get("generator") != "trellis2":
            continue
        rows = by_asset.get(asset.id, [])
        generation = next((row for row in rows if "trellis" in row.name.lower() or "geometry" in row.name.lower()), None)
        history.append({
            "assetId": asset.id,
            "name": asset.name,
            "runtime": str(geometry.get("runtime") or "unknown"),
            "resolution": int(geometry.get("resolution") or 1024),
            "generationSeconds": round(generation.duration_s, 3) if generation else None,
            "totalSeconds": round(sum(row.duration_s for row in rows), 3),
            "status": "failed" if any(row.status == "failed" for row in rows) else "completed",
        })
    inspected = await asyncio.to_thread(model_registry.inspect_trellis, native_path, "", "", r"D:\DINOv3")
    runtimes = [row for row in inspected if row.get("id") == "trellis-native"]
    return {
        "vlaJepa": await asyncio.to_thread(local_vla.inspect_checkpoint),
        "trellis": runtimes,
        "hardware": await asyncio.to_thread(performance.snapshot),
        "generationHistory": history[:30],
        "benchmarkComparable": False,
        "benchmarkRunnable": False,
        "benchmarkBlocker": "Quantized TRELLIS is disabled for this workspace; only the native Microsoft pipeline is active.",
    }


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


def _source_detail_from_rows(row: Source, rows: list[dict[str, Any]]) -> tuple[dict[str, Any], float]:
    """Normalize collector output without inventing values.

    Raw rows are retained (bounded) as evidence.  Missing required physical
    fields reduce completeness and remain missing for downstream validation.
    """
    first = rows[0] if rows else {}
    product = next((first.get(k) for k in ("product", "product_name", "name", "title") if first.get(k)), row.query or row.domain)
    model = next((first.get(k) for k in ("model", "model_number", "sku", "mpn") if first.get(k)), "—")
    scalar_skip = {"product", "product_name", "name", "title", "images", "image", "image_url", "photos", "url", "source_url"}
    specs = [[str(k), str(v)] for k, v in first.items() if k not in scalar_skip and isinstance(v, (str, int, float, bool))][:30]
    image_values: list[str] = []
    for key in ("images", "photos", "image", "image_url"):
        value = first.get(key)
        if isinstance(value, str):
            image_values.append(value)
        elif isinstance(value, list):
            image_values.extend(str(item.get("url") if isinstance(item, dict) else item) for item in value)
    image_values = [url for url in image_values if url.startswith(("http://", "https://"))][:8]
    photos = [
        {"id": i + 1, "url": url, "score": 100 if i == 0 else 80, "state": "selected" if i == 0 else "candidate", "front": 0, "background": 0, "isolation": 0, "identity": 0}
        for i, url in enumerate(image_values)
    ]
    source_urls = []
    for item in rows[:50]:
        url = item.get("source_url") or item.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in source_urls:
            source_urls.append(url)
    required_aliases = [
        ("model", "model_number", "sku", "mpn"),
        ("width_m", "width_cm", "width", "dimensions"),
        ("height_m", "height_cm", "height", "dimensions"),
        ("depth_m", "depth_cm", "depth", "dimensions"),
        ("image", "image_url", "images", "photos"),
        ("url", "source_url"),
    ]
    present = sum(any(first.get(key) not in (None, "", []) for key in aliases) for aliases in required_aliases)
    completeness = round(100.0 * present / len(required_aliases), 1) if rows else 0.0
    detail = {
        "product": str(product),
        "model": str(model),
        "specs": specs,
        "provenance": [["Scraper Studio collector", row.collector], ["Source", row.domain], *[["Row URL", url] for url in source_urls[:5]]],
        "photos": photos,
        "rawRows": rows[:50],
    }
    return detail, completeness


async def _collect_source(source_id: str) -> dict[str, Any]:
    async with SessionLocal() as db:
        row = await db.get(Source, source_id)
        if row is None:
            raise KeyError("Source not found")
        collector, domain = row.collector, row.domain
    if not collector:
        raise ValueError("This source has no custom Scraper Studio collector ID. Add its c_* ID before running it.")
    rows = await brightdata.dca_run_and_wait(collector, [{"url": f"https://{domain}"}])
    if not all(isinstance(item, dict) for item in rows):
        raise brightdata.BrightDataError("Collector result must be an array of JSON objects.")
    async with SessionLocal() as db:
        row = await db.get(Source, source_id)
        if row is None:
            raise KeyError("Source not found")
        detail, completeness = _source_detail_from_rows(row, rows)
        row.items = len(rows)
        row.completeness = completeness
        row.health = "healthy" if rows and completeness >= 80 else "degraded"
        row.last_run_at = datetime.now(timezone.utc)
        row.detail = detail
        await db.commit()
    return {"items": len(rows), "completeness": completeness, "collector": collector}


@app.post("/api/sources/{source_id}/run", status_code=202)
async def run_source(source_id: str):
    async with SessionLocal() as session:
        row = await session.get(Source, source_id)
        if row is None:
            raise HTTPException(404, "Source not found")
        if not row.collector:
            raise HTTPException(422, "Configure this source's custom Scraper Studio c_* collector before running it.")
    job_id = await _start_job("source_collection", {"name": source_id, "collector": row.collector}, _collect_source(source_id))
    return {"jobId": job_id}


@app.post("/api/sources/{source_id}/repair", status_code=202)
async def repair_source(source_id: str, payload: SourceRepairIn):
    async with SessionLocal() as session:
        row = await session.get(Source, source_id)
        if row is None:
            raise HTTPException(404, "Source not found")
        if not row.collector:
            raise HTTPException(422, "A c_* Scraper Studio collector is required for self-healing.")
        collector, url = row.collector, f"https://{row.domain}"

    async def work():
        async with SessionLocal() as db:
            source = await db.get(Source, source_id)
            if source:
                source.health = "repairing"
                db.add(RepairEvent(source_id=source_id, time=datetime.now(timezone.utc).strftime("%H:%M:%S"), title="Schema failure confirmed", desc=payload.prompt, kind="detect"))
                await db.commit()
        await brightdata.dca_heal(collector, payload.prompt, url)
        progress = await brightdata.dca_wait_for_heal(collector, stop_at_approval=True)
        state = str(progress.get("status") or progress.get("state") or "awaiting approval")
        async with SessionLocal() as db:
            db.add(RepairEvent(source_id=source_id, time=datetime.now(timezone.utc).strftime("%H:%M:%S"), title="Repair preview ready", desc=f"Bright Data state: {state}. Human approval required.", kind="heal"))
            await db.commit()
        return {"collector": collector, "state": state, "approvalRequired": True}

    job_id = await _start_job("source_repair", {"name": source_id, "collector": collector, "stage": "awaiting human approval"}, work())
    return {"jobId": job_id, "approvalRequired": True}


@app.post("/api/sources/{source_id}/repair/approve", status_code=202)
async def approve_source_repair(source_id: str):
    async with SessionLocal() as session:
        row = await session.get(Source, source_id)
        if row is None:
            raise HTTPException(404, "Source not found")
        if not row.collector:
            raise HTTPException(422, "A c_* Scraper Studio collector is required for approval.")
        collector = row.collector

    async def work():
        await brightdata.dca_approve(collector, True, auto_save=True)
        progress = await brightdata.dca_wait_for_heal(collector, stop_at_approval=False)
        state = str(progress.get("status") or progress.get("state") or "unknown").lower()
        if state in {"failed", "error", "cancelled"}:
            raise brightdata.BrightDataError(f"Collector repair ended in state '{state}'.")
        async with SessionLocal() as db:
            db.add(RepairEvent(source_id=source_id, time=datetime.now(timezone.utc).strftime("%H:%M:%S"), title="Repair approved and saved", desc=f"Bright Data state: {state}; rerunning the same collector.", kind="approve"))
            await db.commit()
        result = await _collect_source(source_id)
        async with SessionLocal() as db:
            db.add(RepairEvent(source_id=source_id, time=datetime.now(timezone.utc).strftime("%H:%M:%S"), title="Collector rerun validated", desc=f"{result['items']} rows; {result['completeness']:.1f}% required-field completeness.", kind="success"))
            await db.commit()
        return result

    job_id = await _start_job("source_repair_approval", {"name": source_id, "collector": collector, "stage": "approved and validating"}, work())
    return {"jobId": job_id}


@app.get("/api/training")
async def training_data():
    async with SessionLocal() as session:
        runs = (await session.execute(select(TrainingRun).where((TrainingRun.skill_id.is_(None)) | (TrainingRun.skill_id.not_in(HIDDEN_LEGACY_SKILLS))).order_by(TrainingRun.created_at.desc()))).scalars().all()
        decisions = (await session.execute(select(AgentDecision).order_by(AgentDecision.created_at.desc()).limit(1))).scalars().all()
        evals = (await session.execute(select(Evaluation).where(Evaluation.skill_id.not_in(HIDDEN_LEGACY_SKILLS)).order_by(Evaluation.created_at))).scalars().all()
    successes = [100.0 if row.success else 0.0 for row in evals]
    collisions = [float(row.collisions) for row in evals]
    best_run = max(runs, key=lambda row: row.success_after or 0.0, default=None)
    avg_delta = statistics.fmean([row.delta_pp for row in runs if row.delta_pp is not None]) if any(row.delta_pp is not None for row in runs) else 0.0
    stats = [
        {"label": "Active runs", "value": str(sum(row.status in {"pending", "in_progress"} for row in runs)), "icon": "play", "tint": "blue", "foot": "persisted jobs"},
        {"label": "Best policy", "value": best_run.policy if best_run else "—", "icon": "trophy", "tint": "amber", "foot": f"{best_run.success_after:.1f}% measured success" if best_run and best_run.success_after is not None else "no completed adaptation"},
        {"label": "Average improvement", "value": f"{avg_delta:+.1f}pp", "icon": "training", "tint": "green", "foot": "completed runs"},
        {"label": "Evaluation success", "value": f"{statistics.fmean(successes):.1f}%" if successes else "0.0%", "icon": "gauge", "tint": "green", "foot": f"{len(evals)} episodes", "donut": statistics.fmean(successes) / 100 if successes else 0},
        {"label": "Current target", "value": runs[0].name if runs else "No active skill", "icon": "target", "tint": "purple", "foot": "latest persisted run" if runs else "attach a compatible robot policy"},
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
        "successCurve": {"measured": successes},
        "collisionCurve": {"measured": collisions},
        "agentDecision": ({"title": decision.title, "decision": decision.decision, "evidence": decision.evidence, "nextStep": decision.next_step, "confidence": decision.confidence} if decision else None),
    }


@app.post("/api/training/runs", status_code=202)
async def queue_training():
    raise HTTPException(409, "Training is disabled. Configure a pinned external VLA and run policy evaluation; RobotWorld will not train on this workstation.")


async def _obs_stats() -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        spans = (await session.execute(select(Span).order_by(Span.created_at.desc()).limit(1000))).scalars().all()
        evals = (await session.execute(select(Evaluation).where(Evaluation.skill_id.not_in(HIDDEN_LEGACY_SKILLS)).order_by(Evaluation.created_at.desc()).limit(200))).scalars().all()
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
    output = []
    for name, points in list(buckets.items())[:8]:
        sampled = points[-90:]
        values = [float(point.value) for point in sampled]
        output.append({
            "name": name,
            "labels": [datetime.fromtimestamp(point.ts_ms / 1000).strftime("%H:%M:%S") for point in sampled],
            "values": values,
            "count": len(points),
            "latest": values[-1] if values else None,
            "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
        })
    return {"series": output, "pointCount": len(rows), "store": "local-opentelemetry-mirror", "signozExporting": signoz_exporting()}


@app.get("/api/observability/logs")
async def observability_logs(level: str | None = Query(default=None)):
    async with SessionLocal() as session:
        stmt = select(LogLine).order_by(LogLine.time_ms.desc()).limit(500)
        if level:
            stmt = select(LogLine).where(LogLine.level == level.upper()).order_by(LogLine.time_ms.desc()).limit(500)
        rows = (await session.execute(stmt)).scalars().all()
    return [{"time": datetime.fromtimestamp(row.time_ms / 1000).strftime("%H:%M:%S.%f")[:-3], "level": row.level, "service": row.service, "message": row.message} for row in rows]


@app.post("/api/diagnostics/frontend-errors", status_code=202)
async def record_frontend_error(payload: FrontendErrorIn):
    """Persist sanitized renderer failures without trusting browser log text."""
    secret_pattern = re.compile(r"(sk-(?:proj-)?[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{12,})", re.IGNORECASE)
    clean = lambda value, limit: secret_pattern.sub("[redacted]", value)[:limit]
    detail = {
        "message": clean(payload.message, 2000),
        "route": clean(payload.route, 500),
        "stack": clean(payload.stack, 12000),
        "componentStack": clean(payload.componentStack, 8000),
        "userAgent": clean(payload.userAgent, 500),
    }
    async with SessionLocal() as session:
        session.add(LogLine(
            time_ms=time.time() * 1000,
            level="ERROR",
            service=f"frontend.{payload.source}",
            message=json.dumps(detail, separators=(",", ":")),
        ))
        await session.commit()
    return {"recorded": True}


@app.get("/api/diagnostics/runtime")
async def runtime_diagnostics():
    """Real recent failures for the in-editor Diagnostics shelf."""
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(LogLine)
            .where(LogLine.level.in_(["ERROR", "WARN"]))
            .order_by(LogLine.time_ms.desc())
            .limit(100)
        )).scalars().all()
    return {
        "status": "degraded" if any(row.level == "ERROR" for row in rows[:10]) else "healthy",
        "uptimeSeconds": round(time.monotonic() - STARTED_AT, 1),
        "events": [{
            "time": datetime.fromtimestamp(row.time_ms / 1000).strftime("%H:%M:%S.%f")[:-3],
            "level": row.level,
            "service": row.service,
            "message": row.message,
        } for row in rows],
    }


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
        skills = (await session.execute(select(Skill).where(Skill.id.not_in(HIDDEN_LEGACY_SKILLS)))).scalars().all()
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
async def create_eval_session(payload: EvalSessionIn = EvalSessionIn()):
    policy_config = None
    if payload.evaluationType == "policy_evaluation":
        try:
            policy_config = PolicyConfig.from_settings(await settings_store.get_flat())
        except PolicyError as exc:
            raise HTTPException(422, str(exc)) from exc
    return live.info(live.create(evaluation_type=payload.evaluationType, policy_config=policy_config))


@app.post("/api/integrations/policy/probe")
async def probe_policy():
    """Check checkpoint/embodiment compatibility without running an episode."""
    try:
        config = PolicyConfig.from_settings(await settings_store.get_flat())
        client = PolicyClient(config)
        try:
            capabilities = await asyncio.to_thread(client.probe)
        finally:
            client.close()
    except PolicyError as exc:
        raise HTTPException(502, {"code": exc.code, "message": str(exc)}) from exc
    return {
        "compatible": True,
        "schemaVersion": capabilities["schemaVersion"],
        "policyId": capabilities["policyId"],
        "embodiment": capabilities["embodiment"],
        "checkpointTrainedForEmbodiment": True,
    }


@app.post("/api/integrations/brightdata/probe")
async def probe_brightdata():
    """Make one real, billable SERP request and return only sanitized evidence."""
    query = "robot refrigerator dimensions official"
    try:
        result = await brightdata.google_search(query)
    except brightdata.NotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except brightdata.BrightDataError as exc:
        raise HTTPException(502, str(exc)) from exc

    general = result.get("general", {}) if isinstance(result, dict) else {}
    organic = result.get("organic", []) if isinstance(result, dict) else []
    domains: list[str] = []
    for item in organic if isinstance(organic, list) else []:
        if not isinstance(item, dict):
            continue
        host = urlparse(str(item.get("link") or "")).hostname
        if host and host not in domains:
            domains.append(host)
    if general.get("search_engine") != "google" or not domains:
        raise HTTPException(502, "Bright Data responded, but no valid Google organic results were returned.")
    return {
        "connected": True,
        "provider": "Bright Data SERP API",
        "searchEngine": "google",
        "queryMatched": general.get("query") == query,
        "organicCount": len(organic),
        "sampleDomains": domains[:5],
    }


@app.post("/api/integrations/trellis/probe")
async def probe_trellis():
    try:
        capabilities = await trellis.probe()
    except trellis.TrellisError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"compatible": True, **capabilities}


@app.post("/api/integrations/signoz/probe")
async def probe_signoz():
    """Verify the configured Community UI and OTLP receiver."""
    try:
        return await signoz.probe()
    except signoz.NotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except signoz.SigNozError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/render/vulkan/probe")
async def probe_vulkan_renderer():
    try:
        return await asyncio.to_thread(vulkan_renderer.probe)
    except vulkan_renderer.VulkanUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/render/vulkan/frame", response_class=Response)
async def render_vulkan_frame(
    scene: Literal["kitchen", "factory"] = "kitchen",
    width: int = Query(default=960, ge=320, le=1600),
    height: int = Query(default=540, ge=180, le=1000),
    yaw: float = Query(default=34.0, ge=-360.0, le=360.0),
    pitch: float = Query(default=24.0, ge=-10.0, le=75.0),
    distance: float = Query(default=12.0, ge=4.0, le=28.0),
    doorAngle: float = Query(default=0.0, ge=0.0, le=120.0),
    variant: Literal["rgb", "seg"] = "rgb",
):
    request = vulkan_renderer.RenderRequest(
        scene=scene,
        width=width,
        height=height,
        yaw=yaw,
        pitch=pitch,
        distance=distance,
        door_angle=doorAngle,
        variant=variant,
    )
    try:
        png = await asyncio.to_thread(vulkan_renderer.render_png, request)
    except (vulkan_renderer.VulkanUnavailable, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-RobotWorld-Renderer": "Vulkan"},
    )


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
