from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.services import lerobot_dataset


def _observation_records(root: Path) -> dict:
    records = {}
    for camera, color in (("front", (20, 40, 80)), ("wrist", (80, 40, 20))):
        path = root / "demonstration_frames" / f"frame-000000-{camera}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (224, 224), color=color).save(path)
        records[camera] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": lerobot_dataset._sha256(path),
        }
    return records


def test_oracle_frame_converts_to_local_cartesian_lerobot_contract(tmp_path: Path) -> None:
    current = {
        "timeSeconds": 0.0,
        "phase": "approach",
        "endEffectorPositionM": [0.5, 0.0, 0.5],
        "endEffectorQuaternionWxyz": [1.0, 0.0, 0.0, 0.0],
        "gripperWidthM": 0.08,
        "observationFrames": _observation_records(tmp_path),
    }
    following = {
        "timeSeconds": 0.1,
        "endEffectorPositionM": [0.51, -0.02, 0.53],
        "endEffectorQuaternionWxyz": [1.0, 0.0, 0.0, 0.0],
        "gripperWidthM": 0.0,
    }
    frame = lerobot_dataset._frame_contract(
        current,
        following,
        artifact_root=tmp_path.resolve(),
        instruction="Pick up the cube.",
    )
    np.testing.assert_allclose(frame["state"], [0.5, 0.0, 0.5, 1.0, 0.0, 0.0, 0.0, 0.08])
    np.testing.assert_allclose(frame["action"], [0.01, -0.02, 0.03, 0.0, 0.0, 0.0, -1.0], atol=1e-8)
    assert set(frame["images"]) == {"exterior_1_left", "exterior_2_left"}


def test_resample_requires_synchronized_oracle_observations() -> None:
    with pytest.raises(lerobot_dataset.DatasetExportError, match="recordObservations=true"):
        lerobot_dataset._resample([{"timeSeconds": 0.0}, {"timeSeconds": 0.1}], 10)


def test_resample_selects_requested_control_rate() -> None:
    trajectory = [
        {"timeSeconds": index * 0.02, "observationFrames": {"front": {}, "wrist": {}}}
        for index in range(16)
    ]
    selected = lerobot_dataset._resample(trajectory, 10)
    np.testing.assert_allclose([item["timeSeconds"] for item in selected], [0.0, 0.1, 0.2, 0.3])


@pytest.mark.asyncio
async def test_dataset_catalog_downgrades_pre_readback_exports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "dataset_legacy"
    root.mkdir()
    (root / "dataset_manifest.json").write_text(
        json.dumps({"datasetId": "dataset_legacy", "lifecycleState": "VALIDATED"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(lerobot_dataset, "DATASETS_DIR", tmp_path)
    rows = await lerobot_dataset.list_datasets()
    assert rows[0]["lifecycleState"] == "LEGACY_UNVERIFIED"
    assert rows[0]["validationErrors"]
