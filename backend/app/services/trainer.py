"""Behavior-cloning trainer: MLP policy over (obs 12 -> act 5) trained on the
scripted controller's successful demonstrations. Real PyTorch training loop
(CPU), real loss curves, real saved checkpoints."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..config import MODELS_DIR
from ..telemetry import span

log = logging.getLogger(__name__)


def build_mlp():
    import torch

    return torch.nn.Sequential(
        torch.nn.Linear(12, 128),
        torch.nn.ReLU(),
        torch.nn.Linear(128, 128),
        torch.nn.ReLU(),
        torch.nn.Linear(128, 5),
    )


def collect_demos(scenarios: list[dict[str, Any]], *, max_episodes: int = 12) -> tuple[np.ndarray, np.ndarray, int]:
    """Record (obs, act) from successful scripted rollouts."""
    from . import simcore

    obs_all, act_all, kept = [], [], 0
    for params in scenarios[:max_episodes]:
        world = simcore.World(params)
        r = simcore.run_rollout(world, simcore.ScriptedController, record=True)
        if r.success and len(r.obs):
            obs_all.append(r.obs)
            act_all.append(r.act)
            kept += 1
    if not obs_all:
        return np.zeros((0, 12), dtype=np.float32), np.zeros((0, 5), dtype=np.float32), 0
    return np.concatenate(obs_all), np.concatenate(act_all), kept


def train_bc(obs: np.ndarray, act: np.ndarray, *, epochs: int = 60, lr: float = 1e-3) -> tuple[Any, list[float], float]:
    """Train the MLP; returns (model, loss_curve, final_loss)."""
    import torch

    torch.manual_seed(0)
    model = build_mlp()
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    X = torch.from_numpy(obs)
    Y = torch.from_numpy(act)
    n = len(X)
    loss_curve: list[float] = []
    t0 = time.time()
    for epoch in range(epochs):
        perm = torch.randperm(n)
        total = 0.0
        nb = 0
        for i in range(0, n, 512):
            idx = perm[i : i + 512]
            pred = model(X[idx])
            loss = torch.nn.functional.mse_loss(pred, Y[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += float(loss)
            nb += 1
        loss_curve.append(round(total / max(nb, 1), 5))
    return model, loss_curve, time.time() - t0


def save_model(model, path: Path) -> None:
    import torch

    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), path)


def load_model(path: Path):
    import torch

    model = build_mlp()
    model.load_state_dict(torch.load(path, map_location="cpu"))
    model.eval()
    return model
