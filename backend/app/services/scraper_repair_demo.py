"""Controlled product page whose deliberate layout change exercises repair gates."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any

from ..contracts import (
    ObjectRequest,
    RecordedEvidenceImport,
    ScraperCandidateCase,
    ScraperCollectorVersionCreate,
    ScraperRepairCase,
    ScraperRepairCreate,
    ScraperRepairDraftSubmission,
)
from ..util import new_id
from . import evidence_catalog, scraper_repair


COLLECTOR_SCHEMA = {
    "type": "object",
    "required": [
        "source_url",
        "manufacturer",
        "model_number",
        "sku",
        "width_mm",
        "height_mm",
        "depth_mm",
        "mass_kg",
        "material",
    ],
    "properties": {
        "source_url": {"type": "string"},
        "manufacturer": {"type": "string"},
        "model_number": {"type": "string"},
        "sku": {"type": "string"},
        "product_name": {"type": "string"},
        "width_mm": {"type": "number"},
        "height_mm": {"type": "number"},
        "depth_mm": {"type": "number"},
        "mass_kg": {"type": "number"},
        "material": {"type": "string"},
    },
}


def page_html(layout: str) -> str:
    product = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "RobotWorld Bench Blender 500",
        "brand": {"@type": "Brand", "name": "RobotWorld Labs"},
        "model": "RW-B500",
        "sku": "RW-B500-BLU",
        "width_mm": 180,
        "height_mm": 420,
        "depth_mm": 180,
        "mass_kg": 2.5,
        "material": "ABS plastic and stainless steel",
    }
    structured = json.dumps(product, separators=(",", ":"))
    if layout == "v1":
        body = (
            '<article data-product="true" data-manufacturer="RobotWorld Labs" '
            'data-model="RW-B500" data-sku="RW-B500-BLU" data-width-mm="180" '
            'data-height-mm="420" data-depth-mm="180" data-mass-kg="2.5" '
            'data-material="ABS plastic and stainless steel">'
            "<h1>RobotWorld Bench Blender 500</h1></article>"
            f'<script type="application/ld+json">{structured}</script>'
        )
    elif layout in {"v2", "v2-canary"}:
        marker = "canary" if layout == "v2-canary" else "production"
        body = (
            f'<main class="product-shell" data-layout="{marker}"><section class="product-copy">'
            "<h1>RobotWorld Bench Blender 500</h1><p>Exact model RW-B500</p></section></main>"
            f'<script type="application/ld+json">{structured}</script>'
        )
    else:
        raise KeyError(layout)
    return f"<!doctype html><html><head><title>Controlled product</title></head><body>{body}</body></html>"


class _LegacyAttributeExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attributes: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "article" and values.get("data-product") == "true":
            self.attributes = values


def extract_legacy(html: str, url: str) -> list[dict[str, Any]]:
    parser = _LegacyAttributeExtractor()
    parser.feed(html)
    attrs = parser.attributes
    if not attrs:
        return [{"source_url": url, "title": "RobotWorld Bench Blender 500"}]
    return [
        {
            "source_url": url,
            "manufacturer": attrs.get("data-manufacturer"),
            "model_number": attrs.get("data-model"),
            "sku": attrs.get("data-sku"),
            "product_name": "RobotWorld Bench Blender 500",
            "width_mm": attrs.get("data-width-mm"),
            "height_mm": attrs.get("data-height-mm"),
            "depth_mm": attrs.get("data-depth-mm"),
            "mass_kg": attrs.get("data-mass-kg"),
            "material": attrs.get("data-material"),
        }
    ]


def extract_jsonld_candidate(html: str, url: str) -> list[dict[str, Any]]:
    match = re.search(
        r'<script\s+type=["\']application/ld\+json["\']>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return [{"source_url": url, "error": "missing Product JSON-LD"}]
    payload = json.loads(match.group(1))
    brand = payload.get("brand")
    manufacturer = brand.get("name") if isinstance(brand, dict) else brand
    return [
        {
            "source_url": url,
            "manufacturer": manufacturer,
            "model_number": payload.get("model"),
            "sku": payload.get("sku"),
            "product_name": payload.get("name"),
            "width_mm": payload.get("width_mm"),
            "height_mm": payload.get("height_mm"),
            "depth_mm": payload.get("depth_mm"),
            "mass_kg": payload.get("mass_kg"),
            "material": payload.get("material"),
        }
    ]


async def run_demo(*, automatic_promotion: bool = False, actor: str = "user") -> dict[str, Any]:
    demo_id = new_id("repairdemo")
    collector_id = f"c_robotworld_controlled_{demo_id.split('_', 1)[1]}"
    urls = {
        "legacy": "https://robotworld.test/products/rw-b500?layout=v1",
        "changed": "https://robotworld.test/products/rw-b500?layout=v2",
        "canary": "https://robotworld.test/products/rw-b500?layout=v2-canary",
    }
    request_envelope = await evidence_catalog.create_request(
        ObjectRequest(
            requestedName="RobotWorld Bench Blender 500 blue",
            manufacturer="RobotWorld Labs",
            modelNumber="RW-B500",
            sku="RW-B500-BLU",
            category="countertop_blender",
            exactIdentity=True,
            authoritativeDomains=["robotworld.test"],
            requiredProperties=["manufacturer", "exact_identifier", "dimensions", "mass", "material", "source_url"],
        ),
        idempotency_key=f"{demo_id}:object-request",
        actor=actor,
    )
    request_id = str(request_envelope["result"]["objectRequest"]["id"])
    version_envelope = await scraper_repair.register_collector_version(
        ScraperCollectorVersionCreate(
            collectorId=collector_id,
            versionLabel="controlled-layout-v1",
            outputSchema=COLLECTOR_SCHEMA,
            extractorRevision="legacy-data-attributes-v1",
            providerMetadata={"controlled": True, "executesDownloadedCode": False},
            activate=True,
        ),
        idempotency_key=f"{demo_id}:collector-v1",
        actor=actor,
    )
    active_version = version_envelope["result"]["collectorVersion"]
    broken_rows = extract_legacy(page_html("v2"), urls["changed"])
    failure_envelope = await evidence_catalog.normalize_recorded(
        request_id,
        RecordedEvidenceImport(
            rows=broken_rows,
            collectorId=collector_id,
            collectorVersion="controlled-layout-v1",
            source="controlled_fixture",
        ),
        idempotency_key=f"{demo_id}:semantic-break",
        actor=actor,
    )
    failure_bundle = failure_envelope["result"]["bundle"]
    if failure_bundle["lifecycleState"] != "QUALITY_FAILED":
        raise scraper_repair.ScraperRepairError("Controlled legacy extractor did not produce the expected semantic failure.")
    golden_cases = [
        ScraperRepairCase(
            name="legacy_layout_regression",
            url=urls["legacy"],
            baselineRows=extract_legacy(page_html("v1"), urls["legacy"]),
        ),
        ScraperRepairCase(
            name="changed_layout_break",
            url=urls["changed"],
            baselineRows=broken_rows,
        ),
    ]
    canary_cases = [
        ScraperRepairCase(
            name="changed_layout_canary",
            url=urls["canary"],
            baselineRows=extract_legacy(page_html("v2-canary"), urls["canary"]),
        )
    ]
    repair_envelope = await scraper_repair.create_repair_run(
        ScraperRepairCreate(
            collectorId=collector_id,
            activeVersionId=active_version["id"],
            objectRequestId=request_id,
            failureBundleId=failure_bundle["id"],
            providerMode="controlled_fixture",
            goldenCases=golden_cases,
            canaryCases=canary_cases,
            automaticPromotion=automatic_promotion,
            allowSchemaChange=False,
            maxAttempts=1,
        ),
        idempotency_key=f"{demo_id}:repair-request",
        actor=actor,
    )
    repair_run = repair_envelope["result"]["repairRun"]
    draft_envelope = await scraper_repair.submit_draft(
        repair_run["id"],
        ScraperRepairDraftSubmission(
            candidateVersionLabel="controlled-jsonld-v2",
            extractorRevision="product-jsonld-v2",
            outputSchema=COLLECTOR_SCHEMA,
            providerMetadata={"controlled": True, "executesDownloadedCode": False},
            goldenOutputs=[
                ScraperCandidateCase(
                    name=case.name,
                    rows=extract_jsonld_candidate(page_html("v1" if index == 0 else "v2"), case.url),
                )
                for index, case in enumerate(golden_cases)
            ],
            canaryOutputs=[
                ScraperCandidateCase(
                    name=canary_cases[0].name,
                    rows=extract_jsonld_candidate(page_html("v2-canary"), canary_cases[0].url),
                )
            ],
        ),
        idempotency_key=f"{demo_id}:candidate-draft",
        actor=actor,
    )
    tested_envelope = await scraper_repair.run_quality_tests(
        repair_run["id"],
        idempotency_key=f"{demo_id}:golden-canary",
        actor=actor,
    )
    return {
        "schemaVersion": "robotworld.controlled-scraper-repair-demo.v1",
        "demoId": demo_id,
        "pagePaths": {
            "legacy": "/api/scraper-repair/demo/page/v1",
            "changed": "/api/scraper-repair/demo/page/v2",
            "canary": "/api/scraper-repair/demo/page/v2-canary",
        },
        "collectorVersion": active_version,
        "failureBundle": failure_bundle,
        "repairRequest": repair_envelope["result"]["repairRun"],
        "candidateDraft": draft_envelope["result"]["candidateCollectorVersion"],
        "repairRun": tested_envelope["result"]["repairRun"],
    }
