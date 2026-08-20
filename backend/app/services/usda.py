"""OpenUSD/SimReady authoring for articulated assets.

The generated layer follows the portable USDPhysics schema: Z-up, metre and
kilogram units, non-nested rigid bodies, GPrim collision APIs, physical
material binding, and schema-correct revolute-joint attributes.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


def _identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"Asset_{cleaned}"
    return cleaned


def build_usda(spec: dict[str, Any], asset_name: str, *, visual_layer: str | None = None) -> str:
    root = _identifier(asset_name)
    w = max(float(spec.get("width_m", 0.7)), 0.05)
    h = max(float(spec.get("height_m", 1.7)), 0.05)
    d = max(float(spec.get("depth_m", 0.65)), 0.05)
    mass = max(float(spec.get("mass_kg", 55.0)), 0.01)
    door_mass = min(max(float(spec.get("door_mass_kg", 12.0)), 0.01), mass * 0.9)
    door_w = min(max(float(spec.get("door_width_m", w * 0.5)), 0.03), w)
    max_open = min(max(float(spec.get("max_open_deg", 110.0)), 1.0), 179.0)
    hinge_side = str(spec.get("hinge_side", "left")).lower()
    hinge_x = -w / 2 if hinge_side == "left" else w / 2
    direction = 1.0 if hinge_side == "left" else -1.0
    handle_h = min(max(float(spec.get("handle_height_m", 1.05)), 0.05), h)
    # Published coefficients should populate these fields when available;
    # conservative material priors remain explicitly tagged in spec.json.
    static_friction = min(max(float(spec.get("static_friction", 0.45)), 0.0), 2.0)
    dynamic_friction = min(max(float(spec.get("dynamic_friction", 0.35)), 0.0), 2.0)
    visual_block = ""
    if visual_layer:
        visual_block = f'''    def Xform "Visual" (
        prepend references = @{visual_layer}@</Visual>
    )
    {{
    }}

'''

    return f'''#usda 1.0
(
    defaultPrim = "{root}"
    metersPerUnit = 1
    kilogramsPerUnit = 1
    upAxis = "Z"
    doc = "RobotWorld physical asset compiled from provenance-tagged specifications"
)

def Xform "{root}"
{{
{visual_block}    def Material "PhysicsMaterial" (
        prepend apiSchemas = ["PhysicsMaterialAPI"]
    )
    {{
        float physics:staticFriction = {static_friction:.4f}
        float physics:dynamicFriction = {dynamic_friction:.4f}
        float physics:restitution = 0.02
    }}

    def Xform "Body" (
        prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]
    )
    {{
        bool physics:kinematicEnabled = true
        float physics:mass = {mass - door_mass:.6f}
        point3f physics:centerOfMass = (0, 0, {h / 2:.6f})

        def Cube "Collision" (
            prepend apiSchemas = ["PhysicsCollisionAPI"]
        )
        {{
            double size = 1
            float3 xformOp:scale = ({w:.6f}, {d:.6f}, {h:.6f})
            double3 xformOp:translate = (0, 0, {h / 2:.6f})
            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
            rel material:binding:physics = </{root}/PhysicsMaterial>
        }}
    }}

    def Xform "Door" (
        prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]
    )
    {{
        float physics:mass = {door_mass:.6f}
        point3f physics:centerOfMass = ({direction * door_w / 2:.6f}, 0, {h * 0.62:.6f})
        double3 xformOp:translate = ({hinge_x:.6f}, {d / 2 + 0.0225:.6f}, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]

        def Cube "PanelCollision" (
            prepend apiSchemas = ["PhysicsCollisionAPI"]
        )
        {{
            double size = 1
            float3 xformOp:scale = ({door_w:.6f}, 0.045, {h * 0.68:.6f})
            double3 xformOp:translate = ({direction * door_w / 2:.6f}, 0, {h * 0.62:.6f})
            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
            rel material:binding:physics = </{root}/PhysicsMaterial>
        }}

        def Capsule "HandleCollision" (
            prepend apiSchemas = ["PhysicsCollisionAPI"]
        )
        {{
            uniform token axis = "Z"
            double height = 0.19
            double radius = 0.014
            double3 xformOp:translate = ({direction * (door_w - 0.06):.6f}, -0.0675, {handle_h:.6f})
            uniform token[] xformOpOrder = ["xformOp:translate"]
            rel material:binding:physics = </{root}/PhysicsMaterial>
        }}
    }}

    def PhysicsRevoluteJoint "DoorHinge"
    {{
        rel physics:body0 = </{root}/Body>
        rel physics:body1 = </{root}/Door>
        uniform token physics:axis = "Z"
        float physics:lowerLimit = 0
        float physics:upperLimit = {max_open:.4f}
        point3f physics:localPos0 = ({hinge_x:.6f}, {d / 2 + 0.0225:.6f}, 0)
        point3f physics:localPos1 = (0, 0, 0)
        quatf physics:localRot0 = (1, 0, 0, 0)
        quatf physics:localRot1 = (1, 0, 0, 0)
    }}
}}
'''


def _validate(path: Path, root: str, *, expect_visual: bool = False, expect_physics: bool = True) -> None:
    try:
        from pxr import Usd, UsdGeom, UsdPhysics  # type: ignore
    except ImportError as exc:  # a production compile must not silently skip validation
        raise RuntimeError("OpenUSD validation unavailable: install usd-core") from exc

    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise RuntimeError("OpenUSD could not open the generated layer")
    if UsdGeom.GetStageUpAxis(stage) != UsdGeom.Tokens.z:
        raise RuntimeError("SimReady validation failed: stage must be Z-up")
    if abs(float(UsdGeom.GetStageMetersPerUnit(stage)) - 1.0) > 1e-9:
        raise RuntimeError("SimReady validation failed: stage must use metres")
    if expect_physics:
        joint = UsdPhysics.RevoluteJoint.Get(stage, f"/{root}/DoorHinge")
        if not joint or not joint.GetPrim().IsValid():
            raise RuntimeError("SimReady validation failed: revolute joint is missing")
        if joint.GetAxisAttr().Get() != "Z":
            raise RuntimeError("SimReady validation failed: hinge axis is invalid")
        for prim_path in (f"/{root}/Body/Collision", f"/{root}/Door/PanelCollision", f"/{root}/Door/HandleCollision"):
            prim = stage.GetPrimAtPath(prim_path)
            if not prim or not prim.HasAPI(UsdPhysics.CollisionAPI):
                raise RuntimeError(f"SimReady validation failed: collider missing at {prim_path}")
    if expect_visual:
        visual = stage.GetPrimAtPath(f"/{root}/Visual/Mesh")
        if not visual or not visual.IsA(UsdGeom.Mesh):
            raise RuntimeError("OpenUSD validation failed: generated GLB visual mesh is not composed into the asset layer")


def write_visual_usdc(glb_path: Path, out_path: Path) -> tuple[Path, dict[str, int]]:
    """Convert a verified GLB mesh into an actual OpenUSD visual layer.

    The physics layer remains separate: this layer contains the generated
    TRELLIS geometry itself, not an inferred box/capsule proxy.
    """
    try:
        import numpy as np
        import trimesh
        from pxr import Sdf, Usd, UsdGeom, UsdShade  # type: ignore
    except ImportError as exc:
        raise RuntimeError("OpenUSD visual conversion unavailable: install usd-core, numpy, and trimesh") from exc

    if not glb_path.is_file():
        raise RuntimeError(f"GLB visual source is missing: {glb_path}")
    loaded = trimesh.load(glb_path, force="scene")
    mesh = loaded.dump(concatenate=True) if isinstance(loaded, trimesh.Scene) else loaded
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.vertices) == 0 or len(mesh.faces) == 0:
        raise RuntimeError("GLB did not contain a usable mesh for OpenUSD conversion")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(out_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    visual = UsdGeom.Xform.Define(stage, "/Visual")
    visual.GetPrim().SetCustomDataByKey("robotworld:sourceGLB", Sdf.AssetPath(glb_path.name))
    usd_mesh = UsdGeom.Mesh.Define(stage, "/Visual/Mesh")
    # glTF is Y-up while RobotWorld/OpenUSD stages are Z-up. Rotate +90°
    # around X so the authored up-axis metadata matches the actual geometry.
    gltf_points = np.asarray(mesh.vertices, dtype=float)
    usd_points = np.stack((gltf_points[:, 0], -gltf_points[:, 2], gltf_points[:, 1]), axis=1)
    points = [tuple(map(float, row)) for row in usd_points]
    faces = np.asarray(mesh.faces, dtype=np.int32)
    usd_mesh.CreatePointsAttr(points)
    usd_mesh.CreateFaceVertexCountsAttr([3] * len(faces))
    usd_mesh.CreateFaceVertexIndicesAttr(faces.reshape(-1).tolist())
    gltf_normals = np.asarray(mesh.vertex_normals, dtype=float)
    normals = np.stack((gltf_normals[:, 0], -gltf_normals[:, 2], gltf_normals[:, 1]), axis=1)
    if len(normals) == len(points):
        usd_mesh.CreateNormalsAttr([tuple(map(float, row)) for row in normals])
        usd_mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)

    # Preserve a physically based material when the GLB exposes one.  The
    # authoritative textured delivery remains model.glb; this USD layer keeps
    # the real generated topology and a compatible material description.
    material = UsdShade.Material.Define(stage, "/Visual/GeneratedMaterial")
    shader = UsdShade.Shader.Define(stage, "/Visual/GeneratedMaterial/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    base = (0.55, 0.55, 0.55)
    metallic, roughness = 0.0, 0.65
    pbr = getattr(getattr(mesh, "visual", None), "material", None)
    if pbr is not None:
        factor = getattr(pbr, "baseColorFactor", None)
        if factor is not None and len(factor) >= 3:
            base = tuple(float(v) / 255.0 for v in factor[:3])
        metallic = float(getattr(pbr, "metallicFactor", metallic) or metallic)
        roughness = float(getattr(pbr, "roughnessFactor", roughness) or roughness)
    diffuse = shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f)
    diffuse.Set(base)
    uv = getattr(getattr(mesh, "visual", None), "uv", None)
    texture_image = getattr(pbr, "baseColorTexture", None) if pbr is not None else None
    if uv is not None and len(uv) == len(points) and texture_image is not None:
        texture_path = out_path.with_name("basecolor.png")
        texture_image.convert("RGBA").save(texture_path, format="PNG", optimize=True)
        # glTF texture V origin is opposite USD's conventional image origin.
        uv_values = [(float(row[0]), 1.0 - float(row[1])) for row in np.asarray(uv)]
        primvar = UsdGeom.PrimvarsAPI(usd_mesh).CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex
        )
        primvar.Set(uv_values)
        reader = UsdShade.Shader.Define(stage, "/Visual/GeneratedMaterial/PrimvarReader")
        reader.CreateIdAttr("UsdPrimvarReader_float2")
        reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
        reader.CreateOutput("result", Sdf.ValueTypeNames.Float2)
        texture = UsdShade.Shader.Define(stage, "/Visual/GeneratedMaterial/BaseColorTexture")
        texture.CreateIdAttr("UsdUVTexture")
        texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(texture_path.name))
        texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
        texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), "result")
        texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        diffuse.ConnectToSource(texture.ConnectableAPI(), "rgb")
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metallic)
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI(usd_mesh).Bind(material)
    stage.SetDefaultPrim(visual.GetPrim())
    stage.Save()

    reopened = Usd.Stage.Open(str(out_path))
    composed = UsdGeom.Mesh.Get(reopened, "/Visual/Mesh") if reopened else None
    if not composed or not composed.GetPointsAttr().Get() or not composed.GetFaceVertexIndicesAttr().Get():
        raise RuntimeError("OpenUSD visual conversion failed validation")
    return out_path, {"vertices": len(points), "faces": len(faces)}


def write_usda(spec: dict[str, Any], asset_name: str, out_path: Path, *, visual_layer: str | None = None) -> tuple[Path, bool]:
    """Write and validate a USDA layer. Invalid output fails the pipeline."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    root = _identifier(asset_name)
    out_path.write_text(build_usda(spec, root, visual_layer=visual_layer), encoding="utf8")
    _validate(out_path, root, expect_visual=visual_layer is not None)
    return out_path, True


def write_visual_asset_usda(asset_name: str, out_path: Path, *, visual_layer: str) -> tuple[Path, bool]:
    """Compose a generated visual into OpenUSD without inventing physics.

    Rigid image-to-3D assets are visual evidence until measured collision and
    task data exists. This layer deliberately contains no hinge, door, or
    proxy collider so it cannot be mistaken for a validated physical asset.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    root = _identifier(asset_name)
    out_path.write_text(
        f'''#usda 1.0
(
    defaultPrim = "{root}"
    metersPerUnit = 1
    upAxis = "Z"
)

def Xform "{root}"
{{
    custom string robotworld:physicalStatus = "visual_only_pending_measurement"
    def Xform "Visual" (
        prepend references = @{visual_layer}@</Visual>
    )
    {{
    }}
}}
''',
        encoding="utf8",
    )
    _validate(out_path, root, expect_visual=True, expect_physics=False)
    return out_path, True


def write_world_usda(asset_layer: Path, asset_name: str, out_path: Path) -> tuple[Path, bool]:
    """Author a composable OpenUSD placement layer for the generated asset."""
    try:
        from pxr import Usd, UsdGeom  # type: ignore
    except ImportError as exc:
        raise RuntimeError("OpenUSD world authoring unavailable: install usd-core") from exc
    root = _identifier(asset_name)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(out_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/RobotWorld")
    placed = UsdGeom.Xform.Define(stage, "/RobotWorld/GeneratedAsset")
    placed.GetPrim().GetReferences().AddReference(asset_layer.name, f"/{root}")
    stage.SetDefaultPrim(world.GetPrim())
    stage.Save()
    reopened = Usd.Stage.Open(str(out_path))
    prim = reopened.GetPrimAtPath("/RobotWorld/GeneratedAsset/Visual/Mesh") if reopened else None
    if not prim or not prim.IsA(UsdGeom.Mesh):
        raise RuntimeError("OpenUSD world validation failed: generated visual mesh reference is unresolved")
    return out_path, True


def write_world_assembly(
    placements: list[dict[str, Any]],
    out_path: Path,
) -> tuple[Path, int]:
    """Compose persisted generated assets into one inspectable OpenUSD stage.

    Each placement references the asset's authored ``asset.usda`` layer.  The
    layout transforms are editor placement data only; generated objects remain
    explicitly visual-only until measured scale/colliders are available.
    """
    try:
        from pxr import Sdf, Usd, UsdGeom  # type: ignore
    except ImportError as exc:
        raise RuntimeError("OpenUSD world authoring unavailable: install usd-core") from exc

    out_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(out_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/RobotWorld")
    assets = UsdGeom.Xform.Define(stage, "/RobotWorld/Assets")
    stage.SetDefaultPrim(world.GetPrim())

    authored: list[str] = []
    for index, item in enumerate(placements):
        layer = Path(item["asset_layer"]).resolve()
        if not layer.is_file():
            raise RuntimeError(f"OpenUSD asset layer is missing: {layer}")
        asset_id = str(item["asset_id"])
        prim_name = _identifier(f"{index:02d}_{asset_id}")
        placed = UsdGeom.Xform.Define(stage, f"/RobotWorld/Assets/{prim_name}")
        relative_layer = os.path.relpath(layer, out_path.parent).replace("\\", "/")
        # Omitting primPath intentionally resolves the referenced layer's
        # authored defaultPrim (currently ``SimReadyAsset``). This also keeps
        # composition valid if the compiler changes its internal root name.
        placed.GetPrim().GetReferences().AddReference(relative_layer)
        placed.GetPrim().CreateAttribute("robotworld:assetId", Sdf.ValueTypeNames.String).Set(asset_id)
        placed.GetPrim().CreateAttribute("robotworld:physicalStatus", Sdf.ValueTypeNames.String).Set(
            "visual_only_pending_measurement"
        )
        placed.GetPrim().CreateAttribute("robotworld:scaleSource", Sdf.ValueTypeNames.String).Set(
            str(item.get("scale_source", "inferred"))
        )
        placed.GetPrim().CreateAttribute("robotworld:dimensionSource", Sdf.ValueTypeNames.String).Set(
            str(item.get("dimension_source", "inferred"))
        )
        placed.GetPrim().CreateAttribute("robotworld:anchorMode", Sdf.ValueTypeNames.String).Set(
            str(item.get("anchor", {}).get("mode", "unanchored"))
        )
        translation = tuple(float(v) for v in item.get("translation", (index * 1.5, 0.0, 0.0)))
        scale = tuple(max(0.001, float(v)) for v in item.get("scale", (1.0, 1.0, 1.0)))
        xformable = UsdGeom.Xformable(placed)
        xformable.AddTranslateOp().Set(translation)
        xformable.AddScaleOp().Set(scale)
        authored.append(f"/RobotWorld/Assets/{prim_name}")

    stage.Save()
    reopened = Usd.Stage.Open(str(out_path))
    if not reopened or not reopened.GetPrimAtPath("/RobotWorld/Assets"):
        raise RuntimeError("OpenUSD world validation failed: assembly root is unresolved")
    for prim_path in authored:
        prim = reopened.GetPrimAtPath(prim_path)
        if not prim or not prim.GetChildren():
            raise RuntimeError(f"OpenUSD world validation failed: reference is unresolved at {prim_path}")
    return out_path, len(authored)
