"""Local-only TRELLIS.2 gateway for RobotWorld.

Run this file with the dedicated CUDA Python environment at
``D:\\TRELLIS.2-runtime\\.venv``.  It intentionally exposes only the two
endpoints RobotWorld uses, serializes generation, and generates a GLB from the
real Microsoft checkpoint.  It has no network listener outside loopback.
"""
from __future__ import annotations

import asyncio
import gc
import io
import os
import sys
import time
from pathlib import Path
from typing import Annotated

# Must be set before importing torch so PyTorch's allocator can avoid a large
# number of fragmented small blocks during the long 1024-cascade job.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
os.environ.setdefault("ATTN_BACKEND", "sdpa")
os.environ.setdefault("TRELLIS_INPUT_ALPHA", "1")
os.environ.setdefault("U2NET_HOME", r"D:\TRELLIS.2-runtime\u2net-models")

RUNTIME_DIR = Path(os.environ.get("TRELLIS_RUNTIME_DIR", r"D:\TRELLIS.2-runtime")).resolve()
MODEL_DIR = Path(os.environ.get("TRELLIS_MODEL_DIR", r"D:\TRELLIS.2-4B")).resolve()
DINOV3_DIR = Path(os.environ.get("TRELLIS_DINOV3_DIR", r"D:\DINOv3")).resolve()
MODEL_ID = "microsoft/TRELLIS.2-4B"
PIPELINE_TYPES = {512: "512", 1024: "1024_cascade", 1536: "1536_cascade"}
os.environ.setdefault("TRELLIS_DINOV3_DIR", str(DINOV3_DIR))

if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

import torch  # noqa: E402
from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from PIL import Image, UnidentifiedImageError  # noqa: E402
from rembg import new_session, remove  # noqa: E402

from trellis2.pipelines import Trellis2ImageTo3DPipeline  # noqa: E402
import o_voxel  # noqa: E402

app = FastAPI(title="RobotWorld local TRELLIS.2 gateway", version="1.0")
_generation_lock = asyncio.Lock()
_pipeline: Trellis2ImageTo3DPipeline | None = None
_matting_session = None
_last_model_use = 0.0
_started_at = time.monotonic()
# Keep the CPU-offloaded pipeline only long enough to amortize a short batch.
# Five minutes matches the desktop idle policy; setting this to zero opts into
# immediate unload after every request.
_idle_unload_seconds = max(0, int(os.environ.get("TRELLIS_IDLE_UNLOAD_S", "300")))


def _preflight() -> dict:
    if not MODEL_DIR.is_dir() or not (MODEL_DIR / "pipeline.json").is_file():
        raise HTTPException(503, f"TRELLIS checkpoint is unavailable at {MODEL_DIR}")
    if not torch.cuda.is_available():
        raise HTTPException(503, "CUDA is unavailable to the TRELLIS runtime.")
    free, total = torch.cuda.mem_get_info()
    return {
        "freeVramBytes": int(free),
        "totalVramBytes": int(total),
        "gpu": torch.cuda.get_device_name(0),
        "conditioningModel": "facebook/dinov3-vitl16-pretrain-lvd1689m",
        "conditioningPath": str(DINOV3_DIR),
        "conditioningReady": (DINOV3_DIR / "config.json").is_file(),
    }


def _get_pipeline() -> Trellis2ImageTo3DPipeline:
    global _pipeline
    if _pipeline is None:
        pipeline = Trellis2ImageTo3DPipeline.from_pretrained(str(MODEL_DIR))
        # The upstream implementation moves every stage to CUDA only while it
        # is sampled/decoded, then returns it to CPU.  Do not call cuda() here.
        pipeline.low_vram = True
        pipeline.to(torch.device("cuda"))
        _pipeline = pipeline
    return _pipeline


def _unload_pipeline() -> None:
    """Release the 4B pipeline after idle time instead of retaining system RAM.

    The gateway is a local, single-flight worker. Keeping model weights in
    CPU RAM after a completed job is not useful while the UI is idle; the next
    job can explicitly pay the model-load cost rather than degrading the whole
    workstation. ``empty_cache`` releases unused allocator blocks only, so the
    Python reference must be dropped first.
    """
    global _pipeline, _matting_session
    _pipeline = None
    _matting_session = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


async def _idle_model_reclaimer() -> None:
    while True:
        await asyncio.sleep(30)
        if _generation_lock.locked():
            continue
        idle_since = _last_model_use or _started_at
        if _idle_unload_seconds > 0 and time.monotonic() - idle_since >= _idle_unload_seconds:
            _unload_pipeline()
            # CUDA/Python allocators can retain many gigabytes after all model
            # references are gone. A real worker exit is the only reliable
            # process-level release on Windows; RobotWorld lazily starts this
            # local worker again on the next requested generation.
            os._exit(0)


@app.on_event("startup")
async def _start_idle_model_reclaimer() -> None:
    app.state.idle_model_reclaimer = asyncio.create_task(_idle_model_reclaimer())


@app.on_event("shutdown")
async def _stop_idle_model_reclaimer() -> None:
    task = getattr(app.state, "idle_model_reclaimer", None)
    if task is not None:
        task.cancel()
    _unload_pipeline()


def _prepare_alpha_source(raw: bytes) -> Image.Image:
    """Create a real foreground matte before the TRELLIS 4B pass.

    The U2NetP session is CPU-only by design: it avoids contending with the
    12 GiB GPU while the 1024 cascade is allocated, and is retained across
    jobs to avoid model reloads.
    """
    global _matting_session
    if _matting_session is None:
        _matting_session = new_session("u2netp")
    output = remove(raw, session=_matting_session)
    prepared = Image.open(io.BytesIO(output)).convert("RGBA")
    alpha = prepared.getchannel("A").getextrema()
    if alpha[0] == 255:
        raise RuntimeError("Foreground preprocessing produced no alpha matte.")
    return prepared


@app.get("/v1/capabilities")
def capabilities() -> dict:
    memory = _preflight()
    idle_for = max(0.0, time.monotonic() - _last_model_use) if _last_model_use else None
    return {
        "schemaVersion": "robotworld.trellis2.v1",
        "model": MODEL_ID,
        "defaultPipelineType": PIPELINE_TYPES[1024],
        "supportedResolutions": sorted(PIPELINE_TYPES),
        "precision": "native-bf16-fp16",
        "output": "static-pbr-glb",
        "pbr": True,
        "articulation": False,
        "lowVram": True,
        "singleFlight": True,
        "offlineConditioning": memory["conditioningReady"],
        "foregroundPreprocessor": "u2netp-local-cpu",
        "unloadAfterJob": _idle_unload_seconds == 0,
        "idleUnloadSeconds": _idle_unload_seconds,
        "pipelineResident": _pipeline is not None,
        "idleSecondsSinceLastUse": round(idle_for, 1) if idle_for is not None else None,
        **memory,
    }


@app.post("/v1/image-to-3d")
async def image_to_3d(
    image: Annotated[UploadFile, File(...)],
    schema_version: Annotated[str, Form(...)],
    model: Annotated[str, Form(...)],
    runtime: Annotated[str, Form()] = "native",
    resolution: Annotated[int, Form()] = 1024,
) -> Response:
    if schema_version != "robotworld.trellis2.v1" or model != MODEL_ID or runtime != "native":
        raise HTTPException(422, "Unsupported RobotWorld TRELLIS.2 request contract.")
    pipeline_type = PIPELINE_TYPES.get(resolution)
    if pipeline_type is None:
        raise HTTPException(422, "Resolution must be one of 512, 1024, or 1536.")
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, "Image must be JPEG, PNG, or WebP.")
    raw = await image.read()
    if not raw or len(raw) > 20 * 1024 * 1024:
        raise HTTPException(413, "Image is empty or exceeds the 20 MiB limit.")
    try:
        source = Image.open(io.BytesIO(raw))
        source.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(422, "Uploaded image could not be decoded.") from exc

    # A 12 GiB GPU cannot safely host two 1024 jobs.  Queue rather than run a
    # second generation concurrently and making both jobs fail unpredictably.
    async with _generation_lock:
        _preflight()
        pipeline = None
        outputs = None
        mesh = None
        glb = None
        try:
            source = await asyncio.to_thread(_prepare_alpha_source, raw)
            pipeline = await asyncio.to_thread(_get_pipeline)
            outputs, (_, _, resolution) = await asyncio.to_thread(
                pipeline.run,
                source,
                pipeline_type=pipeline_type,
                return_latent=True,
            )
            mesh = outputs[0]
            mesh.simplify(16_777_216)  # nvdiffrast's supported triangle limit
            glb = await asyncio.to_thread(
                o_voxel.postprocess.to_glb,
                vertices=mesh.vertices,
                faces=mesh.faces,
                attr_volume=mesh.attrs,
                coords=mesh.coords,
                attr_layout=pipeline.pbr_attr_layout,
                grid_size=resolution,
                aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
                decimation_target=500_000,
                texture_size=2048,
                remesh=True,
                remesh_band=1,
                remesh_project=0,
                verbose=True,
            )
            # A BytesIO target has no filename extension.  State the GLB
            # exporter explicitly so trimesh cannot resolve its file type to
            # "none" after the expensive mesh/PBR bake has completed.
            payload = glb.export(file_type="glb", extension_webp=True)
            if len(payload) < 20 or payload[:4] != b"glTF":
                raise RuntimeError("O-Voxel did not export a valid binary GLB.")
            global _last_model_use
            _last_model_use = time.monotonic()
            return Response(payload, media_type="model/gltf-binary")
        except torch.OutOfMemoryError as exc:
            torch.cuda.empty_cache()
            raise HTTPException(507, "TRELLIS.2 ran out of GPU memory during native 1024-cascade generation.") from exc
        except HTTPException:
            raise
        except Exception as exc:
            torch.cuda.empty_cache()
            raise HTTPException(500, f"TRELLIS.2 generation failed: {exc}") from exc
        finally:
            # Drop per-job tensors immediately. Keep the CPU-offloaded pipeline
            # for a short active batch, then let the idle reclaimer release it.
            del pipeline, outputs, mesh, glb
            gc.collect()
            if _idle_unload_seconds == 0:
                _unload_pipeline()
            elif torch.cuda.is_available():
                torch.cuda.empty_cache()
