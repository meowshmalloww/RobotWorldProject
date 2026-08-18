"""Database schema (SQLAlchemy 2.0)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str] = mapped_column(String(40), default="target")
    target: Mapped[float] = mapped_column(Float, default=85.0)
    promoted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class ScenarioFamily(Base):
    __tablename__ = "scenario_families"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), index=True)
    family: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(200), default="generated")
    status: Mapped[str] = mapped_column(String(24), default="in_progress")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("scenario_families.id"), index=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # door_mass, handle_height, friction...
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), index=True)
    family_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scenario_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy: Mapped[str] = mapped_column(String(120), default="scripted-v1")
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    door_angle_deg: Mapped[float] = mapped_column(Float, default=0.0)
    collisions: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    failure_mode: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class TrainingRun(Base):
    __tablename__ = "training_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    skill_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(160))
    policy: Mapped[str] = mapped_column(String(120), default="bc-mlp")
    worlds: Mapped[int] = mapped_column(Integer, default=0)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    delta_pp: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    loss_curve: Mapped[list] = mapped_column(JSON, default=list)
    success_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    success_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(24), default="articulated")
    status: Mapped[str] = mapped_column(String(24), default="draft")
    source: Mapped[str] = mapped_column(String(300), default="")
    spec: Mapped[dict] = mapped_column(JSON, default=dict)      # scraped + inferred physical spec
    parts: Mapped[list] = mapped_column(JSON, default=list)     # part tree
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    physics_validity: Mapped[float] = mapped_column(Float, default=0.0)
    scale_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    articulation: Mapped[float] = mapped_column(Float, default=0.0)
    last_eval_result: Mapped[str] = mapped_column(String(16), default="pending")
    last_eval_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    file: Mapped[str] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class CompileStage(Base):
    __tablename__ = "compile_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(120))
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="running")


class World(Base):
    __tablename__ = "worlds"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    scene_tree: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Variant(Base):
    __tablename__ = "variants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    desc: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    domain: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(80), default="product")
    collector: Mapped[str] = mapped_column(String(40), default="")   # Bright Data collector id (c_*), if any
    query: Mapped[str] = mapped_column(String(300), default="")
    health: Mapped[str] = mapped_column(String(16), default="healthy")
    brand: Mapped[str] = mapped_column(String(40), default="generic")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)          # SourceDetail payload
    last_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    items: Mapped[int] = mapped_column(Integer, default=0)
    completeness: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class RepairEvent(Base):
    __tablename__ = "repair_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    time: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(160))
    desc: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16), default="detect")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Span(Base):
    __tablename__ = "spans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    span_id: Mapped[str] = mapped_column(String(32))
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(160))
    service: Mapped[str] = mapped_column(String(80), default="robotworld-backend")
    start_ms: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(8), default="ok")
    attrs: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class LogLine(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    time_ms: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    level: Mapped[str] = mapped_column(String(8), default="INFO")
    service: Mapped[str] = mapped_column(String(80), default="robotworld-backend")
    message: Mapped[str] = mapped_column(Text, default="")
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MetricPoint(Base):
    __tablename__ = "metric_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_ms: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    attrs: Mapped[dict] = mapped_column(JSON, default=dict)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[object] = mapped_column(JSON)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class AgentDecision(Base):
    __tablename__ = "agent_decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    skill_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    decision: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    next_step: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
