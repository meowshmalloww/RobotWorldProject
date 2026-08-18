"""MuJoCo rollout engine.

Builds the compiled MJCF world, runs a real contact-physics episode of the
mobile manipulator attempting to open the articulated door, records
observation/action trajectories for behavior cloning, and classifies genuine
failure modes (no_contact / grasp_slip / insufficient_pull / collision /
timeout) from contact and joint telemetry.

Two controllers:
  - ScriptedController: state machine + damped-least-squares IK (the expert
    that supplies demonstration trajectories).
  - PolicyController:   a trained torch MLP (bc-mlp) evaluated the same way.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import mujoco
import numpy as np

from .mjcf import ARM, build_world

CTRL_HZ = 60
DT_CTRL = 1.0 / CTRL_HZ
DOOR_SUCCESS_RAD = math.radians(60.0)

HOME = np.array([1.57, 0.35, -0.45, 0.55])   # arm forward-up, safely clear of the appliance
READY = np.array([1.57, 0.60, -0.70, 0.55])  # posture prior — mid-range, untwisted


@dataclass
class StepInfo:
    t: float
    qpos: np.ndarray
    ee: np.ndarray
    handle: np.ndarray
    door_rad: float
    grip: float
    in_contact: bool
    contact_force: float
    collisions: int


@dataclass
class RolloutResult:
    success: bool
    door_angle_deg: float
    collisions: int
    duration_s: float
    failure_mode: str | None
    failure_detail: str | None
    door_peak_deg: float = 0.0
    obs: np.ndarray = field(default_factory=lambda: np.zeros((0, 12), dtype=np.float32))
    act: np.ndarray = field(default_factory=lambda: np.zeros((0, 5), dtype=np.float32))
    frames: list[dict] = field(default_factory=list)


class World:
    """A loaded MuJoCo world with named handles."""

    def __init__(self, scenario: dict[str, Any], asset_spec: dict[str, Any] | None = None):
        # The compiler's canonical refrigerator geometry is the default asset.
        # Passing an empty spec previously placed a much smaller door at the
        # world origin, making every nominal controller plan infeasible.
        self.xml = build_world(scenario, asset_spec or FRIDGE_SPEC)
        self.model = mujoco.MjModel.from_xml_string(self.xml)
        self.data = mujoco.MjData(self.model)
        self.scenario = scenario
        self.j = {
            "yaw": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "j_yaw"),
            "shoulder": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "j_shoulder"),
            "elbow": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "j_elbow"),
            "wrist": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "j_wrist"),
            "fl": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "j_finger_l"),
            "fr": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "j_finger_r"),
            "door": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "j_door"),
        }
        self.ee_site = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "ee")
        self.handle_site = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "handle_site")
        self.door_max = self.model.jnt_range[self.j["door"]][1]
        self._ee_jac_t = np.zeros((3, self.model.nv))
        self._ee_jac_r = np.zeros((3, self.model.nv))
        self.grasp_eqs = {
            name: mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_EQUALITY, f"grasp_eq{sfx}")
            for name, sfx in (("root", "_root"), ("mid", ""), ("tip", "_tip"))
        }
        self.grasp_sites = {
            name: mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, sname)
            for name, sname in (("root", "grasp_root"), ("mid", "ee"), ("tip", "grasp_tip"))
        }
        self.active_grasp: str | None = None
        self.hand_body = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "hand")
        self.door_body = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "door")
        self.reset()  # geometry queries must be valid right after construction

    # -- grasp assist (sticky grasp: site-to-site ball joint at the bar) ----
    @property
    def attached(self) -> bool:
        return self.active_grasp is not None

    def grasp_distances(self) -> dict[str, float]:
        """Distance from each grasp site to the handle site."""
        hp = self.handle_pos()
        return {
            name: float(np.linalg.norm(self.data.site_xpos[sid] - hp))
            for name, sid in self.grasp_sites.items()
        }

    def attach(self) -> str:
        """Activate the ball joint for the grasp site NEAREST the bar —
        the real contact point varies with how deep the bar seated."""
        dists = self.grasp_distances()
        best = min(dists, key=dists.get)
        self.active_grasp = best
        self.data.eq_active[self.grasp_eqs[best]] = 1
        # offset of the active grasp site from the ee site (world, ~constant
        # during the gentle pull) — pull IK targets are corrected by it
        self.grasp_site_offset = self.data.site_xpos[self.grasp_sites[best]] - self.data.site_xpos[self.ee_site]
        return best

    def detach(self) -> None:
        if self.active_grasp is not None:
            self.data.eq_active[self.grasp_eqs[self.active_grasp]] = 0
            self.active_grasp = None
            self.grasp_site_offset = None

    def reset(self) -> None:
        mujoco.mj_resetData(self.model, self.data)
        # Start AT the home pose (not qpos=0): the straight-up configuration is
        # IK-singular in yaw and drops the solver onto the twisted branch.
        for i, k in enumerate(("yaw", "shoulder", "elbow", "wrist")):
            self.data.qpos[self.adr(k)] = HOME[i]
        self.set_arm(HOME)
        self.set_grip(0.0)
        mujoco.mj_forward(self.model, self.data)

    # -- state -------------------------------------------------------------
    def adr(self, joint: str) -> int:
        return self.model.jnt_qposadr[self.j[joint]]

    def qpos(self, joint: str) -> float:
        return float(self.data.qpos[self.adr(joint)])

    def arm_qpos(self) -> np.ndarray:
        return np.array([self.qpos(k) for k in ("yaw", "shoulder", "elbow", "wrist")])

    def grip(self) -> float:
        return float(np.clip(self.qpos("fl") / self.GRIP_RANGE, 0.0, 1.0))

    def door_rad(self) -> float:
        return self.qpos("door")

    def ee_pos(self) -> np.ndarray:
        return self.data.site_xpos[self.ee_site].copy()

    def handle_pos(self) -> np.ndarray:
        return self.data.site_xpos[self.handle_site].copy()

    # -- control -----------------------------------------------------------
    def set_arm(self, q: np.ndarray) -> None:
        for i, k in enumerate(("yaw", "shoulder", "elbow", "wrist")):
            self.data.ctrl[i] = q[i]

    GRIP_RANGE = 0.04  # finger slide range (m)

    def set_grip(self, g: float) -> None:
        g = float(np.clip(g, 0.0, 1.0))
        self.data.ctrl[4] = self.GRIP_RANGE * g
        self.data.ctrl[5] = self.GRIP_RANGE * g

    JOINT_LIM = [(-3.0, 3.0), (0.05, 1.9), (-2.8, 0.2), (-1.5, 2.2)]
    IK_SEEDS = [
        READY,
        np.array([1.57, 0.9, -1.2, 0.8]),      # mid fold
        np.array([1.9, 1.7, -0.5, -1.0]),      # reach forward, hand up
        np.array([1.9, 1.9, -0.2, -1.4]),      # extended, fingers up (grasp)
        np.array([1.57, 0.3, -0.2, 0.4]),      # near-extended
    ]

    def _clip_arm(self, q: np.ndarray) -> np.ndarray:
        return np.array([np.clip(q[i], *self.JOINT_LIM[i]) for i in range(4)])

    def hand_pitch(self, q: np.ndarray) -> float:
        """Hand (wrist-stub) elevation from vertical: shoulder+elbow+wrist."""
        return float(q[1] + q[2] + q[3])

    def solve_ik(self, target: np.ndarray, pitch: float | None = None, *, iters: int = 40, damping: float = 0.06) -> np.ndarray:
        """Weighted-norm DLS IK: [position ; pitch? ; posture] residual. The
        posture term (soft pull toward READY) systematically rejects contorted
        branches instead of hoping local descent finds a good one.
        Warm-started with the current pose, multi-seed fallback with hysteresis."""
        cols = [self.model.jnt_dofadr[self.j[k]] for k in ("yaw", "shoulder", "elbow", "wrist")]
        saved_qpos = self.data.qpos.copy()
        pitch_w = 0.6
        posture_w = 0.02  # tie-breaker only — must never fight the position task
        eye4 = posture_w * np.eye(4)
        pitch_row = np.array([[0.0, pitch_w, pitch_w, pitch_w]])

        def residual(q: np.ndarray) -> np.ndarray:
            err = target - self.data.site_xpos[self.ee_site]
            parts = [err, posture_w * (q - READY)]
            if pitch is not None:
                parts.insert(1, np.array([pitch_w * (pitch - self.hand_pitch(q))]))
            return np.concatenate(parts)

        def solve_from(seed: np.ndarray) -> tuple[np.ndarray, float]:
            q = seed.astype(float).copy()
            for _ in range(iters):
                self.data.qpos[cols] = q
                mujoco.mj_forward(self.model, self.data)
                err = residual(q)
                mujoco.mj_jacSite(self.model, self.data, self._ee_jac_t, self._ee_jac_r, self.ee_site)
                J = self._ee_jac_t[:, cols]
                rows = [J, eye4]
                if pitch is not None:
                    rows.insert(1, pitch_row)
                J = np.vstack(rows)
                JJt = J @ J.T + damping**2 * np.eye(J.shape[0])
                dq = J.T @ np.linalg.solve(JJt, err)
                q = self._clip_arm(q + np.clip(dq, -0.25, 0.25))
            self.data.qpos[cols] = q
            mujoco.mj_forward(self.model, self.data)
            return q, float(np.linalg.norm(residual(q)))

        # hysteresis: prefer the warm-start branch; switch only on a big win
        warm_q, warm_err = solve_from(self.arm_qpos())
        best_q, best_err = warm_q, warm_err
        if warm_err > 0.05:
            for seed in self.IK_SEEDS:
                q, ferr = solve_from(seed)
                if ferr < 0.4 * best_err:
                    best_err, best_q = ferr, q
        self.data.qpos[:] = saved_qpos
        mujoco.mj_forward(self.model, self.data)
        return best_q

    def handle_at(self, door_rad: float) -> np.ndarray:
        """Handle site position if the door were at `door_rad` (scratch FK)."""
        saved = self.data.qpos.copy()
        self.data.qpos[self.adr("door")] = door_rad
        mujoco.mj_forward(self.model, self.data)
        pos = self.data.site_xpos[self.handle_site].copy()
        self.data.qpos[:] = saved
        mujoco.mj_forward(self.model, self.data)
        return pos

    def ik_error(self, q: np.ndarray, target: np.ndarray) -> float:
        """FK position error of an arm config against a target (state-restoring)."""
        saved = self.data.qpos.copy()
        cols = [self.model.jnt_dofadr[self.j[k]] for k in ("yaw", "shoulder", "elbow", "wrist")]
        for i in range(4):
            self.data.qpos[cols[i]] = q[i]
        mujoco.mj_forward(self.model, self.data)
        err = float(np.linalg.norm(target - self.data.site_xpos[self.ee_site]))
        self.data.qpos[:] = saved
        mujoco.mj_forward(self.model, self.data)
        return err

    def solve_ik_warm(self, q_from: np.ndarray, target: np.ndarray, *, iters: int = 80) -> np.ndarray:
        """Warm-started position IK from a given configuration (continuity)."""
        cols = [self.model.jnt_dofadr[self.j[k]] for k in ("yaw", "shoulder", "elbow", "wrist")]
        saved = self.data.qpos.copy()
        saved_ctrl = self.data.ctrl.copy()
        for i in range(4):
            self.data.qpos[cols[i]] = q_from[i]
        mujoco.mj_forward(self.model, self.data)
        q = self.solve_ik(target, None, iters=iters)
        self.data.qpos[:] = saved
        self.data.ctrl[:] = saved_ctrl
        mujoco.mj_forward(self.model, self.data)
        return q

    def solve_ik_checked(self, target: np.ndarray, pitch: float | None, *, iters: int = 120, allow_handle_contact: bool = False) -> tuple[np.ndarray, float]:
        """Planning-time IK: all branch seeds compete; solutions whose final
        configuration collides with the world are heavily penalized. Returns
        (q, position error) — the caller judges feasibility."""
        saved = self.data.qpos.copy()
        saved_q = self.arm_qpos()
        best_q, best_score, best_err = None, np.inf, np.inf
        for seed in self.IK_SEEDS:
            for i, k in enumerate(("yaw", "shoulder", "elbow", "wrist")):
                self.data.qpos[self.adr(k)] = seed[i]
            self.set_arm(seed)
            mujoco.mj_forward(self.model, self.data)
            q = self.solve_ik(target, pitch, iters=iters)
            for i, k in enumerate(("yaw", "shoulder", "elbow", "wrist")):
                self.data.qpos[self.adr(k)] = q[i]
            mujoco.mj_forward(self.model, self.data)
            ferr = float(np.linalg.norm(target - self.data.site_xpos[self.ee_site]))
            ncon = self.config_contacts(q)
            if allow_handle_contact:
                # subtract the intended handle contacts from the penalty
                ncon = max(0, ncon - 2)
            score = ferr + 0.5 * ncon
            if score < best_score:
                best_score, best_err, best_q = score, ferr, q.copy()
        self.data.qpos[:] = saved
        self.set_arm(saved_q)
        mujoco.mj_forward(self.model, self.data)
        return best_q, best_err

    # -- contacts ----------------------------------------------------------
    def contacts(self) -> tuple[bool, float, int, str | None]:
        """(handle_contact, handle_force_N, other_collisions, other_geom_name)."""
        handle_hit, force, others, other_name = False, 0.0, 0, None

        def is_robot(g: str) -> bool:
            return g.startswith(("finger_", "palm", "upper_arm", "forearm", "wrist_stub"))

        for i in range(self.data.ncon):
            con = self.data.contact[i]
            # robot geoms carry a 2 cm planning margin: a contact record exists
            # for near misses; only real engagement (penetration) counts here
            if con.dist > 0.0:
                continue
            g1 = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, con.geom1) or ""
            g2 = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, con.geom2) or ""
            pair = {g1, g2}
            if "handle" in pair and any(is_robot(g) for g in pair):
                handle_hit = True
                f = np.zeros(6)
                mujoco.mj_contactForce(self.model, self.data, i, f)
                force = max(force, float(np.linalg.norm(f[:3])))
            else:
                rest = [g for g in pair if not is_robot(g) and g != "floor_geom"]
                if any(is_robot(g) for g in pair) and rest and "handle_mount" not in pair:
                    others += 1
                    other_name = sorted(rest)[0]
        return handle_hit, force, others, other_name

    def step(self) -> None:
        mujoco.mj_step(self.model, self.data)

    def observe(self) -> np.ndarray:
        hp = self.handle_pos()
        return np.array([*self.arm_qpos(), self.grip(), self.door_rad(), *self.ee_pos(), *hp], dtype=np.float32)

    # -- planning-time collision queries (scratch FK, state restored) -------
    def config_contacts(self, q: np.ndarray) -> int:
        """Robot-vs-world contact count at arm config q (scratch evaluation)."""
        saved = self.data.qpos.copy()
        for i, k in enumerate(("yaw", "shoulder", "elbow", "wrist")):
            self.data.qpos[self.adr(k)] = q[i]
        mujoco.mj_forward(self.model, self.data)

        def is_robot(g: str) -> bool:
            return g.startswith(("finger_", "palm", "upper_arm", "forearm", "wrist_", "shoulder_", "elbow_", "robot_"))

        # base/mast legitimately rest on the floor; arm links never may
        floor_ok = ("robot_base", "robot_mast")
        n = 0
        for i in range(self.data.ncon):
            con = self.data.contact[i]
            g1 = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, con.geom1) or ""
            g2 = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, con.geom2) or ""
            pair = {g1, g2}
            if not any(is_robot(g) for g in pair) or all(is_robot(g) for g in pair):
                continue
            if "floor_geom" in pair and any(g in floor_ok for g in pair):
                continue
            n += 1
        self.data.qpos[:] = saved
        mujoco.mj_forward(self.model, self.data)
        return n

    def path_clear(self, q0: np.ndarray, q1: np.ndarray, *, step: float = 0.12, allow_handle: bool = False) -> bool:
        n = max(2, int(np.max(np.abs(q1 - q0)) / step) + 1)
        for a in np.linspace(0, 1, n):
            q = q0 + a * (q1 - q0)
            if allow_handle:
                saved = self.data.qpos.copy()
                for i, k in enumerate(("yaw", "shoulder", "elbow", "wrist")):
                    self.data.qpos[self.adr(k)] = q[i]
                mujoco.mj_forward(self.model, self.data)
                bad = 0
                for i in range(self.data.ncon):
                    con = self.data.contact[i]
                    g1 = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, con.geom1) or ""
                    g2 = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, con.geom2) or ""
                    pair = {g1, g2}
                    if "handle" in pair or "handle_mount" in pair:
                        continue
                    robot_hit = [g for g in pair if g.startswith(("finger_", "palm", "upper_arm", "forearm", "wrist_", "shoulder_", "elbow_", "robot_"))]
                    if not robot_hit:
                        continue
                    if "floor_geom" in pair and all(g in ("robot_base", "robot_mast", "floor_geom") for g in pair):
                        continue
                    # caging inherently grazes: only genuine penetration blocks
                    if con.dist < -0.004:
                        bad += 1
                self.data.qpos[:] = saved
                mujoco.mj_forward(self.model, self.data)
                if bad > 0:
                    return False
            elif self.config_contacts(q) > 0:
                return False
        return True


class ScriptedController:
    """Waypoint-driven expert: plans joint-space waypoints offline (the world
    geometry is known), tracks them at a bounded joint rate, and transitions
    on physical gates (reached / contact verified).

    Phases: travel -> approach -> cage -> close (verify + attach) -> pull
    (joint-space tracking of the door's own arc via the weld) -> release ->
    retract. Failure to reach the handle within the budget aborts to retract
    and is classified downstream as no_contact / no_grasp — real failures.
    """

    JOINT_RATE = 0.7  # rad/s target tracking speed (keeps actuator overshoot honest)

    SAFE = np.array([1.57, 0.50, -0.60, 0.60])  # tucked transit pose, clear of the appliance

    def __init__(self, world: World):
        self.w = world
        handle = world.handle_pos()
        self.q_home = HOME.copy()
        # position-only IK (posture-regularized): the pads clamp the bar in
        # whatever orientation the arm reaches — the multi-site grasp connect
        # then anchors the real contact point
        self.q_cage, err_c = world.solve_ik_checked(handle + np.array([0.0, 0.0, 0.002]), None, allow_handle_contact=True)
        # stand-off pose 15 cm out from the handle — free pitch in free space
        self.q_approach, err_a = world.solve_ik_checked(handle + np.array([0.0, 0.0, 0.15]), None)
        self.plan_error = float(max(err_a, err_c))
        self.pull_target = min(world.door_max * 0.92, math.radians(100))
        self.pull_path: list[np.ndarray] = []  # planned lazily at attach (needs the anchor)
        # collision-checked waypoint path home -> SAFE -> approach -> cage
        self.q_path = self._plan_path(world, [self.q_home, self.SAFE, self.q_approach, self.q_cage])
        self.path_blocked = len(self.q_path) == 0
        self.grasp_verified = False
        self.phase = "retract" if (self.plan_error > 0.06 or self.path_blocked) else "move"
        self.phase_t = 0.0
        self.wp_idx = 1
        self.pull_idx = 0
        self._lost_t = 0.0
        self._ref = self.q_home.copy()

    def _set(self, phase: str) -> None:
        self.phase = phase
        self.phase_t = 0.0
        if phase in ("press", "close", "pull"):
            # freeze the reference at the current physical pose for these phases
            self._ref = self.w.arm_qpos().copy()

    def _plan_pull(self) -> None:
        """Joint-space waypoints for the pull along the door's arc: the grasp
        ball-joint ties the ee site to the handle site, so the hand target at
        door angle θ is exactly handle_site(θ). Warm-start for continuity,
        all-seed fallback, honest truncation when genuinely out of reach."""
        w = self.w
        self.pull_path = [w.arm_qpos()]
        q = w.arm_qpos()
        offset = w.grasp_site_offset if w.grasp_site_offset is not None else np.zeros(3)
        for frac in (0.15, 0.3, 0.5, 0.75, 1.0):
            hand_tgt = w.handle_at(self.pull_target * frac) - offset
            q_next = w.solve_ik_warm(q, hand_tgt)
            err = w.ik_error(q_next, hand_tgt)
            if err > 0.05:
                q_chk, err2 = w.solve_ik_checked(hand_tgt, None)
                if err2 > 0.05:
                    break  # arm reaches no further — the pull ends here, honestly
                q_next, err = q_chk, err2
            self.pull_path.append(q_next)
            q = q_next

    def _plan_path(self, world: World, waypoints: list[np.ndarray]) -> list[np.ndarray]:
        """Chain waypoints; each segment collision-checked. The two terminal
        segments (pre-cage, caging advance) run near the asset by design:
        handle contact is allowed and only genuine penetration blocks.
        Any blocked segment = genuine failure."""
        n = len(waypoints)
        out: list[np.ndarray] = [waypoints[0]]
        for j, target in enumerate(waypoints[1:]):
            terminal = j >= n - 3  # last two segments
            if not world.path_clear(out[-1], target, allow_handle=terminal):
                return []
            out.append(target)
        return out

    def _track(self, q_target: np.ndarray, rate: float | None = None) -> tuple[np.ndarray, bool]:
        """Rate-limited reference tracking; returns (ctrl, reached).

        The internal reference advances toward the target at the given joint
        rate regardless of how much the physical joints lag — so the position
        actuators develop their full authority against load/contact instead
        of being capped at kp*step by the lag itself."""
        w = self.w
        step = (rate or self.JOINT_RATE) * DT_CTRL
        self._ref = self._ref + np.clip(q_target - self._ref, -step, step)
        reached = bool(np.max(np.abs(q_target - self._ref)) < 0.03)
        return self._ref.copy(), reached

    def act(self, dt: float) -> tuple[np.ndarray, float, bool]:
        self.phase_t += dt
        w = self.w
        grip = 0.0
        if self.phase == "move":
            last = self.wp_idx == len(self.q_path) - 1
            q, done = self._track(self.q_path[self.wp_idx])
            if last:
                # caging is an insertion: gate on EE proximity to the bar, not
                # joint error (pad contact resists the last centimeters)
                d = float(np.linalg.norm(w.ee_pos() - w.handle_pos()))
                done = done or d < 0.055
            if done:
                self.wp_idx += 1
                self.phase_t = 0.0
                if self.wp_idx >= len(self.q_path):
                    self._set("press")
            elif self.phase_t > 8.0:
                self._set("retract")  # path tracking stalled (contact blocked)
        elif self.phase == "press":
            # compliant press-in: creep the site THROUGH the bar along the
            # current approach direction so it seats deep between the pads
            handle = w.handle_pos()
            ee = w.ee_pos()
            direction = handle - ee
            n = float(np.linalg.norm(direction))
            direction = direction / n if n > 1e-6 else np.array([0.0, 0.0, -1.0])
            press_target = handle + direction * 0.035  # 3.5 cm past the bar
            q_ik = w.solve_ik(press_target, None)
            step = 0.35 * dt  # slow creep
            self._ref = self._ref + np.clip(q_ik - self._ref, -step, step)
            q = self._ref.copy()
            d = float(np.linalg.norm(w.ee_pos() - handle))
            if d < 0.045:
                self._set("close")
            elif self.phase_t > 2.5:
                self._set("close")  # judge with what we have
        elif self.phase == "close":
            grip = 1.0
            q, _ = self._track(self.q_cage)
            # verification: fingers close onto the bar -> joint stalls early
            # with sustained contact force; air-closing reaches ~fully closed
            if self.phase_t > 1.1:
                hit, force, _, _ = w.contacts()
                dmin = min(w.grasp_distances().values())
                # pads on the bar (grip stalled mid-range) + a grasp site near
                # the bar -> attach; the compliant connect seats the last cm
                if 0.12 < w.grip() < 0.92 and hit and dmin < 0.07:
                    w.attach()
                    self.grasp_verified = True
                    self._plan_pull()
                    self._set("pull")
                elif self.phase_t > 2.8:
                    self._set("release")  # no verified grasp — do not grind the door
        elif self.phase == "pull":
            grip = 1.0
            if self.pull_path:
                q, reached = self._track(self.pull_path[self.pull_idx], rate=1.1)
                track_err = float(np.max(np.abs(q - w.arm_qpos())))
                if track_err > 0.55:
                    self._lost_t += dt  # actuator vs weld fight — tracking lost
                else:
                    self._lost_t = 0.0
                if self._lost_t > 1.5:
                    self._set("release")  # drop and back out — real abort
                elif reached and self.pull_idx < len(self.pull_path) - 1:
                    self.pull_idx += 1
                    self._lost_t = 0.0
            else:
                q = w.arm_qpos()
            if w.door_rad() >= DOOR_SUCCESS_RAD or self.phase_t > 14.0 or (
                self.pull_idx >= len(self.pull_path) - 1 and w.door_rad() > 0.9 * self.pull_target
            ):
                self._set("release")
        elif self.phase == "release":
            if w.attached:
                w.detach()
            grip = 0.0
            if self.phase_t < 0.6 and w.grip() > 0.2:
                # open the fingers fully BEFORE moving — a still-clamped hand
                # retracting drags the door back shut
                q = w.arm_qpos()
                done = False
            else:
                # back out via the tucked SAFE pose — up and away from the
                # now-open door (the approach waypoint sits inside its sweep)
                q, done = self._track(self.SAFE)
            if done or self.phase_t > 3.5:
                self._set("retract")
        else:  # retract
            q, done = self._track(self.q_home)
            if done or self.phase_t > 4.0:
                return q, 0.0, True
        return q, grip, False


class PolicyController:
    """Trained BC policy: obs(12) -> action(5) = [d_arm(4), grip_target]."""

    def __init__(self, world: World, model, clip: float = 0.06):
        self.w = world
        self.model = model
        self.clip = clip
        self.t = 0.0
        self.done_after = 12.0

    def act(self, dt: float) -> tuple[np.ndarray, float, bool]:
        import torch

        self.t += dt
        obs = torch.from_numpy(self.w.observe()).unsqueeze(0)
        with torch.no_grad():
            a = self.model(obs).numpy()[0]
        q = self.w.arm_qpos() + np.clip(a[:4], -self.clip, self.clip)
        q[0] = np.clip(q[0], -3.0, 3.0)
        q[1] = np.clip(q[1], 0.05, 1.9)
        q[2] = np.clip(q[2], -2.8, 0.2)
        q[3] = np.clip(q[3], -1.5, 2.2)
        grip = float(np.clip(a[4], 0.0, 1.0))
        return q, grip, self.t > self.done_after


def run_rollout(
    world: World,
    controller_fn: Callable[[World], Any],
    *,
    max_s: float = 20.0,
    on_frame: Callable[[dict], bool] | None = None,
    frame_hz: float = 20.0,
    record: bool = True,
    real_time: bool = False,
    should_stop: Callable[[], bool] | None = None,
) -> RolloutResult:
    """Run one episode. `controller_fn(world)` is created AFTER reset, so any
    planning it does at construction sees valid geometry.
    `on_frame(frame_dict) -> continue` receives ~20 Hz UI frames."""
    import time as _time

    world.reset()
    controller = controller_fn(world)
    obs_buf: list[np.ndarray] = []
    act_buf: list[np.ndarray] = []
    frames: list[dict] = []
    collisions_total = 0
    door_peak = 0.0
    contact_ever = False
    grasp_max_angle = 0.0
    grasped_once = False
    t = 0.0
    steps_per_frame = max(1, int((1.0 / frame_hz) / DT_CTRL))
    step_i = 0
    done = False
    wall0 = _time.time()

    while t < max_s and not done:
        if should_stop and should_stop():
            break
        obs_now = world.observe()
        q, grip, done = controller.act(DT_CTRL)
        if record:
            obs_buf.append(obs_now)
            act_buf.append(np.array([*(q - world.arm_qpos()), grip], dtype=np.float32))
        world.set_arm(q)
        world.set_grip(grip)
        # integrate physics for one control step
        n_sub = max(1, int(DT_CTRL / world.model.opt.timestep))
        for _ in range(n_sub):
            world.step()
        t += DT_CTRL
        step_i += 1

        hit, force, others, _other = world.contacts()
        collisions_total += others
        door_peak = max(door_peak, world.door_rad())
        contact_ever = contact_ever or hit
        if hit and world.grip() > 0.5:
            grasped_once = True
            grasp_max_angle = max(grasp_max_angle, world.door_rad())

        if on_frame and step_i % steps_per_frame == 0:
            fr = {
                "t": round(t, 3),
                "pose": {
                    "yaw": world.qpos("yaw"),
                    "shoulder": world.qpos("shoulder"),
                    "elbow": world.qpos("elbow"),
                    "wrist": world.qpos("wrist"),
                    "grip": world.grip(),
                },
                "door": world.door_rad() / world.door_max,
                "doorAngleDeg": math.degrees(world.door_rad()),
                "gripper": "closed" if world.grip() > 0.5 else "open",
                "forceN": round(force, 2),
                "inContact": hit,
                "collisions": collisions_total,
            }
            frames.append(fr)
            if not on_frame(fr):
                break
        if real_time:
            target_wall = wall0 + t
            now = _time.time()
            if target_wall > now:
                _time.sleep(target_wall - now)

    door_deg = math.degrees(world.door_rad())
    peak_deg = math.degrees(door_peak)
    success = world.door_rad() >= DOOR_SUCCESS_RAD
    failure_mode, failure_detail = None, None
    if not success:
        if getattr(controller, "path_blocked", False):
            failure_mode, failure_detail = "path_blocked", "No collision-free joint-space path to the handle exists."
        elif getattr(controller, "plan_error", 0.0) > 0.06 and not contact_ever:
            failure_mode = "plan_infeasible"
            failure_detail = f"IK planning could not reach the handle within {getattr(controller, 'plan_error', 0.0):.2f} m — geometry out of workspace."
        elif not contact_ever:
            failure_mode, failure_detail = "no_contact", "Gripper never reached the handle within the approach budget."
        elif not getattr(controller, "grasp_verified", grasped_once):
            failure_mode, failure_detail = "no_grasp", "Handle contacted but a firm grasp was never established."
        elif collisions_total > 400:
            failure_mode, failure_detail = "collision", f"{collisions_total} unintended contacts during the episode."
        elif peak_deg >= 60:
            failure_mode, failure_detail = (
                "drop_early",
                f"Door opened to {peak_deg:.0f} deg but fell back to {door_deg:.0f} deg before settling — released too early.",
            )
        elif peak_deg >= 15:
            failure_mode, failure_detail = (
                "insufficient_pull",
                f"Grasped but the door only reached {peak_deg:.0f} deg — hinge resistance exceeds actuator authority.",
            )
        else:
            failure_mode, failure_detail = "timeout", f"Door reached {door_deg:.0f} deg of the required 60 deg before the episode ended."

    return RolloutResult(
        success=success,
        door_peak_deg=math.degrees(door_peak),
        door_angle_deg=door_deg,
        collisions=collisions_total,
        duration_s=t,
        failure_mode=failure_mode,
        failure_detail=failure_detail,
        obs=np.asarray(obs_buf, dtype=np.float32) if obs_buf else np.zeros((0, 12), dtype=np.float32),
        act=np.asarray(act_buf, dtype=np.float32) if act_buf else np.zeros((0, 5), dtype=np.float32),
        frames=frames,
    )


def default_scenario_family(rng: np.random.Generator | None = None) -> dict[str, Any]:
    """Nominal refrigerator-door scenario with mild domain randomization."""
    rng = rng or np.random.default_rng()
    return {
        "door_mass": float(rng.uniform(9.0, 16.0)),
        "hinge_friction": float(rng.uniform(1.5, 5.0)),
        # nominal band: at/above the shoulder. Below-shoulder handles (~<1.0 m)
        # are a genuine coverage gap for this robot — the agent should find it.
        "handle_height": float(rng.uniform(1.0, 1.25)),
        "handle_orientation": "vertical",
        "max_open_deg": 110.0,
        # standoff: the compact arm (0.915 m) works at ~2/3 extension
        "robot_base": (0.68, 1.05),
    }


FRIDGE_SPEC: dict[str, Any] = {
    "pos": [0.55, 0.0],
    "width": 0.7,
    "height": 1.75,
    "depth": 0.7,
    "door_width": 0.35,
    "door_mass": 12.0,
    "hinge_side": "left",
    "hinge_friction": 2.5,
    "hinge_damping": 1.2,
    "max_open_deg": 110.0,
    "handle": {"height": 1.05, "orientation": "vertical", "offset_from_edge": 0.06, "protrude": 0.09},
}
