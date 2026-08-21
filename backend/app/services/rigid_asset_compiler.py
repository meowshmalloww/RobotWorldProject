"""Immutable rigid-asset compilation from a verified GLB into OpenUSD and MuJoCo.

The source GLB is visual evidence, never a collider.  This compiler applies one
uniform scale, authors a separate convex collision derivative, writes explicit
mass properties, and runs a real MuJoCo drop/settle test before reporting the
version as ``PHYSICS_VALIDATED``.  Promotion remains a separate oracle gate.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from ..config import ASSETS_DIR, BASE_DIR, DATA_DIR
from ..contracts import ArtifactReference, AssetManifest, RigidAssetCompileRequest
from ..db import SessionLocal
from ..models import AuditEvent, CompiledAssetVersionRecord
from ..telemetry import emit_metric, span
from ..util import new_id
from . import command_store, evidence_catalog, usda


MAX_SOURCE_BYTES_DEFAULT = 500 * 1024 * 1024
DIMENSION_CONFIDENCE_GATE = 0.80
MASS_CONFIDENCE_GATE = 0.70
MAX_COLLISION_SAMPLE_POINTS = 512
DROP_TEST_SECONDS = 6.0
DROP_SETTLE_WINDOW_SECONDS = 0.75
MAX_DROP_PENETRATION_M = 0.01
MAX_SETTLE_POSITION_SPAN_M = 0.003
MAX_SETTLE_LINEAR_P95_MPS = 0.02
MAX_SETTLE_ANGULAR_P95_RAD_S = 0.15
MAX_FINAL_LINEAR_SPEED_MPS = 0.01
MAX_FINAL_ANGULAR_SPEED_RAD_S = 0.05


class AssetCompileError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_limit() -> int:
    try:
        configured = int(os.environ.get("ROBOTWORLD_MAX_GLB_BYTES", MAX_SOURCE_BYTES_DEFAULT))
    except ValueError:
        return MAX_SOURCE_BYTES_DEFAULT
    return max(1_000_000, min(configured, 2_000_000_000))


def allowed_source_roots() -> list[Path]:
    """Return resolved roots from explicit server configuration.

    The artifact store is always allowed. External paths require one of the
    two documented environment settings; no drive-wide implicit allowlist is
    used.
    """
    values = [str(ASSETS_DIR)]
    single = os.environ.get("ROBOT_ASSET_ROOT", "").strip()
    if single:
        values.append(single)
    values.extend(
        item.strip()
        for item in os.environ.get("ROBOTWORLD_ASSET_IMPORT_ROOTS", "").split(os.pathsep)
        if item.strip()
    )
    roots: list[Path] = []
    for value in values:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = BASE_DIR.parent / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)
    return roots


def resolve_source_glb(raw_path: str) -> tuple[Path, str, int]:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = BASE_DIR.parent / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Source GLB does not exist: {candidate}") from exc
    if not resolved.is_file() or resolved.suffix.lower() != ".glb":
        raise ValueError("Rigid compilation source must be a .glb file.")
    roots = allowed_source_roots()
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        shown = ", ".join(str(root) for root in roots) or "(none configured)"
        raise ValueError(f"Source GLB is outside the server allowlist. Allowed roots: {shown}")
    size = resolved.stat().st_size
    if size <= 20 or size > _safe_limit():
        raise ValueError(f"Source GLB size {size} bytes is outside the configured limit.")
    with resolved.open("rb") as handle:
        magic = handle.read(4)
    if magic != b"glTF":
        raise ValueError("Source has a .glb extension but does not have the glTF binary magic bytes.")
    return resolved, _sha256_file(resolved), size


def _artifact(path: Path, kind: str, media_type: str) -> ArtifactReference:
    resolved = path.resolve(strict=True)
    data_root = DATA_DIR.resolve()
    if not resolved.is_relative_to(data_root):
        raise RuntimeError("Compiled artifact escaped the RobotWorld data root.")
    digest = _sha256_file(resolved)
    return ArtifactReference(
        id=f"artifact_{digest[:20]}",
        kind=kind,
        artifact_ref=resolved.relative_to(data_root).as_posix(),
        sha256=digest,
        size_bytes=resolved.stat().st_size,
        media_type=media_type,
        immutable=True,
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(command_store.json_safe(value), indent=2, sort_keys=True, ensure_ascii=False).encode("utf8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)


def asset_version_view(row: CompiledAssetVersionRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "assetId": row.asset_id,
        "version": row.version,
        "displayName": row.display_name,
        "category": row.category,
        "assetKind": row.asset_kind,
        "lifecycleState": row.lifecycle_state,
        "evidenceBundleId": row.evidence_bundle_id,
        "sourcePath": row.source_path,
        "sourceSha256": row.source_sha256,
        "manifestSha256": row.manifest_sha256,
        "artifactRoot": row.artifact_root,
        "manifest": dict(row.manifest or {}),
        "validationReport": dict(row.validation_report or {}),
        "validationErrors": list(row.validation_errors or []),
        "promotionEligible": row.promotion_eligible,
        "promotionBlockers": list(row.promotion_blockers or []),
        "commandId": row.command_id,
        "createdBy": row.created_by,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


async def list_versions(limit: int = 100) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(CompiledAssetVersionRecord)
                .order_by(CompiledAssetVersionRecord.created_at.desc())
                .limit(max(1, min(limit, 500)))
            )
        ).scalars().all()
    return [asset_version_view(row) for row in rows]


async def get_version(version_id: str) -> dict[str, Any]:
    async with SessionLocal() as session:
        row = await session.get(CompiledAssetVersionRecord, version_id)
        if row is None:
            raise KeyError(version_id)
        return asset_version_view(row)


async def _evidence_provenance(payload: RigidAssetCompileRequest) -> dict[str, Any]:
    if payload.source_identity_scope == "exact" and not payload.evidence_bundle_id:
        raise ValueError("Exact-identity compilation requires a QUALITY_PASSED evidence bundle.")
    if not payload.evidence_bundle_id:
        return {"identityScope": payload.source_identity_scope, "evidenceBundle": None}
    value = await evidence_catalog.get_bundle(payload.evidence_bundle_id)
    bundle = value["bundle"]
    if bundle["lifecycleState"] != "QUALITY_PASSED":
        raise ValueError("Asset compilation requires a QUALITY_PASSED evidence bundle.")
    identity = dict(bundle.get("identity") or {})
    if payload.source_identity_scope == "exact" and not identity.get("exact"):
        raise ValueError("The linked evidence bundle did not pass exact-object identity resolution.")

    properties = {item["name"]: item for item in bundle.get("properties") or []}
    expected_dimensions = [properties.get(name) for name in ("width", "height", "depth")]
    if payload.source_identity_scope == "exact" and any(item is None for item in expected_dimensions):
        raise ValueError("Exact-identity compilation requires width, height, and depth evidence.")
    if all(item is not None for item in expected_dimensions):
        for label, provided, item in zip(("width", "height", "depth"), payload.dimensions_m, expected_dimensions):
            expected = float(item["value"])
            if abs(float(provided) - expected) / max(expected, 1e-12) > 0.05:
                raise ValueError(f"Requested {label} differs from the linked evidence by more than 5%.")
        minimum_confidence = min(float(item["confidence"]) for item in expected_dimensions)
        if payload.dimension_confidence > minimum_confidence + 1e-9:
            raise ValueError("Dimension confidence cannot exceed the linked evidence confidence.")
    mass = properties.get("mass")
    if payload.source_identity_scope == "exact" and mass is None:
        raise ValueError("Exact-identity compilation requires mass evidence.")
    if mass is not None:
        expected_mass = float(mass["value"])
        if abs(payload.mass_kg - expected_mass) / max(expected_mass, 1e-12) > 0.10:
            raise ValueError("Requested mass differs from the linked evidence by more than 10%.")
        if payload.mass_confidence > float(mass["confidence"]) + 1e-9:
            raise ValueError("Mass confidence cannot exceed the linked evidence confidence.")
    return {
        "identityScope": payload.source_identity_scope,
        "evidenceBundle": {
            "id": bundle["id"],
            "sha256": bundle["bundleSha256"],
            "identityConfidence": bundle["identityConfidence"],
            "completeness": bundle["completeness"],
            "identity": identity,
        },
    }


def _connected_components(mesh) -> tuple[int, float]:
    import numpy as np
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    welded = mesh.copy()
    welded.merge_vertices(digits_vertex=6)
    faces = np.asarray(welded.faces, dtype=np.int64)
    if not len(faces):
        return 0, 0.0
    edges = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]), axis=0)
    rows = np.concatenate((edges[:, 0], edges[:, 1]))
    cols = np.concatenate((edges[:, 1], edges[:, 0]))
    graph = coo_matrix((np.ones(len(rows), dtype=np.uint8), (rows, cols)), shape=(len(welded.vertices), len(welded.vertices)))
    count, labels = connected_components(graph, directed=False)
    sizes = np.bincount(labels)
    return int(count), float(sizes.max() / max(1, sizes.sum()))


def _prepare_mesh(source: Path, payload: RigidAssetCompileRequest):
    import numpy as np
    import trimesh

    loaded = trimesh.load(source, force="scene", process=False, skip_materials=True)
    mesh = loaded.to_geometry() if isinstance(loaded, trimesh.Scene) else loaded
    if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.vertices) or not len(mesh.faces):
        raise AssetCompileError("STATIC_VALIDATION: source GLB contains no usable triangle mesh.")
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    errors: list[str] = []
    warnings: list[str] = []
    if not np.isfinite(vertices).all():
        errors.append("source mesh contains NaN or infinite vertices")
    if faces.min(initial=0) < 0 or faces.max(initial=0) >= len(vertices):
        errors.append("source mesh contains invalid face indices")
    source_extents = np.asarray(mesh.extents, dtype=np.float64)
    if not np.isfinite(source_extents).all() or (source_extents <= 1e-9).any():
        errors.append("source mesh has an empty or near-zero bounding-box axis")
    if len(faces) > payload.max_visual_triangles:
        errors.append(f"visual triangle count {len(faces)} exceeds configured limit {payload.max_visual_triangles}")

    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    degenerate = int(np.count_nonzero(~np.isfinite(areas) | (areas <= 1e-14)))
    sorted_faces = np.sort(faces, axis=1)
    duplicate = int(len(faces) - len(np.unique(sorted_faces, axis=0)))
    if degenerate / max(1, len(faces)) > 0.05:
        errors.append("more than 5% of source faces are degenerate")
    if duplicate:
        warnings.append(f"removed {duplicate} duplicate source faces from the derived visual")
    if degenerate:
        warnings.append(f"removed {degenerate} degenerate source faces from the derived visual")

    target_whd = np.asarray(payload.dimensions_m, dtype=np.float64)
    # glTF is X=width, Y=height, Z=depth. Input dimensions are W,H,D.
    ratios = target_whd / source_extents
    uniform_scale = float(np.median(ratios))
    residuals = np.abs(source_extents * uniform_scale - target_whd) / target_whd
    max_residual = float(np.max(residuals))
    if not math.isfinite(uniform_scale) or uniform_scale <= 0:
        errors.append("uniform scale could not be resolved")
    if max_residual > payload.max_aspect_residual:
        errors.append(
            f"uniform-scale aspect residual {max_residual:.4f} exceeds configured limit {payload.max_aspect_residual:.4f}"
        )
    if errors:
        report = {
            "stage": "STATIC_VALIDATION",
            "passed": False,
            "errors": errors,
            "warnings": warnings,
            "sourceGeometry": {
                "vertices": int(len(vertices)),
                "triangles": int(len(faces)),
                "sourceExtentsGltf": source_extents.tolist(),
                "targetDimensionsWHD": target_whd.tolist(),
                "uniformScale": uniform_scale,
                "axisResiduals": residuals.tolist(),
                "maxAspectResidual": max_residual,
                "degenerateFaces": degenerate,
                "duplicateFaces": duplicate,
            },
        }
        raise AssetCompileError(json.dumps(report, separators=(",", ":")))

    clean = mesh.copy()
    keep = clean.nondegenerate_faces(height=1e-12) & clean.unique_faces()
    clean.update_faces(keep)
    clean.remove_unreferenced_vertices()
    robot_points = np.stack(
        (np.asarray(clean.vertices)[:, 0], -np.asarray(clean.vertices)[:, 2], np.asarray(clean.vertices)[:, 1]), axis=1
    ) * uniform_scale
    bounds = np.stack((robot_points.min(axis=0), robot_points.max(axis=0)))
    translation = np.asarray((-(bounds[0, 0] + bounds[1, 0]) / 2, -(bounds[0, 1] + bounds[1, 1]) / 2, -bounds[0, 2]))
    robot_points += translation
    clean.vertices = robot_points

    component_count, largest_component_ratio = _connected_components(clean)
    if component_count > 1:
        warnings.append(
            f"derived rigid visual has {component_count} welded components; largest contains {largest_component_ratio:.4f} of vertices"
        )
    normals = np.asarray(clean.vertex_normals)
    if len(normals) != len(clean.vertices) or not np.isfinite(normals).all():
        errors.append("derived visual normals are invalid")
    uv = getattr(getattr(clean, "visual", None), "uv", None)
    if uv is not None and not np.isfinite(np.asarray(uv)).all():
        errors.append("derived visual UV coordinates contain NaN or infinity")
    if errors:
        raise AssetCompileError("STATIC_VALIDATION: " + "; ".join(errors))

    actual_xyz = np.asarray(clean.extents, dtype=float)
    report = {
        "stage": "STATIC_VALIDATION",
        "passed": True,
        "errors": [],
        "warnings": warnings,
        "sourceGeometry": {
            "vertices": int(len(vertices)),
            "triangles": int(len(faces)),
            "sourceExtentsGltf": source_extents.tolist(),
            "targetDimensionsWHD": target_whd.tolist(),
            "uniformScale": uniform_scale,
            "axisResiduals": residuals.tolist(),
            "maxAspectResidual": max_residual,
            "degenerateFaces": degenerate,
            "duplicateFaces": duplicate,
        },
        "derivedVisual": {
            "vertices": int(len(clean.vertices)),
            "triangles": int(len(clean.faces)),
            "dimensionsRobotXYZ": actual_xyz.tolist(),
            "dimensionsWHD": [float(actual_xyz[0]), float(actual_xyz[2]), float(actual_xyz[1])],
            "weldedComponentCount": component_count,
            "largestWeldedComponentRatio": largest_component_ratio,
            "watertight": bool(clean.is_watertight),
            "eulerNumber": int(clean.euler_number),
            "normalCount": int(len(normals)),
            "uvCount": int(len(uv)) if uv is not None else 0,
        },
        "coordinateTransform": {
            "source": "glTF right-handed Y-up",
            "target": "RobotWorld right-handed Z-up metres",
            "mapping": "(x, y, z) -> (x, -z, y)",
            "uniformScale": uniform_scale,
            "translationM": translation.tolist(),
            "bottomAligned": True,
        },
    }
    return clean, report, uniform_scale, tuple(float(item) for item in translation), tuple(float(item) for item in actual_xyz)


def _collision_and_mass(mesh, mass_kg: float):
    import numpy as np
    import trimesh

    points = np.asarray(mesh.vertices, dtype=np.float64)
    # A generated visual may have hundreds of thousands of vertices. The
    # collision derivative is deliberately bounded before taking its hull so
    # it cannot silently become another high-detail dynamic triangle mesh.
    sample_count = min(MAX_COLLISION_SAMPLE_POINTS, len(points))
    sampled = points[np.linspace(0, len(points) - 1, sample_count, dtype=np.int64)]
    hull = trimesh.Trimesh(vertices=sampled, process=False).convex_hull
    if not hull.is_watertight or not math.isfinite(float(hull.volume)) or hull.volume <= 1e-12:
        raise AssetCompileError("COLLISION_VALIDATION: convex collision derivative has no positive watertight volume.")
    properties = hull.mass_properties
    density = mass_kg / float(properties.mass)
    inertia = np.asarray(properties.inertia, dtype=np.float64) * density
    center = np.asarray(properties.center_mass, dtype=np.float64)
    eigenvalues, eigenvectors = np.linalg.eigh(inertia)
    if np.linalg.det(eigenvectors) < 0:
        eigenvectors[:, 0] *= -1
    if not np.isfinite(inertia).all() or (eigenvalues <= 0).any():
        raise AssetCompileError("MASS_VALIDATION: computed inertia is not finite positive-definite.")
    # A rigid-body inertia must satisfy the triangle inequalities.
    ordered = np.sort(eigenvalues)
    if ordered[0] + ordered[1] < ordered[2] - 1e-10:
        raise AssetCompileError("MASS_VALIDATION: computed principal inertia violates the triangle inequality.")
    full = (
        float(inertia[0, 0]),
        float(inertia[1, 1]),
        float(inertia[2, 2]),
        float(inertia[0, 1]),
        float(inertia[0, 2]),
        float(inertia[1, 2]),
    )
    return hull, center, inertia, full, eigenvalues, eigenvectors, {
        "type": "deterministic_sampled_convex_hull",
        "inputPointCount": int(len(points)),
        "samplePointCount": int(sample_count),
        "vertices": int(len(hull.vertices)),
        "triangles": int(len(hull.faces)),
        "watertight": bool(hull.is_watertight),
        "volumeM3": float(hull.volume),
        "densityKgM3": density,
    }


def _write_rigid_usd(
    visual_layer: Path,
    collision_mesh,
    out_path: Path,
    *,
    payload: RigidAssetCompileRequest,
    center,
    eigenvalues,
    eigenvectors,
) -> dict[str, Any]:
    import numpy as np
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade  # type: ignore
    from scipy.spatial.transform import Rotation

    stage = Usd.Stage.CreateNew(str(out_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(root.GetPrim())
    rigid = UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
    rigid.CreateRigidBodyEnabledAttr(True)
    mass_api = UsdPhysics.MassAPI.Apply(root.GetPrim())
    mass_api.CreateMassAttr(float(payload.mass_kg))
    mass_api.CreateCenterOfMassAttr(Gf.Vec3f(*[float(item) for item in center]))
    rotation = Rotation.from_matrix(np.asarray(eigenvectors, dtype=float)).as_quat()
    mass_api.CreateDiagonalInertiaAttr(Gf.Vec3f(*[float(item) for item in eigenvalues]))
    mass_api.CreatePrincipalAxesAttr(Gf.Quatf(float(rotation[3]), Gf.Vec3f(*[float(item) for item in rotation[:3]])))

    visual = UsdGeom.Xform.Define(stage, "/Asset/Visual")
    visual.GetPrim().GetReferences().AddReference(visual_layer.name, "/Visual")
    collision = UsdGeom.Mesh.Define(stage, "/Asset/Collision")
    collision.CreatePointsAttr([tuple(map(float, row)) for row in collision_mesh.vertices])
    collision.CreateFaceVertexCountsAttr([3] * len(collision_mesh.faces))
    collision.CreateFaceVertexIndicesAttr(collision_mesh.faces.reshape(-1).tolist())
    UsdPhysics.CollisionAPI.Apply(collision.GetPrim())
    mesh_collision = UsdPhysics.MeshCollisionAPI.Apply(collision.GetPrim())
    mesh_collision.CreateApproximationAttr("convexHull")

    material = UsdShade.Material.Define(stage, "/Asset/PhysicsMaterial")
    physics_material = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
    static_friction = float(payload.friction_range[1])
    dynamic_friction = float(sum(payload.friction_range) / 2)
    restitution = float(sum(payload.restitution_range) / 2)
    physics_material.CreateStaticFrictionAttr(static_friction)
    physics_material.CreateDynamicFrictionAttr(dynamic_friction)
    physics_material.CreateRestitutionAttr(restitution)
    collision.GetPrim().CreateRelationship("material:binding:physics").SetTargets([material.GetPath()])
    root.GetPrim().CreateAttribute("robotworld:semantics", Sdf.ValueTypeNames.StringArray).Set(payload.semantics)
    root.GetPrim().CreateAttribute("robotworld:affordances", Sdf.ValueTypeNames.StringArray).Set(payload.affordances)
    root.GetPrim().CreateAttribute("robotworld:physicalStatus", Sdf.ValueTypeNames.String).Set("physics_candidate")
    stage.Save()

    reopened = Usd.Stage.Open(str(out_path))
    if reopened is None:
        raise AssetCompileError("OPENUSD_VALIDATION: generated rigid layer could not be reopened.")
    if UsdGeom.GetStageUpAxis(reopened) != UsdGeom.Tokens.z or abs(UsdGeom.GetStageMetersPerUnit(reopened) - 1.0) > 1e-9:
        raise AssetCompileError("OPENUSD_VALIDATION: stage units or up axis are invalid.")
    if abs(UsdPhysics.GetStageKilogramsPerUnit(reopened) - 1.0) > 1e-9:
        raise AssetCompileError("OPENUSD_VALIDATION: stage kilogram unit is invalid.")
    root_prim = reopened.GetPrimAtPath("/Asset")
    collider = reopened.GetPrimAtPath("/Asset/Collision")
    visual_mesh = reopened.GetPrimAtPath("/Asset/Visual/Mesh")
    if not root_prim.HasAPI(UsdPhysics.RigidBodyAPI) or not root_prim.HasAPI(UsdPhysics.MassAPI):
        raise AssetCompileError("OPENUSD_VALIDATION: explicit rigid-body or mass schema is missing.")
    if not collider.HasAPI(UsdPhysics.CollisionAPI) or not visual_mesh.IsA(UsdGeom.Mesh):
        raise AssetCompileError("OPENUSD_VALIDATION: collision or composed visual mesh is missing.")
    return {
        "passed": True,
        "metersPerUnit": float(UsdGeom.GetStageMetersPerUnit(reopened)),
        "kilogramsPerUnit": float(UsdPhysics.GetStageKilogramsPerUnit(reopened)),
        "upAxis": str(UsdGeom.GetStageUpAxis(reopened)),
        "visualResolved": True,
        "collisionApproximation": "convexHull",
        "massAuthored": True,
        "inertiaAuthored": True,
    }


def _inertial_xml(center, full_inertia, mass_kg: float) -> str:
    pos = " ".join(f"{float(value):.12g}" for value in center)
    values = " ".join(f"{float(value):.12g}" for value in full_inertia)
    return f'<inertial pos="{pos}" mass="{mass_kg:.12g}" fullinertia="{values}"/>'


def _write_mjcf(
    runtime_path: Path,
    validation_path: Path,
    *,
    center,
    full_inertia,
    payload: RigidAssetCompileRequest,
    actual_height: float,
) -> None:
    friction = float(sum(payload.friction_range) / 2)
    inertial = _inertial_xml(center, full_inertia, payload.mass_kg)
    asset_block = '''
    <mesh name="compiled_visual" file="../../visual/model.obj"/>
    <mesh name="compiled_collision" file="../../collision/convex_hull.obj" maxhullvert="256"/>
    <material name="compiled_pbr_fallback" rgba="0.65 0.65 0.68 1" roughness="0.65" metallic="0.0"/>
'''
    body_block = f'''
    <body name="compiled_asset" pos="0 0 0.02">
      <freejoint name="compiled_asset_free"/>
      {inertial}
      <geom name="compiled_visual_geom" type="mesh" mesh="compiled_visual" material="compiled_pbr_fallback" contype="0" conaffinity="0" group="2"/>
      <geom name="compiled_collision_geom" type="mesh" mesh="compiled_collision" rgba="0 0 0 0" friction="{friction:.8g} 0.005 0.0001" solref="0.005 1" group="3"/>
    </body>
'''
    runtime_path.write_text(
        f'''<mujoco model="robotworld_compiled_rigid_asset">
  <compiler angle="radian" inertiafromgeom="false" meshdir="."/>
  <option timestep="0.002" gravity="0 0 -9.81"/>
  <asset>{asset_block}  </asset>
  <worldbody>{body_block}  </worldbody>
</mujoco>
''',
        encoding="utf8",
    )
    drop_height = max(0.12, min(0.35, actual_height * 0.5))
    validation_body = body_block.replace('pos="0 0 0.02"', f'pos="0 0 {drop_height:.8g}"')
    validation_asset_block = asset_block.replace("../../visual/", "../visual/").replace(
        "../../collision/", "../collision/"
    )
    validation_path.write_text(
        f'''<mujoco model="robotworld_rigid_drop_validation">
  <compiler angle="radian" inertiafromgeom="false" meshdir="."/>
  <option timestep="0.002" gravity="0 0 -9.81" integrator="implicitfast"/>
  <visual><global offwidth="320" offheight="320"/></visual>
  <asset>{validation_asset_block}  </asset>
  <worldbody>
    <light name="key" pos="0 -1 1.5" dir="0 1 -1"/>
    <geom name="floor" type="plane" size="2 2 0.1" rgba="0.18 0.19 0.21 1" friction="0.8 0.01 0.001" solref="0.005 1"/>
    <camera name="validation" pos="0.8 -0.8 0.65" xyaxes="0.7071 0.7071 0 -0.4082 0.4082 0.8165"/>
    {validation_body}
  </worldbody>
</mujoco>
''',
        encoding="utf8",
    )


def _simulate_drop(validation_path: Path, preview_path: Path) -> dict[str, Any]:
    import mujoco
    import numpy as np

    def run_once() -> tuple[dict[str, Any], Any, Any]:
        model = mujoco.MjModel.from_xml_path(str(validation_path))
        data = mujoco.MjData(model)
        mujoco.mj_resetData(model, data)
        mujoco.mj_forward(model, data)
        initial_contacts = int(data.ncon)
        body_id = model.body("compiled_asset").id
        steps = int(math.ceil(DROP_TEST_SECONDS / model.opt.timestep))
        settle_samples: list[list[float]] = []
        settle_speeds: list[float] = []
        settle_linear_speeds: list[float] = []
        settle_angular_speeds: list[float] = []
        trajectory: list[dict[str, Any]] = []
        contact_observed = False
        minimum_contact_distance = 0.0
        finite = True
        for index in range(steps):
            mujoco.mj_step(model, data)
            finite = finite and bool(np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all())
            if data.ncon:
                contact_observed = True
                minimum_contact_distance = min(minimum_contact_distance, *(float(data.contact[i].dist) for i in range(data.ncon)))
            if index >= steps - int(DROP_SETTLE_WINDOW_SECONDS / model.opt.timestep):
                settle_samples.append(np.asarray(data.xpos[body_id], dtype=float).tolist())
                settle_speeds.append(float(np.linalg.norm(data.qvel[:6])))
                settle_linear_speeds.append(float(np.linalg.norm(data.qvel[:3])))
                settle_angular_speeds.append(float(np.linalg.norm(data.qvel[3:6])))
            if index % 100 == 0 or index == steps - 1:
                trajectory.append(
                    {
                        "timeSeconds": float(data.time),
                        "positionM": np.asarray(data.xpos[body_id], dtype=float).tolist(),
                        "linearAngularSpeed": float(np.linalg.norm(data.qvel[:6])),
                        "contacts": int(data.ncon),
                        "finite": finite,
                    }
                )
        positions = np.asarray(settle_samples, dtype=float)
        position_span = float(np.max(np.ptp(positions, axis=0))) if len(positions) else math.inf
        maximum_settle_speed = max(settle_speeds, default=math.inf)
        result = {
            "physicsBackend": "mujoco",
            "mujocoVersion": mujoco.__version__,
            "timestepSeconds": float(model.opt.timestep),
            "steps": steps,
            "simulatedSeconds": float(data.time),
            "initialContactCount": initial_contacts,
            "contactObserved": contact_observed,
            "maxPenetrationM": max(0.0, -minimum_contact_distance),
            "finite": finite,
            "settlePositionSpanM": position_span,
            "maxSettleSpeed": maximum_settle_speed,
            "settleLinearSpeedP95Mps": float(np.percentile(settle_linear_speeds, 95)) if settle_linear_speeds else math.inf,
            "settleAngularSpeedP95RadS": float(np.percentile(settle_angular_speeds, 95)) if settle_angular_speeds else math.inf,
            "finalLinearSpeedMps": float(np.linalg.norm(data.qvel[:3])),
            "finalAngularSpeedRadS": float(np.linalg.norm(data.qvel[3:6])),
            "finalQpos": np.asarray(data.qpos, dtype=float).tolist(),
            "finalQvel": np.asarray(data.qvel, dtype=float).tolist(),
            "trajectory": trajectory,
        }
        return result, model, data

    first, model, data = run_once()
    second, _, _ = run_once()
    repeat_error = float(max(abs(a - b) for a, b in zip(first["finalQpos"], second["finalQpos"])))
    errors: list[str] = []
    if first["initialContactCount"]:
        errors.append("drop test starts in contact or penetration")
    if not first["contactObserved"]:
        errors.append("drop test never produced floor contact")
    if not first["finite"]:
        errors.append("drop test produced NaN or infinite state")
    if first["maxPenetrationM"] > MAX_DROP_PENETRATION_M:
        errors.append(
            f"contact penetration {first['maxPenetrationM']:.6f} m exceeds {MAX_DROP_PENETRATION_M:.6f} m"
        )
    if (
        first["settlePositionSpanM"] > MAX_SETTLE_POSITION_SPAN_M
        or first["settleLinearSpeedP95Mps"] > MAX_SETTLE_LINEAR_P95_MPS
        or first["settleAngularSpeedP95RadS"] > MAX_SETTLE_ANGULAR_P95_RAD_S
        or first["finalLinearSpeedMps"] > MAX_FINAL_LINEAR_SPEED_MPS
        or first["finalAngularSpeedRadS"] > MAX_FINAL_ANGULAR_SPEED_RAD_S
    ):
        errors.append("object did not reach the configured stable settle window")
    if repeat_error > 1e-9:
        errors.append(f"fixed-seed deterministic repeat error {repeat_error:.3g} exceeds 1e-9")
    preview_error: str | None = None
    try:
        from PIL import Image

        renderer = mujoco.Renderer(model, height=320, width=320)
        try:
            renderer.update_scene(data, camera="validation")
            Image.fromarray(renderer.render()).save(preview_path, format="PNG", optimize=True)
        finally:
            renderer.close()
    except Exception as exc:  # physics validity is independent from optional OpenGL preview availability
        preview_error = f"{type(exc).__name__}: {exc}"
    first.update(
        {
            "passed": not errors,
            "errors": errors,
            "deterministicRepeatMaxQposError": repeat_error,
            "previewGenerated": preview_path.is_file(),
            "previewError": preview_error,
            "thresholds": {
                "testSeconds": DROP_TEST_SECONDS,
                "settleWindowSeconds": DROP_SETTLE_WINDOW_SECONDS,
                "maxPenetrationM": MAX_DROP_PENETRATION_M,
                "maxSettlePositionSpanM": MAX_SETTLE_POSITION_SPAN_M,
                "maxSettleLinearSpeedP95Mps": MAX_SETTLE_LINEAR_P95_MPS,
                "maxSettleAngularSpeedP95RadS": MAX_SETTLE_ANGULAR_P95_RAD_S,
                "maxFinalLinearSpeedMps": MAX_FINAL_LINEAR_SPEED_MPS,
                "maxFinalAngularSpeedRadS": MAX_FINAL_ANGULAR_SPEED_RAD_S,
                "deterministicRepeatMaxQposError": 1e-9,
            },
        }
    )
    return first


def _promotion_blockers(payload: RigidAssetCompileRequest, provenance: dict[str, Any]) -> list[str]:
    blockers = ["deterministic_oracle_validation_pending"]
    if payload.source_identity_scope != "exact":
        blockers.append(f"source_identity_is_{payload.source_identity_scope}")
    bundle = provenance.get("evidenceBundle")
    if payload.source_identity_scope == "exact" and not bundle:
        blockers.append("exact_evidence_bundle_missing")
    if payload.dimension_confidence < DIMENSION_CONFIDENCE_GATE:
        blockers.append(f"dimension_confidence_below_{DIMENSION_CONFIDENCE_GATE:.2f}")
    if payload.mass_confidence < MASS_CONFIDENCE_GATE:
        blockers.append(f"mass_confidence_below_{MASS_CONFIDENCE_GATE:.2f}")
    redistribution = str(payload.license_metadata.get("redistribution") or "unknown").strip().lower()
    if redistribution not in {"allowed", "permitted", "cc0", "cc-by", "cc-by-sa", "public_domain"}:
        blockers.append(f"redistribution_{redistribution or 'unknown'}")
    if not payload.license_metadata.get("source"):
        blockers.append("geometry_license_source_missing")
    return blockers


def _compile_core(
    payload: RigidAssetCompileRequest,
    *,
    source: Path,
    source_sha256: str,
    source_size: int,
    root: Path,
    asset_id: str,
    version_id: str,
    version: int,
    actor: str,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    for name in ("evidence", "source", "generated", "visual", "collision", "openusd", "runtime/mujoco", "validation", "previews"):
        (root / name).mkdir(parents=True, exist_ok=True)
    source_copy = root / "source" / "source.glb"
    shutil.copyfile(source, source_copy)
    if _sha256_file(source_copy) != source_sha256:
        raise AssetCompileError("SOURCE_VALIDATION: immutable source copy hash mismatch.")

    with span("asset.compile", asset_id=asset_id, version_id=version_id, category=payload.category):
        mesh, static_report, uniform_scale, translation, actual_xyz = _prepare_mesh(source_copy, payload)
        visual_obj = root / "visual" / "model.obj"
        mesh.export(visual_obj, file_type="obj", include_normals=True, include_color=True)
        hull, center, inertia, full_inertia, eigenvalues, eigenvectors, collision_report = _collision_and_mass(mesh, payload.mass_kg)
        collision_obj = root / "collision" / "convex_hull.obj"
        hull.export(collision_obj, file_type="obj", include_normals=True)

        visual_usd = root / "openusd" / "visual.usdc"
        visual_usd_path, visual_usd_counts = usda.write_visual_usdc(
            source_copy,
            visual_usd,
            uniform_scale=uniform_scale,
            translation_m=translation,
        )
        rigid_usd = root / "openusd" / "asset.usdc"
        usd_report = _write_rigid_usd(
            visual_usd_path,
            hull,
            rigid_usd,
            payload=payload,
            center=center,
            eigenvalues=eigenvalues,
            eigenvectors=eigenvectors,
        )
        usd_report["visualVertices"] = visual_usd_counts["vertices"]
        usd_report["visualTriangles"] = visual_usd_counts["faces"]

        runtime_xml = root / "runtime" / "mujoco" / "asset.xml"
        validation_xml = root / "validation" / "drop_test.xml"
        _write_mjcf(
            runtime_xml,
            validation_xml,
            center=center,
            full_inertia=full_inertia,
            payload=payload,
            actual_height=actual_xyz[2],
        )
        import mujoco

        parsed = mujoco.MjModel.from_xml_path(str(runtime_xml))
        runtime_report = {
            "passed": True,
            "backend": "mujoco",
            "modelBodies": int(parsed.nbody),
            "modelGeoms": int(parsed.ngeom),
            "modelMeshes": int(parsed.nmesh),
            "visualCollisionSeparated": True,
            "visualContype": 0,
            "collisionHullMaxRuntimeVertices": 256,
        }
        preview_path = root / "previews" / "drop_settled.png"
        physics_report = _simulate_drop(validation_xml, preview_path)

    visual_artifacts = [_artifact(visual_obj, "runtime_visual_mesh", "model/obj")]
    collision_artifacts = [_artifact(collision_obj, "convex_collision_mesh", "model/obj")]
    openusd_paths = [visual_usd, rigid_usd]
    texture = visual_usd.with_name("basecolor.png")
    if texture.is_file():
        openusd_paths.append(texture)
    openusd_artifacts = [
        _artifact(path, "openusd_texture" if path.suffix.lower() == ".png" else "openusd_layer", "image/png" if path.suffix.lower() == ".png" else "model/vnd.usd")
        for path in openusd_paths
    ]
    runtime_artifacts = [_artifact(runtime_xml, "mujoco_runtime", "application/xml")]
    validation_artifacts = [_artifact(validation_xml, "mujoco_drop_test", "application/xml")]
    if preview_path.is_file():
        validation_artifacts.append(_artifact(preview_path, "drop_settle_preview", "image/png"))
    source_artifact = _artifact(source_copy, "immutable_source_glb", "model/gltf-binary")

    errors = list(physics_report["errors"])
    lifecycle = "PHYSICS_VALIDATED" if not errors else "REJECTED"
    blockers = _promotion_blockers(payload, provenance)
    if errors:
        blockers.insert(0, "physics_validation_failed")
    report = {
        "schemaVersion": "robotworld.rigid-asset-validation-report.v1",
        "assetId": asset_id,
        "versionId": version_id,
        "source": {"sha256": source_sha256, "sizeBytes": source_size, "artifactRef": source_artifact.artifact_ref},
        "staticValidation": static_report,
        "collision": collision_report,
        "massProperties": {
            "massKg": payload.mass_kg,
            "method": payload.mass_method,
            "confidence": payload.mass_confidence,
            "centerOfMassM": [float(item) for item in center],
            "inertiaKgM2": [float(item) for item in full_inertia],
            "inertiaMatrixKgM2": inertia.tolist(),
            "principalInertiaKgM2": [float(item) for item in eigenvalues],
        },
        "openusd": usd_report,
        "runtime": runtime_report,
        "physicsValidation": physics_report,
        "promotionBlockers": blockers,
        "createdAt": _now().isoformat(),
    }
    report_path = root / "validation" / "report.json"
    _write_json(report_path, report)
    validation_artifacts.insert(0, _artifact(report_path, "validation_report", "application/json"))

    manifest_value = {
        "assetId": asset_id,
        "versionId": version_id,
        "version": version,
        "displayName": payload.display_name,
        "category": payload.category,
        "lifecycleState": lifecycle,
        "sourceVisual": source_artifact.model_dump(mode="json", by_alias=True),
        "visualArtifacts": [item.model_dump(mode="json", by_alias=True) for item in visual_artifacts],
        "collisionArtifacts": [item.model_dump(mode="json", by_alias=True) for item in collision_artifacts],
        "openusdArtifacts": [item.model_dump(mode="json", by_alias=True) for item in openusd_artifacts],
        "runtimeArtifacts": [item.model_dump(mode="json", by_alias=True) for item in runtime_artifacts],
        "validationArtifacts": [item.model_dump(mode="json", by_alias=True) for item in validation_artifacts],
        "coordinateConvention": static_report["coordinateTransform"] | {"dimensionsOrder": "width,height,depth"},
        "dimensionsM": (actual_xyz[0], actual_xyz[2], actual_xyz[1]),
        "uniformScale": uniform_scale,
        "massKg": payload.mass_kg,
        "centerOfMassM": tuple(float(item) for item in center),
        "inertiaKgM2": full_inertia,
        "material": {
            "frictionRange": list(payload.friction_range),
            "restitutionRange": list(payload.restitution_range),
            "source": "evidence_or_explicit_prior",
            "mujocoRestitutionMapping": "contact impedance; requested range retained for domain variants",
        },
        "semantics": payload.semantics,
        "affordances": payload.affordances,
        "evidenceBundleId": payload.evidence_bundle_id,
        "provenance": provenance
        | {
            "sourceAssetId": payload.source_asset_id,
            "sourceSha256": source_sha256,
            "dimensionMethod": payload.dimension_method,
            "dimensionConfidence": payload.dimension_confidence,
            "massMethod": payload.mass_method,
            "massConfidence": payload.mass_confidence,
            "license": payload.license_metadata,
            "compiler": "robotworld.rigid_asset_compiler.v1",
        },
        "validationErrors": errors,
        "promotionEligible": False,
        "promotionBlockers": blockers,
        "manifestSha256": "0" * 64,
        "createdBy": actor,
        "createdAt": _now(),
    }
    digest_value = dict(manifest_value)
    digest_value.pop("manifestSha256")
    manifest_value["manifestSha256"] = _sha256_bytes(_canonical_bytes(command_store.json_safe(digest_value)))
    manifest = AssetManifest.model_validate(manifest_value)
    manifest_wire = manifest.model_dump(mode="json", by_alias=True)
    manifest_path = root / "manifest.json"
    _write_json(manifest_path, manifest_wire)
    return {
        "accepted": not errors,
        "lifecycleState": lifecycle,
        "manifest": manifest_wire,
        "manifestSha256": manifest.manifest_sha256,
        "report": report,
        "errors": errors,
        "promotionBlockers": blockers,
    }


async def _audit_transition(
    session,
    *,
    command_id: str,
    entity_id: str,
    action: str,
    from_state: str | None,
    to_state: str,
    actor: str,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            command_id=command_id,
            entity_type="asset_version",
            entity_id=entity_id,
            action=action,
            from_state=from_state,
            to_state=to_state,
            detail=detail or {},
            actor=actor,
        )
    )


async def compile_rigid(
    payload: RigidAssetCompileRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    wire = payload.model_dump(mode="json", by_alias=True)
    command, reused = await command_store.start_command(
        kind="asset.rigid.compile",
        target_type="asset_version",
        target_id=payload.source_asset_id,
        payload=wire,
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        return command_store.command_view(command, reused=True)
    try:
        source, source_sha256, source_size = await asyncio.to_thread(resolve_source_glb, payload.source_glb_path)
        if payload.expected_source_sha256 and payload.expected_source_sha256 != source_sha256:
            raise ValueError(
                f"Source SHA-256 mismatch: expected {payload.expected_source_sha256}, resolved {source_sha256}."
            )
        provenance = await _evidence_provenance(payload)
    except Exception as exc:
        await command_store.finish_command(command.id, error=str(exc))
        raise

    asset_id = payload.source_asset_id or new_id("asset")
    async with SessionLocal() as session:
        maximum = (
            await session.execute(
                select(func.max(CompiledAssetVersionRecord.version)).where(CompiledAssetVersionRecord.asset_id == asset_id)
            )
        ).scalar_one_or_none()
        version = int(maximum or 0) + 1
    version_id = new_id("assetver")
    root = (ASSETS_DIR / asset_id / f"v{version:04d}").resolve()
    if not root.is_relative_to(ASSETS_DIR.resolve()):
        await command_store.finish_command(command.id, error="Compiled asset root escaped the artifact store.")
        raise ValueError("Compiled asset root escaped the artifact store.")
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        await command_store.finish_command(command.id, error="Asset version artifact directory already exists.")
        raise AssetCompileError("Asset version artifact directory already exists.") from exc

    row = CompiledAssetVersionRecord(
        id=version_id,
        asset_id=asset_id,
        version=version,
        display_name=payload.display_name,
        category=payload.category,
        asset_kind="rigid",
        lifecycle_state="IMPORTED",
        evidence_bundle_id=payload.evidence_bundle_id,
        source_path=str(source),
        source_sha256=source_sha256,
        artifact_root=root.relative_to(DATA_DIR).as_posix(),
        command_id=command.id,
        created_by=actor,
    )
    async with SessionLocal() as session:
        session.add(row)
        await _audit_transition(
            session,
            command_id=command.id,
            entity_id=version_id,
            action="asset.import",
            from_state=None,
            to_state="IMPORTED",
            actor=actor,
            detail={"sourceSha256": source_sha256, "sourceSizeBytes": source_size, "version": version},
        )
        await session.commit()

    outcome: dict[str, Any]
    unexpected_error: str | None = None
    try:
        outcome = await asyncio.to_thread(
            _compile_core,
            payload,
            source=source,
            source_sha256=source_sha256,
            source_size=source_size,
            root=root,
            asset_id=asset_id,
            version_id=version_id,
            version=version,
            actor=actor,
            provenance=provenance,
        )
    except Exception as exc:
        unexpected_error = str(exc)
        try:
            parsed = json.loads(unexpected_error)
            errors = list(parsed.get("errors") or [unexpected_error]) if isinstance(parsed, dict) else [unexpected_error]
            report = parsed if isinstance(parsed, dict) else {"stage": "COMPILATION", "passed": False, "errors": errors}
        except json.JSONDecodeError:
            errors = [unexpected_error]
            report = {"stage": "COMPILATION", "passed": False, "errors": errors}
        report.update({"schemaVersion": "robotworld.rigid-asset-validation-report.v1", "assetId": asset_id, "versionId": version_id})
        _write_json(root / "validation" / "report.json", report)
        outcome = {
            "accepted": False,
            "lifecycleState": "REJECTED",
            "manifest": {},
            "manifestSha256": None,
            "report": report,
            "errors": errors,
            "promotionBlockers": ["asset_compilation_or_validation_failed"],
        }

    async with SessionLocal() as session:
        persisted = await session.get(CompiledAssetVersionRecord, version_id)
        assert persisted is not None
        persisted.manifest = outcome["manifest"]
        persisted.manifest_sha256 = outcome["manifestSha256"]
        persisted.validation_report = outcome["report"]
        persisted.validation_errors = outcome["errors"]
        persisted.promotion_eligible = False
        persisted.promotion_blockers = outcome["promotionBlockers"]
        persisted.updated_at = _now()
        previous = persisted.lifecycle_state
        if outcome["accepted"]:
            for target, action in (
                ("COMPILED", "asset.compile"),
                ("STATIC_VALIDATED", "asset.static_validate"),
                ("PHYSICS_VALIDATED", "asset.physics_validate"),
            ):
                await _audit_transition(
                    session,
                    command_id=command.id,
                    entity_id=version_id,
                    action=action,
                    from_state=previous,
                    to_state=target,
                    actor=actor,
                )
                previous = target
            persisted.lifecycle_state = "PHYSICS_VALIDATED"
        else:
            persisted.lifecycle_state = "REJECTED"
            await _audit_transition(
                session,
                command_id=command.id,
                entity_id=version_id,
                action="asset.reject",
                from_state=previous,
                to_state="REJECTED",
                actor=actor,
                detail={"errors": outcome["errors"]},
            )
        await session.commit()
        result = {"assetVersion": asset_version_view(persisted)}

    emit_metric("robotworld.asset.compile", 1, category=payload.category, status=outcome["lifecycleState"])
    if outcome["accepted"]:
        await command_store.finish_command(command.id, output=result)
        command.output = command_store.json_safe(result)
        command.status = "SUCCEEDED"
        return command_store.command_view(command)
    message = unexpected_error or "; ".join(outcome["errors"]) or "Asset candidate was rejected."
    await command_store.finish_command(command.id, output=result, error=message)
    command.output = command_store.json_safe(result)
    command.error = message
    command.status = "FAILED"
    return command_store.command_view(command)
