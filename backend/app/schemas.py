"""Pydantic DTOs — mirror frontend/src/data/types.ts exactly."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Health = Literal["healthy", "degraded", "failed", "repairing"]
RunStatus = Literal["in_progress", "success", "failed", "pending", "building", "completed"]
SkillStatus = Literal["ready", "improving", "in_training", "weak", "not_started"]
Severity = Literal["high", "medium", "low", "info"]
Tint = Literal["blue", "green", "amber", "red", "orange", "purple", "teal"]


class Delta(BaseModel):
    value: str
    dir: Literal["up", "down", "flat"]
    goodWhen: Literal["up", "down"] | None = None
    label: str | None = None


class Stat(BaseModel):
    label: str
    value: str
    icon: str
    tint: Tint
    foot: str | None = None
    delta: Delta | None = None
    spark: list[float] | None = None
    donut: float | None = None


class Skill(BaseModel):
    id: str
    name: str
    category: Literal["Manipulation", "Navigation", "Perception"]
    description: str
    success: float
    successDelta: float
    coverage: float
    lastTrained: str
    status: SkillStatus
    icon: str


class Weakness(BaseModel):
    mode: str
    detail: str
    contribution: float
    examples: int


class ScenarioFamily(BaseModel):
    id: str
    family: str
    count: int
    success: float
    coverage: float
    source: str
    status: Literal["promoted", "needs_data", "in_progress", "at_risk", "healthy", "needs_attention"]
    updated: str


class CoverageDimension(BaseModel):
    dimension: str
    coverage: float
    gaps: int
    bands: tuple[float, float, float, float]


class CurriculumItem(BaseModel):
    rank: int
    name: str
    desc: str
    impact: Literal["high", "medium", "low"]
    scenarios: int


class SkillDetail(Skill):
    target: float
    avgCollisions: float
    collisionsDelta: float
    lastGain: str
    scenarioCount: str
    weaknesses: list[Weakness]
    families: list[ScenarioFamily]
    curriculum: list[CurriculumItem]
    beforeAfter: dict  # {before: [], after: [], labels: []}
    successTrend: list[float]
    coverageTrend: list[float]
    collisionTrend: list[float]
    promoted: bool


class AssetPart(BaseModel):
    id: str
    name: str
    joint: str | None = None
    children: list["AssetPart"] = []


class Artifact(BaseModel):
    type: str
    file: str
    size: str
    generated: str


class CompileStage(BaseModel):
    name: str
    duration: str
    status: Literal["passed", "failed", "running"]


class Asset(BaseModel):
    id: str
    name: str
    kind: Literal["articulated", "rigid", "environment"]
    status: Literal["ready", "building", "testing", "blocked", "draft"]
    readiness: float
    physicsValidity: float
    scaleConfidence: float
    articulation: float
    lastEval: str
    lastEvalResult: Literal["passed", "failed", "pending"]
    source: str
    parts: list[AssetPart]
    artifacts: list[Artifact]
    compile: list[CompileStage]
    properties: dict
    tags: list[str]


class SceneNode(BaseModel):
    id: str
    name: str
    icon: str
    visible: bool | None = None
    locked: bool | None = None
    tag: str | None = None
    children: list["SceneNode"] = []


class ScenarioVariant(BaseModel):
    id: str
    name: str
    desc: str
    active: bool | None = None


class PhysicsCheck(BaseModel):
    check: str
    status: Literal["pass", "warn", "fail"]
    details: str
    impacted: str
    severity: Literal["Info", "Medium", "High"]


class LiveStep(BaseModel):
    name: str
    state: Literal["done", "active", "pending", "failed"]


class Source(BaseModel):
    id: str
    domain: str
    category: str
    collector: str
    items: int
    completeness: float
    lastRun: str
    health: Health
    brand: str


class PhotoCandidate(BaseModel):
    id: int
    score: float
    state: Literal["selected", "secondary", "rejected", "candidate"]
    front: float
    background: float
    isolation: float
    identity: float
    seed: int
    url: str | None = None


class RepairEvent(BaseModel):
    time: str
    title: str
    desc: str
    kind: Literal["detect", "fail", "heal", "approve", "done"]


class SourceDetail(BaseModel):
    product: str
    model: str
    imageSeed: int
    specs: list[tuple[str, str]]
    provenance: list[tuple[str, str]]
    photos: list[PhotoCandidate]
    repairs: list[RepairEvent]


class TrainingRunOut(BaseModel):
    id: str
    runId: str
    name: str
    policy: str
    worlds: int
    duration: str
    delta: float
    status: RunStatus
    when: str


class EvalComparisonRow(BaseModel):
    task: str
    icon: str
    baseline: float
    candidate: float


class TraceSpan(BaseModel):
    name: str
    service: str
    startMs: float
    durationMs: float
    status: Literal["ok", "error"]
    icon: str
    color: str


class LogLineOut(BaseModel):
    time: str
    level: Literal["INFO", "WARN", "ERROR", "DEBUG"]
    service: str
    message: str


class Alert(BaseModel):
    title: str
    severity: Severity
    service: str
    firingFor: str
    pending: bool | None = None
    meta: list[tuple[str, str]]
    tags: list[str]


class AgentInsight(BaseModel):
    icon: str
    title: str
    body: str


class PipelineActivity(BaseModel):
    pipeline: str
    icon: str
    stage: str
    stageIcon: str
    status: RunStatus
    started: str
    duration: str


class SkillGap(BaseModel):
    icon: str
    name: str
    family: str
    success: float
    coverage: float


class RecentCandidate(BaseModel):
    name: str
    status: Literal["promoted", "blocked"]


class ServiceRow(BaseModel):
    name: str
    kind: Literal["core", "agent", "integration", "worker"]
    status: Literal["running", "degraded", "stopped"]
    version: str
    latency: str
    uptime: str
    restarts: int
    gpu: str | None = None
