"""Execute RobotWorld's first local compiler -> oracle -> VLA vertical slice.

This is a bounded verification entry point, not a fixture. It compiles the
specified real GLB, runs authoritative MuJoCo validation/oracle control, loads
the configured VLA-JEPA checkpoint in its isolated CUDA worker, persists an
explicit zero-shot camera/action bridge, and records the learned-policy result.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.contracts import (
    CompiledAssetOracleRequest,
    CompiledAssetVlaEvaluationRequest,
    ModelRegistrationCreate,
    RigidAssetCompileRequest,
)
from app.db import init_db
from app.services import (
    control_catalog,
    evaluation_catalog,
    rigid_asset_compiler,
    vla_bridge,
    vla_policy_worker,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-glb", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, default=Path(r"D:\VLA-JEPA-Pretrain"))
    parser.add_argument("--robot-id", default="franka-panda-mujoco-f9a4918f6663")
    parser.add_argument("--display-name", default="TRELLIS red apple")
    parser.add_argument("--category", default="apple")
    parser.add_argument("--dimensions-m", type=float, nargs=3, default=(0.09, 0.085, 0.09))
    parser.add_argument("--mass-kg", type=float, default=0.18)
    parser.add_argument("--seed", type=int, default=6203)
    parser.add_argument("--max-policy-steps", type=int, default=15)
    parser.add_argument("--skip-vla", action="store_true")
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _existing_model(checkpoint: Path) -> dict[str, Any] | None:
    resolved = str(checkpoint.resolve(strict=True))
    return next((row for row in await control_catalog.list_models() if row.get("localPath") == resolved), None)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    await init_db()
    # A worker process is local to this one-shot command. Reconcile any stale
    # LOADED state left by a previous command before deciding whether to load.
    await control_catalog.reconcile_local_worker_state()
    source = args.source_glb.resolve(strict=True)
    checkpoint = args.checkpoint.resolve(strict=True)
    summary: dict[str, Any] = {"sourceGlb": str(source), "checkpoint": str(checkpoint), "robotId": args.robot_id}

    requested_dimensions = tuple(float(value) for value in args.dimensions_m)
    compile_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "sourcePath": str(source),
                "sourceSha256": _sha256(source),
                "dimensionsM": requested_dimensions,
                "massKg": args.mass_kg,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf8")
    ).hexdigest()
    existing = next(
        (
            row
            for row in await rigid_asset_compiler.list_versions()
            if row.get("sourceSha256") == _sha256(source)
            and tuple((row.get("manifest") or {}).get("dimensionsM") or ()) == requested_dimensions
            and (row.get("manifest") or {}).get("massKg") == args.mass_kg
        ),
        None,
    )
    if existing is None:
        compile_request = RigidAssetCompileRequest(
            displayName=args.display_name,
            category=args.category,
            sourceGlbPath=str(source),
            expectedSourceSha256=_sha256(source),
            sourceAssetId=source.parent.name,
            sourceIdentityScope="category_prior",
            dimensionsM=requested_dimensions,
            dimensionMethod=f"bounded_{args.category}_category_prior",
            dimensionConfidence=0.55,
            massKg=args.mass_kg,
            massMethod=f"bounded_{args.category}_category_prior",
            massConfidence=0.50,
            frictionRange=(0.35, 0.65),
            restitutionRange=(0.02, 0.10),
            semantics=[args.category],
            affordances=["pickable"],
            licenseMetadata={"source": "local_trellis_generation", "redistribution": "unreviewed"},
        )
        compiled = await rigid_asset_compiler.compile_rigid(
            compile_request,
            idempotency_key=f"local-vertical-compile-{compile_fingerprint[:24]}",
            actor="local-verification",
        )
        existing = compiled["result"]["assetVersion"]
    summary["assetVersion"] = {key: existing.get(key) for key in ("id", "assetId", "version", "lifecycleState")}

    if existing["lifecycleState"] not in {"PHYSICS_VALIDATED", "ORACLE_VALIDATED"}:
        summary["oracle"] = {
            "skipped": True,
            "reason": "asset did not pass static/physics compilation",
            "validationErrors": existing.get("validationErrors") or [],
        }
        summary["vla"] = {"skipped": True, "reason": "invalid compiled asset"}
        return summary

    if existing["lifecycleState"] != "ORACLE_VALIDATED":
        oracle = await evaluation_catalog.run_compiled_asset_pick_place_oracle(
            CompiledAssetOracleRequest(robotId=args.robot_id, assetVersionId=existing["id"], seed=args.seed),
            idempotency_key=f"local-vertical-oracle-{existing['id']}-{args.seed}",
            actor="local-verification",
        )
        summary["oracle"] = oracle["result"]["evaluation"]
        existing = oracle["result"]["assetVersion"]
    else:
        summary["oracle"] = {"reused": True, "assetLifecycleState": existing["lifecycleState"]}

    if args.skip_vla or existing["lifecycleState"] != "ORACLE_VALIDATED":
        summary["vla"] = {
            "skipped": True,
            "reason": "disabled" if args.skip_vla else "asset did not pass the deterministic oracle",
        }
        return summary

    model = await _existing_model(checkpoint)
    if model is None:
        registered = await control_catalog.register_model(
            ModelRegistrationCreate(
                displayName="VLA-JEPA Pretrain (local)",
                roles=["vla_policy"],
                providerType="local_path",
                localPath=str(checkpoint),
                expectedDevice="cuda",
                precision="bfloat16",
                licenseMetadata={"spdx": "Apache-2.0", "source": "local_checkpoint"},
            ),
            idempotency_key=f"local-vla-register-{_sha256(checkpoint / 'config.json')[:24]}",
            actor="local-verification",
        )
        model = registered["result"]["model"]
    if model["lifecycleState"] in {"REGISTERED", "INVALID"}:
        validated = await control_catalog.validate_model(
            model["id"],
            compute_content_hash=False,
            idempotency_key=(
                f"local-vla-validate-{model['id']}-r{model['revision']}-"
                f"{hashlib.sha256(str(model.get('lastError') or 'none').encode('utf8')).hexdigest()[:12]}"
            ),
            actor="local-verification",
        )
        model = validated["result"]["model"]
    if model["lifecycleState"] != "LOADED":
        loaded = await control_catalog.load_model(
            model["id"],
            # Worker residency is process-local, so a new verification process
            # must not reuse a durable command whose old worker no longer exists.
            idempotency_key=(
                f"local-vla-load-{model['id']}-pid{os.getpid()}-"
                f"{str(model.get('manifestSha256') or 'unvalidated')[:16]}"
            ),
            actor="local-verification",
        )
        model = loaded["result"]["model"]
        summary["worker"] = loaded["result"].get("worker")

    camera_keys = list((model.get("capabilities") or {}).get("cameraKeys") or [])
    mapping = {camera_keys[0]: "front", camera_keys[1]: "wrist"}
    attached = await vla_bridge.attach_zero_shot_bridge(
        model["id"],
        args.robot_id,
        camera_mapping=mapping,
        policy_control_hz=10,
        idempotency_key=f"local-vla-franka-bridge-{model['id']}-{args.robot_id}",
        actor="local-verification",
    )
    summary["bridge"] = attached["result"]["bridge"]
    evaluated = await evaluation_catalog.run_compiled_asset_pick_place_vla(
        CompiledAssetVlaEvaluationRequest(
            robotId=args.robot_id,
            assetVersionId=existing["id"],
            modelId=model["id"],
            instruction=f"Pick up the {args.display_name} and place it in the target.",
            maxPolicySteps=args.max_policy_steps,
            seed=args.seed,
        ),
        idempotency_key=(
            f"local-vla-eval-{model['id']}-{existing['id']}-{args.seed}-pid{os.getpid()}-"
            f"{args.max_policy_steps}-checkpoint-gripper-pm1"
        ),
        actor="local-verification",
    )
    summary["vla"] = evaluated["result"]["evaluation"]
    return summary


async def main() -> int:
    args = _arguments()
    try:
        result = await run(args)
        oracle = result.get("oracle") or {}
        vla = result.get("vla") or {}
        worker = result.get("worker") or {}
        resident = worker.get("resident") or {}
        concise = {
            "sourceGlb": result.get("sourceGlb"),
            "assetVersion": result.get("assetVersion"),
            "oracle": {
                "id": oracle.get("id") or oracle.get("runId"),
                "status": oracle.get("status"),
                "success": oracle.get("success"),
                "failureCode": oracle.get("failureCode"),
                "reused": oracle.get("reused"),
                "assetLifecycleState": oracle.get("assetLifecycleState"),
            },
            "worker": {
                "loaded": worker.get("loaded"),
                "device": resident.get("device"),
                "parameterCount": resident.get("parameterCount"),
                "loadDurationSeconds": resident.get("loadDurationSeconds"),
                "checkpointConfigSha256": resident.get("checkpointConfigSha256"),
                "worldModelInferenceRequired": resident.get("worldModelInferenceRequired"),
            },
            "bridge": {
                key: (result.get("bridge") or {}).get(key)
                for key in ("modelId", "robotId", "validationLevel", "zeroShot", "executable", "blockers")
            },
            "vla": {
                "id": vla.get("id") or vla.get("runId"),
                "status": vla.get("status"),
                "success": vla.get("success"),
                "failureCode": vla.get("failureCode"),
                "failureDetail": vla.get("failureDetail"),
                "predicate": (vla.get("result") or {}).get("predicate") or vla.get("predicate"),
                "artifactDir": vla.get("artifactDir"),
            },
        }
        print(json.dumps(concise, indent=2, default=str))
        return 0
    finally:
        vla_policy_worker.stop()
        await control_catalog.reconcile_local_worker_state()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
