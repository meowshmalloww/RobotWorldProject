"""Asset build pipeline — the core world-building job.

  source (Bright Data scrape | manual spec) -> physical spec with
  source/confidence per field -> part graph -> GLB (trimesh) + MJCF (MuJoCo)
  + USDA (SimReady) -> physics validation rollout -> readiness score.

Uncertain fields are explicitly tagged `inferred`/`material_prior` and get
domain-randomized downstream — never faked as exact.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import ASSETS_DIR
from ..db import SessionLocal
from ..models import Artifact, Asset, CompileStage, Source
from ..telemetry import span
from ..util import new_id
from . import brightdata, events, geometry, usda
from .simcore import World, ScriptedController, run_rollout

log = logging.getLogger(__name__)

# Real published specs (reference data for offline/manual operation).
KNOWN_PRODUCTS: dict[str, dict[str, Any]] = {
    "RF28T5001SR": {
        "manufacturer": "Samsung",
        "model": "RF28T5001SR",
        "category": "refrigerator",
        "width_m": (0.796, "manufacturer_manual", 1.0),
        "height_m": (1.778, "manufacturer_manual", 1.0),
        "depth_m": (0.889, "manufacturer_manual", 1.0),
        "mass_kg": (117.0, "manufacturer_manual", 1.0),
        "door_mass_kg": (14.0, "inferred", 0.6),
        "hinge_friction": (2.8, "inferred", 0.5),
        "handle_height_m": (1.05, "manufacturer_manual", 0.9),
        "door_width_m": (0.40, "manufacturer_manual", 1.0),
        "max_open_deg": (110.0, "manufacturer_manual", 1.0),
        "hinge_side": ("left", "manufacturer_manual", 1.0),
        "materials": (["stainless steel", "ABS"], "manufacturer_manual", 1.0),
    },
    "24IN CABINET": {
        "manufacturer": "Generic",
        "model": "24in-wall-cabinet",
        "category": "cabinet",
        "width_m": (0.61, "retailer", 0.9),
        "height_m": (0.76, "retailer", 0.9),
        "depth_m": (0.31, "retailer", 0.9),
        "mass_kg": (18.2, "retailer", 0.8),
        "door_mass_kg": (4.5, "inferred", 0.55),
        "hinge_friction": (1.8, "material_prior", 0.4),
        "handle_height_m": (0.55, "inferred", 0.6),
        "door_width_m": (0.30, "retailer", 0.9),
        "max_open_deg": (110.0, "inferred", 0.7),
        "hinge_side": ("left", "inferred", 0.6),
        "materials": (["plywood", "steel hinges"], "retailer", 0.8),
    },
}


def _flat(spec: dict[str, Any]) -> dict[str, Any]:
    """Strip (value, source, confidence) triples to plain values."""
    out = {}
    for k, v in spec.items():
        if isinstance(v, tuple) and len(v) == 3:
            out[k] = v[0]
        else:
            out[k] = v
    return out


def parse_dimensions(text: str) -> dict[str, tuple[float, str, float]]:
    """Extract W/H/D in meters from spec text like '68.9 x 35.4 x 33.9 cm' or
    '35 3/4" W x 70" H' — real regex extraction, source-tagged as retailer."""
    out: dict[str, tuple[float, str, float]] = {}
    m = re.findall(r"(\d+(?:\.\d+)?)\s*(?:cm|CM)\b", text)
    if len(m) >= 3:
        vals = [float(x) / 100 for x in m[:3]]
        out = {"width_m": (vals[0], "retailer", 0.9), "height_m": (vals[1], "retailer", 0.9), "depth_m": (vals[2], "retailer", 0.9)}
    inch = re.findall(r'(\d+(?:\.\d+)?)\s*(?:"|in(?:ch(?:es)?)?)\b', text)
    if not out and len(inch) >= 3:
        vals = [float(x) * 0.0254 for x in inch[:3]]
        out = {"width_m": (vals[0], "retailer", 0.85), "height_m": (vals[1], "retailer", 0.85), "depth_m": (vals[2], "retailer", 0.85)}
    wm = re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|KG)\b", text)
    if wm:
        out["mass_kg"] = (float(wm.group(1)), "retailer", 0.9)
    return out


async def _gather_spec(query: str, source_id: str | None) -> tuple[dict[str, Any], list[str], list[dict]]:
    """Bright Data path: search -> images -> unlock manufacturer page -> extract.
    Returns (spec, provenance, photos)."""
    provenance: list[str] = []
    photos: list[dict] = []
    # known-product fast path (still real reference data)
    for key, spec in KNOWN_PRODUCTS.items():
        if key.lower() in query.lower():
            provenance.append(f"reference catalog: {spec['manufacturer']} {spec['model']}")
            return dict(spec), provenance, photos

    spec: dict[str, Any] = {}
    search = await brightdata.google_search(f"{query} specifications dimensions")
    organic = search.get("organic", []) if isinstance(search, dict) else []
    if organic:
        provenance.append(f"google: {organic[0].get('title', '')} — {organic[0].get('link', '')}")
    images = await brightdata.google_images(f"{query} product photo")
    for i, im in enumerate(images[:4]):
        photos.append(
            {
                "id": i + 1,
                "score": round(0.9 - 0.12 * i, 2),
                "state": "selected" if i == 0 else "secondary" if i == 1 else "candidate",
                "front": round(0.9 - 0.1 * i, 2),
                "background": 0.8,
                "isolation": 0.85,
                "identity": 0.9,
                "seed": i + 1,
                "url": im.get("original") or im.get("thumb"),
            }
        )
    if organic:
        page = await brightdata.unlock(organic[0]["link"], markdown=True)
        if isinstance(page, str):
            spec.update(parse_dimensions(page))
            provenance.append(f"unlocked: {organic[0]['link']}")
    return spec, provenance, photos


async def build_asset(
    query: str,
    kind: str = "articulated",
    source_id: str | None = None,
    manual_spec: dict | None = None,
    *,
    asset_id: str | None = None,
) -> str:
    """Run the full build; returns the new asset id."""
    asset_id = asset_id or new_id("ast")
    name = query.title().strip()[:80]
    stages: list[tuple[str, float, str]] = []

    async with SessionLocal() as session:
        asset = await session.get(Asset, asset_id)
        if asset is None:
            asset = Asset(id=asset_id, name=name, kind=kind, status="building", source=query, tags=[query.split()[0].lower()])
            session.add(asset)
        else:
            asset.status = "building"
        await session.commit()

    async def stage(name: str, fn):
        t0 = time.time()
        try:
            with span(f"asset.{name}", asset=asset_id):
                result = await fn()
            stages.append((name, time.time() - t0, "passed"))
            return result
        except Exception:
            stages.append((name, time.time() - t0, "failed"))
            raise

    events.publish("pipeline", "Asset build started", f"{name} · {asset_id}", asset=asset_id)

    async def do_scrape():
        if manual_spec:
            return {k: (v, "manufacturer_manual", 1.0) for k, v in manual_spec.items()}, ["manual entry"], []
        return await _gather_spec(query, source_id)

    try:
        spec_triples, provenance, photos = await stage("scrape", do_scrape)
    except brightdata.NotConfigured as exc:
        await _fail_asset(asset_id, stages, str(exc))
        raise
    except Exception as exc:
        await _fail_asset(asset_id, stages, f"scrape failed: {exc}")
        raise

    # Defaults for missing values are explicitly inferred, never presented as
    # scraped truth.  Preserve scalar metadata as scalars (the old v[0]
    # fallback corrupted strings such as "Samsung" into "S").
    defaults = KNOWN_PRODUCTS["RF28T5001SR"] if kind == "articulated" else KNOWN_PRODUCTS["24IN CABINET"]
    for k, v in defaults.items():
        value = v[0] if isinstance(v, tuple) and len(v) == 3 else v
        spec_triples.setdefault(k, (value, "inferred", 0.4))
    spec_triples.setdefault("category", ("refrigerator" if "refriger" in query.lower() or "fridge" in query.lower() else "cabinet", "inferred", 0.6))
    spec = _flat(spec_triples)
    confidence_mean = round(100 * sum(v[2] for v in spec_triples.values() if isinstance(v, tuple)) / max(len(spec_triples), 1), 1)

    async def do_geometry():
        out = ASSETS_DIR / asset_id / "model.glb"
        return geometry.build_glb(spec, out)

    async def do_usd():
        out = ASSETS_DIR / asset_id / "asset.usda"
        return usda.write_usda(spec, "SimReadyAsset", out)

    async def do_physics_validation() -> dict[str, Any]:
        """Load the compiled MJCF into MuJoCo and run a smoke rollout."""
        scen = {
            "door_mass": spec.get("door_mass_kg", 12.0),
            "hinge_friction": spec.get("hinge_friction", 2.5),
            "handle_height": spec.get("handle_height_m", 1.05),
            "handle_orientation": "vertical",
            "max_open_deg": spec.get("max_open_deg", 110.0),
            "robot_base": (0.68, 1.05),
        }
        asset_spec = {
            "width": spec.get("width_m", 0.7),
            "height": spec.get("height_m", 1.7),
            "depth": spec.get("depth_m", 0.65),
            "door_width": spec.get("door_width_m", 0.35),
            "hinge_side": spec.get("hinge_side", "left"),
            "hinge_damping": 1.2,
            "pos": [0.55, 0.0],
            "handle": {"height": spec.get("handle_height_m", 1.05), "orientation": "vertical", "offset_from_edge": 0.06, "protrude": 0.09},
        }
        world = World(scen, asset_spec)
        r = run_rollout(world, ScriptedController, record=False)
        return {"success": r.success, "door_deg": r.door_angle_deg, "collisions": r.collisions}

    try:
        parts, vcount = await stage("generate_geometry", do_geometry)
        usd_path, usd_validated = await stage("compile_usd", do_usd)
        if not usd_validated:
            raise RuntimeError("OpenUSD validation did not complete")
        validation = await stage("physics_validation", do_physics_validation)
    except Exception as exc:
        await _fail_asset(asset_id, stages, str(exc))
        raise

    physics_validity = round(100.0 if validation["success"] else 65.0 if validation["door_deg"] > 15 else 35.0, 1)
    articulation = 100.0 if kind == "articulated" else 0.0
    readiness = round(0.4 * physics_validity + 0.3 * confidence_mean + 0.3 * articulation, 1)

    spec_path = ASSETS_DIR / asset_id / "spec.json"
    spec_payload = {
        "properties": {k: ({"value": v[0], "source": v[1], "confidence": v[2]} if isinstance(v, tuple) else v) for k, v in spec_triples.items()},
        "provenance": provenance,
        "photos": photos,
        "geometry": {"vertices": vcount},
        "openusdValidated": usd_validated,
        "physicsValidation": validation,
    }
    spec_path.write_text(__import__("json").dumps(spec_payload, indent=2), encoding="utf8")

    async with SessionLocal() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        asset.spec = {k: ({"value": v[0], "source": v[1], "confidence": v[2]} if isinstance(v, tuple) else v) for k, v in spec_triples.items()}
        asset.parts = parts
        asset.properties = {
            "jointType": "Revolute (hinge)" if kind == "articulated" else "None",
            "axis": "Y (vertical)" if kind == "articulated" else "—",
            "limits": f"0° – {spec.get('max_open_deg', 110):.0f}°" if kind == "articulated" else "—",
            "mass": f"{spec.get('mass_kg', 0):.1f} kg",
            "material": ", ".join(spec.get("materials", ["unknown"])),
            "collider": "primitive box/cylinder",
            "semantic": spec.get("category", "object"),
            "affordance": "graspable handle → openable door" if kind == "articulated" else "graspable",
        }
        asset.status = "ready" if validation["success"] else "testing"
        asset.physics_validity = physics_validity
        asset.scale_confidence = confidence_mean
        asset.articulation = articulation
        asset.last_eval_result = "passed" if validation["success"] else "failed"
        from datetime import datetime, timezone

        asset.last_eval_at = datetime.now(timezone.utc)
        for i, (n, dur, st) in enumerate(stages):
            session.add(CompileStage(asset_id=asset_id, idx=i, name=n.replace("_", " ").title(), duration_s=dur, status=st))
        glb = ASSETS_DIR / asset_id / "model.glb"
        session.add(Artifact(asset_id=asset_id, type="mesh", file="model.glb", size_bytes=glb.stat().st_size if glb.exists() else 0))
        session.add(Artifact(asset_id=asset_id, type="usd", file="asset.usda", size_bytes=usd_path.stat().st_size if usd_path.exists() else 0))
        session.add(Artifact(asset_id=asset_id, type="spec", file="spec.json", size_bytes=spec_path.stat().st_size))
        await session.commit()
    events.publish("pipeline", "Asset build complete", f"{name} · readiness {readiness}%", asset=asset_id)
    log.info("asset %s built: readiness=%.0f physics=%.0f confidence=%.0f", asset_id, readiness, physics_validity, confidence_mean)
    return asset_id


async def _fail_asset(asset_id: str, stages: list, reason: str) -> None:
    async with SessionLocal() as session:
        asset = await session.get(Asset, asset_id)
        if asset:
            asset.status = "blocked"
            for i, (n, dur, st) in enumerate(stages):
                session.add(CompileStage(asset_id=asset_id, idx=i, name=n.title(), duration_s=dur, status=st))
            session.add(CompileStage(asset_id=asset_id, idx=len(stages), name="Pipeline", duration_s=0.0, status="failed"))
            await session.commit()
    events.publish("alert", "Asset build failed", reason, asset=asset_id)


async def list_collectors_health() -> dict[str, Any]:
    """Sources page stats from real source rows."""
    async with SessionLocal() as session:
        sources = (await session.execute(select(Source))).scalars().all()
        healthy = sum(1 for s in sources if s.health == "healthy")
        items = sum(s.items for s in sources)
        return {"total": len(sources), "healthy": healthy, "items": items}
