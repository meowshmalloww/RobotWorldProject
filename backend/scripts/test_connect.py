"""Unit test: activated connect constraint must couple door motion to the arm."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mujoco
import numpy as np

import app.services.simcore as sc

w = sc.World(sc.default_scenario_family(np.random.default_rng(2)), sc.FRIDGE_SPEC)
c = sc.ScriptedController(w)
w.reset()
cols = [w.model.jnt_dofadr[w.j[k]] for k in ("yaw", "shoulder", "elbow", "wrist")]
w.data.qpos[cols] = c.q_cage
mujoco.mj_forward(w.model, w.data)
ee0 = w.ee_pos().copy()
hp0 = w.handle_pos().copy()
print("ee:", np.round(ee0, 3), "hp:", np.round(hp0, 3), "dist:", np.linalg.norm(ee0 - hp0))
w.attach()
print("eq_active:", w.attached)

# 1) hold for 0.5 s — nothing should snap
for _ in range(30):
    w.set_arm(c.q_cage)
    w.set_grip(1.0)
    for _ in range(8):
        w.step()
print("after hold: door=", round(np.degrees(w.door_rad()), 2), "ee=", np.round(w.ee_pos(), 3))

# 2) push the DOOR directly — if coupled, the ARM must move
dadr = w.model.jnt_dofadr[w.j["door"]]
q0 = w.arm_qpos().copy()
for _ in range(120):
    w.data.qfrc_applied[dadr] = 40.0
    w.set_arm(q0)
    w.set_grip(1.0)
    for _ in range(4):
        w.step()
w.data.qfrc_applied[dadr] = 0.0
print("door after push:", round(np.degrees(w.door_rad()), 1), " arm moved:", np.round(w.arm_qpos() - q0, 3))

# 3) pull via the arm: track the 40% waypoint
c._plan_pull()
print("pull path:", [np.round(q, 2).tolist() for q in c.pull_path])
q_goal = c.pull_path[2]
ref = w.arm_qpos().copy()
for i in range(5 * 60):
    ref = ref + np.clip(q_goal - ref, -0.012, 0.012)
    w.set_arm(ref)
    w.set_grip(1.0)
    for _ in range(8):
        w.step()
    if i % 60 == 0:
        print(f"t={i/60:.1f} door={np.degrees(w.door_rad()):6.1f} ee={np.round(w.ee_pos(),3)}")
