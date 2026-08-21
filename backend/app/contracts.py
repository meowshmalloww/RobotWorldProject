"""Versioned canonical contracts used by HTTP commands and worker boundaries.

The legacy dashboard DTOs remain in :mod:`app.schemas`; new control-plane
work starts here so model/robot state is explicit, schema-generatable, and
shared by humans and autonomous tools.
"""
from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        extra="forbid",
        use_enum_values=True,
    )


class ModelProviderType(StrEnum):
    LOCAL_PATH = "local_path"
    HUGGING_FACE = "hugging_face"
    OPENAI_COMPATIBLE = "openai_compatible"
    NATIVE_PROVIDER = "native_provider"
    LOCAL_SERVER = "local_server"


class ModelRole(StrEnum):
    PLATFORM_AGENT = "platform_agent"
    VLA_POLICY = "vla_policy"
    VISION_ENCODER = "vision_encoder"
    WORLD_MODEL = "world_model"
    IMAGE_TO_3D = "image_to_3d"
    PART_UNDERSTANDING = "part_understanding"
    EMBEDDING = "embedding"


class ModelLifecycle(StrEnum):
    REGISTERED = "REGISTERED"
    VALIDATING = "VALIDATING"
    AVAILABLE = "AVAILABLE"
    INVALID = "INVALID"
    LOADED = "LOADED"
    UNLOADING = "UNLOADING"


class RobotLifecycle(StrEnum):
    IMPORTED = "IMPORTED"
    PARSED = "PARSED"
    KINEMATICS_VALIDATED = "KINEMATICS_VALIDATED"
    PHYSICS_VALIDATED = "PHYSICS_VALIDATED"
    AVAILABLE = "AVAILABLE"
    REJECTED = "REJECTED"


class EvaluationLifecycle(StrEnum):
    QUEUED = "QUEUED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    CRASHED = "CRASHED"


class FailureCode(StrEnum):
    ASSET_LOAD_ERROR = "asset_load_error"
    INVALID_SCALE = "invalid_scale"
    INVALID_COLLIDER = "invalid_collider"
    INITIAL_PENETRATION = "initial_penetration"
    PHYSICS_INSTABILITY = "physics_instability"
    INVALID_JOINT = "invalid_joint"
    UNREACHABLE_TARGET = "unreachable_target"
    PRE_GRASP_COLLISION = "pre_grasp_collision"
    PERCEPTION_LOCALIZATION_FAILURE = "perception_localization_failure"
    GRASP_MISS = "grasp_miss"
    GRASP_SLIP = "grasp_slip"
    OBJECT_DROPPED = "object_dropped"
    WRONG_PART = "wrong_part"
    JOINT_RESISTANCE_CONTROL_FAILURE = "joint_resistance_control_failure"
    POLICY_TIMEOUT = "policy_timeout"
    INVALID_ACTION = "invalid_action"
    POLICY_INSTABILITY = "policy_instability"
    SUCCESS_PREDICATE_FAILURE = "success_predicate_failure"
    SCRAPER_EVIDENCE_FAILURE = "scraper_evidence_failure"
    GENERATOR_FAILURE = "generator_failure"
    WORKER_CRASH = "worker_crash"


class AutonomyMode(StrEnum):
    OBSERVE_ONLY = "OBSERVE_ONLY"
    PLAN_ONLY = "PLAN_ONLY"
    EXECUTE_WITH_APPROVAL = "EXECUTE_WITH_APPROVAL"
    AUTONOMOUS_WITH_BUDGETS = "AUTONOMOUS_WITH_BUDGETS"


class AgentToolEffect(StrEnum):
    QUERY = "QUERY"
    MUTATION = "MUTATION"


class ModelCapability(ContractModel):
    name: str = Field(min_length=1, max_length=100)
    supported: bool
    detail: dict[str, Any] = Field(default_factory=dict)


class ModelRegistrationCreate(ContractModel):
    display_name: str = Field(min_length=1, max_length=160)
    roles: list[ModelRole] = Field(min_length=1)
    provider_type: ModelProviderType
    local_path: str | None = Field(default=None, max_length=1000)
    base_url: str | None = Field(default=None, max_length=1000)
    model_id: str | None = Field(default=None, max_length=300)
    model_revision: str | None = Field(default=None, max_length=200)
    api_key_env: str | None = Field(default=None, max_length=160)
    expected_device: str = Field(default="auto", max_length=40)
    precision: str = Field(default="unknown", max_length=40)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    license_metadata: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("roles")
    @classmethod
    def unique_roles(cls, value: list[ModelRole]) -> list[ModelRole]:
        if len(set(value)) != len(value):
            raise ValueError("roles must not contain duplicates")
        return value

    @field_validator("api_key_env")
    @classmethod
    def environment_reference_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or not value.replace("_", "A").isalnum() or value.upper() != value or value[0].isdigit():
            raise ValueError("apiKeyEnv must be an uppercase environment-variable name")
        return value

    @model_validator(mode="after")
    def provider_fields(self) -> "ModelRegistrationCreate":
        if self.provider_type == ModelProviderType.LOCAL_PATH and not self.local_path:
            raise ValueError("localPath is required for providerType=local_path")
        if self.provider_type in {ModelProviderType.OPENAI_COMPATIBLE, ModelProviderType.LOCAL_SERVER} and not self.base_url:
            raise ValueError("baseUrl is required for endpoint-backed providers")
        if self.provider_type == ModelProviderType.HUGGING_FACE and not self.model_id:
            raise ValueError("modelId is required for providerType=hugging_face")
        return self


class ModelValidationRequest(ContractModel):
    compute_content_hash: bool = False


class ModelRegistrationView(ContractModel):
    id: str
    revision: int
    display_name: str
    roles: list[str]
    provider_type: str
    local_path: str | None = None
    base_url: str | None = None
    model_id: str | None = None
    model_revision: str | None = None
    api_key_env: str | None = None
    api_key_configured: bool = False
    expected_device: str
    precision: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    capabilities: dict[str, Any]
    license_metadata: dict[str, Any]
    lifecycle_state: str
    health_status: str
    enabled: bool
    manifest_sha256: str | None = None
    content_sha256: str | None = None
    last_error: str | None = None
    last_validated_at: datetime | None = None
    last_loaded_at: datetime | None = None
    created_by: str
    source: str
    created_at: datetime
    updated_at: datetime


class LinkSpec(ContractModel):
    id: str
    parent_id: str | None = None
    visual_artifacts: list[str] = Field(default_factory=list)
    collision_artifacts: list[str] = Field(default_factory=list)
    mass_kg: float | None = Field(default=None, gt=0)
    center_of_mass_m: tuple[float, float, float] | None = None
    inertia_kg_m2: tuple[float, float, float, float, float, float] | None = None


class JointSpec(ContractModel):
    id: str
    parent_link: str
    child_link: str
    joint_type: str
    axis: tuple[float, float, float] | None = None
    origin_xyz_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    lower: float | None = None
    upper: float | None = None
    velocity_limit: float | None = None
    effort_limit: float | None = None
    damping: float | None = Field(default=None, ge=0)
    friction: float | None = Field(default=None, ge=0)
    actuated: bool = False


class SensorSpec(ContractModel):
    id: str
    sensor_type: str
    parent_link: str
    translation_m: tuple[float, float, float]
    quaternion_wxyz: tuple[float, float, float, float]
    intrinsics: dict[str, Any] = Field(default_factory=dict)
    calibrated: bool = False
    calibration_source: str = "unverified"


class EmbodimentContract(ContractModel):
    base_type: str
    end_effectors: list[str]
    grippers: list[str] = Field(default_factory=list)
    observation_schema: dict[str, Any]
    action_schema: dict[str, Any]
    reset_pose: dict[str, float]
    safety_limits: dict[str, Any]
    controller: dict[str, Any]


class RobotDefinition(ContractModel):
    schema_version: str = "robotworld.robot.v1"
    id: str
    revision: int = Field(default=1, ge=1)
    display_name: str
    source_format: str
    source_path: str | None = None
    source_revision: str | None = None
    source_sha256: str | None = None
    links: list[LinkSpec]
    joints: list[JointSpec]
    sensors: list[SensorSpec]
    embodiment: EmbodimentContract
    license_metadata: dict[str, Any]
    lifecycle_state: RobotLifecycle
    validation_errors: list[str] = Field(default_factory=list)


class FrankaRegistrationRequest(ContractModel):
    source_path: str | None = Field(default=None, max_length=1000)
    allow_download: bool = False
    wrist_camera_translation_m: tuple[float, float, float] = (0.04, 0.0, 0.055)
    wrist_camera_quaternion_wxyz: tuple[float, float, float, float] = (0.0, 0.70710678, 0.70710678, 0.0)

    @field_validator("wrist_camera_translation_m")
    @classmethod
    def finite_mount_translation(cls, value: tuple[float, float, float]) -> tuple[float, float, float]:
        if any(not math.isfinite(item) or abs(item) > 1.0 for item in value):
            raise ValueError("wristCameraTranslationM must contain finite values within ±1 metre")
        return value

    @field_validator("wrist_camera_quaternion_wxyz")
    @classmethod
    def normalized_mount_quaternion(cls, value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        if any(not math.isfinite(item) for item in value):
            raise ValueError("wristCameraQuaternionWxyz must contain finite values")
        norm = math.sqrt(sum(item * item for item in value))
        if abs(norm - 1.0) > 1e-4:
            raise ValueError("wristCameraQuaternionWxyz must be normalized")
        return value


class CommandResult(ContractModel):
    command_id: str
    status: str
    reused: bool = False
    result: dict[str, Any] = Field(default_factory=dict)


class OracleEvaluationRequest(ContractModel):
    robot_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    seed: int = Field(default=0, ge=0, le=2**31 - 1)


class PlacementRequest(ContractModel):
    """Semantic constraints for deterministic geometry/physics placement.

    Callers select the support surface and variation dimensions; they cannot
    inject an unchecked XYZ pose. The backend derives, settles, and validates
    the executable pose from this immutable request.
    """

    schema_version: str = "robotworld.placement-request.v1"
    semantic_support_surface: str = Field(
        default="workspace_surface",
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z][A-Za-z0-9._-]+$",
    )
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    vary_position: bool = False
    vary_orientation: bool = False
    require_reachability: bool = True
    reject_penetration: bool = True
    drop_and_settle: bool = True
    scenario_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class CompiledAssetOracleRequest(OracleEvaluationRequest):
    asset_version_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    placement_request: PlacementRequest | None = None


class CompiledAssetVlaEvaluationRequest(CompiledAssetOracleRequest):
    model_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    instruction: str = Field(min_length=1, max_length=1000)
    max_policy_steps: int = Field(default=150, ge=1, le=1000)


class EvaluationAnalysisRequest(ContractModel):
    evaluation_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class ScenarioTargetToolInput(ContractModel):
    scenario_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class CoverageStateToolInput(ContractModel):
    robot_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    model_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    task_family: str = Field(default="pick_place", min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]+$")
    limit: int = Field(default=200, ge=1, le=500)


class CurriculumPlanRequest(ContractModel):
    schema_version: str = "robotworld.curriculum-plan-request.v1"
    robot_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    model_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    task_family: str = Field(default="pick_place", min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]+$")
    target_success_rate: float = Field(default=0.80, ge=0, le=1)
    minimum_attempts: int = Field(default=5, ge=1, le=1000)
    max_evaluation_episodes: int = Field(default=100, ge=1, le=100000)
    max_new_scenarios: int = Field(default=1, ge=0, le=100)
    lookback_limit: int = Field(default=200, ge=1, le=500)
    allowed_asset_version_ids: list[str] = Field(default_factory=list, max_length=100)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)

    @field_validator("allowed_asset_version_ids")
    @classmethod
    def unique_asset_versions(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("allowedAssetVersionIds must not contain duplicates")
        if any(not item or len(item) > 64 or any(not (char.isalnum() or char in "._-") for char in item) for item in value):
            raise ValueError("allowedAssetVersionIds contains an invalid ID")
        return value


class AutonomousRunBudgets(ContractModel):
    max_worlds: int = Field(default=1, ge=0, le=100)
    max_scrape_requests: int = Field(default=0, ge=0, le=1000)
    max_gpu_minutes: float = Field(default=0.0, ge=0, le=100000)
    max_evaluation_episodes: int = Field(default=2, ge=1, le=100000)
    max_retries: int = Field(default=0, ge=0, le=20)
    max_iterations: int = Field(default=1, ge=1, le=1000)
    max_consecutive_failures: int = Field(default=3, ge=1, le=1000)


class AutonomousCurriculumRunRequest(ContractModel):
    schema_version: str = "robotworld.autonomous-curriculum-run-request.v1"
    autonomy_mode: AutonomyMode = AutonomyMode.EXECUTE_WITH_APPROVAL
    robot_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    model_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    task_family: str = Field(default="pick_place", min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]+$")
    instruction: str = Field(default="Pick up the object and place it in the target.", min_length=1, max_length=1000)
    target_success_rate: float = Field(default=0.80, ge=0, le=1)
    minimum_attempts: int = Field(default=5, ge=1, le=1000)
    lookback_limit: int = Field(default=200, ge=1, le=500)
    allowed_asset_version_ids: list[str] = Field(default_factory=list, max_length=100)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    execute_vla: bool = True
    max_policy_steps: int = Field(default=150, ge=1, le=1000)
    budgets: AutonomousRunBudgets = Field(default_factory=AutonomousRunBudgets)

    @field_validator("allowed_asset_version_ids")
    @classmethod
    def valid_asset_versions(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("allowedAssetVersionIds must not contain duplicates")
        if any(not item or len(item) > 64 or any(not (char.isalnum() or char in "._-") for char in item) for item in value):
            raise ValueError("allowedAssetVersionIds contains an invalid ID")
        return value

    @model_validator(mode="after")
    def executable_budget_policy(self) -> "AutonomousCurriculumRunRequest":
        if self.autonomy_mode in {AutonomyMode.OBSERVE_ONLY, AutonomyMode.PLAN_ONLY}:
            raise ValueError("An executable curriculum run requires EXECUTE_WITH_APPROVAL or AUTONOMOUS_WITH_BUDGETS")
        if self.execute_vla and not self.model_id:
            raise ValueError("executeVla requires a selected modelId")
        minimum_episodes = 2 if self.execute_vla else 1
        if self.budgets.max_evaluation_episodes < minimum_episodes:
            raise ValueError(f"maxEvaluationEpisodes must be at least {minimum_episodes} for the selected stages")
        if self.execute_vla and self.budgets.max_gpu_minutes <= 0:
            raise ValueError("executeVla requires a positive maxGpuMinutes budget")
        if self.budgets.max_worlds <= 0:
            raise ValueError("An executable curriculum run requires maxWorlds greater than zero")
        return self


class AutonomousRunTargetToolInput(ContractModel):
    run_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class EvaluationResultContract(ContractModel):
    schema_version: str = "robotworld.evaluation-result.v1"
    run_id: str
    robot_id: str
    world_template_id: str
    world_template_revision: int
    world_runtime_sha256: str
    policy: str
    seed: int
    success: bool
    failure_code: str | None = None
    failure_detail: str | None = None
    duration_seconds: float = Field(ge=0)
    physics_hz: int = Field(gt=0)
    control_hz: int = Field(gt=0)
    phases: list[dict[str, Any]]
    trajectory: list[dict[str, Any]]
    contact_summary: dict[str, Any]
    predicate: dict[str, Any]
    frame_hashes: dict[str, dict[str, str]]


class VlaNormalizedAction(ContractModel):
    values: tuple[float, float, float, float, float, float, float]
    adapter_revision: str = Field(min_length=1, max_length=120)

    @field_validator("values")
    @classmethod
    def bounded_finite_action(cls, value: tuple[float, float, float, float, float, float, float]):
        if any(not math.isfinite(item) or item < -1.0 or item > 1.0 for item in value):
            raise ValueError("VLA normalized actions must be finite and bounded to [-1, 1]")
        return value


class EmptyToolInput(ContractModel):
    pass


class ModelTargetToolInput(ContractModel):
    model_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class ValidateModelToolInput(ModelTargetToolInput):
    compute_content_hash: bool = False


class RobotTargetToolInput(ContractModel):
    robot_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class EvaluationListToolInput(ContractModel):
    limit: int = Field(default=25, ge=1, le=100)


class EvaluationTargetToolInput(ContractModel):
    run_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class AuditListToolInput(ContractModel):
    entity_type: str | None = Field(default=None, min_length=1, max_length=40)
    entity_id: str | None = Field(default=None, min_length=1, max_length=64)
    limit: int = Field(default=50, ge=1, le=200)


class VlaBridgeStatusToolInput(ContractModel):
    model_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    robot_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class AgentToolDefinition(ContractModel):
    schema_version: str = "robotworld.agent-tool-definition.v1"
    name: str
    version: str
    description: str
    effect: AgentToolEffect
    permission: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    idempotency_supported: bool
    approval_required: bool
    autonomous_allowed: bool


class AgentToolCall(ContractModel):
    schema_version: str = "robotworld.agent-tool-call.v1"
    tool_name: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_.-]+$")
    arguments: dict[str, Any] = Field(default_factory=dict)
    autonomy_mode: AutonomyMode = AutonomyMode.OBSERVE_ONLY
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)
    approval_decision_id: str | None = Field(default=None, min_length=1, max_length=64)
    actor: str = Field(default="platform-agent", min_length=1, max_length=80)


class AgentToolCallResult(ContractModel):
    schema_version: str = "robotworld.agent-tool-call-result.v1"
    tool_call_id: str
    tool_name: str
    tool_version: str
    status: str
    data: dict[str, Any] = Field(default_factory=dict)
    command_id: str | None = None
    error: str | None = None


class ApprovalDecision(ContractModel):
    schema_version: str = "robotworld.approval-decision.v1"
    tool_name: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_.-]+$")
    arguments: dict[str, Any] = Field(default_factory=dict)
    approved: bool
    reason: str = Field(default="", max_length=1000)
    decided_by: str = Field(default="user", min_length=1, max_length=80)
    expires_in_seconds: int = Field(default=900, ge=30, le=86400)


class ObjectRequest(ContractModel):
    schema_version: str = "robotworld.object-request.v1"
    requested_name: str = Field(min_length=2, max_length=240)
    manufacturer: str | None = Field(default=None, min_length=1, max_length=200)
    model_number: str | None = Field(default=None, min_length=1, max_length=200)
    sku: str | None = Field(default=None, min_length=1, max_length=200)
    gtin: str | None = Field(default=None, min_length=8, max_length=14, pattern=r"^\d{8,14}$")
    category: str = Field(min_length=2, max_length=120)
    exact_identity: bool = True
    authoritative_domains: list[str] = Field(default_factory=list, max_length=20)
    required_properties: list[str] = Field(
        default_factory=lambda: ["manufacturer", "exact_identifier", "dimensions", "source_url"],
        min_length=1,
        max_length=20,
    )

    @field_validator("authoritative_domains")
    @classmethod
    def valid_domains(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for domain in value:
            item = domain.strip().lower().rstrip(".")
            if not item or "/" in item or ":" in item or " " in item or "." not in item:
                raise ValueError("authoritativeDomains must contain hostnames, not URLs")
            if item not in cleaned:
                cleaned.append(item)
        return cleaned

    @field_validator("required_properties")
    @classmethod
    def unique_required_properties(cls, value: list[str]) -> list[str]:
        allowed = {"manufacturer", "exact_identifier", "dimensions", "mass", "material", "image", "source_url"}
        if any(item not in allowed for item in value):
            raise ValueError(f"requiredProperties must use: {', '.join(sorted(allowed))}")
        if len(set(value)) != len(value):
            raise ValueError("requiredProperties must not contain duplicates")
        return value

    @model_validator(mode="after")
    def exact_request_has_identifiers(self) -> "ObjectRequest":
        if self.exact_identity and (not self.manufacturer or not any((self.model_number, self.sku, self.gtin))):
            raise ValueError("exactIdentity requests require manufacturer and at least one of modelNumber, sku, or gtin")
        return self


class RecordedEvidenceImport(ContractModel):
    schema_version: str = "robotworld.recorded-evidence-import.v1"
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=50)
    collector_id: str | None = Field(default=None, max_length=160, pattern=r"^c_[A-Za-z0-9_-]+$")
    collector_version: str | None = Field(default=None, max_length=160)
    source: str = Field(default="recorded_brightdata", pattern=r"^(recorded_brightdata|controlled_fixture)$")
    retrieved_at: datetime | None = None


class ObjectRequestTargetToolInput(ContractModel):
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class EvidenceBundleTargetToolInput(ContractModel):
    bundle_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class NormalizeRecordedEvidenceToolInput(ContractModel):
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    evidence: RecordedEvidenceImport


class BrightDataCollectionRequest(ContractModel):
    schema_version: str = "robotworld.brightdata-collection-request.v1"
    collector_id: str = Field(min_length=3, max_length=160, pattern=r"^c_[A-Za-z0-9_-]+$")
    collector_version: str | None = Field(default=None, max_length=160)
    input_urls: list[str] = Field(min_length=1, max_length=20)
    timeout_seconds: float = Field(default=180.0, ge=15.0, le=900.0)

    @field_validator("input_urls")
    @classmethod
    def unique_input_urls(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item or len(item) > 1600 for item in cleaned):
            raise ValueError("inputUrls must contain non-empty URLs of at most 1600 characters")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("inputUrls must not contain duplicates")
        return cleaned


class BrightDataCollectionToolInput(BrightDataCollectionRequest):
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class EvidenceCollectionTargetToolInput(ContractModel):
    collection_run_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class ScraperRepairProviderMode(StrEnum):
    CONTROLLED_FIXTURE = "controlled_fixture"
    BRIGHTDATA_LIVE = "brightdata_live"


class ScraperCollectorVersionCreate(ContractModel):
    schema_version: str = "robotworld.scraper-collector-version-create.v1"
    collector_id: str = Field(min_length=3, max_length=160, pattern=r"^c_[A-Za-z0-9_-]+$")
    version_label: str = Field(min_length=1, max_length=160)
    output_schema: dict[str, Any]
    extractor_revision: str = Field(min_length=1, max_length=200)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    activate: bool = False


class ScraperRepairCase(ContractModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    url: str = Field(min_length=1, max_length=1600)
    baseline_rows: list[dict[str, Any]] = Field(min_length=1, max_length=20)


class ScraperRepairCreate(ContractModel):
    schema_version: str = "robotworld.scraper-repair-create.v1"
    collector_id: str = Field(min_length=3, max_length=160, pattern=r"^c_[A-Za-z0-9_-]+$")
    active_version_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    object_request_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    failure_bundle_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    provider_mode: ScraperRepairProviderMode = ScraperRepairProviderMode.CONTROLLED_FIXTURE
    golden_cases: list[ScraperRepairCase] = Field(min_length=1, max_length=20)
    canary_cases: list[ScraperRepairCase] = Field(min_length=1, max_length=20)
    automatic_promotion: bool = False
    allow_schema_change: bool = False
    max_attempts: int = Field(default=2, ge=1, le=10)

    @model_validator(mode="after")
    def safe_policy(self) -> "ScraperRepairCreate":
        names = [case.name for case in self.golden_cases + self.canary_cases]
        if len(names) != len(set(names)):
            raise ValueError("goldenCases and canaryCases must have unique names")
        if self.provider_mode == ScraperRepairProviderMode.BRIGHTDATA_LIVE and self.automatic_promotion:
            raise ValueError("Bright Data live repair requires an explicit promotion decision")
        return self


class ScraperCandidateCase(ContractModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=20)


class ScraperRepairDraftSubmission(ContractModel):
    schema_version: str = "robotworld.scraper-repair-draft-submission.v1"
    candidate_version_label: str = Field(min_length=1, max_length=160)
    extractor_revision: str = Field(min_length=1, max_length=200)
    output_schema: dict[str, Any]
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    golden_outputs: list[ScraperCandidateCase] = Field(min_length=1, max_length=20)
    canary_outputs: list[ScraperCandidateCase] = Field(min_length=1, max_length=20)


class ScraperRepairDecisionValue(StrEnum):
    PROMOTE = "PROMOTE"
    REJECT = "REJECT"


class ScraperRepairDecision(ContractModel):
    schema_version: str = "robotworld.scraper-repair-decision.v1"
    decision: ScraperRepairDecisionValue
    reason: str = Field(min_length=3, max_length=1000)


class ScraperRepairRollback(ContractModel):
    schema_version: str = "robotworld.scraper-repair-rollback.v1"
    reason: str = Field(min_length=3, max_length=1000)
    provider_rollback_confirmed: bool = False


class ScraperRepairDemoRequest(ContractModel):
    schema_version: str = "robotworld.scraper-repair-demo-request.v1"
    automatic_promotion: bool = False


class ScraperCollectorTargetToolInput(ContractModel):
    collector_version_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class ScraperRepairTargetToolInput(ContractModel):
    repair_run_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class ScraperCollectorVersionsListToolInput(ContractModel):
    collector_id: str | None = Field(default=None, min_length=3, max_length=160, pattern=r"^c_[A-Za-z0-9_-]+$")


class ScraperRepairDecisionToolInput(ScraperRepairDecision):
    repair_run_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class ScraperRepairRollbackToolInput(ScraperRepairRollback):
    repair_run_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class PropertyEstimate(ContractModel):
    schema_version: str = "robotworld.property-estimate.v1"
    name: str
    value: float | str | list[float]
    unit: str | None = None
    method: str
    confidence: float = Field(ge=0, le=1)
    uncertainty_low: float | None = None
    uncertainty_high: float | None = None
    evidence_record_ids: list[str] = Field(min_length=1)


class ObjectIdentity(ContractModel):
    schema_version: str = "robotworld.object-identity.v1"
    manufacturer: str
    model_number: str | None = None
    sku: str | None = None
    gtin: str | None = None
    category: str
    exact: bool
    confidence: float = Field(ge=0, le=1)
    method: str
    evidence_record_ids: list[str]
    conflicts: list[str] = Field(default_factory=list)


class EvidenceRecord(ContractModel):
    schema_version: str = "robotworld.evidence-record.v1"
    id: str
    request_id: str
    source_url: str
    source_type: str
    source_domain: str
    retrieved_at: datetime
    collector_id: str | None = None
    collector_version: str | None = None
    content_sha256: str
    artifact_ref: str
    normalized: dict[str, Any]
    identity_claims: dict[str, Any]
    quality_errors: list[str]
    license_metadata: dict[str, Any]
    created_at: datetime


class EvidenceBundle(ContractModel):
    schema_version: str = "robotworld.evidence-bundle.v1"
    id: str
    request_id: str
    revision: int
    lifecycle_state: str
    identity: ObjectIdentity
    evidence_record_ids: list[str]
    properties: list[PropertyEstimate]
    completeness: float = Field(ge=0, le=1)
    identity_confidence: float = Field(ge=0, le=1)
    validation_errors: list[str]
    bundle_sha256: str
    artifact_ref: str
    created_by: str
    source: str
    created_at: datetime


class ArtifactReference(ContractModel):
    schema_version: str = "robotworld.artifact-reference.v1"
    id: str
    kind: str
    artifact_ref: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str
    immutable: bool = True


class RigidAssetCompileRequest(ContractModel):
    schema_version: str = "robotworld.rigid-asset-compile-request.v1"
    display_name: str = Field(min_length=2, max_length=200)
    category: str = Field(min_length=2, max_length=120)
    source_glb_path: str = Field(min_length=1, max_length=1200)
    expected_source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_asset_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    evidence_bundle_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    source_identity_scope: str = Field(default="unknown", pattern=r"^(exact|category_prior|unknown)$")
    dimensions_m: tuple[float, float, float] = Field(
        description="Measured width, height, and depth in metres. The compiler maps glTF Y-up to RobotWorld Z-up."
    )
    dimension_method: str = Field(min_length=2, max_length=120)
    dimension_confidence: float = Field(ge=0, le=1)
    mass_kg: float = Field(gt=0, le=10000)
    mass_method: str = Field(min_length=2, max_length=120)
    mass_confidence: float = Field(ge=0, le=1)
    friction_range: tuple[float, float] = (0.3, 0.8)
    restitution_range: tuple[float, float] = (0.0, 0.1)
    semantics: list[str] = Field(default_factory=list, max_length=30)
    affordances: list[str] = Field(default_factory=list, max_length=30)
    license_metadata: dict[str, Any] = Field(default_factory=dict)
    max_aspect_residual: float = Field(default=0.20, gt=0, le=0.50)
    max_visual_triangles: int = Field(default=1_000_000, ge=1000, le=5_000_000)

    @field_validator("dimensions_m")
    @classmethod
    def valid_dimensions(cls, value: tuple[float, float, float]) -> tuple[float, float, float]:
        if any(not math.isfinite(item) or item <= 0 or item > 20 for item in value):
            raise ValueError("dimensionsM must be finite positive metres no larger than 20 m")
        return value

    @field_validator("friction_range")
    @classmethod
    def valid_friction_range(cls, value: tuple[float, float]) -> tuple[float, float]:
        if not (0 <= value[0] <= value[1] <= 2):
            raise ValueError("frictionRange must be ordered within [0, 2]")
        return value

    @field_validator("restitution_range")
    @classmethod
    def valid_restitution_range(cls, value: tuple[float, float]) -> tuple[float, float]:
        if not (0 <= value[0] <= value[1] <= 1):
            raise ValueError("restitutionRange must be ordered within [0, 1]")
        return value

    @field_validator("semantics", "affordances")
    @classmethod
    def unique_labels(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip().lower() for item in value if item.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("semantic and affordance labels must be unique")
        return cleaned


class AssetManifest(ContractModel):
    schema_version: str = "robotworld.asset-manifest.v1"
    asset_id: str
    version_id: str
    version: int = Field(ge=1)
    display_name: str
    category: str
    lifecycle_state: str
    source_visual: ArtifactReference
    visual_artifacts: list[ArtifactReference]
    collision_artifacts: list[ArtifactReference]
    openusd_artifacts: list[ArtifactReference]
    runtime_artifacts: list[ArtifactReference]
    validation_artifacts: list[ArtifactReference]
    coordinate_convention: dict[str, Any]
    dimensions_m: tuple[float, float, float]
    uniform_scale: float = Field(gt=0)
    mass_kg: float = Field(gt=0)
    center_of_mass_m: tuple[float, float, float]
    inertia_kg_m2: tuple[float, float, float, float, float, float]
    material: dict[str, Any]
    semantics: list[str]
    affordances: list[str]
    evidence_bundle_id: str | None = None
    provenance: dict[str, Any]
    validation_errors: list[str]
    promotion_eligible: bool
    promotion_blockers: list[str]
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_by: str
    created_at: datetime


class AssetVersionTargetToolInput(ContractModel):
    version_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
