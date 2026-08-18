"""Feasibility of the 'scoop from below' approach waypoint at pitch 0.25."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

import app.services.simcore as sc

for seed in range(10):
    w = sc.World(sc.default_scenario_family(np.random.default_rng(seed)), sc.FRIDGE_SPEC)
    hp = w.handle_pos()
    qa, ea = w.solve_ik_checked(hp + np.array([0, -0.12, 0.16]), 0.25, allow_handle_contact=True)
    qc, ec = w.solve_ik_checked(hp + np.array([0, 0, 0.002]), 0.25, allow_handle_contact=True)
    ok = w.path_clear(qa, qc, allow_handle=True)
    print(f"seed={seed} hh={w.scenario['handle_height']:.2f}: scoop_err={ea:.4f} cage_err={ec:.4f} seg_ok={ok}")
