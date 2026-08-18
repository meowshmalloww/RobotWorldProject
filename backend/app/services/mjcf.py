"""MJCF compiler — turns scenario parameters + asset specs into MuJoCo XML.

World convention matches the frontend 3D scene 1:1 (Y-up, meters), so joint
values stream straight into the React viewport without conversion:
  yaw      -> hinge axis (0, 1, 0)
  shoulder -> hinge axis (0, 0,-1)   (frontend rotation.z = -shoulder)
  elbow    -> hinge axis (0, 0,-1)
  wrist    -> hinge axis (0, 0,-1)
  grip     -> two slide joints, qpos = 0.02 * grip
  door     -> hinge axis (0,-1, 0),  qpos 0..max_open
Gravity is applied as (0, -9.81, 0) — MuJoCo has no fixed up-axis.
"""
from __future__ import annotations

import math
from typing import Any

ARM = {
    "mast_h": 1.02,  # shoulder at mid-torso height — handles sit inside the workspace
    "upper_len": 0.30,  # arm sized for 0.4–0.7 m manipulation work (no deep folds)
    "fore_len": 0.26,
    "wrist_len": 0.12,
}

# Arm position-actuator torque limits (N·m). Heavy doors genuinely beat these —
# that is what produces real failures on out-of-coverage scenarios.
ARM_FORCE = {"yaw": 25.0, "shoulder": 32.0, "elbow": 22.0, "wrist": 10.0, "finger": 60.0}


def robot_xml(base: tuple[float, float] = (0.55, 0.55)) -> str:
    bx, bz = base
    m = ARM
    return f"""
  <body name="robot" pos="{bx} 0 {bz}">
    <geom name="robot_base" class="robot" type="cylinder" size="0.25 0.14" pos="0 0.14 0" euler="90 0 0" mass="18" rgba="0.16 0.18 0.22 1" contype="1" conaffinity="1"/>
    <geom name="robot_mast" class="robot" type="cylinder" size="0.08 {(m['mast_h'] - 0.38)/2:.3f}" pos="0 {0.30 + (m['mast_h'] - 0.38)/2:.3f} 0" euler="90 0 0" mass="4" rgba="0.75 0.78 0.83 1"/>
    <body name="yaw_link" pos="0 {m['mast_h']} 0">
      <joint name="j_yaw" type="hinge" axis="0 1 0" range="-3.0 3.0" damping="2.5"/>
      <geom name="shoulder_housing" class="robot" type="sphere" size="0.085" mass="1.2" rgba="0.35 0.38 0.44 1"/>
      <body name="shoulder_link" pos="0 0 0">
        <joint name="j_shoulder" type="hinge" axis="0 0 -1" range="0.05 1.9" damping="2.0"/>
        <geom name="upper_arm" class="robot" type="capsule" size="0.055 {m['upper_len']/2:.3f}" pos="0 {m['upper_len']/2:.3f} 0" euler="90 0 0" mass="1.6" rgba="0.8 0.83 0.88 1"/>
        <body name="elbow_link" pos="0 {m['upper_len']} 0">
          <joint name="j_elbow" type="hinge" axis="0 0 -1" range="-2.8 0.2" damping="1.5"/>
          <geom name="elbow_housing" class="robot" type="sphere" size="0.07" mass="0.8" rgba="0.35 0.38 0.44 1"/>
          <geom name="forearm" class="robot" type="capsule" size="0.046 {m['fore_len']/2:.3f}" pos="0 {m['fore_len']/2:.3f} 0" euler="90 0 0" mass="1.1" rgba="0.8 0.83 0.88 1"/>
          <body name="wrist_link" pos="0 {m['fore_len']} 0">
            <joint name="j_wrist" type="hinge" axis="0 0 -1" range="-1.5 2.2" damping="0.8"/>
            <geom name="wrist_housing" class="robot" type="sphere" size="0.052" mass="0.4" rgba="0.35 0.38 0.44 1"/>
            <geom name="wrist_stub" class="robot" type="capsule" size="0.032 {m['wrist_len']/2:.3f}" pos="0 {m['wrist_len']/2:.3f} 0" euler="90 0 0" mass="0.3" rgba="0.8 0.83 0.88 1"/>
            <body name="hand" pos="0 {m['wrist_len']} 0">
              <geom name="palm" class="robot" type="box" size="0.045 0.0225 0.035" mass="0.25" rgba="0.35 0.38 0.44 1"/>
              <!-- grasp sites along the finger length: root / mid(ee) / tip —
                   attach picks whichever coincides with the bar -->
              <site name="ee" pos="0 0.075 0" size="0.008" rgba="1 0 0 0"/>
              <site name="grasp_root" pos="0 0.03 0" size="0.008" rgba="1 0 0 0"/>
              <site name="grasp_tip" pos="0 0.125 0" size="0.008" rgba="1 0 0 0"/>
              <body name="finger_l" pos="-0.05 0.055 0">
                <joint name="j_finger_l" type="slide" axis="1 0 0" range="0 0.04" damping="4.0"/>
                <geom name="finger_l_pad" class="robot" type="box" size="0.007 0.047 0.045" pos="0 0.02 -0.01" mass="0.05" friction="1.6 0.1 0.001" rgba="0.2 0.21 0.24 1"/>
                <!-- hooked fingertips: the handle cannot slide out along the jaw -->
                <geom name="finger_l_hook_a" class="robot" type="box" size="0.007 0.047 0.009" pos="0.006 0.02 -0.055" mass="0.01" friction="1.4 0.1 0.001" rgba="0.2 0.21 0.24 1"/>
                <geom name="finger_l_hook_b" class="robot" type="box" size="0.007 0.047 0.009" pos="0.006 0.02 0.035" mass="0.01" friction="1.4 0.1 0.001" rgba="0.2 0.21 0.24 1"/>
              </body>
              <body name="finger_r" pos="0.05 0.055 0">
                <joint name="j_finger_r" type="slide" axis="-1 0 0" range="0 0.04" damping="4.0"/>
                <geom name="finger_r_pad" class="robot" type="box" size="0.007 0.047 0.045" pos="0 0.02 -0.01" mass="0.05" friction="1.6 0.1 0.001" rgba="0.2 0.21 0.24 1"/>
                <geom name="finger_r_hook_a" class="robot" type="box" size="0.007 0.047 0.009" pos="-0.006 0.02 -0.055" mass="0.01" friction="1.4 0.1 0.001" rgba="0.2 0.21 0.24 1"/>
                <geom name="finger_r_hook_b" class="robot" type="box" size="0.007 0.047 0.009" pos="-0.006 0.02 0.035" mass="0.01" friction="1.4 0.1 0.001" rgba="0.2 0.21 0.24 1"/>
              </body>
            </body>
          </body>
        </body>
      </body>
    </body>
  </body>
"""


def _handle_xml(h: dict[str, Any], door_w: float, door_t: float, door_cy: float) -> str:
    """Handle geometry in door-local coordinates (door hinge at x=0, panel +X)."""
    hy = h.get("height", 1.05) - door_cy
    hx = door_w - h.get("offset_from_edge", 0.06)
    protrude = h.get("protrude", 0.045)
    orient = h.get("orientation", "vertical")
    mount_z = door_t / 2 + protrude / 2  # spans from the panel face to the bar
    if orient == "horizontal":
        # capsule default axis is local Z; euler 0 90 0 lays it along door-local X
        return f"""
      <geom name="handle_mount" type="cylinder" size="0.012 {protrude/2 + 0.02:.3f}" pos="{hx - 0.09:.3f} {hy:.3f} {mount_z:.3f}" mass="0.15" rgba="0.62 0.64 0.68 1"/>
      <geom name="handle" type="capsule" size="0.014 0.09" pos="{hx - 0.09:.3f} {hy:.3f} {door_t/2 + protrude:.3f}" euler="0 90 0" mass="0.35" friction="1.1 0.05 0.001" rgba="0.66 0.68 0.72 1"/>
      <site name="handle_site" pos="{hx - 0.09:.3f} {hy:.3f} {door_t/2 + protrude:.3f}" size="0.006" rgba="0 1 0 0"/>"""
    # vertical: euler 90 0 0 stands the capsule along door-local Y
    return f"""
      <geom name="handle_mount" type="cylinder" size="0.012 {protrude/2 + 0.02:.3f}" pos="{hx:.3f} {hy + 0.07:.3f} {mount_z:.3f}" mass="0.15" rgba="0.62 0.64 0.68 1"/>
      <geom name="handle" type="capsule" size="0.014 0.085" pos="{hx:.3f} {hy:.3f} {door_t/2 + protrude:.3f}" euler="90 0 0" mass="0.35" friction="1.1 0.05 0.001" rgba="0.66 0.68 0.72 1"/>
      <site name="handle_site" pos="{hx:.3f} {hy:.3f} {door_t/2 + protrude:.3f}" size="0.006" rgba="0 1 0 0"/>"""


def door_asset_xml(spec: dict[str, Any]) -> str:
    """A hinged-door appliance (refrigerator/cabinet) from a physical spec.

    spec keys: pos [x, z], width, height, depth, door_width, door_mass,
    hinge_side, hinge_friction (N·m), hinge_damping, max_open_deg,
    handle {height, orientation, offset_from_edge, protrude}
    """
    w = float(spec.get("width", 0.7))
    h = float(spec.get("height", 1.7))
    d = float(spec.get("depth", 0.65))
    fx, fz = spec.get("pos", [0.0, 0.0])
    door_w = float(spec.get("door_width", w * 0.5))
    door_t = 0.045
    door_mass = float(spec.get("door_mass", 12.0))
    friction = float(spec.get("hinge_friction", 2.5))
    damping = float(spec.get("hinge_damping", 1.2))
    max_open = math.radians(float(spec.get("max_open_deg", 110.0)))
    hinge_side = spec.get("hinge_side", "left")
    door_cy = h * 0.62

    front_z = d / 2
    hinge_x = -w / 2 if hinge_side == "left" else w / 2 - door_w
    panel_cx = door_w / 2  # panel extends +X from hinge
    handle = spec.get("handle", {})
    handle_x = float(handle.get("offset_from_edge", 0.06))
    handle_xml = _handle_xml(handle, door_w, door_t, door_cy)

    return f"""
  <body name="appliance" pos="{fx} 0 {fz}">
    <geom name="appliance_body" type="box" size="{w/2:.3f} {h/2:.3f} {d/2:.3f}" pos="0 {h/2:.3f} 0" mass="55" rgba="0.78 0.8 0.84 1" contype="1" conaffinity="1"/>
    <body name="door" pos="{hinge_x:.3f} {door_cy:.3f} {front_z + door_t/2 + 0.008:.3f}">
      <joint name="j_door" type="hinge" axis="0 -1 0" range="0 {max_open:.3f}" frictionloss="{friction:.3f}" damping="{damping:.3f}"/>
      <geom name="door_panel" type="box" size="{door_w/2:.3f} {h*0.34:.3f} {door_t/2:.3f}" pos="{panel_cx:.3f} 0 0" mass="{door_mass:.3f}" rgba="0.82 0.84 0.88 1" friction="0.6 0.05 0.001"/>
      {handle_xml}
    </body>
  </body>
"""


def build_world(scenario: dict[str, Any], asset_spec: dict[str, Any] | None = None) -> str:
    """Full MJCF world: floor + walls + robot + articulated door asset.

    scenario params (domain randomization): door_mass, hinge_friction,
    handle_height, handle_orientation, max_open_deg, robot_base.
    """
    spec = dict(asset_spec or {})
    spec["door_mass"] = scenario.get("door_mass", spec.get("door_mass", 12.0))
    spec["hinge_friction"] = scenario.get("hinge_friction", spec.get("hinge_friction", 2.5))
    spec.setdefault("handle", {})
    spec["handle"] = dict(spec["handle"])
    if "handle_height" in scenario:
        spec["handle"]["height"] = scenario["handle_height"]
    if "handle_orientation" in scenario:
        spec["handle"]["orientation"] = scenario["handle_orientation"]
    if "max_open_deg" in scenario:
        spec["max_open_deg"] = scenario["max_open_deg"]
    spec.setdefault("pos", [0.0, 0.0])
    robot_base = tuple(scenario.get("robot_base", (0.55, 0.75)))

    return f"""<mujoco model="robotworld_eval">
  <compiler angle="radian" coordinate="local"/>
  <option gravity="0 -9.81 0" timestep="0.002" solver="Newton" iterations="60">
    <flag warmstart="enable"/>
  </option>
  <default>
    <geom contype="1" conaffinity="1" condim="3" solimp="0.9 0.95 0.001" solref="0.02 1"/>
    <default class="robot">
      <geom margin="0.02"/>
    </default>
  </default>
  <worldbody>
    <!-- front-right elevated view of the appliance (Y-up world; camera looks along -Z of its frame) -->
    <camera name="debug" pos="1.7 1.5 1.75" xyaxes="0.672 0 -0.740 -0.198 0.934 -0.180" fovy="50"/>
    <body name="floor" pos="0 0 0">
      <geom name="floor_geom" type="plane" size="6 6 0.1" euler="-90 0 0" friction="0.9 0.05 0.001" rgba="0.22 0.23 0.26 1" contype="1" conaffinity="1"/>
    </body>
    {robot_xml(robot_base)}
    {door_asset_xml(spec)}
  </worldbody>
  <equality>
    <!-- grasp-assist ball joint between the gripper's ee site (between the
         pads) and the handle site: position-locked, rotation-free — exactly
         what a pinch on a vertical bar permits. Activated at runtime only
         after verified finger contact + closed gripper (sticky-grasp
         convention, as in the MuJoCo attach/detach tutorial / UMPNet). -->
    <connect name="grasp_eq" site1="ee" site2="handle_site" active="false" solref="0.01 1"/>
    <connect name="grasp_eq_root" site1="grasp_root" site2="handle_site" active="false" solref="0.01 1"/>
    <connect name="grasp_eq_tip" site1="grasp_tip" site2="handle_site" active="false" solref="0.01 1"/>
  </equality>
  <contact>
    <!-- MuJoCo does not auto-exclude pairs connected by actuated joints -->
    <exclude body1="robot" body2="yaw_link"/>
    <exclude body1="robot" body2="shoulder_link"/>
    <exclude body1="robot" body2="elbow_link"/>
    <exclude body1="robot" body2="wrist_link"/>
    <exclude body1="robot" body2="hand"/>
    <exclude body1="yaw_link" body2="elbow_link"/>
    <exclude body1="yaw_link" body2="wrist_link"/>
    <exclude body1="yaw_link" body2="hand"/>
    <exclude body1="shoulder_link" body2="wrist_link"/>
    <exclude body1="shoulder_link" body2="hand"/>
    <exclude body1="elbow_link" body2="hand"/>
  </contact>
  <actuator>
    <position name="a_yaw" joint="j_yaw" kp="220" forcerange="-{ARM_FORCE['yaw']} {ARM_FORCE['yaw']}" ctrlrange="-3.0 3.0"/>
    <position name="a_shoulder" joint="j_shoulder" kp="260" forcerange="-{ARM_FORCE['shoulder']} {ARM_FORCE['shoulder']}" ctrlrange="0.05 1.9"/>
    <position name="a_elbow" joint="j_elbow" kp="200" forcerange="-{ARM_FORCE['elbow']} {ARM_FORCE['elbow']}" ctrlrange="-2.8 0.2"/>
    <position name="a_wrist" joint="j_wrist" kp="90" forcerange="-{ARM_FORCE['wrist']} {ARM_FORCE['wrist']}" ctrlrange="-1.5 2.2"/>
    <position name="a_finger_l" joint="j_finger_l" kp="2400" forcerange="-{ARM_FORCE['finger']} {ARM_FORCE['finger']}" ctrlrange="0 0.04"/>
    <position name="a_finger_r" joint="j_finger_r" kp="2400" forcerange="-{ARM_FORCE['finger']} {ARM_FORCE['finger']}" ctrlrange="0 0.04"/>
  </actuator>
</mujoco>"""
