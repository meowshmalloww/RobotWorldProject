"""Exact-object evidence normalization, identity resolution, and quality gates."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select

from ..config import DATA_DIR, EVIDENCE_DIR
from ..contracts import EvidenceBundle, EvidenceRecord, ObjectIdentity, ObjectRequest, PropertyEstimate, RecordedEvidenceImport
from ..db import SessionLocal
from ..models import AuditEvent, EvidenceBundleRecord, EvidenceRecordRow, ObjectRequestRecord
from ..util import new_id
from . import command_store


MAX_RECORDED_PAYLOAD_BYTES = 1_000_000
DEFAULT_IDENTITY_CONFIDENCE_THRESHOLD = 0.90
DEFAULT_COMPLETENESS_THRESHOLD = 0.75


class EvidenceCatalogError(RuntimeError):
    pass


class EvidenceConflict(EvidenceCatalogError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _identifier(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _words(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _threshold(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if 0 <= value <= 1 else default


def _source_url(value: Any, *, controlled: bool) -> tuple[str, str]:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.username or parsed.password:
        raise ValueError("source URL must not contain credentials")
    if not host or parsed.scheme not in {"http", "https"}:
        raise ValueError("source URL must be absolute HTTP(S)")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        address = None
    if parsed.scheme != "https":
        if not controlled or address is None or not address.is_loopback:
            raise ValueError("external evidence URLs must use HTTPS")
    if address is not None and (address.is_private or address.is_link_local or address.is_reserved) and not (controlled and address.is_loopback):
        raise ValueError("source URL points to a disallowed private or link-local address")
    return raw, host


def validate_external_source_url(value: str) -> tuple[str, str]:
    """Validate a provider target without performing a local network lookup."""
    return _source_url(value, controlled=False)


def validate_source_url(value: str, *, controlled: bool = False) -> tuple[str, str]:
    """Validate a captured-test URL under the explicit controlled-fixture policy."""
    return _source_url(value, controlled=controlled)


def _first(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    return next((row[name] for name in names if row.get(name) not in (None, "", [])), None)


def _identity_claims(row: dict[str, Any]) -> dict[str, str | None]:
    return {
        "manufacturer": str(_first(row, ("manufacturer", "brand", "make")) or "").strip() or None,
        "modelNumber": str(_first(row, ("model_number", "modelNumber", "model", "mpn")) or "").strip() or None,
        "sku": str(_first(row, ("sku", "product_sku", "item_number")) or "").strip() or None,
        "gtin": re.sub(r"\D", "", str(_first(row, ("gtin", "upc", "ean")) or "")) or None,
    }


def _authoritative(host: str, domains: list[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _bad_page(row: dict[str, Any]) -> str | None:
    text = " ".join(str(row.get(key) or "")[:5000] for key in ("title", "text", "body", "html", "error"))
    lowered = text.lower()
    signatures = {
        "captcha": ("captcha", "verify you are human", "unusual traffic"),
        "login": ("sign in to continue", "login required", "access denied"),
        "error_page": ("page not found", "404 not found", "internal server error"),
    }
    for label, candidates in signatures.items():
        if any(candidate in lowered for candidate in candidates):
            return label
    return None


_UNIT_SCALE = {
    "m": 1.0,
    "meter": 1.0,
    "meters": 1.0,
    "cm": 0.01,
    "centimeter": 0.01,
    "centimeters": 0.01,
    "mm": 0.001,
    "millimeter": 0.001,
    "millimeters": 0.001,
    "in": 0.0254,
    "inch": 0.0254,
    "inches": 0.0254,
    "ft": 0.3048,
}
_MASS_SCALE = {"kg": 1.0, "g": 0.001, "lb": 0.45359237, "lbs": 0.45359237, "oz": 0.028349523125}


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _measurement(row: dict[str, Any], name: str, units: dict[str, float]) -> tuple[float, str] | None:
    for unit, scale in units.items():
        for key in (f"{name}_{unit}", f"{name}{unit.upper()}"):
            number = _finite_positive(row.get(key))
            if number is not None:
                return number * scale, unit
    raw = row.get(name)
    unit = str(row.get(f"{name}_unit") or row.get("dimension_unit" if name != "mass" else "mass_unit") or "").strip().lower()
    number = _finite_positive(raw)
    if number is not None and unit in units:
        return number * units[unit], unit
    if isinstance(raw, str):
        match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*", raw)
        if match and match.group(2).lower() in units:
            return float(match.group(1)) * units[match.group(2).lower()], match.group(2).lower()
    return None


def _images(row: dict[str, Any], *, controlled: bool) -> tuple[list[dict[str, Any]], list[str]]:
    raw_values: list[Any] = []
    for key in ("images", "photos", "image", "image_url"):
        value = row.get(key)
        if isinstance(value, list):
            raw_values.extend(value)
        elif value:
            raw_values.append(value)
    output: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, value in enumerate(raw_values[:12]):
        item = value if isinstance(value, dict) else {"url": value}
        try:
            url, _ = _source_url(item.get("url") or item.get("original") or item.get("src"), controlled=controlled)
        except ValueError as exc:
            errors.append(f"image {index + 1}: {exc}")
            continue
        mime = str(item.get("mime_type") or item.get("mimeType") or "").lower()
        width = _finite_positive(item.get("width"))
        height = _finite_positive(item.get("height"))
        if mime and not mime.startswith("image/"):
            errors.append(f"image {index + 1}: invalid MIME {mime}")
        if width is not None and height is not None and (width < 128 or height < 128):
            errors.append(f"image {index + 1}: dimensions below 128 px")
        output.append(
            {
                "url": url,
                "view": item.get("view") or item.get("label") or "unknown",
                "mimeType": mime or None,
                "width": int(width) if width is not None else None,
                "height": int(height) if height is not None else None,
                "contentSha256": item.get("content_sha256") or item.get("contentSha256"),
                "magicValidated": bool(item.get("magic_validated") or item.get("magicValidated")),
            }
        )
    return output, errors


def request_view(row: ObjectRequestRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "revision": row.revision,
        "requestedName": row.requested_name,
        "manufacturer": row.manufacturer,
        "modelNumber": row.model_number,
        "sku": row.sku,
        "gtin": row.gtin,
        "category": row.category,
        "exactIdentity": row.exact_identity,
        "authoritativeDomains": list(row.authoritative_domains or []),
        "requiredProperties": list(row.required_properties or []),
        "lifecycleState": row.lifecycle_state,
        "validationErrors": list(row.validation_errors or []),
        "createdBy": row.created_by,
        "source": row.source,
        "requestSha256": row.request_sha256,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def evidence_record_view(row: EvidenceRecordRow) -> dict[str, Any]:
    return EvidenceRecord(
        id=row.id,
        request_id=row.request_id,
        source_url=row.source_url,
        source_type=row.source_type,
        source_domain=row.source_domain,
        retrieved_at=row.retrieved_at,
        collector_id=row.collector_id,
        collector_version=row.collector_version,
        content_sha256=row.content_sha256,
        artifact_ref=row.artifact_ref,
        normalized=dict(row.normalized or {}),
        identity_claims=dict(row.identity_claims or {}),
        quality_errors=list(row.quality_errors or []),
        license_metadata=dict(row.license_metadata or {}),
        created_at=row.created_at,
    ).model_dump(mode="json", by_alias=True)


def bundle_view(row: EvidenceBundleRecord) -> dict[str, Any]:
    return EvidenceBundle(
        id=row.id,
        request_id=row.request_id,
        revision=row.revision,
        lifecycle_state=row.lifecycle_state,
        identity=ObjectIdentity.model_validate(row.identity),
        evidence_record_ids=list(row.evidence_ids or []),
        properties=[PropertyEstimate.model_validate(value) for value in (row.properties or [])],
        completeness=row.completeness,
        identity_confidence=row.identity_confidence,
        validation_errors=list(row.validation_errors or []),
        bundle_sha256=row.bundle_sha256,
        artifact_ref=row.artifact_ref,
        created_by=row.created_by,
        source=row.source,
        created_at=row.created_at,
    ).model_dump(mode="json", by_alias=True)


async def create_request(payload: ObjectRequest, *, idempotency_key: str | None, actor: str = "user") -> dict[str, Any]:
    wire = payload.model_dump(mode="json", by_alias=True)
    command, reused = await command_store.start_command(
        kind="evidence.request.create",
        target_type="object_request",
        target_id=None,
        payload=wire,
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        return command_store.command_view(command, reused=True)
    row = ObjectRequestRecord(
        id=new_id("objreq"),
        revision=1,
        requested_name=payload.requested_name,
        manufacturer=payload.manufacturer,
        model_number=payload.model_number,
        sku=payload.sku,
        gtin=payload.gtin,
        category=payload.category,
        exact_identity=payload.exact_identity,
        authoritative_domains=payload.authoritative_domains,
        required_properties=payload.required_properties,
        lifecycle_state="REQUESTED",
        created_by=actor,
        source="api",
        request_sha256=_sha256(wire),
    )
    async with SessionLocal() as session:
        session.add(row)
        session.add(
            AuditEvent(
                command_id=command.id,
                entity_type="object_request",
                entity_id=row.id,
                action="evidence.request.create",
                from_state=None,
                to_state="REQUESTED",
                detail={"requestSha256": row.request_sha256, "exactIdentity": row.exact_identity},
                actor=actor,
            )
        )
        await session.commit()
    output = {"objectRequest": request_view(row)}
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_store.command_view(command)


def _normalize_rows(request: ObjectRequestRecord, payload: RecordedEvidenceImport, raw_artifact_ref: str):
    expected_identifiers = {
        key: _identifier(value)
        for key, value in {"modelNumber": request.model_number, "sku": request.sku, "gtin": request.gtin}.items()
        if value
    }
    expected_values = set(expected_identifiers.values())
    expected_manufacturer = _words(request.manufacturer)
    controlled = payload.source == "controlled_fixture"
    record_values: list[dict[str, Any]] = []
    candidate_properties: list[dict[str, Any]] = []
    identity_ids: list[str] = []
    explicit_identifiers: set[str] = set()
    bundle_errors: list[str] = []
    exact_domains: set[str] = set()

    for index, raw in enumerate(payload.rows):
        record_id = new_id("evr")
        errors: list[str] = []
        url_value = _first(raw, ("source_url", "sourceUrl", "url", "product_url"))
        try:
            source_url, domain = _source_url(url_value, controlled=controlled)
        except ValueError as exc:
            source_url, domain = str(url_value or ""), "invalid"
            errors.append(str(exc))
        claims = _identity_claims(raw)
        declared = {_identifier(claims[key]) for key in ("modelNumber", "sku", "gtin") if claims.get(key)}
        explicit_identifiers.update(declared)
        exact_match = bool(expected_values & declared) if expected_values else not request.exact_identity
        manufacturer_match = bool(expected_manufacturer and _words(claims.get("manufacturer")) == expected_manufacturer)
        is_authoritative = _authoritative(domain, list(request.authoritative_domains or []))
        if exact_match:
            exact_domains.add(domain)
        conflicting = declared - expected_values if request.exact_identity else set()
        if conflicting:
            errors.append(f"conflicting explicit model/SKU/GTIN: {', '.join(sorted(conflicting))}")
        if claims.get("manufacturer") and expected_manufacturer and not manufacturer_match:
            errors.append(f"manufacturer mismatch: {claims['manufacturer']}")
        bad_page = _bad_page(raw)
        if bad_page:
            errors.append(f"semantic error page detected: {bad_page}")
        images, image_errors = _images(raw, controlled=controlled)
        errors.extend(image_errors)
        declared_source_type = str(raw.get("source_type") or "corroborating")
        if declared_source_type not in {"manufacturer", "manufacturer_manual", "official_cad", "authorized_retailer", "corroborating", "category_prior"}:
            declared_source_type = "corroborating"
        source_type = "category_prior" if raw.get("category_prior") else "manufacturer" if is_authoritative else declared_source_type
        if source_type == "category_prior":
            exact_match = False
        identity_confidence = 1.0 if exact_match and manufacturer_match and is_authoritative else 0.9 if exact_match and manufacturer_match else 0.7 if exact_match else 0.0
        if exact_match and not errors:
            identity_ids.append(record_id)

        normalized = {
            "manufacturer": claims.get("manufacturer"),
            "modelNumber": claims.get("modelNumber"),
            "sku": claims.get("sku"),
            "gtin": claims.get("gtin"),
            "title": str(_first(raw, ("product_name", "name", "title")) or "")[:500],
            "materials": _first(raw, ("materials", "material")),
            "images": images,
            "manualUrls": list(raw.get("manual_urls") or raw.get("manuals") or [])[:10],
            "parts": list(raw.get("parts") or [])[:50],
            "identityExactMatch": exact_match,
            "identityConfidence": identity_confidence,
            "authoritative": is_authoritative,
        }
        priority = 3 if is_authoritative else 2 if source_type != "category_prior" else 1
        method = "manufacturer_spec" if is_authoritative else "reported_spec" if source_type != "category_prior" else "category_prior"
        confidence = 1.0 if is_authoritative else 0.85 if source_type != "category_prior" else 0.35
        for name in ("width", "height", "depth", "length"):
            value = _measurement(raw, name, _UNIT_SCALE)
            if value:
                candidate_properties.append({"name": name, "value": value[0], "unit": "m", "method": method, "confidence": confidence, "evidenceRecordIds": [record_id], "priority": priority})
        mass = _measurement(raw, "mass", _MASS_SCALE) or _measurement(raw, "weight", _MASS_SCALE)
        if mass:
            candidate_properties.append({"name": "mass", "value": mass[0], "unit": "kg", "method": method, "confidence": confidence, "evidenceRecordIds": [record_id], "priority": priority})
        material = _first(raw, ("material", "materials"))
        if isinstance(material, str) and material.strip():
            candidate_properties.append({"name": "material", "value": material.strip()[:300], "unit": None, "method": method, "confidence": confidence, "evidenceRecordIds": [record_id], "priority": priority})
        record_values.append(
            {
                "id": record_id,
                "request_id": request.id,
                "source_url": source_url,
                "source_type": source_type,
                "source_domain": domain,
                "retrieved_at": payload.retrieved_at or _now(),
                "collector_id": payload.collector_id,
                "collector_version": payload.collector_version,
                "content_sha256": _sha256(raw),
                "artifact_ref": raw_artifact_ref,
                "normalized": normalized,
                "identity_claims": claims,
                "quality_errors": errors,
                "license_metadata": {
                    "license": raw.get("license") or "unknown",
                    "redistribution": raw.get("redistribution") or "unknown",
                },
            }
        )

    conflicting_ids = explicit_identifiers - expected_values if request.exact_identity else set()
    if conflicting_ids:
        bundle_errors.append(f"mixed or conflicting SKU/model evidence: {', '.join(sorted(conflicting_ids))}")
    if request.exact_identity and not identity_ids:
        bundle_errors.append("no record passed exact identifier and manufacturer identity matching")

    selected_properties: list[dict[str, Any]] = []
    for name in sorted({item["name"] for item in candidate_properties}):
        candidates = sorted((item for item in candidate_properties if item["name"] == name), key=lambda item: item["priority"], reverse=True)
        selected = dict(candidates[0])
        if isinstance(selected["value"], float):
            comparable = [item for item in candidates if item["priority"] == selected["priority"] and isinstance(item["value"], float)]
            if any(abs(item["value"] - selected["value"]) / max(selected["value"], 1e-12) > (0.10 if name == "mass" else 0.05) for item in comparable[1:]):
                bundle_errors.append(f"unresolved authoritative conflict for {name}")
        selected.pop("priority", None)
        selected_properties.append(selected)

    best_identity_confidence = max((float(item["normalized"]["identityConfidence"]) for item in record_values if item["id"] in identity_ids), default=0.0)
    if best_identity_confidence == 0.9 and len(exact_domains) >= 2:
        best_identity_confidence = 0.95
    property_names = {item["name"] for item in selected_properties}
    valid_images = any(
        str(image.get("mimeType") or "").startswith("image/") and image.get("width") and image.get("height") and image.get("magicValidated")
        for record in record_values
        for image in record["normalized"].get("images", [])
    )
    available = {
        "manufacturer": bool(identity_ids and request.manufacturer),
        "exact_identifier": bool(identity_ids),
        "dimensions": len(property_names.intersection({"width", "height", "depth", "length"})) >= 2,
        "mass": "mass" in property_names,
        "material": "material" in property_names,
        "image": valid_images,
        "source_url": any(record["source_domain"] != "invalid" for record in record_values),
    }
    required = list(request.required_properties or [])
    completeness = sum(bool(available.get(name)) for name in required) / len(required)
    if completeness < _threshold("ROBOTWORLD_EVIDENCE_COMPLETENESS_THRESHOLD", DEFAULT_COMPLETENESS_THRESHOLD):
        missing = [name for name in required if not available.get(name)]
        bundle_errors.append(f"required evidence completeness {completeness:.3f} below threshold; missing: {', '.join(missing)}")
    if best_identity_confidence < _threshold("ROBOTWORLD_IDENTITY_CONFIDENCE_THRESHOLD", DEFAULT_IDENTITY_CONFIDENCE_THRESHOLD):
        bundle_errors.append(f"identity confidence {best_identity_confidence:.3f} below configured threshold")

    identity = ObjectIdentity(
        manufacturer=request.manufacturer or "unknown",
        model_number=request.model_number,
        sku=request.sku,
        gtin=request.gtin,
        category=request.category,
        exact=request.exact_identity and bool(identity_ids),
        confidence=best_identity_confidence,
        method="exact_identifier_plus_manufacturer_and_source_authority",
        evidence_record_ids=identity_ids,
        conflicts=[error for error in bundle_errors if "conflict" in error or "mixed" in error],
    ).model_dump(mode="json", by_alias=True)
    properties = [PropertyEstimate.model_validate(value).model_dump(mode="json", by_alias=True) for value in selected_properties]
    return record_values, identity, properties, completeness, best_identity_confidence, bundle_errors


def evaluate_rows(
    request: ObjectRequestRecord,
    rows: list[dict[str, Any]],
    *,
    collector_id: str,
    collector_version: str,
    source: str,
) -> dict[str, Any]:
    """Run the canonical semantic gate without mutating evidence state.

    Scraper golden/canary tests use this pure projection. Raw captured rows are
    stored in the artifact store by the repair service; this summary is safe to
    persist in catalog JSON and deliberately excludes generated record IDs.
    """
    payload = RecordedEvidenceImport(
        rows=rows,
        collectorId=collector_id,
        collectorVersion=collector_version,
        source=source,
        retrievedAt=_now(),
    )
    records, identity, properties, completeness, confidence, errors = _normalize_rows(
        request,
        payload,
        "scraper-repair/captured-rows.json",
    )
    record_errors = [
        {
            "sourceUrl": record["source_url"],
            "contentSha256": record["content_sha256"],
            "errors": list(record["quality_errors"]),
        }
        for record in records
    ]
    normalized_fields = sorted(
        {
            key
            for record in records
            for key, value in dict(record["normalized"]).items()
            if value not in (None, "", [], {})
        }
    )
    return {
        "passed": not errors,
        "completeness": completeness,
        "identityConfidence": confidence,
        "identity": {key: value for key, value in identity.items() if key != "evidenceRecordIds"},
        "properties": sorted({str(value.get("name")) for value in properties}),
        "normalizedFields": normalized_fields,
        "recordCount": len(records),
        "recordResults": record_errors,
        "errors": list(errors),
    }


async def normalize_recorded(
    request_id: str,
    payload: RecordedEvidenceImport,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    wire = payload.model_dump(mode="json", by_alias=True)
    encoded = _canonical_bytes(wire)
    if len(encoded) > MAX_RECORDED_PAYLOAD_BYTES:
        raise ValueError(f"Recorded evidence payload exceeds {MAX_RECORDED_PAYLOAD_BYTES} bytes.")
    command, reused = await command_store.start_command(
        kind="evidence.recorded.normalize",
        target_type="object_request",
        target_id=request_id,
        payload={"requestId": request_id, **wire},
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        return command_store.command_view(command, reused=True)
    async with SessionLocal() as session:
        request = await session.get(ObjectRequestRecord, request_id)
        if request is None:
            await command_store.finish_command(command.id, error="Object request not found.")
            raise KeyError(request_id)
        if request.lifecycle_state not in {"REQUESTED", "DISCOVERING"}:
            message = f"Object request is {request.lifecycle_state}; create a new revision before rebuilding evidence."
            await command_store.finish_command(command.id, error=message)
            raise EvidenceConflict(message)
        previous_state = request.lifecycle_state
        request.lifecycle_state = "DISCOVERING"
        request.updated_at = _now()
        if previous_state != "DISCOVERING":
            session.add(
                AuditEvent(
                    command_id=command.id,
                    entity_type="object_request",
                    entity_id=request.id,
                    action="evidence.discovery.start",
                    from_state=previous_state,
                    to_state="DISCOVERING",
                    detail={"source": payload.source, "rowCount": len(payload.rows)},
                    actor=actor,
                )
            )
        await session.commit()

    bundle_id = new_id("evb")
    root = (EVIDENCE_DIR / request_id / bundle_id).resolve()
    if EVIDENCE_DIR.resolve() not in root.parents:
        raise ValueError("Evidence artifact path escaped its root.")
    source_dir = root / "source"
    source_dir.mkdir(parents=True, exist_ok=False)
    raw_path = source_dir / "collector.json"
    raw_path.write_bytes(encoded)
    raw_artifact_ref = raw_path.relative_to(DATA_DIR).as_posix()
    record_values, identity, properties, completeness, confidence, errors = _normalize_rows(request, payload, raw_artifact_ref)
    lifecycle = "QUALITY_PASSED" if not errors else "QUALITY_FAILED"
    bundle_payload = {
        "requestId": request_id,
        "identity": identity,
        "evidenceRecordIds": [value["id"] for value in record_values],
        "properties": properties,
        "completeness": completeness,
        "identityConfidence": confidence,
        "validationErrors": errors,
        "rawContentSha256": hashlib.sha256(encoded).hexdigest(),
    }
    bundle_sha = _sha256(bundle_payload)
    report_path = root / "bundle.json"
    artifact_ref = report_path.relative_to(DATA_DIR).as_posix()

    async with SessionLocal() as session:
        request = await session.get(ObjectRequestRecord, request_id)
        assert request is not None
        existing = (
            await session.execute(select(EvidenceBundleRecord).where(EvidenceBundleRecord.bundle_sha256 == bundle_sha))
        ).scalar_one_or_none()
        if existing is not None:
            output = {"objectRequest": request_view(request), "bundle": bundle_view(existing), "records": []}
            await command_store.finish_command(command.id, output=output)
            command.output = command_store.json_safe(output)
            command.status = "SUCCEEDED"
            return command_store.command_view(command)
        rows: list[EvidenceRecordRow] = []
        for value in record_values:
            row = EvidenceRecordRow(**value)
            rows.append(row)
            session.add(row)
        revision = (
            await session.execute(select(EvidenceBundleRecord).where(EvidenceBundleRecord.request_id == request_id))
        ).scalars().all()
        bundle = EvidenceBundleRecord(
            id=bundle_id,
            request_id=request_id,
            revision=len(revision) + 1,
            lifecycle_state=lifecycle,
            identity=identity,
            evidence_ids=[value["id"] for value in record_values],
            properties=properties,
            completeness=completeness,
            identity_confidence=confidence,
            validation_errors=errors,
            bundle_sha256=bundle_sha,
            artifact_ref=artifact_ref,
            created_by=actor,
            source=payload.source,
        )
        session.add(bundle)
        from_state = request.lifecycle_state
        if lifecycle == "QUALITY_PASSED":
            request.lifecycle_state = "IDENTITY_VALIDATED"
            request.validation_errors = []
        else:
            request.validation_errors = errors
        request.updated_at = _now()
        session.add(
            AuditEvent(
                command_id=command.id,
                entity_type="evidence_bundle",
                entity_id=bundle.id,
                action="evidence.quality_gate",
                from_state=None,
                to_state=lifecycle,
                detail={"bundleSha256": bundle_sha, "completeness": completeness, "identityConfidence": confidence, "errors": errors},
                actor=actor,
            )
        )
        if request.lifecycle_state != from_state:
            session.add(
                AuditEvent(
                    command_id=command.id,
                    entity_type="object_request",
                    entity_id=request.id,
                    action="evidence.identity.validate",
                    from_state=from_state,
                    to_state=request.lifecycle_state,
                    detail={"bundleId": bundle.id, "bundleSha256": bundle_sha},
                    actor=actor,
                )
            )
        await session.commit()
        output = {
            "objectRequest": request_view(request),
            "bundle": bundle_view(bundle),
            "records": [evidence_record_view(row) for row in rows],
        }
    report_path.write_text(json.dumps(command_store.json_safe(output), indent=2), encoding="utf8")
    await command_store.finish_command(command.id, output=output)
    command.output = command_store.json_safe(output)
    command.status = "SUCCEEDED"
    return command_store.command_view(command)


async def list_requests() -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(ObjectRequestRecord).order_by(ObjectRequestRecord.created_at.desc()))).scalars().all()
    return [request_view(row) for row in rows]


async def get_request(request_id: str) -> dict[str, Any]:
    async with SessionLocal() as session:
        request = await session.get(ObjectRequestRecord, request_id)
        if request is None:
            raise KeyError(request_id)
        bundles = (
            await session.execute(select(EvidenceBundleRecord).where(EvidenceBundleRecord.request_id == request_id).order_by(EvidenceBundleRecord.revision.desc()))
        ).scalars().all()
        records = (
            await session.execute(select(EvidenceRecordRow).where(EvidenceRecordRow.request_id == request_id).order_by(EvidenceRecordRow.created_at))
        ).scalars().all()
    return {"objectRequest": request_view(request), "bundles": [bundle_view(row) for row in bundles], "records": [evidence_record_view(row) for row in records]}


async def get_bundle(bundle_id: str) -> dict[str, Any]:
    async with SessionLocal() as session:
        row = await session.get(EvidenceBundleRecord, bundle_id)
        if row is None:
            raise KeyError(bundle_id)
        records = (
            await session.execute(select(EvidenceRecordRow).where(EvidenceRecordRow.id.in_(row.evidence_ids or [])))
        ).scalars().all()
    return {"bundle": bundle_view(row), "records": [evidence_record_view(value) for value in records]}
