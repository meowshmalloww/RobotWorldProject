"""Authoritative compiled-asset VLA-JEPA evaluation for the Franka backend.

There is no random or scripted fallback in this module.  The caller must pass
the resident worker's inference function after ``vla_bridge`` has validated an
exact checkpoint/robot binding.  Tests may inject a bounded policy function,
but production wiring always uses the isolated VLA worker.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

import mujoco
import numpy as np
from PIL import Image

from ..contracts import PlacementRequest, VlaNormalizedAction
from ..config import WORLDS_DIR
from . import franka_pick_place, vla_bridge, vla_policy_worker


MAX_JOINT_DELTA_RAD = 0.12
CARTESIAN_BOUNDS_M = np.array([[0.10, 0.85], [-0.55, 0.55], [0.04, 0.90]], dtype=np.float64)
SETTLE_SECONDS = 6.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rotation_xyz(delta: np.ndarray) -> np.ndarray:
    roll, pitch, yaw = (float(value) for value in delta)
    cr, sr = np.cos(roll), np.sin(roll)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]], dtype=np.float64)
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]], dtype=np.float64)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]], dtype=np.float64)
    return rx @ ry @ rz


def _orientation_error(current: np.ndarray, target: np.ndarray) -> np.ndarray:
    return 0.5 * sum(
        (np.cross(current[:, index], target[:, index]) for index in range(3)),
        start=np.zeros(3),
    )


def _apply_cartesian_delta(
    backend: franka_pick_place.MujocoFrankaBackend,
    physical: list[float],
    *,
    physics_substeps: int,
    frame: str,
    workspace_bounds_m: np.ndarray = CARTESIAN_BOUNDS_M,
) -> dict[str, Any]:
    model, data = backend._require()
    values = np.asarray(physical, dtype=np.float64)
    if values.shape != (7,) or not np.isfinite(values).all():
        raise ValueError("Decoded Franka action must contain seven finite values.")
    current_position = data.site_xpos[backend.ee_site].copy()
    current_rotation = data.site_xmat[backend.ee_site].reshape(3, 3).copy()
    if frame == "robot_base_delta":
        target_position = current_position + values[:3]
        target_rotation = _rotation_xyz(values[3:6]) @ current_rotation
        translation_frame = "robot_base"
        rotation_convention = "extrinsic_xyz_radians"
    elif frame == "end_effector_local_delta":
        target_position = current_position + current_rotation @ values[:3]
        target_rotation = current_rotation @ _rotation_xyz(values[3:6])
        translation_frame = "end_effector_local"
        rotation_convention = "intrinsic_xyz_radians"
    else:
        raise ValueError(f"Unsupported Cartesian action frame: {frame}")
    if np.any(target_position < workspace_bounds_m[:, 0]) or np.any(target_position > workspace_bounds_m[:, 1]):
        raise ValueError(f"Cartesian target is outside the configured Franka workspace safety box: {target_position.tolist()}")
    position_error = target_position - current_position
    rotation_error = _orientation_error(current_rotation, target_rotation)
    jacp = np.zeros((3, model.nv))
    jacr = np.zeros((3, model.nv))
    mujoco.mj_jacSite(model, data, jacp, jacr, backend.ee_site)
    jacobian = np.vstack((jacp[:, backend.arm_dofs], jacr[:, backend.arm_dofs]))
    task_error = np.concatenate((position_error, rotation_error))
    lhs = jacobian @ jacobian.T + np.eye(6) * 0.004
    joint_delta = jacobian.T @ np.linalg.solve(lhs, task_error)
    norm = float(np.linalg.norm(joint_delta))
    if norm > MAX_JOINT_DELTA_RAD:
        joint_delta *= MAX_JOINT_DELTA_RAD / norm
    qpos = np.asarray([data.qpos[index] for index in backend.arm_qpos], dtype=np.float64)
    target_qpos = np.clip(
        qpos + joint_delta,
        model.jnt_range[backend.arm_joints, 0] + 0.01,
        model.jnt_range[backend.arm_joints, 1] - 0.01,
    )
    action = data.ctrl.copy()
    action[:7] = target_qpos
    gripper_low, gripper_high = model.actuator_ctrlrange[model.nu - 1]
    action[-1] = float(gripper_low + values[6] * (gripper_high - gripper_low))
    backend.apply_action(action)
    backend.step(physics_substeps)
    return {
        "targetPositionM": [float(value) for value in target_position],
        "translationFrame": translation_frame,
        "rotationConvention": rotation_convention,
        "targetJointPosition": [float(value) for value in target_qpos],
        "jointDeltaNormRad": float(np.linalg.norm(joint_delta)),
        "gripperOpenFraction": float(values[6]),
        "actuatorCommand": [float(value) for value in action],
    }


def _rotation_span(quaternions: list[np.ndarray]) -> float | None:
    if len(quaternions) < 2:
        return None
    reference = quaternions[0] / max(float(np.linalg.norm(quaternions[0])), 1e-12)
    angles = []
    for value in quaternions[1:]:
        normalized = value / max(float(np.linalg.norm(value)), 1e-12)
        angles.append(2.0 * np.arccos(np.clip(abs(float(np.dot(reference, normalized))), 0.0, 1.0)))
    return float(max(angles, default=0.0))


def run_compiled_asset_policy(
    *,
    robot_id: str,
    asset_version: dict[str, Any],
    model: dict[str, Any],
    bridge: dict[str, Any],
    run_id: str,
    seed: int,
    instruction: str,
    max_policy_steps: int,
    infer_action: Callable[..., dict[str, Any]],
    placement_request: PlacementRequest | dict[str, Any] | None = None,
    template_override: dict[str, Any] | None = None,
    artifact_dir_override: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not bridge.get("executable"):
        raise ValueError("VLA bridge is not executable: " + "; ".join(bridge.get("blockers") or []))
    template = template_override or franka_pick_place.compile_compiled_asset_world_template(
        robot_id, asset_version, placement_request=placement_request,
    )
    expected = (
        Path(template["runtimePath"]).resolve().parent.parent / "evaluations"
        if template_override is not None
        else WORLDS_DIR / franka_pick_place.TEMPLATE_ID / "evaluations"
    ).resolve()
    artifact_dir = (artifact_dir_override or (expected / run_id)).resolve()
    if expected not in artifact_dir.parents:
        raise ValueError("Invalid VLA evaluation artifact target.")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    observations_dir = artifact_dir / "observations"
    frames_dir = artifact_dir / "frames"
    observations_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    capabilities = dict(model.get("capabilities") or {})
    camera_keys = list(capabilities.get("cameraKeys") or [])
    camera_mapping = dict((bridge.get("observationContract") or {}).get("cameraMapping") or {})
    state_required = bool((bridge.get("observationContract") or {}).get("stateRequired"))
    normalization_revision = str(capabilities.get("normalizationRevision") or "")
    policy_control_hz = int((bridge.get("actionContract") or {}).get("policyControlHz") or 0)
    if policy_control_hz <= 0 or franka_pick_place.PHYSICS_HZ % policy_control_hz:
        raise ValueError("Validated policy control rate no longer divides the physics rate.")
    physics_substeps = franka_pick_place.PHYSICS_HZ // policy_control_hz
    workspace_bounds = np.asarray(template.get("workspaceSafetyBoundsM") or CARTESIAN_BOUNDS_M, dtype=np.float64)
    if workspace_bounds.shape != (3, 2) or not np.isfinite(workspace_bounds).all():
        raise ValueError("World template contains an invalid Cartesian workspace safety box.")
    image_size = capabilities.get("imageSize") or [224, 224]
    if not isinstance(image_size, list) or len(image_size) != 2:
        image_size = [224, 224]
    height, width = (int(image_size[0]), int(image_size[1]))
    if min(width, height) < 32 or max(width, height) > 1024:
        raise ValueError("Checkpoint image size is outside the bounded simulation-render range.")

    backend = franka_pick_place.MujocoFrankaBackend(Path(template["runtimePath"]))
    started = time.perf_counter()
    trajectory: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []
    frame_hashes: dict[str, dict[str, str]] = {}
    contact_pairs: dict[str, int] = {}
    failure: tuple[str, str] | None = None
    initial_grasp_height = 0.0
    max_grasp_height = 0.0
    finger_contact_observed = False

    def capture(step_name: str, *, persist: bool) -> dict[str, str]:
        worker_paths: dict[str, str] = {}
        sampled: dict[str, str] = {}
        for checkpoint_key in camera_keys:
            camera = camera_mapping[checkpoint_key]
            frame = backend.render_rgb(camera, width=width, height=height)
            live_path = observations_dir / f"current-{camera}.png"
            Image.fromarray(frame, mode="RGB").save(live_path, format="PNG", optimize=False)
            worker_paths[checkpoint_key] = str(live_path)
            if persist:
                sample_path = frames_dir / f"{step_name}-{camera}.png"
                Image.fromarray(frame, mode="RGB").save(sample_path, format="PNG", optimize=False)
                sampled[camera] = _sha256(sample_path)
        if sampled:
            frame_hashes[step_name] = sampled
        return worker_paths

    try:
        backend.reset(seed)
        backend.step(250)
        initial_grasp_height = float(backend.data.site_xpos[backend.asset_grasp_site, 2])
        max_grasp_height = initial_grasp_height
        capture("reset", persist=True)
        phases.append({"phase": "reset", "simulatedSeconds": 250 / franka_pick_place.PHYSICS_HZ})
        for step_index in range(max_policy_steps):
            image_paths = capture(f"step_{step_index:04d}", persist=step_index % 25 == 0)
            state = backend.state()
            state_vector = None
            if state_required:
                ee_quaternion = np.empty(4, dtype=np.float64)
                mujoco.mju_mat2Quat(ee_quaternion, backend.data.site_xmat[backend.ee_site])
                state_vector = [
                    *state["endEffectorPositionM"],
                    *[float(value) for value in ee_quaternion],
                    float(state["gripperWidthM"]),
                ]
            try:
                inference = infer_action(
                    images=image_paths,
                    state=state_vector,
                    instruction=instruction,
                    adapter_revision=vla_bridge.ADAPTER_REVISION,
                    normalization_revision=normalization_revision,
                )
                normalized = VlaNormalizedAction(
                    values=tuple(inference.get("normalizedAction") or ()),
                    adapterRevision=vla_bridge.ADAPTER_REVISION,
                )
                checkpoint_action = tuple(inference.get("checkpointAction") or ())
                decoded = vla_bridge.decode_checkpoint_action(
                    checkpoint_action,
                    adapter_revision=str(bridge.get("adapterRevision") or ""),
                )
                controller = _apply_cartesian_delta(
                    backend,
                    decoded["physical"],
                    physics_substeps=physics_substeps,
                    frame=str(decoded["frame"]),
                    workspace_bounds_m=workspace_bounds,
                )
            except vla_policy_worker.VlaWorkerError as exc:
                failure = ("worker_crash", str(exc))
                break
            except (ValueError, RuntimeError) as exc:
                failure = ("invalid_action", str(exc))
                break
            state = backend.state()
            contacts = backend.contacts()
            object_contacts = [
                contact
                for contact in contacts
                if "pick_object" in {contact.body_a, contact.body_b}
            ]
            for contact in object_contacts:
                pair = "|".join(sorted((contact.body_a, contact.body_b)))
                contact_pairs[pair] = contact_pairs.get(pair, 0) + 1
            finger_contact = any(
                {contact.body_a, contact.body_b} & {"left_finger", "right_finger"}
                for contact in object_contacts
            )
            finger_contact_observed = finger_contact_observed or finger_contact
            max_grasp_height = max(max_grasp_height, float(backend.data.site_xpos[backend.asset_grasp_site, 2]))
            trajectory.append(
                {
                    **state,
                    "step": step_index,
                    "instruction": instruction,
                    "normalizedAction": list(normalized.values),
                    "checkpointAction": list(checkpoint_action),
                    "physicalAction": list(decoded["physical"]),
                    "controller": controller,
                    "inferenceDurationSeconds": inference.get("inferenceDurationSeconds"),
                    "checkpointConfigSha256": inference.get("checkpointConfigSha256"),
                    "objectContacts": [
                        {
                            "bodyA": contact.body_a,
                            "bodyB": contact.body_b,
                            "distanceM": contact.distance_m,
                            "normalForceN": contact.normal_force_n,
                        }
                        for contact in object_contacts[:16]
                    ],
                }
            )
            if not state["finite"]:
                failure = ("policy_instability", "MuJoCo state became non-finite after a bounded policy action.")
                break
        phases.append(
            {
                "phase": "policy_rollout",
                "requestedSteps": max_policy_steps,
                "executedSteps": len(trajectory),
                "controlHz": policy_control_hz,
                "physicsSubstepsPerAction": physics_substeps,
                "terminalFailure": failure[0] if failure else None,
            }
        )

        settle_positions: list[np.ndarray] = []
        settle_quaternions: list[np.ndarray] = []
        settle_linear_speeds: list[float] = []
        settle_angular_speeds: list[float] = []
        settle_steps = int(SETTLE_SECONDS * franka_pick_place.PHYSICS_HZ)
        settle_window = int(0.75 * franka_pick_place.PHYSICS_HZ)
        for index in range(settle_steps):
            backend.step()
            if index >= settle_steps - settle_window:
                settle_positions.append(backend.data.xpos[backend.object_body].copy())
                settle_quaternions.append(backend.data.xquat[backend.object_body].copy())
                settle_linear_speeds.append(float(np.linalg.norm(backend.data.cvel[backend.object_body, 3:])))
                settle_angular_speeds.append(float(np.linalg.norm(backend.data.cvel[backend.object_body, :3])))
        capture("final", persist=True)
        final_state = backend.state()
        final_contacts = backend.contacts()
        target = template["targetVolumes"][0]
        support_body = str(target.get("supportBody", "workspace_calibration"))
        support_contact = any(
            "pick_object" in {contact.body_a, contact.body_b}
            and support_body in {contact.body_a, contact.body_b}
            for contact in final_contacts
        )
        finger_contact = any(
            "pick_object" in {contact.body_a, contact.body_b}
            and ({contact.body_a, contact.body_b} & {"left_finger", "right_finger"})
            for contact in final_contacts
        )
        target_center = np.asarray(target["centerM"], dtype=np.float64)
        final_grasp = backend.data.site_xpos[backend.asset_grasp_site].copy()
        target_error = float(np.linalg.norm(final_grasp[:2] - target_center[:2]))
        bounding_radius = float(template["graspContract"]["localBoundingRadiusM"])
        containment_residual = target_error + bounding_radius - float(target["radiusM"])
        settle_positions_array = np.asarray(settle_positions)
        settle_span = float(np.max(np.ptp(settle_positions_array, axis=0)))
        settle_linear_p95 = float(np.percentile(settle_linear_speeds, 95))
        settle_angular_p95 = float(np.percentile(settle_angular_speeds, 95))
        settle_rotation_span = _rotation_span(settle_quaternions)
        final_linear_speed = float(np.linalg.norm(backend.data.cvel[backend.object_body, 3:]))
        final_angular_speed = float(np.linalg.norm(backend.data.cvel[backend.object_body, :3]))
        angular_velocity_gate = settle_angular_p95 < 0.15 and final_angular_speed < 0.05
        rotation_transform_gate = settle_rotation_span is not None and settle_rotation_span < 0.01
        settled = bool(
            settle_span < 0.003
            and settle_linear_p95 < 0.02
            and final_linear_speed < 0.01
            and (angular_velocity_gate or rotation_transform_gate)
        )
        contained = containment_residual <= 0.001
        released = not finger_contact
        success = bool(failure is None and support_contact and contained and released and settled)
        if failure is None and not success:
            if not finger_contact_observed:
                failure = ("grasp_miss", "No policy step produced a gripper/object contact.")
            elif max_grasp_height < initial_grasp_height + 0.04:
                failure = ("grasp_slip", "The policy contacted the object but did not lift its grasp frame by 4 cm.")
            else:
                failure = (
                    "policy_timeout",
                    f"Policy exhausted {max_policy_steps} steps without satisfying containment/release/settle predicates.",
                )
        phases.append(
            {
                "phase": "settle",
                "steps": settle_steps,
                "simulatedSeconds": SETTLE_SECONDS,
                "positionSpanM": settle_span,
                "linearSpeedP95Mps": settle_linear_p95,
                "angularSpeedP95RadS": settle_angular_p95,
                "rotationSpanRad": settle_rotation_span,
                "angularVelocityGatePassed": angular_velocity_gate,
                "rotationTransformGatePassed": rotation_transform_gate,
            }
        )
        predicate = {
            "assetVersionId": asset_version["id"],
            "assetManifestSha256": asset_version["manifestSha256"],
            "modelRegistrationId": model["id"],
            "modelRevision": model.get("modelRevision"),
            "modelContentSha256": model.get("contentSha256"),
            "normalizationRevision": normalization_revision,
            "adapterRevision": bridge.get("adapterRevision"),
            "actionRepresentation": (bridge.get("actionContract") or {}).get("checkpointRepresentation"),
            "instruction": instruction,
            "contained": contained,
            "onSupportSurface": support_contact,
            "settled": settled,
            "released": released,
            "targetErrorM": target_error,
            "containmentResidualM": containment_residual,
            "finalObjectPositionM": final_state["objectPositionM"],
            "finalObjectGraspPositionM": [float(value) for value in final_grasp],
            "finalLinearSpeedMps": final_linear_speed,
            "finalAngularSpeedRadS": final_angular_speed,
            "settlePositionSpanM": settle_span,
            "settleRotationSpanRad": settle_rotation_span,
            "maxGraspLiftM": max_grasp_height - initial_grasp_height,
            "policySteps": len(trajectory),
        }
        policy_name = f"vla-jepa:{model['id']}:r{model['revision']}"
        output = {
            "schemaVersion": "robotworld.evaluation-result.v1",
            "runId": run_id,
            "robotId": robot_id,
            "worldTemplateId": template["id"],
            "worldTemplateRevision": int(template["revision"]),
            "worldRuntimeSha256": template["runtimeSha256"],
            "policy": policy_name,
            "seed": seed,
            "success": success,
            "failureCode": failure[0] if failure else None,
            "failureDetail": failure[1] if failure else None,
            "durationSeconds": time.perf_counter() - started,
            "physicsHz": franka_pick_place.PHYSICS_HZ,
            "controlHz": policy_control_hz,
            "actionContract": dict(bridge.get("actionContract") or {}),
            "phases": phases,
            "trajectory": trajectory,
            "contactSummary": {
                "sampledPairs": contact_pairs,
                "samples": sum(contact_pairs.values()),
                "fingerContactObserved": finger_contact_observed,
                "finalSupportContact": support_contact,
                "finalFingerContact": finger_contact,
            },
            "predicate": predicate,
            "frameHashes": frame_hashes,
        }
        (artifact_dir / "evaluation.json").write_text(json.dumps(output, indent=2), encoding="utf8")
        return output, template
    finally:
        backend.close()
