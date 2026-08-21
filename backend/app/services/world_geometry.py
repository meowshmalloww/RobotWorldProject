"""Measured placement helpers for generated GLB assets.

TRELLIS outputs are normalized but are not unit cubes: an object's occupied
extent can be much smaller than the exported ``[-.5, .5]`` coordinate box on
one or more axes.  Fitting the authored target dimensions to the occupied
mesh bounds prevents long/thin objects from being scaled twice.
"""
from __future__ import annotations

from functools import lru_cache
import math
from pathlib import Path
from typing import Any

import numpy as np
import trimesh


@lru_cache(maxsize=128)
def _bounds_for_stamp(path_text: str, modified_ns: int) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    del modified_ns  # the value is part of the cache key
    loaded = trimesh.load(path_text, force="scene", process=False)
    bounds = np.asarray(loaded.bounds, dtype=float)
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
        raise ValueError("GLB has no finite three-dimensional bounds")
    extents = bounds[1] - bounds[0]
    if np.any(extents <= 1e-7):
        raise ValueError("GLB has a collapsed mesh axis")
    return tuple(float(v) for v in bounds[0]), tuple(float(v) for v in bounds[1])


def measured_fit(model_path: Path, target_width: float, target_height: float, target_depth: float) -> dict[str, Any]:
    """Return a GLB-to-USD fit using occupied mesh bounds.

    glTF uses Y-up and the authored USD layer maps ``(x, y, z)`` to
    ``(x, -z, y)``.  Consequently USD scale is ordered width/depth/height.
    """
    path = model_path.resolve()
    stat = path.stat()
    low, high = _bounds_for_stamp(str(path), stat.st_mtime_ns)
    glb_extents = tuple(high[i] - low[i] for i in range(3))
    glb_scale = (
        max(0.001, float(target_width)) / glb_extents[0],
        max(0.001, float(target_height)) / glb_extents[1],
        max(0.001, float(target_depth)) / glb_extents[2],
    )
    usd_scale = (glb_scale[0], glb_scale[2], glb_scale[1])
    local_usd_low = (
        low[0] * usd_scale[0],
        -high[2] * usd_scale[1],
        low[1] * usd_scale[2],
    )
    local_usd_high = (
        high[0] * usd_scale[0],
        -low[2] * usd_scale[1],
        high[1] * usd_scale[2],
    )
    return {
        "raw_bounds": (low, high),
        "raw_extents": glb_extents,
        "scale": usd_scale,
        "local_usd_low": local_usd_low,
        "local_usd_high": local_usd_high,
        "target_dimensions": (float(target_width), float(target_depth), float(target_height)),
    }


def world_bounds(
    fit: dict[str, Any],
    translation: tuple[float, float, float],
    rotation_z_deg: float = 0.0,
    scale_multiplier: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    low = tuple(float(fit["local_usd_low"][index]) * scale_multiplier[index] for index in range(3))
    high = tuple(float(fit["local_usd_high"][index]) * scale_multiplier[index] for index in range(3))
    angle = math.radians(float(rotation_z_deg))
    cosine, sine = math.cos(angle), math.sin(angle)
    corners = [(x, y) for x in (low[0], high[0]) for y in (low[1], high[1])]
    rotated = [(x * cosine - y * sine, x * sine + y * cosine) for x, y in corners]
    return (
        (min(x for x, _ in rotated) + translation[0], min(y for _, y in rotated) + translation[1], low[2] + translation[2]),
        (max(x for x, _ in rotated) + translation[0], max(y for _, y in rotated) + translation[1], high[2] + translation[2]),
    )
