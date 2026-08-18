"""OpenUSD/SimReady authoring for articulated assets.

The generated layer follows the portable USDPhysics schema: Z-up, metre and
kilogram units, non-nested rigid bodies, GPrim collision APIs, physical
material binding, and schema-correct revolute-joint attributes.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"Asset_{cleaned}"
    return cleaned


def build_usda(spec: dict[str, Any], asset_name: str) -> str:
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
    def Material "PhysicsMaterial" (
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


def _validate(path: Path, root: str) -> None:
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
    joint = UsdPhysics.RevoluteJoint.Get(stage, f"/{root}/DoorHinge")
    if not joint or not joint.GetPrim().IsValid():
        raise RuntimeError("SimReady validation failed: revolute joint is missing")
    if joint.GetAxisAttr().Get() != "Z":
        raise RuntimeError("SimReady validation failed: hinge axis is invalid")
    for prim_path in (f"/{root}/Body/Collision", f"/{root}/Door/PanelCollision", f"/{root}/Door/HandleCollision"):
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.HasAPI(UsdPhysics.CollisionAPI):
            raise RuntimeError(f"SimReady validation failed: collider missing at {prim_path}")


def write_usda(spec: dict[str, Any], asset_name: str, out_path: Path) -> tuple[Path, bool]:
    """Write and validate a USDA layer. Invalid output fails the pipeline."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    root = _identifier(asset_name)
    out_path.write_text(build_usda(spec, root), encoding="utf8")
    _validate(out_path, root)
    return out_path, True
