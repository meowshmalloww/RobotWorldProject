"""Evidence-bounded OpenAI analysis for real asset sources.

This is deliberately not a dimension generator.  It turns already collected
Bright Data / Scraper Studio evidence into a typed review record and preserves
the evidence IDs behind every claim.  Values without a measurement in the
input remain unknown.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, OpenAIError

from ..config import ASSETS_DIR
from ..telemetry import span
from . import settings_store


class EvidenceAnalysisError(RuntimeError):
    """A safe, user-actionable analysis failure."""


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "assetSummary": {"type": "string"},
        "parts": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"}, "role": {"type": "string"},
                    "material": {"type": ["string", "null"]}, "evidenceIds": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                },
                "required": ["name", "role", "material", "evidenceIds", "confidence"],
            },
        },
        "dimensions": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"}, "valueMm": {"type": ["number", "null"]},
                    "sourceUnit": {"type": ["string", "null"]}, "evidenceIds": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                },
                "required": ["name", "valueMm", "sourceUnit", "evidenceIds", "confidence"],
            },
        },
        "materials": {"type": "array", "items": {"type": "string"}},
        "imageObservations": {"type": "array", "items": {"type": "string"}},
        "unknowns": {"type": "array", "items": {"type": "string"}},
        "humanReviewRequired": {"type": "boolean"},
    },
    "required": ["assetSummary", "parts", "dimensions", "materials", "imageObservations", "unknowns", "humanReviewRequired"],
}


def _load_source(asset_id: str) -> dict[str, Any]:
    path = ASSETS_DIR / asset_id / "spec.json"
    if not path.is_file():
        raise EvidenceAnalysisError("Asset evidence file is unavailable. Build or import the asset first.")
    try:
        payload = json.loads(path.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceAnalysisError("Asset evidence file is invalid.") from exc
    if not isinstance(payload, dict):
        raise EvidenceAnalysisError("Asset evidence file is invalid.")
    return payload


def _evidence_from_spec(payload: dict[str, Any]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for index, value in enumerate(payload.get("provenance") or [], start=1):
        if not isinstance(value, str) or not value.strip():
            continue
        evidence.append({"id": f"E{index:02d}", "kind": "source_provenance", "text": value.strip()[:6000]})
    for name, value in (payload.get("properties") or {}).items():
        if not isinstance(value, dict) or value.get("source") in {"inferred", "material_prior", "query_category"}:
            continue
        evidence.append({
            "id": f"E{len(evidence) + 1:02d}",
            "kind": str(value.get("source") or "collected_field"),
            "text": f"{name}: {value.get('value')}",
        })
    return evidence


def _has_measurement(text: str) -> bool:
    return bool(re.search(r"\d", text)) and bool(re.search(r"(?:mm|cm|\bin\b|inch|m\b|\")", text, re.IGNORECASE))


def _validate_result(result: Any, evidence: list[dict[str, str]]) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise EvidenceAnalysisError("OpenAI returned an invalid analysis record.")
    valid_ids = {item["id"] for item in evidence}
    measured_ids = {item["id"] for item in evidence if _has_measurement(item["text"])}
    for field in ("parts", "dimensions"):
        if not isinstance(result.get(field), list):
            raise EvidenceAnalysisError("OpenAI analysis is missing a required structured field.")
        for item in result[field]:
            if not isinstance(item, dict):
                raise EvidenceAnalysisError("OpenAI analysis contains an invalid record.")
            item["confidence"] = max(0.0, min(float(item.get("confidence", 0)), 1.0))
            ids = [value for value in item.get("evidenceIds", []) if value in valid_ids]
            item["evidenceIds"] = ids
            if field == "dimensions" and item.get("valueMm") is not None and not (set(ids) & measured_ids):
                # Do not let a model turn a product image, title, or inferred
                # record into an exact physical measurement.
                item["valueMm"] = None
                item["sourceUnit"] = None
                item["confidence"] = 0.0
                result.setdefault("unknowns", []).append(f"{item.get('name', 'dimension')}: no measured source evidence")
    result["humanReviewRequired"] = True
    return result


async def analyze(asset_id: str) -> dict[str, Any]:
    """Analyze current persisted evidence once, via the official Responses API."""
    payload = _load_source(asset_id)
    evidence = _evidence_from_spec(payload)
    if not evidence:
        raise EvidenceAnalysisError("No collected textual evidence is available. Add a manual/spec source or run a Scraper Studio collector first.")

    flat = await settings_store.get_flat()
    key = str(flat.get("models.openaiKey") or "")
    base_url = str(flat.get("models.openaiBaseUrl") or "https://api.openai.com/v1").rstrip("/")
    host = (urlparse(base_url).hostname or "").lower()
    if not key:
        raise EvidenceAnalysisError("OpenAI is not configured. Add the write-only API key in Settings -> Models.")
    if host != "api.openai.com":
        raise EvidenceAnalysisError("Asset evidence analysis requires the official OpenAI API endpoint; configure https://api.openai.com/v1.")
    model = str(flat.get("models.assetAnalysisModel") or "gpt-5.6-luna")
    effort = str(flat.get("models.reasoningEffort") or "high")
    if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
        effort = "high"
    verbosity = str(flat.get("models.verbosity") or "medium")
    if verbosity not in {"low", "medium", "high"}:
        verbosity = "medium"
    client = AsyncOpenAI(api_key=key, base_url=base_url, timeout=90, max_retries=1)
    instructions = (
        "You extract evidence for a physical asset review. Use only the supplied records. "
        "Never invent a part, manufacturer, material, lens, scale, depth, dimension, or citation. "
        "A source image can support only visual observations, never numeric dimensions. "
        "A numeric dimension may be emitted only when its cited record explicitly contains a number and unit. "
        "When evidence is insufficient, return null for valueMm and list the unknown. "
        "Do not recommend training and do not claim robot or physics readiness."
    )
    input_text = json.dumps({
        "asset": {"sourceImagePresent": bool(payload.get("photos")), "geometry": payload.get("geometry")},
        "evidence": evidence,
    }, ensure_ascii=False)
    try:
        with span("asset.openai_evidence", asset=asset_id, model=model) as trace:
            response = await client.responses.create(
                model=model,
                instructions=instructions,
                input=input_text,
                store=False,
                reasoning={"effort": effort},
                text={"verbosity": verbosity, "format": {"type": "json_schema", "name": "asset_evidence", "strict": True, "schema": _SCHEMA}},
            )
            trace.set_attribute("gen_ai.response.id", getattr(response, "_request_id", "") or "")
            raw = response.output_text
    except APIStatusError as exc:
        raise EvidenceAnalysisError(f"OpenAI rejected the evidence analysis ({exc.status_code}). Check the rotated server credential and model access.") from exc
    except (APITimeoutError, APIConnectionError) as exc:
        raise EvidenceAnalysisError("OpenAI evidence analysis could not reach the service. No asset data was changed.") from exc
    except OpenAIError as exc:
        raise EvidenceAnalysisError(f"OpenAI evidence analysis failed: {type(exc).__name__}") from exc
    finally:
        await client.close()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceAnalysisError("OpenAI did not return a valid structured evidence record.") from exc
    result = _validate_result(parsed, evidence)
    return {"provider": "openai", "model": model, "evidence": evidence, "analysis": result}
