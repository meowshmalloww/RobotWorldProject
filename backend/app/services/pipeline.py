"""Asset build pipeline — the core world-building job.

  source (Bright Data scrape | manual spec) -> physical spec with
  source/confidence per field -> part graph -> GLB (trimesh) + MJCF (MuJoCo)
  + USDA (SimReady) -> physics validation rollout -> readiness score.

Uncertain fields are explicitly tagged `inferred`/`material_prior` and get
domain-randomized downstream — never faked as exact.
"""
from __future__ import annotations

import logging
import math
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import ASSETS_DIR
from ..db import SessionLocal
from ..models import Artifact, Asset, CompileStage, Source
from ..telemetry import span
from ..util import new_id
from . import brightdata, events, geometry, trellis, usda
from .simcore import World, ScriptedController, robot_base_for_asset, run_rollout

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
    "KITCHEN SINK": {
        "manufacturer": "Generic",
        "model": "kitchen-sink",
        "category": "sink",
        "width_m": (0.61, "inferred", 0.4),
        "height_m": (0.23, "inferred", 0.4),
        "depth_m": (0.46, "inferred", 0.4),
        "mass_kg": (8.0, "inferred", 0.35),
        "materials": (["stainless steel"], "inferred", 0.4),
    },
    "KITCHEN FAUCET": {
        "manufacturer": "Generic", "model": "kitchen-faucet", "category": "faucet",
        "width_m": (0.06, "inferred", 0.3), "height_m": (0.40, "inferred", 0.3),
        "depth_m": (0.25, "inferred", 0.3), "mass_kg": (2.0, "inferred", 0.25),
        "materials": (["stainless steel"], "inferred", 0.35),
    },
    "KITCHEN COUNTER": {
        "manufacturer": "Generic", "model": "kitchen-counter", "category": "counter",
        "width_m": (1.80, "inferred", 0.25), "height_m": (0.90, "inferred", 0.25),
        "depth_m": (0.65, "inferred", 0.25), "mass_kg": (85.0, "inferred", 0.2),
        "materials": (["stone", "wood"], "inferred", 0.25),
    },
    "BLENDER": {
        "manufacturer": "Generic", "model": "countertop-blender", "category": "blender",
        "width_m": (0.20, "inferred", 0.3), "height_m": (0.42, "inferred", 0.3),
        "depth_m": (0.20, "inferred", 0.3), "mass_kg": (4.0, "inferred", 0.25),
        "materials": (["glass", "plastic", "stainless steel"], "inferred", 0.3),
    },
    "APPLE": {
        "manufacturer": "Natural produce", "model": "apple", "category": "fruit",
        "width_m": (0.09, "inferred", 0.25), "height_m": (0.08, "inferred", 0.25),
        "depth_m": (0.09, "inferred", 0.25), "mass_kg": (0.18, "inferred", 0.2),
        "materials": (["organic produce"], "inferred", 0.3),
    },
    "ORANGE": {
        "manufacturer": "Natural produce", "model": "orange", "category": "fruit",
        "width_m": (0.08, "inferred", 0.25), "height_m": (0.08, "inferred", 0.25),
        "depth_m": (0.08, "inferred", 0.25), "mass_kg": (0.15, "inferred", 0.2),
        "materials": (["organic produce"], "inferred", 0.3),
    },
    "BANANA": {
        "manufacturer": "Natural produce", "model": "banana", "category": "fruit",
        "width_m": (0.20, "inferred", 0.25), "height_m": (0.05, "inferred", 0.2),
        "depth_m": (0.05, "inferred", 0.2), "mass_kg": (0.12, "inferred", 0.2),
        "materials": (["organic produce"], "inferred", 0.3),
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


async def _query_brightdata_images(query: str, limit: int = 4) -> list[dict]:
    limit = max(1, min(20, limit))
    images = await brightdata.google_images(f"{query} product photo")
    def source_rank(item: dict[str, Any]) -> tuple[int, int]:
        raw = str(item.get("original") or item.get("thumb") or "")
        host = (urllib.parse.urlsplit(raw).hostname or "").lower()
        title = str(item.get("title") or "").lower()
        if any(token in f"{host} {title}" for token in (
            "shoprite", "amazon", "walmart", "target", "costco", "kroger",
            "whole foods", "lunds", "wayfair", "homedepot", "lowes",
        )):
            tier = 0
        elif "wikimedia.org" in host:
            tier = 1
        elif any(token in host for token in ("vecteezy", "istockphoto", "dreamstime", "shutterstock")):
            tier = 3
        else:
            tier = 2
        try:
            pixels = int(item.get("width") or 0) * int(item.get("height") or 0)
        except (TypeError, ValueError):
            pixels = 0
        return tier, -pixels

    images = sorted(images, key=source_rank)
    photos: list[dict] = []
    for im in images:
        candidate = str(im.get("original") or im.get("thumb") or "").strip()
        parsed = urllib.parse.urlsplit(candidate)
        # A TRELLIS source image is fetched by the local model gateway. Keep
        # the asset build provenance intact, but never select an insecure,
        # credentialed, or malformed candidate that the gateway must reject.
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            continue
        host = parsed.hostname.lower()
        if host == "leonardo.ai" or host.endswith(".leonardo.ai") or any(token in host for token in ("midjourney", "stablediffusion")):
            continue
        title = str(im.get("title") or "").lower()
        source_text = f"{title} {urllib.parse.unquote(parsed.path).lower()}"
        # Reject common scene/mockup results before spending a 1024 generation.
        # These terms describe backgrounds and presentation templates, not an
        # isolated product/object suitable for single-image reconstruction.
        if any(term in source_text for term in (
            "copy space", "blur background", "mockup", "product display",
            "presentation background", "whole-and-cut", "whole and cut",
            "cross section", "cross_section", "cutaway", "sliced", "slice",
        )):
            continue
        width = im.get("width")
        height = im.get("height")
        try:
            pixels = max(0, int(width)) * max(0, int(height))
        except (TypeError, ValueError):
            pixels = 0
        resolution_score = round(min(1.0, math.sqrt(pixels) / 1600), 2) if pixels else 0.0
        i = len(photos)
        photos.append(
            {
                "id": i + 1,
                "score": resolution_score,
                "state": "selected" if i == 0 else "secondary" if i == 1 else "candidate",
                "front": 0.0,
                "background": 0.0,
                "isolation": 0.0,
                "identity": 0.0,
                "seed": i + 1,
                "url": candidate,
                "title": str(im.get("title") or "").strip(),
                "sourceDomain": host,
                "qualityNote": "resolution scored; pose, isolation, and identity not yet independently verified",
            }
        )
        if len(photos) >= limit:
            break
    return photos


async def _gather_spec(
    query: str,
    source_id: str | None,
    *,
    image_count: int = 4,
    require_unlock: bool = True,
) -> tuple[dict[str, Any], list[str], list[dict]]:
    """Bright Data path: search -> images -> unlock manufacturer page -> extract.
    Returns (spec, provenance, photos)."""
    provenance: list[str] = []
    photos: list[dict] = []
    # When a source is explicitly selected, its validated Scraper Studio rows
    # are authoritative.  Do not silently bypass them with reference data.
    if source_id:
        async with SessionLocal() as session:
            source = await session.get(Source, source_id)
        if source is None:
            raise ValueError(f"unknown source '{source_id}'")
        detail = source.detail or {}
        rows = detail.get("rawRows") or []
        if not rows or source.completeness < 50:
            raise ValueError("Selected Scraper Studio source has no sufficiently complete validated rows; run its collector first.")
        first = rows[0]
        spec: dict[str, Any] = {}

        def number(*keys: str) -> float | None:
            for key in keys:
                value = first.get(key)
                if isinstance(value, (int, float)):
                    return float(value)
                if isinstance(value, str):
                    match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
                    if match:
                        return float(match.group())
            return None

        unit_scale = {
            "width_m": 1.0, "height_m": 1.0, "depth_m": 1.0,
            "width_cm": 0.01, "height_cm": 0.01, "depth_cm": 0.01,
            "width_in": 0.0254, "height_in": 0.0254, "depth_in": 0.0254,
            "width_inches": 0.0254, "height_inches": 0.0254, "depth_inches": 0.0254,
        }
        for axis in ("width", "height", "depth"):
            for key in (f"{axis}_m", f"{axis}_cm", f"{axis}_in", f"{axis}_inches"):
                value = number(key)
                if value is not None:
                    spec[f"{axis}_m"] = (value * unit_scale[key], "scraper_studio", 0.95)
                    break
        # A combined dimension string is accepted only through the unit-aware
        # parser; it is never treated as an unlabeled scalar.
        if not all(key in spec for key in ("width_m", "height_m", "depth_m")):
            spec.update(parse_dimensions(str(first.get("dimensions") or "")))
        mass = number("mass_kg", "weight_kg")
        if mass is not None:
            spec["mass_kg"] = (mass, "scraper_studio", 0.9)
        for key, aliases in {
            "manufacturer": ("manufacturer", "brand"),
            "model": ("model", "model_number", "sku", "mpn"),
            "category": ("category", "product_type"),
            "hinge_side": ("hinge_side",),
        }.items():
            value = next((first.get(alias) for alias in aliases if first.get(alias)), None)
            if value is not None:
                spec[key] = (value, "scraper_studio", 0.95)
        photos = list(detail.get("photos") or [])
        provenance = [f"Scraper Studio {source.collector}: https://{source.domain}"]
        for item in detail.get("provenance") or []:
            if isinstance(item, list) and len(item) == 2:
                provenance.append(f"{item[0]}: {item[1]}")
        if not photos and image_count > 0:
            photos = await _query_brightdata_images(query, image_count)
        return spec, provenance, photos
    # known-product fast path (still real reference data)
    for key, spec in KNOWN_PRODUCTS.items():
        dimension_rows = [spec.get(name) for name in ("width_m", "height_m", "depth_m")]
        published_dimensions = all(isinstance(value, tuple) and value[1] != "inferred" for value in dimension_rows)
        if key.lower() in query.lower() and published_dimensions:
            provenance.append(f"reference catalog: {spec['manufacturer']} {spec['model']}")
            if image_count > 0:
                photos = await _query_brightdata_images(query, image_count)
            return dict(spec), provenance, photos

    spec: dict[str, Any] = {}
    search = await brightdata.google_search(f"{query} specifications dimensions")
    organic = search.get("organic", []) if isinstance(search, dict) else []
    if organic:
        provenance.append(f"google: {organic[0].get('title', '')} — {organic[0].get('link', '')}")
    if image_count > 0:
        photos = await _query_brightdata_images(query, image_count)
    if organic and require_unlock:
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
    generator: str = "parametric",
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

    try:
        # Image-conditioned geometry must have a real source image. Parametric
        # builds are driven by measured/catalog specs and must remain usable
        # offline instead of spending SERP usage for an optional thumbnail.
        image_count = 1 if generator == "trellis2" else 0
        # Image-conditioned builds need both a source image and physical
        # evidence. The former never substitutes for measured dimensions.
        require_unlock = True

        async def do_scrape():
            if manual_spec:
                return {k: (v, "manufacturer_manual", 1.0) for k, v in manual_spec.items()}, ["manual entry"], []
            return await _gather_spec(query, source_id, image_count=image_count, require_unlock=require_unlock)

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
    query_lower = query.lower()
    if kind == "articulated":
        defaults = KNOWN_PRODUCTS["RF28T5001SR"]
    elif "faucet" in query_lower or "water tap" in query_lower:
        defaults = KNOWN_PRODUCTS["KITCHEN FAUCET"]
    elif "counter" in query_lower or "island cabinet" in query_lower:
        defaults = KNOWN_PRODUCTS["KITCHEN COUNTER"]
    elif "blender" in query_lower:
        defaults = KNOWN_PRODUCTS["BLENDER"]
    elif "apple" in query_lower:
        defaults = KNOWN_PRODUCTS["APPLE"]
    elif "orange" in query_lower:
        defaults = KNOWN_PRODUCTS["ORANGE"]
    elif "banana" in query_lower:
        defaults = KNOWN_PRODUCTS["BANANA"]
    elif "sink" in query_lower:
        defaults = KNOWN_PRODUCTS["KITCHEN SINK"]
    else:
        defaults = KNOWN_PRODUCTS["24IN CABINET"]
    for k, v in defaults.items():
        value = v[0] if isinstance(v, tuple) and len(v) == 3 else v
        spec_triples.setdefault(k, (value, "inferred", 0.4))
    inferred_category = str(_flat(defaults).get("category", "object"))
    spec_triples.setdefault("category", (inferred_category, "inferred", 0.6))
    spec = _flat(spec_triples)
    confidence_mean = round(100 * sum(v[2] for v in spec_triples.values() if isinstance(v, tuple)) / max(len(spec_triples), 1), 1)

    # Persist the real source evidence before 3D generation.  A model-side
    # failure must never erase the Bright Data result or leave the UI looking
    # as though no collection happened.
    source_path = ASSETS_DIR / asset_id / "spec.json"
    source_payload = {
        "properties": {k: ({"value": v[0], "source": v[1], "confidence": v[2]} if isinstance(v, tuple) else v) for k, v in spec_triples.items()},
        "provenance": provenance,
        "photos": photos,
        "geometry": {"generator": generator, "status": "source_acquired" if photos or generator == "parametric" else "source_image_missing"},
        "openusdValidated": False,
    }
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(__import__("json").dumps(source_payload, indent=2), encoding="utf8")
    async with SessionLocal() as session:
        asset = await session.get(Asset, asset_id)
        if asset is not None:
            asset.spec = source_payload["properties"]
            asset.properties = {
                "sourceStatus": "Bright Data image acquired" if photos else "No validated source image acquired",
                "geometryStatus": "Awaiting TRELLIS.2 1024-cascade generation" if photos else "Blocked before geometry generation",
                "openUsdStatus": "Pending generated visual mesh",
            }
            await session.commit()

    async def do_geometry():
        out = ASSETS_DIR / asset_id / "model.glb"
        if generator == "parametric":
            return geometry.build_glb(spec, out)
        if generator == "trellis2":
            candidates = [item for item in photos if item.get("url")]
            candidates.sort(key=lambda item: 0 if item.get("state") == "selected" else 1)
            if not candidates:
                raise trellis.TrellisError(
                    "TRELLIS.2 requires a validated source image. Re-run with a higher-quality image source or run SERP lookup."
                )
            last_error: Exception | None = None
            for candidate in candidates:
                try:
                    result = await trellis.generate_glb(str(candidate["url"]), out)
                except trellis.TrellisError as exc:
                    last_error = exc
                    # Candidate transport/content failures are recoverable and
                    # should advance to the next Bright Data result without
                    # spending a second 3D generation. Model/OOM/timeouts are
                    # not retried because the first GPU job may be ambiguous.
                    recoverable = any(message in str(exc) for message in (
                        "Source image fetch failed", "Image redirects are rejected",
                        "Unsupported source image type", "Source image is empty",
                    ))
                    if not recoverable:
                        raise
                    candidate["state"] = "rejected"
                    candidate["error"] = str(exc)
                    continue
                for item in photos:
                    if item is not candidate and item.get("state") == "selected":
                        item["state"] = "secondary"
                candidate["state"] = "selected"
                return result
            raise trellis.TrellisError(f"No Bright Data image candidate could be fetched: {last_error}")
        raise ValueError(f"unknown geometry generator '{generator}'")

    async def do_usd():
        out = ASSETS_DIR / asset_id / "asset.usda"
        if generator != "trellis2":
            usd_path, validated = usda.write_usda(spec, "SimReadyAsset", out)
            return usd_path, validated, None, None
        visual_path = out.parent / "visual.usdc"
        visual_path, visual_stats = usda.write_visual_usdc(out.parent / "model.glb", visual_path)
        if kind == "articulated":
            usd_path, validated = usda.write_usda(spec, "SimReadyAsset", out, visual_layer=visual_path.name)
        else:
            usd_path, validated = usda.write_visual_asset_usda("SimReadyAsset", out, visual_layer=visual_path.name)
        world_path, _ = usda.write_world_usda(usd_path, "SimReadyAsset", out.parent / "world.usda")
        return usd_path, validated, visual_stats, world_path

    async def do_physics_validation() -> dict[str, Any]:
        """Load the compiled MJCF into MuJoCo and run a smoke rollout."""
        if kind != "articulated":
            return {
                "success": None,
                "status": "not_applicable",
                "failure_mode": "physical_evidence_missing",
                "failure_detail": "Visual image-to-3D asset has no measured collision, dimensions, articulation, or task-specific affordance evidence.",
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
        robot_base = robot_base_for_asset(asset_spec)
        scen = {
            "door_mass": spec.get("door_mass_kg", 12.0),
            "hinge_friction": spec.get("hinge_friction", 2.5),
            "handle_height": spec.get("handle_height_m", 1.05),
            "handle_orientation": "vertical",
            "max_open_deg": spec.get("max_open_deg", 110.0),
            "robot_base": robot_base,
        }
        world = World(scen, asset_spec)
        r = run_rollout(world, ScriptedController, record=False)
        return {
            "success": r.success,
            "door_deg": round(r.door_angle_deg, 3),
            "door_peak_deg": round(r.door_peak_deg, 3),
            "threshold_deg": 60.0,
            "collisions": r.collisions,
            "duration_s": round(r.duration_s, 3),
            "failure_mode": r.failure_mode,
            "failure_detail": r.failure_detail,
            "robot_base_xz_m": list(robot_base),
        }

    try:
        parts, vcount = await stage("generate_geometry", do_geometry)
        usd_path, usd_validated, visual_stats, world_path = await stage("compile_usd", do_usd)
        if not usd_validated:
            raise RuntimeError("OpenUSD validation did not complete")
        validation = await stage("physics_validation" if kind == "articulated" else "physical_evidence_gate", do_physics_validation)
    except Exception as exc:
        await _fail_asset(asset_id, stages, str(exc))
        raise

    physics_validity = round(100.0 if validation["success"] else 65.0 if validation.get("door_deg", 0) > 15 else 35.0, 1) if validation["success"] is not None else 0.0
    articulation = 100.0 if kind == "articulated" else 0.0
    readiness = round(0.4 * physics_validity + 0.3 * confidence_mean + 0.3 * articulation, 1)

    spec_path = ASSETS_DIR / asset_id / "spec.json"
    spec_payload = {
        "properties": {k: ({"value": v[0], "source": v[1], "confidence": v[2]} if isinstance(v, tuple) else v) for k, v in spec_triples.items()},
        "provenance": provenance,
        "photos": photos,
        "geometry": {
            "vertices": vcount,
            "generator": generator,
            "runtime": parts[0].get("runtime") if parts and isinstance(parts[0], dict) else None,
            "resolution": parts[0].get("resolution") if parts and isinstance(parts[0], dict) else None,
        },
        "openusdVisual": visual_stats,
        "openusdWorld": str(world_path.name) if world_path else None,
        "openusdValidated": usd_validated,
        "physicsValidation": validation,
    }
    spec_path.write_text(__import__("json").dumps(spec_payload, indent=2), encoding="utf8")

    async with SessionLocal() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        asset.spec = {
            **{k: ({"value": v[0], "source": v[1], "confidence": v[2]} if isinstance(v, tuple) else v) for k, v in spec_triples.items()},
            "geometry": spec_payload["geometry"],
        }
        asset.parts = parts
        visual_only = generator == "trellis2" and kind != "articulated"
        asset.properties = {
            "jointType": "Revolute (hinge)" if kind == "articulated" else "None",
            "axis": "Y (vertical)" if kind == "articulated" else "—",
            "limits": f"0° – {spec.get('max_open_deg', 110):.0f}°" if kind == "articulated" else "—",
            "mass": f"{spec.get('mass_kg', 0):.1f} kg ({'inferred' if visual_only else 'spec-derived'})",
            "material": f"{', '.join(spec.get('materials', ['unknown']))}{' (inferred)' if visual_only else ''}",
            "collider": "not authored - physical evidence required" if visual_only else "primitive box/cylinder",
            "semantic": spec.get("category", "object"),
            "affordance": "graspable handle → openable door" if kind == "articulated" else "not verified" if visual_only else "graspable",
            **({"physicalStatus": "visual-only pending measurement"} if visual_only else {}),
        }
        asset.status = "ready" if validation["success"] else "testing"
        asset.physics_validity = physics_validity
        asset.scale_confidence = confidence_mean
        asset.articulation = articulation
        asset.last_eval_result = "passed" if validation["success"] else "failed" if validation["success"] is False else "pending"
        from datetime import datetime, timezone

        asset.last_eval_at = datetime.now(timezone.utc)
        for i, (n, dur, st) in enumerate(stages):
            session.add(CompileStage(asset_id=asset_id, idx=i, name=n.replace("_", " ").title(), duration_s=dur, status=st))
        glb = ASSETS_DIR / asset_id / "model.glb"
        session.add(Artifact(asset_id=asset_id, type="mesh", file="model.glb", size_bytes=glb.stat().st_size if glb.exists() else 0))
        session.add(Artifact(asset_id=asset_id, type="usd", file="asset.usda", size_bytes=usd_path.stat().st_size if usd_path.exists() else 0))
        visual = ASSETS_DIR / asset_id / "visual.usdc"
        if visual.exists():
            session.add(Artifact(asset_id=asset_id, type="usd_visual", file="visual.usdc", size_bytes=visual.stat().st_size))
        basecolor = ASSETS_DIR / asset_id / "basecolor.png"
        if basecolor.exists():
            session.add(Artifact(asset_id=asset_id, type="texture", file="basecolor.png", size_bytes=basecolor.stat().st_size))
        if world_path and world_path.exists():
            session.add(Artifact(asset_id=asset_id, type="usd_world", file="world.usda", size_bytes=world_path.stat().st_size))
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
            asset.properties = {**(asset.properties or {}), "pipelineError": reason}
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
