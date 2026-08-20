"""Read-only local VLA-JEPA checkpoint inspection and compatibility gating.

Inspection parses the safetensors header only; it never loads the 6 GB model
or reserves CPU/GPU memory. RobotWorld must reject an incompatible embodiment
before a policy process is allowed to execute actions.
"""
from __future__ import annotations

import importlib.util
import json
import os
import struct
from functools import lru_cache
from pathlib import Path
from typing import Any


MODEL_DIR = Path(os.environ.get("ROBOTWORLD_VLA_JEPA_DIR", r"D:\VLA-JEPA-Pretrain")).resolve()
ROBOTWORLD_CAMERAS = ["front", "wrist"]
ROBOTWORLD_STATE_SIZE = 5
ROBOTWORLD_ACTION_SIZE = 5


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _safetensors_header(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        raw_size = stream.read(8)
        if len(raw_size) != 8:
            raise ValueError("model.safetensors has no valid header length")
        header_size = struct.unpack("<Q", raw_size)[0]
        if header_size <= 2 or header_size > 256 * 1024 * 1024:
            raise ValueError("model.safetensors header length is invalid")
        header = json.loads(stream.read(header_size))
    if not isinstance(header, dict):
        raise ValueError("model.safetensors header is not an object")
    tensor_count = sum(1 for key in header if key != "__metadata__")
    return int(header_size), tensor_count


@lru_cache(maxsize=2)
def _inspect_cached(model_mtime_ns: int, model_size: int) -> dict[str, Any]:
    del model_mtime_ns
    required = [
        "config.json",
        "model.safetensors",
        "policy_preprocessor.json",
        "policy_preprocessor_step_3_normalizer_processor.safetensors",
        "policy_postprocessor.json",
        "policy_postprocessor_step_2_unnormalizer_processor.safetensors",
    ]
    missing = [name for name in required if not (MODEL_DIR / name).is_file()]
    if missing:
        return {"available": False, "path": str(MODEL_DIR), "missing": missing, "resident": False}

    config = _json(MODEL_DIR / "config.json")
    pre = _json(MODEL_DIR / "policy_preprocessor.json")
    post = _json(MODEL_DIR / "policy_postprocessor.json")
    header_size, tensor_count = _safetensors_header(MODEL_DIR / "model.safetensors")
    camera_names = [key for key in (config.get("input_features") or {}) if key.startswith("observation.images.")]
    action_size = int(config.get("action_dim") or ((config.get("output_features") or {}).get("action") or {}).get("shape", [0])[0])
    state_size = int(config.get("state_dim") or 0)
    reasons: list[str] = []
    if camera_names != ["observation.images.exterior_1_left", "observation.images.exterior_2_left"]:
        reasons.append("checkpoint camera contract is not the expected two-view DROID contract")
    if camera_names != [f"observation.images.{name}" for name in ROBOTWORLD_CAMERAS]:
        reasons.append("checkpoint cameras exterior_1_left/exterior_2_left do not match RobotWorld front/wrist")
    if state_size != ROBOTWORLD_STATE_SIZE:
        reasons.append(f"checkpoint state dimension {state_size} does not match RobotWorld {ROBOTWORLD_STATE_SIZE}")
    if action_size != ROBOTWORLD_ACTION_SIZE:
        reasons.append(f"checkpoint action dimension {action_size} does not match RobotWorld {ROBOTWORLD_ACTION_SIZE}")

    return {
        "available": True,
        "modelId": "lerobot/VLA-JEPA-Pretrain",
        "path": str(MODEL_DIR),
        "modelBytes": model_size,
        "safetensorsHeaderBytes": header_size,
        "tensorCount": tensor_count,
        "checkpoint": {
            "type": config.get("type"),
            "dtype": config.get("torch_dtype"),
            "backbone": config.get("qwen_model_name"),
            "worldModel": config.get("jepa_encoder_name"),
            "worldModelEnabledInCheckpoint": bool(config.get("enable_world_model")),
            "cameras": camera_names,
            "imageSize": config.get("resize_images_to"),
            "stateSize": state_size,
            "actionSize": action_size,
            "actionHorizon": config.get("n_action_steps"),
            "inferenceTimesteps": config.get("num_inference_timesteps"),
            "preprocessorSteps": [step.get("registry_name") for step in pre.get("steps", [])],
            "postprocessorSteps": [step.get("registry_name") for step in post.get("steps", [])],
        },
        "robotWorldContract": {
            "schemaVersion": "robotworld.policy.v1",
            "embodiment": "robotworld-4dof-v1",
            "cameras": ROBOTWORLD_CAMERAS,
            "stateSize": ROBOTWORLD_STATE_SIZE,
            "actionSize": ROBOTWORLD_ACTION_SIZE,
            "compatible": not reasons,
            "blockers": reasons,
        },
        "runtime": {
            "resident": False,
            "idleUnloadSeconds": 300,
            "lerobotInstalledInBackend": importlib.util.find_spec("lerobot") is not None,
            "loadAllowed": not reasons,
            "requiredAdaptation": [] if not reasons else [
                "map or fine-tune the two camera feature keys",
                "reinitialize and fine-tune the state encoder for the robot state contract",
                "reinitialize and fine-tune the action encoder/decoder for the robot action contract",
                "record matching normalization statistics before evaluation",
            ],
        },
    }


def inspect_checkpoint() -> dict[str, Any]:
    model = MODEL_DIR / "model.safetensors"
    if not model.is_file():
        return {"available": False, "path": str(MODEL_DIR), "missing": ["model.safetensors"], "resident": False}
    stat = model.stat()
    return _inspect_cached(stat.st_mtime_ns, stat.st_size)
