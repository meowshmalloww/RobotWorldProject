"""Isolated, local-only LeRobot VLA-JEPA training preflight.

This worker deliberately performs no optimization.  It validates the exact
dataset/checkpoint/config contract that a later cancellable training process
will receive.  It never downloads or pushes artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


RESULT_PREFIX = "ROBOTWORLD_TRAINING_PREFLIGHT_RESULT="


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _shape(value: Any) -> list[int]:
    shape = getattr(value, "shape", None)
    if shape is None:
        return []
    return [int(item) for item in shape]


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _git_revision(root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    revision = completed.stdout.strip()
    return revision if completed.returncode == 0 and len(revision) == 40 else None


def _validate(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != "robotworld.vla-jepa-training-preflight.v1":
        raise ValueError("Unsupported training preflight schema.")

    artifact_root = Path(str(manifest["artifactRoot"])).resolve(strict=True)
    if not _inside(manifest_path.resolve(strict=True), artifact_root):
        raise ValueError("Training manifest is outside its declared artifact root.")
    dataset_root = Path(str(manifest["datasetRoot"])).resolve(strict=True)
    checkpoint_root = Path(str(manifest["checkpointRoot"])).resolve(strict=True)
    allowed_dataset_root = Path(str(manifest["allowedDatasetRoot"])).resolve(strict=True)
    allowed_model_root = Path(str(manifest["allowedModelRoot"])).resolve(strict=True)
    if not _inside(dataset_root, allowed_dataset_root):
        raise ValueError("Dataset path is outside the RobotWorld dataset store.")
    if not _inside(checkpoint_root, allowed_model_root):
        raise ValueError("Checkpoint path is outside its registered allowlisted root.")
    if not (checkpoint_root / "model.safetensors").is_file() or not (checkpoint_root / "config.json").is_file():
        raise ValueError("The base checkpoint is incomplete.")
    qwen_metadata = Path(str(manifest["qwenMetadataRoot"])).resolve(strict=True)
    for filename in ("config.json", "preprocessor_config.json", "tokenizer.json"):
        if not (qwen_metadata / filename).is_file():
            raise ValueError(f"Qwen metadata bootstrap is missing {filename}.")

    import torch
    from lerobot.configs import PreTrainedConfig
    from lerobot.configs.accelerator import AcceleratorConfig
    from lerobot.configs.default import DatasetConfig, WandBConfig
    from lerobot.configs.train import TrainPipelineConfig
    from lerobot.datasets.factory import make_dataset
    from lerobot.policies.vla_jepa.configuration_vla_jepa import VLAJEPAConfig  # noqa: F401

    policy = PreTrainedConfig.from_pretrained(checkpoint_root, local_files_only=True)
    if policy.type != "vla_jepa":
        raise ValueError(f"Expected VLA-JEPA checkpoint, got {policy.type!r}.")
    policy.pretrained_path = checkpoint_root
    policy.push_to_hub = False
    policy.repo_id = None
    policy.device = "cuda"
    policy.freeze_qwen = bool(manifest["freezeQwen"])
    policy.enable_world_model = bool(manifest["enableWorldModel"])
    policy.resize_images_to = (224, 224)
    policy.pre_snap_gripper_action = False
    policy.binarize_gripper_action = False

    candidate_output = Path(str(manifest["candidateOutput"])).resolve()
    if candidate_output.exists():
        raise FileExistsError("Candidate output already exists; preflight never overwrites a run.")
    if not _inside(candidate_output, artifact_root):
        raise ValueError("Candidate output escapes its immutable training-run root.")
    config = TrainPipelineConfig(
        dataset=DatasetConfig(
            repo_id=str(manifest["datasetRepoId"]),
            root=str(dataset_root),
            use_imagenet_stats=True,
            streaming=False,
            eval_split=0.0,
        ),
        policy=policy,
        output_dir=candidate_output,
        job_name=f"robotworld-{manifest['runId']}",
        seed=int(manifest["seed"]),
        cudnn_deterministic=True,
        num_workers=0,
        batch_size=int(manifest["batchSize"]),
        persistent_workers=False,
        steps=int(manifest["steps"]),
        env_eval_freq=0,
        eval_steps=0,
        save_checkpoint=True,
        save_freq=int(manifest["steps"]),
        accelerator=AcceleratorConfig(mixed_precision="bf16"),
        wandb=WandBConfig(enable=False, mode="disabled"),
        save_checkpoint_to_hub=False,
    )
    config.validate()
    dataset = make_dataset(config)
    if dataset.num_episodes < 1 or dataset.num_frames < 8:
        raise ValueError("The dataset is too small for the VLA-JEPA action horizon.")
    sample = dataset[0]
    required = {
        "observation.images.exterior_1_left",
        "observation.images.exterior_2_left",
        "observation.state",
        "action",
        "task",
    }
    missing = sorted(required.difference(sample))
    if missing:
        raise ValueError(f"LeRobot sample is missing required features: {missing}")
    shapes = {key: _shape(sample[key]) for key in sorted(required - {"task"})}
    if shapes["observation.state"][-1:] != [8]:
        raise ValueError(f"Expected 8-D state, got {shapes['observation.state']}.")
    if shapes["action"][-1:] != [7]:
        raise ValueError(f"Expected 7-D action, got {shapes['action']}.")
    for camera in ("observation.images.exterior_1_left", "observation.images.exterior_2_left"):
        if shapes[camera][-3:] != [3, 224, 224]:
            raise ValueError(f"Expected {camera} to end in [3,224,224], got {shapes[camera]}.")
    finite = all(
        bool(torch.isfinite(sample[key]).all())
        for key in ("observation.state", "action")
    )
    if not finite:
        raise ValueError("Training sample contains non-finite state/action values.")

    lerobot_root = Path(str(manifest["lerobotRepoRoot"])).resolve(strict=True)
    training_command = [
        sys.executable,
        "-m",
        "lerobot.scripts.lerobot_train",
        f"--policy.path={checkpoint_root}",
        "--policy.push_to_hub=false",
        f"--policy.freeze_qwen={str(policy.freeze_qwen).lower()}",
        f"--policy.enable_world_model={str(policy.enable_world_model).lower()}",
        "--policy.pre_snap_gripper_action=false",
        "--policy.binarize_gripper_action=false",
        f"--dataset.repo_id={manifest['datasetRepoId']}",
        f"--dataset.root={dataset_root}",
        f"--output_dir={candidate_output}",
        f"--steps={manifest['steps']}",
        f"--batch_size={manifest['batchSize']}",
        "--num_workers=0",
        "--persistent_workers=false",
        "--env_eval_freq=0",
        f"--save_freq={manifest['steps']}",
        "--accelerator.mixed_precision=bf16",
        "--wandb.enable=false",
    ]
    return {
        "schemaVersion": "robotworld.vla-jepa-training-preflight-result.v1",
        "validated": True,
        "runId": manifest["runId"],
        "dataset": {
            "repoId": manifest["datasetRepoId"],
            "root": str(dataset_root),
            "episodes": int(dataset.num_episodes),
            "frames": int(dataset.num_frames),
            "sampleShapes": shapes,
            "sampleFinite": finite,
        },
        "baseCheckpoint": {
            "root": str(checkpoint_root),
            "configSha256": _sha256(checkpoint_root / "config.json"),
            "weightsBytes": (checkpoint_root / "model.safetensors").stat().st_size,
            "type": policy.type,
        },
        "runtime": {
            "python": sys.executable,
            "pythonVersion": sys.version.split()[0],
            "torchVersion": torch.__version__,
            "cudaAvailable": bool(torch.cuda.is_available()),
            "cudaDevice": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "bfloat16Supported": bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported()),
            "lerobotVersion": importlib.metadata.version("lerobot"),
            "accelerateVersion": importlib.metadata.version("accelerate"),
            "wandbVersion": importlib.metadata.version("wandb"),
            "offline": os.environ.get("HF_HUB_OFFLINE") == "1" and os.environ.get("TRANSFORMERS_OFFLINE") == "1",
            "lerobotRepoRevision": _git_revision(lerobot_root),
        },
        "configuration": _json_safe(config.to_dict()),
        "qwenBootstrap": {
            "metadataRoot": str(qwen_metadata),
            "weightsSource": str(checkpoint_root / "model.safetensors"),
            "scopedWorkerPatchRequired": True,
        },
        "candidateOutput": str(candidate_output),
        "trainingCommand": training_command,
        "trainingExecuted": False,
        "pushedToHub": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    result = _validate(Path(args.manifest).resolve(strict=True))
    print(RESULT_PREFIX + json.dumps(result, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
