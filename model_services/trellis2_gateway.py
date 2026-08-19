"""Production RobotWorld gateway for Microsoft's official TRELLIS.2 pipeline.

Run this file inside a separately installed TRELLIS.2 Linux environment.  It
loads the real 4B checkpoint once and returns the actual PBR GLB bytes; there
is no placeholder or procedural fallback.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import o_voxel
import torch
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image
from trellis2.pipelines import Trellis2ImageTo3DPipeline

MODEL_ID = os.environ.get("TRELLIS2_MODEL", "microsoft/TRELLIS.2-4B")
TOKEN = os.environ.get("ROBOTWORLD_GATEWAY_TOKEN", "")
MAX_IMAGE_BYTES = 20 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    pipeline = Trellis2ImageTo3DPipeline.from_pretrained(MODEL_ID)
    pipeline.cuda()
    app.state.pipeline = pipeline
    app.state.lock = asyncio.Lock()
    yield
    del pipeline
    torch.cuda.empty_cache()


app = FastAPI(title="RobotWorld TRELLIS.2 Gateway", version="1.0.0", lifespan=lifespan)


def authorize(authorization: str | None) -> None:
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "invalid gateway token")


@app.get("/healthz")
async def healthz():
    return {"ready": hasattr(app.state, "pipeline"), "model": MODEL_ID}


@app.get("/v1/capabilities")
async def capabilities(authorization: str | None = Header(default=None)):
    authorize(authorization)
    return {
        "schemaVersion": "robotworld.trellis2.v1",
        "model": MODEL_ID,
        "output": "model/gltf-binary",
        "articulation": False,
        "pbr": True,
    }


def generate(image_path: Path) -> bytes:
    image = Image.open(image_path).convert("RGB")
    mesh = app.state.pipeline.run(image)[0]
    mesh.simplify(16_777_216)
    glb = o_voxel.postprocess.to_glb(
        vertices=mesh.vertices,
        faces=mesh.faces,
        attr_volume=mesh.attrs,
        coords=mesh.coords,
        attr_layout=mesh.layout,
        voxel_size=mesh.voxel_size,
        aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
        decimation_target=1_000_000,
        texture_size=4096,
        remesh=True,
        remesh_band=1,
        remesh_project=0,
        verbose=True,
    )
    with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as handle:
        output = Path(handle.name)
    try:
        glb.export(str(output), extension_webp=True)
        return output.read_bytes()
    finally:
        output.unlink(missing_ok=True)


@app.post("/v1/image-to-3d")
async def image_to_3d(
    image: UploadFile = File(...),
    schema_version: str = Form(...),
    model: str = Form(...),
    authorization: str | None = Header(default=None),
):
    authorize(authorization)
    if schema_version != "robotworld.trellis2.v1" or model != MODEL_ID:
        raise HTTPException(409, "schema or model mismatch")
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, "unsupported image type")
    content = await image.read(MAX_IMAGE_BYTES + 1)
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "image exceeds 20 MiB")
    suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[image.content_type]
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        input_path = Path(handle.name)
        handle.write(content)
    try:
        async with app.state.lock:
            result = await asyncio.to_thread(generate, input_path)
    finally:
        input_path.unlink(missing_ok=True)
    return Response(result, media_type="model/gltf-binary", headers={"X-RobotWorld-Model": MODEL_ID})


if __name__ == "__main__":
    import uvicorn

    # Remote access should be through an authenticated TLS/VPN gateway.  The
    # safe standalone default is loopback only.
    uvicorn.run(app, host=os.environ.get("GATEWAY_HOST", "127.0.0.1"), port=int(os.environ.get("GATEWAY_PORT", "8091")))
