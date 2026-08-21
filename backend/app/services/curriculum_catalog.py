"""Structured failure evidence, coverage bins, and deterministic next-scenario planning.

This is the canonical curriculum path for authoritative ``EvaluationRunRecord``
episodes.  It does not use the legacy refrigerator demo, an LLM-generated XYZ
pose, or an invented success score.  Every bin and stop threshold is explicit
in the request or the versioned taxonomy below.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..contracts import CompiledAssetOracleRequest, CurriculumPlanRequest, FailureCode, PlacementRequest
from ..db import SessionLocal
from ..models import (
    AuditEvent,
    CompiledAssetVersionRecord,
    CommandExecution,
    CoverageObservationRecord,
    CurriculumPlanRecord,
    EvaluationRunRecord,
    FailureEventRecord,
    ModelRegistrationRecord,
    RobotRegistrationRecord,
    ScenarioExecutionRecord,
    ScenarioSpecRecord,
)
from ..telemetry import span
from ..util import new_id
from . import command_store, evaluation_catalog


CLASSIFIER_REVISION = "structured-failure-v2"
TAXONOMY_REVISION = "pick-place-coverage-v1"
TERMINAL_EVALUATION_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "CRASHED"}
KNOWN_FAILURE_CODES = {item.value for item in FailureCode}
MATERIALIZABLE_PLACEMENT_VARIATIONS = {
    "baseline_policy_evaluation",
    "object_pose",
    "orientation",
    "support_region",
}

CONFIGURED_BINS: dict[str, tuple[str, ...]] = {
    "size": ("small", "medium", "large"),
    "aspectRatio": ("compact", "elongated", "slender"),
    "mass": ("light", "medium", "heavy"),
    "friction": ("low", "medium", "high"),
}

SUBSYSTEM_BY_CODE = {
    "asset_load_error": "asset",
    "invalid_scale": "asset",
    "invalid_collider": "asset",
    "initial_penetration": "world",
    "physics_instability": "physics",
    "invalid_joint": "asset",
    "unreachable_target": "world",
    "pre_grasp_collision": "world",
    "perception_localization_failure": "policy",
    "grasp_miss": "policy",
    "grasp_slip": "policy",
    "object_dropped": "policy",
    "wrong_part": "policy",
    "joint_resistance_control_failure": "control",
    "policy_timeout": "policy",
    "invalid_action": "policy",
    "policy_instability": "policy",
    "success_predicate_failure": "evaluation",
    "scraper_evidence_failure": "evidence",
    "generator_failure": "generator",
    "worker_crash": "worker",
}

VARIATIONS_BY_FAILURE = {
    "unreachable_target": ["support_region", "object_pose"],
    "pre_grasp_collision": ["object_pose", "approach_clearance"],
    "perception_localization_failure": ["object_pose", "camera", "lighting"],
    "grasp_miss": ["object_pose", "orientation"],
    "grasp_slip": ["friction", "mass", "orientation"],
    "object_dropped": ["friction", "mass", "transport_acceleration"],
    "wrong_part": ["appearance", "clutter", "part_semantics"],
    "joint_resistance_control_failure": ["joint_resistance", "handle_pose"],
    "policy_timeout": ["object_pose", "target_location"],
    "success_predicate_failure": ["target_location", "settle_properties"],
}


class CurriculumError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf8")
    return hashlib.sha256(encoded).hexdigest()


def _failure_event_view(row: FailureEventRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "evaluationId": row.evaluation_id,
        "code": row.code,
        "subsystem": row.subsystem,
        "certainty": row.certainty,
        "classifierRevision": row.classifier_revision,
        "evidence": dict(row.evidence or {}),
        "recommendedAction": dict(row.recommended_action or {}),
        "eventSha256": row.event_sha256,
        "createdAt": row.created_at,
    }


def _coverage_view(row: CoverageObservationRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "evaluationId": row.evaluation_id,
        "scenarioFingerprint": row.scenario_fingerprint,
        "taxonomyRevision": row.taxonomy_revision,
        "taskFamily": row.task_family,
        "robotId": row.robot_id,
        "modelId": row.model_id,
        "assetVersionId": row.asset_version_id,
        "policy": row.policy,
        "seed": row.seed,
        "success": row.success,
        "failureCode": row.failure_code,
        "dimensions": dict(row.dimensions or {}),
        "createdAt": row.created_at,
    }


def _scenario_view(row: ScenarioSpecRecord | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "revision": row.revision,
        "lifecycleState": row.lifecycle_state,
        "taskFamily": row.task_family,
        "robotId": row.robot_id,
        "modelId": row.model_id,
        "assetVersionId": row.asset_version_id,
        "sourceEvaluationId": row.source_evaluation_id,
        "scenarioFingerprint": row.scenario_fingerprint,
        "specification": dict(row.specification or {}),
        "oracleRequired": row.oracle_required,
        "createdBy": row.created_by,
        "source": row.source,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def _scenario_execution_view(row: ScenarioExecutionRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "scenarioId": row.scenario_id,
        "stage": row.stage,
        "status": row.status,
        "evaluationId": row.evaluation_id,
        "commandId": row.command_id,
        "error": row.error,
        "startedAt": row.started_at,
        "finishedAt": row.finished_at,
        "createdBy": row.created_by,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def _plan_view(row: CurriculumPlanRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "revision": row.revision,
        "status": row.status,
        "robotId": row.robot_id,
        "modelId": row.model_id,
        "sourceEvaluationId": row.source_evaluation_id,
        "scenarioSpecId": row.scenario_spec_id,
        "request": dict(row.request or {}),
        "analysis": dict(row.analysis or {}),
        "decision": dict(row.decision or {}),
        "commandId": row.command_id,
        "createdBy": row.created_by,
        "createdAt": row.created_at,
    }


def _model_id(evaluation: EvaluationRunRecord) -> str | None:
    predicate = dict((evaluation.result or {}).get("predicate") or {})
    explicit = predicate.get("modelRegistrationId")
    if explicit:
        return str(explicit)
    if evaluation.policy.startswith("vla-jepa:"):
        parts = evaluation.policy.split(":")
        return parts[1] if len(parts) >= 3 else None
    return None


def _asset_version_id(evaluation: EvaluationRunRecord) -> str | None:
    value = ((evaluation.result or {}).get("predicate") or {}).get("assetVersionId")
    return str(value) if value else None


def _bounded_predicate(result: dict[str, Any]) -> dict[str, Any]:
    predicate = dict(result.get("predicate") or {})
    allowed = (
        "assetVersionId",
        "modelRegistrationId",
        "contained",
        "onSupportSurface",
        "settled",
        "released",
        "targetErrorM",
        "containmentResidualM",
        "maxGraspLiftM",
        "policySteps",
        "finalLinearSpeedMps",
        "finalAngularSpeedRadS",
        "settlePositionSpanM",
        "settleRotationSpanRad",
    )
    return {key: predicate[key] for key in allowed if key in predicate}


def _recommended_action(code: str, *, policy_is_vla: bool, oracle_validated: bool) -> dict[str, Any]:
    if code in {"asset_load_error", "invalid_scale", "invalid_collider", "invalid_joint"}:
        return {
            "action": "REJECT_OR_RECOMPILE_ASSET",
            "reason": "The world is not valid enough for learned-policy evaluation.",
            "varyDimensions": [],
            "oracleRequired": True,
        }
    if code in {"initial_penetration", "physics_instability"}:
        return {
            "action": "REPAIR_WORLD_OR_PHYSICS",
            "reason": "Placement/physics validity must pass before any policy retry.",
            "varyDimensions": ["object_pose"] if code == "initial_penetration" else ["physical_properties"],
            "oracleRequired": True,
        }
    if code in {"worker_crash", "invalid_action", "policy_instability"}:
        return {
            "action": "REPAIR_POLICY_RUNTIME",
            "reason": "Do not create a new world until the policy worker/action contract is valid.",
            "varyDimensions": [],
            "oracleRequired": False,
        }
    if code in {"scraper_evidence_failure", "generator_failure"}:
        return {
            "action": "REPAIR_UPSTREAM_ASSET_PIPELINE",
            "reason": "No scenario should be evaluated from invalid evidence or generation output.",
            "varyDimensions": [],
            "oracleRequired": True,
        }
    variations = list(VARIATIONS_BY_FAILURE.get(code, ["object_pose"]))
    action = "REUSE_VALID_ASSET_TARGETED_VARIATION" if policy_is_vla and oracle_validated else "REVISE_WORLD_AND_ORACLE_VALIDATE"
    return {
        "action": action,
        "reason": (
            "A deterministic oracle passed this asset/world family, so target the measured policy failure."
            if policy_is_vla and oracle_validated
            else "World validity is not established for this failure; validate the revised scenario with the oracle first."
        ),
        "varyDimensions": variations,
        "oracleRequired": True,
    }


async def _oracle_counterpart(session, evaluation: EvaluationRunRecord) -> EvaluationRunRecord | None:
    asset_version_id = _asset_version_id(evaluation)
    if not evaluation.policy.startswith("vla-jepa:") or not asset_version_id:
        return None
    rows = (
        await session.execute(
            select(EvaluationRunRecord)
            .where(
                EvaluationRunRecord.robot_id == evaluation.robot_id,
                EvaluationRunRecord.success.is_(True),
                EvaluationRunRecord.created_at <= evaluation.created_at,
            )
            .order_by(EvaluationRunRecord.created_at.desc())
            .limit(100)
        )
    ).scalars().all()
    return next(
        (
            row
            for row in rows
            if not row.policy.startswith("vla-jepa:") and _asset_version_id(row) == asset_version_id
        ),
        None,
    )


async def _classify(session, evaluation: EvaluationRunRecord) -> tuple[dict[str, Any], FailureEventRecord | None]:
    result = dict(evaluation.result or {})
    trajectory = list(result.get("trajectory") or [])
    non_finite_steps = sum(1 for item in trajectory if isinstance(item, dict) and item.get("finite") is False)
    oracle = await _oracle_counterpart(session, evaluation)
    oracle_validated = oracle is not None
    common_evidence = {
        "evaluationStatus": evaluation.status,
        "policy": evaluation.policy,
        "success": evaluation.success,
        "trajectorySteps": len(trajectory),
        "nonFiniteSteps": non_finite_steps,
        "failureCodeSignal": evaluation.failure_code,
        "failureDetail": (evaluation.failure_detail or "")[:500] or None,
        "predicate": _bounded_predicate(result),
        "contactSamples": int((result.get("contactSummary") or {}).get("samples") or 0),
        "oracleCounterpartEvaluationId": oracle.id if oracle else None,
        "oracleCounterpartPassed": oracle_validated,
    }
    if evaluation.success is True and evaluation.status == "SUCCEEDED":
        return {
            "evaluationId": evaluation.id,
            "outcome": "SUCCESS",
            "failureEvent": None,
            "evidence": common_evidence,
        }, None

    existing = await session.scalar(
        select(FailureEventRecord).where(FailureEventRecord.evaluation_id == evaluation.id)
    )
    if existing is not None:
        return {
            "evaluationId": evaluation.id,
            "outcome": "FAILURE",
            "failureEvent": _failure_event_view(existing),
            "evidence": dict(existing.evidence or {}),
        }, existing

    signal = str(evaluation.failure_code or result.get("failureCode") or "").strip().lower()
    certainty = "direct_signal"
    if signal in KNOWN_FAILURE_CODES:
        code = signal
    elif evaluation.status == "CRASHED" or not result:
        code = "worker_crash"
        certainty = "derived_signal"
    elif non_finite_steps:
        code = "policy_instability"
        certainty = "derived_signal"
    else:
        code = "success_predicate_failure"
        certainty = "insufficient_evidence"
    policy_is_vla = evaluation.policy.startswith("vla-jepa:")
    recommendation = _recommended_action(code, policy_is_vla=policy_is_vla, oracle_validated=oracle_validated)
    evidence = {**common_evidence, "classificationRule": f"{CLASSIFIER_REVISION}:{code}"}
    event_payload = {
        "evaluationId": evaluation.id,
        "code": code,
        "subsystem": SUBSYSTEM_BY_CODE[code],
        "certainty": certainty,
        "classifierRevision": CLASSIFIER_REVISION,
        "evidence": evidence,
        "recommendedAction": recommendation,
    }
    event_sha256 = _canonical_sha256(event_payload)
    event = FailureEventRecord(
        id=new_id("failure"),
        evaluation_id=evaluation.id,
        code=code,
        subsystem=SUBSYSTEM_BY_CODE[code],
        certainty=certainty,
        classifier_revision=CLASSIFIER_REVISION,
        evidence=evidence,
        recommended_action=recommendation,
        event_sha256=event_sha256,
    )
    session.add(event)
    return {
        "evaluationId": evaluation.id,
        "outcome": "FAILURE",
        "failureEvent": _failure_event_view(event),
        "evidence": evidence,
    }, event


def _size_bin(dimensions: list[float]) -> str:
    maximum = max(dimensions)
    return "small" if maximum < 0.08 else "medium" if maximum < 0.20 else "large"


def _aspect_bin(dimensions: list[float]) -> str:
    ratio = max(dimensions) / max(min(dimensions), 1e-9)
    return "compact" if ratio < 1.5 else "elongated" if ratio < 3.0 else "slender"


def _mass_bin(mass_kg: float) -> str:
    return "light" if mass_kg < 0.25 else "medium" if mass_kg < 1.0 else "heavy"


def _friction_bin(friction: float) -> str:
    return "low" if friction < 0.35 else "medium" if friction < 0.70 else "high"


def _asset_configured_bins(asset: CompiledAssetVersionRecord) -> dict[str, str]:
    manifest = dict(asset.manifest or {})
    dimensions_raw = manifest.get("dimensionsM")
    dimensions_m = [float(value) for value in dimensions_raw] if isinstance(dimensions_raw, list) and len(dimensions_raw) == 3 else []
    mass_value = manifest.get("massKg")
    friction_range = (manifest.get("material") or {}).get("frictionRange")
    friction_midpoint = None
    if isinstance(friction_range, list) and len(friction_range) == 2:
        friction_midpoint = (float(friction_range[0]) + float(friction_range[1])) / 2.0
    return {
        "size": _size_bin(dimensions_m) if dimensions_m else "unknown",
        "aspectRatio": _aspect_bin(dimensions_m) if dimensions_m else "unknown",
        "mass": _mass_bin(float(mass_value)) if isinstance(mass_value, (int, float)) else "unknown",
        "friction": _friction_bin(friction_midpoint) if friction_midpoint is not None else "unknown",
    }


def _scenario_validation_errors(specification: dict[str, Any]) -> list[str]:
    variations = set(specification.get("variationDimensions") or [])
    errors: list[str] = []
    if specification.get("assetReuseRequired") and variations & {"size", "aspectRatio", "shapeFamily"}:
        errors.append("immutable asset reuse cannot vary size, aspectRatio, or shapeFamily")
    placement = specification.get("placementConstraints")
    if not isinstance(placement, dict) or not placement.get("semanticSupportSurface"):
        errors.append("semantic placement constraints are required")
    if specification.get("oracleBeforeVla") is not True:
        errors.append("oracleBeforeVla must be true")
    return errors


def placement_request_for_scenario(
    specification: dict[str, Any],
    scenario_fingerprint: str,
) -> tuple[list[str], int, PlacementRequest | None]:
    """Validate materializable dimensions and derive a semantic request."""

    variations = list(specification.get("variationDimensions") or [])
    unsupported_variations = sorted(set(variations) - MATERIALIZABLE_PLACEMENT_VARIATIONS)
    if unsupported_variations:
        raise CurriculumError(
            "The current scenario executor cannot materialize these variations: "
            + ", ".join(unsupported_variations)
        )
    if "baseline_policy_evaluation" in variations and len(variations) != 1:
        raise CurriculumError("baseline_policy_evaluation cannot be combined with targeted variations.")
    if not variations:
        raise CurriculumError("Scenario has no materializable variation dimensions.")
    placement = dict(specification.get("placementConstraints") or {})
    seed = placement.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2**31 - 1:
        raise CurriculumError("Scenario placement seed is missing or invalid.")
    if variations == ["baseline_policy_evaluation"]:
        return variations, seed, None
    request = PlacementRequest(
        semanticSupportSurface=str(placement.get("semanticSupportSurface") or ""),
        seed=seed,
        varyPosition=bool({"object_pose", "support_region"} & set(variations)),
        varyOrientation="orientation" in variations,
        requireReachability=placement.get("requireReachability") is True,
        rejectPenetration=placement.get("rejectPenetration") is True,
        dropAndSettle=placement.get("dropAndSettle") is True,
        scenarioFingerprint=scenario_fingerprint,
    )
    return variations, seed, request


async def _reject_invalid_scenarios(session, *, command_id: str, actor: str) -> int:
    rows = (
        await session.execute(
            select(ScenarioSpecRecord).where(ScenarioSpecRecord.lifecycle_state == "PLANNED")
        )
    ).scalars().all()
    rejected = 0
    for row in rows:
        errors = _scenario_validation_errors(dict(row.specification or {}))
        if not errors:
            continue
        row.lifecycle_state = "REJECTED"
        row.updated_at = _now()
        session.add(
            AuditEvent(
                command_id=command_id,
                entity_type="scenario_spec",
                entity_id=row.id,
                action="scenario.reject_invalid",
                from_state="PLANNED",
                to_state="REJECTED",
                detail={"validationErrors": errors},
                actor=actor,
            )
        )
        rejected += 1
    return rejected


async def _coverage_dimensions(session, evaluation: EvaluationRunRecord) -> tuple[dict[str, Any], str]:
    result = dict(evaluation.result or {})
    predicate = dict(result.get("predicate") or {})
    asset_version_id = _asset_version_id(evaluation)
    asset = await session.get(CompiledAssetVersionRecord, asset_version_id) if asset_version_id else None
    manifest = dict(asset.manifest or {}) if asset else {}
    configured_bins = _asset_configured_bins(asset) if asset else {
        "size": "unknown",
        "aspectRatio": "unknown",
        "mass": "unknown",
        "friction": "unknown",
    }
    placement = dict(predicate.get("placementEvidence") or {})
    dimensions: dict[str, Any] = {
        **configured_bins,
        "shapeFamily": str(manifest.get("category") or asset.category if asset else "unknown"),
        "pose": f"stable_pose_{placement.get('stablePoseIndex')}" if placement.get("stablePoseIndex") is not None else "unknown",
        "orientation": f"jaw_axis_{placement.get('gripperClosingAxisIndex')}" if placement.get("gripperClosingAxisIndex") is not None else "unknown",
        "clutter": "single_object",
        "targetLocation": "tabletop_target_volume",
        "cameraSet": "front+wrist" if evaluation.policy.startswith("vla-jepa:") else "recorded_front+wrist",
    }
    scenario_payload = {
        "taxonomyRevision": TAXONOMY_REVISION,
        "taskFamily": "pick_place",
        "robotId": evaluation.robot_id,
        "worldRuntimeSha256": result.get("worldRuntimeSha256"),
        "assetManifestSha256": predicate.get("assetManifestSha256"),
        "assetVersionId": asset_version_id,
        "placement": placement,
        "target": dimensions["targetLocation"],
    }
    return dimensions, _canonical_sha256(scenario_payload)


async def _persist_analysis(
    evaluation_id: str,
    *,
    actor: str,
    command_id: str | None,
) -> dict[str, Any]:
    async with SessionLocal() as session:
        evaluation = await session.get(EvaluationRunRecord, evaluation_id)
        if evaluation is None:
            raise KeyError(evaluation_id)
        if evaluation.status not in TERMINAL_EVALUATION_STATES:
            raise CurriculumError(f"Evaluation {evaluation_id} is {evaluation.status}; only terminal runs can be analyzed.")
        classification, event = await _classify(session, evaluation)
        observation = await session.scalar(
            select(CoverageObservationRecord).where(CoverageObservationRecord.evaluation_id == evaluation.id)
        )
        if observation is None:
            dimensions, fingerprint = await _coverage_dimensions(session, evaluation)
            observation = CoverageObservationRecord(
                id=new_id("coverage"),
                evaluation_id=evaluation.id,
                scenario_fingerprint=fingerprint,
                taxonomy_revision=TAXONOMY_REVISION,
                task_family="pick_place",
                robot_id=evaluation.robot_id,
                model_id=_model_id(evaluation),
                asset_version_id=_asset_version_id(evaluation),
                policy=evaluation.policy,
                seed=evaluation.seed,
                success=bool(evaluation.success),
                failure_code=evaluation.failure_code,
                dimensions=dimensions,
            )
            session.add(observation)
            session.add(
                AuditEvent(
                    command_id=command_id,
                    entity_type="coverage_observation",
                    entity_id=observation.id,
                    action="coverage.observe",
                    from_state=None,
                    to_state="RECORDED",
                    detail={"evaluationId": evaluation.id, "scenarioFingerprint": fingerprint},
                    actor=actor,
                )
            )
        if event is not None and event.id not in {
            value
            for value in (
                await session.execute(
                    select(AuditEvent.entity_id).where(
                        AuditEvent.entity_type == "failure_event",
                        AuditEvent.entity_id == event.id,
                    )
                )
            ).scalars()
        }:
            session.add(
                AuditEvent(
                    command_id=command_id,
                    entity_type="failure_event",
                    entity_id=event.id,
                    action="failure.classify",
                    from_state=None,
                    to_state="CLASSIFIED",
                    detail={"evaluationId": evaluation.id, "code": event.code, "certainty": event.certainty},
                    actor=actor,
                )
            )
        await session.commit()
        return {"classification": classification, "coverageObservation": _coverage_view(observation)}


async def analyze_evaluation(
    evaluation_id: str,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    try:
        command, reused = await command_store.start_command(
            kind="evaluation.analyze",
            target_type="evaluation",
            target_id=evaluation_id,
            payload={"evaluationId": evaluation_id, "classifierRevision": CLASSIFIER_REVISION},
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except command_store.CommandConflict as exc:
        raise CurriculumError(str(exc)) from exc
    if reused:
        return command_store.command_view(command, reused=True)
    try:
        with span("failure.classify", evaluation_id=evaluation_id):
            output = await _persist_analysis(evaluation_id, actor=actor, command_id=command.id)
    except Exception as exc:
        await command_store.finish_command(command.id, error=str(exc))
        raise
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_store.command_view(command)


async def list_failure_events(limit: int = 100) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(FailureEventRecord).order_by(FailureEventRecord.created_at.desc()).limit(max(1, min(limit, 500)))
            )
        ).scalars().all()
    return [_failure_event_view(row) for row in rows]


async def coverage_state(
    *,
    robot_id: str | None = None,
    model_id: str | None = None,
    task_family: str = "pick_place",
    limit: int = 200,
) -> dict[str, Any]:
    async with SessionLocal() as session:
        query = (
            select(CoverageObservationRecord)
            .where(CoverageObservationRecord.task_family == task_family)
            .order_by(CoverageObservationRecord.created_at.desc())
            .limit(max(1, min(limit, 500)))
        )
        if robot_id:
            query = query.where(CoverageObservationRecord.robot_id == robot_id)
        if model_id:
            query = query.where(CoverageObservationRecord.model_id == model_id)
        rows = (await session.execute(query)).scalars().all()
    dimensions: dict[str, Any] = {}
    for name, configured in CONFIGURED_BINS.items():
        counts = {bucket: 0 for bucket in configured}
        unknown = 0
        for row in rows:
            bucket = (row.dimensions or {}).get(name)
            if bucket in counts:
                counts[bucket] += 1
            else:
                unknown += 1
        covered = sum(1 for count in counts.values() if count > 0)
        dimensions[name] = {
            "configuredBins": list(configured),
            "counts": counts,
            "unknownCount": unknown,
            "coveredBins": covered,
            "coverageFraction": covered / len(configured),
            "underrepresentedBins": [bucket for bucket, count in counts.items() if count == min(counts.values(), default=0)],
        }
    dynamic: dict[str, dict[str, int]] = {}
    for name in ("shapeFamily", "pose", "orientation", "clutter", "targetLocation", "cameraSet"):
        counts: dict[str, int] = {}
        for row in rows:
            value = str((row.dimensions or {}).get(name) or "unknown")
            counts[value] = counts.get(value, 0) + 1
        dynamic[name] = counts
    return {
        "schemaVersion": "robotworld.coverage-state.v1",
        "taxonomyRevision": TAXONOMY_REVISION,
        "taskFamily": task_family,
        "robotId": robot_id,
        "modelId": model_id,
        "sampleCount": len(rows),
        "uniqueScenarioCount": len({row.scenario_fingerprint for row in rows}),
        "successCount": sum(1 for row in rows if row.success),
        "failureCounts": {
            code: sum(1 for row in rows if row.failure_code == code)
            for code in sorted({row.failure_code for row in rows if row.failure_code})
        },
        "dimensions": dimensions,
        "dynamicDimensions": dynamic,
        "observations": [_coverage_view(row) for row in rows],
    }


def _wilson_interval(successes: int, samples: int) -> tuple[float, float] | None:
    if samples <= 0:
        return None
    z = 1.959963984540054
    proportion = successes / samples
    denominator = 1.0 + z * z / samples
    center = (proportion + z * z / (2.0 * samples)) / denominator
    margin = z * math.sqrt(proportion * (1.0 - proportion) / samples + z * z / (4.0 * samples * samples)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


async def _index_terminal_evaluations(rows: list[EvaluationRunRecord], *, actor: str, command_id: str) -> None:
    for row in rows:
        await _persist_analysis(row.id, actor=actor, command_id=command_id)


async def plan_next(
    request: CurriculumPlanRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", by_alias=True)
    try:
        command, reused = await command_store.start_command(
            kind="curriculum.plan_next",
            target_type="robot",
            target_id=request.robot_id,
            payload=payload,
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except command_store.CommandConflict as exc:
        raise CurriculumError(str(exc)) from exc
    if reused:
        return command_store.command_view(command, reused=True)
    try:
        if request.task_family != "pick_place":
            raise CurriculumError("The current canonical coverage taxonomy supports taskFamily=pick_place only.")
        async with SessionLocal() as session:
            robot = await session.get(RobotRegistrationRecord, request.robot_id)
            if robot is None:
                raise KeyError(request.robot_id)
            if robot.lifecycle_state != "AVAILABLE":
                raise CurriculumError("Curriculum planning requires an AVAILABLE robot revision.")
            if request.model_id:
                model = await session.get(ModelRegistrationRecord, request.model_id)
                if model is None:
                    raise KeyError(request.model_id)
                if "vla_policy" not in (model.roles or []):
                    raise CurriculumError("Selected model is not registered with the vla_policy role.")
            evaluations = (
                await session.execute(
                    select(EvaluationRunRecord)
                    .where(
                        EvaluationRunRecord.robot_id == request.robot_id,
                        EvaluationRunRecord.status.in_(TERMINAL_EVALUATION_STATES),
                    )
                    .order_by(EvaluationRunRecord.created_at.desc())
                    .limit(request.lookback_limit)
                )
            ).scalars().all()
        with span("curriculum.query_coverage", robot_id=request.robot_id, model_id=request.model_id or "oracle"):
            await _index_terminal_evaluations(evaluations, actor=actor, command_id=command.id)
        selected = [row for row in evaluations if _model_id(row) == request.model_id] if request.model_id else [row for row in evaluations if _model_id(row) is None]
        attempts = len(selected)
        successes = sum(1 for row in selected if row.success)
        interval = _wilson_interval(successes, attempts)
        coverage = await coverage_state(
            robot_id=request.robot_id,
            model_id=request.model_id,
            task_family=request.task_family,
            limit=request.lookback_limit,
        )
        async with SessionLocal() as session:
            rejected_invalid_scenarios = await _reject_invalid_scenarios(
                session,
                command_id=command.id,
                actor=actor,
            )
            failure_rows = (
                await session.execute(
                    select(FailureEventRecord)
                    .where(FailureEventRecord.evaluation_id.in_([row.id for row in selected] or ["__none__"]))
                    .order_by(FailureEventRecord.created_at.desc())
                )
            ).scalars().all()
            assets_query = select(CompiledAssetVersionRecord).where(
                CompiledAssetVersionRecord.lifecycle_state == "ORACLE_VALIDATED"
            )
            if request.allowed_asset_version_ids:
                assets_query = assets_query.where(
                    CompiledAssetVersionRecord.id.in_(request.allowed_asset_version_ids)
                )
            valid_assets = (
                await session.execute(assets_query.order_by(CompiledAssetVersionRecord.created_at.desc()))
            ).scalars().all()

            failure_counts: dict[str, int] = {}
            for event in failure_rows:
                failure_counts[event.code] = failure_counts.get(event.code, 0) + 1
            top_failure = max(failure_counts, key=lambda key: (failure_counts[key], key)) if failure_counts else None
            latest = selected[0] if selected else None
            latest_event = next((event for event in failure_rows if latest and event.evaluation_id == latest.id), None)
            status = "PLANNED"
            decision: dict[str, Any]
            scenario: ScenarioSpecRecord | None = None
            scenario_reused = False
            success_rate = successes / attempts if attempts else None
            if attempts >= request.max_evaluation_episodes:
                status = "STOPPED"
                decision = {"action": "STOP", "reason": "evaluation_budget_exhausted", "scenarioReused": False}
            elif attempts >= request.minimum_attempts and success_rate is not None and success_rate >= request.target_success_rate:
                status = "STOPPED"
                decision = {"action": "STOP", "reason": "target_success_rate_reached", "scenarioReused": False}
            elif latest_event and latest_event.code in {"worker_crash", "invalid_action", "policy_instability"}:
                status = "BLOCKED"
                decision = {
                    "action": "REPAIR_POLICY_RUNTIME",
                    "reason": latest_event.code,
                    "scenarioReused": False,
                    "recommendedAction": dict(latest_event.recommended_action or {}),
                }
            elif request.max_new_scenarios == 0:
                status = "STOPPED"
                decision = {"action": "STOP", "reason": "new_scenario_budget_exhausted", "scenarioReused": False}
            elif not valid_assets:
                status = "ACTION_REQUIRED"
                decision = {
                    "action": "REQUEST_EXACT_OBJECT_EVIDENCE",
                    "reason": "no_oracle_validated_asset_available",
                    "scenarioReused": False,
                    "requestedCoverageBins": {
                        name: detail["underrepresentedBins"]
                        for name, detail in coverage["dimensions"].items()
                    },
                }
            else:
                latest_asset_id = _asset_version_id(latest) if latest else None
                candidate = next((asset for asset in valid_assets if asset.id == latest_asset_id), None)
                tried_assets = {row.asset_version_id for row in (await session.execute(
                    select(CoverageObservationRecord).where(
                        CoverageObservationRecord.robot_id == request.robot_id,
                        CoverageObservationRecord.model_id == request.model_id,
                    )
                )).scalars().all()}
                if candidate is None:
                    candidate = next((asset for asset in valid_assets if asset.id not in tried_assets), valid_assets[0])
                untried_by_policy = candidate.id not in tried_assets
                candidate_bins = _asset_configured_bins(candidate)
                target_coverage_bins = {
                    name: [bucket]
                    if bucket in coverage["dimensions"][name]["underrepresentedBins"]
                    else []
                    for name, bucket in candidate_bins.items()
                }
                if latest_event:
                    requested_variations = list(
                        (latest_event.recommended_action or {}).get("varyDimensions") or []
                    )
                    variation_dimensions = [
                        value for value in requested_variations if value in MATERIALIZABLE_PLACEMENT_VARIATIONS
                    ] or requested_variations
                    deferred_variations = [
                        value for value in requested_variations if value not in MATERIALIZABLE_PLACEMENT_VARIATIONS
                    ]
                elif untried_by_policy:
                    variation_dimensions = ["baseline_policy_evaluation"]
                    deferred_variations = []
                else:
                    variation_dimensions = ["object_pose"]
                    deferred_variations = []
                specification = {
                    "schemaVersion": "robotworld.scenario-spec.v1",
                    "taskFamily": request.task_family,
                    "robotId": request.robot_id,
                    "modelId": request.model_id,
                    "assetVersionId": candidate.id,
                    "assetManifestSha256": candidate.manifest_sha256,
                    "sourceEvaluationId": latest.id if latest else None,
                    "targetFailureCode": latest_event.code if latest_event else top_failure,
                    "variationDimensions": variation_dimensions,
                    "deferredVariationDimensions": deferred_variations,
                    "targetCoverageBins": target_coverage_bins,
                    "immutableAssetBins": candidate_bins,
                    "placementConstraints": {
                        "semanticSupportSurface": "workspace_surface",
                        "requireReachability": True,
                        "rejectPenetration": True,
                        "dropAndSettle": True,
                        "seed": request.seed,
                    },
                    "assetReuseRequired": True,
                    "oracleBeforeVla": True,
                }
                validation_errors = _scenario_validation_errors(specification)
                if validation_errors:
                    raise CurriculumError("Scenario specification failed validation: " + "; ".join(validation_errors))
                fingerprint = _canonical_sha256(specification)
                scenario = await session.scalar(
                    select(ScenarioSpecRecord).where(ScenarioSpecRecord.scenario_fingerprint == fingerprint)
                )
                if scenario is None:
                    scenario = ScenarioSpecRecord(
                        id=new_id("scenario"),
                        lifecycle_state="PLANNED",
                        task_family=request.task_family,
                        robot_id=request.robot_id,
                        model_id=request.model_id,
                        asset_version_id=candidate.id,
                        source_evaluation_id=latest.id if latest else None,
                        scenario_fingerprint=fingerprint,
                        specification=specification,
                        oracle_required=True,
                        created_by=actor,
                    )
                    session.add(scenario)
                else:
                    if scenario.lifecycle_state == "REJECTED":
                        raise CurriculumError("The deterministic scenario fingerprint resolves to a rejected specification.")
                    scenario_reused = True
                decision = {
                    "action": "REUSE_EXISTING_VALID_ASSET",
                    "reason": latest_event.code if latest_event else "underrepresented_coverage_bins",
                    "assetVersionId": candidate.id,
                    "scenarioFingerprint": fingerprint,
                    "scenarioReused": scenario_reused,
                    "expectedInformation": {
                        "repeatedFailureCount": failure_counts.get(latest_event.code, 0) if latest_event else 0,
                        "untriedBySelectedPolicy": untried_by_policy,
                        "underrepresentedBinsTargeted": sum(len(values) for values in target_coverage_bins.values()),
                    },
                    "nextGate": "DETERMINISTIC_ORACLE",
                }
            analysis = {
                "sampleCount": attempts,
                "successCount": successes,
                "successRate": success_rate,
                "wilson95": list(interval) if interval else None,
                "targetSuccessRate": request.target_success_rate,
                "minimumAttempts": request.minimum_attempts,
                "evaluationBudget": request.max_evaluation_episodes,
                "failureCounts": failure_counts,
                "topFailureCode": top_failure,
                "coverageSampleCount": coverage["sampleCount"],
                "uniqueScenarioCount": coverage["uniqueScenarioCount"],
                "coverageDimensions": coverage["dimensions"],
                "validReusableAssetIds": [asset.id for asset in valid_assets],
                "rejectedInvalidScenarioCount": rejected_invalid_scenarios,
            }
            plan = CurriculumPlanRecord(
                id=new_id("curriculum"),
                status=status,
                robot_id=request.robot_id,
                model_id=request.model_id,
                source_evaluation_id=latest.id if latest else None,
                scenario_spec_id=scenario.id if scenario else None,
                request=payload,
                analysis=analysis,
                decision=decision,
                command_id=command.id,
                created_by=actor,
            )
            session.add(plan)
            session.add(
                AuditEvent(
                    command_id=command.id,
                    entity_type="curriculum_plan",
                    entity_id=plan.id,
                    action="curriculum.plan_next",
                    from_state=None,
                    to_state=status,
                    detail={"decision": decision["action"], "scenarioSpecId": scenario.id if scenario else None},
                    actor=actor,
                )
            )
            await session.commit()
            output = {"plan": _plan_view(plan), "scenario": _scenario_view(scenario), "coverage": coverage}
    except Exception as exc:
        await command_store.finish_command(command.id, error=str(exc))
        raise
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_store.command_view(command)


async def execute_scenario_oracle(
    scenario_id: str,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    try:
        command, reused = await command_store.start_command(
            kind="scenario.oracle_validate",
            target_type="scenario_spec",
            target_id=scenario_id,
            payload={"scenarioId": scenario_id, "stage": "DETERMINISTIC_ORACLE"},
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except command_store.CommandConflict as exc:
        raise CurriculumError(str(exc)) from exc
    if reused:
        return command_store.command_view(command, reused=True)

    execution_id = new_id("scenarioexec")
    try:
        async with SessionLocal() as session:
            scenario = await session.get(ScenarioSpecRecord, scenario_id)
            if scenario is None:
                raise KeyError(scenario_id)
            if scenario.lifecycle_state != "PLANNED":
                raise CurriculumError(
                    f"Scenario must be PLANNED before oracle execution; current state is {scenario.lifecycle_state}."
                )
            specification = dict(scenario.specification or {})
            errors = _scenario_validation_errors(specification)
            if errors:
                scenario.lifecycle_state = "REJECTED"
                session.add(
                    AuditEvent(
                        command_id=command.id,
                        entity_type="scenario_spec",
                        entity_id=scenario.id,
                        action="scenario.reject_invalid",
                        from_state="PLANNED",
                        to_state="REJECTED",
                        detail={"validationErrors": errors},
                        actor=actor,
                    )
                )
                await session.commit()
                raise CurriculumError("Scenario specification failed validation: " + "; ".join(errors))
            variations, seed, placement_request = placement_request_for_scenario(
                specification,
                scenario.scenario_fingerprint,
            )
            robot = await session.get(RobotRegistrationRecord, scenario.robot_id)
            asset = await session.get(CompiledAssetVersionRecord, scenario.asset_version_id)
            if robot is None or asset is None:
                raise CurriculumError("Scenario references a missing robot or asset version.")
            if robot.lifecycle_state != "AVAILABLE" or not robot.active:
                raise CurriculumError("Scenario oracle execution requires its robot to be active and AVAILABLE.")
            if asset.lifecycle_state != "ORACLE_VALIDATED":
                raise CurriculumError("Scenario asset must remain ORACLE_VALIDATED before execution.")
            execution = ScenarioExecutionRecord(
                id=execution_id,
                scenario_id=scenario.id,
                stage="DETERMINISTIC_ORACLE",
                status="STARTING",
                command_id=command.id,
                started_at=_now(),
                created_by=actor,
            )
            session.add(execution)
            session.add(
                AuditEvent(
                    command_id=command.id,
                    entity_type="scenario_execution",
                    entity_id=execution.id,
                    action="scenario_execution.transition",
                    from_state=None,
                    to_state="STARTING",
                    detail={"scenarioId": scenario.id, "stage": execution.stage},
                    actor=actor,
                )
            )
            scenario.lifecycle_state = "ORACLE_VALIDATING"
            scenario.updated_at = _now()
            session.add(
                AuditEvent(
                    command_id=command.id,
                    entity_type="scenario_spec",
                    entity_id=scenario.id,
                    action="scenario.oracle.start",
                    from_state="PLANNED",
                    to_state="ORACLE_VALIDATING",
                    detail={
                        "executionId": execution.id,
                        "seed": seed,
                        "variationDimensions": variations,
                        "placementFingerprint": (
                            _canonical_sha256(placement_request.model_dump(mode="json", by_alias=True))
                            if placement_request is not None
                            else None
                        ),
                    },
                    actor=actor,
                )
            )
            await session.commit()
            robot_id = scenario.robot_id
            asset_version_id = str(scenario.asset_version_id)
            scenario_fingerprint = scenario.scenario_fingerprint

        async with SessionLocal() as session:
            execution = await session.get(ScenarioExecutionRecord, execution_id)
            assert execution is not None
            execution.status = "RUNNING"
            execution.updated_at = _now()
            session.add(
                AuditEvent(
                    command_id=command.id,
                    entity_type="scenario_execution",
                    entity_id=execution.id,
                    action="scenario_execution.transition",
                    from_state="STARTING",
                    to_state="RUNNING",
                    detail={"scenarioId": scenario_id},
                    actor=actor,
                )
            )
            await session.commit()

        with span(
            "curriculum.oracle_validate",
            scenario_id=scenario_id,
            robot_id=robot_id,
            asset_version_id=asset_version_id,
        ):
            evaluation_command = await evaluation_catalog.run_compiled_asset_pick_place_oracle(
                CompiledAssetOracleRequest(
                    robotId=robot_id,
                    assetVersionId=asset_version_id,
                    seed=seed,
                    placementRequest=placement_request,
                ),
                idempotency_key=f"scenario-oracle:{scenario_fingerprint}",
                actor=actor,
            )
        nested = dict(evaluation_command.get("result") or {})
        evaluation = dict(nested.get("evaluation") or {})
        evaluation_id = str(evaluation.get("id") or "")
        if not evaluation_id:
            raise CurriculumError("Oracle command returned no durable evaluation ID.")
        succeeded = bool(evaluation.get("success")) and evaluation.get("status") == "SUCCEEDED"
        analysis = await _persist_analysis(evaluation_id, actor=actor, command_id=command.id)
        async with SessionLocal() as session:
            execution = await session.get(ScenarioExecutionRecord, execution_id)
            scenario = await session.get(ScenarioSpecRecord, scenario_id)
            assert execution is not None and scenario is not None
            execution.status = "SUCCEEDED" if succeeded else "FAILED"
            execution.evaluation_id = evaluation_id
            execution.error = None if succeeded else str(evaluation.get("failureDetail") or evaluation.get("failureCode") or "oracle failed")
            execution.finished_at = _now()
            execution.updated_at = _now()
            scenario.lifecycle_state = "ORACLE_VALIDATED" if succeeded else "REJECTED"
            scenario.updated_at = _now()
            session.add(
                AuditEvent(
                    command_id=command.id,
                    entity_type="scenario_execution",
                    entity_id=execution.id,
                    action="scenario_execution.transition",
                    from_state="RUNNING",
                    to_state=execution.status,
                    detail={"evaluationId": evaluation_id, "success": succeeded},
                    actor=actor,
                )
            )
            session.add(
                AuditEvent(
                    command_id=command.id,
                    entity_type="scenario_spec",
                    entity_id=scenario.id,
                    action="scenario.oracle.finish",
                    from_state="ORACLE_VALIDATING",
                    to_state=scenario.lifecycle_state,
                    detail={
                        "executionId": execution.id,
                        "evaluationId": evaluation_id,
                        "success": succeeded,
                        "failureCode": evaluation.get("failureCode"),
                    },
                    actor=actor,
                )
            )
            await session.commit()
            output = {
                "scenario": _scenario_view(scenario),
                "execution": _scenario_execution_view(execution),
                "evaluation": evaluation,
                "worldTemplate": nested.get("worldTemplate"),
                "analysis": analysis,
            }
    except Exception as exc:
        async with SessionLocal() as session:
            execution = await session.get(ScenarioExecutionRecord, execution_id)
            scenario = await session.get(ScenarioSpecRecord, scenario_id)
            if execution is not None and execution.status in {"STARTING", "RUNNING"}:
                execution_source = execution.status
                execution.status = "CRASHED"
                execution.error = str(exc)
                execution.finished_at = _now()
                execution.updated_at = _now()
                session.add(
                    AuditEvent(
                        command_id=command.id,
                        entity_type="scenario_execution",
                        entity_id=execution.id,
                        action="scenario_execution.transition",
                        from_state=execution_source,
                        to_state="CRASHED",
                        detail={"error": str(exc)[:500]},
                        actor=actor,
                    )
                )
            if scenario is not None and scenario.lifecycle_state == "ORACLE_VALIDATING":
                scenario.lifecycle_state = "PLANNED"
                scenario.updated_at = _now()
                session.add(
                    AuditEvent(
                        command_id=command.id,
                        entity_type="scenario_spec",
                        entity_id=scenario.id,
                        action="scenario.oracle.crash",
                        from_state="ORACLE_VALIDATING",
                        to_state="PLANNED",
                        detail={"executionId": execution_id, "error": str(exc)[:500]},
                        actor=actor,
                    )
                )
            await session.commit()
        await command_store.finish_command(command.id, error=str(exc))
        raise
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_store.command_view(command)


async def list_scenario_executions(limit: int = 100) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ScenarioExecutionRecord)
                .order_by(ScenarioExecutionRecord.created_at.desc())
                .limit(max(1, min(limit, 500)))
            )
        ).scalars().all()
    return [_scenario_execution_view(row) for row in rows]


async def reconcile_incomplete_executions(*, actor: str = "system") -> int:
    """Make interrupted synchronous oracle jobs safely retryable on restart.

    MuJoCo execution happens in a worker thread owned by the API process. A
    process loss cannot safely infer completion, so stale STARTING/RUNNING
    wrappers are marked CRASHED and their immutable scenario returns to
    PLANNED. A new idempotency key can then retry without silently claiming the
    interrupted episode succeeded.
    """

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ScenarioExecutionRecord).where(
                    ScenarioExecutionRecord.status.in_(("STARTING", "RUNNING"))
                )
            )
        ).scalars().all()
        for execution in rows:
            source = execution.status
            message = "Interrupted by backend restart before a terminal oracle result was persisted."
            execution.status = "CRASHED"
            execution.error = message
            execution.finished_at = _now()
            execution.updated_at = _now()
            scenario = await session.get(ScenarioSpecRecord, execution.scenario_id)
            if scenario is not None and scenario.lifecycle_state == "ORACLE_VALIDATING":
                scenario.lifecycle_state = "PLANNED"
                scenario.updated_at = _now()
                session.add(
                    AuditEvent(
                        command_id=execution.command_id,
                        entity_type="scenario_spec",
                        entity_id=scenario.id,
                        action="scenario.oracle.reconcile_restart",
                        from_state="ORACLE_VALIDATING",
                        to_state="PLANNED",
                        detail={"executionId": execution.id, "error": message},
                        actor=actor,
                    )
                )
            command = await session.get(CommandExecution, execution.command_id)
            if command is not None and command.status == "RUNNING":
                command.status = "FAILED"
                command.error = message
                command.updated_at = _now()
            session.add(
                AuditEvent(
                    command_id=execution.command_id,
                    entity_type="scenario_execution",
                    entity_id=execution.id,
                    action="scenario_execution.transition",
                    from_state=source,
                    to_state="CRASHED",
                    detail={"error": message, "restartReconciled": True},
                    actor=actor,
                )
            )
        await session.commit()
    return len(rows)


async def list_plans(limit: int = 100) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(CurriculumPlanRecord).order_by(CurriculumPlanRecord.created_at.desc()).limit(max(1, min(limit, 500)))
            )
        ).scalars().all()
    return [_plan_view(row) for row in rows]


async def list_scenarios(limit: int = 100) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ScenarioSpecRecord).order_by(ScenarioSpecRecord.created_at.desc()).limit(max(1, min(limit, 500)))
            )
        ).scalars().all()
    return [_scenario_view(row) for row in rows if row is not None]
