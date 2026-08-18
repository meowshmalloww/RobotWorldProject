"""Unit test: weld attach must make the door follow the hand."""
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
print("before attach: door=", np.degrees(w.door_rad()), "ee=", np.round(w.ee_pos(), 3), "hp=", np.round(w.handle_pos(), 3))
w.attach()
print("eq_active:", w.data.eq_active[w.grasp_eq])
print("eq_data row:", np.round(w.model.eq_data[w.grasp_eq, :11], 3))
# hold the arm in place for 1 s — the door must NOT snap/drift if the anchor is clean
for _ in range(60):
    w.set_arm(c.q_cage)
    w.set_grip(1.0)
    for _ in range(8):
        w.step()
print("after 1 s hold: door=", np.degrees(w.door_rad()), "ee=", np.round(w.ee_pos(), 3))
c._plan_pull()
q_tgt = c.pull_path[2]
print("pull path:", [np.round(q, 2).tolist() for q in c.pull_path])
print("pull waypoint:", np.round(q_tgt, 2))
for i in range(3 * 60):
    w.set_arm(q_tgt)
    w.set_grip(1.0)
    for _ in range(8):
        w.step()
    if i % 60 == 0:
        print(
            f"t={i/60:.1f} door={np.degrees(w.door_rad()):6.1f} ee={np.round(w.ee_pos(),3)} "
            f"hp={np.round(w.handle_pos(),3)} attached={w.attached}"
        )
        print("   eq_data row now:", np.round(w.model.eq_data[w.grasp_eq, :8], 3))
