"""Run with Isaac Sim 5.1's python.bat, never the RobotWorld venv.

Usage: <isaac-root>/python.bat backend/isaac_bridge.py <isaac-launch.json>
The bridge composes RobotWorld's physics-authored stage and NVIDIA's official
Franka asset. It intentionally stops before policy control until the manifest's
policy execution gate is satisfied.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from isaacsim import SimulationApp

simulation_app = SimulationApp({"headless": False})

import numpy as np
import omni.timeline
import omni.usd
import isaacsim.core.experimental.utils.stage as stage_utils
from isaacsim.core.experimental.prims import Articulation, XformPrim
from isaacsim.storage.native import get_assets_root_path
from pxr import Usd, UsdGeom


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Expected one isaac-launch.json path.")
    manifest_path = Path(sys.argv[1]).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    if manifest.get("simulatorVersion") != "5.1":
        raise RuntimeError("This bridge targets Isaac Sim 5.1 exactly.")

    world_stage = Path(manifest["worldStage"]).resolve()
    if not world_stage.is_file():
        raise FileNotFoundError(world_stage)
    if not stage_utils.open_stage(str(world_stage)):
        raise RuntimeError("Isaac Sim could not open RobotWorld's OpenUSD stage.")

    stage = omni.usd.get_context().get_stage()
    UsdGeom.Xform.Define(stage, "/RobotWorld/Robots")
    robot = manifest["robot"]
    asset_path = get_assets_root_path() + robot["assetFromRoot"]
    stage_utils.add_reference_to_stage(usd_path=asset_path, path=robot["primPath"])
    XformPrim(robot["primPath"]).set_world_poses(positions=np.asarray([robot["basePoseM"]], dtype=float))
    franka = Articulation(robot["primPath"])

    omni.timeline.get_timeline_interface().play()
    simulation_app.update()
    if franka.num_dofs < 9:
        raise RuntimeError(f"Unexpected Franka articulation: {franka.num_dofs} DOFs")
    print(json.dumps({"event": "ready", "frankaDofs": franka.num_dofs, "dofNames": franka.dof_names}))
    while simulation_app.is_running():
        simulation_app.update()


try:
    main()
finally:
    simulation_app.close()
