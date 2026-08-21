"""Robot file ingestion and embodiment-readiness inspection.

RobotWorld preserves the uploaded source and reports only what can be proven
from it. A visual GLB is not promoted to an articulated robot, and a parsed
URDF/USD is not called executable until cameras and a compatible policy exist.
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterable
from uuid import uuid4

from pxr import Usd, UsdGeom, UsdPhysics

from ..config import ROBOTS_DIR

ALLOWED = {".urdf", ".xml", ".mjcf", ".usd", ".usda", ".usdc", ".glb"}
MAX_BYTES = 250 * 1024 * 1024


def _safe_name(value: str) -> str:
    name = Path(value.replace("\\", "/")).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip("-.")[:80] or "robot"
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED:
        raise ValueError(f"Unsupported robot file '{suffix or 'without extension'}'. Use URDF, MJCF/XML, USD/USDA/USDC, or GLB.")
    return f"{stem}{suffix}"


def _xml_manifest(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag == "robot":
        links = root.findall(".//link")
        joints = root.findall(".//joint")
        cameras = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() == "camera"]
        meshes = [node.get("filename") for node in root.findall(".//mesh") if node.get("filename")]
        limits_missing = sum(1 for joint in joints if joint.get("type") not in {"fixed", "continuous"} and joint.find("limit") is None)
        return {
            "format": "urdf", "name": root.get("name") or path.stem,
            "links": len(links), "joints": len(joints), "cameras": len(cameras),
            "cameraNames": [node.get("name") or f"camera_{i}" for i, node in enumerate(cameras)],
            "externalResources": meshes, "missingJointLimits": limits_missing,
            "articulated": bool(joints), "physicsParsed": bool(links),
        }
    if tag == "mujoco":
        bodies = root.findall(".//body")
        joints = root.findall(".//joint")
        cameras = root.findall(".//camera")
        return {
            "format": "mjcf", "name": root.get("model") or path.stem,
            "links": len(bodies), "joints": len(joints), "cameras": len(cameras),
            "cameraNames": [node.get("name") or f"camera_{i}" for i, node in enumerate(cameras)],
            "externalResources": [node.get("file") for node in root.findall(".//*[@file]") if node.get("file")],
            "missingJointLimits": sum(1 for node in joints if node.get("limited") == "true" and not node.get("range")),
            "articulated": bool(joints), "physicsParsed": bool(bodies),
        }
    raise ValueError("XML root must be <robot> (URDF) or <mujoco> (MJCF).")


def _usd_manifest(path: Path) -> dict[str, Any]:
    stage = Usd.Stage.Open(str(path))
    if not stage:
        raise ValueError("OpenUSD could not open this stage.")
    prims = list(stage.Traverse())
    cameras = [prim for prim in prims if prim.IsA(UsdGeom.Camera)]
    joints = [prim for prim in prims if prim.IsA(UsdPhysics.Joint)]
    articulations = [prim for prim in prims if prim.HasAPI(UsdPhysics.ArticulationRootAPI)]
    rigid = [prim for prim in prims if prim.HasAPI(UsdPhysics.RigidBodyAPI)]
    meshes = [prim for prim in prims if prim.IsA(UsdGeom.Mesh)]
    return {
        "format": "openusd", "name": stage.GetDefaultPrim().GetName() if stage.GetDefaultPrim() else path.stem,
        "links": len(rigid), "joints": len(joints), "cameras": len(cameras),
        "cameraNames": [prim.GetPath().pathString for prim in cameras],
        "meshes": len(meshes), "articulationRoots": [prim.GetPath().pathString for prim in articulations],
        "metersPerUnit": UsdGeom.GetStageMetersPerUnit(stage), "upAxis": UsdGeom.GetStageUpAxis(stage),
        "externalResources": [], "missingJointLimits": 0,
        "articulated": bool(articulations and joints), "physicsParsed": bool(rigid or articulations),
    }


def _inspect(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix in {".urdf", ".xml", ".mjcf"}:
        return _xml_manifest(path)
    if suffix in {".usd", ".usda", ".usdc"}:
        return _usd_manifest(path)
    if suffix == ".glb":
        return {
            "format": "glb", "name": path.stem, "links": 0, "joints": 0, "cameras": 0,
            "cameraNames": [], "externalResources": [], "missingJointLimits": 0,
            "articulated": False, "physicsParsed": False,
            "warning": "GLB is a visual mesh only; it contains no RobotWorld articulation or control contract.",
        }
    raise ValueError("Unsupported robot file.")


async def ingest(filename: str, chunks: AsyncIterable[bytes]) -> dict[str, Any]:
    safe = _safe_name(filename)
    robot_id = f"robot-{uuid4().hex[:12]}"
    root = (ROBOTS_DIR / robot_id).resolve()
    if root.parent != ROBOTS_DIR.resolve():
        raise ValueError("Invalid robot target.")
    root.mkdir(parents=True, exist_ok=False)
    source = root / safe
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open("wb") as stream:
            async for chunk in chunks:
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Robot file exceeds the 250 MiB upload limit.")
                digest.update(chunk)
                stream.write(chunk)
        if size == 0:
            raise ValueError("Robot file is empty.")
        parsed = _inspect(source)
        unresolved = []
        for ref in parsed.get("externalResources", []):
            clean = str(ref).removeprefix("package://")
            if not (source.parent / clean).resolve().is_file():
                unresolved.append(str(ref))
        manifest = {
            "id": robot_id, "name": parsed.pop("name"), "sourceFile": safe,
            "sourceBytes": size, "sha256": digest.hexdigest(),
            "importedAt": datetime.now(timezone.utc).isoformat(),
            **parsed, "unresolvedResources": unresolved,
            "cameraMappings": {}, "policyAdapter": None,
        }
        manifest["readiness"] = readiness(manifest)
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
        return manifest
    except Exception:
        for child in root.glob("*"):
            child.unlink(missing_ok=True)
        root.rmdir()
        raise


def readiness(manifest: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    blockers.extend(str(value) for value in manifest.get("runtimeBlockers", []) if value)
    if not manifest.get("articulated"):
        blockers.append("No articulated robot/joint graph was detected.")
    if not manifest.get("physicsParsed"):
        blockers.append("No executable rigid-body physics contract was detected.")
    if manifest.get("unresolvedResources"):
        blockers.append(f"{len(manifest['unresolvedResources'])} referenced mesh/resource files are unresolved.")
    if int(manifest.get("missingJointLimits") or 0):
        blockers.append(f"{manifest['missingJointLimits']} joints are missing required limits.")
    mappings = manifest.get("cameraMappings") or {}
    for key in ("observation.images.exterior_1_left", "observation.images.exterior_2_left"):
        if not mappings.get(key):
            blockers.append(f"Camera mapping '{key}' is not configured.")
    if not manifest.get("policyAdapter"):
        blockers.append("No robot-specific VLA-JEPA state/action adapter or fine-tuned checkpoint is attached.")
    return {"executable": not blockers, "blockers": blockers}


def register_isaac_franka(runtime_status: dict[str, Any]) -> dict[str, Any]:
    """Register the official Isaac Sim asset by reference, without copying it."""
    robot_id = "franka-panda-isaac-6"
    root = (ROBOTS_DIR / robot_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "manifest.json"
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf8"))
        except json.JSONDecodeError:
            existing = {}
    manifest = {
        "id": robot_id,
        "name": "Franka Panda 7-DOF arm + parallel gripper",
        "format": "isaac-openusd-reference",
        "sourceFile": None,
        "sourceBytes": 0,
        "sha256": None,
        "importedAt": existing.get("importedAt") or datetime.now(timezone.utc).isoformat(),
        "links": 12,
        "joints": 9,
        "armDof": 7,
        "gripperJoints": 2,
        "cameras": 0,
        "cameraNames": [],
        "externalResources": [runtime_status.get("frankaAsset")],
        "unresolvedResources": [] if runtime_status.get("ready") else [runtime_status.get("frankaAsset")],
        "missingJointLimits": 0,
        "articulated": True,
        "physicsParsed": True,
        "isaacAssetPath": runtime_status.get("frankaAsset"),
        "cameraMappings": existing.get("cameraMappings") or {},
        "policyAdapter": existing.get("policyAdapter"),
        "runtimeBlockers": list(runtime_status.get("blockers") or []),
    }
    manifest["readiness"] = readiness(manifest)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf8")
    return manifest


def list_all() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(ROBOTS_DIR.glob("*/manifest.json"), key=lambda value: value.stat().st_mtime, reverse=True):
        try:
            value = json.loads(path.read_text(encoding="utf8"))
            value["readiness"] = readiness(value)
            rows.append(value)
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def update(robot_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    path = (ROBOTS_DIR / robot_id / "manifest.json").resolve()
    if path.parent != (ROBOTS_DIR / robot_id).resolve() or not path.is_file():
        raise FileNotFoundError(robot_id)
    value = json.loads(path.read_text(encoding="utf8"))
    if "cameraMappings" in patch:
        mappings = patch["cameraMappings"]
        if not isinstance(mappings, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mappings.items()):
            raise ValueError("cameraMappings must be a string-to-string object.")
        value["cameraMappings"] = {**(value.get("cameraMappings") or {}), **mappings}
    if "policyAdapter" in patch:
        adapter = patch["policyAdapter"]
        value["policyAdapter"] = str(adapter).strip()[:500] if adapter else None
    value["readiness"] = readiness(value)
    path.write_text(json.dumps(value, indent=2), encoding="utf8")
    return value
