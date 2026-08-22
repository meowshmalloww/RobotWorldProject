"""Isolated LeRobot dataset writer for validated RobotWorld demonstrations.

The control plane creates a bounded JSON manifest containing only generated
RobotWorld frame paths and numeric state/action values. This worker imports the
installed LeRobot version, writes one local dataset revision, finalizes it, and
prints a single machine-readable result line. It never pushes to the Hub.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


RESULT_PREFIX = "ROBOTWORLD_DATASET_RESULT="


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_image(path_value: str, artifact_root: Path) -> Path:
    path = Path(path_value).resolve()
    if artifact_root != path and artifact_root not in path.parents:
        raise ValueError(f"Observation image escapes the evaluation artifact root: {path}")
    if path.suffix.lower() != ".png" or not path.is_file():
        raise ValueError(f"Observation image is missing or not PNG: {path}")
    return path


def write_dataset(manifest_path: Path, output_root: Path) -> dict[str, Any]:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != "robotworld.lerobot-export-manifest.v1":
        raise ValueError("Unsupported RobotWorld dataset export manifest.")
    frames = manifest.get("frames")
    if not isinstance(frames, list) or len(frames) < 2:
        raise ValueError("A demonstration dataset requires at least two synchronized frames.")
    artifact_root = Path(str(manifest["evaluationArtifactRoot"])).resolve()
    if not artifact_root.is_dir():
        raise ValueError("Evaluation artifact root is missing.")
    fps = int(manifest["fps"])
    if fps < 1 or fps > 100:
        raise ValueError("Dataset fps must be between 1 and 100.")
    dataset_id = str(manifest["datasetId"])
    repo_id = f"robotworld/{dataset_id}"
    output_root = output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"Dataset output already contains files: {output_root}")
    if output_root.exists():
        output_root.rmdir()
    output_root.parent.mkdir(parents=True, exist_ok=True)

    image_shape = (3, 224, 224)
    features = {
        "observation.images.exterior_1_left": {
            "dtype": "image",
            "shape": image_shape,
            "names": ["channels", "height", "width"],
        },
        "observation.images.exterior_2_left": {
            "dtype": "image",
            "shape": image_shape,
            "names": ["channels", "height", "width"],
        },
        "observation.state": {"dtype": "float32", "shape": (8,), "names": None},
        "action": {"dtype": "float32", "shape": (7,), "names": None},
    }
    dataset = LeRobotDataset.create(
        repo_id=repo_id,
        fps=fps,
        features=features,
        root=output_root,
        robot_type="franka_panda",
        use_videos=False,
        image_writer_threads=2,
    )
    for index, frame in enumerate(frames):
        images = frame.get("images") or {}
        front_path = _safe_image(str(images.get("exterior_1_left") or ""), artifact_root)
        wrist_path = _safe_image(str(images.get("exterior_2_left") or ""), artifact_root)
        with Image.open(front_path) as source:
            front = np.asarray(source.convert("RGB"), dtype=np.uint8).transpose(2, 0, 1)
        with Image.open(wrist_path) as source:
            wrist = np.asarray(source.convert("RGB"), dtype=np.uint8).transpose(2, 0, 1)
        if front.shape != image_shape or wrist.shape != image_shape:
            raise ValueError(f"Frame {index} does not contain two 224x224 RGB observations.")
        state = np.asarray(frame.get("state"), dtype=np.float32)
        action = np.asarray(frame.get("action"), dtype=np.float32)
        if state.shape != (8,) or action.shape != (7,) or not np.isfinite(state).all() or not np.isfinite(action).all():
            raise ValueError(f"Frame {index} contains an invalid state/action contract.")
        dataset.add_frame(
            {
                "observation.images.exterior_1_left": front,
                "observation.images.exterior_2_left": wrist,
                "observation.state": state,
                "action": action,
                "task": str(frame.get("task") or "Pick up the object and place it in the target."),
            }
        )
    dataset.save_episode(parallel_encoding=False)
    dataset.finalize()

    info_path = output_root / "meta" / "info.json"
    data_files = sorted((output_root / "data").rglob("*.parquet"))
    if not info_path.is_file() or not data_files:
        raise RuntimeError("LeRobot finalized without the required metadata/parquet artifacts.")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    if int(info.get("total_episodes") or 0) != 1 or int(info.get("total_frames") or 0) != len(frames):
        raise RuntimeError("LeRobot metadata counts do not match the source manifest.")
    readback = LeRobotDataset(repo_id=repo_id, root=output_root)
    if readback.num_episodes != 1 or readback.num_frames != len(frames):
        raise RuntimeError("LeRobot readback counts do not match the source manifest.")
    for sample_index in (0, len(frames) - 1):
        sample = readback[sample_index]
        expected_shapes = {
            "observation.images.exterior_1_left": image_shape,
            "observation.images.exterior_2_left": image_shape,
            "observation.state": (8,),
            "action": (7,),
        }
        for key, shape in expected_shapes.items():
            if tuple(sample[key].shape) != shape:
                raise RuntimeError(f"LeRobot readback shape mismatch for {key}: {tuple(sample[key].shape)}")
    return {
        "schemaVersion": "robotworld.lerobot-dataset-result.v1",
        "datasetId": dataset_id,
        "repoId": repo_id,
        "root": str(output_root),
        "fps": fps,
        "totalEpisodes": 1,
        "totalFrames": len(frames),
        "infoSha256": _sha256(info_path),
        "dataFiles": [{"path": path.relative_to(output_root).as_posix(), "sha256": _sha256(path)} for path in data_files],
        "imageCount": len(frames) * 2,
        "imageStorage": "parquet_image",
        "readbackValidated": True,
        "features": sorted(features),
        "pushedToHub": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = write_dataset(Path(args.manifest).resolve(), Path(args.output).resolve())
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
