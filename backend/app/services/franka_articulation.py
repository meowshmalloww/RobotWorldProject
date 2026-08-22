"""Controlled Franka one-drawer world and deterministic physical oracle.

This is the first embodiment-correct articulated acceptance fixture.  It is
not generated geometry and is never represented as an exact product.  Its job
is to prove the Panda, gripper, moving link, handle parentage, contacts, joint
limits, two cameras, and success predicate before a multipart generated asset
is allowed into the same runtime adapter.
"""
from __future__ import annotations

import json
import math
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mujoco
import numpy as np
from PIL import Image

from ..config import WORLDS_DIR
from .franka_pick_place import CONTROL_HZ, PHYSICS_HZ, _safe_robot_manifest, _sha256


TEMPLATE_ID = "franka-controlled-drawer-open-v1"
TEMPLATE_REVISION = 1
ORACLE_POLICY = "deterministic_differential_ik_franka_drawer_oracle_v1"
DRAWER_INITIAL_POSITION = np.array([0.48, 0.08, 0.425], dtype=float)
HANDLE_LOCAL_POSITION = np.array([0.0, -0.17, 0.025], dtype=float)
DRAWER_RANGE_M = 0.18
SUCCESS_DISPLACEMENT_M = 0.10


def compile_world_template(robot_id: str) -> dict[str, Any]:
    runtime, robot = _safe_robot_manifest(robot_id)
    root = (WORLDS_DIR / TEMPLATE_ID / f"robot-{robot_id}").resolve()
    if (WORLDS_DIR / TEMPLATE_ID).resolve() not in root.parents:
        raise ValueError("Invalid articulated world-template target.")
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
            "name": "drawer_support",
            "type": "box",
            "pos": "0.48 0.08 0.32",
            "size": "0.21 0.23 0.04",
            "rgba": "0.28 0.30 0.34 1",
            "friction": "0.8 0.02 0.001",
        },
    )
    # Side/back pieces make the controlled fixture visibly a drawer carcass
    # without intersecting the moving link at its closed pose.
    for name, pos, size in (
        ("drawer_carcass_left", "0.285 0.08 0.41", "0.015 0.23 0.13"),
        ("drawer_carcass_right", "0.675 0.08 0.41", "0.015 0.23 0.13"),
        ("drawer_carcass_back", "0.48 0.295 0.41", "0.18 0.015 0.13"),
    ):
        ET.SubElement(
            worldbody,
            "geom",
            {"name": name, "type": "box", "pos": pos, "size": size, "rgba": "0.34 0.36 0.40 1"},
        )

    drawer = ET.SubElement(
        worldbody,
        "body",
        {"name": "controlled_drawer", "pos": " ".join(str(value) for value in DRAWER_INITIAL_POSITION)},
    )
    ET.SubElement(
        drawer,
        "joint",
        {
            "name": "drawer_slide",
            "type": "slide",
            "axis": "0 -1 0",
            "range": f"0 {DRAWER_RANGE_M}",
            "limited": "true",
            "damping": "1.0",
            "frictionloss": "0.25",
        },
    )
    ET.SubElement(
        drawer,
        "geom",
        {
            "name": "drawer_box",
            "type": "box",
            "size": "0.16 0.14 0.032",
            "mass": "1.2",
            "rgba": "0.74 0.77 0.82 1",
            "friction": "0.7 0.02 0.001",
        },
    )
    ET.SubElement(
        drawer,
        "geom",
        {
            "name": "drawer_front",
            "type": "box",
            "pos": "0 -0.155 0.025",
            "size": "0.18 0.015 0.085",
            "mass": "0.5",
            "rgba": "0.82 0.84 0.88 1",
        },
    )
    ET.SubElement(
        drawer,
        "geom",
        {
            "name": "drawer_handle",
            "type": "capsule",
            "pos": " ".join(str(value) for value in HANDLE_LOCAL_POSITION),
            "size": "0.012 0.05",
            "mass": "0.12",
            "rgba": "0.15 0.16 0.18 1",
            "friction": "2.5 0.01 0.001",
            "solref": "0.003 1",
            "solimp": "0.95 0.99 0.001",
        },
    )
    ET.SubElement(
        drawer,
        "site",
        {
            "name": "drawer_handle_site",
            "pos": " ".join(str(value) for value in HANDLE_LOCAL_POSITION),
            "size": "0.006",
            "rgba": "0.1 0.9 0.4 0.7",
        },
    )
    for finger_name in ("left_finger", "right_finger"):
        finger = next((item for item in worldbody.iter("body") if item.get("name") == finger_name), None)
        if finger is not None:
            for geom in finger.findall("geom"):
                geom.set("friction", "2.8 0.01 0.001")

    keyframe = mujoco_root.find("keyframe")
    if keyframe is not None:
        for key in keyframe.findall("key"):
            qpos = str(key.get("qpos") or "").strip()
            key.set("qpos", f"{qpos} 0".strip())

    ET.indent(tree, space="  ")
    world_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(world_path, encoding="utf-8", xml_declaration=True)
    model = mujoco.MjModel.from_xml_path(str(world_path))
    data = mujoco.MjData(model)
    home = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    drawer_joint = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "drawer_slide")
    drawer_body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "controlled_drawer")
    handle_site = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "drawer_handle_site")
    handle_geom = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "drawer_handle")
    if min(home, drawer_joint, drawer_body, handle_site, handle_geom) < 0:
        raise ValueError("Controlled drawer world is missing a required joint/link/handle.")
    if int(model.site_bodyid[handle_site]) != drawer_body or int(model.geom_bodyid[handle_geom]) != drawer_body:
        raise ValueError("Drawer handle is not parented to the moving link.")
    mujoco.mj_resetDataKeyframe(model, data, home)
    mujoco.mj_forward(model, data)
    initial_severe = sum(1 for index in range(data.ncon) if float(data.contact[index].dist) < -0.005)
    qadr = int(model.jnt_qposadr[drawer_joint])
    handle_positions: list[list[float]] = []
    finite = True
    severe_sweep = 0
    for value in np.linspace(0.0, DRAWER_RANGE_M, 9):
        data.qpos[qadr] = float(value)
        data.qvel[:] = 0.0
        mujoco.mj_forward(model, data)
        handle_positions.append([float(item) for item in data.site_xpos[handle_site]])
        finite = finite and bool(np.isfinite(data.qpos).all() and np.isfinite(data.xpos).all())
        severe_sweep += sum(1 for index in range(data.ncon) if float(data.contact[index].dist) < -0.005)
    path_span = float(np.linalg.norm(np.asarray(handle_positions[-1]) - np.asarray(handle_positions[0])))
    errors: list[str] = []
    if initial_severe:
        errors.append(f"initial severe penetration count={initial_severe}")
    if severe_sweep:
        errors.append(f"joint sweep severe penetration count={severe_sweep}")
    if not finite:
        errors.append("joint sweep produced non-finite state")
    if path_span < DRAWER_RANGE_M - 1e-5:
        errors.append(f"handle did not follow the moving link across its limit ({path_span:.6f} m)")
    if errors:
        raise ValueError("; ".join(errors))
    template = {
        "schemaVersion": "robotworld.world-template.v1",
        "id": TEMPLATE_ID,
        "revision": TEMPLATE_REVISION,
        "name": "Controlled Franka one-drawer opening validation",
        "truthMode": "authoritative_physics_controlled_fixture",
        "runtimeBackend": "mujoco",
        "runtimePath": str(world_path),
        "runtimeSha256": _sha256(world_path),
        "robotId": robot_id,
        "robotRuntimeSha256": robot["runtimeSha256"],
        "partGraph": {
            "rootPartId": "carcass",
            "parts": [
                {"id": "carcass", "semantic": "drawer_carcass", "parentPartId": None},
                {"id": "drawer", "semantic": "sliding_drawer", "parentPartId": "carcass"},
                {"id": "handle", "semantic": "drawer_handle", "parentPartId": "drawer"},
            ],
            "joints": [
                {
                    "id": "drawer_slide",
                    "type": "prismatic",
                    "parent": "carcass",
                    "child": "drawer",
                    "axis": [0.0, -1.0, 0.0],
                    "limitsM": [0.0, DRAWER_RANGE_M],
                },
                {"id": "handle_mount", "type": "fixed", "parent": "drawer", "child": "handle"},
            ],
        },
        "affordance": {
            "id": "drawer_handle_open",
            "handleSite": "drawer_handle_site",
            "requiredGripperWidthM": 0.024,
            "pullAxis": [0.0, -1.0, 0.0],
        },
        "jointSweep": {
            "passed": True,
            "sampleCount": 9,
            "handleAttachedToMovingPart": True,
            "handlePathSpanM": path_span,
            "severePenetrationCount": severe_sweep,
            "finite": finite,
            "samples": handle_positions,
        },
        "successPredicate": {
            "joint": "drawer_slide",
            "minimumDisplacementM": SUCCESS_DISPLACEMENT_M,
            "requiresBilateralHandleContact": True,
        },
        "source": {
            "type": "robotworld_controlled_articulation_fixture",
            "identityScope": "controlled_not_product_evidence",
            "license": "project",
        },
    }
    template_path = root / "template.json"
    template_path.write_text(json.dumps(template, indent=2, sort_keys=True), encoding="utf8")
    return template


@dataclass
class DrawerOracleResult:
    success: bool
    failure_code: str | None
    failure_detail: str | None
    duration_s: float
    phases: list[dict[str, Any]]
    trajectory: list[dict[str, Any]]
    contact_summary: dict[str, Any]
    predicate: dict[str, Any]
    frames: dict[str, dict[str, str]]


class FrankaDrawerOracle:
    def __init__(self, world_path: Path, artifact_dir: Path):
        self.model = mujoco.MjModel.from_xml_path(str(world_path))
        self.data = mujoco.MjData(self.model)
        self.artifact_dir = artifact_dir
        self.ee_site = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "franka_ee")
        self.handle_site = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "drawer_handle_site")
        self.drawer_joint = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "drawer_slide")
        self.drawer_body = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "controlled_drawer")
        self.finger_bodies = {
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "left_finger"),
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "right_finger"),
        }
        self.arm_joints = [
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, f"joint{index}") for index in range(1, 8)
        ]
        self.arm_qpos = [int(self.model.jnt_qposadr[index]) for index in self.arm_joints]
        self.arm_dofs = [int(self.model.jnt_dofadr[index]) for index in self.arm_joints]
        if min(self.ee_site, self.handle_site, self.drawer_joint, self.drawer_body, *self.finger_bodies, *self.arm_joints) < 0:
            raise ValueError("World does not satisfy the Franka drawer oracle contract.")
        self.drawer_qpos = int(self.model.jnt_qposadr[self.drawer_joint])
        self.trajectory: list[dict[str, Any]] = []
        self.phases: list[dict[str, Any]] = []
        self.frames: dict[str, dict[str, str]] = {}
        self.contact_pairs: dict[str, int] = {}
        self.renderers: dict[tuple[int, int], mujoco.Renderer] = {}
        self.desired_rotation = np.eye(3)

    def close(self) -> None:
        for renderer in self.renderers.values():
            renderer.close()
        self.renderers.clear()

    def _reset(self) -> None:
        home = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_KEY, "home")
        mujoco.mj_resetDataKeyframe(self.model, self.data, home)
        self.data.qpos[self.drawer_qpos] = 0.0
        self.data.qvel[:] = 0.0
        mujoco.mj_forward(self.model, self.data)

    def _step(self, count: int = 1) -> None:
        for _ in range(max(1, int(count))):
            mujoco.mj_step(self.model, self.data)

    def _body_name(self, body_id: int) -> str:
        return str(mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_BODY, body_id) or "world")

    def _contacts(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index in range(self.data.ncon):
            contact = self.data.contact[index]
            body_a = int(self.model.geom_bodyid[contact.geom1])
            body_b = int(self.model.geom_bodyid[contact.geom2])
            force = np.zeros(6)
            mujoco.mj_contactForce(self.model, self.data, index, force)
            rows.append(
                {
                    "bodyA": self._body_name(body_a),
                    "bodyB": self._body_name(body_b),
                    "distanceM": float(contact.dist),
                    "normalForceN": float(force[0]),
                }
            )
        return rows

    def _state(self, phase: str) -> dict[str, Any]:
        contacts = self._contacts()
        row = {
            "phase": phase,
            "timeSeconds": float(self.data.time),
            "jointPosition": [float(self.data.qpos[index]) for index in self.arm_qpos],
            "gripperWidthM": float(self.data.qpos[7] + self.data.qpos[8]),
            "endEffectorPositionM": [float(value) for value in self.data.site_xpos[self.ee_site]],
            "handlePositionM": [float(value) for value in self.data.site_xpos[self.handle_site]],
            "drawerDisplacementM": float(self.data.qpos[self.drawer_qpos]),
            "contacts": contacts[:24],
            "finite": bool(np.isfinite(self.data.qpos).all() and np.isfinite(self.data.qvel).all()),
        }
        for contact in contacts:
            pair = "|".join(sorted((str(contact["bodyA"]), str(contact["bodyB"]))))
            self.contact_pairs[pair] = self.contact_pairs.get(pair, 0) + 1
        return row

    def _record(self, phase: str) -> None:
        self.trajectory.append(self._state(phase))

    def _capture(self, phase: str) -> None:
        self.frames[phase] = {}
        frame_root = self.artifact_dir / "frames"
        frame_root.mkdir(parents=True, exist_ok=True)
        for camera in ("front", "wrist"):
            key = (224, 224)
            renderer = self.renderers.get(key)
            if renderer is None:
                renderer = mujoco.Renderer(self.model, height=224, width=224)
                self.renderers[key] = renderer
            renderer.update_scene(self.data, camera=camera)
            path = frame_root / f"{phase}-{camera}.png"
            Image.fromarray(renderer.render().copy(), mode="RGB").save(path, format="PNG", optimize=False)
            self.frames[phase][camera] = _sha256(path)

    @staticmethod
    def _orientation_error(current: np.ndarray, target: np.ndarray) -> np.ndarray:
        return 0.5 * sum(
            (np.cross(current[:, index], target[:, index]) for index in range(3)), start=np.zeros(3)
        )

    def _move(self, target: np.ndarray, phase: str, max_ticks: int = 240) -> bool:
        jacp = np.zeros((3, self.model.nv))
        jacr = np.zeros((3, self.model.nv))
        reached = False
        for tick in range(max_ticks):
            position_error = target - self.data.site_xpos[self.ee_site]
            rotation = self.data.site_xmat[self.ee_site].reshape(3, 3)
            rotation_error = self._orientation_error(rotation, self.desired_rotation)
            if float(np.linalg.norm(position_error)) < 0.006 and float(np.linalg.norm(rotation_error)) < 0.08:
                reached = True
                break
            mujoco.mj_jacSite(self.model, self.data, jacp, jacr, self.ee_site)
            jacobian = np.vstack((jacp[:, self.arm_dofs], jacr[:, self.arm_dofs]))
            error = np.concatenate((position_error * 1.1, rotation_error * 0.45))
            lhs = jacobian @ jacobian.T + np.eye(6) * 0.004
            delta = jacobian.T @ np.linalg.solve(lhs, error)
            norm = float(np.linalg.norm(delta))
            if norm > 0.08:
                delta *= 0.08 / norm
            qpos = np.asarray([self.data.qpos[index] for index in self.arm_qpos], dtype=float)
            target_qpos = np.clip(
                qpos + delta,
                self.model.jnt_range[self.arm_joints, 0] + 0.01,
                self.model.jnt_range[self.arm_joints, 1] - 0.01,
            )
            self.data.ctrl[:7] = target_qpos
            self._step(PHYSICS_HZ // CONTROL_HZ)
            if tick % 2 == 0:
                self._record(phase)
            if not np.isfinite(self.data.qpos).all() or not np.isfinite(self.data.qvel).all():
                break
        self.phases.append(
            {
                "phase": phase,
                "reached": reached,
                "ticks": tick + 1,
                "targetM": [float(value) for value in target],
                "finalErrorM": float(np.linalg.norm(target - self.data.site_xpos[self.ee_site])),
            }
        )
        self._capture(phase)
        return reached

    def _gripper(self, control: float, phase: str, steps: int = 260) -> None:
        self.data.ctrl[-1] = float(control)
        for index in range(steps):
            self._step()
            if index % 10 == 0:
                self._record(phase)
        self.phases.append(
            {
                "phase": phase,
                "control": float(control),
                "steps": steps,
                "widthM": float(self.data.qpos[7] + self.data.qpos[8]),
            }
        )
        self._capture(phase)

    def run(self, seed: int = 0) -> DrawerOracleResult:
        del seed  # the controlled fixture has no randomized state yet
        started = time.perf_counter()
        self._reset()
        self._step(250)
        self.desired_rotation = self.data.site_xmat[self.ee_site].reshape(3, 3).copy()
        initial_handle = self.data.site_xpos[self.handle_site].copy()
        self._record("reset")
        self._capture("reset")
        failure: tuple[str, str] | None = None
        bilateral = False
        self._gripper(255.0, "open_gripper", steps=120)
        pregrasp = initial_handle + np.array([0.0, 0.0, 0.13])
        grasp = initial_handle + np.array([0.0, 0.0, 0.003])
        if not self._move(pregrasp, "pre_grasp"):
            failure = ("unreachable_target", "Differential IK did not reach the drawer pre-grasp waypoint.")
        elif not self._move(grasp, "grasp_approach"):
            failure = ("pre_grasp_collision", "Differential IK did not reach the drawer handle grasp frame.")
        else:
            self._gripper(0.0, "close_gripper", steps=320)
            contacts = self._contacts()
            contacted = {
                body
                for contact in contacts
                if "controlled_drawer" in {contact["bodyA"], contact["bodyB"]}
                for body in ({contact["bodyA"], contact["bodyB"]} & {"left_finger", "right_finger"})
            }
            bilateral = contacted == {"left_finger", "right_finger"}
            if not bilateral:
                failure = ("grasp_miss", f"Bilateral handle contact was not established; fingers={sorted(contacted)}.")
            else:
                pull_target = self.data.site_xpos[self.ee_site].copy() + np.array([0.0, -0.14, 0.0])
                self._move(pull_target, "follow_prismatic_joint", max_ticks=320)
                displacement = float(self.data.qpos[self.drawer_qpos])
                if displacement < SUCCESS_DISPLACEMENT_M:
                    failure = (
                        "joint_resistance_control_failure",
                        f"Drawer moved {displacement:.4f} m; required {SUCCESS_DISPLACEMENT_M:.4f} m.",
                    )
                self._gripper(255.0, "release", steps=220)
        displacement = float(self.data.qpos[self.drawer_qpos])
        final_handle = self.data.site_xpos[self.handle_site].copy()
        finite = bool(np.isfinite(self.data.qpos).all() and np.isfinite(self.data.qvel).all())
        joint_limit_tolerance_m = 0.002
        within_joint_limit = bool(
            -joint_limit_tolerance_m <= displacement <= DRAWER_RANGE_M + joint_limit_tolerance_m
        )
        success = bool(
            failure is None
            and bilateral
            and displacement >= SUCCESS_DISPLACEMENT_M
            and finite
            and within_joint_limit
        )
        if failure is None and not success:
            failure = ("success_predicate_failure", "Drawer predicate did not pass after the physical pull.")
        self._record("terminal")
        self._capture("terminal")
        return DrawerOracleResult(
            success=success,
            failure_code=failure[0] if failure else None,
            failure_detail=failure[1] if failure else None,
            duration_s=time.perf_counter() - started,
            phases=self.phases,
            trajectory=self.trajectory,
            contact_summary={"pairs": self.contact_pairs, "bilateralHandleContact": bilateral},
            predicate={
                "drawerDisplacementM": displacement,
                "minimumDisplacementM": SUCCESS_DISPLACEMENT_M,
                "handleDisplacementM": float(np.linalg.norm(final_handle - initial_handle)),
                "finite": finite,
                "withinJointLimit": within_joint_limit,
                "jointLimitToleranceM": joint_limit_tolerance_m,
            },
            frames=self.frames,
        )


def run_oracle(robot_id: str, run_id: str, seed: int = 0) -> dict[str, Any]:
    template = compile_world_template(robot_id)
    artifact_dir = WORLDS_DIR / TEMPLATE_ID / "runs" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    oracle = FrankaDrawerOracle(Path(template["runtimePath"]), artifact_dir)
    try:
        result = oracle.run(seed)
    finally:
        oracle.close()
    wire = {
        "schemaVersion": "robotworld.evaluation-result.v1",
        "runId": run_id,
        "worldTemplateId": TEMPLATE_ID,
        "worldTemplateRevision": TEMPLATE_REVISION,
        "worldRuntimeSha256": template["runtimeSha256"],
        "robotId": robot_id,
        "policy": ORACLE_POLICY,
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
        "predicate": result.predicate | {"truthMode": "authoritative_physics_controlled_fixture"},
        "frameHashes": result.frames,
    }
    (artifact_dir / "result.json").write_text(json.dumps(wire, indent=2, sort_keys=True), encoding="utf8")
    return wire
