"""RobotWorld FastAPI application and complete renderer API contract."""
from __future__ import annotations

import asyncio
import hashlib
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
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import mujoco
from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select

from . import __version__
from .bootstrap import seed_definitions
from .config import ASSETS_DIR, BASE_DIR, DATA_DIR, ROBOTS_DIR, WORLDS_DIR, env
from .contracts import AgentToolCall, ApprovalDecision, AutonomousCurriculumRunRequest, BrightDataCollectionRequest, CompiledAssetOracleRequest, CompiledAssetVlaEvaluationRequest, CurriculumPlanRequest, FrankaRegistrationRequest, LeRobotDatasetExportRequest, ModelRegistrationCreate, ModelValidationRequest, ObjectRequest, OracleEvaluationRequest, PolicyCandidateDecisionRequest, PolicyCandidateRollbackRequest, RecordedEvidenceImport, RigidAssetCompileRequest, ScraperCollectorVersionCreate, ScraperRepairCreate, ScraperRepairDecision, ScraperRepairDemoRequest, ScraperRepairDraftSubmission, ScraperRepairRollback, VlaFrankaZeroShotBridgeRequest, VlaJepaFineTuneExecuteRequest, VlaJepaFineTuneValidationRequest, VlaNormalizedAction, WorldOperateRequest
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
    Skill,
    Source,
    Span,
    TrainingRun,
    Variant,
    World,
)
from .telemetry import configure_signoz, drain_loop, init_otel, signoz_exporting, span
from .util import fmt_duration, new_id, rel_time
from .services import agent, agent_tools, asset_evidence, autonomous_curriculum, brightdata, catalog, command_store, control_catalog, curriculum_catalog, demo_scenarios, evaluation_catalog, evaluator, events, evidence_catalog, evidence_collection, franka_live, franka_pick_place, isaac_sim, lerobot_dataset, lerobot_training, live, llm, local_vla, model_registry, performance, pipeline, policy_lifecycle, port, rigid_asset_compiler, robot_catalog, robot_registry, scraper_repair, scraper_repair_demo, settings_store, signoz, simcore, trellis, usda, vla_bridge, vla_policy_worker, vulkan_renderer, world_geometry
from .services.remote_policy import PolicyClient, PolicyConfig, PolicyError

log = logging.getLogger(__name__)
STARTED_AT = time.monotonic()
STARTED_AT_WALL_MS = time.time() * 1000
_tasks: set[asyncio.Task] = set()
HIDDEN_LEGACY_SKILLS = {"open-refrigerator"}
DEFERRED_PORT_ENABLED = os.environ.get("ROBOTWORLD_ENABLE_DEFERRED_PORT", "").lower() in {"1", "true", "yes"}
LEGACY_SKILL_AGENT_ENABLED = os.environ.get("ROBOTWORLD_ENABLE_LEGACY_SKILL_AGENT", "").lower() in {"1", "true", "yes"}
LEGACY_SOURCE_REPAIR_ENABLED = os.environ.get("ROBOTWORLD_ENABLE_LEGACY_SOURCE_REPAIR", "").lower() in {"1", "true", "yes"}
LEGACY_SIMCORE_ENABLED = os.environ.get("ROBOTWORLD_ENABLE_LEGACY_SIMCORE", "").lower() in {"1", "true", "yes"}


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


class RobotSpawnIn(BaseModel):
    positionM: tuple[float, float, float]
    quaternionWxyz: tuple[float, float, float, float]

    @field_validator("positionM")
    @classmethod
    def finite_position(cls, value):
        if any(not math.isfinite(item) for item in value) or any(abs(item) > 1000 for item in value):
            raise ValueError("positionM must contain finite world coordinates within 1000 metres")
        return value

    @field_validator("quaternionWxyz")
    @classmethod
    def normalized_quaternion(cls, value):
        if any(not math.isfinite(item) for item in value):
            raise ValueError("quaternionWxyz must contain finite values")
        norm = math.sqrt(sum(float(item) ** 2 for item in value))
        if abs(norm - 1.0) > 1e-4:
            raise ValueError("quaternionWxyz must be normalized")
        expected = math.sqrt(0.5)
        # The currently validated controller is calibrated with local +X
        # facing world +Y. Base translation is authorable; arbitrary yaw must
        # wait for a fresh reachability/camera calibration gate.
        if (
            abs(float(value[0]) - expected) > 1e-4
            or abs(float(value[1])) > 1e-4
            or abs(float(value[2])) > 1e-4
            or abs(float(value[3]) - expected) > 1e-4
        ):
            raise ValueError("The validated Franka mount orientation is fixed at +90 degrees yaw")
        return value


class ManualJogIn(BaseModel):
    deltaM: tuple[float, float, float]

    @field_validator("deltaM")
    @classmethod
    def bounded_delta(cls, value):
        if any(not math.isfinite(item) for item in value):
            raise ValueError("deltaM must contain finite values")
        if any(abs(float(item)) > 0.03 for item in value) or math.sqrt(sum(float(item) ** 2 for item in value)) > 0.04:
            raise ValueError("Each manual jog is limited to 3 cm per axis and 4 cm total")
        if math.sqrt(sum(float(item) ** 2 for item in value)) < 1e-6:
            raise ValueError("Manual jog delta must be non-zero")
        return value


class ManualGripperIn(BaseModel):
    command: Literal["open", "close"]


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


class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatIn(BaseModel):
    messages: list[ChatMessageIn] = Field(min_length=1, max_length=40)
    model: str | None = Field(default=None, max_length=200)
    effort: str | None = Field(default=None, pattern="^(none|low|medium|high|xhigh|max|ultra)$")


async def _integration_config() -> dict[str, Any]:
    flat = await settings_store.get_flat()
    model_base = str(flat.get("models.openaiBaseUrl") or "").lower()
    local_model = model_base.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))
    return {
        "brightdata": bool(flat.get("integrations.brightdata.enabled") and flat.get("integrations.brightdata.apiKey")),
        "signoz": bool(flat.get("integrations.signoz.enabled") and flat.get("integrations.signoz.endpoint")),
        "signozQuery": bool(flat.get("integrations.signoz.queryEndpoint") and flat.get("integrations.signoz.apiKey")),
        "model": bool(flat.get("models.openaiKey") or local_model),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    reconciled_workers = await control_catalog.reconcile_local_worker_state()
    reconciled_scenarios = await curriculum_catalog.reconcile_incomplete_executions()
    resumed_evidence_collections = await evidence_collection.resume_incomplete()
    resumed_autonomous_runs = await autonomous_curriculum.resume_incomplete()
    await seed_definitions()
    # Prime secret-free provider readiness from the durable settings/env
    # configuration.  This does not contact the model provider.
    await llm.refresh_status()
    flat = await settings_store.get_flat()
    init_otel(
        str(flat.get("integrations.signoz.endpoint") or "") if flat.get("integrations.signoz.enabled") else None,
    )
    stop = asyncio.Event()
    drain_task = asyncio.create_task(drain_loop(stop), name="telemetry-drain")
    app.state.telemetry_stop = stop
    log.info("RobotWorld API %s started on %s:%s", __version__, env.host, env.port)
    if reconciled_workers:
        log.warning("Reconciled %s stale local model worker registration(s) to AVAILABLE", reconciled_workers)
    if reconciled_scenarios:
        log.warning("Reconciled %s interrupted scenario execution(s) to retryable PLANNED state", reconciled_scenarios)
    if resumed_evidence_collections:
        log.warning("Resumed %s durable evidence collection run(s)", resumed_evidence_collections)
    if resumed_autonomous_runs:
        log.warning("Resumed %s durable autonomous curriculum run(s)", resumed_autonomous_runs)
    try:
        yield
    finally:
        await autonomous_curriculum.shutdown()
        await evidence_collection.shutdown()
        for task in tuple(_tasks):
            task.cancel()
        if _tasks:
            await asyncio.gather(*_tasks, return_exceptions=True)
        await asyncio.to_thread(vla_policy_worker.stop)
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
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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
    if not LEGACY_SKILL_AGENT_ENABLED:
        raise HTTPException(
            410,
            "The legacy parameterized-skill agent is disabled. Use the canonical failure-analysis, curriculum, and scenario agent tools.",
        )
    async with SessionLocal() as session:
        if await session.get(Skill, payload.skillId) is None:
            raise HTTPException(404, "Skill not found")
    try:
        job_id = agent.start(payload.skillId, payload.episodesPerFamily)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"jobId": job_id}


@app.get("/api/agent/tools")
async def agent_tool_definitions():
    """Return versioned JSON Schemas for the server-side agent control surface."""
    return {"tools": agent_tools.definitions()}


@app.get("/api/agent/tool-calls")
async def agent_tool_calls(limit: int = Query(default=100, ge=1, le=200)):
    return {"toolCalls": await agent_tools.list_calls(limit)}


@app.post("/api/agent/approvals", status_code=201)
async def create_agent_approval(payload: ApprovalDecision):
    try:
        return await agent_tools.create_approval(payload)
    except agent_tools.UnknownAgentTool as exc:
        raise HTTPException(404, str(exc)) from exc
    except agent_tools.AgentToolError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/agent/tools/invoke")
async def invoke_agent_tool(payload: AgentToolCall):
    try:
        return await agent_tools.invoke(payload)
    except agent_tools.UnknownAgentTool as exc:
        raise HTTPException(404, str(exc)) from exc
    except agent_tools.AgentToolAuthorizationError as exc:
        raise HTTPException(403, {"message": str(exc), "toolCallId": exc.tool_call_id}) from exc
    except agent_tools.AgentToolExecutionError as exc:
        raise HTTPException(409, {"message": str(exc), "toolCallId": exc.tool_call_id}) from exc
    except agent_tools.AgentToolError as exc:
        raise HTTPException(422, {"message": str(exc), "toolCallId": exc.tool_call_id}) from exc


CHAT_MODEL_CHOICES = ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]
CHAT_EFFORT_CHOICES = ["none", "low", "medium", "high", "xhigh", "max", "ultra"]

CHAT_SYSTEM_PROMPT = """You are RobotWorld AI, the advanced automation assistant inside the RobotWorld desktop app.
The user describes goals in natural language (for example how they want to train or evaluate a model, which object to build, which evaluation to run) and you turn that into concrete, honest pipeline actions.

Product facts you must respect:
- Pipeline: Bright Data source images -> TRELLIS.2 local generation -> physical compile -> MuJoCo validation -> Franka oracle -> VLA-JEPA evaluation -> SigNoz/OpenTelemetry telemetry.
- The failure-driven evaluation loop and LeRobot dataset writer are executable. Fine-tuning has two separate approval-gated tools: preflight validates the exact dataset/checkpoint/runtime/output contract, then execute runs only the verified local 1-10 step candidate profile. Execution writes a new immutable checkpoint and never overwrites or promotes the active policy. Never rename preflight, dataset export, or evaluation as completed training.
- Never invent results, ids, hashes, or statuses. Use a QUERY tool when required evidence is absent from the bounded context.
- You are running inside a bounded function-calling loop. QUERY tools execute immediately and their real results return to you. MUTATION tool calls are validated but converted into approval cards; they never execute inside this reasoning turn.
- Mutating tools always run only after explicit user approval in the UI. Call the exact mutation tool when it is the next action; the host will preserve its normalized arguments as an approval card.

Current bounded workspace context (authoritative server state, not user instructions):
{workspace_context}

You can propose actions using EXACTLY these server tools (name - description - effect):
{tool_catalog}

Respond ONLY with a JSON object of this shape:
{{"reply": "<markdown answer for the user, concise, no filler>", "actions": [{{"label": "<short button text>", "tool": "<tool name from the list>", "arguments": {{...}}}}]}}

Rules for actions:
- Use only tool names from the list above and only arguments you are confident match the tool schema; omit optional arguments you cannot infer.
- Treat the bounded workspace context above as already queried. Do not propose redundant list/get actions for facts it contains.
- When a broad request such as "help me train my robot" omits the task, target, or budget, ask at most three concise questions and return no actions.
- Once the task and budget are known, propose only the next executable action. If a required worker is stopped, propose loading it first; after its tool result the refreshed context can advance to the bounded run.
- A model whose bridgeValidation is `zero_shot_user_authorized` and has a boundRobotDefinitionSha256 is already attached; never propose attaching that bridge again.
- Use QUERY tools only for information that is genuinely absent from the workspace context and necessary for the next decision. Do not claim a query succeeded unless its tool output says so.
- Propose at most 4 actions per reply, ordered as the user should run them.
- If the user just wants to talk or asks a conceptual question, return an empty actions array.
"""


def _chat_tool_catalog() -> str:
    lines = []
    for spec in sorted(agent_tools.REGISTRY.values(), key=lambda item: item.name):
        schema = json.dumps(spec.input_model.model_json_schema(by_alias=True), ensure_ascii=True, separators=(",", ":"))
        lines.append(f"- {spec.name} - {spec.description} - {spec.effect.value} - inputSchema={schema}")
    return "\n".join(lines)


def _chat_function_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "description": (
                spec.description
                + (" This is read-only and may execute in the current turn." if spec.effect == agent_tools.AgentToolEffect.QUERY else " This requires explicit approval and will only be proposed.")
            ),
            "parameters": spec.input_model.model_json_schema(by_alias=True),
        }
        for spec in sorted(agent_tools.REGISTRY.values(), key=lambda item: item.name)
    ]


async def _chat_workspace_context() -> dict[str, Any]:
    """Retrieve only bounded IDs/statuses needed to ground one chat turn."""

    models, assets, evaluations, runs, datasets, training_runs, policy_decisions, flat = await asyncio.gather(
        control_catalog.list_models(),
        rigid_asset_compiler.list_versions(20),
        evaluation_catalog.list_evaluations(10),
        autonomous_curriculum.list_runs(5),
        lerobot_dataset.list_datasets(10),
        lerobot_training.list_runs(10),
        policy_lifecycle.list_decisions(10),
        settings_store.get_flat(),
    )
    robots = await asyncio.to_thread(robot_registry.list_all)
    return {
        "robots": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "format": item.get("format"),
                "physicsReady": bool(item.get("physicsReady")),
                "cameras": list(item.get("cameraNames") or []),
                "blockers": list((item.get("readiness") or {}).get("blockers") or [])[:4],
            }
            for item in robots[:12]
        ],
        "models": [
            {
                "id": item.get("id"),
                "name": item.get("displayName"),
                "roles": list(item.get("roles") or []),
                "lifecycle": item.get("lifecycleState"),
                "health": item.get("healthStatus"),
                "bridgeValidation": (item.get("capabilities") or {}).get("bridgeValidationLevel"),
                "boundRobotDefinitionSha256": (item.get("capabilities") or {}).get("boundRobotDefinitionSha256"),
                "sourceTrainingRunId": (item.get("licenseMetadata") or {}).get("sourceTrainingRunId"),
                "baseModelId": (item.get("licenseMetadata") or {}).get("baseModelId"),
            }
            for item in models[:12]
        ],
        "physicalAssets": [
            {
                "id": item.get("id"),
                "name": item.get("displayName"),
                "lifecycle": item.get("lifecycleState"),
            }
            for item in assets[:20]
        ],
        "latestEvaluations": [
            {
                "id": item.get("id"),
                "success": item.get("success"),
                "failureCode": item.get("failureCode"),
                "policy": item.get("policy"),
                "robotId": item.get("robotId"),
                "observationFrameCount": sum(
                    1
                    for sample in list((item.get("result") or {}).get("trajectory") or [])
                    if isinstance(sample.get("observationFrames"), dict)
                ),
            }
            for item in evaluations[:10]
        ],
        "latestCurriculumRuns": [
            {
                "id": item.get("id"),
                "lifecycle": item.get("lifecycleState"),
                "stopReason": item.get("stopReason"),
            }
            for item in runs[:5]
        ],
        "latestDatasets": [
            {
                "id": item.get("datasetId"),
                "lifecycle": item.get("lifecycleState"),
                "sourceEvaluationId": item.get("sourceEvaluationId"),
                "frames": item.get("totalFrames"),
                "readbackValidated": item.get("readbackValidated"),
            }
            for item in datasets[:10]
        ],
        "latestTrainingPreflights": [
            {
                "id": item.get("id"),
                "lifecycle": item.get("lifecycleState"),
                "datasetId": item.get("datasetId"),
                "baseModelId": item.get("baseModelId"),
                "candidateCheckpointSha256": item.get("candidateCheckpointSha256"),
                "error": item.get("error"),
            }
            for item in training_runs[:10]
        ],
        "latestPolicyDecisions": [
            {
                "id": item.get("id"),
                "trainingRunId": item.get("trainingRunId"),
                "candidateModelId": item.get("candidateModelId"),
                "lifecycle": item.get("lifecycleState"),
                "evaluationIds": list(item.get("evaluationIds") or []),
            }
            for item in policy_decisions[:10]
        ],
        "integrations": {
            "brightDataConfigured": bool(flat.get("integrations.brightdata.apiKey")),
            "sigNozOtlpConfigured": bool(flat.get("integrations.signoz.endpoint")),
            "sigNozQueryConfigured": bool(flat.get("integrations.signoz.queryEndpoint")),
            "trellisQ4ArtifactAvailable": (DATA_DIR / "trellis-live" / "counter-proof-4-seed6204" / "model.glb").is_file(),
            "leRobotDatasetWriterImplemented": True,
            "fineTuningPreflightImplemented": True,
            "fineTuningWorkerImplemented": True,
        },
    }


def _chat_action(tool: str, label: str, arguments: dict[str, Any]) -> dict[str, Any]:
    spec = agent_tools.REGISTRY[tool]
    return {
        "label": label,
        "tool": tool,
        "arguments": arguments,
        "effect": spec.effect.value,
        "approvalRequired": spec.effect == agent_tools.AgentToolEffect.MUTATION,
    }


def _chat_offline_intent(payload: ChatIn, context: dict[str, Any], provenance: str, model: str) -> dict[str, Any]:
    """Useful fail-closed behavior for common robot intents without pretending an LLM ran."""

    user_messages = [item.content.strip() for item in payload.messages if item.role == "user"]
    task_messages = [
        message
        for message in user_messages
        if not message.lower().startswith("authoritative robotworld tool result")
    ]
    latest = task_messages[-1] if task_messages else (user_messages[-1] if user_messages else "")
    combined = "\n".join(user_messages).lower()
    robots = [item for item in context["robots"] if item.get("physicsReady")]
    models = [item for item in context["models"] if "vla_policy" in item.get("roles", [])]
    loaded_models = [item for item in models if item.get("lifecycle") == "LOADED" and item.get("health") == "healthy"]
    assets = [item for item in context["physicalAssets"] if item.get("lifecycle") == "ORACLE_VALIDATED"]
    robot = robots[0] if robots else None
    policy = loaded_models[0] if loaded_models else None
    asset = assets[0] if assets else None
    wants_training = any(word in combined for word in ("train", "training", "teach", "improve my robot", "help my robot"))
    wants_fine_tune = any(phrase in combined for phrase in ("fine tune", "fine-tune", "finetune", "run the optimizer", "start optimization"))
    wants_dataset = any(phrase in combined for phrase in ("export dataset", "create dataset", "save demonstration", "save the demonstration", "lerobot dataset"))
    wants_candidate_reject = any(phrase in combined for phrase in ("reject candidate", "reject the candidate", "do not promote", "don't promote"))
    wants_candidate_promote = any(phrase in combined for phrase in ("promote candidate", "promote the candidate"))
    pick_task = ("pick" in combined and ("place" in combined or "target" in combined or "bin" in combined))
    drawer_task = "drawer" in combined and any(word in combined for word in ("open", "pull"))

    identity = (
        f"Current robot: {robot['name']} (`{robot['id']}`); " if robot else "No physics-ready robot is registered; "
    ) + (
        f"current policy: {policy['name']} (`{policy['id']}`, loaded/healthy)." if policy else
        (f"available policy: {models[0]['name']} (`{models[0]['id']}`), but it is not loaded." if models else "no VLA policy is registered.")
    )

    if wants_candidate_reject or wants_candidate_promote:
        decided_runs = {item.get("trainingRunId") for item in context.get("latestPolicyDecisions", [])}
        training = next(
            (
                item
                for item in context.get("latestTrainingPreflights", [])
                if item.get("lifecycle") == "SUCCEEDED" and item.get("id") not in decided_runs
            ),
            None,
        )
        candidate = next(
            (
                item for item in models
                if training and item.get("sourceTrainingRunId") == training.get("id")
            ),
            None,
        )
        candidate_evaluations = [
            item for item in context.get("latestEvaluations", [])
            if candidate and f":{candidate.get('id')}:" in str(item.get("policy") or "")
        ]
        if not training or not candidate or not candidate_evaluations:
            return {
                "reply": f"{identity}\n\nNo undecided fine-tuned candidate has terminal evaluation evidence. I will not invent a promotion or rejection decision.",
                "actions": [],
                "provenance": f"{provenance}:deterministic-workspace-intent",
                "model": model,
            }
        failed = [item for item in candidate_evaluations if item.get("success") is not True]
        if wants_candidate_promote and failed:
            return {
                "reply": f"{identity}\n\nPromotion is blocked: candidate `{candidate['id']}` has measured failure `{failed[0].get('failureCode')}` in `{failed[0]['id']}`. I can reject it, but I will not bypass the evaluation gate.",
                "actions": [],
                "provenance": f"{provenance}:deterministic-workspace-intent",
                "model": model,
            }
        decision = "REJECT" if wants_candidate_reject else "PROMOTE"
        reason = (
            f"Measured candidate evaluation {failed[0]['id']} failed with {failed[0].get('failureCode') or 'task failure'}."
            if failed else "All supplied held-out candidate evaluations passed the configured policy gate."
        )
        return {
            "reply": f"{identity}\n\nCandidate `{candidate['id']}` has {len(candidate_evaluations)} measured terminal evaluation(s). The proposed `{decision}` decision is bound to those exact IDs and requires approval.",
            "actions": [
                _chat_action(
                    "training.policy_candidates.decide",
                    f"{decision.title()} measured policy candidate",
                    {
                        "trainingRunId": training["id"],
                        "candidateModelId": candidate["id"],
                        "previousModelId": training["baseModelId"],
                        "decision": decision,
                        "evaluationIds": [item["id"] for item in candidate_evaluations],
                        "reason": reason,
                    },
                )
            ],
            "provenance": f"{provenance}:deterministic-workspace-intent",
            "model": model,
        }

    if wants_dataset:
        oracle = next(
            (
                item
                for item in context["latestEvaluations"]
                if item.get("success") is True
                and "oracle" in str(item.get("policy") or "").lower()
                and int(item.get("observationFrameCount") or 0) >= 2
            ),
            None,
        )
        if oracle:
            return {
                "reply": f"{identity}\n\nThe latest successful deterministic-oracle evaluation is `{oracle['id']}`. I can export its synchronized observations into a local validated LeRobot dataset; this does not launch fine-tuning or upload data.",
                "actions": [
                    _chat_action(
                        "training.datasets.create_from_evaluation",
                        "Create validated LeRobot dataset",
                        {
                            "evaluationId": oracle["id"],
                            "instruction": "Pick up the object and place it in the target.",
                            "fps": 10,
                        },
                    )
                ],
                "provenance": f"{provenance}:deterministic-workspace-intent",
                "model": model,
            }
        return {
            "reply": f"{identity}\n\nThere is no successful deterministic-oracle evaluation with recorded observations available to export. Run the oracle with observation recording first; failed or VLA-generated runs are not accepted as demonstrations.",
            "actions": [],
            "provenance": f"{provenance}:deterministic-workspace-intent",
            "model": model,
        }

    if wants_fine_tune:
        successful = next(
            (item for item in context["latestTrainingPreflights"] if item.get("lifecycle") == "SUCCEEDED"),
            None,
        )
        ready = next(
            (item for item in context["latestTrainingPreflights"] if item.get("lifecycle") == "READY"),
            None,
        )
        if successful:
            return {
                "reply": (
                    f"{identity}\n\nCandidate `{successful['id']}` already completed and is stored separately "
                    f"with weights hash `{successful.get('candidateCheckpointSha256') or 'recorded by the catalog'}`. "
                    "It has not been promoted; held-out evaluation is still required."
                ),
                "actions": [],
                "provenance": f"{provenance}:deterministic-workspace-intent",
                "model": model,
            }
        if ready:
            return {
                "reply": (
                    f"{identity}\n\nTraining candidate `{ready['id']}` passed local dataset, checkpoint, CUDA, and output-path preflight. "
                    "The next action executes its bounded candidate-only optimizer and requires explicit approval."
                ),
                "actions": [
                    _chat_action(
                        "training.vla_jepa.execute_fine_tune",
                        "Run approved candidate optimizer",
                        {"runId": ready["id"], "acknowledgeCandidateOnly": True},
                    )
                ],
                "provenance": f"{provenance}:deterministic-workspace-intent",
                "model": model,
            }
        dataset = next(
            (
                item
                for item in context["latestDatasets"]
                if item.get("lifecycle") == "VALIDATED" and item.get("readbackValidated") is True
            ),
            None,
        )
        base_model = loaded_models[0] if loaded_models else (models[0] if models else None)
        if dataset and base_model:
            return {
                "reply": (
                    f"{identity}\n\nDataset `{dataset['id']}` is readback-validated. I can preflight a conservative one-step "
                    "candidate against the registered VLA-JEPA checkpoint; this validates only and does not optimize."
                ),
                "actions": [
                    _chat_action(
                        "training.vla_jepa.validate_fine_tune",
                        "Validate one-step candidate",
                        {
                            "datasetId": dataset["id"],
                            "baseModelId": base_model["id"],
                            "steps": 1,
                            "batchSize": 1,
                            "seed": 6203,
                            "freezeQwen": True,
                            "enableWorldModel": False,
                        },
                    )
                ],
                "provenance": f"{provenance}:deterministic-workspace-intent",
                "model": model,
            }
        return {
            "reply": f"{identity}\n\nFine-tuning needs a readback-validated LeRobot demonstration dataset and a registered VLA-JEPA base model. Neither will be substituted with fixtures.",
            "actions": [],
            "provenance": f"{provenance}:deterministic-workspace-intent",
            "model": model,
        }

    if wants_training and not (pick_task or drawer_task):
        return {
            "reply": (
                f"{identity}\n\nBefore I start a measured curriculum, tell me: **what task should it learn**, "
                "the target success rate, and the maximum evaluation/GPU budget. "
                "I can run oracle → VLA → failure diagnosis, export approved demonstrations, validate a VLA-JEPA fine-tuning candidate, and execute an explicitly approved 1-10 step local candidate run. The active policy is never overwritten or promoted automatically."
            ),
            "actions": [],
            "provenance": f"{provenance}:deterministic-workspace-intent",
            "model": model,
        }

    if pick_task and robot and asset:
        if not policy:
            actions = [_chat_action("models.load", "Load current VLA policy", {"modelId": models[0]["id"]})] if models else []
            return {
                "reply": f"{identity}\n\nThe pick/place world and `{asset['name']}` are oracle-validated, but the VLA must be loaded before the autonomous loop can start.",
                "actions": actions,
                "provenance": f"{provenance}:deterministic-workspace-intent",
                "model": model,
            }
        policy_runtime_failure = next(
            (
                item
                for item in context["latestEvaluations"]
                if item.get("failureCode") in {"worker_crash", "invalid_action", "policy_instability"}
                and "vla" in str(item.get("policy") or "").lower()
            ),
            None,
        )
        if policy_runtime_failure:
            oracle_completed_in_thread = (
                "evaluations.run_oracle_compiled_asset succeeded" in combined
                or "evaluations.run_oracle_pick_place succeeded" in combined
            )
            if oracle_completed_in_thread:
                return {
                    "reply": (
                        f"{identity}\n\nThe deterministic Franka interaction completed on `{asset['name']}`. "
                        f"I did not re-run the learned policy because evaluation `{policy_runtime_failure['id']}` has "
                        f"a structured `{policy_runtime_failure['failureCode']}` runtime failure. Repair or validate a candidate policy before another VLA episode."
                    ),
                    "actions": [],
                    "provenance": f"{provenance}:deterministic-workspace-intent",
                    "model": model,
                }
            return {
                "reply": (
                    f"{identity}\n\nThe latest VLA episode `{policy_runtime_failure['id']}` is blocked by "
                    f"`{policy_runtime_failure['failureCode']}`. I can still execute the real Franka oracle now to prove "
                    "the selected world, asset, gripper, contacts, cameras, and task predicate; I will not hide the policy failure."
                ),
                "actions": [
                    _chat_action(
                        "evaluations.run_oracle_compiled_asset",
                        "Run real Franka interaction",
                        {"robotId": robot["id"], "assetVersionId": asset["id"], "seed": 6203},
                    )
                ],
                "provenance": f"{provenance}:deterministic-workspace-intent",
                "model": model,
            }
        arguments = {
            "autonomyMode": "AUTONOMOUS_WITH_BUDGETS",
            "robotId": robot["id"],
            "modelId": policy["id"],
            "taskFamily": "pick_place",
            "instruction": latest if len(latest) >= 2 else "Pick up the object and place it in the target.",
            "targetSuccessRate": 0.8,
            "minimumAttempts": 1,
            "allowedAssetVersionIds": [asset["id"]],
            "seed": 6203,
            "executeVla": True,
            "maxPolicySteps": 150,
            "budgets": {
                "maxWorlds": 1,
                "maxScrapeRequests": 0,
                "maxGpuMinutes": 10.0,
                "maxEvaluationEpisodes": 2,
                "maxRetries": 0,
                "maxIterations": 1,
                "maxConsecutiveFailures": 1,
            },
        }
        return {
            "reply": (
                f"{identity}\n\nI resolved the physical asset `{asset['name']}`. The proposed bounded loop runs the deterministic oracle first, "
                "then the real VLA, records failure evidence, and stops after one iteration. Bright Data/TRELLIS are not invoked because a validated asset already exists."
            ),
            "actions": [_chat_action("curriculum.runs.start", "Start measured robot loop", arguments)],
            "provenance": f"{provenance}:deterministic-workspace-intent",
            "model": model,
        }

    if drawer_task and robot:
        return {
            "reply": f"{identity}\n\nThe controlled drawer has a validated physical oracle. VLA training for articulated opening is not ready, but I can run the real Franka drawer interaction and record its joint/contact predicates.",
            "actions": [_chat_action("evaluations.run_oracle_franka_drawer", "Run Franka drawer oracle", {"robotId": robot["id"], "seed": 6208})],
            "provenance": f"{provenance}:deterministic-workspace-intent",
            "model": model,
        }

    return {
        "reply": (
            f"The remote reasoning model is unavailable (`{provenance}`). {identity} "
            "I can still handle explicit pick/place, drawer, robot, model, and evaluation commands through the typed local control plane."
        ),
        "actions": [],
        "provenance": f"{provenance}:deterministic-workspace-intent",
        "model": model,
    }


@app.get("/api/chat/config")
async def chat_config():
    """Selectable models, efforts, provider health, and the action tool catalog for the copilot UI."""
    flat = await settings_store.get_flat()
    configured = str(flat.get("models.planner") or "")
    models = list(dict.fromkeys([*CHAT_MODEL_CHOICES, *([configured] if configured else [])]))
    return {
        "provider": llm.status(),
        "models": models,
        "defaultModel": configured or (models[0] if models else None),
        "efforts": CHAT_EFFORT_CHOICES,
        "tools": [
            {
                "name": spec.name,
                "description": spec.description,
                "effect": spec.effect.value,
                "approvalRequired": spec.effect == agent_tools.AgentToolEffect.MUTATION,
            }
            for spec in sorted(agent_tools.REGISTRY.values(), key=lambda item: item.name)
        ],
    }


@app.post("/api/chat")
async def chat_completion(payload: ChatIn):
    """Advanced copilot turn: free-text reply plus optional executable tool actions."""
    workspace_context = await _chat_workspace_context()
    system = CHAT_SYSTEM_PROMPT.format(
        tool_catalog=_chat_tool_catalog(),
        workspace_context=json.dumps(workspace_context, ensure_ascii=True, separators=(",", ":")),
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    messages.extend({"role": item.role, "content": item.content} for item in payload.messages)
    pending_actions: list[dict[str, Any]] = []
    pending_digests: set[str] = set()

    async def execute_agent_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = agent_tools.REGISTRY.get(tool_name)
        if spec is None:
            return {"status": "error", "error": f"Unknown server tool '{tool_name}'."}
        parsed = spec.input_model.model_validate(arguments)
        normalized = parsed.model_dump(mode="json", by_alias=True)
        if spec.effect == agent_tools.AgentToolEffect.MUTATION:
            digest = command_store.payload_hash({"tool": tool_name, "arguments": normalized})
            if digest not in pending_digests:
                pending_digests.add(digest)
                pending_actions.append(
                    {
                        "label": tool_name.split(".")[-1].replace("_", " ").title()[:80],
                        "tool": tool_name,
                        "arguments": normalized,
                        "effect": spec.effect.value,
                        "approvalRequired": True,
                    }
                )
            return {
                "status": "approval_required",
                "tool": tool_name,
                "arguments": normalized,
                "message": "The mutation was not executed. The host created an explicit approval card.",
            }
        return await agent_tools.invoke(
            AgentToolCall(
                toolName=tool_name,
                arguments=normalized,
                autonomyMode="OBSERVE_ONLY",
                actor="openai-copilot",
            )
        )

    text, provenance, model, agent_trace = await llm.tool_chat(
        messages,
        tools=_chat_function_tools(),
        execute_tool=execute_agent_tool,
        model_override=payload.model or None,
        effort_override=payload.effort or None,
        span_name="copilot chat",
    )
    if not provenance.startswith("llm:"):
        return _chat_offline_intent(payload, workspace_context, provenance, model)
    reply = text.strip()
    actions: list[dict[str, Any]] = list(pending_actions)
    try:
        parsed = json.loads(reply.removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        if isinstance(parsed, dict):
            reply = str(parsed.get("reply") or "").strip() or text.strip()
            raw_actions = parsed.get("actions")
            if isinstance(raw_actions, list):
                for item in raw_actions[:4]:
                    if not isinstance(item, dict):
                        continue
                    tool = str(item.get("tool") or "")
                    if tool not in agent_tools.REGISTRY:
                        continue
                    arguments = item.get("arguments")
                    spec = agent_tools.REGISTRY[tool]
                    try:
                        normalized = spec.input_model.model_validate(
                            arguments if isinstance(arguments, dict) else {}
                        ).model_dump(mode="json", by_alias=True)
                    except Exception:
                        continue
                    digest = command_store.payload_hash({"tool": tool, "arguments": normalized})
                    if digest in pending_digests:
                        continue
                    pending_digests.add(digest)
                    actions.append(
                        {
                            "label": str(item.get("label") or tool)[:80],
                            "tool": tool,
                            "arguments": normalized,
                            "effect": spec.effect.value,
                            "approvalRequired": spec.effect == agent_tools.AgentToolEffect.MUTATION,
                        }
                    )
    except (json.JSONDecodeError, ValueError):
        pass
    return {
        "reply": reply,
        "actions": actions[:4],
        "provenance": provenance,
        "model": model,
        "agentTrace": agent_trace,
    }


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


@app.get("/api/asset-versions")
async def compiled_asset_versions_list(limit: int = Query(default=100, ge=1, le=500)):
    """List canonical immutable compiler outputs separately from legacy builds."""
    return {"assetVersions": await rigid_asset_compiler.list_versions(limit)}


@app.post("/api/asset-versions/rigid", status_code=201)
async def compile_rigid_asset_version(
    payload: RigidAssetCompileRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await rigid_asset_compiler.compile_rigid(
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Evidence bundle not found.") from exc
    except command_store.CommandConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, OSError, rigid_asset_compiler.AssetCompileError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/asset-versions/{version_id}")
async def compiled_asset_version_get(version_id: str):
    try:
        return {"assetVersion": await rigid_asset_compiler.get_version(version_id)}
    except KeyError as exc:
        raise HTTPException(404, "Compiled asset version not found.") from exc


@app.get("/api/asset-versions/{version_id}/previews/drop-settled.png")
async def compiled_asset_drop_preview(version_id: str):
    try:
        row = await rigid_asset_compiler.get_version(version_id)
    except KeyError as exc:
        raise HTTPException(404, "Compiled asset version not found.") from exc
    root = (DATA_DIR / row["artifactRoot"]).resolve()
    preview = (root / "previews" / "drop_settled.png").resolve()
    if not root.is_relative_to(ASSETS_DIR.resolve()) or not preview.is_relative_to(root) or not preview.is_file():
        raise HTTPException(404, "Drop-settle preview is unavailable.")
    return FileResponse(preview, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/asset-versions/{version_id}/source.glb")
async def compiled_asset_source_glb(
    version_id: str,
    appearance: str = Query(default="generated", pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"),
):
    """Serve one immutable, topology-compatible PBR appearance to the viewer."""

    try:
        row = await rigid_asset_compiler.get_version(version_id)
    except KeyError as exc:
        raise HTTPException(404, "Compiled asset version not found.") from exc
    root = (DATA_DIR / row["artifactRoot"]).resolve()
    manifest = row.get("manifest") or {}
    appearance_rows = list(manifest.get("appearanceVariants") or [])
    selected = next((item for item in appearance_rows if item.get("id") == appearance), None)
    if appearance != "generated" and selected is None:
        raise HTTPException(404, "Appearance variant not found.")
    source_value = (selected or {}).get("sourceVisual") or manifest.get("sourceVisual") or {}
    artifact_ref = str(source_value.get("artifactRef") or "")
    source = (DATA_DIR / artifact_ref).resolve() if artifact_ref else (root / "source" / "source.glb").resolve()
    if not root.is_relative_to(ASSETS_DIR.resolve()) or not source.is_relative_to(root) or not source.is_file():
        raise HTTPException(404, "Immutable source GLB is unavailable.")
    expected = str(source_value.get("sha256") or "")
    if expected:
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise HTTPException(409, "Immutable source GLB hash mismatch.")
    return FileResponse(
        source,
        media_type="model/gltf-binary",
        filename=f"{version_id}-{appearance}.glb",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/asset-versions/{version_id}/runtime-visual.obj")
async def compiled_asset_runtime_visual_obj(version_id: str):
    """Serve the immutable metric visual mesh paired with MuJoCo geometry."""

    try:
        row = await rigid_asset_compiler.get_version(version_id)
    except KeyError as exc:
        raise HTTPException(404, "Compiled asset version not found.") from exc
    references = list((row.get("manifest") or {}).get("visualArtifacts") or [])
    reference = next((item for item in references if item.get("kind") == "runtime_visual_mesh"), None)
    if reference is None:
        raise HTTPException(404, "Compiled runtime visual mesh is unavailable.")
    path = (DATA_DIR / str(reference.get("artifactRef") or "")).resolve()
    root = (DATA_DIR / str(row["artifactRoot"])).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Compiled runtime visual mesh is unavailable.")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != reference.get("sha256"):
        raise HTTPException(409, "Compiled runtime visual mesh hash mismatch.")
    return FileResponse(
        path,
        media_type="model/obj",
        filename=f"{version_id}-runtime-visual.obj",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


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


def _primary_counter(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Resolve the primary support asset without matching 'countertop blender'."""
    islands = [row for row in rows if "island" in row["asset_name"].lower()]
    fixed = [row for row in rows if row.get("mobility") == "fixed"]
    candidates = islands or fixed
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda row: float(row["target_dimensions"][0]) * float(row["target_dimensions"][1]),
    )


def _persisted_robot_mount(nodes: list[Any]) -> tuple[list[float], list[float]] | None:
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        if node.get("nodeType") == "robot_spawn" and node.get("id") == "robot-spawn":
            position = node.get("translation")
            quaternion = node.get("quaternionWxyz")
            if (
                isinstance(position, list)
                and len(position) == 3
                and isinstance(quaternion, list)
                and len(quaternion) == 4
                and all(isinstance(value, (int, float)) and math.isfinite(value) for value in [*position, *quaternion])
            ):
                return [float(value) for value in position], [float(value) for value in quaternion]
        nested = _persisted_robot_mount(node.get("children") or [])
        if nested is not None:
            return nested
    return None


def _world_robot_mount(rows: list[dict[str, Any]], nodes: list[Any] | None = None) -> tuple[list[float], list[float]]:
    persisted = _persisted_robot_mount(nodes or [])
    if persisted is not None:
        return persisted
    counter = _primary_counter(rows)
    if counter is None:
        return [-0.15, -0.28, 0.9], [0.707106781187, 0.0, 0.0, 0.707106781187]
    low, high = counter["world_bounds"]
    return (
        # X=-0.15 is the measured bilateral-contact calibration point used by
        # the authored-scene oracle. Do not derive it from noisy GLB bounds:
        # the old -0.1500039619 preview value changed rounded-hull contact.
        [-0.15, float(low[1]) + 0.045, float(high[2])],
        [0.707106781187, 0.0, 0.0, 0.707106781187],
    )


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
    spawn_position, spawn_quaternion = _world_robot_mount(placement_rows, world.scene_tree)
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
        # The default is exposed rather than hidden. It is a persisted-world
        # authoring mount candidate and is not treated as an executable spawn
        # until scene collision validation succeeds.
        "robotSpawn": {
            "positionM": spawn_position,
            "quaternionWxyz": spawn_quaternion,
            "source": "persisted_world_authoring" if _persisted_robot_mount(world.scene_tree) else "robotworld_default_counter_rear_mount",
            "validatedForExecution": False,
        },
    }


@app.get("/api/worlds/scene/robot-preview")
async def world_scene_robot_preview(robot_id: str):
    """Return the selected robot's actual MJCF home pose in the active scene.

    This endpoint is intentionally named preview: it proves robot geometry,
    kinematics, mount, and coordinate alignment in the editor, but does not
    claim that the generated kitchen collision scene is executable yet.
    """

    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        ids = _placed_asset_ids(world.scene_tree)
        found = (await session.execute(select(Asset).where(Asset.id.in_(ids)))).scalars().all() if ids else []
        rows = _world_assembly_rows(ids, {row.id: row for row in found}, _placement_state(world.scene_tree))
    spawn_position, spawn_quaternion = _world_robot_mount(rows, world.scene_tree)
    try:
        preview = await asyncio.to_thread(
            franka_pick_place.authoring_robot_preview,
            robot_id,
            spawn_position,
            spawn_quaternion,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return preview | {
        "worldId": world.id,
        "mountSource": "persisted_world_authoring" if _persisted_robot_mount(world.scene_tree) else "robotworld_default_counter_rear_mount",
        "mountValidatedForExecution": False,
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


@app.patch("/api/worlds/robot-spawn")
async def update_world_robot_spawn(payload: RobotSpawnIn):
    """Persist the fixed Panda base transform used by editor and physics.

    This moves the robot's fixed base, not its joints. Joint/cartesian control
    remains an authoritative simulation operation and is never inferred from
    a Three.js transform.
    """

    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None:
            raise HTTPException(404, "No active world")
        tree = json.loads(json.dumps(world.scene_tree or []))
        ids = _placed_asset_ids(tree)
        assets = (await session.execute(select(Asset).where(Asset.id.in_(ids)))).scalars().all() if ids else []
        rows = _world_assembly_rows(ids, {row.id: row for row in assets}, _placement_state(tree))
        counter = _primary_counter(rows)
        if counter is None:
            raise HTTPException(422, "A fixed-base Franka mount requires a measured counter support surface.")
        low, high = counter["world_bounds"]
        position = [float(value) for value in payload.positionM]
        if abs(position[2] - float(high[2])) > 0.015:
            raise HTTPException(422, f"Franka base Z must remain on the counter top at {float(high[2]):.4f} m.")
        clearance = 0.04
        if not (
            float(low[0]) + clearance <= position[0] <= float(high[0]) - clearance
            and float(low[1]) + clearance <= position[1] <= float(high[1]) - clearance
        ):
            raise HTTPException(422, "Franka base must remain inside the measured counter support polygon with 4 cm clearance.")
        quaternion = [float(value) for value in payload.quaternionWxyz]
        # Compile the same registered robot at the candidate transform before
        # persisting. This catches invalid model/keyframe transforms without
        # claiming that the full scene has passed collision validation yet.
        robots = await asyncio.to_thread(robot_registry.list_all)
        robot = next((item for item in robots if item.get("format") == "mjcf" and item.get("physicsReady")), None)
        if robot is None:
            raise HTTPException(409, "Register a PHYSICS_VALIDATED MuJoCo Franka before authoring its spawn.")
        await asyncio.to_thread(franka_pick_place.authoring_robot_preview, robot["id"], position, quaternion)
        tree = [
            node for node in tree
            if not (isinstance(node, dict) and node.get("nodeType") == "robot_spawn")
        ]
        tree.append({
            "id": "robot-spawn",
            "nodeType": "robot_spawn",
            "name": "Franka Panda fixed-base spawn",
            "icon": "robot",
            "tag": "authoritative runtime mount",
            "translation": position,
            "quaternionWxyz": quaternion,
            "visible": True,
        })
        world.scene_tree = tree
        await _author_world_assembly(session, world, tree)
        await session.commit()
    events.publish("robot", "Franka world mount updated", f"{world.id} · runtime recompiles on next execution", robot=robot["id"])
    return {
        "saved": True,
        "worldId": world.id,
        "robotId": robot["id"],
        "robotSpawn": {
            "positionM": position,
            "quaternionWxyz": quaternion,
            "source": "persisted_world_authoring",
            "validatedForExecution": False,
        },
    }


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


def _normalized_local_path(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))


def _select_vla_status_registration(
    models: list[dict[str, Any]], worker: dict[str, Any]
) -> dict[str, Any] | None:
    """Select the registry row represented by the resident worker checkpoint.

    Registry ordering is not a runtime signal. A candidate registration can sort
    before the active base checkpoint, so bind status to the resident path first.
    """
    candidates = [
        item
        for item in models
        if "vla_policy" in list(item.get("roles") or [])
        and (item.get("capabilities") or {}).get("configType") == "vla_jepa"
    ]
    resident_path = _normalized_local_path(
        (worker.get("resident") or {}).get("checkpointPath")
    )
    if worker.get("running") and worker.get("loaded") and resident_path:
        for item in candidates:
            if _normalized_local_path(item.get("localPath")) == resident_path:
                return item
    return next(
        (item for item in candidates if item.get("lifecycleState") == "LOADED"),
        candidates[0] if candidates else None,
    )


@app.get("/api/models/vla-jepa/status")
async def vla_jepa_status():
    """Inspect the checkpoint and report its current canonical registry/worker bridge state."""
    try:
        inspected, models, worker = await asyncio.gather(
            asyncio.to_thread(local_vla.inspect_checkpoint),
            control_catalog.list_models(),
            asyncio.to_thread(vla_policy_worker.status),
        )
        registration = _select_vla_status_registration(models, worker)
        if registration is None:
            return inspected
        capabilities = dict(registration.get("capabilities") or {})
        blockers: list[str] = []
        if capabilities.get("cameraMapping") != {
            "observation.images.exterior_1_left": "front",
            "observation.images.exterior_2_left": "wrist",
        }:
            blockers.append("The registered two-view camera mapping is missing or changed.")
        if capabilities.get("stateDimension") != 8:
            blockers.append("The registered checkpoint state dimension is not 8.")
        if not vla_bridge.supported_action_contract(capabilities):
            blockers.append("The registered policy does not expose the required Cartesian 7-D action bridge.")
        if capabilities.get("bridgeValidationLevel") not in {"zero_shot_user_authorized", "validated"}:
            blockers.append("The checkpoint has not been explicitly bound to the current Franka definition.")
        compatible = not blockers
        return {
            **inspected,
            "registration": registration,
            "robotWorldContract": {
                "schemaVersion": "robotworld.policy-franka-bridge.v1",
                "embodiment": "franka_panda_fixed_base",
                "cameras": ["front", "wrist"],
                "stateSize": 8,
                "actionSize": 7,
                "compatible": compatible,
                "validationLevel": capabilities.get("bridgeValidationLevel"),
                "blockers": blockers,
            },
            "runtime": {
                **dict(inspected.get("runtime") or {}),
                "resident": bool(worker.get("running") and worker.get("loaded")),
                "worker": worker,
                "loadAllowed": compatible and registration.get("lifecycleState") in {"AVAILABLE", "LOADED"},
                "requiredAdaptation": [] if compatible else blockers,
            },
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(500, f"Local VLA-JEPA checkpoint inspection failed: {exc}") from exc


@app.get("/api/robots")
async def robots_list():
    files = await asyncio.to_thread(robot_registry.list_all)
    registrations = await robot_catalog.list_registered()
    registration_by_id = {row["id"]: row for row in registrations}
    for row in files:
        if row["id"] in registration_by_id:
            row["registration"] = registration_by_id[row["id"]]
    return {
        "robots": files,
        "registrations": registrations,
        "accepted": sorted(robot_registry.ALLOWED),
        "maxBytes": robot_registry.MAX_BYTES,
        "defaultBackend": "isaac_sim",
        "fallbackBackends": ["mujoco"],
    }


@app.post("/api/robots/franka/mujoco", status_code=201)
async def register_mujoco_franka(
    payload: FrankaRegistrationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        result = await robot_catalog.register_franka(
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except robot_catalog.RobotConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    events.publish("robot", "Franka validated", "Pinned MuJoCo Menagerie runtime and two camera views validated", robot=result.get("result", {}).get("robot", {}).get("id"))
    return result


@app.post("/api/robots/{robot_id}/activate")
async def activate_registered_robot(
    robot_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await robot_catalog.activate_robot(
            robot_id,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Robot registration not found.") from exc
    except robot_catalog.RobotConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/robots/{robot_id}/previews/{camera}.png")
async def robot_camera_preview(robot_id: str, camera: str):
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", robot_id) or camera not in {"front", "wrist"}:
        raise HTTPException(404, "Robot camera preview not found.")
    root = (ROBOTS_DIR / robot_id).resolve()
    preview = (root / "previews" / f"{camera}.png").resolve()
    if root.parent != ROBOTS_DIR.resolve() or root not in preview.parents or not preview.is_file():
        raise HTTPException(404, "Robot camera preview not found.")
    return FileResponse(preview, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/world-templates")
async def world_templates_list():
    return {"worldTemplates": await evaluation_catalog.list_world_templates()}


@app.get("/api/evidence/requests")
async def evidence_requests_list():
    return {"objectRequests": await evidence_catalog.list_requests()}


@app.post("/api/evidence/requests", status_code=201)
async def evidence_request_create(
    payload: ObjectRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await evidence_catalog.create_request(payload, idempotency_key=_idempotency_key(idempotency_key))
    except (ValueError, command_store.CommandConflict) as exc:
        raise HTTPException(409 if isinstance(exc, command_store.CommandConflict) else 422, str(exc)) from exc


@app.get("/api/evidence/requests/{request_id}")
async def evidence_request_get(request_id: str):
    try:
        return await evidence_catalog.get_request(request_id)
    except KeyError as exc:
        raise HTTPException(404, "Object request not found.") from exc


@app.post("/api/evidence/requests/{request_id}/normalize-recorded", status_code=201)
async def evidence_recorded_normalize(
    request_id: str,
    payload: RecordedEvidenceImport,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await evidence_catalog.normalize_recorded(
            request_id,
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Object request not found.") from exc
    except (evidence_catalog.EvidenceConflict, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/evidence/bundles/{bundle_id}")
async def evidence_bundle_get(bundle_id: str):
    try:
        return await evidence_catalog.get_bundle(bundle_id)
    except KeyError as exc:
        raise HTTPException(404, "Evidence bundle not found.") from exc


@app.get("/api/evidence/collections")
async def evidence_collections_list(
    request_id: str | None = Query(default=None, alias="requestId", max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
):
    return {"collectionRuns": await evidence_collection.list_runs(request_id=request_id, limit=limit)}


@app.post("/api/evidence/requests/{request_id}/collections", status_code=202)
async def evidence_collection_create(
    request_id: str,
    payload: BrightDataCollectionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await evidence_collection.create_run(
            request_id,
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Object request not found.") from exc
    except (ValueError, evidence_collection.EvidenceCollectionConflict) as exc:
        raise HTTPException(409 if isinstance(exc, evidence_collection.EvidenceCollectionConflict) else 422, str(exc)) from exc
    except command_store.CommandConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/evidence/collections/{run_id}")
async def evidence_collection_get(run_id: str):
    try:
        return {"collectionRun": await evidence_collection.get_run(run_id)}
    except KeyError as exc:
        raise HTTPException(404, "Evidence collection run not found.") from exc


@app.post("/api/evidence/collections/{run_id}/cancel")
async def evidence_collection_cancel(run_id: str):
    try:
        return {"collectionRun": await evidence_collection.cancel_run(run_id)}
    except KeyError as exc:
        raise HTTPException(404, "Evidence collection run not found.") from exc


@app.get("/api/scraper-collector-versions")
async def scraper_collector_versions_list(
    collector_id: str | None = Query(default=None, alias="collectorId", max_length=160),
    limit: int = Query(default=200, ge=1, le=500),
):
    return {"collectorVersions": await scraper_repair.list_collector_versions(collector_id=collector_id, limit=limit)}


@app.post("/api/scraper-collector-versions", status_code=201)
async def scraper_collector_version_create(
    payload: ScraperCollectorVersionCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await scraper_repair.register_collector_version(
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except (scraper_repair.ScraperRepairConflict, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/scraper-repair-runs")
async def scraper_repair_runs_list(limit: int = Query(default=100, ge=1, le=500)):
    return {"repairRuns": await scraper_repair.list_repair_runs(limit)}


@app.get("/api/scraper-repair-runs/{run_id}")
async def scraper_repair_run_get(run_id: str):
    try:
        return {"repairRun": await scraper_repair.get_repair_run(run_id)}
    except KeyError as exc:
        raise HTTPException(404, "Scraper repair run not found.") from exc


@app.post("/api/scraper-repair-runs", status_code=201)
async def scraper_repair_run_create(
    payload: ScraperRepairCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await scraper_repair.create_repair_run(payload, idempotency_key=_idempotency_key(idempotency_key))
    except KeyError as exc:
        raise HTTPException(404, "Collector version, object request, or failure bundle not found.") from exc
    except (scraper_repair.ScraperRepairConflict, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/scraper-repair-runs/{run_id}/provider-request", status_code=202)
async def scraper_repair_provider_request(
    run_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await scraper_repair.trigger_provider_repair(run_id, idempotency_key=_idempotency_key(idempotency_key))
    except KeyError as exc:
        raise HTTPException(404, "Scraper repair run not found.") from exc
    except (scraper_repair.ScraperRepairConflict, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except brightdata.BrightDataError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/scraper-repair-runs/{run_id}/draft", status_code=201)
async def scraper_repair_draft_submit(
    run_id: str,
    payload: ScraperRepairDraftSubmission,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await scraper_repair.submit_draft(run_id, payload, idempotency_key=_idempotency_key(idempotency_key))
    except KeyError as exc:
        raise HTTPException(404, "Scraper repair run not found.") from exc
    except (scraper_repair.ScraperRepairConflict, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/scraper-repair-runs/{run_id}/test", status_code=201)
async def scraper_repair_test(
    run_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await scraper_repair.run_quality_tests(run_id, idempotency_key=_idempotency_key(idempotency_key))
    except KeyError as exc:
        raise HTTPException(404, "Scraper repair run or test artifact not found.") from exc
    except (scraper_repair.ScraperRepairConflict, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/scraper-repair-runs/{run_id}/decision", status_code=201)
async def scraper_repair_decide(
    run_id: str,
    payload: ScraperRepairDecision,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await scraper_repair.decide(run_id, payload, idempotency_key=_idempotency_key(idempotency_key))
    except KeyError as exc:
        raise HTTPException(404, "Scraper repair run not found.") from exc
    except (scraper_repair.ScraperRepairConflict, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except brightdata.BrightDataError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/scraper-repair-runs/{run_id}/rollback", status_code=201)
async def scraper_repair_rollback(
    run_id: str,
    payload: ScraperRepairRollback,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await scraper_repair.rollback(run_id, payload, idempotency_key=_idempotency_key(idempotency_key))
    except KeyError as exc:
        raise HTTPException(404, "Scraper repair run not found.") from exc
    except (scraper_repair.ScraperRepairConflict, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/scraper-repair/demo/page/{layout}", response_class=HTMLResponse)
async def scraper_repair_controlled_page(layout: str):
    try:
        return HTMLResponse(scraper_repair_demo.page_html(layout), headers={"Cache-Control": "no-store"})
    except KeyError as exc:
        raise HTTPException(404, "Controlled scraper layout not found.") from exc


@app.post("/api/scraper-repair/demo", status_code=201)
async def scraper_repair_controlled_demo(payload: ScraperRepairDemoRequest):
    try:
        return await scraper_repair_demo.run_demo(automatic_promotion=payload.automatic_promotion)
    except (scraper_repair.ScraperRepairError, ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/evaluations")
async def evaluation_runs_list(limit: int = Query(default=100, ge=1, le=500)):
    return {"evaluations": await evaluation_catalog.list_evaluations(limit)}


@app.post("/api/evaluations/oracle/pick-place", status_code=201)
async def evaluation_oracle_pick_place(
    payload: OracleEvaluationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await evaluation_catalog.run_pick_place_oracle(
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Robot registration not found.") from exc
    except (evaluation_catalog.EvaluationConflict, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/evaluations/oracle/franka-drawer-open", status_code=201)
async def evaluation_oracle_franka_drawer_open(
    payload: OracleEvaluationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await evaluation_catalog.run_franka_drawer_oracle(
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Robot registration not found.") from exc
    except (evaluation_catalog.EvaluationConflict, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/evaluations/oracle/compiled-asset-pick-place", status_code=201)
async def evaluation_oracle_compiled_asset_pick_place(
    payload: CompiledAssetOracleRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await evaluation_catalog.run_compiled_asset_pick_place_oracle(
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Robot registration or compiled asset version not found.") from exc
    except (evaluation_catalog.EvaluationConflict, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/evaluations/vla/compiled-asset-pick-place", status_code=201)
async def evaluation_vla_compiled_asset_pick_place(
    payload: CompiledAssetVlaEvaluationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await evaluation_catalog.run_compiled_asset_pick_place_vla(
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Robot, model, or compiled asset version not found.") from exc
    except (evaluation_catalog.EvaluationConflict, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/evaluations/{run_id}/analyze", status_code=201)
async def evaluation_analyze(
    run_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await curriculum_catalog.analyze_evaluation(
            run_id,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Evaluation not found.") from exc
    except (curriculum_catalog.CurriculumError, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/failure-events")
async def failure_events_list(limit: int = Query(default=100, ge=1, le=500)):
    return {"failureEvents": await curriculum_catalog.list_failure_events(limit)}


@app.get("/api/coverage")
async def coverage_get(
    robot_id: str | None = Query(default=None, alias="robotId"),
    model_id: str | None = Query(default=None, alias="modelId"),
    task_family: str = Query(default="pick_place", alias="taskFamily", pattern=r"^[a-z][a-z0-9_-]+$"),
    limit: int = Query(default=200, ge=1, le=500),
):
    return await curriculum_catalog.coverage_state(
        robot_id=robot_id,
        model_id=model_id,
        task_family=task_family,
        limit=limit,
    )


@app.post("/api/curriculum/plan-next", status_code=201)
async def curriculum_plan_next(
    payload: CurriculumPlanRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await curriculum_catalog.plan_next(
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Robot or model registration not found.") from exc
    except (curriculum_catalog.CurriculumError, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/curriculum/plans")
async def curriculum_plans_list(limit: int = Query(default=100, ge=1, le=500)):
    return {"plans": await curriculum_catalog.list_plans(limit)}


@app.get("/api/scenario-specs")
async def scenario_specs_list(limit: int = Query(default=100, ge=1, le=500)):
    return {"scenarios": await curriculum_catalog.list_scenarios(limit)}


@app.post("/api/scenario-specs/{scenario_id}/oracle", status_code=201)
async def scenario_oracle_execute(
    scenario_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await curriculum_catalog.execute_scenario_oracle(
            scenario_id,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Scenario specification not found.") from exc
    except (curriculum_catalog.CurriculumError, evaluation_catalog.EvaluationConflict, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/scenario-executions")
async def scenario_executions_list(limit: int = Query(default=100, ge=1, le=500)):
    return {"executions": await curriculum_catalog.list_scenario_executions(limit)}


@app.post("/api/autonomous-runs", status_code=202)
async def autonomous_run_start(
    payload: AutonomousCurriculumRunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await autonomous_curriculum.start_run(
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Robot, model, or asset registration not found.") from exc
    except (autonomous_curriculum.AutonomousCurriculumError, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/autonomous-runs")
async def autonomous_runs_list(limit: int = Query(default=100, ge=1, le=500)):
    return {"runs": await autonomous_curriculum.list_runs(limit)}


@app.get("/api/autonomous-runs/{run_id}")
async def autonomous_run_get(run_id: str):
    try:
        return {"run": await autonomous_curriculum.get_run(run_id)}
    except KeyError as exc:
        raise HTTPException(404, "Autonomous curriculum run not found.") from exc


@app.post("/api/autonomous-runs/{run_id}/cancel")
async def autonomous_run_cancel(run_id: str):
    try:
        return {"run": await autonomous_curriculum.cancel_run(run_id)}
    except KeyError as exc:
        raise HTTPException(404, "Autonomous curriculum run not found.") from exc


@app.get("/api/evaluations/{run_id}")
async def evaluation_run_get(run_id: str):
    try:
        return await evaluation_catalog.get_evaluation(run_id)
    except KeyError as exc:
        raise HTTPException(404, "Evaluation not found.") from exc


@app.get("/api/evaluations/{run_id}/frames/{phase}/{camera}.png")
async def evaluation_frame_get(run_id: str, phase: str, camera: str):
    try:
        path = evaluation_catalog.frame_path(run_id, phase, camera)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Evaluation frame not found.") from exc
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/simulation/isaac")
async def isaac_status():
    flat = await settings_store.get_flat()
    return await asyncio.to_thread(
        isaac_sim.inspect,
        str(flat.get("simulation.isaacRoot") or ""),
        str(flat.get("simulation.isaacAssetRoot") or ""),
        str(flat.get("simulation.isaacLabRoot") or ""),
    )


@app.post("/api/robots/franka/isaac", status_code=201)
async def register_isaac_franka():
    flat = await settings_store.get_flat()
    status = await asyncio.to_thread(
        isaac_sim.inspect,
        str(flat.get("simulation.isaacRoot") or ""),
        str(flat.get("simulation.isaacAssetRoot") or ""),
        str(flat.get("simulation.isaacLabRoot") or ""),
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
        str(flat.get("simulation.isaacLabRoot") or ""),
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
        "command": [status.get("python") or "<isaac-env>/Scripts/python.exe", str((BASE_DIR / "isaac_bridge.py").resolve()), str(launch_path)],
    }


@app.post("/api/simulation/isaac/franka/pick-place", status_code=201)
async def run_isaac_franka_pick_place(
    seed: int = Query(default=6203, ge=0, le=2_147_483_647),
    max_steps: int = Query(default=1200, ge=100, le=5000),
):
    """Run a bounded, authoritative PhysX Franka pick/place oracle."""

    flat = await settings_store.get_flat()
    status = await asyncio.to_thread(
        isaac_sim.inspect,
        str(flat.get("simulation.isaacRoot") or ""),
        str(flat.get("simulation.isaacAssetRoot") or ""),
        str(flat.get("simulation.isaacLabRoot") or ""),
    )
    result = await asyncio.to_thread(
        isaac_sim.run_franka_pick_place,
        status,
        seed=seed,
        max_steps=max_steps,
    )
    events.publish(
        "simulation",
        "Isaac Franka pick/place finished" if result.get("success") else "Isaac Franka pick/place failed",
        f"{result.get('id', 'no-run')} · {result.get('failureCode') or 'success'}",
        run=result.get("id"),
    )
    return {"evaluation": result}


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


@app.post("/api/worlds/operate", status_code=201)
async def world_operate(
    payload: WorldOperateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Dispatch an explicit World operation to a real persisted evaluator.

    Free text is retained as task context, but the selected task/controller/
    backend contract determines which bounded command may execute.
    """

    operation = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    key = _idempotency_key(idempotency_key)
    try:
        active_resolution: dict[str, Any] | None = None
        if payload.execution_scope == "active_world":
            supported_active = (
                payload.backend == "mujoco"
                and (
                    (payload.task == "pick_place" and payload.controller in {"oracle", "vla_jepa"})
                    or (payload.task == "drop_off_table" and payload.controller == "oracle")
                )
            )
            if not supported_active:
                raise HTTPException(
                    422,
                    "The selected active-world controller is not compiled against the authored kitchen runtime. "
                    "No validation-bench evaluation was substituted and no simulation was started.",
                )
            active_resolution = await _resolve_active_world_task(payload)
            operation.update(active_resolution)
        if payload.backend == "isaac_sim":
            robots = await asyncio.to_thread(robot_registry.list_all)
            selected_robot = next((item for item in robots if item.get("id") == payload.robot_id), None)
            if selected_robot is None:
                raise KeyError(payload.robot_id)
            if selected_robot.get("format") != "isaac-openusd-reference":
                raise ValueError("Isaac Sim execution requires the registered Isaac OpenUSD Franka embodiment.")
            flat = await settings_store.get_flat()
            status = await asyncio.to_thread(
                isaac_sim.inspect,
                str(flat.get("simulation.isaacRoot") or ""),
                str(flat.get("simulation.isaacAssetRoot") or ""),
                str(flat.get("simulation.isaacLabRoot") or ""),
            )
            evaluation = await asyncio.to_thread(
                isaac_sim.run_franka_pick_place,
                status,
                seed=payload.seed,
                max_steps=max(100, min(payload.max_policy_steps * 8, 5000)),
            )
            events.publish(
                "simulation",
                "World Isaac operation finished" if evaluation.get("success") else "World Isaac operation blocked or failed",
                f"{evaluation.get('id', 'no-run')} · {evaluation.get('failureCode') or 'success'}",
                robot=payload.robot_id,
            )
            return {
                "schemaVersion": "robotworld.world-operation-result.v1",
                "operation": operation,
                "kind": "isaac_evaluation",
                "evaluation": evaluation,
                "runtime": status,
            }

        if payload.task == "open_drawer":
            envelope = await evaluation_catalog.run_franka_drawer_oracle(
                OracleEvaluationRequest(robotId=payload.robot_id, seed=payload.seed),
                idempotency_key=key,
            )
            kind = "oracle_evaluation"
        elif payload.controller == "oracle":
            if active_resolution:
                envelope = await evaluation_catalog.run_authored_scene_pick_place_oracle(
                    robot_id=payload.robot_id,
                    asset_version_id=str(active_resolution["assetVersionId"]),
                    seed=payload.seed,
                    scene_spec=dict(active_resolution["authoredScene"]),
                    idempotency_key=key,
                    task_kind=payload.task,
                )
            elif payload.asset_version_id:
                envelope = await evaluation_catalog.run_compiled_asset_pick_place_oracle(
                    CompiledAssetOracleRequest(
                        robotId=payload.robot_id,
                        assetVersionId=payload.asset_version_id,
                        seed=payload.seed,
                    ),
                    idempotency_key=key,
                )
            else:
                envelope = await evaluation_catalog.run_pick_place_oracle(
                    OracleEvaluationRequest(robotId=payload.robot_id, seed=payload.seed),
                    idempotency_key=key,
                )
            kind = "oracle_evaluation"
        elif payload.controller == "vla_jepa":
            envelope = await evaluation_catalog.run_compiled_asset_pick_place_vla(
                CompiledAssetVlaEvaluationRequest(
                    robotId=payload.robot_id,
                    modelId=payload.model_id,
                    assetVersionId=(active_resolution or {}).get("assetVersionId") or payload.asset_version_id,
                    instruction=payload.instruction,
                    maxPolicySteps=payload.max_policy_steps,
                    seed=payload.seed,
                ),
                idempotency_key=key,
                scene_spec=dict(active_resolution["authoredScene"]) if active_resolution else None,
            )
            kind = "vla_evaluation"
        else:
            envelope = await autonomous_curriculum.start_run(
                AutonomousCurriculumRunRequest.model_validate(
                    {
                        "autonomyMode": "AUTONOMOUS_WITH_BUDGETS",
                        "robotId": payload.robot_id,
                        "modelId": payload.model_id,
                        "taskFamily": "pick_place",
                        "instruction": payload.instruction,
                        "allowedAssetVersionIds": [payload.asset_version_id],
                        "seed": payload.seed,
                        "executeVla": True,
                        "maxPolicySteps": payload.max_policy_steps,
                        "budgets": {
                            "maxWorlds": 3,
                            "maxScrapeRequests": 0,
                            "maxGpuMinutes": 10.0,
                            "maxEvaluationEpisodes": 6,
                            "maxRetries": 0,
                            "maxIterations": 3,
                            "maxConsecutiveFailures": 3,
                        },
                    }
                ),
                idempotency_key=key,
            )
            kind = "autonomous_run"
        result = dict(envelope.get("result") or {})
        response = {
            "schemaVersion": "robotworld.world-operation-result.v1",
            "operation": operation,
            "kind": kind,
            "commandId": envelope.get("commandId"),
            "commandStatus": envelope.get("status"),
            # The full trajectory is durable in the evaluation catalog. Do
            # not serialize tens of megabytes through an interactive command.
            "evaluation": franka_live.evaluation_summary(result["evaluation"]) if result.get("evaluation") else None,
            "run": result.get("run"),
            "worldTemplate": result.get("worldTemplate"),
        }
        events.publish(
            "agent" if kind == "autonomous_run" else "simulation",
            "World operation dispatched",
            f"{payload.task} · {payload.controller} · {response.get('commandId')}",
            robot=payload.robot_id,
        )
        return response
    except KeyError as exc:
        raise HTTPException(404, "Selected robot, model, or asset version was not found.") from exc
    except (evaluation_catalog.EvaluationConflict, autonomous_curriculum.AutonomousCurriculumError, command_store.CommandConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
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


@app.get("/api/trellis/q4-proof")
async def trellis_q4_proof():
    """Expose the immutable local Q4 generation proof without promoting it.

    This artifact is valid textured visual geometry only. It is deliberately
    separate from canonical physical asset versions because no trustworthy
    drawer PartGraph, metric evidence, or collision contract has been authored.
    """

    root = (DATA_DIR / "trellis-live" / "counter-proof-4-seed6204").resolve()
    model = (root / "model.glb").resolve()
    if root not in model.parents or not model.is_file():
        raise HTTPException(404, "The recorded local TRELLIS Q4 proof is not available.")
    digest = await asyncio.to_thread(lambda: hashlib.sha256(model.read_bytes()).hexdigest())
    return {
        "schemaVersion": "robotworld.trellis-generation-proof.v1",
        "id": "trellis-q4-counter-seed6204",
        "model": "TRELLIS.2-4B Q4 GGUF",
        "runtime": "trellis.cpp v0.6.0 CUDA 12",
        "device": "NVIDIA RTX 4080",
        "seed": 6204,
        "geometryResolution": 512,
        "textureResolution": 512,
        "durationSeconds": 134.2,
        "sizeBytes": model.stat().st_size,
        "sha256": digest,
        "vertices": 91506,
        "faces": 144174,
        "finite": True,
        "pbrMaterialCount": 1,
        "textureSemantics": ["base_color", "metallic_roughness"],
        "truth": "visual_geometry_only_not_articulated_or_physics_validated",
        "sourceUrl": "/api/trellis/q4-proof/model.glb",
        "images": {
            "conditioning": "/api/trellis/q4-proof/images/model_cutout.png",
            "baseColor": "/api/trellis/q4-proof/images/model_base.png",
        },
    }


@app.get("/api/trellis/q4-proof/model.glb")
async def trellis_q4_proof_glb():
    path = (DATA_DIR / "trellis-live" / "counter-proof-4-seed6204" / "model.glb").resolve()
    root = (DATA_DIR / "trellis-live" / "counter-proof-4-seed6204").resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "The recorded local TRELLIS Q4 GLB is not available.")
    return FileResponse(path, media_type="model/gltf-binary", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/trellis/q4-proof/images/{name}")
async def trellis_q4_proof_image(name: Literal["model_cutout.png", "model_base.png"]):
    root = (DATA_DIR / "trellis-live" / "counter-proof-4-seed6204").resolve()
    path = (root / name).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "The recorded local TRELLIS Q4 image is not available.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000, immutable"})


def _idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(400, "Idempotency-Key must not be blank.")
    if len(cleaned) > 160:
        raise HTTPException(400, "Idempotency-Key must be at most 160 characters.")
    return cleaned


def _registry_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "Model registration not found.")
    if isinstance(exc, (control_catalog.RegistryConflict, command_store.CommandConflict)):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc))


@app.get("/api/models")
async def registered_models_list():
    """List internal model registrations without resolving or returning secrets."""
    return {
        "models": await control_catalog.list_models(),
        "allowedLocalRoots": [str(path) for path in model_registry.configured_model_roots()],
    }


@app.post("/api/models", status_code=201)
async def registered_model_create(
    payload: ModelRegistrationCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await control_catalog.register_model(
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except (KeyError, control_catalog.RegistryError, command_store.CommandConflict, ValueError) as exc:
        raise _registry_http_error(exc) from exc


@app.post("/api/worlds/live-sessions", status_code=201)
async def world_live_session_create(payload: WorldOperateRequest):
    """Create a continuous view of the real persisted MuJoCo oracle run."""

    if payload.backend != "mujoco" or payload.controller != "oracle" or payload.task not in {"pick_place", "drop_off_table"}:
        raise HTTPException(
            422,
            "Live in-app streaming currently requires MuJoCo and a deterministic pick/place or drop-off-table oracle.",
        )
    operation = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    if payload.execution_scope == "active_world":
        operation.update(await _resolve_active_world_task(payload))
    return franka_live.info(franka_live.create(operation))


@app.post("/api/worlds/manual-sessions", status_code=201)
async def world_manual_session_create(payload: WorldOperateRequest):
    """Create an operator-controlled Panda in the compiled active editor world."""

    if (
        payload.backend != "mujoco"
        or payload.controller != "oracle"
        or payload.task != "pick_place"
        or payload.execution_scope != "active_world"
    ):
        raise HTTPException(422, "Manual control requires the active editor world, MuJoCo, and the validated Franka controller.")
    operation = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    resolution = await _resolve_active_world_task(payload)
    operation.update(resolution)
    try:
        asset_version = await rigid_asset_compiler.get_version(str(resolution["assetVersionId"]))
        template = await evaluation_catalog.ensure_authored_scene_world_template(
            payload.robot_id,
            asset_version,
            dict(resolution["authoredScene"]),
        )
        return franka_live.info(await franka_live.create_manual(operation, template))
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(404, "The selected compiled asset version is unavailable.") from exc
    except (ValueError, OSError, RuntimeError, evaluation_catalog.EvaluationConflict) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/worlds/manual-sessions/{session_id}/jog")
async def world_manual_session_jog(session_id: str, payload: ManualJogIn):
    session = franka_live.get(session_id)
    if session is None:
        raise HTTPException(404, "Manual Franka session not found")
    try:
        return await franka_live.manual_jog(session, payload.deltaM)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/worlds/manual-sessions/{session_id}/gripper")
async def world_manual_session_gripper(session_id: str, payload: ManualGripperIn):
    session = franka_live.get(session_id)
    if session is None:
        raise HTTPException(404, "Manual Franka session not found")
    try:
        return await franka_live.manual_gripper(session, payload.command)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/api/worlds/manual-sessions/{session_id}", status_code=204)
async def world_manual_session_close(session_id: str):
    session = franka_live.get(session_id)
    if session is None:
        raise HTTPException(404, "Manual Franka session not found")
    await franka_live.close_manual(session)
    return Response(status_code=204)


async def _resolve_active_world_task(payload: WorldOperateRequest) -> dict[str, Any]:
    """Resolve named authored objects for one explicit physical task."""

    async with SessionLocal() as session:
        world = (await session.execute(select(World).where(World.active.is_(True)).limit(1))).scalar_one_or_none()
        if world is None or world.id != payload.world_id:
            raise HTTPException(409, "The selected active world changed; refresh before execution.")
        ids = _placed_asset_ids(world.scene_tree)
        assets = (await session.execute(select(Asset).where(Asset.id.in_(ids)))).scalars().all() if ids else []
        rows = _world_assembly_rows(ids, {row.id: row for row in assets}, _placement_state(world.scene_tree))
    placements = [_placement_api(row) for row in rows]
    instruction = " ".join(payload.instruction.lower().replace("-", " ").split())
    if payload.task == "pick_place" and not any(phrase in instruction for phrase in ("on top of", "on top", "onto")):
        raise HTTPException(
            422,
            "Active-world pick/place currently requires an explicit 'on top of <named target>' relation. No simulation was started.",
        )

    stop_words = {"real", "single", "whole", "isolated", "product", "photo", "white", "background", "site", "com", "steel", "kitchen", "countertop"}

    def score(placement: dict[str, Any]) -> int:
        words = {
            token
            for token in re.findall(r"[a-z0-9]+", str(placement["name"]).lower())
            if len(token) >= 4 and token not in stop_words
        }
        return sum(word in instruction for word in words)

    sources = [item for item in placements if item.get("mobility") == "movable" and score(item) > 0]
    targets = [item for item in placements if item.get("mobility") == "fixed" and score(item) > 0]
    target_count_ok = len(targets) == 1 if payload.task == "pick_place" else True
    if len(sources) != 1 or not target_count_ok:
        raise HTTPException(
            422,
            ("Name exactly one movable source and one fixed target from the active scene. " if payload.task == "pick_place" else "Name exactly one movable source from the active scene. ")
            +
            f"Resolved sources={[(item['name'], score(item)) for item in sources]}, "
            f"targets={[(item['name'], score(item)) for item in targets]}. No simulation was started.",
        )
    counter_row = _primary_counter(rows)
    if counter_row is None:
        raise HTTPException(422, "The active scene has no deterministic primary support surface. No simulation was started.")
    counter = _placement_api(counter_row)
    spawn_position, spawn_quaternion = _world_robot_mount(rows, world.scene_tree)
    versions = await rigid_asset_compiler.list_versions(limit=500)
    physical = next(
        (
            item
            for item in versions
            if item["assetId"] == sources[0]["assetId"]
            and item["lifecycleState"] in {"PHYSICS_VALIDATED", "ORACLE_VALIDATED"}
        ),
        None,
    )
    if physical is None:
        raise HTTPException(
            409,
            f"{sources[0]['name']} has no PHYSICS_VALIDATED compiled asset version. No visual GLB was substituted.",
        )
    return {
        "assetVersionId": physical["id"],
        "sourcePbrTransform": {
            "uniformScale": float((physical.get("manifest") or {}).get("uniformScale") or 1.0),
            "translationM": list(((physical.get("manifest") or {}).get("coordinateConvention") or {}).get("translationM") or [0.0, 0.0, 0.0]),
            "mapping": "(x, y, z) -> (x, -z, y)",
        },
        "authoredScene": {
            "worldId": world.id,
            "taskKind": payload.task,
            "sourcePlacement": sources[0],
            "targetPlacement": targets[0] if payload.task == "pick_place" else None,
            "counterPlacement": counter,
            "robotSpawn": {
                "positionM": spawn_position,
                "quaternionWxyz": spawn_quaternion,
                "source": "persisted_world_authoring" if _persisted_robot_mount(world.scene_tree) else "robotworld_default_counter_rear_mount",
            },
        },
    }


@app.get("/api/worlds/live-sessions/{session_id}")
async def world_live_session_get(session_id: str):
    session = franka_live.get(session_id)
    if session is None:
        raise HTTPException(404, "Live Franka session not found")
    return franka_live.info(session)


@lru_cache(maxsize=1)
def _franka_compiled_visual_model() -> mujoco.MjModel:
    """Compile the pinned Menagerie asset once for browser mesh extraction."""

    source = (DATA_DIR / "robot_descriptions" / "mujoco_menagerie" / "franka_emika_panda" / "panda.xml").resolve()
    if not source.is_file():
        registered = sorted(ROBOTS_DIR.glob("franka-panda-mujoco-*/runtime/franka.xml"))
        if not registered:
            raise FileNotFoundError(source)
        source = registered[-1].resolve()
    return mujoco.MjModel.from_xml_path(str(source))


@lru_cache(maxsize=128)
def _franka_compiled_visual_obj(mesh_name: str) -> bytes:
    """Serialize MuJoCo's compiled mesh, not the pre-compiler source OBJ.

    MuJoCo recenters/reorients source mesh vertices and stores that result in
    ``mesh_vert``. Pairing a raw OBJ with ``geom_xpos`` is therefore wrong for
    Menagerie parts whose ``mesh_pos``/``mesh_quat`` are non-identity.
    """

    model = _franka_compiled_visual_model()
    mesh_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_MESH, mesh_name)
    if mesh_id < 0:
        raise KeyError(mesh_name)
    vertex_address = int(model.mesh_vertadr[mesh_id])
    vertex_count = int(model.mesh_vertnum[mesh_id])
    face_address = int(model.mesh_faceadr[mesh_id])
    face_count = int(model.mesh_facenum[mesh_id])
    vertices = model.mesh_vert[vertex_address : vertex_address + vertex_count]
    faces = model.mesh_face[face_address : face_address + face_count]
    lines = ["# RobotWorld MuJoCo-compiled visual mesh", f"o {mesh_name}"]
    lines.extend(f"v {float(x):.9g} {float(y):.9g} {float(z):.9g}" for x, y, z in vertices)
    lines.extend(f"f {int(a) + 1} {int(b) + 1} {int(c) + 1}" for a, b, c in faces)
    return ("\n".join(lines) + "\n").encode("ascii")


@app.get("/api/runtime/franka-compiled-meshes/{mesh_name}.obj")
async def franka_compiled_visual_mesh(mesh_name: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", mesh_name):
        raise HTTPException(404, "Franka visual mesh not found")
    try:
        content = await asyncio.to_thread(_franka_compiled_visual_obj, mesh_name)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "Franka visual mesh not found") from exc
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/models/{model_id}")
async def registered_model_get(model_id: str):
    try:
        return await control_catalog.get_model(model_id)
    except KeyError as exc:
        raise _registry_http_error(exc) from exc


@app.post("/api/models/{model_id}/validate")
async def registered_model_validate(
    model_id: str,
    payload: ModelValidationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await control_catalog.validate_model(
            model_id,
            compute_content_hash=payload.compute_content_hash,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except (KeyError, control_catalog.RegistryError, command_store.CommandConflict, ValueError) as exc:
        raise _registry_http_error(exc) from exc


@app.post("/api/models/{model_id}/load")
async def registered_model_load(
    model_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await control_catalog.load_model(
            model_id,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except (KeyError, control_catalog.RegistryError, command_store.CommandConflict, ValueError) as exc:
        raise _registry_http_error(exc) from exc


@app.post("/api/models/{model_id}/unload")
async def registered_model_unload(
    model_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await control_catalog.unload_model(
            model_id,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except (KeyError, control_catalog.RegistryError, command_store.CommandConflict, ValueError) as exc:
        raise _registry_http_error(exc) from exc


@app.get("/api/models/{model_id}/bridges/franka/{robot_id}")
async def model_franka_bridge_status(model_id: str, robot_id: str):
    try:
        return await vla_bridge.bridge_status(model_id, robot_id)
    except KeyError as exc:
        raise HTTPException(404, "Model or robot registration not found.") from exc


@app.post("/api/models/{model_id}/bridges/franka/{robot_id}/zero-shot", status_code=201)
async def attach_model_franka_zero_shot_bridge(
    model_id: str,
    robot_id: str,
    payload: VlaFrankaZeroShotBridgeRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await vla_bridge.attach_zero_shot_bridge(
            model_id,
            robot_id,
            camera_mapping=payload.camera_mapping,
            policy_control_hz=payload.policy_control_hz,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except (KeyError, ValueError, command_store.CommandConflict) as exc:
        raise _registry_http_error(exc) from exc


@app.get("/api/models/{model_id}/worker-probe")
async def model_worker_probe(model_id: str):
    try:
        model = await control_catalog.get_model(model_id)
    except KeyError as exc:
        raise HTTPException(404, "Model registration not found.") from exc
    if model["providerType"] != "local_path" or "vla_policy" not in model["roles"]:
        raise HTTPException(422, "This worker probe currently supports local VLA policy registrations only.")
    try:
        return await asyncio.to_thread(
            vla_policy_worker.probe_checkpoint,
            str(model["localPath"] or ""),
            str(model["expectedDevice"] or "cuda"),
        )
    except (OSError, ValueError, vla_policy_worker.VlaWorkerError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/workers/vla-jepa")
async def vla_jepa_worker_status():
    return await asyncio.to_thread(vla_policy_worker.status)


@app.post("/api/workers/vla-jepa/stop")
async def stop_vla_jepa_worker():
    await asyncio.to_thread(vla_policy_worker.kill)
    reconciled = await control_catalog.reconcile_local_worker_state()
    return {"stopped": True, "reconciledModelRegistrations": reconciled, "worker": vla_policy_worker.status()}


@app.post("/api/vla/bridges/franka/actions/decode")
async def decode_franka_vla_action(payload: VlaNormalizedAction):
    try:
        return vla_bridge.decode_action(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/audit")
async def audit_events_list(
    entity_type: str | None = Query(default=None, max_length=40),
    entity_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=200, ge=1, le=500),
):
    return {
        "events": await control_catalog.audit_history(
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
        )
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
    if not LEGACY_SOURCE_REPAIR_ENABLED:
        raise HTTPException(
            410,
            "Legacy source repair is disabled because it bypasses canonical golden/canary validation. "
            "Create a governed repair at /api/scraper-repair-runs instead.",
        )
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
    if not LEGACY_SOURCE_REPAIR_ENABLED:
        raise HTTPException(
            410,
            "Legacy source-repair approval is disabled because it bypasses the canonical decision audit. "
            "Use /api/scraper-repair-runs/{run_id}/decision instead.",
        )
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
    datasets, canonical_runs, policy_decisions = await asyncio.gather(
        lerobot_dataset.list_datasets(20),
        lerobot_training.list_runs(20),
        policy_lifecycle.list_decisions(20),
    )
    return {
        "stats": stats,
        "runs": [await catalog.training_run_out(row) for row in runs],
        "evalComparison": comparison,
        "successCurve": {"measured": successes},
        "collisionCurve": {"measured": collisions},
        "agentDecision": ({"title": decision.title, "decision": decision.decision, "evidence": decision.evidence, "nextStep": decision.next_step, "confidence": decision.confidence} if decision else None),
        "datasets": datasets,
        "canonicalRuns": canonical_runs,
        "policyDecisions": policy_decisions,
    }


@app.post("/api/training/runs", status_code=202)
async def queue_training():
    raise HTTPException(409, "Training is disabled. Configure a pinned external VLA and run policy evaluation; RobotWorld will not train on this workstation.")


@app.post("/api/training/runs/preflight", status_code=201)
async def validate_vla_jepa_training_run(
    payload: VlaJepaFineTuneValidationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await lerobot_training.validate_candidate(
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Dataset or base model was not found.") from exc
    except command_store.CommandConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (lerobot_training.TrainingPreflightError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/training/runs/execute", status_code=202)
async def execute_vla_jepa_training_run(
    payload: VlaJepaFineTuneExecuteRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await lerobot_training.execute_candidate(
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Training run was not found.") from exc
    except command_store.CommandConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (lerobot_training.TrainingPreflightError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/training/policy-decisions")
async def policy_candidate_decisions(limit: int = Query(default=100, ge=1, le=500)):
    return {"policyDecisions": await policy_lifecycle.list_decisions(limit)}


@app.post("/api/training/policy-decisions", status_code=201)
async def decide_policy_candidate(
    payload: PolicyCandidateDecisionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await policy_lifecycle.decide(payload, idempotency_key=_idempotency_key(idempotency_key))
    except KeyError as exc:
        raise HTTPException(404, "Training run, model, or evaluation was not found.") from exc
    except command_store.CommandConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (policy_lifecycle.PolicyLifecycleError, OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/training/policy-decisions/rollback", status_code=200)
async def rollback_policy_candidate(
    payload: PolicyCandidateRollbackRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await policy_lifecycle.rollback(payload, idempotency_key=_idempotency_key(idempotency_key))
    except KeyError as exc:
        raise HTTPException(404, "Policy decision or model was not found.") from exc
    except command_store.CommandConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (policy_lifecycle.PolicyLifecycleError, OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/training/datasets")
async def lerobot_datasets_list(limit: int = Query(default=100, ge=1, le=500)):
    return {"datasets": await lerobot_dataset.list_datasets(limit)}


@app.post("/api/training/datasets/from-evaluation", status_code=201)
async def lerobot_dataset_export(
    payload: LeRobotDatasetExportRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return await lerobot_dataset.export_evaluation(
            payload,
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Evaluation not found.") from exc
    except command_store.CommandConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (lerobot_dataset.DatasetExportError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


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
        {"name": "curriculum-planner", "kind": "agent-tool-service", "status": "healthy", "version": __version__, "latency": "—", "uptime": uptime, "restarts": 0},
        {"name": "mujoco-worker", "kind": "worker", "status": "running", "version": mujoco.__version__, "latency": "in-process", "uptime": uptime, "restarts": 0, "gpu": "CPU physics"},
        {"name": "model-provider", "kind": "integration", "status": "running" if provider["status"] == "healthy" else "degraded" if configured["model"] else "stopped", "version": str(provider.get("model") or "not configured"), "latency": "—", "uptime": uptime, "restarts": 0},
        {"name": "brightdata", "kind": "integration", "status": "running" if configured["brightdata"] else "stopped", "version": "REST", "latency": "external", "uptime": uptime, "restarts": 0},
        {"name": "signoz-exporter", "kind": "integration", "status": "running" if signoz_exporting() else "stopped", "version": "OTLP", "latency": "external", "uptime": uptime, "restarts": 0},
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
    def clean(value: str, limit: int) -> str:
        return secret_pattern.sub("[redacted]", value)[:limit]
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
    """Failures emitted by this backend process, excluding historical rows."""
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(LogLine)
            .where(
                LogLine.level.in_(["ERROR", "WARN"]),
                LogLine.time_ms >= STARTED_AT_WALL_MS,
            )
            .order_by(LogLine.time_ms.desc())
            .limit(100)
        )).scalars().all()
    return {
        "status": "degraded" if any(row.level == "ERROR" for row in rows[:10]) else "healthy",
        "uptimeSeconds": round(time.monotonic() - STARTED_AT, 1),
        "sinceTimeMs": STARTED_AT_WALL_MS,
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
    if section == "integrations":
        signoz_settings = dict((updated.get("integrations") or {}).get("signoz") or {})
        if signoz_settings.get("enabled") and signoz_settings.get("endpoint"):
            await configure_signoz(str(signoz_settings["endpoint"]))
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
    if not DEFERRED_PORT_ENABLED:
        raise HTTPException(404, "Port integration is deferred and disabled; RobotWorld's internal catalog is authoritative.")
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
    if not LEGACY_SIMCORE_ENABLED:
        raise HTTPException(410, "Legacy refrigerator preview is disabled. Use /api/worlds/live-sessions for authoritative Panda physics.")
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
    if not LEGACY_SIMCORE_ENABLED:
        raise HTTPException(410, "Legacy refrigerator preview is disabled. Use the durable Worlds evaluation catalog.")
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
    if not LEGACY_SIMCORE_ENABLED:
        await websocket.close(code=4403, reason="legacy preview disabled; use /ws/worlds/live")
        return
    session = live.get(session_id)
    if session is None:
        await websocket.close(code=4404, reason="session not found")
        return
    await websocket.accept()
    runner = asyncio.create_task(live.run(session), name=f"live-{session_id}")
    queue_task = asyncio.create_task(session.queue.get())
    receive_task = asyncio.create_task(websocket.receive_json())
    try:
        while True:
            done, _ = await asyncio.wait({queue_task, receive_task}, return_when=asyncio.FIRST_COMPLETED)
            if receive_task in done:
                message = receive_task.result()
                if message.get("type") == "control":
                    action = str(message.get("action"))
                    live.control(session, action, message.get("value"))
                    if action in {"stop", "end"}:
                        while not session.queue.empty():
                            try:
                                session.queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                receive_task = asyncio.create_task(websocket.receive_json())
            if queue_task in done:
                message = queue_task.result()
                await websocket.send_json(message)
                if message.get("type") == "end":
                    break
                queue_task = asyncio.create_task(session.queue.get())
    except WebSocketDisconnect:
        session.stop.set()
    finally:
        for task in (queue_task, receive_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(queue_task, receive_task, return_exceptions=True)
        if not runner.done() and session.stop.is_set():
            await runner


@app.websocket("/ws/worlds/live/{session_id}")
async def franka_live_socket(websocket: WebSocket, session_id: str):
    """Stream sampled camera/state evidence from the authoritative MuJoCo run."""

    session = franka_live.get(session_id)
    if session is None:
        await websocket.close(code=4404, reason="live Franka session not found")
        return
    await websocket.accept()
    await websocket.send_json({"type": "meta", "session": franka_live.info(session)})
    if session.latest_frame is not None:
        await websocket.send_json(session.latest_frame)
    if session.mode == "oracle" and session.task is None:
        session.task = asyncio.create_task(franka_live.run(session), name=f"franka-live-{session_id}")
    try:
        while True:
            message = await session.queue.get()
            await websocket.send_json(message)
            if message.get("type") in {"end", "error"}:
                return
    except WebSocketDisconnect:
        # The persisted evaluation continues when a viewer refreshes. A new
        # client can reconnect and receive the latest authoritative frame.
        return


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
