"""Bounded local VLA-JEPA optimizer worker for a READY RobotWorld preflight."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from typing import Any


RESULT_PREFIX = "ROBOTWORLD_TRAINING_RESULT="


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


@contextmanager
def _metadata_qwen_loader(metadata_path: Path):
    """Construct Qwen without duplicate base weights; VLA safetensors fill it next."""

    from transformers import AutoConfig, Qwen3VLForConditionalGeneration
    from transformers.initialization import no_init_weights

    root = metadata_path.resolve(strict=True)
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

    with patch.object(Qwen3VLForConditionalGeneration, "from_pretrained", side_effect=load_structure):
        yield


def _configuration(manifest: dict[str, Any]):
    from lerobot.configs import PreTrainedConfig
    from lerobot.configs.accelerator import AcceleratorConfig
    from lerobot.configs.default import DatasetConfig, WandBConfig
    from lerobot.configs.train import TrainPipelineConfig
    from lerobot.policies.vla_jepa.configuration_vla_jepa import VLAJEPAConfig  # noqa: F401

    checkpoint = Path(str(manifest["checkpointRoot"])).resolve(strict=True)
    dataset = Path(str(manifest["datasetRoot"])).resolve(strict=True)
    output = Path(str(manifest["candidateOutput"])).resolve()
    artifact_root = Path(str(manifest["artifactRoot"])).resolve(strict=True)
    if output.exists() or not _inside(output, artifact_root):
        raise ValueError("Candidate output must be a new path inside the READY training run.")
    policy = PreTrainedConfig.from_pretrained(checkpoint, local_files_only=True)
    if policy.type != "vla_jepa":
        raise ValueError("Only VLA-JEPA candidates are supported.")
    policy.pretrained_path = checkpoint
    policy.push_to_hub = False
    policy.repo_id = None
    policy.device = "cuda"
    policy.freeze_qwen = bool(manifest["freezeQwen"])
    policy.enable_world_model = bool(manifest["enableWorldModel"])
    policy.resize_images_to = (224, 224)
    policy.pre_snap_gripper_action = False
    policy.binarize_gripper_action = False
    policy.qwen_model_name = str(Path(str(manifest["qwenMetadataRoot"])).resolve(strict=True))
    return TrainPipelineConfig(
        dataset=DatasetConfig(
            repo_id=str(manifest["datasetRepoId"]),
            root=str(dataset),
            use_imagenet_stats=True,
            streaming=False,
            eval_split=0.0,
        ),
        policy=policy,
        output_dir=output,
        job_name=f"robotworld-{manifest['runId']}",
        seed=int(manifest["seed"]),
        cudnn_deterministic=True,
        num_workers=0,
        batch_size=int(manifest["batchSize"]),
        persistent_workers=False,
        steps=int(manifest["steps"]),
        env_eval_freq=0,
        log_freq=1,
        eval_steps=0,
        save_checkpoint=True,
        save_freq=int(manifest["steps"]),
        accelerator=AcceleratorConfig(mixed_precision="bf16"),
        wandb=WandBConfig(enable=False, mode="disabled"),
        save_checkpoint_to_hub=False,
    )


def _execute(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != "robotworld.vla-jepa-training-preflight.v1":
        raise ValueError("Unsupported training manifest schema.")
    artifact_root = Path(str(manifest["artifactRoot"])).resolve(strict=True)
    if not _inside(manifest_path.resolve(strict=True), artifact_root):
        raise ValueError("Training manifest escapes its run root.")
    if int(manifest["steps"]) < 1 or int(manifest["steps"]) > 10:
        raise ValueError("This bounded worker permits 1-10 steps per explicitly approved run.")
    if int(manifest["batchSize"]) != 1:
        raise ValueError("The verified single-GPU smoke worker currently requires batch size 1.")
    if not bool(manifest["freezeQwen"]) or bool(manifest["enableWorldModel"]):
        raise ValueError("The verified 12 GiB profile requires freezeQwen=true and enableWorldModel=false.")
    if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1":
        raise ValueError("Training worker must run with Hugging Face and Transformers offline mode enabled.")

    import torch
    import lerobot.scripts.lerobot_train as train_module

    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("CUDA with bfloat16 support is required.")
    config = _configuration(manifest)
    config.validate()
    started = time.perf_counter()
    qwen_metadata = Path(str(manifest["qwenMetadataRoot"])).resolve(strict=True)
    original_update_last = train_module.update_last_checkpoint

    def portable_update_last(checkpoint_dir: Path) -> None:
        try:
            original_update_last(checkpoint_dir)
        except OSError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) != 1314:
                raise
            pointer = checkpoint_dir.parent / "last_checkpoint.txt"
            pointer.write_text(checkpoint_dir.name, encoding="utf-8")

    with _metadata_qwen_loader(qwen_metadata), patch.object(
        train_module,
        "update_last_checkpoint",
        side_effect=portable_update_last,
    ):
        train_module.train.__wrapped__(config)
    duration = time.perf_counter() - started

    output = Path(str(manifest["candidateOutput"])).resolve(strict=True)
    checkpoints = sorted(
        path
        for path in (output / "checkpoints").glob("*/pretrained_model")
        if path.is_dir() and path.parent.name != "last"
    )
    if not checkpoints:
        raise RuntimeError("LeRobot completed without a candidate pretrained_model checkpoint.")
    candidate = checkpoints[-1]
    weights = candidate / "model.safetensors"
    config_path = candidate / "config.json"
    if not weights.is_file() or not config_path.is_file():
        raise RuntimeError("Candidate checkpoint is missing model.safetensors or config.json.")
    return {
        "schemaVersion": "robotworld.vla-jepa-training-result.v1",
        "runId": manifest["runId"],
        "trainingExecuted": True,
        "steps": int(manifest["steps"]),
        "batchSize": int(manifest["batchSize"]),
        "durationSeconds": duration,
        "candidateCheckpointPath": str(candidate),
        "candidateWeightsBytes": weights.stat().st_size,
        "candidateWeightsSha256": _sha256(weights),
        "candidateConfigSha256": _sha256(config_path),
        "baseCheckpointPath": str(Path(str(manifest["checkpointRoot"])).resolve(strict=True)),
        "datasetRepoId": manifest["datasetRepoId"],
        "device": torch.cuda.get_device_name(0),
        "peakAllocatedBytes": int(torch.cuda.max_memory_allocated()),
        "lastCheckpointPointer": str(output / "checkpoints" / "last_checkpoint.txt"),
        "pushedToHub": False,
        "activeCheckpointOverwritten": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve(strict=True)
    result = _execute(manifest_path)
    _write_json(manifest_path.parent / "training_result.json", result)
    print(RESULT_PREFIX + json.dumps(result, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
