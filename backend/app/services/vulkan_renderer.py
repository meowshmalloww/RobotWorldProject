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
from pathlib import Path
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


@dataclass(frozen=True)
class WorldPlacement:
    """A generated GLB positioned from the active OpenUSD stage recipe.

    ``translation`` and ``usd_scale`` use the authored USD Z-up convention.
    The renderer converts them back to the GLB's Y-up frame before rasterizing.
    """

    asset_id: str
    model_path: Path
    translation: tuple[float, float, float]
    usd_scale: tuple[float, float, float]


_lock = threading.RLock()
_adapter: Any = None
_device: Any = None
_cached_canvas: RenderCanvas | None = None
_cached_renderer: gfx.WgpuRenderer | None = None
_cached_size: tuple[int, int] = (0, 0)
_cached_scene_key: str = ""
_cached_scene: gfx.Scene | None = None
_cup_door_mesh: gfx.Mesh | None = None
_glb_cache: dict[str, tuple[Any, np.ndarray, np.ndarray]] = {}
_world_scene_cache_key = ""
_world_scene_cache: tuple[gfx.Scene, np.ndarray, np.ndarray] | None = None


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


def _build_kitchen(variant: str) -> tuple[gfx.Scene, gfx.Mesh]:
    scene = gfx.Scene()
    scene.add(gfx.Background.from_color("#181818"))
    _room(scene, variant)
    # Counter, sink, cabinet and a genuinely separate blender base/jar/lid.
    _box(scene, (5.8, 0.9, 0.75), (-1.0, 0.45, -4.55), "#55514b", variant=variant, object_id=3)
    _box(scene, (5.9, 0.12, 0.92), (-1.0, 0.96, -4.48), "#b1aea7", variant=variant, object_id=3)
    _box(scene, (1.2, 0.08, 0.55), (-1.8, 1.03, -4.42), "#70777a", variant=variant, object_id=4)
    _box(scene, (1.75, 1.15, 0.55), (0.8, 2.15, -4.85), "#68645e", variant=variant, object_id=3)
    cup_door = _box(scene, (0.8, 1.05, 0.08), (0.37, 2.15, -4.54), "#8a857d", variant=variant, object_id=3)
    _box(scene, (0.55, 0.42, 0.55), (1.05, 1.22, -4.2), "#34373a", variant=variant, object_id=5)
    _box(scene, (0.46, 0.72, 0.46), (1.05, 1.78, -4.2), "#9aa1a2", variant=variant, object_id=5)
    _box(scene, (0.5, 0.08, 0.5), (1.05, 2.18, -4.2), "#292b2d", variant=variant, object_id=5)
    # Work table and deterministic fruit placement; run manifests randomize these.
    _box(scene, (3.3, 0.16, 1.5), (2.7, 1.0, -1.4), "#77736b", variant=variant, object_id=3)
    for x, z, color, oid in ((2.0, -1.25, "#b06043", 6), (2.55, -1.5, "#a7a04a", 7), (3.0, -1.15, "#718d55", 8), (3.45, -1.55, "#b96d45", 9)):
        _sphere(scene, 0.18, (x, 1.22, z), color, variant=variant, object_id=oid)
    # Simple robot pedestal and links for viewport context only.
    _box(scene, (0.7, 0.25, 0.7), (0.0, 0.13, -1.4), "#585858", variant=variant, object_id=10)
    _box(scene, (0.25, 1.15, 0.25), (0.0, 0.8, -1.4), "#d0d0d0", variant=variant, object_id=10)
    _box(scene, (0.25, 0.25, 1.35), (0.0, 1.35, -2.0), "#bdbdbd", variant=variant, object_id=10)

    scene.add(gfx.AmbientLight("#ffffff", 0.42))
    light = gfx.DirectionalLight("#ffffff", 2.1)
    light.local.position = (4.0, 8.0, 5.0)
    scene.add(light)
    return scene, cup_door


def _build_factory(variant: str) -> tuple[gfx.Scene, None]:
    scene = gfx.Scene()
    scene.add(gfx.Background.from_color("#181818"))
    _room(scene, variant)
    _box(scene, (5.5, 0.18, 1.15), (-0.8, 0.8, -0.8), "#4b4d4e", variant=variant, object_id=3)
    for i, (x, color) in enumerate(((-2.2, "#8c7358"), (-0.8, "#9c835f"), (0.7, "#786754"))):
        _box(scene, (0.72, 0.55 + i * 0.08, 0.72), (x, 1.18, -0.8), color, variant=variant, object_id=6 + i)
    for i, (x, color) in enumerate(((-5.2, "#5a6470"), (0.0, "#665d54"), (5.2, "#4f665b"))):
        _box(scene, (3.0, 1.9, 2.2), (x, 1.0, -4.2), color, variant=variant, object_id=12 + i)
        _box(scene, (2.4, 1.25, 0.08), (x, 0.75, -3.05), "#242424", variant=variant, object_id=12 + i)
    _box(scene, (0.8, 0.25, 0.8), (1.2, 0.13, 1.2), "#565656", variant=variant, object_id=10)
    _box(scene, (0.28, 1.35, 0.28), (1.2, 0.9, 1.2), "#cacaca", variant=variant, object_id=10)
    _box(scene, (1.4, 0.28, 0.28), (0.55, 1.45, 0.8), "#b8b8b8", variant=variant, object_id=10)

    scene.add(gfx.AmbientLight("#ffffff", 0.42))
    light = gfx.DirectionalLight("#ffffff", 2.1)
    light.local.position = (4.0, 8.0, 5.0)
    scene.add(light)
    return scene, None


def _get_canvas_and_renderer(width: int, height: int) -> tuple[RenderCanvas, gfx.WgpuRenderer]:
    global _cached_canvas, _cached_renderer, _cached_size
    if _cached_canvas is None or _cached_renderer is None or _cached_size != (width, height):
        _ensure_device()
        _cached_canvas = RenderCanvas(size=(width, height), pixel_ratio=1)
        _cached_renderer = gfx.WgpuRenderer(_cached_canvas)
        _cached_size = (width, height)
    return _cached_canvas, _cached_renderer


def render_png(req: RenderRequest) -> bytes:
    if req.scene not in {"kitchen", "factory"}:
        raise ValueError("scene must be 'kitchen' or 'factory'")
    if req.variant not in {"rgb", "seg"}:
        raise ValueError("variant must be 'rgb' or 'seg'")

    width = max(320, min(int(req.width), 1600))
    height = max(180, min(int(req.height), 1000))

    with _lock:
        global _cached_scene, _cached_scene_key, _cup_door_mesh
        canvas, renderer = _get_canvas_and_renderer(width, height)

        scene_key = f"{req.scene}:{req.variant}"
        if _cached_scene is None or _cached_scene_key != scene_key:
            if req.scene == "kitchen":
                _cached_scene, _cup_door_mesh = _build_kitchen(req.variant)
            else:
                _cached_scene, _cup_door_mesh = _build_factory(req.variant)
            _cached_scene_key = scene_key

        if _cup_door_mesh is not None:
            _cup_door_mesh.local.rotation = la.quat_from_euler((0.0, math.radians(req.door_angle), 0.0), order="XYZ")

        target = (0.0, 1.1, -1.8) if req.scene == "kitchen" else (0.0, 1.0, -1.3)
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
            renderer.render(_cached_scene, camera)

        frame = np.asarray(canvas.draw())
        out = io.BytesIO()
        # Fast PNG compression level 1 encodes in <2ms with zero compression artifacting
        Image.fromarray(frame, mode="RGBA").convert("RGB").save(out, format="PNG", compress_level=1, optimize=False)
        return out.getvalue()


def render_glb_png(
    model_path: Path,
    *,
    width: int = 960,
    height: int = 540,
    yaw: float = 34.0,
    pitch: float = 18.0,
    zoom: float = 1.0,
) -> bytes:
    """Rasterize the actual generated GLB through native Vulkan.

    This deliberately does not use the procedural kitchen/factory renderer:
    the returned pixels are produced from the binary GLB selected by TRELLIS.
    """
    if not model_path.is_file() or model_path.suffix.lower() != ".glb":
        raise ValueError("Generated GLB is unavailable for native preview.")
    width = max(320, min(int(width), 1600))
    height = max(180, min(int(height), 1000))
    zoom = max(0.45, min(float(zoom), 4.0))

    with _lock:
        canvas, renderer = _get_canvas_and_renderer(width, height)
        cache_key = str(model_path.resolve())
        try:
            cached = _glb_cache.get(cache_key)
            if cached is None:
                gltf = gfx.load_gltf(cache_key, quiet=True)
                asset = gltf.scenes[0]
                bounds = asset.get_world_bounding_box()
                if bounds is None:
                    raise ValueError("Generated GLB has no renderable bounds.")
                low = np.asarray(bounds[0], dtype=float)
                high = np.asarray(bounds[1], dtype=float)
                _glb_cache[cache_key] = (asset, low, high)
            else:
                asset, low, high = cached
        except Exception as exc:
            raise ValueError(f"Native Vulkan renderer could not load GLB: {exc}") from exc
        target = (low + high) / 2.0
        radius = max(float(np.linalg.norm(high - low) / 2.0), 0.05)

        scene = gfx.Scene()
        scene.add(gfx.Background.from_color("#161616"))
        scene.add(asset)
        scene.add(gfx.AmbientLight("#ffffff", 0.65))
        key = gfx.DirectionalLight("#ffffff", 2.4)
        key.local.position = (radius * 3.0, radius * 5.0, radius * 4.0)
        scene.add(key)
        fill = gfx.DirectionalLight("#9aa4ad", 1.0)
        fill.local.position = (-radius * 3.0, radius * 2.0, -radius * 2.0)
        scene.add(fill)

        camera = gfx.PerspectiveCamera(35, width / height, depth_range=(0.01, 1000.0))
        yaw_r = math.radians(yaw)
        pitch_r = math.radians(max(-45.0, min(pitch, 75.0)))
        distance = max(radius * 3.1 * zoom, radius * 1.6)
        cp = math.cos(pitch_r)
        camera.local.position = (
            float(target[0] + distance * math.sin(yaw_r) * cp),
            float(target[1] + distance * math.sin(pitch_r)),
            float(target[2] + distance * math.cos(yaw_r) * cp),
        )
        camera.show_pos(tuple(float(v) for v in target))

        @canvas.request_draw
        def draw() -> None:
            renderer.render(scene, camera)

        frame = np.asarray(canvas.draw())
        out = io.BytesIO()
        Image.fromarray(frame, mode="RGBA").convert("RGB").save(out, format="PNG", compress_level=1, optimize=False)
        return out.getvalue()


def render_world_glb_png(
    placements: list[WorldPlacement],
    *,
    width: int = 960,
    height: int = 540,
    yaw: float = 34.0,
    pitch: float = 18.0,
    zoom: float = 1.0,
) -> bytes:
    """Rasterize every persisted generated GLB as one assembled world.

    This accepts only local GLBs that are also referenced by the active stage;
    it never falls back to the old procedural kitchen/factory renderer.  The
    USD stage is Z-up while the generated GLBs are Y-up, hence the explicit
    position/scale axis conversion at the root of each loaded GLB scene.
    """
    if not placements:
        raise ValueError("Active world has no generated GLB placements.")
    width = max(320, min(int(width), 1600))
    height = max(180, min(int(height), 1000))
    zoom = max(0.45, min(float(zoom), 4.0))

    with _lock:
        global _world_scene_cache_key, _world_scene_cache
        canvas, renderer = _get_canvas_and_renderer(width, height)
        cache_entries: list[str] = []
        for placement in placements:
            model = placement.model_path.resolve()
            if not model.is_file() or model.suffix.lower() != ".glb":
                raise ValueError(f"Generated GLB is unavailable for {placement.asset_id}.")
            stamp = model.stat().st_mtime_ns
            cache_entries.append(f"{placement.asset_id}:{model}:{stamp}:{placement.translation}:{placement.usd_scale}")
        cache_key = "|".join(cache_entries)

        if _world_scene_cache is None or _world_scene_cache_key != cache_key:
            scene = gfx.Scene()
            scene.add(gfx.Background.from_color("#161616"))
            bounds_low: list[np.ndarray] = []
            bounds_high: list[np.ndarray] = []
            for placement in placements:
                model = placement.model_path.resolve()
                try:
                    # WorldObjects have one parent. Load each GLB once into
                    # this persistent composed scene, then reuse that exact
                    # graph for camera updates until the stage or GLBs change.
                    asset = gfx.load_gltf(str(model), quiet=True).scenes[0]
                    usd_x, usd_y, usd_z = placement.translation
                    usd_width, usd_depth, usd_height = placement.usd_scale
                    # USD (X, Y, Z) = glTF (X, -Z, Y) for our visual.usdc authoring.
                    asset.local.position = (usd_x, usd_z, -usd_y)
                    asset.local.scale = (usd_width, usd_height, usd_depth)
                    bounds = asset.get_world_bounding_box()
                    if bounds is None:
                        raise ValueError("GLB has no renderable bounds.")
                    bounds_low.append(np.asarray(bounds[0], dtype=float))
                    bounds_high.append(np.asarray(bounds[1], dtype=float))
                    scene.add(asset)
                except Exception as exc:
                    raise ValueError(f"Native Vulkan renderer could not load {placement.asset_id}: {exc}") from exc
            low = np.min(np.stack(bounds_low), axis=0)
            high = np.max(np.stack(bounds_high), axis=0)
            target = (low + high) / 2.0
            radius = max(float(np.linalg.norm(high - low) / 2.0), 0.2)
            scene.add(gfx.AmbientLight("#ffffff", 0.68))
            key = gfx.DirectionalLight("#ffffff", 2.5)
            key.local.position = (float(target[0] + radius * 2.5), float(target[1] + radius * 5.0), float(target[2] + radius * 3.5))
            scene.add(key)
            fill = gfx.DirectionalLight("#9aa4ad", 1.1)
            fill.local.position = (float(target[0] - radius * 3.0), float(target[1] + radius * 2.0), float(target[2] - radius * 2.0))
            scene.add(fill)
            _world_scene_cache = (scene, low, high)
            _world_scene_cache_key = cache_key
        else:
            scene, low, high = _world_scene_cache

        target = (low + high) / 2.0
        radius = max(float(np.linalg.norm(high - low) / 2.0), 0.2)

        camera = gfx.PerspectiveCamera(42, width / height, depth_range=(0.01, 1000.0))
        yaw_r = math.radians(yaw)
        pitch_r = math.radians(max(-45.0, min(pitch, 75.0)))
        distance = max(radius * 3.5 * zoom, radius * 1.8)
        cp = math.cos(pitch_r)
        camera.local.position = (
            float(target[0] + distance * math.sin(yaw_r) * cp),
            float(target[1] + distance * math.sin(pitch_r)),
            float(target[2] + distance * math.cos(yaw_r) * cp),
        )
        camera.show_pos(tuple(float(value) for value in target))

        @canvas.request_draw
        def draw() -> None:
            renderer.render(scene, camera)

        frame = np.asarray(canvas.draw())
        out = io.BytesIO()
        Image.fromarray(frame, mode="RGBA").convert("RGB").save(out, format="PNG", compress_level=1, optimize=False)
        return out.getvalue()
