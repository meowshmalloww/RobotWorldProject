"""Debug: render rollout frames to PNGs so we can SEE the physics."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mujoco
import numpy as np
from PIL import Image

from app.services.simcore import DT_CTRL, FRIDGE_SPEC, ScriptedController, World, default_scenario_family

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "debug")
os.makedirs(OUT, exist_ok=True)

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 3
w = World(default_scenario_family(np.random.default_rng(seed)), FRIDGE_SPEC)
c = ScriptedController(w)
w.reset()
renderer = mujoco.Renderer(w.model, 480, 640)
cam = "debug"

t = 0.0
shots = {0.1: "a_start", 1.6: "b_approach", 2.6: "c_grasp", 3.6: "d_pull1", 5.0: "e_pull2", 7.0: "f_pull3", 9.5: "g_late", 12.0: "h_end"}
for i in range(int(13 * 60)):
    q, g, done = c.act(DT_CTRL)
    w.set_arm(q)
    w.set_grip(g)
    for _ in range(8):
        w.step()
    t += DT_CTRL
    for ts, name in list(shots.items()):
        if abs(t - ts) < DT_CTRL / 2:
            renderer.update_scene(w.data, camera=cam)
            Image.fromarray(renderer.render()).save(os.path.join(OUT, f"{name}.png"))
            print(f"saved {name} phase={c.phase} door={np.degrees(w.door_rad()):.1f} grip={w.grip():.2f} ee={np.round(w.ee_pos(),2)} hp={np.round(w.handle_pos(),2)}")
    if done:
        break
print("done")
