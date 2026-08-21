"""Backend-neutral authoritative simulation contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ContactEvent:
    body_a: str
    body_b: str
    distance_m: float
    normal_force_n: float


class SimulationBackend(ABC):
    """Physics engines implement this surface; product logic never invents state."""

    @abstractmethod
    def load_world(self, artifact: Path) -> None: ...

    @abstractmethod
    def reset(self, seed: int) -> dict[str, Any]: ...

    @abstractmethod
    def step(self, substeps: int = 1) -> dict[str, Any]: ...

    @abstractmethod
    def apply_action(self, action: np.ndarray) -> None: ...

    @abstractmethod
    def state(self) -> dict[str, Any]: ...

    @abstractmethod
    def contacts(self) -> list[ContactEvent]: ...

    @abstractmethod
    def render_rgb(self, camera: str, width: int = 256, height: int = 256) -> np.ndarray: ...

    @abstractmethod
    def close(self) -> None: ...

