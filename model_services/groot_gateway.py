"""RobotWorld REST gateway for NVIDIA GR00T's native ZMQ PolicyClient.

The actual checkpoint remains inside NVIDIA's supported Isaac-GR00T runtime.
This process converts RobotWorld's authenticated, versioned wire contract into
the checkpoint's nested NumPy modality contract.  It never fabricates or
replays actions.
"""
from __future__ import annotations

import base64
import io
import os
import time
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, Header, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

from gr00t.policy.server_client import PolicyClient

SCHEMA = "robotworld.policy.v1"
POLICY_ID = os.environ.get("GROOT_POLICY_ID", "nvidia/GR00T-N1.7-3B")
EMBODIMENT = os.environ.get("GROOT_EMBODIMENT", "robotworld-4dof-v1")
MODEL_REVISION = os.environ.get("GROOT_MODEL_REVISION", "")
MODEL_SHA256 = os.environ.get("GROOT_MODEL_SHA256", "")
NORMALIZATION_SHA256 = os.environ.get("GROOT_NORMALIZATION_SHA256", "")
ENVIRONMENT_SHA256 = os.environ.get("ROBOTWORLD_ENVIRONMENT_SHA256", "")
GATEWAY_TOKEN = os.environ.get("ROBOTWORLD_GATEWAY_TOKEN", "")
TRAINED = os.environ.get("GROOT_CHECKPOINT_TRAINED_FOR_EMBODIMENT", "false").lower() == "true"
ACTION_HORIZON = int(os.environ.get("GROOT_ACTION_HORIZON", "40"))
VIDEO_KEYS = tuple(os.environ.get("GROOT_VIDEO_KEYS", "front,wrist").split(","))
ARM_STATE_KEY = os.environ.get("GROOT_ARM_STATE_KEY", "single_arm")
GRIPPER_STATE_KEY = os.environ.get("GROOT_GRIPPER_STATE_KEY", "gripper")
ARM_ACTION_KEY = os.environ.get("GROOT_ARM_ACTION_KEY", "single_arm")
GRIPPER_ACTION_KEY = os.environ.get("GROOT_GRIPPER_ACTION_KEY", "gripper")


class ResetRequest(BaseModel):
    schemaVersion: str
    episodeId: str
    seed: int
    instruction: str
    environmentSha256: str


class VideoFrame(BaseModel):
    name: str
    mimeType: str
    dataBase64: str


class ActionRequest(BaseModel):
    schemaVersion: str
    episodeId: str
    sequence: int = Field(ge=0)
    simTimeNs: int
    deadlineNs: int
    instruction: str
    video: list[VideoFrame]
    state: dict[str, list[float]]
    requestedHorizon: int = Field(ge=1, le=40)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.policy = PolicyClient(
        host=os.environ.get("GROOT_HOST", "127.0.0.1"),
        port=int(os.environ.get("GROOT_PORT", "5555")),
        timeout_ms=int(os.environ.get("GROOT_TIMEOUT_MS", "15000")),
        api_token=os.environ.get("GROOT_API_TOKEN") or None,
        strict=False,
    )
    if not app.state.policy.ping():
        raise RuntimeError("GR00T native policy server is unavailable")
    yield


app = FastAPI(title="RobotWorld GR00T Gateway", version="1.0.0", lifespan=lifespan)


def authorize(authorization: str | None) -> None:
    if GATEWAY_TOKEN and authorization != f"Bearer {GATEWAY_TOKEN}":
        raise HTTPException(401, "invalid gateway token")


def checkpoint_manifest_ready() -> bool:
    return TRAINED and len(MODEL_SHA256) == 64 and len(NORMALIZATION_SHA256) == 64 and bool(MODEL_REVISION)


@app.get("/healthz")
async def healthz():
    return {"ready": bool(app.state.policy.ping()), "policyId": POLICY_ID}


@app.get("/v1/capabilities")
async def capabilities(authorization: str | None = Header(default=None)):
    authorize(authorization)
    return {
        "schemaVersion": SCHEMA,
        "policyId": POLICY_ID,
        "modelRevision": MODEL_REVISION,
        "modelSha256": MODEL_SHA256,
        "normalizationSha256": NORMALIZATION_SHA256,
        "environmentSha256": ENVIRONMENT_SHA256,
        "embodiment": EMBODIMENT,
        "checkpointTrainedForEmbodiment": checkpoint_manifest_ready(),
        "adapter": "groot_zmq",
        "observation": {
            "cameras": [{"name": "front", "width": 256, "height": 256}, {"name": "wrist", "width": 256, "height": 256}],
            "stateSize": 5,
        },
        "action": {"size": 5, "representation": "relative_joint_absolute_gripper", "horizon": ACTION_HORIZON},
    }


@app.post("/v1/reset")
async def reset(request: ResetRequest, authorization: str | None = Header(default=None)):
    authorize(authorization)
    if request.schemaVersion != SCHEMA:
        raise HTTPException(409, "schema mismatch")
    if request.environmentSha256 != ENVIRONMENT_SHA256:
        raise HTTPException(409, "environment hash mismatch")
    app.state.policy.reset(options={"episode_id": request.episodeId, "seed": request.seed})
    return {"episodeId": request.episodeId, "accepted": True}


def decode_frame(item: VideoFrame) -> np.ndarray:
    if item.mimeType != "image/png":
        raise HTTPException(422, "only image/png observations are accepted")
    try:
        raw = base64.b64decode(item.dataBase64, validate=True)
        image = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"), dtype=np.uint8)
    except Exception as exc:
        raise HTTPException(422, "invalid RGB observation") from exc
    if image.shape != (256, 256, 3):
        raise HTTPException(422, "camera frame must be uint8[256,256,3]")
    return image[None, None, ...]


@app.post("/v1/actions")
async def actions(request: ActionRequest, authorization: str | None = Header(default=None)):
    authorize(authorization)
    if request.schemaVersion != SCHEMA or request.deadlineNs < time.time_ns():
        raise HTTPException(409, "schema mismatch or stale observation")
    if request.requestedHorizon > ACTION_HORIZON:
        raise HTTPException(422, "requested horizon exceeds checkpoint horizon")
    frames = {item.name: decode_frame(item) for item in request.video}
    if tuple(frames) != ("front", "wrist"):
        raise HTTPException(422, "camera order must be front,wrist")
    state = np.asarray(request.state.get("jointPositionAndGripper", []), dtype=np.float32)
    if state.shape != (5,) or not np.isfinite(state).all():
        raise HTTPException(422, "state must be finite float32[5]")
    observation = {
        "video": {VIDEO_KEYS[0]: frames["front"], VIDEO_KEYS[1]: frames["wrist"]},
        "state": {ARM_STATE_KEY: state[:4][None, None, :], GRIPPER_STATE_KEY: state[4:][None, None, :]},
        "language": {"task": [[request.instruction]]},
    }
    output = app.state.policy.get_action(observation)
    action_dict = output[0] if isinstance(output, tuple) else output
    if ARM_ACTION_KEY not in action_dict or GRIPPER_ACTION_KEY not in action_dict:
        raise HTTPException(502, "checkpoint action streams do not match gateway configuration")
    arm = np.asarray(action_dict[ARM_ACTION_KEY], dtype=np.float32)[0]
    grip = np.asarray(action_dict[GRIPPER_ACTION_KEY], dtype=np.float32)[0]
    if arm.ndim != 2 or arm.shape[1] != 4 or grip.ndim != 2 or grip.shape[1] != 1 or arm.shape[0] != grip.shape[0]:
        raise HTTPException(502, "checkpoint returned incompatible action tensors")
    horizon = min(request.requestedHorizon, arm.shape[0])
    chunk = np.concatenate([arm[:horizon], grip[:horizon]], axis=1)
    if not np.isfinite(chunk).all():
        raise HTTPException(502, "checkpoint returned non-finite actions")
    return {
        "schemaVersion": SCHEMA,
        "episodeId": request.episodeId,
        "requestSequence": request.sequence,
        "actions": chunk.astype(float).tolist(),
        "modelRevision": MODEL_REVISION,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("GATEWAY_HOST", "127.0.0.1"), port=int(os.environ.get("GATEWAY_PORT", "8090")))
