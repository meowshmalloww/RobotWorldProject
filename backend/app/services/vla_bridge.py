"""Explicit VLA-JEPA-to-Franka observation/action contract.

This adapter never treats the unmodified DROID checkpoint as Franka-ready.
Its encode/decode functions are usable only for a policy revision trained with
the named RobotWorld adapter and matching normalization statistics.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import numpy as np

from ..contracts import VlaNormalizedAction
from ..db import SessionLocal
from ..models import AuditEvent, ModelRegistrationRecord, RobotRegistrationRecord
from . import command_store
from .franka_pick_place import PHYSICS_HZ


ADAPTER_REVISION = "franka-cartesian-delta-v1"
DROID_ADAPTER_REVISION = "droid-franka-cartesian-velocity-v1"
ACTION_NAMES = ("dx", "dy", "dz", "droll", "dpitch", "dyaw", "gripper_command")
STATE_NAMES = ("ee_x", "ee_y", "ee_z", "ee_qw", "ee_qx", "ee_qy", "ee_qz", "gripper_width")
TRANSLATION_LIMIT_M = 0.05
ROTATION_LIMIT_RAD = 0.20
DROID_TRANSLATION_DELTA_M = 0.075
DROID_ROTATION_DELTA_RAD = 0.15


def supported_action_contract(capabilities: dict[str, Any]) -> bool:
    """Return true only for one of the two executable Franka bridge contracts."""

    adapter_revision = capabilities.get("embodimentAdapterRevision")
    action_representation = capabilities.get("actionRepresentation")
    return capabilities.get("actionDimension") == 7 and (
        (adapter_revision == ADAPTER_REVISION and action_representation == "end_effector_local_delta")
        or (
            adapter_revision == DROID_ADAPTER_REVISION
            and action_representation == "droid_base_cartesian_velocity"
        )
    )


async def attach_zero_shot_bridge(
    model_id: str,
    robot_id: str,
    *,
    camera_mapping: dict[str, str],
    policy_control_hz: int,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    """Persist an explicit zero-shot mapping without claiming training compatibility."""

    payload = {
        "modelId": model_id,
        "robotId": robot_id,
        "cameraMapping": dict(camera_mapping),
        "policyControlHz": policy_control_hz,
        "validationLevel": "zero_shot_user_authorized",
    }
    command, reused = await command_store.start_command(
        kind="model.franka_zero_shot_bridge.attach",
        target_type="model",
        target_id=model_id,
        payload=payload,
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        return command_store.command_view(command, reused=True)
    async with SessionLocal() as session:
        model = await session.get(ModelRegistrationRecord, model_id)
        robot = await session.get(RobotRegistrationRecord, robot_id)
        if model is None or robot is None:
            message = "Model or robot registration was not found."
            await command_store.finish_command(command.id, error=message)
            raise KeyError(message)
        capabilities = dict(model.capabilities or {})
        camera_keys = set(capabilities.get("cameraKeys") or [])
        if "vla_policy" not in (model.roles or []) or capabilities.get("actionDimension") != 7:
            message = "Zero-shot Franka bridge requires a validated seven-dimensional VLA policy."
            await command_store.finish_command(command.id, error=message)
            raise ValueError(message)
        if set(camera_mapping) != camera_keys or set(camera_mapping.values()) != {"front", "wrist"}:
            message = "Camera mapping must bind the policy's exact two keys one-to-one to front and wrist."
            await command_store.finish_command(command.id, error=message)
            raise ValueError(message)
        if PHYSICS_HZ % policy_control_hz:
            message = f"policyControlHz must evenly divide the {PHYSICS_HZ} Hz physics rate."
            await command_store.finish_command(command.id, error=message)
            raise ValueError(message)
        definition = dict(robot.definition or {})
        sensors = {sensor.get("id") for sensor in definition.get("sensors", [])}
        if not {"front", "wrist"}.issubset(sensors):
            message = "Robot definition does not expose front and wrist cameras."
            await command_store.finish_command(command.id, error=message)
            raise ValueError(message)
        robot_sha256 = hashlib.sha256(
            json.dumps(definition, sort_keys=True, separators=(",", ":")).encode("utf8")
        ).hexdigest()
        capabilities.update(
            embodimentAdapterRevision=DROID_ADAPTER_REVISION,
            actionRepresentation="droid_base_cartesian_velocity",
            cameraMapping=dict(camera_mapping),
            policyControlHz=policy_control_hz,
            boundRobotDefinitionSha256=robot_sha256,
            bridgeValidationLevel="zero_shot_user_authorized",
            zeroShotAcknowledged=True,
        )
        model.capabilities = capabilities
        model.revision += 1
        model.updated_at = datetime.now(timezone.utc)
        session.add(
            AuditEvent(
                command_id=command.id,
                entity_type="model",
                entity_id=model_id,
                action="model.franka_zero_shot_bridge.attach",
                detail={**payload, "boundRobotDefinitionSha256": robot_sha256, "calibrationValidated": False},
                actor=actor,
            )
        )
        await session.commit()
    status = await bridge_status(model_id, robot_id)
    output = {"bridge": status}
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_store.command_view(command)


def decode_checkpoint_action(
    values: list[float] | tuple[float, ...],
    *,
    adapter_revision: str = DROID_ADAPTER_REVISION,
) -> dict[str, Any]:
    """Decode an action after the checkpoint's serialized postprocessor.

    The unmodified DROID checkpoint produces normalized base-frame Cartesian
    velocity commands. RobotWorld-trained candidates instead unnormalize to
    physical end-effector-local deltas. Keeping the paths explicit prevents a
    second, erroneous scale operation on a trained candidate.
    """

    checkpoint = np.asarray(values, dtype=np.float64)
    if checkpoint.shape != (7,) or not np.isfinite(checkpoint).all():
        raise ValueError("Checkpoint Franka action must contain seven finite values.")
    if checkpoint[6] not in {-1.0, 1.0}:
        raise ValueError("Checkpoint postprocessed gripper command must be binary -1/+1.")
    physical = np.empty(7, dtype=np.float64)
    if adapter_revision == DROID_ADAPTER_REVISION:
        if np.any(np.abs(checkpoint[:6]) > 1.00001):
            raise ValueError("DROID Cartesian velocity exceeds its bounded [-1, 1] contract.")
        physical[:3] = checkpoint[:3] * DROID_TRANSLATION_DELTA_M
        physical[3:6] = checkpoint[3:6] * DROID_ROTATION_DELTA_RAD
        frame = "robot_base_delta"
        representation = "droid_base_cartesian_velocity"
    elif adapter_revision == ADAPTER_REVISION:
        limits = np.asarray([TRANSLATION_LIMIT_M] * 3 + [ROTATION_LIMIT_RAD] * 3)
        if np.any(np.abs(checkpoint[:6]) > limits + 1e-9):
            raise ValueError("RobotWorld Cartesian delta exceeds its physical safety contract.")
        physical[:6] = checkpoint[:6]
        frame = "end_effector_local_delta"
        representation = "robotworld_physical_cartesian_delta"
    else:
        raise ValueError(f"Unsupported Franka action adapter revision: {adapter_revision}")
    physical[6] = (checkpoint[6] + 1.0) * 0.5
    return {
        "schemaVersion": "robotworld.vla-checkpoint-action.v1",
        "adapterRevision": adapter_revision,
        "frame": frame,
        "checkpointRepresentation": representation,
        "names": list(ACTION_NAMES),
        "checkpoint": [float(value) for value in checkpoint],
        "physical": [float(value) for value in physical],
        "gripperConvention": {
            "checkpointClosed": -1.0,
            "checkpointOpen": 1.0,
            "physicalClosed": 0.0,
            "physicalOpen": 1.0,
        },
        "additionalBinarizationApplied": False,
    }


def decode_action(action: VlaNormalizedAction) -> dict[str, Any]:
    if action.adapter_revision != ADAPTER_REVISION:
        raise ValueError(f"Action adapter revision must be {ADAPTER_REVISION}.")
    normalized = np.asarray(action.values, dtype=np.float64)
    physical = np.empty(7, dtype=np.float64)
    physical[:3] = normalized[:3] * TRANSLATION_LIMIT_M
    physical[3:6] = normalized[3:6] * ROTATION_LIMIT_RAD
    physical[6] = (normalized[6] + 1.0) * 0.5
    return {
        "schemaVersion": "robotworld.vla-action.v1",
        "adapterRevision": ADAPTER_REVISION,
        "frame": "end_effector_local_delta",
        "names": list(ACTION_NAMES),
        "normalized": [float(value) for value in normalized],
        "physical": [float(value) for value in physical],
        "gripperConvention": {"normalizedClosed": -1.0, "normalizedOpen": 1.0, "physicalClosed": 0.0, "physicalOpen": 1.0},
        "bounds": {
            "translationM": [-TRANSLATION_LIMIT_M, TRANSLATION_LIMIT_M],
            "rotationRad": [-ROTATION_LIMIT_RAD, ROTATION_LIMIT_RAD],
            "gripper": [0.0, 1.0],
        },
        "additionalBinarizationApplied": False,
    }


def encode_action(physical: list[float] | tuple[float, ...]) -> list[float]:
    values = np.asarray(physical, dtype=np.float64)
    if values.shape != (7,) or not np.isfinite(values).all():
        raise ValueError("Physical Franka action must contain seven finite values.")
    bounds = np.array([TRANSLATION_LIMIT_M] * 3 + [ROTATION_LIMIT_RAD] * 3)
    if np.any(np.abs(values[:6]) > bounds + 1e-12) or values[6] < 0 or values[6] > 1:
        raise ValueError("Physical Franka action exceeds the adapter safety limits.")
    normalized = np.empty(7, dtype=np.float64)
    normalized[:6] = values[:6] / bounds
    normalized[6] = values[6] * 2.0 - 1.0
    return [float(value) for value in normalized]


async def bridge_status(model_id: str, robot_id: str) -> dict[str, Any]:
    async with SessionLocal() as session:
        model = await session.get(ModelRegistrationRecord, model_id)
        robot = await session.get(RobotRegistrationRecord, robot_id)
        if model is None:
            raise KeyError(f"model:{model_id}")
        if robot is None:
            raise KeyError(f"robot:{robot_id}")
        capabilities = dict(model.capabilities or {})
        definition = dict(robot.definition or {})
    blockers: list[str] = []
    if "vla_policy" not in (model.roles or []):
        blockers.append("Model registration does not have the vla_policy role.")
    if capabilities.get("actionDimension") != 7:
        blockers.append(f"Checkpoint action dimension is {capabilities.get('actionDimension')}, expected 7.")
    state_feature_present = bool(capabilities.get("stateFeaturePresent"))
    state_feature_dimension = capabilities.get("stateFeatureDimension")
    if state_feature_present and state_feature_dimension != 8:
        blockers.append(f"Checkpoint observation.state dimension is {state_feature_dimension}, expected 8.")
    cameras = list(capabilities.get("cameraKeys") or [])
    if len(cameras) != 2:
        blockers.append(f"Checkpoint exposes {len(cameras)} camera keys, expected 2.")
    sensors = {sensor.get("id") for sensor in definition.get("sensors", [])}
    if not {"front", "wrist"}.issubset(sensors):
        blockers.append("Robot definition does not expose both front and wrist RGB cameras.")
    if model.lifecycle_state != "LOADED":
        blockers.append(f"Policy model is {model.lifecycle_state}; an isolated policy worker has not loaded it.")
    adapter_revision = capabilities.get("embodimentAdapterRevision")
    action_representation = capabilities.get("actionRepresentation")
    if not supported_action_contract(capabilities):
        blockers.append(
            "Checkpoint metadata does not declare a supported, matching Franka action adapter and representation."
        )
    policy_control_hz = capabilities.get("policyControlHz")
    control_rate_valid = (
        isinstance(policy_control_hz, int)
        and not isinstance(policy_control_hz, bool)
        and 1 <= policy_control_hz <= 100
        and PHYSICS_HZ % policy_control_hz == 0
    )
    if not control_rate_valid:
        blockers.append(
            f"Checkpoint metadata must declare policyControlHz that evenly divides the {PHYSICS_HZ} Hz physics rate."
        )
    if not capabilities.get("normalizationRevision"):
        blockers.append("No Franka dataset normalization revision is recorded.")
    camera_mapping = capabilities.get("cameraMapping") if isinstance(capabilities.get("cameraMapping"), dict) else {}
    mapping_valid = (
        set(camera_mapping) == set(cameras)
        and set(camera_mapping.values()) == {"front", "wrist"}
        and len(camera_mapping) == 2
    )
    if not mapping_valid:
        blockers.append(
            "Checkpoint camera keys are not explicitly bound one-to-one to the robot's front and wrist sensors; exterior-view names are not silently remapped."
        )
    robot_definition_sha256 = hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":")).encode("utf8")
    ).hexdigest()
    trained_robot_sha256 = capabilities.get("trainedRobotDefinitionSha256")
    bound_robot_sha256 = capabilities.get("boundRobotDefinitionSha256")
    zero_shot = capabilities.get("bridgeValidationLevel") == "zero_shot_user_authorized" and capabilities.get(
        "zeroShotAcknowledged"
    ) is True
    calibrated_robot_revision = trained_robot_sha256 == robot_definition_sha256
    zero_shot_robot_revision = zero_shot and bound_robot_sha256 == robot_definition_sha256
    robot_revision_valid = calibrated_robot_revision or zero_shot_robot_revision
    if not robot_revision_valid:
        blockers.append("Checkpoint metadata is not bound to this exact robot-definition SHA-256.")
    state_contract_compatible = not state_feature_present or state_feature_dimension == 8
    return {
        "schemaVersion": "robotworld.vla-bridge-status.v1",
        "modelId": model_id,
        "robotId": robot_id,
        "adapterRevision": adapter_revision,
        "robotDefinitionSha256": robot_definition_sha256,
        "trainedRobotDefinitionSha256": trained_robot_sha256,
        "boundRobotDefinitionSha256": bound_robot_sha256,
        "robotRevisionValidated": robot_revision_valid,
        "calibrationValidated": calibrated_robot_revision,
        "zeroShot": zero_shot,
        "validationLevel": capabilities.get("bridgeValidationLevel")
        or ("trained_checkpoint_metadata" if calibrated_robot_revision else "unvalidated"),
        "warnings": (
            [
                "This bridge is executable for evaluation, but the checkpoint was not fine-tuned "
                "or calibrated for this exact simulated embodiment."
            ]
            if zero_shot
            else []
        ),
        "shapeCompatible": capabilities.get("actionDimension") == 7 and state_contract_compatible and len(cameras) == 2,
        "executable": not blockers,
        "blockers": blockers,
        "observationContract": {
            "cameraMapping": dict(camera_mapping),
            "cameraMappingValidated": mapping_valid,
            "cameraOrdering": ["front", "wrist"],
            "stateNames": list(STATE_NAMES),
            "stateRequired": state_feature_present,
            "stateDimension": 8 if state_feature_present else 0,
            "checkpointStateFeaturePresent": state_feature_present,
            "timestampsRequired": True,
        },
        "actionContract": {
            "names": list(ACTION_NAMES),
            "actionDimension": 7,
            "frame": (
                "robot_base_delta"
                if adapter_revision == DROID_ADAPTER_REVISION
                else "end_effector_local_delta"
            ),
            "checkpointRepresentation": action_representation,
            "translationLimitM": (
                DROID_TRANSLATION_DELTA_M
                if adapter_revision == DROID_ADAPTER_REVISION
                else TRANSLATION_LIMIT_M
            ),
            "rotationLimitRad": (
                DROID_ROTATION_DELTA_RAD
                if adapter_revision == DROID_ADAPTER_REVISION
                else ROTATION_LIMIT_RAD
            ),
            "gripperConvention": "checkpoint_-1_closed_+1_open_to_physical_0_closed_1_open",
            "checkpointPreSnapGripper": capabilities.get("preSnapGripper"),
            "checkpointBinarizeGripper": capabilities.get("binarizeGripper"),
            "postBridgeBinarization": False,
            "policyControlHz": policy_control_hz,
            "physicsSubstepsPerAction": PHYSICS_HZ // policy_control_hz if control_rate_valid else None,
        },
    }
