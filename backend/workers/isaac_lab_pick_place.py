"""Run one bounded Franka pick/place episode in NVIDIA Isaac Sim/Isaac Lab.

This process must be launched with RobotWorld's isolated Isaac Python 3.12
environment, never the API server environment.  The controller follows the
same absolute task-space action contract used by Isaac Lab's BSD-3-Clause
``scripts/environments/state_machine/lift_cube_sm.py`` example, extended with
transport, release, real contact evidence, two RGB sensors, and a terminal
pick/place predicate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from isaaclab.app import AppLauncher


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RobotWorld Isaac Lab Franka pick/place oracle")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=6203)
    parser.add_argument("--max-steps", type=int, default=1200)
    AppLauncher.add_app_launcher_args(parser)
    return parser.parse_args()


ARGS = _arguments()
APP_LAUNCHER = AppLauncher(ARGS)
SIMULATION_APP = APP_LAUNCHER.app

import gymnasium as gym  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402

import isaaclab.sim as sim_utils  # noqa: E402
import isaaclab_tasks  # noqa: E402, F401
from isaaclab.sensors import CameraCfg, ContactSensorCfg  # noqa: E402
from isaaclab_tasks.utils.parse_cfg import parse_env_cfg  # noqa: E402


SCHEMA_VERSION = "robotworld.isaac-franka-pick-place.v1"
ENVIRONMENT_ID = "Isaac-Lift-Cube-Franka-IK-Abs-v0"
CONTROLLER = "isaaclab-differential-ik-absolute-dls-oracle-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _camera_cfg(*, prim_path: str, position: tuple[float, float, float], rotation: tuple[float, ...]) -> CameraCfg:
    return CameraCfg(
        prim_path=prim_path,
        update_period=0.0,
        height=224,
        width=224,
        data_types=["rgb"],
        spawn=sim_utils.PinholeCameraCfg(
            focal_length=24.0,
            focus_distance=400.0,
            horizontal_aperture=20.955,
            clipping_range=(0.05, 4.0),
        ),
        offset=CameraCfg.OffsetCfg(pos=position, rot=rotation, convention="ros"),
    )


def _save_rgb(camera, output: Path) -> dict[str, object]:
    value = camera.data.output["rgb"]
    value = value.torch if hasattr(value, "torch") else value
    array = value[0].detach().to(device="cpu").numpy()
    if array.dtype != np.uint8:
        upper = float(np.nanmax(array)) if array.size else 0.0
        if upper <= 1.0:
            array = array * 255.0
        array = np.clip(array, 0.0, 255.0).astype(np.uint8)
    if array.shape[-1] == 4:
        array = array[..., :3]
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array, mode="RGB").save(output, format="PNG", optimize=True)
    return {
        "path": str(output.resolve()),
        "sha256": _sha256(output),
        "width": int(array.shape[1]),
        "height": int(array.shape[0]),
    }


def _tensor_list(value: torch.Tensor) -> list[float]:
    return [float(item) for item in value.detach().to(device="cpu").reshape(-1).tolist()]


def _distance(current: torch.Tensor, desired: torch.Tensor) -> float:
    return float(torch.linalg.vector_norm(current - desired).item())


def run() -> dict[str, object]:
    torch.manual_seed(ARGS.seed)
    np.random.seed(ARGS.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(ARGS.seed)

    cfg = parse_env_cfg(ENVIRONMENT_ID, device=ARGS.device, num_envs=1, use_fabric=True)
    cfg.seed = ARGS.seed
    cfg.scene.num_envs = 1
    cfg.scene.env_spacing = 2.5
    cfg.observations.policy.enable_corruption = False
    cfg.episode_length_s = max(30.0, ARGS.max_steps * cfg.sim.dt * cfg.decimation + 2.0)
    cfg.scene.robot.spawn.activate_contact_sensors = True
    cfg.scene.left_finger_contact = ContactSensorCfg(
        prim_path="{ENV_REGEX_NS}/Robot/panda_leftfinger",
        update_period=0.0,
        history_length=4,
        debug_vis=False,
        filter_prim_paths_expr=["{ENV_REGEX_NS}/Object"],
    )
    cfg.scene.right_finger_contact = ContactSensorCfg(
        prim_path="{ENV_REGEX_NS}/Robot/panda_rightfinger",
        update_period=0.0,
        history_length=4,
        debug_vis=False,
        filter_prim_paths_expr=["{ENV_REGEX_NS}/Object"],
    )
    # These are the calibrated transforms shipped by the pinned Franka
    # visuomotor configuration in Isaac Lab. They are explicit and versioned,
    # rather than guessed by the browser.
    cfg.scene.wrist_camera = _camera_cfg(
        prim_path="{ENV_REGEX_NS}/Robot/panda_hand/wrist_camera",
        position=(0.13, 0.0, -0.15),
        rotation=(0.03701, 0.03701, -0.70614, -0.70614),
    )
    cfg.scene.front_camera = _camera_cfg(
        prim_path="{ENV_REGEX_NS}/front_camera",
        position=(1.0, 0.0, 0.4),
        rotation=(-0.61237, -0.61237, 0.35355, 0.35355),
    )
    cfg.sim.render_interval = cfg.decimation
    cfg.num_rerenders_on_reset = 3

    env = gym.make(ENVIRONMENT_ID, cfg=cfg)
    started = time.perf_counter()
    try:
        env.reset(seed=ARGS.seed)
        unwrapped = env.unwrapped
        actions = torch.zeros(unwrapped.action_space.shape, device=unwrapped.device)
        actions[:, 3] = 1.0
        actions[:, -1] = 1.0
        dt = float(cfg.sim.dt * cfg.decimation)
        output_root = ARGS.output.resolve()
        output_root.mkdir(parents=True, exist_ok=True)

        ee_sensor = unwrapped.scene["ee_frame"]
        obj = unwrapped.scene["object"]
        left_contact = unwrapped.scene["left_finger_contact"]
        right_contact = unwrapped.scene["right_finger_contact"]
        front_camera = unwrapped.scene["front_camera"]
        wrist_camera = unwrapped.scene["wrist_camera"]

        initial_object = obj.data.root_pos_w.torch[0].clone() - unwrapped.scene.env_origins[0]
        initial_z = float(initial_object[2].item())
        place_target = torch.tensor([0.45, 0.20, initial_z], device=unwrapped.device)
        downward = torch.tensor([0.0, 1.0, 0.0, 0.0], device=unwrapped.device)
        phase = "rest"
        phase_seconds = 0.0
        max_object_z = initial_z
        max_left_force = 0.0
        max_right_force = 0.0
        contact_samples = 0
        trajectory: list[dict[str, object]] = []
        frames = {
            "reset": {
                "front": _save_rgb(front_camera, output_root / "reset-front.png"),
                "wrist": _save_rgb(wrist_camera, output_root / "reset-wrist.png"),
            }
        }

        thresholds = {
            "rest": 0.25,
            "approach_above": 0.015,
            "approach_object": 0.012,
            "grasp": 0.45,
            "lift": 0.020,
            "transport": 0.020,
            "lower": 0.015,
            "release": 0.50,
            "retreat": 0.020,
            "settle": 1.20,
        }
        current_target = None
        failure_detail = "episode step budget exhausted"

        for step in range(ARGS.max_steps):
            ee_position = ee_sensor.data.target_pos_w.torch[0, 0].clone() - unwrapped.scene.env_origins[0]
            object_position = obj.data.root_pos_w.torch[0].clone() - unwrapped.scene.env_origins[0]
            max_object_z = max(max_object_z, float(object_position[2].item()))
            left_force = float(torch.linalg.vector_norm(left_contact.data.net_forces_w.torch[0]).item())
            right_force = float(torch.linalg.vector_norm(right_contact.data.net_forces_w.torch[0]).item())
            max_left_force = max(max_left_force, left_force)
            max_right_force = max(max_right_force, right_force)
            if left_force > 0.25 or right_force > 0.25:
                contact_samples += 1

            target = ee_position.clone()
            gripper = 1.0
            reached = False
            if phase == "rest":
                reached = phase_seconds >= thresholds[phase]
            elif phase == "approach_above":
                target = object_position + torch.tensor([0.0, 0.0, 0.10], device=unwrapped.device)
                reached = _distance(ee_position, target) <= thresholds[phase]
            elif phase == "approach_object":
                target = object_position
                reached = _distance(ee_position, target) <= thresholds[phase]
            elif phase == "grasp":
                target = object_position
                gripper = -1.0
                reached = phase_seconds >= thresholds[phase] and left_force > 0.25 and right_force > 0.25
            elif phase == "lift":
                target = initial_object + torch.tensor([0.0, 0.0, 0.20], device=unwrapped.device)
                gripper = -1.0
                reached = _distance(ee_position, target) <= thresholds[phase] and max_object_z >= initial_z + 0.08
            elif phase == "transport":
                target = place_target + torch.tensor([0.0, 0.0, 0.18], device=unwrapped.device)
                gripper = -1.0
                reached = _distance(ee_position, target) <= thresholds[phase]
            elif phase == "lower":
                target = place_target
                gripper = -1.0
                reached = _distance(ee_position, target) <= thresholds[phase]
            elif phase == "release":
                target = place_target
                reached = phase_seconds >= thresholds[phase]
            elif phase == "retreat":
                target = place_target + torch.tensor([0.0, 0.0, 0.18], device=unwrapped.device)
                reached = _distance(ee_position, target) <= thresholds[phase]
            elif phase == "settle":
                target = place_target + torch.tensor([0.0, 0.0, 0.18], device=unwrapped.device)
                reached = phase_seconds >= thresholds[phase]
            else:
                raise RuntimeError(f"Unknown oracle phase: {phase}")

            current_target = target
            actions[0, :3] = target
            actions[0, 3:7] = downward
            actions[0, 7] = gripper
            _, _, terminated, truncated, _ = env.step(actions)
            phase_seconds += dt
            if step % 10 == 0:
                trajectory.append(
                    {
                        "step": step,
                        "phase": phase,
                        "eePositionM": _tensor_list(ee_position),
                        "objectPositionM": _tensor_list(object_position),
                        "leftForceN": left_force,
                        "rightForceN": right_force,
                    }
                )
            if bool(terminated[0].item()) or bool(truncated[0].item()):
                failure_detail = "Isaac Lab environment terminated before the oracle predicate"
                break
            if reached:
                next_phase = {
                    "rest": "approach_above",
                    "approach_above": "approach_object",
                    "approach_object": "grasp",
                    "grasp": "lift",
                    "lift": "transport",
                    "transport": "lower",
                    "lower": "release",
                    "release": "retreat",
                    "retreat": "settle",
                    "settle": "complete",
                }[phase]
                phase = next_phase
                phase_seconds = 0.0
                if phase == "complete":
                    failure_detail = ""
                    break

        # Render the result after physics and release, never from browser state.
        frames["final"] = {
            "front": _save_rgb(front_camera, output_root / "final-front.png"),
            "wrist": _save_rgb(wrist_camera, output_root / "final-wrist.png"),
        }
        final_object = obj.data.root_pos_w.torch[0].clone() - unwrapped.scene.env_origins[0]
        final_velocity = obj.data.root_lin_vel_w.torch[0].clone()
        target_xy_error = float(torch.linalg.vector_norm(final_object[:2] - place_target[:2]).item())
        lifted = max_object_z >= initial_z + 0.08
        bilateral_contact = max_left_force > 0.25 and max_right_force > 0.25
        placed = target_xy_error <= 0.08 and abs(float(final_object[2].item()) - initial_z) <= 0.08
        settled = float(torch.linalg.vector_norm(final_velocity).item()) <= 0.10
        success = phase == "complete" and lifted and bilateral_contact and placed and settled
        if not success and not failure_detail:
            failed = [
                name
                for name, passed in (
                    ("state_machine_complete", phase == "complete"),
                    ("lifted", lifted),
                    ("bilateral_contact", bilateral_contact),
                    ("placed", placed),
                    ("settled", settled),
                )
                if not passed
            ]
            failure_detail = "failed predicate(s): " + ", ".join(failed)

        return {
            "schemaVersion": SCHEMA_VERSION,
            "backend": "nvidia_isaac_sim",
            "backendVersion": "6.0.1",
            "isaacLabEnvironment": ENVIRONMENT_ID,
            "controller": CONTROLLER,
            "seed": ARGS.seed,
            "success": success,
            "failureCode": None if success else "success_predicate_failure",
            "failureDetail": failure_detail or None,
            "terminalPhase": phase,
            "steps": step + 1,
            "durationSeconds": time.perf_counter() - started,
            "actionContract": "[x,y,z,qw,qx,qy,qz,gripper] oracle; VLA runtime uses 7D relative DLS",
            "robot": {"name": "Franka Panda", "armDof": 7, "fingerJoints": 2},
            "cameras": {
                "front": {"resolution": [224, 224], "mount": "world"},
                "wrist": {
                    "resolution": [224, 224],
                    "mount": "panda_hand",
                    "positionM": [0.13, 0.0, -0.15],
                    "quaternionRosWxyz": [0.03701, 0.03701, -0.70614, -0.70614],
                },
            },
            "frames": frames,
            "predicate": {
                "initialObjectPositionM": _tensor_list(initial_object),
                "finalObjectPositionM": _tensor_list(final_object),
                "placeTargetM": _tensor_list(place_target),
                "targetXyErrorM": target_xy_error,
                "maxObjectZM": max_object_z,
                "liftHeightM": max_object_z - initial_z,
                "bilateralContact": bilateral_contact,
                "contactSamples": contact_samples,
                "maxLeftFingerForceN": max_left_force,
                "maxRightFingerForceN": max_right_force,
                "finalLinearSpeedMps": float(torch.linalg.vector_norm(final_velocity).item()),
                "lifted": lifted,
                "placed": placed,
                "settled": settled,
            },
            "trajectory": trajectory,
            "lastTargetM": _tensor_list(current_target) if current_target is not None else None,
        }
    finally:
        env.close()


def main() -> int:
    result: dict[str, object]
    try:
        result = run()
    except Exception as exc:
        result = {
            "schemaVersion": SCHEMA_VERSION,
            "backend": "nvidia_isaac_sim",
            "backendVersion": "6.0.1",
            "success": False,
            "failureCode": "worker_crash",
            "failureDetail": f"{type(exc).__name__}: {exc}",
        }
    finally:
        SIMULATION_APP.close()
    ARGS.output.mkdir(parents=True, exist_ok=True)
    result_path = ARGS.output / "result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf8")
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0 if result.get("success") else 2


if __name__ == "__main__":
    raise SystemExit(main())
