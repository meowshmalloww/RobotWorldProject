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


class PolicyTrainingRunRecord(Base):
    """Canonical local policy-training candidate; legacy dashboard runs stay separate."""

    __tablename__ = "policy_training_run_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="REQUESTED", index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    base_model_id: Mapped[str] = mapped_column(String(64), index=True)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    input_sha256: Mapped[str] = mapped_column(String(64), index=True)
    artifact_dir: Mapped[str] = mapped_column(String(1000))
    candidate_checkpoint_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    candidate_checkpoint_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class PolicyCandidateDecisionRecord(Base):
    """Auditable promotion/rejection state for one immutable policy candidate."""

    __tablename__ = "policy_candidate_decision_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    training_run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    candidate_model_id: Mapped[str] = mapped_column(String(64), index=True)
    previous_model_id: Mapped[str] = mapped_column(String(64), index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), index=True)
    evaluation_ids: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    command_id: Mapped[str] = mapped_column(String(64), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    promoted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


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


class ModelRegistrationRecord(Base):
    """Internal source of truth for a configured model connection.

    Secrets are referenced by environment-variable name and are never stored
    in this row.  Lifecycle changes are recorded separately in ``audit_events``.
    """

    __tablename__ = "model_registrations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    display_name: Mapped[str] = mapped_column(String(160))
    roles: Mapped[list] = mapped_column(JSON, default=list)
    provider_type: Mapped[str] = mapped_column(String(40))
    local_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    model_revision: Mapped[str | None] = mapped_column(String(200), nullable=True)
    api_key_env: Mapped[str | None] = mapped_column(String(160), nullable=True)
    expected_device: Mapped[str] = mapped_column(String(40), default="auto")
    precision: Mapped[str] = mapped_column(String(40), default="unknown")
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    license_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    lifecycle_state: Mapped[str] = mapped_column(String(24), default="REGISTERED", index=True)
    health_status: Mapped[str] = mapped_column(String(24), default="unknown")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_loaded_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    source: Mapped[str] = mapped_column(String(80), default="api")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class RobotRegistrationRecord(Base):
    """Revisioned canonical robot/embodiment metadata."""

    __tablename__ = "robot_registrations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    display_name: Mapped[str] = mapped_column(String(160))
    source_format: Mapped[str] = mapped_column(String(40))
    source_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_revision: Mapped[str | None] = mapped_column(String(200), nullable=True)
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="IMPORTED", index=True)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    license_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    source: Mapped[str] = mapped_column(String(80), default="api")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class CommandExecution(Base):
    """Durable command envelope shared by the UI and platform-agent tools."""

    __tablename__ = "command_executions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="RUNNING", index=True)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(80), default="user")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class AuditEvent(Base):
    """Append-only internal audit history for commands and state changes."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    command_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(100))
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(80), default="user")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class WorldTemplateRecord(Base):
    """Revisioned reusable semantic world template and runtime artifact."""

    __tablename__ = "world_template_records"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(180))
    backend: Mapped[str] = mapped_column(String(40))
    robot_id: Mapped[str] = mapped_column(String(64), index=True)
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    runtime_sha256: Mapped[str] = mapped_column(String(64))
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="AVAILABLE", index=True)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class EvaluationRunRecord(Base):
    """Durable state and structured evidence for an oracle or policy episode."""

    __tablename__ = "evaluation_run_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    robot_id: Mapped[str] = mapped_column(String(64), index=True)
    world_template_id: Mapped[str] = mapped_column(String(100), index=True)
    policy: Mapped[str] = mapped_column(String(120), index=True)
    seed: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_dir: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class FailureEventRecord(Base):
    """Immutable structured diagnosis derived from one authoritative evaluation."""

    __tablename__ = "failure_event_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    code: Mapped[str] = mapped_column(String(80), index=True)
    subsystem: Mapped[str] = mapped_column(String(40), index=True)
    certainty: Mapped[str] = mapped_column(String(32))
    classifier_revision: Mapped[str] = mapped_column(String(80))
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    recommended_action: Mapped[dict] = mapped_column(JSON, default=dict)
    event_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class CoverageObservationRecord(Base):
    """One evaluation projected into explicit configured curriculum bins."""

    __tablename__ = "coverage_observation_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scenario_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    taxonomy_revision: Mapped[str] = mapped_column(String(80))
    task_family: Mapped[str] = mapped_column(String(80), index=True)
    robot_id: Mapped[str] = mapped_column(String(64), index=True)
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    asset_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    policy: Mapped[str] = mapped_column(String(120), index=True)
    seed: Mapped[int] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean)
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class ScenarioSpecRecord(Base):
    """Durable proposed scenario; execution still requires oracle validation."""

    __tablename__ = "scenario_spec_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="PLANNED", index=True)
    task_family: Mapped[str] = mapped_column(String(80), index=True)
    robot_id: Mapped[str] = mapped_column(String(64), index=True)
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    asset_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_evaluation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scenario_fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    specification: Mapped[dict] = mapped_column(JSON, default=dict)
    oracle_required: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    source: Mapped[str] = mapped_column(String(80), default="curriculum_planner")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class ScenarioExecutionRecord(Base):
    """Durable oracle/VLA execution state for one immutable scenario spec."""

    __tablename__ = "scenario_execution_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    evaluation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    command_id: Mapped[str] = mapped_column(String(64), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class CurriculumPlanRecord(Base):
    """Auditable next-scenario decision with explicit budgets and stop reason."""

    __tablename__ = "curriculum_plan_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), index=True)
    robot_id: Mapped[str] = mapped_column(String(64), index=True)
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_evaluation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scenario_spec_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    request: Mapped[dict] = mapped_column(JSON, default=dict)
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    decision: Mapped[dict] = mapped_column(JSON, default=dict)
    command_id: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class AutonomousCurriculumRunRecord(Base):
    """Persisted, budget-bounded orchestration over canonical commands."""

    __tablename__ = "autonomous_curriculum_run_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    autonomy_mode: Mapped[str] = mapped_column(String(40), index=True)
    robot_id: Mapped[str] = mapped_column(String(64), index=True)
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    task_family: Mapped[str] = mapped_column(String(80), index=True)
    instruction: Mapped[str] = mapped_column(Text)
    request: Mapped[dict] = mapped_column(JSON, default=dict)
    budgets: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    command_id: Mapped[str] = mapped_column(String(64), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class AgentToolCallRecord(Base):
    """Durable, bounded record of a platform-agent tool invocation."""

    __tablename__ = "agent_tool_call_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(120), index=True)
    tool_version: Mapped[str] = mapped_column(String(40))
    effect: Mapped[str] = mapped_column(String(16))
    autonomy_mode: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(24), default="RUNNING", index=True)
    arguments_sha256: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    command_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    approval_decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(80), default="platform-agent")
    started_at: Mapped[datetime] = mapped_column(default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ApprovalDecisionRecord(Base):
    """One-use human policy decision bound to an exact tool and arguments hash."""

    __tablename__ = "approval_decision_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(120), index=True)
    arguments_sha256: Mapped[str] = mapped_column(String(64), index=True)
    approved: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(80), default="user")
    expires_at: Mapped[datetime] = mapped_column(index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class ObjectRequestRecord(Base):
    """Exact or category-level physical object request."""

    __tablename__ = "object_request_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    requested_name: Mapped[str] = mapped_column(String(240))
    manufacturer: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    model_number: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    sku: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    gtin: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(120), index=True)
    exact_identity: Mapped[bool] = mapped_column(Boolean, default=True)
    authoritative_domains: Mapped[list] = mapped_column(JSON, default=list)
    required_properties: Mapped[list] = mapped_column(JSON, default=list)
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="REQUESTED", index=True)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    source: Mapped[str] = mapped_column(String(80), default="api")
    request_sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class EvidenceRecordRow(Base):
    """Normalized evidence metadata; raw payloads live in the artifact store."""

    __tablename__ = "evidence_record_rows"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str] = mapped_column(String(1600))
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    source_domain: Mapped[str] = mapped_column(String(240), index=True)
    retrieved_at: Mapped[datetime] = mapped_column(index=True)
    collector_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    collector_version: Mapped[str | None] = mapped_column(String(160), nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    artifact_ref: Mapped[str] = mapped_column(String(1000))
    normalized: Mapped[dict] = mapped_column(JSON, default=dict)
    identity_claims: Mapped[dict] = mapped_column(JSON, default=dict)
    quality_errors: Mapped[list] = mapped_column(JSON, default=list)
    license_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class EvidenceBundleRecord(Base):
    """Immutable exact-identity resolution and physical-property bundle."""

    __tablename__ = "evidence_bundle_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    lifecycle_state: Mapped[str] = mapped_column(String(32), index=True)
    identity: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    properties: Mapped[list] = mapped_column(JSON, default=list)
    completeness: Mapped[float] = mapped_column(Float, default=0.0)
    identity_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    bundle_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    artifact_ref: Mapped[str] = mapped_column(String(1000))
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    source: Mapped[str] = mapped_column(String(80), default="recorded_brightdata")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class EvidenceCollectionRunRecord(Base):
    """Durable Scraper Studio collection and semantic-normalization run."""

    __tablename__ = "evidence_collection_run_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    collector_id: Mapped[str] = mapped_column(String(160), index=True)
    collector_version: Mapped[str | None] = mapped_column(String(160), nullable=True)
    input_urls: Mapped[list] = mapped_column(JSON, default=list)
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    bundle_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    command_id: Mapped[str] = mapped_column(String(64), index=True)
    provider_attempt: Mapped[int] = mapped_column(Integer, default=0)
    normalization_attempt: Mapped[int] = mapped_column(Integer, default=0)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=180.0)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class ScraperCollectorVersionRecord(Base):
    """Internal, revisioned source of truth for Scraper Studio collectors."""

    __tablename__ = "scraper_collector_version_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    collector_id: Mapped[str] = mapped_column(String(160), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    version_label: Mapped[str] = mapped_column(String(160))
    lifecycle_state: Mapped[str] = mapped_column(String(32), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    previous_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    output_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    schema_sha256: Mapped[str] = mapped_column(String(64), index=True)
    extractor_revision: Mapped[str] = mapped_column(String(200))
    provider_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    source: Mapped[str] = mapped_column(String(80), default="api")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class ScraperRepairRunRecord(Base):
    """Governed candidate repair, golden/canary evidence, and rollback state."""

    __tablename__ = "scraper_repair_run_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    lifecycle_state: Mapped[str] = mapped_column(String(40), index=True)
    collector_id: Mapped[str] = mapped_column(String(160), index=True)
    active_version_id: Mapped[str] = mapped_column(String(64), index=True)
    last_known_good_version_id: Mapped[str] = mapped_column(String(64), index=True)
    candidate_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    object_request_id: Mapped[str] = mapped_column(String(64), index=True)
    failure_bundle_id: Mapped[str] = mapped_column(String(64), index=True)
    provider_mode: Mapped[str] = mapped_column(String(40))
    repair_prompt: Mapped[str] = mapped_column(Text)
    failing_fields: Mapped[list] = mapped_column(JSON, default=list)
    failure_examples: Mapped[list] = mapped_column(JSON, default=list)
    test_cases: Mapped[dict] = mapped_column(JSON, default=dict)
    test_artifact_ref: Mapped[str] = mapped_column(String(1000))
    candidate_artifact_ref: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    candidate_artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_diff: Mapped[dict] = mapped_column(JSON, default=dict)
    record_diff: Mapped[dict] = mapped_column(JSON, default=dict)
    golden_report: Mapped[dict] = mapped_column(JSON, default=dict)
    canary_report: Mapped[dict] = mapped_column(JSON, default=dict)
    provider_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    command_id: Mapped[str] = mapped_column(String(64), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class CompiledAssetVersionRecord(Base):
    """Canonical immutable rigid/articulated asset version metadata."""

    __tablename__ = "compiled_asset_version_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    display_name: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(120), index=True)
    asset_kind: Mapped[str] = mapped_column(String(32), default="rigid", index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="IMPORTED", index=True)
    evidence_bundle_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_path: Mapped[str] = mapped_column(String(1200))
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    artifact_root: Mapped[str] = mapped_column(String(1000))
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_report: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    promotion_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    promotion_blockers: Mapped[list] = mapped_column(JSON, default=list)
    command_id: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)
