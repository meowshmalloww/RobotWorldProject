"""Remove live-view geometry duplication from durable evaluation trajectories.

The immutable ``evaluation.json`` artifact is verified before the database
copy is compacted.  No evaluation, trajectory sample, contact, predicate, or
recorded frame is deleted; only ``renderGeometries`` (which is reconstructed
from the compiled MuJoCo model for the live viewer) is removed per sample.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "robotworld.db"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Commit verified compactions.")
    args = parser.parse_args()

    connection = sqlite3.connect(DB_PATH)
    rows = connection.execute(
        "SELECT id, result, artifact_dir FROM evaluation_run_records ORDER BY created_at"
    ).fetchall()
    changed_rows = 0
    removed_entries = 0
    bytes_before = 0
    bytes_after = 0
    skipped: list[str] = []

    for run_id, raw_result, artifact_dir_value in rows:
        if not raw_result:
            continue
        result = json.loads(raw_result)
        trajectory = result.get("trajectory")
        if not isinstance(trajectory, list):
            continue
        duplicate_count = sum(
            1 for sample in trajectory if isinstance(sample, dict) and "renderGeometries" in sample
        )
        if duplicate_count == 0:
            continue

        artifact_path = Path(str(artifact_dir_value or "")) / "evaluation.json"
        if not artifact_path.is_file():
            skipped.append(f"{run_id}: missing {artifact_path}")
            continue
        artifact = json.loads(artifact_path.read_text(encoding="utf8"))
        artifact_trajectory = artifact.get("trajectory")
        if not isinstance(artifact_trajectory, list) or len(artifact_trajectory) != len(trajectory):
            skipped.append(f"{run_id}: artifact trajectory mismatch")
            continue

        compacted = dict(result)
        compacted["trajectory"] = [
            {key: value for key, value in sample.items() if key != "renderGeometries"}
            if isinstance(sample, dict)
            else sample
            for sample in trajectory
        ]
        compacted_json = json.dumps(compacted, separators=(",", ":"), ensure_ascii=False)
        changed_rows += 1
        removed_entries += duplicate_count
        bytes_before += len(raw_result.encode("utf8"))
        bytes_after += len(compacted_json.encode("utf8"))
        if args.apply:
            connection.execute(
                "UPDATE evaluation_run_records SET result = ? WHERE id = ?",
                (compacted_json, run_id),
            )

    if args.apply:
        connection.commit()
    else:
        connection.rollback()
    connection.close()
    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "changedRows": changed_rows,
        "removedGeometryCopies": removed_entries,
        "bytesBefore": bytes_before,
        "bytesAfter": bytes_after,
        "skipped": skipped,
    }, indent=2))
    return 0 if not skipped else 2


if __name__ == "__main__":
    raise SystemExit(main())
