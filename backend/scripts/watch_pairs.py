"""Dump contact pairs over time + render frames for one rollout."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mujoco
import numpy as np
from PIL import Image

import app.services.simcore as sc

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 2
w = sc.World(sc.default_scenario_family(np.random.default_rng(seed)), sc.FRIDGE_SPEC)
c = sc.ScriptedController(w)
print("path:", [np.round(q, 2).tolist() for q in c.q_path], "blocked:", c.path_blocked)
w.reset()
r = mujoco.Renderer(w.model, 480, 640)
t = 0.0
seen = set()
for i in range(14 * 60):
    q, g, done = c.act(sc.DT_CTRL)
    w.set_arm(q)
    w.set_grip(g)
    for _ in range(8):
        w.step()
    t += sc.DT_CTRL
    if done:
        break
    if i % 30 == 0:
        pairs = set()
        for k in range(w.data.ncon):
            con = w.data.contact[k]
            n1 = mujoco.mj_id2name(w.model, mujoco.mjtObj.mjOBJ_GEOM, con.geom1) or str(con.geom1)
            n2 = mujoco.mj_id2name(w.model, mujoco.mjtObj.mjOBJ_GEOM, con.geom2) or str(con.geom2)
            pairs.add(f"{n1}|{n2}")
        new = pairs - seen
        seen |= pairs
        print(f"t={t:5.2f} ph={c.phase:8s} q={np.round(w.arm_qpos(),2)} pairs={sorted(pairs)}")
    if i % 120 == 0:
        r.update_scene(w.data, camera="debug")
        Image.fromarray(r.render()).save(os.path.join(os.path.dirname(__file__), "..", "data", "debug", f"wp_{seed}_{i//120}.png"))
print("final door:", np.degrees(w.door_rad()))
