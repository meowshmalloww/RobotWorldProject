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


def write_visual_usdc(
    glb_path: Path,
    out_path: Path,
    *,
    uniform_scale: float = 1.0,
    translation_m: tuple[float, float, float] = (0.0, 0.0, 0.0),
    appearance_variants: list[dict[str, Any]] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Convert a verified GLB mesh into an actual OpenUSD visual layer.

    The physics layer remains separate: this layer contains the generated
    TRELLIS geometry itself, not an inferred box/capsule proxy. Optional
    appearances must have identical vertices, faces, and UVs; otherwise they
    are different geometry and must become distinct immutable asset versions.
    """
    try:
        import numpy as np
        import trimesh
        from pxr import Sdf, Usd, UsdGeom, UsdShade, Vt  # type: ignore
    except ImportError as exc:
        raise RuntimeError("OpenUSD visual conversion unavailable: install usd-core, numpy, and trimesh") from exc

    if not glb_path.is_file():
        raise RuntimeError(f"GLB visual source is missing: {glb_path}")
    if not 0 < uniform_scale < 1_000_000:
        raise RuntimeError("OpenUSD visual scale must be finite and positive")
    loaded = trimesh.load(glb_path, force="scene", process=False)
    mesh = loaded.to_geometry() if isinstance(loaded, trimesh.Scene) else loaded
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
    usd_points = usd_points * float(uniform_scale) + np.asarray(translation_m, dtype=float)
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
            divisor = 255.0 if max(float(v) for v in factor[:3]) > 1.0 else 1.0
            base = tuple(float(v) / divisor for v in factor[:3])
        metallic = float(getattr(pbr, "metallicFactor", metallic) or metallic)
        roughness = float(getattr(pbr, "roughnessFactor", roughness) or roughness)
    diffuse = shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f)
    diffuse.Set(base)
    uv = getattr(getattr(mesh, "visual", None), "uv", None)
    texture_artifacts: list[dict[str, str]] = []
    has_uv = uv is not None and len(uv) == len(points)
    reader = None
    if has_uv:
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

    texture_image = getattr(pbr, "baseColorTexture", None) if pbr is not None else None
    if reader is not None and texture_image is not None:
        texture_path = out_path.with_name("basecolor.png")
        texture_image.convert("RGBA").save(texture_path, format="PNG", optimize=True)
        texture_artifacts.append({"file": texture_path.name, "role": "base_color", "colorSpace": "sRGB"})
        texture = UsdShade.Shader.Define(stage, "/Visual/GeneratedMaterial/BaseColorTexture")
        texture.CreateIdAttr("UsdUVTexture")
        texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(texture_path.name))
        texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
        texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), "result")
        texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        texture.CreateOutput("a", Sdf.ValueTypeNames.Float)
        diffuse.ConnectToSource(texture.ConnectableAPI(), "rgb")
        shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(texture.ConnectableAPI(), "a")

    metallic_input = shader.CreateInput("metallic", Sdf.ValueTypeNames.Float)
    metallic_input.Set(metallic)
    roughness_input = shader.CreateInput("roughness", Sdf.ValueTypeNames.Float)
    roughness_input.Set(roughness)
    metallic_roughness_image = getattr(pbr, "metallicRoughnessTexture", None) if pbr is not None else None
    if reader is not None and metallic_roughness_image is not None:
        metallic_roughness_path = out_path.with_name("metallic_roughness.png")
        metallic_roughness_image.convert("RGB").save(metallic_roughness_path, format="PNG", optimize=True)
        texture_artifacts.append(
            {"file": metallic_roughness_path.name, "role": "metallic_roughness", "colorSpace": "raw"}
        )
        texture = UsdShade.Shader.Define(stage, "/Visual/GeneratedMaterial/MetallicRoughnessTexture")
        texture.CreateIdAttr("UsdUVTexture")
        texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(metallic_roughness_path.name))
        texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")
        texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), "result")
        texture.CreateOutput("g", Sdf.ValueTypeNames.Float)
        texture.CreateOutput("b", Sdf.ValueTypeNames.Float)
        # glTF packs perceptual roughness in G and metallic in B.
        roughness_input.ConnectToSource(texture.ConnectableAPI(), "g")
        metallic_input.ConnectToSource(texture.ConnectableAPI(), "b")

    normal_image = getattr(pbr, "normalTexture", None) if pbr is not None else None
    if reader is not None and normal_image is not None:
        normal_path = out_path.with_name("normal.png")
        normal_image.convert("RGB").save(normal_path, format="PNG", optimize=True)
        texture_artifacts.append({"file": normal_path.name, "role": "normal", "colorSpace": "raw"})
        texture = UsdShade.Shader.Define(stage, "/Visual/GeneratedMaterial/NormalTexture")
        texture.CreateIdAttr("UsdUVTexture")
        texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(normal_path.name))
        texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")
        texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), "result")
        texture.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set((2.0, 2.0, 2.0, 1.0))
        texture.CreateInput("bias", Sdf.ValueTypeNames.Float4).Set((-1.0, -1.0, -1.0, 0.0))
        texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        shader.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(texture.ConnectableAPI(), "rgb")

    emissive_image = getattr(pbr, "emissiveTexture", None) if pbr is not None else None
    emissive_input = shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f)
    emissive_factor = getattr(pbr, "emissiveFactor", None) if pbr is not None else None
    if emissive_factor is not None and len(emissive_factor) >= 3:
        divisor = 255.0 if max(float(v) for v in emissive_factor[:3]) > 1.0 else 1.0
        emissive_input.Set(tuple(float(v) / divisor for v in emissive_factor[:3]))
    else:
        emissive_input.Set((0.0, 0.0, 0.0))
    if reader is not None and emissive_image is not None:
        emissive_path = out_path.with_name("emissive.png")
        emissive_image.convert("RGB").save(emissive_path, format="PNG", optimize=True)
        texture_artifacts.append({"file": emissive_path.name, "role": "emissive", "colorSpace": "sRGB"})
        texture = UsdShade.Shader.Define(stage, "/Visual/GeneratedMaterial/EmissiveTexture")
        texture.CreateIdAttr("UsdUVTexture")
        texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(emissive_path.name))
        texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
        texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), "result")
        texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        emissive_input.ConnectToSource(texture.ConnectableAPI(), "rgb")
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    binding_api = UsdShade.MaterialBindingAPI.Apply(usd_mesh.GetPrim())

    appearance_report: list[dict[str, Any]] = [
        {
            "id": "generated",
            "displayName": "Generated source",
            "materialPath": str(material.GetPath()),
            "textures": list(texture_artifacts),
        }
    ]
    appearance_bindings: list[tuple[str, Any]] = [("generated", material)]
    base_faces = np.asarray(mesh.faces, dtype=np.int64)
    base_vertices = np.asarray(mesh.vertices, dtype=np.float64)
    base_uv = np.asarray(uv, dtype=np.float64) if has_uv else None

    for definition in appearance_variants or []:
        variant_id = str(definition["id"])
        display_name = str(definition.get("displayName") or variant_id)
        variant_path = Path(str(definition["sourcePath"]))
        if not variant_path.is_file():
            raise RuntimeError(f"Appearance source is missing: {variant_path}")
        alternate_loaded = trimesh.load(variant_path, force="scene", process=False)
        alternate_mesh = (
            alternate_loaded.to_geometry() if isinstance(alternate_loaded, trimesh.Scene) else alternate_loaded
        )
        if not isinstance(alternate_mesh, trimesh.Trimesh):
            raise RuntimeError(f"Appearance '{variant_id}' did not contain one usable mesh")
        alternate_faces = np.asarray(alternate_mesh.faces, dtype=np.int64)
        alternate_vertices = np.asarray(alternate_mesh.vertices, dtype=np.float64)
        alternate_uv_value = getattr(getattr(alternate_mesh, "visual", None), "uv", None)
        alternate_uv = np.asarray(alternate_uv_value, dtype=np.float64) if alternate_uv_value is not None else None
        topology_matches = (
            alternate_faces.shape == base_faces.shape
            and np.array_equal(alternate_faces, base_faces)
            and alternate_vertices.shape == base_vertices.shape
            and np.allclose(alternate_vertices, base_vertices, rtol=0.0, atol=1e-8)
        )
        uv_matches = (base_uv is None and alternate_uv is None) or (
            base_uv is not None
            and alternate_uv is not None
            and base_uv.shape == alternate_uv.shape
            and np.allclose(base_uv, alternate_uv, rtol=0.0, atol=1e-8)
        )
        if not topology_matches or not uv_matches:
            raise RuntimeError(
                f"Appearance '{variant_id}' changes geometry/topology/UVs; compile it as a new asset version"
            )

        material_name = _identifier(variant_id)
        material_path = f"/Visual/AppearanceMaterials/{material_name}"
        alternate_material = UsdShade.Material.Define(stage, material_path)
        alternate_shader = UsdShade.Shader.Define(stage, f"{material_path}/PreviewSurface")
        alternate_shader.CreateIdAttr("UsdPreviewSurface")
        alternate_pbr = getattr(getattr(alternate_mesh, "visual", None), "material", None)
        alternate_base = (0.55, 0.55, 0.55)
        alternate_metallic, alternate_roughness = 0.0, 0.65
        if alternate_pbr is not None:
            factor = getattr(alternate_pbr, "baseColorFactor", None)
            if factor is not None and len(factor) >= 3:
                divisor = 255.0 if max(float(value) for value in factor[:3]) > 1.0 else 1.0
                alternate_base = tuple(float(value) / divisor for value in factor[:3])
            alternate_metallic = float(
                getattr(alternate_pbr, "metallicFactor", alternate_metallic) or alternate_metallic
            )
            alternate_roughness = float(
                getattr(alternate_pbr, "roughnessFactor", alternate_roughness) or alternate_roughness
            )
        alternate_diffuse = alternate_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f)
        alternate_diffuse.Set(alternate_base)
        alternate_metallic_input = alternate_shader.CreateInput("metallic", Sdf.ValueTypeNames.Float)
        alternate_metallic_input.Set(alternate_metallic)
        alternate_roughness_input = alternate_shader.CreateInput("roughness", Sdf.ValueTypeNames.Float)
        alternate_roughness_input.Set(alternate_roughness)
        variant_textures: list[dict[str, str]] = []

        def _variant_texture(
            image: Any,
            *,
            role: str,
            color_space: str,
            mode: str,
            node_name: str,
        ) -> Any | None:
            if reader is None or image is None:
                return None
            filename = f"{material_name}_{role}.png"
            image.convert(mode).save(out_path.with_name(filename), format="PNG", optimize=True)
            metadata = {
                "file": filename,
                "role": role,
                "colorSpace": color_space,
                "appearanceId": variant_id,
            }
            variant_textures.append(metadata)
            texture_artifacts.append(metadata)
            texture_shader = UsdShade.Shader.Define(stage, f"{material_path}/{node_name}")
            texture_shader.CreateIdAttr("UsdUVTexture")
            texture_shader.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(filename))
            texture_shader.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set(color_space)
            texture_shader.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
                reader.ConnectableAPI(), "result"
            )
            return texture_shader

        alternate_base_texture = _variant_texture(
            getattr(alternate_pbr, "baseColorTexture", None) if alternate_pbr is not None else None,
            role="base_color",
            color_space="sRGB",
            mode="RGBA",
            node_name="BaseColorTexture",
        )
        if alternate_base_texture is not None:
            alternate_base_texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            alternate_base_texture.CreateOutput("a", Sdf.ValueTypeNames.Float)
            alternate_diffuse.ConnectToSource(alternate_base_texture.ConnectableAPI(), "rgb")
            alternate_shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(
                alternate_base_texture.ConnectableAPI(), "a"
            )

        alternate_mr_texture = _variant_texture(
            getattr(alternate_pbr, "metallicRoughnessTexture", None) if alternate_pbr is not None else None,
            role="metallic_roughness",
            color_space="raw",
            mode="RGB",
            node_name="MetallicRoughnessTexture",
        )
        if alternate_mr_texture is not None:
            alternate_mr_texture.CreateOutput("g", Sdf.ValueTypeNames.Float)
            alternate_mr_texture.CreateOutput("b", Sdf.ValueTypeNames.Float)
            alternate_roughness_input.ConnectToSource(alternate_mr_texture.ConnectableAPI(), "g")
            alternate_metallic_input.ConnectToSource(alternate_mr_texture.ConnectableAPI(), "b")

        alternate_normal_texture = _variant_texture(
            getattr(alternate_pbr, "normalTexture", None) if alternate_pbr is not None else None,
            role="normal",
            color_space="raw",
            mode="RGB",
            node_name="NormalTexture",
        )
        if alternate_normal_texture is not None:
            alternate_normal_texture.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set((2.0, 2.0, 2.0, 1.0))
            alternate_normal_texture.CreateInput("bias", Sdf.ValueTypeNames.Float4).Set((-1.0, -1.0, -1.0, 0.0))
            alternate_normal_texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            alternate_shader.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(
                alternate_normal_texture.ConnectableAPI(), "rgb"
            )

        alternate_emissive = alternate_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f)
        alternate_emissive_factor = (
            getattr(alternate_pbr, "emissiveFactor", None) if alternate_pbr is not None else None
        )
        if alternate_emissive_factor is not None and len(alternate_emissive_factor) >= 3:
            divisor = (
                255.0 if max(float(value) for value in alternate_emissive_factor[:3]) > 1.0 else 1.0
            )
            alternate_emissive.Set(tuple(float(value) / divisor for value in alternate_emissive_factor[:3]))
        else:
            alternate_emissive.Set((0.0, 0.0, 0.0))
        alternate_emissive_texture = _variant_texture(
            getattr(alternate_pbr, "emissiveTexture", None) if alternate_pbr is not None else None,
            role="emissive",
            color_space="sRGB",
            mode="RGB",
            node_name="EmissiveTexture",
        )
        if alternate_emissive_texture is not None:
            alternate_emissive_texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            alternate_emissive.ConnectToSource(alternate_emissive_texture.ConnectableAPI(), "rgb")

        alternate_material.CreateSurfaceOutput().ConnectToSource(alternate_shader.ConnectableAPI(), "surface")
        appearance_bindings.append((variant_id, alternate_material))
        appearance_report.append(
            {
                "id": variant_id,
                "displayName": display_name,
                "materialPath": material_path,
                "textures": variant_textures,
            }
        )

    appearance_set = visual.GetPrim().GetVariantSets().AddVariantSet("appearance")
    for variant_id, variant_material in appearance_bindings:
        appearance_set.AddVariant(variant_id)
        appearance_set.SetVariantSelection(variant_id)
        with appearance_set.GetVariantEditContext():
            binding_api.Bind(variant_material)
    appearance_set.SetVariantSelection("generated")
    visual.GetPrim().SetCustomDataByKey(
        "robotworld:appearanceVariantIds", Vt.StringArray([item[0] for item in appearance_bindings])
    )
    stage.SetDefaultPrim(visual.GetPrim())
    stage.Save()

    reopened = Usd.Stage.Open(str(out_path))
    composed = UsdGeom.Mesh.Get(reopened, "/Visual/Mesh") if reopened else None
    if not composed or not composed.GetPointsAttr().Get() or not composed.GetFaceVertexIndicesAttr().Get():
        raise RuntimeError("OpenUSD visual conversion failed validation")
    reopened_appearance = reopened.GetPrimAtPath("/Visual").GetVariantSet("appearance")
    if not reopened_appearance or set(reopened_appearance.GetVariantNames()) != {
        item["id"] for item in appearance_report
    }:
        raise RuntimeError("OpenUSD appearance VariantSet failed validation")
    return out_path, {
        "vertices": len(points),
        "faces": len(faces),
        "materialCount": 1 if pbr is not None else 0,
        "textures": texture_artifacts,
        "sourcePbrPreserved": bool(texture_artifacts),
        "appearanceVariantSet": "appearance",
        "defaultAppearanceVariantId": "generated",
        "appearanceVariants": appearance_report,
    }


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
        from pxr import Sdf, Usd, UsdGeom, UsdPhysics  # type: ignore
    except ImportError as exc:
        raise RuntimeError("OpenUSD world authoring unavailable: install usd-core") from exc

    out_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(out_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/RobotWorld")
    UsdGeom.Xform.Define(stage, "/RobotWorld/Assets")
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
        placed.GetPrim().CreateAttribute("robotworld:assetKind", Sdf.ValueTypeNames.Token).Set(str(item.get("asset_kind", "rigid")))
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
        mobility = str(item.get("mobility", "fixed"))
        placed.GetPrim().CreateAttribute("robotworld:mobility", Sdf.ValueTypeNames.Token).Set(mobility)
        placed.GetPrim().CreateAttribute("robotworld:massSource", Sdf.ValueTypeNames.String).Set(
            str(item.get("mass_source", "unknown"))
        )
        placed.GetPrim().CreateAttribute("robotworld:physicalStatus", Sdf.ValueTypeNames.String).Set(
            "usd_physics_authored_pending_isaac_validation"
        )
        translation = tuple(float(v) for v in item.get("translation", (index * 1.5, 0.0, 0.0)))
        scale = tuple(max(0.001, float(v)) for v in item.get("scale", (1.0, 1.0, 1.0)))
        xformable = UsdGeom.Xformable(placed)
        xformable.AddTranslateOp().Set(translation)
        xformable.AddRotateZOp().Set(float(item.get("rotation_z_deg", 0.0)))
        xformable.AddScaleOp().Set(scale)
        # The generated visual mesh is the collision source. Dynamic meshes use
        # a convex hull because PhysX does not permit triangle-mesh collision on
        # moving rigid bodies. Fixed fixtures remain static triangle meshes.
        mesh_prim = stage.GetPrimAtPath(f"/RobotWorld/Assets/{prim_name}/Visual/Mesh")
        if mesh_prim and mesh_prim.IsValid() and str(item.get("asset_kind", "rigid")) != "articulated":
            UsdPhysics.CollisionAPI.Apply(mesh_prim)
            UsdPhysics.MeshCollisionAPI.Apply(mesh_prim).CreateApproximationAttr().Set(
                "convexHull" if mobility == "movable" else "none"
            )
            if mobility == "movable":
                UsdPhysics.RigidBodyAPI.Apply(placed.GetPrim())
                UsdPhysics.MassAPI.Apply(placed.GetPrim()).CreateMassAttr().Set(
                    max(0.001, float(item.get("mass_kg", 1.0)))
                )
        authored.append(f"/RobotWorld/Assets/{prim_name}")

    stage.Save()
    reopened = Usd.Stage.Open(str(out_path))
    if not reopened or not reopened.GetPrimAtPath("/RobotWorld/Assets"):
        raise RuntimeError("OpenUSD world validation failed: assembly root is unresolved")
    for prim_path in authored:
        prim = reopened.GetPrimAtPath(prim_path)
        if not prim or not prim.GetChildren():
            raise RuntimeError(f"OpenUSD world validation failed: reference is unresolved at {prim_path}")
        mesh = reopened.GetPrimAtPath(f"{prim_path}/Visual/Mesh")
        if mesh and mesh.IsValid() and prim.GetAttribute("robotworld:assetKind").Get() != "articulated":
            if not mesh.HasAPI(UsdPhysics.CollisionAPI):
                raise RuntimeError(f"OpenUSD world validation failed: collision API missing at {mesh.GetPath()}")
    return out_path, len(authored)
