"""Fail-closed native Vulkan viewport renderer.

The browser only presents PNG frames. Scene rasterization happens here through
pygfx -> wgpu-native with the backend explicitly forced to Vulkan. No browser
WebGL/WebGPU renderer participates in this path.
"""
from __future__ import annotations

import io
import math
import os
import threading
from dataclasses import dataclass
from typing import Any

os.environ.setdefault("WGPU_BACKEND_TYPE", "Vulkan")

import numpy as np
import pygfx as gfx
import pylinalg as la
import wgpu
from PIL import Image
from rendercanvas.offscreen import RenderCanvas


class VulkanUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderRequest:
    scene: str = "kitchen"
    width: int = 960
    height: int = 540
    yaw: float = 34.0
    pitch: float = 24.0
    distance: float = 12.0
    door_angle: float = 0.0
    variant: str = "rgb"


_lock = threading.RLock()
_adapter: Any = None
_device: Any = None


def _ensure_device() -> tuple[Any, Any]:
    global _adapter, _device
    if _adapter is None:
        try:
            _adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
        except Exception as exc:
            raise VulkanUnavailable(f"Vulkan adapter request failed: {exc}") from exc
        info = dict(_adapter.info)
        if str(info.get("backend_type", "")).lower() != "vulkan":
            raise VulkanUnavailable(f"Renderer selected {info.get('backend_type')!r}, not Vulkan.")
        if str(info.get("adapter_type", "")).lower() in {"cpu", "unknown"}:
            raise VulkanUnavailable("Vulkan resolved to a software/unknown adapter.")
        try:
            _device = _adapter.request_device_sync()
        except Exception as exc:
            raise VulkanUnavailable(f"Vulkan device creation failed: {exc}") from exc
    return _adapter, _device


def probe() -> dict[str, Any]:
    with _lock:
        adapter, _ = _ensure_device()
        info = dict(adapter.info)
        return {
            "available": True,
            "backend": info.get("backend_type"),
            "adapterType": info.get("adapter_type"),
            "vendor": info.get("vendor"),
            "device": info.get("device"),
            "driver": info.get("description"),
            "browser3dApi": "none",
        }


def _material(color: str, variant: str, object_id: int) -> gfx.Material:
    if variant == "seg":
        palette = ("#9e6ad8", "#58a6a6", "#cb8c52", "#6f86c6", "#a9a9a9", "#7eaa72")
        color = palette[object_id % len(palette)]
    return gfx.MeshPhongMaterial(color=color)


def _box(scene: gfx.Scene, size: tuple[float, float, float], pos: tuple[float, float, float], color: str, *, variant: str, object_id: int = 0) -> gfx.Mesh:
    mesh = gfx.Mesh(gfx.box_geometry(*size), _material(color, variant, object_id))
    mesh.local.position = pos
    scene.add(mesh)
    return mesh


def _sphere(scene: gfx.Scene, radius: float, pos: tuple[float, float, float], color: str, *, variant: str, object_id: int = 0) -> gfx.Mesh:
    mesh = gfx.Mesh(gfx.sphere_geometry(radius, 24, 16), _material(color, variant, object_id))
    mesh.local.position = pos
    scene.add(mesh)
    return mesh


def _room(scene: gfx.Scene, variant: str) -> None:
    _box(scene, (17.0, 0.12, 11.0), (0.0, -0.06, 0.0), "#343434", variant=variant, object_id=0)
    _box(scene, (17.0, 4.6, 0.12), (0.0, 2.3, -5.5), "#444444", variant=variant, object_id=1)
    _box(scene, (0.12, 4.6, 11.0), (-8.5, 2.3, 0.0), "#3b3b3b", variant=variant, object_id=1)
    for x in (-5.8, -2.9, 0.0, 2.9, 5.8):
        _box(scene, (0.05, 0.012, 8.2), (x, 0.012, -0.3), "#77705d", variant=variant, object_id=2)


def _kitchen(scene: gfx.Scene, req: RenderRequest) -> None:
    v = req.variant
    _room(scene, v)
    # Counter, sink, cabinet and a genuinely separate blender base/jar/lid.
    _box(scene, (5.8, 0.9, 0.75), (-1.0, 0.45, -4.55), "#55514b", variant=v, object_id=3)
    _box(scene, (5.9, 0.12, 0.92), (-1.0, 0.96, -4.48), "#b1aea7", variant=v, object_id=3)
    _box(scene, (1.2, 0.08, 0.55), (-1.8, 1.03, -4.42), "#70777a", variant=v, object_id=4)
    _box(scene, (1.75, 1.15, 0.55), (0.8, 2.15, -4.85), "#68645e", variant=v, object_id=3)
    cup_door = _box(scene, (0.8, 1.05, 0.08), (0.37, 2.15, -4.54), "#8a857d", variant=v, object_id=3)
    cup_door.local.rotation = la.quat_from_euler((0.0, math.radians(req.door_angle), 0.0), order="XYZ")
    _box(scene, (0.55, 0.42, 0.55), (1.05, 1.22, -4.2), "#34373a", variant=v, object_id=5)
    _box(scene, (0.46, 0.72, 0.46), (1.05, 1.78, -4.2), "#9aa1a2", variant=v, object_id=5)
    _box(scene, (0.5, 0.08, 0.5), (1.05, 2.18, -4.2), "#292b2d", variant=v, object_id=5)
    # Work table and deterministic fruit placement; run manifests randomize these.
    _box(scene, (3.3, 0.16, 1.5), (2.7, 1.0, -1.4), "#77736b", variant=v, object_id=3)
    for x, z, color, oid in ((2.0, -1.25, "#b06043", 6), (2.55, -1.5, "#a7a04a", 7), (3.0, -1.15, "#718d55", 8), (3.45, -1.55, "#b96d45", 9)):
        _sphere(scene, 0.18, (x, 1.22, z), color, variant=v, object_id=oid)
    # Simple robot pedestal and links for viewport context only.
    _box(scene, (0.7, 0.25, 0.7), (0.0, 0.13, -1.4), "#585858", variant=v, object_id=10)
    _box(scene, (0.25, 1.15, 0.25), (0.0, 0.8, -1.4), "#d0d0d0", variant=v, object_id=10)
    _box(scene, (0.25, 0.25, 1.35), (0.0, 1.35, -2.0), "#bdbdbd", variant=v, object_id=10)


def _factory(scene: gfx.Scene, req: RenderRequest) -> None:
    v = req.variant
    _room(scene, v)
    _box(scene, (5.5, 0.18, 1.15), (-0.8, 0.8, -0.8), "#4b4d4e", variant=v, object_id=3)
    for i, (x, color) in enumerate(((-2.2, "#8c7358"), (-0.8, "#9c835f"), (0.7, "#786754"))):
        _box(scene, (0.72, 0.55 + i * 0.08, 0.72), (x, 1.18, -0.8), color, variant=v, object_id=6 + i)
    for i, (x, color) in enumerate(((-5.2, "#5a6470"), (0.0, "#665d54"), (5.2, "#4f665b"))):
        _box(scene, (3.0, 1.9, 2.2), (x, 1.0, -4.2), color, variant=v, object_id=12 + i)
        _box(scene, (2.4, 1.25, 0.08), (x, 0.75, -3.05), "#242424", variant=v, object_id=12 + i)
    _box(scene, (0.8, 0.25, 0.8), (1.2, 0.13, 1.2), "#565656", variant=v, object_id=10)
    _box(scene, (0.28, 1.35, 0.28), (1.2, 0.9, 1.2), "#cacaca", variant=v, object_id=10)
    _box(scene, (1.4, 0.28, 0.28), (0.55, 1.45, 0.8), "#b8b8b8", variant=v, object_id=10)


def render_png(req: RenderRequest) -> bytes:
    if req.scene not in {"kitchen", "factory"}:
        raise ValueError("scene must be 'kitchen' or 'factory'")
    if req.variant not in {"rgb", "seg"}:
        raise ValueError("variant must be 'rgb' or 'seg'")
    width = max(320, min(int(req.width), 1600))
    height = max(180, min(int(req.height), 1000))
    with _lock:
        _ensure_device()
        canvas = RenderCanvas(size=(width, height), pixel_ratio=1)
        renderer = gfx.WgpuRenderer(canvas)
        scene = gfx.Scene()
        scene.add(gfx.Background.from_color("#181818"))
        if req.scene == "kitchen":
            _kitchen(scene, req)
            target = (0.0, 1.1, -1.8)
        else:
            _factory(scene, req)
            target = (0.0, 1.0, -1.3)
        scene.add(gfx.AmbientLight("#ffffff", 0.42))
        light = gfx.DirectionalLight("#ffffff", 2.1)
        light.local.position = (4.0, 8.0, 5.0)
        scene.add(light)
        camera = gfx.PerspectiveCamera(48, width / height, depth_range=(0.05, 100.0))
        yaw = math.radians(req.yaw)
        pitch = math.radians(max(-10.0, min(req.pitch, 75.0)))
        cp = math.cos(pitch)
        camera.local.position = (
            target[0] + req.distance * math.sin(yaw) * cp,
            target[1] + req.distance * math.sin(pitch),
            target[2] + req.distance * math.cos(yaw) * cp,
        )
        camera.show_pos(target)

        @canvas.request_draw
        def draw() -> None:
            renderer.render(scene, camera)

        frame = np.asarray(canvas.draw())
        out = io.BytesIO()
        Image.fromarray(frame, mode="RGBA").convert("RGB").save(out, format="PNG", optimize=True)
        return out.getvalue()
