"""Isolated JSON-lines worker for an explicitly configured LeRobot VLA-JEPA.

The control plane launches this file with a configured Python executable. It
never downloads model code or weights by default. All responses are one-line
JSON objects so stdout remains a typed protocol; diagnostics go to stderr.
"""
from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import platform
import sys
import time
import traceback
from pathlib import Path
from typing import Any
from unittest.mock import patch


PROTOCOL_VERSION = "robotworld.vla-worker.v1"
REQUIRED_CHECKPOINT_FILES = (
    "config.json",
    "model.safetensors",
    "policy_preprocessor.json",
    "policy_preprocessor_step_3_normalizer_processor.safetensors",
    "policy_postprocessor.json",
    "policy_postprocessor_step_2_unnormalizer_processor.safetensors",
)

_policy = None
_preprocessor = None
_postprocessor = None
_loaded: dict[str, Any] | None = None


def _clip_normalized_action(raw_values: list[float]) -> tuple[list[float], bool, float]:
    """Apply the checkpoint-declared normalized action clamp with evidence."""

    if len(raw_values) != 7 or any(not math.isfinite(value) for value in raw_values):
        raise RuntimeError(f"Policy returned invalid normalized action shape/value: {raw_values}")
    bounded = [max(-1.0, min(1.0, value)) for value in raw_values]
    maximum_delta = max(abs(raw - value) for raw, value in zip(raw_values, bounded, strict=True))
    return bounded, bounded != raw_values, maximum_delta


def _emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":"), default=str) + "\n")
    sys.stdout.flush()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _package(name: str) -> dict[str, Any]:
    available = importlib.util.find_spec(name) is not None
    version = None
    if available:
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = "source-tree"
    return {"available": available, "version": version}


def _git_revision_without_process(repo: Path) -> str | None:
    git = repo / ".git"
    if git.is_file():
        line = git.read_text(encoding="utf8", errors="replace").strip()
        if line.startswith("gitdir:"):
            git = (repo / line.split(":", 1)[1].strip()).resolve()
    head = git / "HEAD"
    if not head.is_file():
        return None
    value = head.read_text(encoding="ascii", errors="replace").strip()
    if value.startswith("ref:"):
        ref = git / value.split(":", 1)[1].strip()
        return ref.read_text(encoding="ascii", errors="replace").strip() if ref.is_file() else None
    return value or None


def _configure_repo(repo_value: str | None) -> tuple[Path | None, Path | None]:
    if not repo_value:
        return None, None
    repo = Path(repo_value).resolve(strict=True)
    source_root = repo / "src" if (repo / "src" / "lerobot").is_dir() else repo
    expected = source_root / "lerobot" / "policies" / "vla_jepa" / "modeling_vla_jepa.py"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    return repo, expected


def _huggingface_cache_roots() -> list[Path]:
    values: list[Path] = []
    if raw := os.environ.get("HF_HUB_CACHE"):
        values.append(Path(raw).expanduser())
    if raw := os.environ.get("HF_HOME"):
        values.append(Path(raw).expanduser() / "hub")
    values.append(Path.home() / ".cache" / "huggingface" / "hub")
    roots: list[Path] = []
    for value in values:
        try:
            resolved = value.resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _local_transformers_dependency(identifier: Any) -> dict[str, Any]:
    value = str(identifier or "").strip()
    if not value:
        return {"identifier": None, "availableLocally": False, "resolvedPath": None, "reason": "not_configured"}
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError):
            resolved = None
        if resolved is not None and resolved.is_dir() and (resolved / "config.json").is_file():
            weight_names = (
                "model.safetensors",
                "model.safetensors.index.json",
                "pytorch_model.bin",
                "pytorch_model.bin.index.json",
            )
            has_weights = any((resolved / name).is_file() for name in weight_names)
            return {
                "identifier": value,
                "availableLocally": has_weights,
                "resolvedPath": str(resolved),
                "reason": "configured_local_directory" if has_weights else "configured_metadata_only_directory",
            }
        return {"identifier": value, "availableLocally": False, "resolvedPath": None, "reason": "local_path_missing_config"}
    if "/" not in value or value.startswith((".", "..")):
        return {"identifier": value, "availableLocally": False, "resolvedPath": None, "reason": "invalid_repository_id"}
    cache_name = "models--" + value.replace("/", "--")
    for root in _huggingface_cache_roots():
        snapshots = root / cache_name / "snapshots"
        if not snapshots.is_dir():
            continue
        for snapshot in snapshots.iterdir():
            if snapshot.is_dir() and (snapshot / "config.json").is_file():
                return {
                    "identifier": value,
                    "availableLocally": True,
                    "resolvedPath": str(snapshot.resolve()),
                    "reason": "huggingface_cache_snapshot",
                }
    return {"identifier": value, "availableLocally": False, "resolvedPath": None, "reason": "not_in_local_huggingface_cache"}


def _qwen_metadata_dependency(value: Any) -> dict[str, Any]:
    raw = str(value or "").strip()
    if not raw:
        return {"availableLocally": False, "resolvedPath": None, "reason": "not_configured"}
    try:
        root = Path(raw).expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        return {"availableLocally": False, "resolvedPath": None, "reason": "path_missing"}
    required = {
        "config.json",
        "chat_template.json",
        "preprocessor_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }
    missing = sorted(name for name in required if not (root / name).is_file()) if root.is_dir() else sorted(required)
    if missing:
        return {
            "availableLocally": False,
            "resolvedPath": str(root),
            "reason": "metadata_incomplete",
            "missing": missing,
        }
    return {
        "availableLocally": True,
        "resolvedPath": str(root),
        "reason": "metadata_only_checkpoint_bootstrap",
        "weightsSource": "vla_checkpoint_model.safetensors",
    }


def _probe(request: dict[str, Any]) -> dict[str, Any]:
    checkpoint = Path(str(request.get("checkpointPath") or "")).resolve(strict=True)
    if not checkpoint.is_dir():
        raise ValueError("checkpointPath must be a directory")
    repo, expected_source = _configure_repo(request.get("lerobotRepoPath"))
    missing = [name for name in REQUIRED_CHECKPOINT_FILES if not (checkpoint / name).is_file()]
    config: dict[str, Any] = {}
    try:
        config = json.loads((checkpoint / "config.json").read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError) as exc:
        missing.append(f"valid config.json ({exc})")

    packages = {name: _package(name) for name in ("torch", "lerobot", "transformers", "safetensors", "PIL")}
    blockers = [f"Missing checkpoint artifact: {name}" for name in missing]
    if repo is None:
        blockers.append("LEROBOT_REPO_PATH is not configured; a checkpoint directory is not LeRobot source code.")
    elif expected_source is None or not expected_source.is_file():
        blockers.append("Configured LEROBOT_REPO_PATH does not contain src/lerobot/policies/vla_jepa/modeling_vla_jepa.py.")
    for name in ("torch", "lerobot", "transformers", "safetensors", "PIL"):
        if not packages[name]["available"]:
            blockers.append(f"Worker environment is missing required package: {name}.")
    if config.get("type") != "vla_jepa":
        blockers.append(f"Checkpoint config type is {config.get('type')!r}, expected 'vla_jepa'.")

    cuda: dict[str, Any] = {"available": False}
    try:
        import torch

        cuda = {
            "available": bool(torch.cuda.is_available()),
            "deviceCount": int(torch.cuda.device_count()),
            "torchVersion": str(torch.__version__),
            "cudaVersion": str(torch.version.cuda) if torch.version.cuda else None,
        }
        if torch.cuda.is_available():
            index = int(request.get("cudaDevice", 0))
            props = torch.cuda.get_device_properties(index)
            cuda.update(
                deviceIndex=index,
                deviceName=props.name,
                totalMemoryBytes=int(props.total_memory),
                bfloat16Supported=bool(torch.cuda.is_bf16_supported()),
            )
    except Exception as exc:
        blockers.append(f"PyTorch/CUDA probe failed: {exc}")
    requested_device = str(request.get("device") or "cuda")
    if requested_device.startswith("cuda") and not cuda.get("available"):
        blockers.append("Configured VLA-JEPA device requires CUDA, but CUDA is unavailable in the worker environment.")

    downloads_allowed = os.environ.get("ROBOTWORLD_WORKER_ALLOW_MODEL_DOWNLOADS") == "1"
    qwen_dependency = _local_transformers_dependency(config.get("qwen_model_name"))
    qwen_metadata = _qwen_metadata_dependency(request.get("qwenMetadataPath"))
    if not qwen_dependency["availableLocally"] and qwen_metadata["availableLocally"]:
        qwen_dependency = {
            "identifier": config.get("qwen_model_name"),
            **qwen_metadata,
        }
    load_world_model = bool(request.get("loadWorldModelForInference", False))
    jepa_dependency = _local_transformers_dependency(config.get("jepa_encoder_name"))
    if not qwen_dependency["availableLocally"] and not downloads_allowed:
        blockers.append(
            f"Offline worker cannot resolve qwen_model_name {qwen_dependency['identifier']!r} from a local path or Hugging Face cache."
        )
    if load_world_model and not jepa_dependency["availableLocally"] and not downloads_allowed:
        blockers.append(
            f"Offline worker cannot resolve jepa_encoder_name {jepa_dependency['identifier']!r} from a local path or Hugging Face cache."
        )

    input_features = config.get("input_features") if isinstance(config.get("input_features"), dict) else {}
    output_features = config.get("output_features") if isinstance(config.get("output_features"), dict) else {}
    camera_keys = [key for key in input_features if str(key).startswith("observation.images.")]
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "readyForLoad": not blockers,
        "blockers": blockers,
        "python": {"executable": sys.executable, "version": platform.python_version(), "platform": platform.platform()},
        "packages": packages,
        "cuda": cuda,
        "offlineMode": os.environ.get("HF_HUB_OFFLINE") == "1" and os.environ.get("TRANSFORMERS_OFFLINE") == "1",
        "networkDownloadsAllowed": downloads_allowed,
        "inferenceDependencies": {
            "qwen": {**qwen_dependency, "required": True},
            "vJepa2": {**jepa_dependency, "required": load_world_model},
        },
        "checkpoint": {
            "path": str(checkpoint),
            "type": config.get("type"),
            "weightBytes": (checkpoint / "model.safetensors").stat().st_size if (checkpoint / "model.safetensors").is_file() else 0,
            "configSha256": _sha256(checkpoint / "config.json") if (checkpoint / "config.json").is_file() else None,
            "cameraKeys": camera_keys,
            "stateFeaturePresent": "observation.state" in input_features,
            "declaredStateDimension": config.get("state_dim"),
            "actionDimension": config.get("action_dim") or ((output_features.get("action") or {}).get("shape") or [None])[0],
            "actionHorizon": config.get("n_action_steps"),
            "preSnapGripper": config.get("pre_snap_gripper_action"),
            "binarizeGripper": config.get("binarize_gripper_action"),
            "worldModelConfigured": config.get("enable_world_model"),
        },
        "lerobot": {
            "repoPath": str(repo) if repo else None,
            "repoRevision": _git_revision_without_process(repo) if repo else None,
            "vlaJepaSource": str(expected_source) if expected_source else None,
            "vlaJepaSourceSha256": _sha256(expected_source) if expected_source and expected_source.is_file() else None,
        },
    }


def _metadata_qwen_loader(metadata_path: str):
    """Return a context that builds Qwen structure without duplicate base weights.

    The VLA checkpoint contains all trained Qwen tensors. Transformers still
    needs the small official config/tokenizer/processor files to construct the
    architecture and prompts. This scoped patch replaces only the initial
    Qwen ``from_pretrained`` call and is removed before checkpoint loading.
    """

    from transformers import AutoConfig, Qwen3VLForConditionalGeneration
    from transformers.initialization import no_init_weights

    root = Path(metadata_path).resolve(strict=True)
    original = Qwen3VLForConditionalGeneration.from_pretrained

    def load_structure(identifier, *args, **kwargs):
        try:
            candidate = Path(str(identifier)).resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError):
            candidate = None
        if candidate != root:
            return original(identifier, *args, **kwargs)
        model_config = AutoConfig.from_pretrained(root, local_files_only=True)
        dtype = kwargs.get("dtype", kwargs.get("torch_dtype"))
        if dtype is not None:
            model_config.dtype = dtype
        with no_init_weights():
            return Qwen3VLForConditionalGeneration._from_config(model_config, dtype=dtype)

    return patch.object(Qwen3VLForConditionalGeneration, "from_pretrained", side_effect=load_structure)


def _load(request: dict[str, Any]) -> dict[str, Any]:
    global _policy, _preprocessor, _postprocessor, _loaded
    probe = _probe(request)
    if not probe["readyForLoad"]:
        raise RuntimeError("; ".join(probe["blockers"]))
    checkpoint = str(Path(str(request["checkpointPath"])).resolve(strict=True))
    device = str(request.get("device") or "cuda")
    from lerobot.policies.factory import make_pre_post_processors
    from lerobot.policies.vla_jepa.configuration_vla_jepa import VLAJEPAConfig
    from lerobot.policies.vla_jepa.modeling_vla_jepa import VLAJEPAPolicy

    config = VLAJEPAConfig.from_pretrained(checkpoint)
    config.device = device
    # Official LeRobot VLA-JEPA inference uses Qwen + the action head only.
    # Avoid loading the training-only V-JEPA2 encoder/predictor unless an
    # explicit future evaluation mode requests it.
    config.enable_world_model = bool(request.get("loadWorldModelForInference", False))
    qwen_dependency = probe["inferenceDependencies"]["qwen"]
    qwen_metadata_only = qwen_dependency.get("reason") == "metadata_only_checkpoint_bootstrap"
    if qwen_metadata_only:
        config.qwen_model_name = str(qwen_dependency["resolvedPath"])
    started = time.perf_counter()
    if qwen_metadata_only:
        with _metadata_qwen_loader(config.qwen_model_name):
            policy = VLAJEPAPolicy.from_pretrained(checkpoint, config=config)
    else:
        policy = VLAJEPAPolicy.from_pretrained(checkpoint, config=config)
    preprocessor, postprocessor = make_pre_post_processors(config, pretrained_path=checkpoint)
    policy.eval()
    parameter_count = sum(parameter.numel() for parameter in policy.parameters())
    resident_device = str(next(policy.parameters()).device)
    _policy = policy
    _preprocessor = preprocessor
    _postprocessor = postprocessor
    _loaded = {
        "checkpointPath": checkpoint,
        "checkpointConfigSha256": probe["checkpoint"]["configSha256"],
        "repoRevision": probe["lerobot"]["repoRevision"],
        "sourceSha256": probe["lerobot"]["vlaJepaSourceSha256"],
        "device": resident_device,
        "parameterCount": int(parameter_count),
        "loadDurationSeconds": time.perf_counter() - started,
        "loadedAtUnixSeconds": time.time(),
        "worldModelConfigured": probe["checkpoint"]["worldModelConfigured"],
        "worldModelLoaded": bool(config.enable_world_model),
        "worldModelInferenceRequired": False,
        "qwenBootstrap": qwen_dependency,
        "cameraKeys": list(probe["checkpoint"]["cameraKeys"]),
        "stateFeaturePresent": bool(probe["checkpoint"]["stateFeaturePresent"]),
        "actionDimension": probe["checkpoint"]["actionDimension"],
    }
    return {"loaded": True, "resident": dict(_loaded), "probe": probe}


def _allowed_file(value: str, roots: list[str]) -> Path:
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Observation artifact is not a file: {path}")
    resolved_roots = [Path(root).resolve(strict=True) for root in roots]
    if not any(path == root or root in path.parents for root in resolved_roots):
        raise ValueError("Observation artifact is outside the configured worker artifact roots.")
    return path


def _infer(request: dict[str, Any]) -> dict[str, Any]:
    if _policy is None or _preprocessor is None or _postprocessor is None or _loaded is None:
        raise RuntimeError("VLA-JEPA policy is not loaded.")
    if not request.get("adapterRevision") or not request.get("normalizationRevision"):
        raise ValueError("Inference requires explicit adapterRevision and normalizationRevision.")
    images = request.get("images")
    if not isinstance(images, dict) or not images:
        raise ValueError("images must map checkpoint observation keys to artifact paths.")
    expected_image_keys = list(_loaded.get("cameraKeys") or [])
    if set(images) != set(expected_image_keys):
        raise ValueError(f"images keys must exactly match the loaded checkpoint camera keys: {expected_image_keys}.")
    roots = [str(value) for value in request.get("allowedArtifactRoots") or []]
    if not roots:
        raise ValueError("allowedArtifactRoots is required.")

    import numpy as np
    import torch
    from PIL import Image

    batch: dict[str, Any] = {}
    for key, value in images.items():
        path = _allowed_file(str(value), roots)
        with Image.open(path) as source:
            rgb = np.asarray(source.convert("RGB"), dtype=np.float32) / 255.0
        batch[str(key)] = torch.from_numpy(rgb).permute(2, 0, 1)
    state = request.get("state")
    state_required = bool(_loaded.get("stateFeaturePresent"))
    if state_required and state is None:
        raise ValueError("The loaded checkpoint requires observation.state.")
    if not state_required and state is not None:
        raise ValueError("The loaded checkpoint does not declare observation.state; refusing an ignored state vector.")
    if state is not None:
        if not isinstance(state, list) or any(not isinstance(item, (int, float)) or not math.isfinite(item) for item in state):
            raise ValueError("state must be a finite numeric array.")
        batch["observation.state"] = torch.tensor(state, dtype=torch.float32)
    instruction = str(request.get("instruction") or "").strip()
    if not instruction:
        raise ValueError("instruction is required.")
    batch["task"] = instruction
    started = time.perf_counter()
    processed = _preprocessor(batch)
    with torch.inference_mode():
        normalized = _policy.select_action(processed)
        checkpoint_action = _postprocessor(normalized)
    normalized = normalized.detach().to(device="cpu", dtype=torch.float32).reshape(-1)
    checkpoint_action = checkpoint_action.detach().to(device="cpu", dtype=torch.float32).reshape(-1)
    raw_values = [float(value) for value in normalized.tolist()]
    physical_values = [float(value) for value in checkpoint_action.tolist()]
    if len(physical_values) != 7 or any(not math.isfinite(value) for value in physical_values):
        raise RuntimeError(f"Checkpoint postprocessor returned an invalid action: {physical_values}")
    # The pinned VLA-JEPA config declares clip_normalized_actions=true and its
    # official LeRobot postprocessor applies Tensor.clamp(-1, 1) before
    # unnormalization. Mirror that contract for the Cartesian adapter while
    # surfacing the clamp as evidence instead of rejecting normal diffusion
    # overshoot (for example a gripper value of 1.0016).
    values, normalized_action_clipped, maximum_clip_delta = _clip_normalized_action(raw_values)
    if physical_values[-1] not in {-1.0, 1.0}:
        raise RuntimeError(
            "Checkpoint gripper postprocessor did not produce its declared LIBERO-style "
            f"binary -1/+1 value: {physical_values[-1]}"
        )
    return {
        "normalizedAction": values,
        "normalizedActionClipped": normalized_action_clipped,
        "normalizedActionMaximumClipDelta": maximum_clip_delta,
        "checkpointAction": physical_values,
        "actionDimension": len(values),
        "outputStage": "checkpoint_postprocessed_droid_relative_action",
        "actionConvention": "[dx,dy,dz,droll,dpitch,dyaw,gripper_-1_closed_+1_open]",
        "checkpointPostprocessorApplied": True,
        "inferenceDurationSeconds": time.perf_counter() - started,
        "adapterRevision": request["adapterRevision"],
        "normalizationRevision": request["normalizationRevision"],
        "checkpointConfigSha256": _loaded["checkpointConfigSha256"],
    }


def _unload() -> dict[str, Any]:
    global _policy, _preprocessor, _postprocessor, _loaded
    was_loaded = _policy is not None
    _policy = None
    _preprocessor = None
    _postprocessor = None
    _loaded = None
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    return {"unloaded": was_loaded}


def _handle(request: dict[str, Any]) -> dict[str, Any]:
    operation = request.get("operation")
    if operation == "probe":
        return _probe(request)
    if operation == "load":
        return _load(request)
    if operation == "status":
        return {"protocolVersion": PROTOCOL_VERSION, "loaded": _loaded is not None, "resident": _loaded}
    if operation == "infer":
        return _infer(request)
    if operation == "unload":
        return _unload()
    if operation == "shutdown":
        return {"shutdown": True}
    raise ValueError(f"Unsupported worker operation: {operation!r}")


def main() -> int:
    for line in sys.stdin:
        request_id = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Worker request must be a JSON object.")
            request_id = request.get("id")
            result = _handle(request)
            _emit({"id": request_id, "ok": True, "result": result})
            if request.get("operation") == "shutdown":
                return 0
        except Exception as exc:
            _emit(
                {
                    "id": request_id,
                    "ok": False,
                    "error": str(exc),
                    "errorType": type(exc).__name__,
                    "traceback": traceback.format_exc(limit=40),
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
