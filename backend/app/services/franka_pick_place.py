"""Authoritative MuJoCo Franka pick/place world and deterministic oracle."""
from __future__ import annotations

import hashlib
import json
import math
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import mujoco
import numpy as np
from PIL import Image

from ..config import DATA_DIR, ROBOTS_DIR, WORLDS_DIR
from ..contracts import PlacementRequest
from .simulation_backend import ContactEvent, SimulationBackend


TEMPLATE_ID = "franka-tabletop-pick-place-v1"
TEMPLATE_REVISION = 1
COMPILED_ASSET_WORLD_REVISION = 6
COMPILED_ASSET_ORACLE_POLICY = "deterministic_differential_ik_compiled_asset_oracle_v13"
AUTHORED_SCENE_ORACLE_POLICY = "deterministic_authored_scene_contact_ik_oracle_v1"
AUTHORED_SCENE_DROP_ORACLE_POLICY = "deterministic_authored_scene_drop_off_table_oracle_v1"
PHYSICS_HZ = 500
CONTROL_HZ = 50
OBJECT_HALF_SIZE_M = 0.025
OBJECT_MASS_KG = 0.04
INITIAL_OBJECT_XYZ = np.array([0.48, -0.12, 0.307])
TARGET_XY = np.array([0.48, 0.18])
TARGET_RADIUS_M = 0.065
TABLE_TOP_Z = 0.28


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf8")
    return hashlib.sha256(encoded).hexdigest()


def _quaternion_rotation_span(quaternions: list[np.ndarray]) -> float:
    """Maximum pairwise SO(3) angle for a bounded WXYZ quaternion window."""
    values = np.asarray(quaternions, dtype=float)
    values /= np.linalg.norm(values, axis=1, keepdims=True)
    minimum_abs_dot = float(np.min(np.abs(values @ values.T)))
    return float(2 * np.arccos(np.clip(minimum_abs_dot, -1.0, 1.0)))


def _safe_robot_manifest(robot_id: str) -> tuple[Path, dict[str, Any]]:
    root = (ROBOTS_DIR / robot_id).resolve()
    if root.parent != ROBOTS_DIR.resolve():
        raise ValueError("Invalid robot ID.")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(robot_id)
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    if not manifest.get("physicsReady"):
        raise ValueError("Robot has not passed physics validation.")
    runtime = Path(str(manifest.get("runtimePath") or "")).resolve(strict=True)
    if root not in runtime.parents or _sha256(runtime) != manifest.get("runtimeSha256"):
        raise ValueError("Robot runtime path or hash does not match the immutable registration.")
    return runtime, manifest


def compile_world_template(robot_id: str) -> dict[str, Any]:
    runtime, robot = _safe_robot_manifest(robot_id)
    root = (WORLDS_DIR / TEMPLATE_ID / f"robot-{robot_id}").resolve()
    if (WORLDS_DIR / TEMPLATE_ID).resolve() not in root.parents:
        raise ValueError("Invalid world-template target.")
    world_path = root / "runtime" / "world.xml"
    tree = ET.parse(runtime)
    mujoco_root = tree.getroot()
    worldbody = mujoco_root.find("worldbody")
    if worldbody is None:
        raise ValueError("Robot runtime has no worldbody.")
    for body in list(worldbody.findall("body")):
        if body.get("name") == "calibration_target":
            worldbody.remove(body)
    ET.SubElement(
        worldbody,
        "geom",
        {
            "name": "target_marker",
            "type": "cylinder",
            "pos": f"{TARGET_XY[0]} {TARGET_XY[1]} {TABLE_TOP_Z + 0.002}",
            "size": f"{TARGET_RADIUS_M} 0.002",
            "rgba": "0.12 0.72 0.36 0.65",
            "contype": "0",
            "conaffinity": "0",
        },
    )
    object_body = ET.SubElement(worldbody, "body", {"name": "pick_object", "pos": " ".join(str(value) for value in INITIAL_OBJECT_XYZ)})
    ET.SubElement(object_body, "freejoint", {"name": "pick_object_free"})
    inertia = OBJECT_MASS_KG * (0.05**2 + 0.05**2) / 12.0
    ET.SubElement(object_body, "inertial", {"mass": str(OBJECT_MASS_KG), "pos": "0 0 0", "diaginertia": f"{inertia} {inertia} {inertia}"})
    ET.SubElement(
        object_body,
        "geom",
        {
            "name": "pick_object_geom",
            "type": "box",
            "size": f"{OBJECT_HALF_SIZE_M} {OBJECT_HALF_SIZE_M} {OBJECT_HALF_SIZE_M}",
            "rgba": "0.88 0.24 0.10 1",
            "friction": "2.0 0.01 0.001",
            "solref": "0.003 1",
            "solimp": "0.95 0.99 0.001",
        },
    )
    for body_name in ("left_finger", "right_finger"):
        body = next((value for value in worldbody.iter("body") if value.get("name") == body_name), None)
        if body is not None:
            for geom in body.findall("geom"):
                geom.set("friction", "2.5 0.01 0.001")
    ET.indent(tree, space="  ")
    world_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(world_path, encoding="utf-8", xml_declaration=True)
    model = mujoco.MjModel.from_xml_path(str(world_path))
    required = {
        "franka_ee": mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "franka_ee"),
        "pick_object": mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "pick_object"),
        "front": mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "front"),
        "wrist": mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "wrist"),
    }
    if any(value < 0 for value in required.values()):
        raise ValueError(f"Compiled world is missing required elements: {required}")
    template = {
        "schemaVersion": "robotworld.world-template.v1",
        "id": TEMPLATE_ID,
        "revision": TEMPLATE_REVISION,
        "name": "Franka tabletop pick/place validation",
        "coordinateSystem": {"units": "metres", "upAxis": "Z", "handedness": "right"},
        "runtimeBackend": "mujoco",
        "runtimePath": str(world_path),
        "runtimeSha256": _sha256(world_path),
        "robotId": robot_id,
        "robotRuntimeSha256": robot["runtimeSha256"],
        "supportSurfaces": [
            {"id": "workspace_surface", "semantic": "table", "centerM": [0.5, 0.0, TABLE_TOP_Z], "halfExtentsM": [0.30, 0.34, 0.025]}
        ],
        "targetVolumes": [
            {"id": "green_target", "shape": "cylinder", "centerM": [float(TARGET_XY[0]), float(TARGET_XY[1]), TABLE_TOP_Z], "radiusM": TARGET_RADIUS_M}
        ],
        "robotSpawnAnchors": [{"id": "franka_fixed", "baseLink": "link0", "pose": [0, 0, 0, 1, 0, 0, 0]}],
        "cameraAnchors": [{"id": "front", "parent": "world"}, {"id": "wrist", "parent": "hand"}],
        "allowedObjectCategories": ["small_rigid_graspable"],
        "source": {"type": "robotworld_controlled_template", "license": "project"},
        "validation": {"loads": True, "nq": model.nq, "nv": model.nv, "nu": model.nu},
    }
    (root / "template.json").write_text(json.dumps(template, indent=2), encoding="utf8")
    return template


def _verified_compiled_artifact(reference: dict[str, Any]) -> Path:
    artifact_ref = str(reference.get("artifactRef") or "")
    expected_sha = str(reference.get("sha256") or "")
    path = (DATA_DIR / artifact_ref).resolve(strict=True)
    if not path.is_relative_to(DATA_DIR.resolve()):
        raise ValueError("Compiled asset artifact escaped the RobotWorld data root.")
    if _sha256(path) != expected_sha:
        raise ValueError(f"Compiled asset artifact hash mismatch: {artifact_ref}")
    return path


def _manifest_artifact(manifest: dict[str, Any], group: str, kind: str) -> Path:
    references = manifest.get(group) or []
    reference = next((item for item in references if item.get("kind") == kind), None)
    if reference is None:
        raise ValueError(f"Compiled asset manifest is missing {kind}.")
    return _verified_compiled_artifact(reference)


def _home_gripper_closing_axis(runtime: Path) -> tuple[int, np.ndarray]:
    """Measure the Panda's parallel-jaw axis in the registered home frame.

    The Menagerie Panda hand closes along its local Y axis, but the fixed-base
    home pose rotates that axis into world X.  Measuring the registered model
    keeps placement planning tied to the actual embodiment rather than a
    silently guessed world axis.
    """
    model = mujoco.MjModel.from_xml_path(str(runtime))
    data = mujoco.MjData(model)
    home = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    left = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "left_finger")
    right = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "right_finger")
    if min(home, left, right) < 0:
        raise ValueError("Registered robot lacks the home keyframe or parallel-jaw finger bodies.")
    mujoco.mj_resetDataKeyframe(model, data, home)
    mujoco.mj_forward(model, data)
    separation = np.asarray(data.xpos[left] - data.xpos[right], dtype=float)
    norm = float(np.linalg.norm(separation))
    if norm < 0.01:
        raise ValueError("Registered robot finger separation is too small to determine a closing axis.")
    direction = separation / norm
    horizontal = np.abs(direction[:2])
    axis_index = int(np.argmax(horizontal))
    if float(horizontal[axis_index]) < 0.95 or abs(float(direction[2])) > 0.1:
        raise ValueError("The current top-grasp oracle requires a nearly world-horizontal parallel-jaw axis.")
    return axis_index, direction


def _stable_placement(
    collision_path: Path,
    *,
    placement_xy: np.ndarray,
    support_center: np.ndarray,
    support_half: np.ndarray,
    gripper_axis_index: int,
    local_grasp_hint: np.ndarray | None = None,
    orientation_seed: int | None = None,
) -> dict[str, Any]:
    """Choose a deterministic stable pose, preferring a Franka-width grasp."""
    import trimesh
    from scipy.spatial.transform import Rotation

    mesh = trimesh.load_mesh(collision_path, process=False)
    if not isinstance(mesh, trimesh.Trimesh) or not mesh.is_watertight:
        raise ValueError("Compiled collision artifact is not a watertight mesh.")
    transforms, probabilities = trimesh.poses.compute_stable_poses(mesh, sigma=0.0, n_samples=1)
    if not len(transforms):
        raise ValueError("No deterministic stable pose was found for the compiled collision mesh.")
    vertices = np.asarray(mesh.vertices, dtype=float)
    candidates: list[dict[str, Any]] = []
    # Yaw does not change stability. Sample it deterministically so the long
    # horizontal axis can align with the table/parallel-jaw grasp direction.
    for pose_index, (transform, probability) in enumerate(zip(transforms[:32], probabilities[:32])):
        base_rotation = np.asarray(transform[:3, :3], dtype=float)
        for yaw_index, yaw in enumerate(np.linspace(0.0, math.pi, 37, endpoint=False)):
            c, s = math.cos(float(yaw)), math.sin(float(yaw))
            yaw_rotation = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)
            rotation = yaw_rotation @ base_rotation
            rotated = vertices @ rotation.T
            bounds = np.stack((rotated.min(axis=0), rotated.max(axis=0)))
            extents = bounds[1] - bounds[0]
            translation = np.array(
                [-(bounds[0, 0] + bounds[1, 0]) / 2, -(bounds[0, 1] + bounds[1, 1]) / 2, -bounds[0, 2]],
                dtype=float,
            )
            clearance = support_half - (np.abs(placement_xy - support_center) + extents[:2] / 2)
            if (clearance < 0.015).any():
                continue
            required_width = float(extents[gripper_axis_index])
            grasp_feasible = required_width <= 0.077
            if local_grasp_hint is None:
                # Compatibility path for already-catalogued world revisions.
                desired_pose_point = np.array([0.0, 0.0, extents[2] / 2])
                local_grasp = rotation.T @ (desired_pose_point - translation)
            else:
                # Grasp at the compiler-authored center of mass to minimize the
                # gravity moment that can rotate a long object inside the jaws.
                local_grasp = np.asarray(local_grasp_hint, dtype=float)
                desired_pose_point = rotation @ local_grasp + translation
            quaternion_xyzw = Rotation.from_matrix(rotation).as_quat()
            bounding_radius = float(np.max(np.linalg.norm(vertices - local_grasp, axis=1)))
            candidates.append(
                {
                    "poseIndex": pose_index,
                    "yawIndex": yaw_index,
                    "probability": float(probability),
                    "rotation": rotation,
                    "quaternionWxyz": [
                        float(quaternion_xyzw[3]),
                        float(quaternion_xyzw[0]),
                        float(quaternion_xyzw[1]),
                        float(quaternion_xyzw[2]),
                    ],
                    "translation": translation,
                    "worldExtentsM": extents,
                    "clearanceM": clearance,
                    "requiredGripperWidthM": required_width,
                    "gripperAxisIndex": gripper_axis_index,
                    "graspFeasible": grasp_feasible,
                    "localGraspPointM": local_grasp,
                    "placedGraspHeightM": float(desired_pose_point[2]),
                    "localBoundingRadiusM": bounding_radius,
                }
            )
    if not candidates:
        raise ValueError("No stable pose fits the semantic support polygon with 15 mm clearance.")
    baseline = max(
        candidates,
        key=lambda value: (
            int(value["graspFeasible"]),
            value["probability"],
            -value["requiredGripperWidthM"],
            -value["poseIndex"],
            -value["yawIndex"],
        ),
    )
    selected = baseline
    selection_mode = "best_graspable_stable_pose"
    if orientation_seed is not None:
        alternatives = [
            candidate
            for candidate in candidates
            if candidate["graspFeasible"]
            and candidate["poseIndex"] == baseline["poseIndex"]
            and candidate["yawIndex"] != baseline["yawIndex"]
        ]
        alternatives.sort(key=lambda value: (value["yawIndex"], value["requiredGripperWidthM"]))
        if not alternatives:
            raise ValueError("No alternate stable orientation remains within the Franka gripper-width limit.")
        digest = hashlib.sha256(f"orientation:{int(orientation_seed)}".encode("ascii")).digest()
        selected = alternatives[int.from_bytes(digest[:8], "big") % len(alternatives)]
        selection_mode = "seeded_graspable_yaw_variation"
    selected = dict(selected)
    selected["candidateCount"] = len(candidates)
    selected["stablePoseCount"] = int(len(transforms))
    selected["selectionMode"] = selection_mode
    selected["baselinePoseIndex"] = baseline["poseIndex"]
    selected["baselineYawIndex"] = baseline["yawIndex"]
    return selected


def compile_compiled_asset_world_template(
    robot_id: str,
    asset_version: dict[str, Any],
    *,
    placement_request: PlacementRequest | dict[str, Any] | None = None,
    _compiler_revision: int = COMPILED_ASSET_WORLD_REVISION,
) -> dict[str, Any]:
    """Compose one PHYSICS_VALIDATED compiler version into the pinned Franka world."""
    if _compiler_revision not in set(range(1, COMPILED_ASSET_WORLD_REVISION + 1)):
        raise ValueError("Unsupported compiled-asset world compiler revision.")
    if asset_version.get("lifecycleState") not in {"PHYSICS_VALIDATED", "ORACLE_VALIDATED"}:
        raise ValueError("Compiled asset must be PHYSICS_VALIDATED before robot evaluation.")
    manifest = dict(asset_version.get("manifest") or {})
    if not manifest or manifest.get("versionId") != asset_version.get("id"):
        raise ValueError("Compiled asset manifest/version identity is invalid.")
    if manifest.get("manifestSha256") != asset_version.get("manifestSha256"):
        raise ValueError("Compiled asset manifest hash does not match catalog metadata.")
    visual_path = _manifest_artifact(manifest, "visualArtifacts", "runtime_visual_mesh")
    collision_path = _manifest_artifact(manifest, "collisionArtifacts", "convex_collision_mesh")
    dimensions = np.asarray(manifest.get("dimensionsM") or [], dtype=float)
    center = np.asarray(manifest.get("centerOfMassM") or [], dtype=float)
    inertia = np.asarray(manifest.get("inertiaKgM2") or [], dtype=float)
    mass = float(manifest.get("massKg") or 0)
    if dimensions.shape != (3,) or center.shape != (3,) or inertia.shape != (6,):
        raise ValueError("Compiled asset physical dimensions, center of mass, or inertia contract is invalid.")
    if not np.isfinite(np.concatenate((dimensions, center, inertia, [mass]))).all() or (dimensions <= 0).any() or mass <= 0:
        raise ValueError("Compiled asset physical contract contains non-finite or non-positive values.")

    placement_contract = (
        PlacementRequest.model_validate(placement_request).model_dump(mode="json", by_alias=True)
        if placement_request is not None
        else None
    )
    if placement_contract is not None:
        if _compiler_revision < 6:
            raise ValueError("Scenario placement requests require compiled-asset world revision 6 or newer.")
        if placement_contract["semanticSupportSurface"] != "workspace_surface":
            raise ValueError("The current Franka world exposes only the workspace_surface semantic support surface.")
        required_checks = ("requireReachability", "rejectPenetration", "dropAndSettle")
        disabled_checks = [name for name in required_checks if placement_contract[name] is not True]
        if disabled_checks:
            raise ValueError("Scenario placement cannot disable required physical checks: " + ", ".join(disabled_checks))
    placement_fingerprint = _canonical_sha256(placement_contract) if placement_contract is not None else None

    runtime, robot = _safe_robot_manifest(robot_id)
    if _compiler_revision == 1:
        # Reproducibility-only path for already-catalogued v1 artifacts.  V1
        # assumed world Y and is not used for new evaluations.
        gripper_axis_index, gripper_axis_world = 1, np.array([0.0, 1.0, 0.0])
    else:
        gripper_axis_index, gripper_axis_world = _home_gripper_closing_axis(runtime)
    family = f"franka-compiled-asset-pick-v{_compiler_revision}"
    variant_suffix = f":p{placement_fingerprint[:12]}" if placement_fingerprint else ""
    template_id = f"{family}:{asset_version['id']}{variant_suffix}"
    root = (WORLDS_DIR / family / f"robot-{robot_id}" / f"asset-{asset_version['id']}").resolve()
    if placement_fingerprint:
        root = (root / "placements" / placement_fingerprint).resolve()
    if not root.is_relative_to(WORLDS_DIR.resolve()):
        raise ValueError("Invalid compiled-asset world target.")
    world_path = root / "runtime" / "world.xml"
    tree = ET.parse(runtime)
    mujoco_root = tree.getroot()
    worldbody = mujoco_root.find("worldbody")
    if worldbody is None:
        raise ValueError("Robot runtime has no worldbody.")
    if _compiler_revision >= 5:
        option = mujoco_root.find("option")
        if option is None:
            option = ET.Element("option")
            mujoco_root.insert(0, option)
        # MuJoCo's documented high-accuracy contact settings suppress solver
        # slip without changing authored friction coefficients or actuator
        # limits. Keep them versioned with the executable world artifact.
        option.set("cone", "elliptic")
        option.set("solver", "Newton")
        option.set("tolerance", "1e-10")
        option.set("impratio", "10")
        option.set("noslip_iterations", "5")
        option.set("noslip_tolerance", "1e-8")
    asset_node = mujoco_root.find("asset")
    if asset_node is None:
        asset_node = ET.Element("asset")
        worldbody_index = list(mujoco_root).index(worldbody)
        mujoco_root.insert(worldbody_index, asset_node)
    for body in list(worldbody.findall("body")):
        if body.get("name") == "calibration_target":
            worldbody.remove(body)

    ET.SubElement(asset_node, "mesh", {"name": "compiled_candidate_visual", "file": visual_path.as_posix()})
    ET.SubElement(
        asset_node,
        "mesh",
        {"name": "compiled_candidate_collision", "file": collision_path.as_posix(), "maxhullvert": "256"},
    )
    support_half = np.array([0.30, 0.34])
    support_center = np.array([0.50, 0.0])
    initial_xy = np.array([0.48, -0.12])
    if placement_contract is not None and placement_contract["varyPosition"]:
        digest = hashlib.sha256(
            f"position:{placement_contract['seed']}:{placement_fingerprint}".encode("ascii")
        ).digest()
        unit_x = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
        unit_y = int.from_bytes(digest[8:16], "big") / float(2**64 - 1)
        # Sample a conservative, embodiment-reachable subregion of the named
        # surface. Final mesh clearance, penetration, settle, and oracle checks
        # below remain authoritative.
        initial_xy = np.array(
            [
                support_center[0] + (unit_x - 0.5) * 0.16,
                support_center[1] - 0.12 + (unit_y - 0.5) * 0.14,
            ]
        )
    placement = _stable_placement(
        collision_path,
        placement_xy=initial_xy,
        support_center=support_center,
        support_half=support_half,
        gripper_axis_index=gripper_axis_index,
        local_grasp_hint=center if _compiler_revision >= 4 else None,
        orientation_seed=(
            int(placement_contract["seed"])
            if placement_contract is not None and placement_contract["varyOrientation"]
            else None
        ),
    )
    stable_translation = np.asarray(placement["translation"], dtype=float)
    initial_xyz = np.array([initial_xy[0], initial_xy[1], TABLE_TOP_Z + 0.002]) + stable_translation
    initial_quaternion = list(placement["quaternionWxyz"])
    world_extents = np.asarray(placement["worldExtentsM"], dtype=float)
    object_half_xy = world_extents[:2] / 2
    clearance = np.asarray(placement["clearanceM"], dtype=float)
    object_body = ET.SubElement(
        worldbody,
        "body",
        {
            "name": "pick_object",
            "pos": " ".join(f"{value:.12g}" for value in initial_xyz),
            "quat": " ".join(f"{value:.12g}" for value in initial_quaternion),
        },
    )
    ET.SubElement(object_body, "freejoint", {"name": "pick_object_free"})
    ET.SubElement(
        object_body,
        "inertial",
        {
            "mass": f"{mass:.12g}",
            "pos": " ".join(f"{value:.12g}" for value in center),
            "fullinertia": " ".join(f"{value:.12g}" for value in inertia),
        },
    )
    ET.SubElement(
        object_body,
        "geom",
        {
            "name": "pick_object_visual",
            "type": "mesh",
            "mesh": "compiled_candidate_visual",
            "rgba": "0.72 0.72 0.74 1",
            "contype": "0",
            "conaffinity": "0",
            "group": "2",
        },
    )
    friction_range = (manifest.get("material") or {}).get("frictionRange") or [0.3, 0.8]
    friction = float(sum(float(value) for value in friction_range) / 2)
    ET.SubElement(
        object_body,
        "geom",
        {
            "name": "pick_object_collision",
            "type": "mesh",
            "mesh": "compiled_candidate_collision",
            "rgba": "0 0 0 0",
            "friction": f"{friction:.8g} 0.005 0.0001",
            "solref": "0.005 1",
            "group": "3",
        },
    )
    local_grasp = np.asarray(placement["localGraspPointM"], dtype=float)
    grasp_height = float(placement["placedGraspHeightM"])
    world_grasp_position = initial_xyz + np.asarray(placement["rotation"], dtype=float) @ local_grasp
    reachability_distance = float(np.linalg.norm(world_grasp_position[:2]))
    if placement_contract is not None and placement_contract["requireReachability"]:
        if not 0.25 <= reachability_distance <= 0.75 or not TABLE_TOP_Z <= world_grasp_position[2] <= 0.85:
            raise ValueError(
                "Sampled placement grasp frame is outside the configured Franka reachability envelope."
            )
    ET.SubElement(
        object_body,
        "site",
        {
            "name": "compiled_asset_grasp",
            "pos": " ".join(f"{value:.12g}" for value in local_grasp),
            "size": "0.006",
            "rgba": "0.1 0.85 1 0.8",
        },
    )
    target_radius = float(max(0.08, min(0.18, np.linalg.norm(object_half_xy) + 0.04)))
    ET.SubElement(
        worldbody,
        "geom",
        {
            "name": "target_marker",
            "type": "cylinder",
            "pos": f"{TARGET_XY[0]} {TARGET_XY[1]} {TABLE_TOP_Z + 0.002}",
            "size": f"{target_radius} 0.002",
            "rgba": "0.12 0.72 0.36 0.65",
            "contype": "0",
            "conaffinity": "0",
        },
    )
    for body_name in ("left_finger", "right_finger"):
        body = next((value for value in worldbody.iter("body") if value.get("name") == body_name), None)
        if body is not None:
            for geom in body.findall("geom"):
                geom.set("friction", "2.5 0.01 0.001")

    ET.indent(tree, space="  ")
    world_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(world_path, encoding="utf-8", xml_declaration=True)
    model = mujoco.MjModel.from_xml_path(str(world_path))
    data = mujoco.MjData(model)
    home = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    mujoco.mj_resetDataKeyframe(model, data, home)
    object_joint = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "pick_object_free")
    object_qpos = int(model.jnt_qposadr[object_joint])
    # The source Franka keyframe predates this appended free joint; restore the
    # compiled body's qpos0 explicitly after applying that robot-only keyframe.
    data.qpos[object_qpos : object_qpos + 7] = model.qpos0[object_qpos : object_qpos + 7]
    data.qvel[:] = 0
    mujoco.mj_forward(model, data)
    object_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "pick_object")
    initial_severe = 0
    for index in range(data.ncon):
        contact = data.contact[index]
        if object_id in {int(model.geom_bodyid[contact.geom1]), int(model.geom_bodyid[contact.geom2])} and float(contact.dist) < -0.005:
            initial_severe += 1
    settle_positions: list[np.ndarray] = []
    for index in range(3000):
        mujoco.mj_step(model, data)
        if index >= 2625:
            settle_positions.append(data.xpos[object_id].copy())
    settle_span = float(np.max(np.ptp(np.asarray(settle_positions), axis=0)))
    settle_speed = float(np.linalg.norm(data.cvel[object_id]))
    settle_linear_speed = float(np.linalg.norm(data.cvel[object_id, 3:]))
    settle_angular_speed = float(np.linalg.norm(data.cvel[object_id, :3]))
    finite = bool(np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all())
    errors: list[str] = []
    if initial_severe:
        errors.append(f"placement starts with {initial_severe} object contacts deeper than 5 mm")
    if not finite:
        errors.append("placement settle produced non-finite state")
    if settle_span > 0.003 or settle_linear_speed > 0.01 or settle_angular_speed > 0.15:
        errors.append("placement did not settle stably on the semantic support surface")
    if errors:
        raise ValueError("; ".join(errors))
    accepted_qpos = data.qpos[object_qpos : object_qpos + 7].copy()
    planned_qpos = np.concatenate((initial_xyz, np.asarray(initial_quaternion, dtype=float)))
    object_body.set("pos", " ".join(f"{value:.12g}" for value in accepted_qpos[:3]))
    object_body.set("quat", " ".join(f"{value:.12g}" for value in accepted_qpos[3:7]))
    ET.indent(tree, space="  ")
    tree.write(world_path, encoding="utf-8", xml_declaration=True)
    # Reopen the exact accepted-pose artifact; this is the runtime hash used by
    # the evaluation and ensures the planner did not only validate in memory.
    model = mujoco.MjModel.from_xml_path(str(world_path))
    if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "compiled_asset_grasp") < 0:
        raise ValueError("Accepted placement runtime lost the compiled grasp frame.")

    template = {
        "schemaVersion": "robotworld.world-template.v1",
        "id": template_id,
        "revision": _compiler_revision,
        "name": f"Franka compiled-asset oracle · {asset_version['displayName']}",
        "coordinateSystem": {"units": "metres", "upAxis": "Z", "handedness": "right"},
        "runtimeBackend": "mujoco",
        "runtimePath": str(world_path),
        "runtimeSha256": _sha256(world_path),
        "robotId": robot_id,
        "robotRuntimeSha256": robot["runtimeSha256"],
        "assetVersionId": asset_version["id"],
        "assetManifestSha256": asset_version["manifestSha256"],
        "placementRequest": placement_contract,
        "placementFingerprint": placement_fingerprint,
        "supportSurfaces": [
            {"id": "workspace_surface", "semantic": "table", "centerM": [0.5, 0.0, TABLE_TOP_Z], "halfExtentsM": [0.30, 0.34, 0.025]}
        ],
        "targetVolumes": [
            {"id": "green_target", "shape": "cylinder", "centerM": [float(TARGET_XY[0]), float(TARGET_XY[1]), TABLE_TOP_Z], "radiusM": target_radius}
        ],
        "placements": [
            {
                "assetVersionId": asset_version["id"],
                "supportSurfaceId": "workspace_surface",
                "pose": accepted_qpos.tolist(),
                "plannedPose": planned_qpos.tolist(),
                "seed": int(placement_contract["seed"]) if placement_contract is not None else 0,
                "requestedPositionVariation": bool(placement_contract and placement_contract["varyPosition"]),
                "requestedOrientationVariation": bool(placement_contract and placement_contract["varyOrientation"]),
                "sampledSupportPositionM": initial_xy.tolist(),
                "clearanceM": clearance.tolist(),
                "stablePoseIndex": placement["poseIndex"],
                "stableYawIndex": placement["yawIndex"],
                "selectionMode": placement["selectionMode"],
                "baselineStablePoseIndex": placement["baselinePoseIndex"],
                "baselineStableYawIndex": placement["baselineYawIndex"],
                "stablePoseProbability": placement["probability"],
                "stablePoseCandidateCount": placement["candidateCount"],
                "stablePoseCount": placement["stablePoseCount"],
                "settledWorldDimensionsM": world_extents.tolist(),
                "settlePoseDelta": float(np.max(np.abs(accepted_qpos - planned_qpos))),
                "initialSeverePenetrations": initial_severe,
                "settlePositionSpanM": settle_span,
                "settleSpeed": settle_speed,
                "settleLinearSpeedMps": settle_linear_speed,
                "settleAngularSpeedRadS": settle_angular_speed,
                "settleSimulatedSeconds": 3000 / PHYSICS_HZ,
                "graspReachabilityDistanceM": reachability_distance,
                "accepted": True,
            }
        ],
        "graspContract": {
            "site": "compiled_asset_grasp",
            "localPositionM": local_grasp.tolist(),
            "placedGraspHeightM": grasp_height,
            "placedObjectHeightM": float(world_extents[2]),
            "requiredGripperWidthM": float(placement["requiredGripperWidthM"]),
            "localBoundingRadiusM": float(placement["localBoundingRadiusM"]),
            "targetContainmentMarginM": float(target_radius - placement["localBoundingRadiusM"]),
            "gripperClosingAxisWorld": gripper_axis_world.tolist(),
            "gripperClosingAxisIndex": gripper_axis_index,
            "geometricallyFeasible": bool(placement["graspFeasible"]),
            "strategy": "deterministic_stable_pose_then_center_of_mass_top_grasp_aligned_to_measured_robot_gripper_axis",
        },
        "robotSpawnAnchors": [{"id": "franka_fixed", "baseLink": "link0", "pose": [0, 0, 0, 1, 0, 0, 0]}],
        "cameraAnchors": [{"id": "front", "parent": "world"}, {"id": "wrist", "parent": "hand"}],
        "allowedObjectCategories": [manifest.get("category") or asset_version.get("category")],
        "source": {
            "type": "compiled_asset_version",
            "assetVersionId": asset_version["id"],
            "scenarioFingerprint": placement_contract.get("scenarioFingerprint") if placement_contract else None,
        },
        "validation": {
            "loads": True,
            "nq": model.nq,
            "nv": model.nv,
            "nu": model.nu,
            "initialSeverePenetrations": initial_severe,
            "placementFinite": finite,
            "placementFingerprint": placement_fingerprint,
            "graspReachabilityDistanceM": reachability_distance,
            "settlePositionSpanM": settle_span,
            "settleSpeed": settle_speed,
            "settleLinearSpeedMps": settle_linear_speed,
            "settleAngularSpeedRadS": settle_angular_speed,
            "settleSimulatedSeconds": 3000 / PHYSICS_HZ,
            "thresholds": {
                "minimumSupportClearanceM": 0.015,
                "maxSettlePositionSpanM": 0.003,
                "maxFinalLinearSpeedMps": 0.01,
                "maxFinalAngularSpeedRadS": 0.15,
            "frankaGraspWidthWithClearanceM": 0.077,
            },
            "contactSolver": {
                "cone": "elliptic" if _compiler_revision >= 5 else "pyramidal",
                "solver": "Newton",
                "tolerance": 1e-10 if _compiler_revision >= 5 else 1e-8,
                "impratio": 10 if _compiler_revision >= 5 else 1,
                "noslipIterations": 5 if _compiler_revision >= 5 else 0,
            },
        },
    }
    (root / "template.json").write_text(json.dumps(template, indent=2), encoding="utf8")
    return template


def compile_authored_scene_asset_world(
    robot_id: str,
    asset_version: dict[str, Any],
    *,
    world_id: str,
    source_placement: dict[str, Any],
    target_placement: dict[str, Any] | None,
    counter_placement: dict[str, Any],
    robot_spawn: dict[str, Any] | None = None,
    task_kind: str = "pick_place",
    relation: str = "on_top_of",
) -> dict[str, Any]:
    """Compose one catalogued physical asset into the active authored world.

    The generated kitchen GLBs remain the visual layer. This compiler creates
    the executable collision subset needed by the selected task: counter,
    selected movable object, target support, and the registered Panda. It
    never treats an arbitrary visual GLB as a dynamic collider.
    """

    if task_kind not in {"pick_place", "drop_off_table"}:
        raise ValueError(f"Unsupported authored-scene task kind: {task_kind}")
    if relation not in {"on_top_of", "inside", "outside_support"}:
        raise ValueError(f"Unsupported authored-scene relation: {relation}")
    identifiers = [world_id, str(source_placement.get("assetId") or "")]
    if target_placement is not None:
        identifiers.append(str(target_placement.get("assetId") or ""))
    for identifier in identifiers:
        if not identifier or not all(character.isalnum() or character in "._-" for character in identifier):
            raise ValueError("Authored world or placement identity is invalid.")
    if asset_version.get("assetId") != source_placement.get("assetId"):
        raise ValueError("Physical asset version does not belong to the selected authored object.")
    base = compile_compiled_asset_world_template(robot_id, asset_version)
    family = "authored-kitchen-pick-place-v1" if task_kind == "pick_place" else "authored-kitchen-drop-off-table-v1"
    root = (WORLDS_DIR / family / f"world-{world_id}" / f"robot-{robot_id}" / f"asset-{asset_version['id']}").resolve()
    if not root.is_relative_to(WORLDS_DIR.resolve()):
        raise ValueError("Invalid authored-world runtime target.")
    world_path = root / "runtime" / "world.xml"
    tree = ET.parse(Path(base["runtimePath"]))
    mujoco_root = tree.getroot()
    worldbody = mujoco_root.find("worldbody")
    if worldbody is None:
        raise ValueError("Compiled world has no worldbody.")

    def bounds(value: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
        raw = np.asarray(value.get("worldBounds") or [], dtype=float)
        if raw.shape != (2, 3) or not np.isfinite(raw).all() or (raw[1] <= raw[0]).any():
            raise ValueError(f"Placement {value.get('assetId')} has invalid world bounds.")
        return raw[0], raw[1]

    counter_low, counter_high = bounds(counter_placement)
    source_low, source_high = bounds(source_placement)
    counter_top = float(counter_high[2])
    if task_kind == "pick_place":
        if target_placement is None:
            raise ValueError("Pick/place authored scene requires a fixed target placement.")
        target_low, target_high = bounds(target_placement)
        target_top = float(target_high[2])

    root_body = next((body for body in worldbody.findall("body") if body.get("name") == "link0"), None)
    workspace = next((body for body in worldbody.findall("body") if body.get("name") == "workspace_calibration"), None)
    pick_object = next((body for body in worldbody.findall("body") if body.get("name") == "pick_object"), None)
    if root_body is None or workspace is None or pick_object is None:
        raise ValueError("Compiled world is missing Panda, workspace, or selected object bodies.")
    requested_spawn = list((robot_spawn or {}).get("positionM") or [-0.15, float(counter_low[1]) + 0.045, counter_top])
    requested_quaternion = list((robot_spawn or {}).get("quaternionWxyz") or [0.707106781187, 0.0, 0.0, 0.707106781187])
    spawn = np.asarray(requested_spawn, dtype=float)
    spawn_quaternion = np.asarray(requested_quaternion, dtype=float)
    if spawn.shape != (3,) or spawn_quaternion.shape != (4,) or not np.isfinite(np.concatenate((spawn, spawn_quaternion))).all():
        raise ValueError("Authored Franka spawn is invalid.")
    if abs(float(np.linalg.norm(spawn_quaternion)) - 1.0) > 1e-4:
        raise ValueError("Authored Franka spawn quaternion is not normalized.")
    if abs(float(spawn[2]) - counter_top) > 0.015:
        raise ValueError("Authored Franka spawn is not mounted on the counter top.")
    root_body.set("pos", " ".join(f"{value:.12g}" for value in spawn))
    # The Menagerie home pose reaches along local +X. Mount the arm at the
    # counter front and rotate local +X toward world +Y, keeping both the
    # authored fruit row and blender in the forward manipulation workspace.
    root_body.set("quat", " ".join(f"{value:.12g}" for value in spawn_quaternion))

    counter_center = (counter_low + counter_high) / 2
    counter_half = (counter_high - counter_low) / 2
    workspace.set("pos", " ".join(f"{value:.12g}" for value in counter_center))
    workspace_geom = workspace.find("geom")
    if workspace_geom is None:
        raise ValueError("Compiled world workspace has no collision geometry.")
    workspace_geom.set("size", " ".join(f"{value:.12g}" for value in counter_half))

    # An integrated sink cannot coexist with a solid countertop box.  Split
    # the measured counter AABB around the measured sink opening so the source
    # can physically descend into the basin instead of colliding with an
    # invisible slab.  These are explicit AABB collision proxies, not claims
    # that the generated visual mesh itself is a validated collider.
    if task_kind == "pick_place" and relation == "inside" and target_placement is not None and "sink" in str(target_placement.get("name") or "").lower():
        workspace.set("pos", "0 0 0")
        workspace.remove(workspace_geom)

        def add_counter_box(name: str, low: np.ndarray, high: np.ndarray) -> None:
            if np.any(high - low <= 0.002):
                return
            center = (low + high) / 2
            half = (high - low) / 2
            ET.SubElement(
                workspace,
                "geom",
                {
                    "name": name,
                    "type": "box",
                    "pos": " ".join(f"{value:.12g}" for value in center),
                    "size": " ".join(f"{value:.12g}" for value in half),
                    "rgba": "0.38 0.40 0.44 1",
                    "friction": "0.8 0.01 0.001",
                },
            )

        opening_center = (target_low + target_high) / 2
        opening_half = (target_high - target_low) / 2 * 0.72
        opening_low = np.maximum(counter_low, opening_center - opening_half)
        opening_high = np.minimum(counter_high, opening_center + opening_half)
        add_counter_box("counter_left", counter_low, np.array([opening_low[0], counter_high[1], counter_high[2]]))
        add_counter_box("counter_right", np.array([opening_high[0], counter_low[1], counter_low[2]]), counter_high)
        add_counter_box(
            "counter_front",
            np.array([opening_low[0], counter_low[1], counter_low[2]]),
            np.array([opening_high[0], opening_low[1], counter_high[2]]),
        )
        add_counter_box(
            "counter_back",
            np.array([opening_low[0], opening_high[1], counter_low[2]]),
            np.array([opening_high[0], counter_high[1], counter_high[2]]),
        )

    original_position = np.asarray([float(value) for value in str(pick_object.get("pos") or "").split()], dtype=float)
    if original_position.shape != (3,):
        raise ValueError("Compiled object pose is invalid.")
    stable_height = float(original_position[2] - TABLE_TOP_Z)
    source_center = (source_low + source_high) / 2
    authored_object_position = np.asarray([source_center[0], source_center[1], counter_top + stable_height])
    pick_object.set("pos", " ".join(f"{value:.12g}" for value in authored_object_position))
    original_quaternion = np.asarray([float(value) for value in str(pick_object.get("quat") or "1 0 0 0").split()], dtype=float)
    if original_quaternion.shape != (4,):
        raise ValueError("Compiled object orientation is invalid.")
    authored_quaternion = np.empty(4, dtype=float)
    mujoco.mju_mulQuat(
        authored_quaternion,
        np.asarray([math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)], dtype=float),
        original_quaternion,
    )
    pick_object.set("quat", " ".join(f"{value:.12g}" for value in authored_quaternion))
    friction_range = list(((asset_version.get("manifest") or {}).get("material") or {}).get("frictionRange") or [0.5, 0.5])
    authored_friction = float(max(friction_range))
    if not math.isfinite(authored_friction) or authored_friction <= 0:
        raise ValueError("Compiled object friction range is invalid.")
    for geom in pick_object.findall("geom"):
        if geom.get("name") == "pick_object_collision":
            geom.set("friction", f"{authored_friction:.12g} 0.005 0.0001")
            geom.set("condim", "4")

    authored_grasp_contract = dict(base.get("graspContract") or {})
    grasp_clearance_adjustment = 0.0
    placed_grasp_height = float(authored_grasp_contract.get("placedGraspHeightM") or 0.0)
    # Long, flat generated objects need a small approach clearance so the
    # fingers do not scrape the support plane.  Applying that offset to compact
    # objects (the apple/cube contracts) moves the grasp above their validated
    # contact band and can turn a proven grasp into an object_dropped failure.
    # Gate the adjustment on measured XY aspect ratio rather than an asset name.
    source_extent = np.maximum(source_high - source_low, 1e-9)
    planar_aspect_ratio = float(max(source_extent[0], source_extent[1]) / min(source_extent[0], source_extent[1]))
    authored_grasp_contract["planarAspectRatio"] = planar_aspect_ratio
    minimum_tabletop_grasp_height = 0.030 if planar_aspect_ratio >= 2.0 else placed_grasp_height
    if placed_grasp_height < minimum_tabletop_grasp_height:
        grasp_site = next((site for site in pick_object.findall("site") if site.get("name") == "compiled_asset_grasp"), None)
        if grasp_site is None:
            raise ValueError("Compiled object is missing its candidate grasp site.")
        grasp_site_position = np.asarray([float(value) for value in str(grasp_site.get("pos") or "").split()], dtype=float)
        if grasp_site_position.shape != (3,):
            raise ValueError("Compiled object grasp-site position is invalid.")
        grasp_clearance_adjustment = minimum_tabletop_grasp_height - placed_grasp_height
        grasp_site_position[2] += grasp_clearance_adjustment
        grasp_site.set("pos", " ".join(f"{value:.12g}" for value in grasp_site_position))
        authored_grasp_contract["placedGraspHeightM"] = minimum_tabletop_grasp_height
        local_position = list(authored_grasp_contract.get("localPositionM") or grasp_site_position.tolist())
        local_position[2] = float(local_position[2]) + grasp_clearance_adjustment
        authored_grasp_contract["localPositionM"] = local_position

    for geom in list(worldbody.findall("geom")):
        if geom.get("name") == "target_marker":
            worldbody.remove(geom)
    target_volumes: list[dict[str, Any]] = []
    if task_kind == "pick_place":
        assert target_placement is not None
        target_center = (target_low + target_high) / 2
        target_half = (target_high - target_low) / 2
        target_support_top = target_top
        support_half = target_half.copy()
        support_center = target_center.copy()
        if relation == "inside":
            if "sink" in str(target_placement.get("name") or "").lower():
                target_support_top = max(float(target_low[2] + 0.03), counter_top - 0.16)
                support_half = np.array([target_half[0] * 0.72, target_half[1] * 0.72, 0.015])
            else:
                target_support_top = max(counter_top + 0.03, float(target_high[2] - min(0.12, target_half[2])))
                support_half = np.array([target_half[0] * 0.72, target_half[1] * 0.72, 0.012])
            support_center = np.array([target_center[0], target_center[1], target_support_top - support_half[2]])
        target_body = ET.SubElement(
            worldbody,
            "body",
            {"name": "target_support", "pos": " ".join(f"{value:.12g}" for value in support_center)},
        )
        ET.SubElement(
            target_body,
            "geom",
            {
                "name": "target_support_collision",
                "type": "box",
                "size": " ".join(f"{value:.12g}" for value in support_half),
                "rgba": "0.18 0.20 0.24 0.35",
                "friction": "0.8 0.01 0.001",
            },
        )
        if relation == "inside":
            wall_thickness = min(0.012, float(min(support_half[0], support_half[1]) * 0.12))
            wall_half_height = max(0.025, float((target_top - target_support_top) / 2))
            wall_z = float(support_half[2] + wall_half_height)
            wall_specs = (
                ("target_wall_left", [-support_half[0] + wall_thickness, 0.0, wall_z], [wall_thickness, support_half[1], wall_half_height]),
                ("target_wall_right", [support_half[0] - wall_thickness, 0.0, wall_z], [wall_thickness, support_half[1], wall_half_height]),
                ("target_wall_front", [0.0, -support_half[1] + wall_thickness, wall_z], [support_half[0], wall_thickness, wall_half_height]),
                ("target_wall_back", [0.0, support_half[1] - wall_thickness, wall_z], [support_half[0], wall_thickness, wall_half_height]),
            )
            for wall_name, wall_pos, wall_size in wall_specs:
                ET.SubElement(
                    target_body,
                    "geom",
                    {
                        "name": wall_name,
                        "type": "box",
                        "pos": " ".join(f"{value:.12g}" for value in wall_pos),
                        "size": " ".join(f"{value:.12g}" for value in wall_size),
                        "rgba": "0.18 0.20 0.24 0.22",
                        "friction": "0.8 0.01 0.001",
                    },
                )
        target_radius = float(max(0.025, min(support_half[0], support_half[1]) * (0.90 if relation == "inside" else 0.82)))
        ET.SubElement(
            worldbody,
            "geom",
            {
                "name": "target_marker",
                "type": "cylinder",
                "pos": f"{target_center[0]:.12g} {target_center[1]:.12g} {target_support_top + 0.002:.12g}",
                "size": f"{target_radius:.12g} 0.002",
                "rgba": "0.12 0.72 0.36 0.6",
                "contype": "0",
                "conaffinity": "0",
            },
        )
        target_volumes = [{
            "id": "authored_target_top",
            "shape": "box" if relation == "inside" else "cylinder",
            "centerM": [float(target_center[0]), float(target_center[1]), target_top],
            "radiusM": target_radius,
            "halfExtentsM": [float(support_half[0]), float(support_half[1])],
            "supportTopM": target_support_top,
            "supportBody": "target_support",
            "assetId": target_placement["assetId"],
            "relation": relation,
        }]
    front = next((camera for camera in worldbody.findall("camera") if camera.get("name") == "front"), None)
    if front is not None:
        front.set("pos", "1.55 -1.65 2.05")
        front.set("xyaxes", "0.729537 0.683942 0 -0.318769 0.339941 0.884304")

    ET.indent(tree, space="  ")
    world_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(world_path, encoding="utf-8", xml_declaration=True)
    model = mujoco.MjModel.from_xml_path(str(world_path))
    data = mujoco.MjData(model)
    home = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    mujoco.mj_resetDataKeyframe(model, data, home)
    mujoco.mj_forward(model, data)
    if not np.isfinite(data.qpos).all():
        raise ValueError("Authored-world reset produced non-finite physics state.")

    template = dict(base)
    authored_grasp_contract.update({
        "gripperClosingAxisWorld": [0.0, 1.0, 0.0],
        "gripperClosingAxisIndex": 1,
        "mountAdjusted": True,
        "tabletopClearanceAdjustmentM": grasp_clearance_adjustment,
        "sourceFootprintHalfExtentsM": [
            float((source_high[0] - source_low[0]) / 2),
            float((source_high[1] - source_low[1]) / 2),
        ],
    })
    object_bounding_radius = float(authored_grasp_contract["localBoundingRadiusM"])
    drop_center = [
        float(source_center[0]),
        float(counter_high[1] + object_bounding_radius + 0.045),
        float(counter_top + max(0.18, float(authored_grasp_contract["placedGraspHeightM"]) + 0.12)),
    ]
    target_name = str(target_placement["name"]) if target_placement is not None else "outside the counter support polygon"
    collision_subset = [counter_placement["assetId"], source_placement["assetId"]]
    if target_placement is not None:
        collision_subset.append(target_placement["assetId"])
    template.update({
        "id": f"{family}:{world_id}:{asset_version['id']}",
        "revision": 1,
        "name": f"{world_id} · {source_placement['name']} → {target_name}",
        "taskKind": task_kind,
        "relation": relation,
        "runtimePath": str(world_path),
        "runtimeSha256": _sha256(world_path),
        "authoredWorldId": world_id,
        "robotSpawnAnchors": [{"id": "franka_counter_mount", "baseLink": "link0", "pose": [*spawn.tolist(), *spawn_quaternion.tolist()]}],
        "workspaceSafetyBoundsM": [
            [float(counter_center[0] - counter_half[0]), float(counter_center[0] + counter_half[0])],
            [float(counter_center[1] - counter_half[1] - 0.25), float(counter_center[1] + counter_half[1] + 0.25)],
            [0.04, float(counter_top + 0.9)],
        ],
        "supportSurfaces": [{
            "id": "authored_counter",
            "semantic": "counter",
            "centerM": counter_center.tolist(),
            "halfExtentsM": counter_half.tolist(),
            "topM": counter_top,
            "assetId": counter_placement["assetId"],
        }],
        "targetVolumes": target_volumes,
        "dropRegions": [{
            "id": "outside_authored_counter_front_edge",
            "releaseCenterM": drop_center,
            "counterBoundsM": [counter_low.tolist(), counter_high.tolist()],
            "minimumClearanceM": 0.045,
            "predicate": "released_and_settled_below_counter_top_outside_counter_support_polygon",
        }] if task_kind == "drop_off_table" else [],
        "placements": [{
            "assetVersionId": asset_version["id"],
            "assetId": source_placement["assetId"],
            "authoredObjectPositionM": authored_object_position.tolist(),
            "visualWorldBoundsM": [source_low.tolist(), source_high.tolist()],
            "supportSurfaceId": "authored_counter",
            "accepted": True,
        }],
        "graspContract": authored_grasp_contract,
        "source": {
            "type": "active_authored_world_collision_subset",
            "worldId": world_id,
            "sourceAssetId": source_placement["assetId"],
            "targetAssetId": target_placement["assetId"] if target_placement is not None else None,
            "omittedVisualOnlyAssets": True,
            "targetCollisionPolicy": "measured_aabb_container_proxy" if relation == "inside" else "measured_aabb_support_proxy",
            "frictionSample": {"value": authored_friction, "method": "upper_evidence_range_for_grasp_validation"},
        },
        "validation": dict(base.get("validation") or {}) | {
            "loads": True,
            "finiteReset": True,
            "executableCollisionSubset": collision_subset,
            "robotSpawnConsumed": True,
        },
    })
    (root / "template.json").write_text(json.dumps(template, indent=2), encoding="utf8")
    return template


class MujocoFrankaBackend(SimulationBackend):
    def __init__(self, artifact: Path | None = None):
        self.model: mujoco.MjModel | None = None
        self.data: mujoco.MjData | None = None
        self._renderers: dict[tuple[int, int], mujoco.Renderer] = {}
        self._seed = 0
        if artifact is not None:
            self.load_world(artifact)

    def load_world(self, artifact: Path) -> None:
        self.close()
        self.model = mujoco.MjModel.from_xml_path(str(artifact))
        self.data = mujoco.MjData(self.model)
        self.ee_site = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "franka_ee")
        self.asset_grasp_site = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "compiled_asset_grasp")
        self.object_body = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "pick_object")
        self.hand_body = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "hand")
        self.finger_bodies = {
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "left_finger"),
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "right_finger"),
        }
        self.arm_joints = [mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, f"joint{index}") for index in range(1, 8)]
        self.arm_qpos = [int(self.model.jnt_qposadr[index]) for index in self.arm_joints]
        self.arm_dofs = [int(self.model.jnt_dofadr[index]) for index in self.arm_joints]
        self.object_qpos = int(self.model.jnt_qposadr[mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "pick_object_free")])
        self.initial_object_qpos = self.model.qpos0[self.object_qpos : self.object_qpos + 7].copy()
        if min([self.ee_site, self.object_body, self.hand_body, *self.finger_bodies, *self.arm_joints]) < 0:
            raise ValueError("World does not satisfy the Franka pick/place backend contract.")

    def _require(self) -> tuple[mujoco.MjModel, mujoco.MjData]:
        if self.model is None or self.data is None:
            raise RuntimeError("No simulation world is loaded.")
        return self.model, self.data

    def reset(self, seed: int) -> dict[str, Any]:
        model, data = self._require()
        self._seed = int(seed)
        home = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
        mujoco.mj_resetDataKeyframe(model, data, home)
        data.qpos[self.object_qpos : self.object_qpos + 7] = self.initial_object_qpos
        data.qvel[:] = 0
        mujoco.mj_forward(model, data)
        return self.state()

    def step(self, substeps: int = 1) -> dict[str, Any]:
        model, data = self._require()
        for _ in range(max(1, int(substeps))):
            mujoco.mj_step(model, data)
        return self.state()

    def apply_action(self, action: np.ndarray) -> None:
        model, data = self._require()
        value = np.asarray(action, dtype=np.float64)
        if value.shape != (model.nu,) or not np.isfinite(value).all():
            raise ValueError(f"Expected a finite actuator action with shape ({model.nu},).")
        data.ctrl[:] = np.clip(value, model.actuator_ctrlrange[:, 0], model.actuator_ctrlrange[:, 1])

    def state(self) -> dict[str, Any]:
        model, data = self._require()
        ee_quaternion = np.empty(4, dtype=np.float64)
        mujoco.mju_mat2Quat(ee_quaternion, data.site_xmat[self.ee_site])
        state = {
            "timeSeconds": float(data.time),
            "seed": self._seed,
            "jointPosition": [float(data.qpos[index]) for index in self.arm_qpos],
            "jointVelocity": [float(data.qvel[index]) for index in self.arm_dofs],
            "gripperWidthM": float(data.qpos[7] + data.qpos[8]),
            "endEffectorPositionM": [float(value) for value in data.site_xpos[self.ee_site]],
            "endEffectorQuaternionWxyz": [float(value) for value in ee_quaternion],
            "objectPositionM": [float(value) for value in data.xpos[self.object_body]],
            "objectQuaternionWxyz": [float(value) for value in data.xquat[self.object_body]],
            "objectVelocityMps": [float(value) for value in data.cvel[self.object_body, 3:]],
            "objectAngularVelocityRadS": [float(value) for value in data.cvel[self.object_body, :3]],
            "contactCount": int(data.ncon),
            "finite": bool(np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all()),
        }
        if self.asset_grasp_site >= 0:
            state["objectGraspPositionM"] = [float(value) for value in data.site_xpos[self.asset_grasp_site]]
        state["renderGeometries"] = render_geometries(model, data)
        return state

    def contacts(self) -> list[ContactEvent]:
        model, data = self._require()
        out: list[ContactEvent] = []
        for index in range(data.ncon):
            contact = data.contact[index]
            body_a = int(model.geom_bodyid[contact.geom1])
            body_b = int(model.geom_bodyid[contact.geom2])
            force = np.zeros(6)
            mujoco.mj_contactForce(model, data, index, force)
            out.append(
                ContactEvent(
                    body_a=str(mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_a) or "world"),
                    body_b=str(mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_b) or "world"),
                    distance_m=float(contact.dist),
                    normal_force_n=float(force[0]),
                )
            )
        return out

    def render_rgb(self, camera: str, width: int = 256, height: int = 256) -> np.ndarray:
        model, data = self._require()
        if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, camera) < 0:
            raise ValueError(f"Unknown camera '{camera}'.")
        key = (width, height)
        renderer = self._renderers.get(key)
        if renderer is None:
            renderer = mujoco.Renderer(model, height=height, width=width)
            self._renderers[key] = renderer
        renderer.update_scene(data, camera=camera)
        return renderer.render().copy()

    def close(self) -> None:
        for renderer in self._renderers.values():
            renderer.close()
        self._renderers.clear()
        self.model = None
        self.data = None


def render_geometries(model: mujoco.MjModel, data: mujoco.MjData) -> list[dict[str, Any]]:
    """Return browser geometry at MuJoCo-computed poses.

    The mesh vertices are served from MuJoCo's compiled mesh buffers by the
    API.  These poses therefore compose with the same geometry MuJoCo uses,
    rather than with the differently centered source OBJ files.
    """

    render_geometries: list[dict[str, Any]] = []
    for geom_id in range(model.ngeom):
        # Group 3 is collision-only in the pinned Menagerie Panda. The
        # browser mirrors visual geometry and explicit world colliders;
        # it never advances or invents their transforms.
        if int(model.geom_group[geom_id]) == 3:
            continue
        geom_type = int(model.geom_type[geom_id])
        kind = {
            int(mujoco.mjtGeom.mjGEOM_PLANE): "plane",
            int(mujoco.mjtGeom.mjGEOM_SPHERE): "sphere",
            int(mujoco.mjtGeom.mjGEOM_CAPSULE): "capsule",
            int(mujoco.mjtGeom.mjGEOM_ELLIPSOID): "ellipsoid",
            int(mujoco.mjtGeom.mjGEOM_CYLINDER): "cylinder",
            int(mujoco.mjtGeom.mjGEOM_BOX): "box",
            int(mujoco.mjtGeom.mjGEOM_MESH): "mesh",
        }.get(geom_type, "unknown")
        data_id = int(model.geom_dataid[geom_id])
        mesh_name = (
            str(mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_MESH, data_id) or "")
            if geom_type == int(mujoco.mjtGeom.mjGEOM_MESH) and data_id >= 0
            else ""
        )
        quat = np.empty(4, dtype=np.float64)
        mujoco.mju_mat2Quat(quat, data.geom_xmat[geom_id])
        body_id = int(model.geom_bodyid[geom_id])
        render_geometries.append({
            "id": f"geom-{geom_id}",
            "name": str(mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_id) or mesh_name or f"geom-{geom_id}"),
            "kind": kind,
            "meshName": mesh_name or None,
            "size": [float(value) for value in model.geom_size[geom_id]],
            "rgba": [float(value) for value in model.geom_rgba[geom_id]],
            "positionM": [float(value) for value in data.geom_xpos[geom_id]],
            "quaternionWxyz": [float(value) for value in quat],
            "bodyPositionM": [float(value) for value in data.xpos[body_id]],
            "bodyQuaternionWxyz": [float(value) for value in data.xquat[body_id]],
        })
    return render_geometries


def authoring_robot_preview(
    robot_id: str,
    spawn_xyz: list[float],
    spawn_quaternion_wxyz: list[float] | None = None,
) -> dict[str, Any]:
    """Evaluate a Franka home pose at the active world's authored mount.

    This is intentionally a reset-pose authoring preview, not a simulated
    rollout.  The exact same registered MJCF and compiled geometry are used by
    the live backend, so the editor does not draw a decorative robot proxy.
    """

    if len(spawn_xyz) != 3 or not np.isfinite(np.asarray(spawn_xyz, dtype=float)).all():
        raise ValueError("Robot spawn must contain three finite metre coordinates.")
    runtime, robot = _safe_robot_manifest(robot_id)
    tree = ET.parse(runtime)
    worldbody = tree.getroot().find("worldbody")
    if worldbody is None:
        raise ValueError("Robot runtime has no worldbody.")
    root_body = next((body for body in worldbody.findall("body") if body.get("name") == "link0"), None)
    if root_body is None:
        raise ValueError("Robot runtime has no fixed link0 root.")
    root_body.set("pos", " ".join(f"{float(value):.12g}" for value in spawn_xyz))
    quaternion = spawn_quaternion_wxyz or [1.0, 0.0, 0.0, 0.0]
    if len(quaternion) != 4 or not np.isfinite(np.asarray(quaternion, dtype=float)).all():
        raise ValueError("Robot spawn quaternion must contain four finite WXYZ values.")
    root_body.set("quat", " ".join(f"{float(value):.12g}" for value in quaternion))
    for body in list(worldbody.findall("body")):
        if body.get("name") in {"workspace_calibration", "calibration_target"}:
            worldbody.remove(body)
    model = mujoco.MjModel.from_xml_string(ET.tostring(tree.getroot(), encoding="unicode"))
    data = mujoco.MjData(model)
    home = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    if home < 0:
        raise ValueError("Registered Franka runtime has no deterministic home keyframe.")
    mujoco.mj_resetDataKeyframe(model, data, home)
    mujoco.mj_forward(model, data)
    values = render_geometries(model, data)
    robot_values = [item for item in values if item["kind"] == "mesh"]
    return {
        "schemaVersion": "robotworld.authoring-robot-preview.v1",
        "robotId": robot_id,
        "robotRuntimeSha256": robot["runtimeSha256"],
        "spawnPositionM": [float(value) for value in spawn_xyz],
        "spawnQuaternionWxyz": [float(value) for value in quaternion],
        "poseSource": "mujoco_home_keyframe_forward_kinematics",
        "authoritativeForExecution": False,
        "geometries": robot_values,
    }


@dataclass
class OracleResult:
    success: bool
    failure_code: str | None
    failure_detail: str | None
    duration_s: float
    phases: list[dict[str, Any]]
    trajectory: list[dict[str, Any]]
    contact_summary: dict[str, Any]
    predicate: dict[str, Any]
    frames: dict[str, dict[str, str]]


class PickPlaceOracle:
    def __init__(
        self,
        backend: MujocoFrankaBackend,
        artifact_dir: Path,
        *,
        record_observations: bool = False,
        live_frame_callback: Callable[[dict[str, Any], np.ndarray, np.ndarray], None] | None = None,
        realtime: bool = False,
    ):
        self.backend = backend
        self.model, self.data = backend._require()
        self.artifact_dir = artifact_dir
        self.desired_rotation = np.eye(3)
        self.trajectory: list[dict[str, Any]] = []
        self.phases: list[dict[str, Any]] = []
        self.contact_pairs: dict[str, int] = {}
        self.frames: dict[str, dict[str, str]] = {}
        self.record_observations = bool(record_observations)
        self.observation_dir = self.artifact_dir / "demonstration_frames"
        self.live_frame_callback = live_frame_callback
        self.realtime = bool(realtime)
        self._live_last_sim_time: float | None = None
        self._live_sim_origin: float | None = None
        self._live_wall_origin: float | None = None

    def _record(self, phase: str) -> None:
        state = self.backend.state()
        state["phase"] = phase
        if self.record_observations:
            self.observation_dir.mkdir(parents=True, exist_ok=True)
            frame_index = len(self.trajectory)
            observations: dict[str, dict[str, str]] = {}
            for camera in ("front", "wrist"):
                path = self.observation_dir / f"frame-{frame_index:06d}-{camera}.png"
                Image.fromarray(self.backend.render_rgb(camera, width=224, height=224), mode="RGB").save(
                    path,
                    format="PNG",
                    optimize=False,
                )
                observations[camera] = {
                    "path": path.relative_to(self.artifact_dir).as_posix(),
                    "sha256": _sha256(path),
                }
            state["observationFrames"] = observations
        contacts = self.backend.contacts()
        state["objectContacts"] = [
            {
                "bodyA": contact.body_a,
                "bodyB": contact.body_b,
                "distanceM": contact.distance_m,
                "normalForceN": contact.normal_force_n,
            }
            for contact in contacts
            if "pick_object" in {contact.body_a, contact.body_b}
        ][:16]
        # Viewer geometry is derived from the compiled MuJoCo model and is
        # needed by the live WebSocket frame, but repeating the complete
        # geometry list in every durable trajectory sample inflated one live
        # evaluation to tens of megabytes.  Keep the authoritative body/joint/
        # contact state in the database and stream geometry only at the live
        # boundary; the immutable evaluation artifact remains the source for
        # recorded phase images.
        self.trajectory.append({key: value for key, value in state.items() if key != "renderGeometries"})
        for contact in contacts:
            pair = "|".join(sorted((contact.body_a, contact.body_b)))
            self.contact_pairs[pair] = self.contact_pairs.get(pair, 0) + 1
        if self.live_frame_callback is not None:
            sim_time = float(state["timeSeconds"])
            # Control samples arrive at 20-50 Hz depending on the current
            # phase. Cap rendering at 25 Hz while preserving simulator time.
            should_render = self._live_last_sim_time is None or sim_time - self._live_last_sim_time >= 0.039
            if should_render:
                if self._live_sim_origin is None:
                    self._live_sim_origin = sim_time
                    self._live_wall_origin = time.perf_counter()
                if self.realtime and self._live_wall_origin is not None:
                    target_wall = self._live_wall_origin + (sim_time - self._live_sim_origin)
                    delay = target_wall - time.perf_counter()
                    if delay > 0:
                        time.sleep(delay)
                front = self.backend.render_rgb("front", width=640, height=360)
                wrist = self.backend.render_rgb("wrist", width=256, height=144)
                self.live_frame_callback(dict(state), front, wrist)
                self._live_last_sim_time = sim_time

    def _capture(self, phase: str) -> None:
        phase_dir = self.artifact_dir / "frames"
        phase_dir.mkdir(parents=True, exist_ok=True)
        self.frames[phase] = {}
        for camera in ("front", "wrist"):
            frame = self.backend.render_rgb(camera)
            path = phase_dir / f"{phase}-{camera}.png"
            Image.fromarray(frame, mode="RGB").save(path, format="PNG", optimize=False)
            self.frames[phase][camera] = _sha256(path)

    @staticmethod
    def _orientation_error(current: np.ndarray, target: np.ndarray) -> np.ndarray:
        return 0.5 * sum((np.cross(current[:, index], target[:, index]) for index in range(3)), start=np.zeros(3))

    def _move(
        self,
        target_position: np.ndarray,
        phase: str,
        max_ticks: int = 180,
        *,
        tracked_site: int | None = None,
        stop_on_support_contact: bool = False,
        support_body_name: str = "workspace_calibration",
        max_joint_step: float = 0.10,
        position_tolerance_m: float = 0.007,
        rotation_tolerance_rad: float = 0.08,
        rotation_gain: float = 0.45,
        axis_tolerance: tuple[int, float] | None = None,
    ) -> bool:
        jacp = np.zeros((3, self.model.nv))
        jacr = np.zeros((3, self.model.nv))
        reached = False
        stop_reason = "max_ticks"
        for tick in range(max_ticks):
            position_site = self.backend.ee_site if tracked_site is None else tracked_site
            current_position = self.data.site_xpos[position_site].copy()
            current_rotation = self.data.site_xmat[self.backend.ee_site].reshape(3, 3).copy()
            position_error = target_position - current_position
            rotation_error = self._orientation_error(current_rotation, self.desired_rotation)
            if stop_on_support_contact:
                support_contact = any(
                    "pick_object" in {contact.body_a, contact.body_b}
                    and support_body_name in {contact.body_a, contact.body_b}
                    for contact in self.backend.contacts()
                )
                if support_contact and float(np.linalg.norm(position_error[:2])) < 0.02:
                    reached = True
                    stop_reason = "support_contact_within_xy_tolerance"
                    break
            axis_reached = axis_tolerance is None or abs(float(position_error[axis_tolerance[0]])) < axis_tolerance[1]
            if (
                np.linalg.norm(position_error) < position_tolerance_m
                and axis_reached
                and np.linalg.norm(rotation_error) < rotation_tolerance_rad
            ):
                reached = True
                stop_reason = "pose_tolerance"
                break
            mujoco.mj_jacSite(self.model, self.data, jacp, jacr, self.backend.ee_site)
            jacobian = np.vstack((jacp[:, self.backend.arm_dofs], jacr[:, self.backend.arm_dofs]))
            error = np.concatenate((position_error * 1.1, rotation_error * rotation_gain))
            lhs = jacobian @ jacobian.T + np.eye(6) * 0.004
            delta = jacobian.T @ np.linalg.solve(lhs, error)
            norm = float(np.linalg.norm(delta))
            if norm > max_joint_step:
                delta *= max_joint_step / norm
            qpos = np.array([self.data.qpos[index] for index in self.backend.arm_qpos])
            target_qpos = qpos + delta
            target_qpos = np.clip(target_qpos, self.model.jnt_range[self.backend.arm_joints, 0] + 0.01, self.model.jnt_range[self.backend.arm_joints, 1] - 0.01)
            action = self.data.ctrl.copy()
            action[:7] = target_qpos
            self.backend.apply_action(action)
            self.backend.step(PHYSICS_HZ // CONTROL_HZ)
            if self.live_frame_callback is not None or tick % 2 == 0:
                self._record(phase)
            if not self.backend.state()["finite"]:
                return False
        position_site = self.backend.ee_site if tracked_site is None else tracked_site
        self.phases.append(
            {
                "phase": phase,
                "reached": reached,
                "ticks": tick + 1,
                "targetM": [float(value) for value in target_position],
                "trackedSite": "franka_ee" if tracked_site is None else "compiled_asset_grasp",
                "stopReason": stop_reason,
                "positionToleranceM": position_tolerance_m,
                "rotationToleranceRad": rotation_tolerance_rad,
                "rotationGain": rotation_gain,
                "axisTolerance": list(axis_tolerance) if axis_tolerance is not None else None,
                "finalErrorM": float(np.linalg.norm(target_position - self.data.site_xpos[position_site])),
            }
        )
        self._capture(phase)
        return reached

    def _gripper(self, control: float, phase: str, steps: int = 220) -> None:
        action = self.data.ctrl.copy()
        action[-1] = control
        self.backend.apply_action(action)
        for index in range(steps):
            self.backend.step()
            if index % 10 == 0:
                self._record(phase)
        self.phases.append({"phase": phase, "control": control, "steps": steps, "widthM": self.backend.state()["gripperWidthM"]})
        self._capture(phase)

    def _align_axis(
        self,
        target_position: np.ndarray,
        axis_index: int,
        phase: str,
        *,
        tolerance_m: float = 0.001,
        max_ticks: int = 100,
    ) -> bool:
        """Compensate repeatable Cartesian servo bias along the jaw axis."""
        reached = False
        attempts: list[dict[str, Any]] = []
        for attempt in range(3):
            current_position = self.data.site_xpos[self.backend.ee_site].copy()
            axis_error = float(target_position[axis_index] - current_position[axis_index])
            if abs(axis_error) < tolerance_m:
                reached = True
                break
            compensated_target = target_position.copy()
            compensated_target[axis_index] += axis_error
            moved = self._move(
                compensated_target,
                f"{phase}_correction_{attempt + 1}",
                max_ticks=max_ticks,
                axis_tolerance=(axis_index, 0.0015),
            )
            final_axis_error = float(target_position[axis_index] - self.data.site_xpos[self.backend.ee_site, axis_index])
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "measuredBiasM": axis_error,
                    "commandedAxisM": float(compensated_target[axis_index]),
                    "moveReached": moved,
                    "resultAxisErrorM": final_axis_error,
                }
            )
            if abs(final_axis_error) < tolerance_m:
                reached = True
                break
        final_error = float(target_position[axis_index] - self.data.site_xpos[self.backend.ee_site, axis_index])
        self.phases.append(
            {
                "phase": phase,
                "reached": reached,
                "attempts": attempts,
                "axisIndex": axis_index,
                "toleranceM": tolerance_m,
                "finalAxisErrorM": final_error,
            }
        )
        self._capture(phase)
        return reached

    def run(self, seed: int = 0) -> OracleResult:
        started = time.perf_counter()
        self.backend.reset(seed)
        self.backend.step(150)
        self.desired_rotation = self.data.site_xmat[self.backend.ee_site].reshape(3, 3).copy()
        initial_object = self.data.xpos[self.backend.object_body].copy()
        self._record("reset")
        self._capture("reset")
        failure: tuple[str, str] | None = None
        pregrasp = initial_object + np.array([0.0, 0.0, 0.15])
        grasp = initial_object + np.array([0.0, 0.0, 0.005])
        if not self._move(pregrasp, "pre_grasp"):
            failure = ("unreachable_target", "Differential IK did not reach the pre-grasp waypoint.")
        elif not self._move(grasp, "grasp_approach"):
            failure = ("pre_grasp_collision", "Differential IK did not reach the grasp waypoint.")
        else:
            self._gripper(0.0, "close_gripper")
            object_contacts = [
                contact for contact in self.backend.contacts()
                if "pick_object" in {contact.body_a, contact.body_b}
                and ({contact.body_a, contact.body_b} & {"left_finger", "right_finger"})
            ]
            if not object_contacts:
                failure = ("grasp_miss", "No gripper/object contact was present after closure.")
            elif not self._move(initial_object + np.array([0.0, 0.0, 0.17]), "lift"):
                failure = ("object_dropped", "The lift waypoint was not reached.")
            elif self.data.xpos[self.backend.object_body, 2] < initial_object[2] + 0.08:
                failure = ("grasp_slip", "The gripper moved upward but the object did not lift by 8 cm.")
            elif not self._move(np.array([TARGET_XY[0], TARGET_XY[1], 0.45]), "transport"):
                failure = ("policy_timeout", "The transport waypoint was not reached.")
            elif not self._move(np.array([TARGET_XY[0], TARGET_XY[1], 0.325]), "place"):
                failure = ("pre_grasp_collision", "The placement waypoint was not reached.")
            else:
                self._gripper(255.0, "release")
                self._move(np.array([TARGET_XY[0], TARGET_XY[1], 0.45]), "retract", max_ticks=100)
                for index in range(500):
                    self.backend.step()
                    if index % 25 == 0:
                        self._record("settle")
                self._capture("settle")
        final_position = self.data.xpos[self.backend.object_body].copy()
        final_speed = float(np.linalg.norm(self.data.cvel[self.backend.object_body, 3:]))
        target_error = float(np.linalg.norm(final_position[:2] - TARGET_XY))
        settled = bool(final_speed < 0.05)
        on_surface = bool(abs((final_position[2] - OBJECT_HALF_SIZE_M) - TABLE_TOP_Z) < 0.02)
        contained = bool(target_error <= TARGET_RADIUS_M - OBJECT_HALF_SIZE_M * 0.25)
        success = bool(failure is None and contained and on_surface and settled)
        if failure is None and not success:
            failure = ("success_predicate_failure", f"Final target error={target_error:.4f} m, z={final_position[2]:.4f} m, speed={final_speed:.4f} m/s.")
        predicate = {
            "contained": contained,
            "onSupportSurface": on_surface,
            "settled": settled,
            "targetErrorM": target_error,
            "finalObjectPositionM": [float(value) for value in final_position],
            "finalSpeedMps": final_speed,
            "targetRadiusM": TARGET_RADIUS_M,
        }
        return OracleResult(
            success=success,
            failure_code=failure[0] if failure else None,
            failure_detail=failure[1] if failure else None,
            duration_s=time.perf_counter() - started,
            phases=self.phases,
            trajectory=self.trajectory,
            contact_summary={"sampledPairs": self.contact_pairs, "samples": sum(self.contact_pairs.values())},
            predicate=predicate,
            frames=self.frames,
        )


class CompiledAssetPickPlaceOracle(PickPlaceOracle):
    """Deterministic contact oracle for a compiler-authored rigid asset."""

    def __init__(
        self,
        backend: MujocoFrankaBackend,
        artifact_dir: Path,
        template: dict[str, Any],
        *,
        record_observations: bool = False,
        live_frame_callback: Callable[[dict[str, Any], np.ndarray, np.ndarray], None] | None = None,
        realtime: bool = False,
    ):
        super().__init__(
            backend,
            artifact_dir,
            record_observations=record_observations,
            live_frame_callback=live_frame_callback,
            realtime=realtime,
        )
        self.template = template
        self.grasp_site = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "compiled_asset_grasp")
        if self.grasp_site < 0:
            raise ValueError("Compiled-asset world is missing its candidate grasp site.")

    def run(self, seed: int = 0) -> OracleResult:
        started = time.perf_counter()
        self.backend.reset(seed)
        self.backend.step(250)
        self.desired_rotation = self.data.site_xmat[self.backend.ee_site].reshape(3, 3).copy()
        initial_grasp = self.data.site_xpos[self.grasp_site].copy()
        self._record("reset")
        self._capture("reset")
        failure: tuple[str, str] | None = None
        settle_positions: list[np.ndarray] = []
        settle_quaternions: list[np.ndarray] = []
        settle_linear_speeds: list[float] = []
        settle_angular_speeds: list[float] = []
        settle_steps = 0
        grasp_contract = self.template["graspContract"]
        required_width = float(grasp_contract["requiredGripperWidthM"])
        actuator_id = self.model.nu - 1
        configured_open_width = 0.08
        if required_width > configured_open_width - 0.003:
            failure = (
                "unreachable_target",
                f"Asset grasp cross-section {required_width:.4f} m exceeds the validated Franka opening with 3 mm clearance.",
            )
            self.phases.append(
                {
                    "phase": "reachability_check",
                    "reached": False,
                    "requiredGripperWidthM": required_width,
                    "availableGripperWidthM": configured_open_width,
                }
            )
        else:
            # Make the explicit open command part of the episode instead of
            # relying on whichever actuator value happens to be in a keyframe.
            action = self.data.ctrl.copy()
            action[actuator_id] = 255.0
            self.backend.apply_action(action)
            self.backend.step(120)
            pregrasp = initial_grasp + np.array([0.0, 0.0, 0.14])
            grasp = initial_grasp + np.array([0.0, 0.0, 0.004])
            if not self._move(pregrasp, "pre_grasp", max_ticks=220):
                failure = ("unreachable_target", "Differential IK did not reach the compiled asset pre-grasp waypoint.")
            elif not self._move(grasp, "grasp_approach", max_ticks=220):
                failure = ("pre_grasp_collision", "Differential IK did not reach the compiled asset grasp frame.")
            elif not self._align_axis(
                grasp,
                int(grasp_contract["gripperClosingAxisIndex"]),
                "grasp_axis_alignment",
            ):
                failure = ("pre_grasp_collision", "Fine jaw-axis alignment did not converge at the compiled grasp frame.")
            else:
                self._gripper(0.0, "close_gripper", steps=300)
                object_contacts = [
                    contact
                    for contact in self.backend.contacts()
                    if "pick_object" in {contact.body_a, contact.body_b}
                    and ({contact.body_a, contact.body_b} & {"left_finger", "right_finger"})
                ]
                contacting_fingers = {
                    finger
                    for contact in object_contacts
                    for finger in ({contact.body_a, contact.body_b} & {"left_finger", "right_finger"})
                }
                if contacting_fingers != {"left_finger", "right_finger"}:
                    failure = (
                        "grasp_miss",
                        f"Bilateral gripper contact was not established; contacting fingers={sorted(contacting_fingers)}.",
                    )
                elif not self._move(
                    initial_grasp + np.array([0.0, 0.0, 0.17]),
                    "lift",
                    max_ticks=220,
                    tracked_site=self.grasp_site,
                ):
                    failure = ("object_dropped", "The deterministic controller did not reach the lift waypoint.")
                elif self.data.site_xpos[self.grasp_site, 2] < initial_grasp[2] + 0.08:
                    failure = ("grasp_slip", "Gripper motion did not lift the compiler-authored body by 8 cm.")
                else:
                    target = self.template["targetVolumes"][0]
                    target_xy = np.asarray(target["centerM"][:2], dtype=float)
                    support_top = float(target.get("supportTopM", TABLE_TOP_Z))
                    support_body = str(target.get("supportBody", "workspace_calibration"))
                    height = float(self.template["graspContract"]["placedObjectHeightM"])
                    placed_grasp_height = float(self.template["graspContract"]["placedGraspHeightM"])
                    if not self._move(
                        np.array([target_xy[0], target_xy[1], support_top + height + 0.12]),
                        "transport",
                        max_ticks=240,
                        tracked_site=self.grasp_site,
                    ):
                        failure = ("policy_timeout", "The deterministic controller did not reach the transport waypoint.")
                    elif not self._move(
                        np.array([target_xy[0], target_xy[1], support_top + placed_grasp_height + 0.012]),
                        "place",
                        max_ticks=220,
                        tracked_site=self.grasp_site,
                        stop_on_support_contact=True,
                        support_body_name=support_body,
                    ):
                        failure = ("pre_grasp_collision", "The deterministic controller did not reach the placement waypoint.")
                    else:
                        self._gripper(255.0, "release", steps=260)
                        self._move(np.array([target_xy[0], target_xy[1], support_top + height + 0.12]), "retract", max_ticks=140)
                        for index in range(6000):
                            self.backend.step()
                            settle_positions.append(self.data.site_xpos[self.grasp_site].copy())
                            settle_quaternions.append(self.data.xquat[self.backend.object_body].copy())
                            settle_linear_speeds.append(float(np.linalg.norm(self.data.cvel[self.backend.object_body, 3:])))
                            settle_angular_speeds.append(float(np.linalg.norm(self.data.cvel[self.backend.object_body, :3])))
                            record_interval = 20 if self.live_frame_callback is not None else 100
                            if index % record_interval == 0:
                                self._record("settle")
                            settle_steps = index + 1
                            if settle_steps >= 3000 and settle_steps % 25 == 0:
                                position_window = np.asarray(settle_positions[-375:])
                                position_span = float(np.max(np.ptp(position_window, axis=0)))
                                rotation_span = _quaternion_rotation_span(settle_quaternions[-375:])
                                linear_p95 = float(np.quantile(settle_linear_speeds[-375:], 0.95))
                                angular_p95 = float(np.quantile(settle_angular_speeds[-375:], 0.95))
                                angular_velocity_stable = angular_p95 < 0.15 and settle_angular_speeds[-1] < 0.05
                                if (
                                    position_span < 0.003
                                    and linear_p95 < 0.02
                                    and settle_linear_speeds[-1] < 0.01
                                    and (angular_velocity_stable or rotation_span < 0.01)
                                ):
                                    break
                        self._capture("settle")

        final_position = self.data.xpos[self.backend.object_body].copy()
        final_grasp_position = self.data.site_xpos[self.grasp_site].copy()
        final_linear_speed = float(np.linalg.norm(self.data.cvel[self.backend.object_body, 3:]))
        final_angular_speed = float(np.linalg.norm(self.data.cvel[self.backend.object_body, :3]))
        settle_window = np.asarray(settle_positions[-375:]) if settle_positions else None
        settle_span = float(np.max(np.ptp(settle_window, axis=0))) if settle_window is not None else None
        settle_linear_p95 = float(np.quantile(settle_linear_speeds[-375:], 0.95)) if settle_linear_speeds else None
        settle_angular_p95 = float(np.quantile(settle_angular_speeds[-375:], 0.95)) if settle_angular_speeds else None
        settle_rotation_span = _quaternion_rotation_span(settle_quaternions[-375:]) if settle_quaternions else None
        target = self.template["targetVolumes"][0]
        target_xy = np.asarray(target["centerM"][:2], dtype=float)
        support_body = str(target.get("supportBody", "workspace_calibration"))
        target_error = float(np.linalg.norm(final_grasp_position[:2] - target_xy))
        bounding_radius = float(self.template["graspContract"]["localBoundingRadiusM"])
        containment_policy = "full_object_inside_target_volume"
        if self.template.get("relation") == "on_top_of" and len(target.get("halfExtentsM") or []) == 2:
            # Stacking does not require the entire source footprint to fit
            # inside the target footprint (an apple may physically balance on
            # a slightly smaller orange).  It requires the object's centre of
            # mass to remain inside the measured support polygon, actual
            # source/target contact, release, and a stable settle window.
            half_extents = np.maximum(np.asarray(target["halfExtentsM"], dtype=float) - 0.002, 0.001)
            containment_residual = float(np.max(np.abs(final_position[:2] - target_xy) - half_extents))
            containment_policy = "center_of_mass_inside_support_polygon_with_2mm_margin"
        elif target.get("shape") == "box" and len(target.get("halfExtentsM") or []) == 2:
            half_extents = np.asarray(target["halfExtentsM"], dtype=float)
            containment_residual = float(np.max(np.abs(final_grasp_position[:2] - target_xy) + bounding_radius - half_extents))
        else:
            containment_residual = target_error + bounding_radius - float(target["radiusM"])
        final_contacts = self.backend.contacts()
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
        angular_velocity_gate = bool(
            settle_angular_p95 is not None
            and settle_angular_p95 < 0.15
            and final_angular_speed < 0.05
        )
        rotation_transform_gate = bool(settle_rotation_span is not None and settle_rotation_span < 0.01)
        settled = bool(
            settle_span is not None
            and settle_span < 0.003
            and settle_linear_p95 is not None
            and settle_linear_p95 < 0.02
            and final_linear_speed < 0.01
            and (angular_velocity_gate or rotation_transform_gate)
        )
        contained = bool(containment_residual <= 0.001)
        released = not finger_contact
        success = bool(failure is None and support_contact and settled and contained and released)
        if failure is None and not success:
            failure = (
                "success_predicate_failure",
                "targetError="
                f"{target_error:.4f} m, containmentResidual={containment_residual:.4f} m, "
                f"supportContact={support_contact}, linearSpeed={final_linear_speed:.4f} m/s, "
                f"angularSpeed={final_angular_speed:.4f} rad/s, rotationSpan={settle_rotation_span}, released={released}.",
            )
        if settle_span is not None:
            self.phases.append(
                {
                    "phase": "settle",
                    "steps": settle_steps,
                    "simulatedSeconds": settle_steps / PHYSICS_HZ,
                    "positionSpanM": settle_span,
                    "linearSpeedP95Mps": settle_linear_p95,
                    "angularSpeedP95RadS": settle_angular_p95,
                    "rotationSpanRad": settle_rotation_span,
                    "angularVelocityGatePassed": angular_velocity_gate,
                    "rotationTransformGatePassed": rotation_transform_gate,
                    "finalLinearSpeedMps": final_linear_speed,
                    "finalAngularSpeedRadS": final_angular_speed,
                }
            )
        predicate = {
            "assetVersionId": self.template["assetVersionId"],
            "assetManifestSha256": self.template["assetManifestSha256"],
            "contained": contained,
            "onSupportSurface": support_contact,
            "settled": settled,
            "released": released,
            "targetErrorM": target_error,
            "finalObjectPositionM": [float(value) for value in final_position],
            "finalObjectGraspPositionM": [float(value) for value in final_grasp_position],
            "finalLinearSpeedMps": final_linear_speed,
            "finalAngularSpeedRadS": final_angular_speed,
            "settlePositionSpanM": settle_span,
            "settleLinearSpeedP95Mps": settle_linear_p95,
            "settleAngularSpeedP95RadS": settle_angular_p95,
            "settleRotationSpanRad": settle_rotation_span,
            "angularVelocityGatePassed": angular_velocity_gate,
            "rotationTransformGatePassed": rotation_transform_gate,
            "settleSimulatedSeconds": settle_steps / PHYSICS_HZ if settle_steps else None,
            "targetRadiusM": float(target["radiusM"]),
            "targetHalfExtentsM": target.get("halfExtentsM"),
            "objectBoundingRadiusM": bounding_radius,
            "containmentResidualM": containment_residual,
            "containmentPolicy": containment_policy,
            "requiredGripperWidthM": required_width,
            "availableGripperWidthM": configured_open_width,
            "placementEvidence": self.template["placements"][0],
        }
        return OracleResult(
            success=success,
            failure_code=failure[0] if failure else None,
            failure_detail=failure[1] if failure else None,
            duration_s=time.perf_counter() - started,
            phases=self.phases,
            trajectory=self.trajectory,
            contact_summary={
                "sampledPairs": self.contact_pairs,
                "samples": sum(self.contact_pairs.values()),
                "finalSupportContact": support_contact,
                "finalFingerContact": finger_contact,
            },
            predicate=predicate,
            frames=self.frames,
        )


class AuthoredScenePickPlaceOracle(CompiledAssetPickPlaceOracle):
    """Compiled-asset oracle with an exact pre-contact IK approach.

    The generic validation bench deliberately retains its already-regressed
    differential-IK behavior. Generated kitchen geometry leaves only a few
    millimetres of clearance around the apple, so this authored-scene adapter
    solves the two free-space approach poses before tracking them through the
    same actuators and physics contacts.
    """

    def _move_solved_ik(
        self,
        target_position: np.ndarray,
        phase: str,
        *,
        max_ticks: int,
        position_tolerance_m: float,
        rotation_tolerance_rad: float,
        tracked_site: int | None = None,
    ) -> bool:
        from scipy.optimize import least_squares

        tracked_site_id = self.backend.ee_site if tracked_site is None else tracked_site
        tracked_offset = self.data.site_xpos[self.backend.ee_site].copy() - self.data.site_xpos[tracked_site_id].copy()
        ee_target_position = target_position + tracked_offset
        scratch = mujoco.MjData(self.model)
        scratch.qpos[:] = self.data.qpos
        scratch.qvel[:] = 0
        lower = self.model.jnt_range[self.backend.arm_joints, 0] + 0.01
        upper = self.model.jnt_range[self.backend.arm_joints, 1] - 0.01
        seed = np.asarray([self.data.qpos[index] for index in self.backend.arm_qpos], dtype=float)

        def residual(joints: np.ndarray) -> np.ndarray:
            scratch.qpos[:] = self.data.qpos
            scratch.qvel[:] = 0
            for index, qpos_address in enumerate(self.backend.arm_qpos):
                scratch.qpos[qpos_address] = joints[index]
            mujoco.mj_forward(self.model, scratch)
            current_rotation = scratch.site_xmat[self.backend.ee_site].reshape(3, 3)
            return np.concatenate((
                (scratch.site_xpos[self.backend.ee_site] - ee_target_position) * 3.0,
                self._orientation_error(current_rotation, self.desired_rotation) * 0.35,
            ))

        solution = least_squares(
            residual,
            np.clip(seed, lower, upper),
            bounds=(lower, upper),
            max_nfev=600,
            ftol=1e-11,
            xtol=1e-11,
            gtol=1e-11,
        )
        solved = np.asarray(solution.x, dtype=float)
        solved_residual = residual(solved)
        solved_position_error = float(np.linalg.norm(solved_residual[:3]) / 3.0)
        reached = False
        stop_reason = "ik_solution_tracking_timeout"
        long_flat_grasp = float((self.template.get("graspContract") or {}).get("planarAspectRatio") or 1.0) >= 2.0
        max_command_step = (
            (0.006 if long_flat_grasp else 0.012) if phase.startswith("lift")
            else (0.008 if long_flat_grasp else 0.012) if phase.startswith("transport")
            else 0.035
        )
        for tick in range(max_ticks):
            current = np.asarray([self.data.qpos[index] for index in self.backend.arm_qpos], dtype=float)
            # Position actuators need a bounded command lead to cancel the
            # steady gravity/contact bias between qpos and ctrl. This remains
            # closed-loop and never teleports qpos.
            delta = np.clip((solved - current) * 8.0, -max_command_step, max_command_step)
            action = self.data.ctrl.copy()
            action[:7] = np.clip(current + delta, lower, upper)
            self.backend.apply_action(action)
            self.backend.step(PHYSICS_HZ // CONTROL_HZ)
            if self.live_frame_callback is not None or tick % 2 == 0:
                self._record(phase)
            current_position = self.data.site_xpos[tracked_site_id].copy()
            current_rotation = self.data.site_xmat[self.backend.ee_site].reshape(3, 3).copy()
            position_error = float(np.linalg.norm(target_position - current_position))
            rotation_error = float(np.linalg.norm(self._orientation_error(current_rotation, self.desired_rotation)))
            if position_error < position_tolerance_m and rotation_error < rotation_tolerance_rad:
                reached = True
                stop_reason = "numerical_ik_pose_tolerance"
                break
            if not self.backend.state()["finite"]:
                stop_reason = "non_finite_state"
                break
        final_error = float(np.linalg.norm(target_position - self.data.site_xpos[tracked_site_id]))
        self.phases.append({
            "phase": phase,
            "reached": reached,
            "ticks": tick + 1,
            "targetM": [float(value) for value in target_position],
            "trackedSite": "franka_ee" if tracked_site is None else "compiled_asset_grasp",
            "stopReason": stop_reason,
            "solver": "bounded_scipy_least_squares_then_mujoco_actuator_tracking",
            "solverSuccess": bool(solution.success),
            "solverEvaluations": int(solution.nfev),
            "solvedPositionErrorM": solved_position_error,
            "solutionDeltaNormRad": float(np.linalg.norm(solved - seed)),
            "finalJointErrorNormRad": float(np.linalg.norm(solved - np.asarray([self.data.qpos[index] for index in self.backend.arm_qpos]))),
            "positionToleranceM": position_tolerance_m,
            "rotationToleranceRad": rotation_tolerance_rad,
            "maxJointCommandStepRad": max_command_step,
            "finalErrorM": final_error,
        })
        self._capture(phase)
        return reached

    def _contacting_fingers(self) -> set[str]:
        return {
            finger
            for contact in self.backend.contacts()
            if "pick_object" in {contact.body_a, contact.body_b}
            for finger in ({contact.body_a, contact.body_b} & {"left_finger", "right_finger"})
        }

    def _gripper(self, control: float, phase: str, steps: int = 220) -> None:
        super()._gripper(control, phase, steps)
        if phase != "close_gripper" or self._contacting_fingers() == {"left_finger", "right_finger"}:
            return
        # A rounded convex hull can translate slightly during the first pad
        # touch. Re-open, read the new physical grasp-site pose, re-center in
        # free space, and close once more. This is contact feedback, not an
        # object teleport or a relaxed bilateral-contact predicate.
        super()._gripper(255.0, "reopen_after_unilateral_contact", steps=120)
        moved = self._move_solved_ik(
            self.data.site_xpos[self.grasp_site].copy(),
            "contact_feedback_recenter",
            max_ticks=220,
            position_tolerance_m=0.0008,
            rotation_tolerance_rad=0.09,
        )
        if moved:
            super()._gripper(0.0, "reclose_gripper", steps=320)

    def _move(
        self,
        target_position: np.ndarray,
        phase: str,
        max_ticks: int = 180,
        **kwargs: Any,
    ) -> bool:
        if phase.startswith(("lift", "transport")):
            tracked_site = kwargs.get("tracked_site")
            tracked_site_id = self.backend.ee_site if tracked_site is None else int(tracked_site)
            start = self.data.site_xpos[tracked_site_id].copy()
            distance = float(np.linalg.norm(target_position - start))
            segment_count = max(1, int(math.ceil(distance / 0.035)))
            for segment in range(1, segment_count + 1):
                fraction = segment / segment_count
                waypoint = start + (target_position - start) * fraction
                if not self._move_solved_ik(
                    waypoint,
                    f"{phase}_segment_{segment:02d}",
                    max_ticks=max(300, max_ticks // segment_count + 60),
                    position_tolerance_m=0.007,
                    rotation_tolerance_rad=0.1,
                    tracked_site=tracked_site,
                ):
                    self.phases.append({
                        "phase": phase,
                        "reached": False,
                        "completedSegments": segment - 1,
                        "segmentCount": segment_count,
                        "finalErrorM": float(np.linalg.norm(target_position - self.data.site_xpos[tracked_site_id])),
                    })
                    return False
            self.phases.append({
                "phase": phase,
                "reached": True,
                "completedSegments": segment_count,
                "segmentCount": segment_count,
                "finalErrorM": float(np.linalg.norm(target_position - self.data.site_xpos[tracked_site_id])),
            })
            return True
        if phase in {"pre_grasp", "grasp_approach"}:
            if phase == "pre_grasp" and self.template.get("relation") == "on_top_of":
                target_volume = (self.template.get("targetVolumes") or [{}])[0]
                obstacle_clearance_z = float(target_volume.get("supportTopM", target_position[2])) + 0.12
                obstacle_center = np.asarray(target_volume.get("centerM", target_position)[:2], dtype=float)
                segment_start = self.data.site_xpos[self.backend.ee_site, :2].copy()
                segment_end = target_position[:2]
                segment = segment_end - segment_start
                denominator = float(np.dot(segment, segment))
                fraction = 0.0 if denominator < 1e-12 else float(np.clip(np.dot(obstacle_center - segment_start, segment) / denominator, 0.0, 1.0))
                closest = segment_start + segment * fraction
                obstacle_half = np.asarray(target_volume.get("halfExtentsM") or [target_volume.get("radiusM", 0.0)] * 2, dtype=float)
                path_intersects_obstacle = float(np.linalg.norm(obstacle_center - closest)) <= float(np.linalg.norm(obstacle_half) + 0.08)
                if obstacle_clearance_z > float(target_position[2]) + 0.05 and path_intersects_obstacle:
                    clearance_target = target_position.copy()
                    clearance_target[2] = obstacle_clearance_z
                    if not self._move_solved_ik(
                        clearance_target,
                        "pre_grasp_obstacle_clearance",
                        max_ticks=max(max_ticks, 360),
                        position_tolerance_m=0.008,
                        rotation_tolerance_rad=0.1,
                        tracked_site=kwargs.get("tracked_site"),
                    ):
                        return False
            return self._move_solved_ik(
                target_position,
                phase,
                max_ticks=max(max_ticks, 260),
                position_tolerance_m=0.0008 if phase == "pre_grasp" else 0.001,
                rotation_tolerance_rad=0.09,
                tracked_site=kwargs.get("tracked_site"),
            )
        return super()._move(target_position, phase, max_ticks=max_ticks, **kwargs)


class AuthoredSceneDropOffTableOracle(AuthoredScenePickPlaceOracle):
    """Pick one compiled object, release it beyond the measured counter edge,
    and verify the resulting MuJoCo motion instead of reusing a place target.
    """

    def run(self, seed: int = 0) -> OracleResult:
        started = time.perf_counter()
        self.backend.reset(seed)
        self.backend.step(250)
        self.desired_rotation = self.data.site_xmat[self.backend.ee_site].reshape(3, 3).copy()
        initial_grasp = self.data.site_xpos[self.grasp_site].copy()
        self._record("reset")
        self._capture("reset")
        failure: tuple[str, str] | None = None
        settle_positions: list[np.ndarray] = []
        settle_quaternions: list[np.ndarray] = []
        settle_linear_speeds: list[float] = []
        settle_angular_speeds: list[float] = []
        settle_steps = 0
        grasp_contract = self.template["graspContract"]
        required_width = float(grasp_contract["requiredGripperWidthM"])
        configured_open_width = 0.08
        if required_width > configured_open_width - 0.003:
            failure = (
                "unreachable_target",
                f"Asset grasp cross-section {required_width:.4f} m exceeds the validated Franka opening with 3 mm clearance.",
            )
        else:
            action = self.data.ctrl.copy()
            action[-1] = 255.0
            self.backend.apply_action(action)
            self.backend.step(120)
            pregrasp = initial_grasp + np.array([0.0, 0.0, 0.14])
            grasp = initial_grasp + np.array([0.0, 0.0, 0.004])
            if not self._move(pregrasp, "pre_grasp", max_ticks=260):
                failure = ("unreachable_target", "Differential IK did not reach the authored object pre-grasp waypoint.")
            elif not self._move(grasp, "grasp_approach", max_ticks=260):
                failure = ("pre_grasp_collision", "Differential IK did not reach the authored object grasp frame.")
            elif not self._align_axis(grasp, int(grasp_contract["gripperClosingAxisIndex"]), "grasp_axis_alignment"):
                failure = ("pre_grasp_collision", "Fine jaw-axis alignment did not converge at the authored grasp frame.")
            else:
                self._gripper(0.0, "close_gripper", steps=300)
                contacting_fingers = self._contacting_fingers()
                if contacting_fingers != {"left_finger", "right_finger"}:
                    failure = (
                        "grasp_miss",
                        f"Bilateral gripper contact was not established; contacting fingers={sorted(contacting_fingers)}.",
                    )
                elif not self._move(
                    initial_grasp + np.array([0.0, 0.0, 0.17]),
                    "lift",
                    max_ticks=260,
                    tracked_site=self.grasp_site,
                ):
                    failure = ("object_dropped", "The deterministic controller did not reach the lift waypoint.")
                elif self.data.site_xpos[self.grasp_site, 2] < initial_grasp[2] + 0.08:
                    failure = ("grasp_slip", "Gripper motion did not lift the compiler-authored body by 8 cm.")
                else:
                    drop_region = self.template["dropRegions"][0]
                    release_center = np.asarray(drop_region["releaseCenterM"], dtype=float)
                    if not self._move(
                        release_center,
                        "transport_off_table",
                        max_ticks=320,
                        tracked_site=self.grasp_site,
                    ):
                        failure = ("policy_timeout", "The deterministic controller did not reach the off-table release waypoint.")
                    else:
                        self._gripper(255.0, "release_off_table", steps=160)
                        retract = release_center + np.array([0.0, -0.10, 0.12])
                        self._move(retract, "retract", max_ticks=180)
                        for index in range(6000):
                            self.backend.step()
                            settle_positions.append(self.data.xpos[self.backend.object_body].copy())
                            settle_quaternions.append(self.data.xquat[self.backend.object_body].copy())
                            settle_linear_speeds.append(float(np.linalg.norm(self.data.cvel[self.backend.object_body, 3:])))
                            settle_angular_speeds.append(float(np.linalg.norm(self.data.cvel[self.backend.object_body, :3])))
                            record_interval = 20 if self.live_frame_callback is not None else 100
                            if index % record_interval == 0:
                                self._record("settle_after_drop")
                            settle_steps = index + 1
                            if settle_steps >= 1500 and settle_steps % 25 == 0:
                                window = np.asarray(settle_positions[-375:])
                                position_span = float(np.max(np.ptp(window, axis=0)))
                                linear_p95 = float(np.quantile(settle_linear_speeds[-375:], 0.95))
                                angular_p95 = float(np.quantile(settle_angular_speeds[-375:], 0.95))
                                rotation_span = _quaternion_rotation_span(settle_quaternions[-375:])
                                if (
                                    position_span < 0.003
                                    and linear_p95 < 0.02
                                    and settle_linear_speeds[-1] < 0.01
                                    and ((angular_p95 < 0.15 and settle_angular_speeds[-1] < 0.05) or rotation_span < 0.01)
                                ):
                                    break
                        self._capture("settle_after_drop")

        final_position = self.data.xpos[self.backend.object_body].copy()
        final_linear_speed = float(np.linalg.norm(self.data.cvel[self.backend.object_body, 3:]))
        final_angular_speed = float(np.linalg.norm(self.data.cvel[self.backend.object_body, :3]))
        settle_window = np.asarray(settle_positions[-375:]) if settle_positions else None
        settle_span = float(np.max(np.ptp(settle_window, axis=0))) if settle_window is not None else None
        settle_linear_p95 = float(np.quantile(settle_linear_speeds[-375:], 0.95)) if settle_linear_speeds else None
        settle_angular_p95 = float(np.quantile(settle_angular_speeds[-375:], 0.95)) if settle_angular_speeds else None
        settle_rotation_span = _quaternion_rotation_span(settle_quaternions[-375:]) if settle_quaternions else None
        counter_low, counter_high = np.asarray(self.template["dropRegions"][0]["counterBoundsM"], dtype=float)
        radius = float(grasp_contract["localBoundingRadiusM"])
        outside_support = bool(
            final_position[0] + radius < counter_low[0]
            or final_position[0] - radius > counter_high[0]
            or final_position[1] + radius < counter_low[1]
            or final_position[1] - radius > counter_high[1]
        )
        below_counter_top = bool(final_position[2] + radius < counter_high[2] - 0.02)
        final_contacts = self.backend.contacts()
        finger_contact = any(
            "pick_object" in {contact.body_a, contact.body_b}
            and ({contact.body_a, contact.body_b} & {"left_finger", "right_finger"})
            for contact in final_contacts
        )
        released = not finger_contact
        settled = bool(
            settle_span is not None
            and settle_span < 0.003
            and settle_linear_p95 is not None
            and settle_linear_p95 < 0.02
            and final_linear_speed < 0.01
            and (
                (settle_angular_p95 is not None and settle_angular_p95 < 0.15 and final_angular_speed < 0.05)
                or (settle_rotation_span is not None and settle_rotation_span < 0.01)
            )
        )
        success = bool(failure is None and outside_support and below_counter_top and released and settled)
        if failure is None and not success:
            failure = (
                "success_predicate_failure",
                f"outsideSupport={outside_support}, belowCounterTop={below_counter_top}, released={released}, "
                f"settled={settled}, finalPosition={final_position.tolist()}.",
            )
        predicate = {
            "assetVersionId": self.template["assetVersionId"],
            "assetManifestSha256": self.template["assetManifestSha256"],
            "taskKind": "drop_off_table",
            "outsideSupportPolygon": outside_support,
            "belowCounterTop": below_counter_top,
            "released": released,
            "settled": settled,
            "finalObjectPositionM": [float(value) for value in final_position],
            "counterBoundsM": [counter_low.tolist(), counter_high.tolist()],
            "objectBoundingRadiusM": radius,
            "finalLinearSpeedMps": final_linear_speed,
            "finalAngularSpeedRadS": final_angular_speed,
            "settlePositionSpanM": settle_span,
            "settleLinearSpeedP95Mps": settle_linear_p95,
            "settleAngularSpeedP95RadS": settle_angular_p95,
            "settleRotationSpanRad": settle_rotation_span,
            "settleSimulatedSeconds": settle_steps / PHYSICS_HZ if settle_steps else None,
            "placementEvidence": self.template["placements"][0],
        }
        return OracleResult(
            success=success,
            failure_code=failure[0] if failure else None,
            failure_detail=failure[1] if failure else None,
            duration_s=time.perf_counter() - started,
            phases=self.phases,
            trajectory=self.trajectory,
            contact_summary={
                "sampledPairs": self.contact_pairs,
                "samples": sum(self.contact_pairs.values()),
                "finalFingerContact": finger_contact,
            },
            predicate=predicate,
            frames=self.frames,
        )


def run_compiled_asset_oracle(
    robot_id: str,
    asset_version: dict[str, Any],
    run_id: str,
    seed: int = 0,
    placement_request: PlacementRequest | dict[str, Any] | None = None,
    record_observations: bool = False,
    live_frame_callback: Callable[[dict[str, Any], np.ndarray, np.ndarray], None] | None = None,
    realtime: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    template = compile_compiled_asset_world_template(
        robot_id,
        asset_version,
        placement_request=placement_request,
    )
    artifact_dir = (WORLDS_DIR / TEMPLATE_ID / "evaluations" / run_id).resolve()
    expected = (WORLDS_DIR / TEMPLATE_ID / "evaluations").resolve()
    if expected not in artifact_dir.parents:
        raise ValueError("Invalid evaluation artifact target.")
    backend = MujocoFrankaBackend(Path(template["runtimePath"]))
    try:
        result = CompiledAssetPickPlaceOracle(
            backend,
            artifact_dir,
            template,
            record_observations=record_observations,
            live_frame_callback=live_frame_callback,
            realtime=realtime,
        ).run(seed)
        output = {
            "schemaVersion": "robotworld.evaluation-result.v1",
            "runId": run_id,
            "robotId": robot_id,
            "worldTemplateId": template["id"],
            "worldTemplateRevision": int(template["revision"]),
            "worldRuntimeSha256": template["runtimeSha256"],
            "policy": COMPILED_ASSET_ORACLE_POLICY,
            "seed": seed,
            "success": result.success,
            "failureCode": result.failure_code,
            "failureDetail": result.failure_detail,
            "durationSeconds": result.duration_s,
            "physicsHz": PHYSICS_HZ,
            "controlHz": CONTROL_HZ,
            "phases": result.phases,
            "trajectory": result.trajectory,
            "contactSummary": result.contact_summary,
            "predicate": result.predicate,
            "frameHashes": result.frames,
        }
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "evaluation.json").write_text(json.dumps(output, indent=2), encoding="utf8")
        return output, template
    finally:
        backend.close()


def run_authored_scene_oracle(
    robot_id: str,
    asset_version: dict[str, Any],
    run_id: str,
    seed: int,
    world_id: str,
    source_placement: dict[str, Any],
    target_placement: dict[str, Any] | None,
    counter_placement: dict[str, Any],
    *,
    robot_spawn: dict[str, Any] | None = None,
    task_kind: str = "pick_place",
    relation: str = "on_top_of",
    live_frame_callback: Callable[[dict[str, Any], np.ndarray, np.ndarray], None] | None = None,
    realtime: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    template = compile_authored_scene_asset_world(
        robot_id,
        asset_version,
        world_id=world_id,
        source_placement=source_placement,
        target_placement=target_placement,
        counter_placement=counter_placement,
        robot_spawn=robot_spawn,
        task_kind=task_kind,
        relation=relation,
    )
    artifact_dir = (Path(template["runtimePath"]).parent.parent / "evaluations" / run_id).resolve()
    if not artifact_dir.is_relative_to(WORLDS_DIR.resolve()):
        raise ValueError("Invalid authored-scene evaluation artifact target.")
    backend = MujocoFrankaBackend(Path(template["runtimePath"]))
    try:
        oracle_type = AuthoredScenePickPlaceOracle if task_kind == "pick_place" else AuthoredSceneDropOffTableOracle
        result = oracle_type(
            backend,
            artifact_dir,
            template,
            live_frame_callback=live_frame_callback,
            realtime=realtime,
        ).run(seed)
        output = {
            "schemaVersion": "robotworld.evaluation-result.v1",
            "runId": run_id,
            "robotId": robot_id,
            "worldTemplateId": template["id"],
            "worldTemplateRevision": int(template["revision"]),
            "worldRuntimeSha256": template["runtimeSha256"],
            "policy": AUTHORED_SCENE_ORACLE_POLICY if task_kind == "pick_place" else AUTHORED_SCENE_DROP_ORACLE_POLICY,
            "seed": seed,
            "success": result.success,
            "failureCode": result.failure_code,
            "failureDetail": result.failure_detail,
            "durationSeconds": result.duration_s,
            "physicsHz": PHYSICS_HZ,
            "controlHz": CONTROL_HZ,
            "phases": result.phases,
            "trajectory": result.trajectory,
            "contactSummary": result.contact_summary,
            "predicate": result.predicate | {
                "authoredWorldId": world_id,
                "sourceAssetId": source_placement["assetId"],
                "targetAssetId": target_placement["assetId"] if target_placement is not None else None,
                "taskKind": task_kind,
            },
            "frameHashes": result.frames,
        }
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "evaluation.json").write_text(json.dumps(output, indent=2), encoding="utf8")
        return output, template
    finally:
        backend.close()


def run_oracle(
    robot_id: str,
    run_id: str,
    seed: int = 0,
    *,
    live_frame_callback: Callable[[dict[str, Any], np.ndarray, np.ndarray], None] | None = None,
    realtime: bool = False,
) -> dict[str, Any]:
    template = compile_world_template(robot_id)
    artifact_dir = (WORLDS_DIR / TEMPLATE_ID / "evaluations" / run_id).resolve()
    expected = (WORLDS_DIR / TEMPLATE_ID / "evaluations").resolve()
    if expected not in artifact_dir.parents:
        raise ValueError("Invalid evaluation artifact target.")
    backend = MujocoFrankaBackend(Path(template["runtimePath"]))
    try:
        result = PickPlaceOracle(
            backend,
            artifact_dir,
            live_frame_callback=live_frame_callback,
            realtime=realtime,
        ).run(seed)
        output = {
            "schemaVersion": "robotworld.evaluation-result.v1",
            "runId": run_id,
            "robotId": robot_id,
            "worldTemplateId": TEMPLATE_ID,
            "worldTemplateRevision": TEMPLATE_REVISION,
            "worldRuntimeSha256": template["runtimeSha256"],
            "policy": "deterministic_differential_ik_oracle_v1",
            "seed": seed,
            "success": result.success,
            "failureCode": result.failure_code,
            "failureDetail": result.failure_detail,
            "durationSeconds": result.duration_s,
            "physicsHz": PHYSICS_HZ,
            "controlHz": CONTROL_HZ,
            "phases": result.phases,
            "trajectory": result.trajectory,
            "contactSummary": result.contact_summary,
            "predicate": result.predicate,
            "frameHashes": result.frames,
        }
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "evaluation.json").write_text(json.dumps(output, indent=2), encoding="utf8")
        return output
    finally:
        backend.close()
