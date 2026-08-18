"""Diagnose which path segments block for given seeds (current 5-waypoint path)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mujoco
import numpy as np

import app.services.simcore as sc

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
w = sc.World(sc.default_scenario_family(np.random.default_rng(seed)), sc.FRIDGE_SPEC)
c = sc.ScriptedController(w)
print(f"seed={seed} plan_err={c.plan_error:.3f} hh={w.scenario['handle_height']:.2f}")
pts = [c.q_home, c.SAFE, c.q_approach, c.q_precage, c.q_cage]
names = ["home", "SAFE", "approach", "precage", "cage"]
for i in range(len(pts)):
    print(f"{names[i]:9s} q={np.round(pts[i], 2)} contacts={w.config_contacts(pts[i])}")
for i in range(len(pts) - 1):
    terminal = i >= len(pts) - 3
    ok = w.path_clear(pts[i], pts[i + 1], allow_handle=terminal)
    print(f"seg {names[i]}->{names[i+1]}: {'clear' if ok else 'BLOCKED'}")
    if not ok:
        q0, q1 = pts[i], pts[i + 1]
        n = max(2, int(np.max(np.abs(q1 - q0)) / 0.05) + 1)
        for a in np.linspace(0, 1, n):
            q = q0 + a * (q1 - q0)
            if w.config_contacts(q) > 0:
                print(f"   first contact at a={a:.2f} q={np.round(q, 2)}")
                cols = [w.model.jnt_dofadr[w.j[k]] for k in ("yaw", "shoulder", "elbow", "wrist")]
                saved = w.data.qpos.copy()
                w.data.qpos[cols] = q
                mujoco.mj_forward(w.model, w.data)
                for k2 in range(w.data.ncon):
                    con = w.data.contact[k2]
                    n1 = mujoco.mj_id2name(w.model, mujoco.mjtObj.mjOBJ_GEOM, con.geom1) or str(con.geom1)
                    n2 = mujoco.mj_id2name(w.model, mujoco.mjtObj.mjOBJ_GEOM, con.geom2) or str(con.geom2)
                    print(f"     {n1}|{n2} dist={con.dist:.4f}")
                w.data.qpos[:] = saved
                mujoco.mj_forward(w.model, w.data)
                break
