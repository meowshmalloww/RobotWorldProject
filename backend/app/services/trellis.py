"""Client for the versioned TRELLIS.2 image-to-GLB gateway.

The Microsoft repository exposes a Python pipeline, not a stable network API.
RobotWorld therefore owns a narrow, testable gateway contract and validates
the returned artifact before it can enter the USD/physics compiler.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import trimesh

from . import settings_store


class TrellisError(RuntimeError):
    pass


async def _settings() -> dict[str, Any]:
    flat = await settings_store.get_flat()
    endpoint = str(flat.get("models.trellisEndpoint") or "").strip().rstrip("/")
    if not endpoint:
        raise TrellisError("TRELLIS.2 gateway is not configured in Settings -> Models.")
    if not endpoint.startswith(("http://", "https://")):
        raise TrellisError("TRELLIS.2 gateway must use http:// or https://.")
    return {
        "endpoint": endpoint,
        "key": str(flat.get("models.trellisApiKey") or ""),
        "model": str(flat.get("models.trellisModel") or "microsoft/TRELLIS.2-4B"),
        "timeout": float(flat.get("models.trellisTimeoutS") or 300),
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


async def probe() -> dict[str, Any]:
    cfg = await _settings()
    headers = {"Authorization": f"Bearer {cfg['key']}"} if cfg["key"] else {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{cfg['endpoint']}/v1/capabilities", headers=headers)
    if response.status_code >= 400:
        raise TrellisError(f"TRELLIS.2 gateway returned HTTP {response.status_code}.")
    data = response.json()
    if data.get("schemaVersion") != "robotworld.trellis2.v1" or data.get("model") != cfg["model"]:
        raise TrellisError("TRELLIS.2 gateway capability/model mismatch.")
    return data


async def generate_glb(image_url: str, output: Path) -> tuple[list[dict[str, Any]], int]:
    cfg = await _settings()
    await probe()
    image, content_type, filename = await _download_image(image_url)
    headers = {"Authorization": f"Bearer {cfg['key']}"} if cfg["key"] else {}
    files = {"image": (filename, image, content_type)}
    data = {"schema_version": "robotworld.trellis2.v1", "model": cfg["model"]}
    # Image-to-3D is expensive and not idempotent.  Do not automatically retry
    # an ambiguous timeout: the first generation may still be consuming GPU.
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(cfg["timeout"], connect=20.0)) as client:
            response = await client.post(f"{cfg['endpoint']}/v1/image-to-3d", headers=headers, files=files, data=data)
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
    parts = [{"id": "trellis_visual", "name": "TRELLIS.2 PBR visual mesh", "type": "visual", "source": cfg["model"]}]
    return parts, vertices
