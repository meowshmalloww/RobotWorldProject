"""Client for the versioned TRELLIS.2 image-to-GLB gateway.

The Microsoft repository exposes a Python pipeline, not a stable network API.
RobotWorld therefore owns a narrow, testable gateway contract and validates
the returned artifact before it can enter the USD/physics compiler.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import trimesh

from . import settings_store


class TrellisError(RuntimeError):
    pass


_local_start_lock = asyncio.Lock()
_local_gateway_process: asyncio.subprocess.Process | None = None


async def _settings() -> dict[str, Any]:
    flat = await settings_store.get_flat()
    runtime_name = str(flat.get("models.trellisRuntime") or "gguf").strip().lower()
    if runtime_name not in {"gguf", "native"}:
        raise TrellisError("TRELLIS.2 runtime must be 'gguf' or 'native'.")
    default_endpoint = "http://127.0.0.1:8189" if runtime_name == "gguf" else "http://127.0.0.1:8188"
    endpoint = str(flat.get("models.trellisEndpoint") or default_endpoint).strip().rstrip("/")
    # Older settings stored the native port while the runtime was hard-coded.
    # Remap loopback only; never rewrite an operator-provided remote endpoint.
    parsed_endpoint = urllib.parse.urlsplit(endpoint)
    if runtime_name == "gguf" and parsed_endpoint.hostname in {"127.0.0.1", "localhost"} and parsed_endpoint.port == 8188:
        endpoint = "http://127.0.0.1:8189"
    if not endpoint:
        raise TrellisError("TRELLIS.2 gateway is not configured in Settings -> Models.")
    if not endpoint.startswith(("http://", "https://")):
        raise TrellisError("TRELLIS.2 gateway must use http:// or https://.")
    return {
        "endpoint": endpoint,
        "key": str(flat.get("models.trellisApiKey") or ""),
        "model": str(flat.get("models.trellisModel") or "microsoft/TRELLIS.2-4B"),
        "runtime": runtime_name,
        "resolution": int(flat.get("models.trellisResolution") or 1024),
        "seed": max(0, min(int(flat.get("models.trellisSeed") or 1048576), 2**31 - 1)),
        "background_removal": bool(flat.get("models.trellisBackgroundRemoval", True)),
        "gguf_path": str(flat.get("models.trellisGgufPath") or r"D:\TRELLIS.2-4B-Q4-GGUF\q4"),
        "cpp_path": str(flat.get("models.trellisCppPath") or r"D:\trellis.cpp-v0.6.0-cuda12"),
        # Queue wait is included in the HTTP read timeout. A four-object
        # single-flight batch on a 12 GiB laptop GPU can legitimately exceed
        # 30 minutes even though each individual generation is healthy.
        "timeout": max(7200.0, float(flat.get("models.trellisTimeoutS") or 7200)),
    }


def _public_https_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise TrellisError("Source image must be a public HTTPS URL.")
    if parsed.port not in (None, 443):
        raise TrellisError("Source image URL must use the standard HTTPS port.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise TrellisError("Source image hostname could not be resolved.") from exc
    if not addresses:
        raise TrellisError("Source image hostname has no address.")
    for raw in addresses:
        ip = ipaddress.ip_address(raw)
        if not ip.is_global:
            raise TrellisError("Private, loopback, link-local, and reserved image hosts are blocked.")
    return value


async def _download_image(url: str, *, max_bytes: int = 20 * 1024 * 1024) -> tuple[bytes, str, str]:
    url = _public_https_url(url)
    async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=15.0), follow_redirects=False) as client:
        response = await client.get(url, headers={"Accept": "image/*"})
    if response.is_redirect:
        raise TrellisError("Image redirects are rejected; store the canonical final HTTPS URL.")
    if response.status_code >= 400:
        raise TrellisError(f"Source image fetch failed with HTTP {response.status_code}.")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise TrellisError(f"Unsupported source image type '{content_type or 'unknown'}'.")
    if not response.content or len(response.content) > max_bytes:
        raise TrellisError("Source image is empty or exceeds the 20 MiB limit.")
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[content_type]
    return response.content, content_type, f"source{ext}"


async def _read_capabilities(cfg: dict[str, Any]) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {cfg['key']}"} if cfg["key"] else {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{cfg['endpoint']}/health" if cfg["runtime"] == "gguf" else f"{cfg['endpoint']}/v1/capabilities", headers=headers)
    if response.status_code >= 400:
        raise TrellisError(f"TRELLIS.2 gateway returned HTTP {response.status_code}.")
    if cfg["runtime"] == "gguf":
        if response.text.strip().lower() != "ok":
            raise TrellisError("trellis.cpp health response was not 'ok'.")
        return {"schemaVersion": "trellis.cpp.v0.6", "model": cfg["model"], "runtime": "gguf", "precision": "Q4", "supportedResolutions": [512, 1024, 1536], "output": "glb", "conditioningPath": cfg["gguf_path"]}
    data = response.json()
    if data.get("schemaVersion") != "robotworld.trellis2.v1" or data.get("model") != cfg["model"]:
        raise TrellisError("TRELLIS.2 gateway capability/model mismatch.")
    supported = data.get("supportedResolutions") or []
    if cfg["resolution"] not in supported:
        raise TrellisError(f"TRELLIS.2 gateway does not support {cfg['resolution']} resolution.")
    return data


async def _start_local_gateway(cfg: dict[str, Any]) -> bool:
    """Start the fixed local worker after its process-level idle shutdown."""
    global _local_gateway_process
    parsed = urllib.parse.urlsplit(str(cfg["endpoint"]))
    expected_port = 8189 if cfg["runtime"] == "gguf" else 8188
    if parsed.hostname not in {"127.0.0.1", "localhost"} or (parsed.port or 80) != expected_port:
        return False
    if cfg["runtime"] == "gguf":
        executable = Path(cfg["cpp_path"]).resolve() / "trellis-server.exe"
        models = Path(cfg["gguf_path"]).resolve()
        if not executable.is_file() or not models.is_dir():
            return False
        async with _local_start_lock:
            try:
                return bool(await _read_capabilities(cfg))
            except (httpx.HTTPError, TrellisError):
                pass
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            _local_gateway_process = await asyncio.create_subprocess_exec(
                str(executable), "--host", "127.0.0.1", "--port", str(expected_port), "--models", str(models), "--require-gpu",
                cwd=str(executable.parent), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, creationflags=creationflags,
            )
            for _ in range(180):
                await asyncio.sleep(0.5)
                if _local_gateway_process.returncode is not None:
                    return False
                try:
                    await _read_capabilities(cfg)
                    return True
                except (httpx.HTTPError, TrellisError):
                    continue
            return False
    runtime = Path(os.environ.get("TRELLIS_RUNTIME_DIR", r"D:\TRELLIS.2-runtime"))
    executable = runtime / ".venv" / "Scripts" / "python.exe"
    gateway_dir = Path(__file__).resolve().parents[2]
    if not executable.is_file() or not (gateway_dir / "trellis_gateway.py").is_file():
        return False

    async with _local_start_lock:
        try:
            return bool(await _read_capabilities(cfg))
        except (httpx.HTTPError, TrellisError):
            pass
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        _local_gateway_process = await asyncio.create_subprocess_exec(
            str(executable), "-m", "uvicorn", "trellis_gateway:app",
            "--host", "127.0.0.1", "--port", "8188",
            cwd=str(gateway_dir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=creationflags,
        )
        for _ in range(90):
            await asyncio.sleep(0.5)
            if _local_gateway_process.returncode is not None:
                return False
            try:
                await _read_capabilities(cfg)
                return True
            except (httpx.HTTPError, TrellisError):
                continue
        return False


async def probe() -> dict[str, Any]:
    cfg = await _settings()
    try:
        return await _read_capabilities(cfg)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        if await _start_local_gateway(cfg):
            return await _read_capabilities(cfg)
        raise TrellisError("Local TRELLIS.2 worker could not be started.") from exc


async def generate_glb(image_url: str, output: Path) -> tuple[list[dict[str, Any]], int]:
    cfg = await _settings()
    await probe()
    image, content_type, filename = await _download_image(image_url)
    headers = {"Authorization": f"Bearer {cfg['key']}"} if cfg["key"] else {}
    files = {"image": (filename, image, content_type)}
    data = {
        "schema_version": "robotworld.trellis2.v1",
        "model": cfg["model"],
        "runtime": cfg["runtime"],
        "resolution": str(cfg["resolution"]),
        "seed": str(cfg["seed"]),
        "background_removal": "true" if cfg["background_removal"] else "false",
    }
    target = f"{cfg['endpoint']}/generate" if cfg["runtime"] == "gguf" else f"{cfg['endpoint']}/v1/image-to-3d"
    if cfg["runtime"] == "gguf":
        data = {"seed": str(cfg["seed"]), "resolution": str(cfg["resolution"]), "bg_removal": "birefnet" if cfg["background_removal"] else "threshold"}
    # Image-to-3D is expensive and not idempotent.  Do not automatically retry
    # an ambiguous timeout: the first generation may still be consuming GPU.
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(cfg["timeout"], connect=20.0)) as client:
            response = await client.post(target, headers=headers, files=files, data=data)
    except httpx.TimeoutException as exc:
        raise TrellisError("TRELLIS.2 generation timed out; check the gateway job before retrying.") from exc
    if response.status_code >= 400:
        raise TrellisError(f"TRELLIS.2 generation failed with HTTP {response.status_code}: {response.text[:240]}")
    if len(response.content) < 20 or len(response.content) > 250 * 1024 * 1024 or response.content[:4] != b"glTF":
        raise TrellisError("TRELLIS.2 gateway did not return a valid-size binary GLB.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(response.content)
    try:
        loaded = trimesh.load(output, force="scene")
        geometries = list(loaded.geometry.values()) if isinstance(loaded, trimesh.Scene) else [loaded]
        vertices = sum(len(mesh.vertices) for mesh in geometries)
        if vertices <= 0:
            raise ValueError("no vertices")
    except Exception as exc:
        output.unlink(missing_ok=True)
        raise TrellisError(f"Generated GLB failed trimesh validation: {exc}") from exc
    parts = [{"id": "trellis_visual", "name": "TRELLIS.2 PBR visual mesh", "type": "visual", "source": cfg["model"], "runtime": cfg["runtime"], "resolution": cfg["resolution"], "seed": cfg["seed"]}]
    return parts, vertices
