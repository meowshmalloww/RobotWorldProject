"""Explicit VLA-JEPA-to-Franka observation/action contract.

This adapter never treats the unmodified DROID checkpoint as Franka-ready.
Its encode/decode functions are usable only for a policy revision trained with
the named RobotWorld adapter and matching normalization statistics.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np

from ..contracts import VlaNormalizedAction
from ..db import SessionLocal
from ..models import ModelRegistrationRecord, RobotRegistrationRecord
from .franka_pick_place import PHYSICS_HZ


ADAPTER_REVISION = "franka-cartesian-delta-v1"
ACTION_NAMES = ("dx", "dy", "dz", "droll", "dpitch", "dyaw", "gripper_command")
STATE_NAMES = ("ee_x", "ee_y", "ee_z", "ee_qw", "ee_qx", "ee_qy", "ee_qz", "gripper_width")
TRANSLATION_LIMIT_M = 0.05
ROTATION_LIMIT_RAD = 0.20


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
    if adapter_revision != ADAPTER_REVISION:
        blockers.append(
            "Checkpoint metadata does not prove it was trained with franka-cartesian-delta-v1."
        )
    action_representation = capabilities.get("actionRepresentation")
    if action_representation != "end_effector_local_delta":
        blockers.append(
            "Checkpoint metadata does not declare the end_effector_local_delta action representation required by the Franka adapter."
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
    robot_revision_valid = trained_robot_sha256 == robot_definition_sha256
    if not robot_revision_valid:
        blockers.append("Checkpoint metadata is not bound to this exact robot-definition SHA-256.")
    state_contract_compatible = not state_feature_present or state_feature_dimension == 8
    return {
        "schemaVersion": "robotworld.vla-bridge-status.v1",
        "modelId": model_id,
        "robotId": robot_id,
        "adapterRevision": ADAPTER_REVISION,
        "robotDefinitionSha256": robot_definition_sha256,
        "trainedRobotDefinitionSha256": trained_robot_sha256,
        "robotRevisionValidated": robot_revision_valid,
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
            "frame": "end_effector_local_delta",
            "checkpointRepresentation": action_representation,
            "translationLimitM": TRANSLATION_LIMIT_M,
            "rotationLimitRad": ROTATION_LIMIT_RAD,
            "gripperConvention": "continuous_0_closed_1_open",
            "checkpointPreSnapGripper": capabilities.get("preSnapGripper"),
            "checkpointBinarizeGripper": capabilities.get("binarizeGripper"),
            "postBridgeBinarization": False,
            "policyControlHz": policy_control_hz,
            "physicsSubstepsPerAction": PHYSICS_HZ // policy_control_hz if control_rate_valid else None,
        },
    }
