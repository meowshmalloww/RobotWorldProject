"""Isaac Sim 6.0.1 / Isaac Lab readiness and Franka launch contract.

This module never imports Isaac packages inside FastAPI. Isaac Sim ships its
own Python runtime; RobotWorld writes a launch manifest that the standalone
bridge consumes under that runtime.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import BASE_DIR, DATA_DIR, WORLDS_DIR

VERSION = "6.0.1"
ISAAC_LAB_TAG = "v3.0.0-beta2.patch1"
ISAAC_LAB_REVISION = "ffff603eafc6b74264a5261cc0183d6a65390d78"
FRANKA_ASSET = (
    "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.0/"
    "Isaac/IsaacLab/Robots/FrankaEmika/panda_instanceable.usd"
)
DEFAULT_ISAAC_ROOT = Path(r"D:\RobotWorldRuntimes\isaac-env")
DEFAULT_ISAAC_LAB_ROOT = Path(r"D:\IsaacLab")
PICK_PLACE_WORKER = (BASE_DIR / "workers" / "isaac_lab_pick_place.py").resolve()


def _git_revision(root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _installed_distribution_version(root: Path, distribution: str) -> str | None:
    site_packages = root / "Lib" / "site-packages"
    if not site_packages.is_dir():
        return None
    for dist_info in site_packages.glob(f"{distribution.replace('-', '_')}-*.dist-info"):
        try:
            return importlib.metadata.PathDistribution(dist_info).version
        except Exception:
            continue
    return None


def inspect(
    configured_root: str = "",
    configured_asset_root: str = "",
    configured_lab_root: str = "",
) -> dict[str, Any]:
    candidates = [
        configured_root,
        os.environ.get("ISAAC_SIM_ROOT", ""),
        str(DEFAULT_ISAAC_ROOT),
    ]
    root = next((Path(value).expanduser().resolve() for value in candidates if value and Path(value).expanduser().is_dir()), None)
    python: Path | None = None
    launcher: Path | None = None
    if root:
        python = next(
            (
                path
                for path in (
                    root / "Scripts" / "python.exe",
                    root / "python.bat",
                    root / "python.sh",
                )
                if path.is_file()
            ),
            None,
        )
        launcher = next(
            (
                path
                for path in (
                    root / "Scripts" / "isaacsim.exe",
                    root / "isaac-sim.bat",
                    root / "isaac-sim.sh",
                )
                if path.is_file()
            ),
            None,
        )
    isaac_version = _installed_distribution_version(root, "isaacsim") if root else None
    lab_package_version = _installed_distribution_version(root, "isaaclab") if root else None
    lab_tasks_version = _installed_distribution_version(root, "isaaclab_tasks") if root else None
    lab_candidates = [configured_lab_root, os.environ.get("ISAAC_LAB_ROOT", ""), str(DEFAULT_ISAAC_LAB_ROOT)]
    lab_root = next(
        (Path(value).expanduser().resolve() for value in lab_candidates if value and Path(value).expanduser().is_dir()),
        None,
    )
    lab_revision = _git_revision(lab_root) if lab_root else None
    lab_launcher = next(
        (path for path in ((lab_root / "isaaclab.bat") if lab_root else Path(),) if path.is_file()),
        None,
    )
    installed = bool(root and python and launcher and isaac_version == "6.0.1.0")
    blockers: list[str] = []
    if not installed:
        blockers.append(
            "Isaac Sim 6.0.1 Python runtime is not installed or simulation.isaacRoot does not point to its isolated environment."
        )
    if not lab_root or not lab_launcher:
        blockers.append("Isaac Lab source is not configured or isaaclab.bat is missing.")
    elif lab_revision != ISAAC_LAB_REVISION:
        blockers.append(
            f"Isaac Lab revision {lab_revision or 'unknown'} does not match pinned {ISAAC_LAB_TAG} ({ISAAC_LAB_REVISION})."
        )
    if installed and (not lab_package_version or not lab_tasks_version):
        blockers.append("Isaac Lab core/task packages are not installed in the configured Isaac Python environment.")
    asset_root = configured_asset_root or os.environ.get("ISAACSIM_ASSET_ROOT", "")
    if installed and asset_root and not (asset_root.startswith("http://") or asset_root.startswith("https://") or Path(asset_root).exists()):
        blockers.append("Configured Isaac asset root is unreachable on this machine.")
    configured_ready = installed and not blockers
    eula_accepted = os.environ.get("OMNI_KIT_ACCEPT_EULA", "").lower() in {"y", "yes", "1"}
    live_blockers = list(blockers)
    if configured_ready and not eula_accepted:
        live_blockers.append(
            "NVIDIA Omniverse EULA has not been accepted for the API/worker process; set OMNI_KIT_ACCEPT_EULA=YES only after the operator accepts the license."
        )
    return {
        "version": VERSION,
        "packageVersion": isaac_version,
        "installed": installed,
        "root": str(root) if root else "",
        "python": str(python) if python else "",
        "launcher": str(launcher) if launcher else "",
        "isaacLabRoot": str(lab_root) if lab_root else "",
        "isaacLabLauncher": str(lab_launcher) if lab_launcher else "",
        "isaacLabTag": ISAAC_LAB_TAG,
        "isaacLabRevision": lab_revision,
        "isaacLabPackageVersion": lab_package_version,
        "isaacLabTasksVersion": lab_tasks_version,
        "eulaAcceptedForApiProcess": eula_accepted,
        "assetRoot": asset_root or "Isaac Sim default asset server",
        "frankaAsset": FRANKA_ASSET,
        "franka": {
            "armDof": 7,
            "fingerJoints": 2,
            "fixedBase": True,
            "actionContract": "[dx,dy,dz,droll,dpitch,dyaw,gripper]",
            "isaacLabEnvironment": "Isaac-Lift-Cube-Franka-IK-Rel-v0",
            "oracleEnvironment": "Isaac-Lift-Cube-Franka-IK-Abs-v0",
            "cameras": ["front", "wrist"],
        },
        "configuredReady": configured_ready,
        "ready": configured_ready and eula_accepted,
        "configurationBlockers": blockers,
        "blockers": live_blockers,
    }


def write_launch_manifest(world_id: str, world_stage: Path, status: dict[str, Any]) -> Path:
    if not world_stage.is_file():
        raise FileNotFoundError("The active OpenUSD stage has not been authored.")
    output = (WORLDS_DIR / world_id / "isaac-launch.json").resolve()
    if output.parent != (WORLDS_DIR / world_id).resolve():
        raise ValueError("Invalid Isaac launch target.")
    payload = {
        "schemaVersion": 2,
        "simulator": "NVIDIA Isaac Sim",
        "simulatorVersion": VERSION,
        "isaacLabTag": ISAAC_LAB_TAG,
        "isaacLabRevision": status.get("isaacLabRevision"),
        "worldStage": str(world_stage.resolve()),
        "robot": {
            "id": "franka-panda-isaac-6",
            "name": "Franka Panda",
            "assetFromRoot": FRANKA_ASSET,
            "primPath": "/RobotWorld/Robots/Franka",
            "basePoseM": [0.0, -0.65, 0.0],
            "armDof": 7,
            "fingerJoints": 2,
            "cameras": {
                "front": {"resolution": [224, 224], "mount": "world"},
                "wrist": {"resolution": [224, 224], "mount": "panda_hand"},
            },
        },
        "physics": {
            "source": "OpenUSD stage APIs",
            "movable": "RigidBodyAPI + MassAPI + convexHull collision",
            "fixed": "static CollisionAPI + triangle mesh collision",
            "gravityMps2": -9.81,
        },
        "policy": {
            "checkpoint": os.environ.get("VLA_JEPA_CHECKPOINT_PATH", r"D:\VLA-JEPA-Pretrain"),
            "actionContract": "7D end-effector delta pose plus binary gripper",
            "adapter": "Isaac Lab DifferentialInverseKinematicsActionCfg (relative pose, DLS)",
            "executionReady": True,
            "executionGate": "The registered policy revision must still load and pass its exact camera/normalization contract.",
        },
        "runtimeReady": bool(status.get("ready")),
        "runtimeBlockers": list(status.get("blockers") or []),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf8")
    return output


def _worker_environment() -> dict[str, str]:
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "CUDA_PATH",
        "CUDA_VISIBLE_DEVICES",
        "OMNI_KIT_ACCEPT_EULA",
        "OMNI_KIT_ALLOW_ROOT",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    return environment


def run_franka_pick_place(
    status: dict[str, Any],
    *,
    seed: int = 6203,
    max_steps: int = 1200,
    timeout_seconds: float = 900.0,
) -> dict[str, Any]:
    """Run the bounded Isaac Lab oracle and return its authored evidence.

    The API process never imports Kit/PhysX. If the operator has not accepted
    NVIDIA's Omniverse EULA, the child remains fail-closed and the result says
    so; RobotWorld never sets the acceptance variable on the user's behalf.
    """

    if not status.get("ready"):
        return {
            "success": False,
            "failureCode": "runtime_unavailable",
            "failureDetail": "; ".join(status.get("blockers") or ["Isaac runtime is not ready."]),
            "runtime": status,
        }
    if not PICK_PLACE_WORKER.is_file():
        raise FileNotFoundError(PICK_PLACE_WORKER)
    python = Path(str(status["python"])).resolve(strict=True)
    run_id = f"isaac_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{os.urandom(4).hex()}"
    output = (DATA_DIR / "evaluations" / run_id).resolve()
    data_root = DATA_DIR.resolve()
    if not output.is_relative_to(data_root):
        raise RuntimeError("Isaac evaluation path escaped the RobotWorld data root.")
    output.mkdir(parents=True, exist_ok=False)
    command = [
        str(python),
        str(PICK_PLACE_WORKER),
        "--output",
        str(output),
        "--seed",
        str(seed),
        "--max-steps",
        str(max(100, min(max_steps, 5000))),
        "--headless",
        "--enable_cameras",
        "--device",
        "cuda:0",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(status["isaacLabRoot"]),
            env=_worker_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf8",
            errors="replace",
            timeout=max(30.0, min(float(timeout_seconds), 3600.0)),
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        (output / "worker.stdout.log").write_text(exc.stdout or "", encoding="utf8")
        (output / "worker.stderr.log").write_text(exc.stderr or "", encoding="utf8")
        return {
            "id": run_id,
            "success": False,
            "failureCode": "policy_timeout",
            "failureDetail": f"Isaac worker exceeded {timeout_seconds:g} seconds.",
            "artifactRoot": str(output),
            "runtime": status,
        }
    (output / "worker.stdout.log").write_text(completed.stdout, encoding="utf8")
    (output / "worker.stderr.log").write_text(completed.stderr, encoding="utf8")
    result_path = output / "result.json"
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf8"))
        except json.JSONDecodeError as exc:
            result = {
                "success": False,
                "failureCode": "worker_crash",
                "failureDetail": f"Isaac worker wrote malformed result.json: {exc}",
            }
    else:
        combined = f"{completed.stdout}\n{completed.stderr}"
        eula = "Do you accept the EULA" in combined or "OMNI_KIT_ACCEPT_EULA" in combined
        result = {
            "success": False,
            "failureCode": "eula_not_accepted" if eula else "worker_crash",
            "failureDetail": (
                "NVIDIA Omniverse EULA acceptance is required before Isaac Sim can start. "
                "RobotWorld did not accept it automatically."
                if eula
                else f"Isaac worker exited with code {completed.returncode} before writing result.json."
            ),
        }
    return {
        "id": run_id,
        **result,
        "exitCode": completed.returncode,
        "artifactRoot": str(output),
        "command": command,
        "runtime": status,
    }
