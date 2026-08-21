"""Isaac Sim 5.1 readiness and Franka launch contract.

This module never imports Isaac packages inside FastAPI. Isaac Sim ships its
own Python runtime; RobotWorld writes a launch manifest that the standalone
bridge consumes under that runtime.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..config import WORLDS_DIR

VERSION = "5.1"
FRANKA_ASSET = "/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd"


def inspect(configured_root: str = "", configured_asset_root: str = "") -> dict[str, Any]:
    candidates = [
        configured_root,
        os.environ.get("ISAAC_SIM_ROOT", ""),
        r"C:\isaacsim",
        r"D:\isaacsim",
        r"C:\Program Files\NVIDIA Corporation\Isaac Sim",
    ]
    root = next((Path(value).expanduser().resolve() for value in candidates if value and Path(value).expanduser().is_dir()), None)
    python = None
    launcher = None
    if root:
        python = next((path for path in (root / "python.bat", root / "python.sh") if path.is_file()), None)
        launcher = next((path for path in (root / "isaac-sim.bat", root / "isaac-sim.sh") if path.is_file()), None)
    installed = bool(root and python and launcher)
    blockers: list[str] = []
    if not installed:
        blockers.append("Isaac Sim 5.1 runtime is not installed or simulation.isaacRoot is not configured.")
    asset_root = configured_asset_root or os.environ.get("ISAACSIM_ASSET_ROOT", "")
    if installed and asset_root and not (asset_root.startswith("http://") or asset_root.startswith("https://") or Path(asset_root).exists()):
        blockers.append("Configured Isaac asset root is unreachable on this machine.")
    return {
        "version": VERSION,
        "installed": installed,
        "root": str(root) if root else "",
        "python": str(python) if python else "",
        "launcher": str(launcher) if launcher else "",
        "assetRoot": asset_root or "Isaac Sim default asset server",
        "frankaAsset": FRANKA_ASSET,
        "franka": {"armDof": 7, "fingerJoints": 2, "fixedBase": True},
        "ready": installed and not blockers,
        "blockers": blockers,
    }


def write_launch_manifest(world_id: str, world_stage: Path, status: dict[str, Any]) -> Path:
    if not world_stage.is_file():
        raise FileNotFoundError("The active OpenUSD stage has not been authored.")
    output = (WORLDS_DIR / world_id / "isaac-launch.json").resolve()
    if output.parent != (WORLDS_DIR / world_id).resolve():
        raise ValueError("Invalid Isaac launch target.")
    payload = {
        "schemaVersion": 1,
        "simulator": "NVIDIA Isaac Sim",
        "simulatorVersion": VERSION,
        "worldStage": str(world_stage.resolve()),
        "robot": {
            "id": "franka-panda-isaac-6",
            "name": "Franka Panda",
            "assetFromRoot": FRANKA_ASSET,
            "primPath": "/RobotWorld/Robots/Franka",
            "basePoseM": [0.0, -0.65, 0.0],
            "armDof": 7,
            "fingerJoints": 2,
        },
        "physics": {
            "source": "OpenUSD stage APIs",
            "movable": "RigidBodyAPI + MassAPI + convexHull collision",
            "fixed": "static CollisionAPI + triangle mesh collision",
            "gravityMps2": -9.81,
        },
        "policy": {
            "checkpoint": r"D:\VLA-JEPA-Pretrain",
            "actionContract": "7D end-effector delta pose plus binary gripper",
            "adapter": "Franka differential IK (damped least squares)",
            "executionReady": False,
            "blocker": "Collect Franka camera/action demonstrations and fine-tune the reinitialized state/action projections before policy execution.",
        },
        "runtimeReady": bool(status.get("ready")),
        "runtimeBlockers": list(status.get("blockers") or []),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf8")
    return output
