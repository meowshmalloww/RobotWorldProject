"""Pinned MuJoCo Menagerie Franka Panda importer and physics validator.

The source model is never edited. RobotWorld derives an immutable runtime
scene containing explicit front/wrist cameras plus a calibration workspace,
then loads and exercises that scene with the authoritative MuJoCo runtime.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mujoco
import numpy as np
from PIL import Image

from ..config import BASE_DIR, ROBOTS_DIR
from ..contracts import FrankaRegistrationRequest, RobotDefinition


MENAGERIE_REVISION = "feadf76d42f8a2162426f7d226a3b539556b3bf5"
MENAGERIE_SOURCE_URL = "https://github.com/google-deepmind/mujoco_menagerie/tree/feadf76d42f8a2162426f7d226a3b539556b3bf5/franka_emika_panda"
ROBOT_ID_PREFIX = "franka-panda-mujoco"
COMPILER_REVISION = "robotworld-franka-compiler-v2"
ARM_JOINTS = tuple(f"joint{index}" for index in range(1, 8))
FINGER_JOINTS = ("finger_joint1", "finger_joint2")
CAMERA_NAMES = ("front", "wrist")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision(path: Path) -> str | None:
    try:
        from git import Repo

        return Repo(str(path), search_parent_directories=True).head.commit.hexsha
    except Exception:
        return None


def _default_source() -> Path:
    return BASE_DIR / "data" / "robot_descriptions" / "mujoco_menagerie" / "franka_emika_panda" / "panda.xml"


def _allowed_robot_roots() -> list[Path]:
    roots = [BASE_DIR / "data" / "robot_descriptions", ROBOTS_DIR]
    raw = str(os.environ.get("ROBOT_ASSET_ROOT") or "").strip()
    if raw:
        roots.extend(Path(value.strip()) for value in raw.split(os.pathsep) if value.strip())
    return [path.expanduser().resolve(strict=False) for path in roots]


def _resolve_source(request: FrankaRegistrationRequest) -> Path:
    if request.source_path:
        try:
            source = Path(request.source_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Configured Franka MJCF does not exist: {request.source_path}") from exc
        if not any(source == root or root in source.parents for root in _allowed_robot_roots()):
            raise ValueError("Franka source is outside ROBOT_ASSET_ROOT and RobotWorld's managed robot roots.")
    else:
        source = _default_source().resolve(strict=False)
        if not source.is_file():
            if not request.allow_download:
                raise ValueError(
                    "Pinned Franka source is not cached. Retry with allowDownload=true to fetch the robot_descriptions-pinned Menagerie revision."
                )
            cache = (BASE_DIR / "data" / "robot_descriptions").resolve()
            os.environ["ROBOT_DESCRIPTIONS_CACHE"] = str(cache)
            try:
                from robot_descriptions import panda_mj_description

                source = Path(panda_mj_description.MJCF_PATH).resolve(strict=True)
            except Exception as exc:
                raise ValueError(f"Pinned Franka source fetch failed: {exc}") from exc
    if source.name.lower() != "panda.xml" or source.suffix.lower() != ".xml":
        raise ValueError("The default Franka adapter requires the Menagerie franka_emika_panda/panda.xml source.")
    if not (source.parent / "assets").is_dir() or not (source.parent / "LICENSE").is_file():
        raise ValueError("Franka source is incomplete: assets/ and LICENSE must accompany panda.xml.")
    return source


def _look_at_xyaxes(eye: np.ndarray, target: np.ndarray) -> str:
    z_axis = eye - target
    z_axis /= np.linalg.norm(z_axis)
    up = np.array([0.0, 0.0, 1.0])
    x_axis = np.cross(up, z_axis)
    x_axis /= np.linalg.norm(x_axis)
    y_axis = np.cross(z_axis, x_axis)
    values = np.concatenate([x_axis, y_axis])
    return " ".join(f"{value:.9g}" for value in values)


def _derive_scene(source: Path, request: FrankaRegistrationRequest, runtime_path: Path) -> None:
    tree = ET.parse(source)
    root = tree.getroot()
    compiler = root.find("compiler")
    if compiler is None:
        raise ValueError("Franka MJCF has no compiler element.")
    compiler.set("meshdir", source.parent.joinpath("assets").as_posix())

    worldbody = root.find("worldbody")
    if worldbody is None:
        raise ValueError("Franka MJCF has no worldbody.")
    hand = next((body for body in worldbody.iter("body") if body.get("name") == "hand"), None)
    if hand is None:
        raise ValueError("Franka MJCF does not contain the named hand link.")

    eye = np.array([1.15, -1.15, 0.95])
    target = np.array([0.35, 0.0, 0.42])
    ET.SubElement(
        worldbody,
        "camera",
        {
            "name": "front",
            "pos": " ".join(str(value) for value in eye),
            "xyaxes": _look_at_xyaxes(eye, target),
            "fovy": "48",
        },
    )
    ET.SubElement(
        hand,
        "camera",
        {
            "name": "wrist",
            "pos": " ".join(f"{value:.9g}" for value in request.wrist_camera_translation_m),
            "quat": " ".join(f"{value:.9g}" for value in request.wrist_camera_quaternion_wxyz),
            "fovy": "74",
        },
    )
    ET.SubElement(hand, "site", {"name": "franka_ee", "pos": "0 0 0.105", "size": "0.006", "rgba": "0.1 0.8 1 0.8"})

    ET.SubElement(worldbody, "geom", {"name": "calibration_floor", "type": "plane", "size": "2 2 0.05", "rgba": "0.16 0.18 0.22 1"})
    workspace = ET.SubElement(worldbody, "body", {"name": "workspace_calibration", "pos": "0.5 0 0.255"})
    ET.SubElement(workspace, "geom", {"name": "workspace_surface", "type": "box", "size": "0.30 0.34 0.025", "rgba": "0.32 0.35 0.40 1", "contype": "1", "conaffinity": "1"})
    target_body = ET.SubElement(worldbody, "body", {"name": "calibration_target", "pos": "0.48 0 0.33"})
    ET.SubElement(target_body, "geom", {"name": "calibration_cube", "type": "box", "size": "0.035 0.035 0.035", "rgba": "0.84 0.18 0.12 1", "contype": "1", "conaffinity": "1"})

    visual = root.find("visual")
    if visual is None:
        visual = ET.SubElement(root, "visual")
    ET.SubElement(visual, "headlight", {"diffuse": "0.75 0.75 0.75", "ambient": "0.3 0.3 0.3", "specular": "0.1 0.1 0.1"})
    ET.indent(tree, space="  ")
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(runtime_path, encoding="utf-8", xml_declaration=True)


def _name(model: mujoco.MjModel, kind: mujoco.mjtObj, index: int) -> str:
    return str(mujoco.mj_id2name(model, kind, index) or f"unnamed_{index}")


def _joint_definition(model: mujoco.MjModel, joint_id: int) -> dict[str, Any]:
    body_id = int(model.jnt_bodyid[joint_id])
    parent_id = int(model.body_parentid[body_id])
    joint_type = int(model.jnt_type[joint_id])
    types = {
        int(mujoco.mjtJoint.mjJNT_HINGE): "revolute",
        int(mujoco.mjtJoint.mjJNT_SLIDE): "prismatic",
        int(mujoco.mjtJoint.mjJNT_BALL): "ball",
        int(mujoco.mjtJoint.mjJNT_FREE): "floating",
    }
    limited = bool(model.jnt_limited[joint_id])
    dof = int(model.jnt_dofadr[joint_id])
    joint_name = _name(model, mujoco.mjtObj.mjOBJ_JOINT, joint_id)
    actuator_ids = [index for index in range(model.nu) if int(model.actuator_trnid[index, 0]) == joint_id]
    effort = max((float(max(abs(value) for value in model.actuator_forcerange[index])) for index in actuator_ids), default=None)
    return {
        "id": joint_name,
        "parentLink": _name(model, mujoco.mjtObj.mjOBJ_BODY, parent_id),
        "childLink": _name(model, mujoco.mjtObj.mjOBJ_BODY, body_id),
        "jointType": types.get(joint_type, f"mujoco_{joint_type}"),
        "axis": [float(value) for value in model.jnt_axis[joint_id]],
        "originXyzM": [float(value) for value in model.jnt_pos[joint_id]],
        "lower": float(model.jnt_range[joint_id, 0]) if limited else None,
        "upper": float(model.jnt_range[joint_id, 1]) if limited else None,
        "velocityLimit": None,
        "effortLimit": effort,
        "damping": float(model.dof_damping[dof]),
        "friction": float(model.dof_frictionloss[dof]),
        "actuated": bool(actuator_ids or joint_name in FINGER_JOINTS),
    }


def _link_definition(model: mujoco.MjModel, body_id: int) -> dict[str, Any]:
    parent_id = int(model.body_parentid[body_id])
    inertia = [float(value) for value in model.body_inertia[body_id]]
    return {
        "id": _name(model, mujoco.mjtObj.mjOBJ_BODY, body_id),
        "parentId": _name(model, mujoco.mjtObj.mjOBJ_BODY, parent_id) if parent_id else None,
        "visualArtifacts": [],
        "collisionArtifacts": [],
        "massKg": float(model.body_mass[body_id]) if model.body_mass[body_id] > 0 else None,
        "centerOfMassM": [float(value) for value in model.body_ipos[body_id]],
        "inertiaKgM2": [inertia[0], inertia[1], inertia[2], 0.0, 0.0, 0.0],
    }


def _camera_observation(renderer: mujoco.Renderer, data: mujoco.MjData, camera: str, model: mujoco.MjModel) -> tuple[np.ndarray, np.ndarray]:
    renderer.disable_segmentation_rendering()
    renderer.update_scene(data, camera=camera)
    rgb = renderer.render().copy()
    renderer.enable_segmentation_rendering()
    renderer.update_scene(data, camera=camera)
    segmentation = renderer.render().copy()
    renderer.disable_segmentation_rendering()
    return rgb, segmentation


def _pixels_for_bodies(segmentation: np.ndarray, model: mujoco.MjModel, body_names: set[str]) -> int:
    body_ids = {
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        for name in body_names
    }
    body_ids.discard(-1)
    geom_ids = np.where(segmentation[..., 1] == int(mujoco.mjtObj.mjOBJ_GEOM), segmentation[..., 0], -1)
    valid = geom_ids >= 0
    visible_bodies = np.full(geom_ids.shape, -1, dtype=np.int32)
    visible_bodies[valid] = model.geom_bodyid[geom_ids[valid]]
    return int(np.isin(visible_bodies, list(body_ids)).sum())


def _validate_runtime(runtime_path: Path, preview_dir: Path) -> tuple[mujoco.MjModel, dict[str, Any], dict[str, str]]:
    model = mujoco.MjModel.from_xml_path(str(runtime_path))
    expected_joints = set(ARM_JOINTS + FINGER_JOINTS)
    actual_joints = {_name(model, mujoco.mjtObj.mjOBJ_JOINT, index) for index in range(model.njnt)}
    actual_cameras = {_name(model, mujoco.mjtObj.mjOBJ_CAMERA, index) for index in range(model.ncam)}
    errors: list[str] = []
    if not expected_joints.issubset(actual_joints):
        errors.append(f"missing joints: {sorted(expected_joints - actual_joints)}")
    if model.nu != 8:
        errors.append(f"expected 8 actuators, found {model.nu}")
    if not set(CAMERA_NAMES).issubset(actual_cameras):
        errors.append(f"missing cameras: {sorted(set(CAMERA_NAMES) - actual_cameras)}")
    if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "franka_ee") < 0:
        errors.append("named end-effector frame franka_ee is missing")
    if model.nkey < 1:
        errors.append("home keyframe is missing")
    elif _name(model, mujoco.mjtObj.mjOBJ_KEY, 0) != "home":
        errors.append("keyframe 0 is not named home")

    data = mujoco.MjData(model)
    if model.nkey:
        mujoco.mj_resetDataKeyframe(model, data, 0)
    mujoco.mj_forward(model, data)
    home_qpos = data.qpos.copy()
    severe_initial_contacts = sum(float(data.contact[index].dist) < -0.005 for index in range(data.ncon))
    if severe_initial_contacts:
        errors.append(f"{severe_initial_contacts} contacts penetrate more than 5 mm at reset")
    for _ in range(250):
        mujoco.mj_step(model, data)
    finite = bool(np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all())
    max_home_drift = float(np.max(np.abs(data.qpos - home_qpos)))
    if not finite:
        errors.append("non-finite state occurred during the reset stability test")
    if max_home_drift > 0.02:
        errors.append(f"home reset drift exceeded 0.02 rad/m ({max_home_drift:.6f})")

    if model.nkey:
        mujoco.mj_resetDataKeyframe(model, data, 0)
    data.ctrl[-1] = 0.0
    for _ in range(300):
        mujoco.mj_step(model, data)
    closed_width = float(data.qpos[-2] + data.qpos[-1])
    data.ctrl[-1] = 255.0
    for _ in range(300):
        mujoco.mj_step(model, data)
    open_width = float(data.qpos[-2] + data.qpos[-1])
    if not (closed_width < 0.02 and open_width > 0.06):
        errors.append(f"gripper range test failed (closed={closed_width:.5f}, open={open_width:.5f})")

    if model.nkey:
        mujoco.mj_resetDataKeyframe(model, data, 0)
    mujoco.mj_forward(model, data)
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_hashes: dict[str, str] = {}
    camera_report: dict[str, Any] = {}
    renderer = mujoco.Renderer(model, height=256, width=256)
    try:
        for camera in CAMERA_NAMES:
            rgb, segmentation = _camera_observation(renderer, data, camera, model)
            preview_path = preview_dir / f"{camera}.png"
            Image.fromarray(rgb, mode="RGB").save(preview_path, format="PNG", optimize=False)
            preview_hashes[camera] = _sha256(preview_path)
            report = {
                "shape": list(rgb.shape),
                "rgbVariance": float(np.var(rgb)),
                "nonzeroPixels": int(np.count_nonzero(rgb)),
                "robotPixels": _pixels_for_bodies(segmentation, model, set(ARM_JOINTS) | {"link0", "link1", "link2", "link3", "link4", "link5", "link6", "link7", "hand", "left_finger", "right_finger"}),
                "gripperPixels": _pixels_for_bodies(segmentation, model, {"hand", "left_finger", "right_finger"}),
                "workspacePixels": _pixels_for_bodies(segmentation, model, {"workspace_calibration", "calibration_target"}),
                "sha256": preview_hashes[camera],
            }
            camera_report[camera] = report
            if report["rgbVariance"] < 20 or report["nonzeroPixels"] < 1000:
                errors.append(f"{camera} camera produced an empty/flat calibration image")
        if camera_report["front"]["robotPixels"] < 100:
            errors.append("front camera does not show the Franka")
        if camera_report["front"]["workspacePixels"] < 100:
            errors.append("front camera does not show the calibration workspace")
        if camera_report["wrist"]["gripperPixels"] < 20:
            errors.append("wrist camera does not show the gripper")
        if camera_report["wrist"]["workspacePixels"] < 20:
            errors.append("wrist camera does not show the calibration workspace")
    finally:
        renderer.close()

    actuator_ranges = {
        _name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, index): [float(value) for value in model.actuator_ctrlrange[index]]
        for index in range(model.nu)
    }
    return model, {
        "passed": not errors,
        "errors": errors,
        "mujocoVersion": mujoco.__version__,
        "timestepSeconds": float(model.opt.timestep),
        "armJointCount": sum(name in actual_joints for name in ARM_JOINTS),
        "gripperJointCount": sum(name in actual_joints for name in FINGER_JOINTS),
        "actuatorCount": model.nu,
        "actuatorControlRanges": actuator_ranges,
        "severeInitialContacts": severe_initial_contacts,
        "maxHomeDrift": max_home_drift,
        "closedWidthM": closed_width,
        "openWidthM": open_width,
        "cameraCalibration": camera_report,
        "sampleCount": 850,
        "seed": 0,
    }, preview_hashes


def build_and_validate(request: FrankaRegistrationRequest) -> dict[str, Any]:
    source = _resolve_source(request)
    revision = _git_revision(source.parent)
    if revision and revision != MENAGERIE_REVISION:
        raise ValueError(f"Menagerie revision mismatch: expected {MENAGERIE_REVISION}, found {revision}.")
    source_sha256 = _sha256(source)
    camera_payload = {
        "translationM": list(request.wrist_camera_translation_m),
        "quaternionWxyz": list(request.wrist_camera_quaternion_wxyz),
    }
    identity = hashlib.sha256(
        json.dumps({"source": source_sha256, "revision": revision, "compiler": COMPILER_REVISION, "wristCamera": camera_payload}, sort_keys=True).encode("utf8")
    ).hexdigest()
    robot_id = f"{ROBOT_ID_PREFIX}-{identity[:12]}"
    root = (ROBOTS_DIR / robot_id).resolve()
    if root.parent != ROBOTS_DIR.resolve():
        raise ValueError("Invalid Franka artifact target.")
    runtime_path = root / "runtime" / "franka.xml"
    _derive_scene(source, request, runtime_path)
    model, validation, preview_hashes = _validate_runtime(runtime_path, root / "previews")

    links = [_link_definition(model, index) for index in range(1, model.nbody) if _name(model, mujoco.mjtObj.mjOBJ_BODY, index) not in {"workspace_calibration", "calibration_target"}]
    joints = [_joint_definition(model, index) for index in range(model.njnt)]
    front_camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "front")
    wrist_camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "wrist")
    sensors = [
        {
            "id": "front",
            "sensorType": "rgb_camera",
            "parentLink": "world",
            "translationM": [1.15, -1.15, 0.95],
            "quaternionWxyz": [float(value) for value in model.cam_quat[front_camera_id]],
            "intrinsics": {"fovyDegrees": 48, "calibrationResolution": [256, 256]},
            "calibrated": False,
            "calibrationSource": "RobotWorld default; view-content validated, optical calibration pending",
        },
        {
            "id": "wrist",
            "sensorType": "rgb_camera",
            "parentLink": "hand",
            "translationM": [float(value) for value in model.cam_pos[wrist_camera_id]],
            "quaternionWxyz": [float(value) for value in model.cam_quat[wrist_camera_id]],
            "intrinsics": {"fovyDegrees": 74, "calibrationResolution": [256, 256]},
            "calibrated": False,
            "calibrationSource": "Explicit RobotWorld mount transform; view-content validated, physical calibration pending",
        },
    ]
    reset_pose = {name: float(model.key_qpos[0, index]) for index, name in enumerate(ARM_JOINTS + FINGER_JOINTS)}
    definition = {
        "schemaVersion": "robotworld.robot.v1",
        "id": robot_id,
        "revision": 1,
        "displayName": "Franka Panda 7-DoF arm + parallel gripper",
        "sourceFormat": "mjcf",
        "sourcePath": str(source),
        "sourceRevision": revision or "unrecorded",
        "sourceSha256": source_sha256,
        "links": links,
        "joints": joints,
        "sensors": sensors,
        "embodiment": {
            "baseType": "fixed",
            "endEffectors": ["franka_ee"],
            "grippers": ["left_finger", "right_finger"],
            "observationSchema": {
                "frontRgb": [256, 256, 3],
                "wristRgb": [256, 256, 3],
                "jointPosition": [9],
                "jointVelocity": [9],
                "endEffectorPose": [7],
                "gripperWidth": [1],
            },
            "actionSchema": {
                "runtimeActuatorControl": [8],
                "recommendedPolicyAction": ["dx", "dy", "dz", "droll", "dpitch", "dyaw", "gripper_command"],
                "frame": "end_effector_delta_then_differential_ik",
            },
            "resetPose": reset_pose,
            "safetyLimits": {"jointPosition": {joint["id"]: [joint["lower"], joint["upper"]] for joint in joints}, "cartesianDeltaM": 0.05, "rotationDeltaRad": 0.2},
            "controller": {"type": "differential_ik", "controlRateHz": 50, "physicsRateHz": round(1.0 / model.opt.timestep)},
        },
        "licenseMetadata": {
            "spdx": "Apache-2.0",
            "licenseFile": "LICENSE",
            "sourceUrl": MENAGERIE_SOURCE_URL,
            "attribution": "MuJoCo Menagerie franka_emika_panda model",
        },
        "lifecycleState": "AVAILABLE" if validation["passed"] else "REJECTED",
        "validationErrors": validation["errors"],
    }
    definition = RobotDefinition.model_validate(definition).model_dump(mode="json", by_alias=True)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source.parent / "LICENSE", root / "LICENSE")
    manifest = {
        "id": robot_id,
        "name": definition["displayName"],
        "format": "mjcf",
        "sourceFile": source.name,
        "sourcePath": str(source),
        "sourceBytes": source.stat().st_size,
        "sha256": source_sha256,
        "sourceRevision": revision or "unrecorded",
        "compilerRevision": COMPILER_REVISION,
        "sourceUrl": MENAGERIE_SOURCE_URL,
        "license": definition["licenseMetadata"],
        "importedAt": datetime.now(timezone.utc).isoformat(),
        "links": len(links),
        "joints": len(joints),
        "armDof": 7,
        "gripperJoints": 2,
        "cameras": 2,
        "cameraNames": list(CAMERA_NAMES),
        "cameraMappings": {
            "observation.images.exterior_1_left": "front",
            "observation.images.exterior_2_left": "wrist",
        },
        "policyAdapter": None,
        "articulated": True,
        "physicsParsed": True,
        "physicsReady": validation["passed"],
        "runtimePath": str(runtime_path),
        "runtimeSha256": _sha256(runtime_path),
        "previewHashes": preview_hashes,
        "wristCameraMount": camera_payload,
        "wristCameraCalibrated": False,
        "validation": validation,
        "definition": definition,
        "runtimeBlockers": [] if validation["passed"] else validation["errors"],
        "unresolvedResources": [],
        "missingJointLimits": 0,
    }
    manifest["readiness"] = {
        "physicsExecutable": validation["passed"],
        "policyExecutable": False,
        "executable": False,
        "blockers": [
            *([] if validation["passed"] else validation["errors"]),
            "No Franka-specific VLA-JEPA state/action adapter or fine-tuned checkpoint is attached.",
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    (root / "validation.json").write_text(json.dumps(validation, indent=2), encoding="utf8")
    return manifest


def probe_registered_runtime(robot_id: str) -> dict[str, Any]:
    """Load a managed runtime scene and prove its reset/camera contract."""
    root = (ROBOTS_DIR / robot_id).resolve()
    if root.parent != ROBOTS_DIR.resolve():
        raise ValueError("Invalid robot ID.")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(robot_id)
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    runtime = Path(str(manifest.get("runtimePath") or "")).resolve(strict=True)
    if root != runtime and root not in runtime.parents:
        raise ValueError("Registered runtime path escaped its immutable robot artifact directory.")
    if _sha256(runtime) != manifest.get("runtimeSha256"):
        raise ValueError("Registered Franka runtime hash no longer matches its manifest.")
    model = mujoco.MjModel.from_xml_path(str(runtime))
    data = mujoco.MjData(model)
    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    if home_id < 0:
        raise ValueError("Registered Franka runtime has no home keyframe.")
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    mujoco.mj_forward(model, data)
    camera_ids = {
        name: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, name)
        for name in CAMERA_NAMES
    }
    if any(index < 0 for index in camera_ids.values()):
        raise ValueError("Registered Franka runtime camera contract is incomplete.")
    return {
        "loadedIntoValidationProcess": True,
        "resident": False,
        "runtimePath": str(runtime),
        "runtimeSha256": manifest["runtimeSha256"],
        "mujocoVersion": mujoco.__version__,
        "nq": model.nq,
        "nv": model.nv,
        "actuators": model.nu,
        "cameras": camera_ids,
        "homeFinite": bool(np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all()),
        "workerContract": "simulation jobs reload this pinned runtime by artifact ID",
    }
