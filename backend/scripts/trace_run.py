"""Phase-level trace of one scripted rollout."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

import app.services.simcore as sc

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
print("module file:", sc.__file__)

w = sc.World(sc.default_scenario_family(np.random.default_rng(seed)), sc.FRIDGE_SPEC)
c = sc.ScriptedController(w)
print("phase0:", c.phase, "path len:", len(c.q_path), "blocked:", c.path_blocked, "plan_err:", round(c.plan_error, 4))
w.reset()
t = 0.0
for i in range(14 * 60):
    q, g, done = c.act(sc.DT_CTRL)
    w.set_arm(q)
    w.set_grip(g)
    for _ in range(8):
        w.step()
    t += sc.DT_CTRL
    if done:
        break
    if c.phase != getattr(c, "_lp", None):
        hit, f, oth, on = w.contacts()
        print(
            f"t={t:5.2f} -> {c.phase:9s} wp={c.wp_idx} ee={np.round(w.ee_pos(),3)} "
            f"hp={np.round(w.handle_pos(),3)} grip={w.grip():.2f} pitch={w.hand_pitch(w.arm_qpos()):.2f} hit={hit}"
        )
        c._lp = c.phase
    if c.phase == "close" and i % 20 == 0:
        hit, f, oth, on = w.contacts()
        d = np.linalg.norm(w.ee_pos() - w.handle_pos())
        print(f"   close t={t:.2f} d={d:.3f} grip={w.grip():.2f} hit={hit} F={f:.1f} other={on}")
print("final door:", np.degrees(w.door_rad()), "attached:", w.attached)
