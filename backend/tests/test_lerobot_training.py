from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.contracts import VlaJepaFineTuneExecuteRequest, VlaJepaFineTuneValidationRequest
from app.services import lerobot_training


def _dataset(tmp_path: Path, *, readback: bool = True) -> Path:
    dataset_root = tmp_path / "dataset_test"
    lerobot_root = dataset_root / "lerobot"
    info = lerobot_root / "meta" / "info.json"
    data = lerobot_root / "data" / "chunk-000" / "file-000.parquet"
    info.parent.mkdir(parents=True, exist_ok=True)
    data.parent.mkdir(parents=True, exist_ok=True)
    info.write_text("{}", encoding="utf-8")
    data.write_bytes(b"parquet-test")
    manifest = {
        "datasetId": "dataset_test",
        "lifecycleState": "VALIDATED",
        "readbackValidated": readback,
        "root": str(lerobot_root),
        "repoId": "robotworld/dataset_test",
        "infoSha256": lerobot_training._sha256(info),
        "dataFiles": [
            {
                "path": "data/chunk-000/file-000.parquet",
                "sha256": lerobot_training._sha256(data),
            }
        ],
    }
    (dataset_root / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return dataset_root


def test_training_preflight_contract_is_bounded() -> None:
    request = VlaJepaFineTuneValidationRequest(datasetId="dataset_test", baseModelId="model_test")
    assert request.steps == 1000
    assert request.batch_size == 1
    assert request.freeze_qwen is True
    assert request.enable_world_model is False
    with pytest.raises(ValueError):
        VlaJepaFineTuneValidationRequest(datasetId="dataset_test", baseModelId="model_test", steps=0)


def test_training_execution_requires_candidate_only_acknowledgement() -> None:
    request = VlaJepaFineTuneExecuteRequest(
        runId="trainrun_test",
        acknowledgeCandidateOnly=True,
    )
    assert request.run_id == "trainrun_test"
    with pytest.raises(ValueError):
        VlaJepaFineTuneExecuteRequest(runId="trainrun_test", acknowledgeCandidateOnly=False)


def test_training_preflight_accepts_only_hash_verified_readback_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _dataset(tmp_path)
    monkeypatch.setattr(lerobot_training, "DATASETS_DIR", tmp_path)
    record = lerobot_training._dataset_record("dataset_test")
    assert record["readbackValidated"] is True

    _dataset(tmp_path, readback=False)
    with pytest.raises(lerobot_training.TrainingPreflightError, match="readback-validated"):
        lerobot_training._dataset_record("dataset_test")
