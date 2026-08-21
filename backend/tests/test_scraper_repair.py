from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import DATA_DIR
from app.contracts import ScraperRepairCreate
from app.main import app
from app.services import brightdata
from app.services.scraper_repair_demo import COLLECTOR_SCHEMA, extract_jsonld_candidate, extract_legacy, page_html


def test_controlled_layout_break_golden_canary_promotion_and_rollback() -> None:
    with TestClient(app) as client:
        legacy_page = client.get("/api/scraper-repair/demo/page/v1")
        changed_page = client.get("/api/scraper-repair/demo/page/v2")
        assert legacy_page.status_code == 200
        assert changed_page.status_code == 200
        assert 'data-product="true"' in legacy_page.text
        assert 'data-product="true"' not in changed_page.text
        assert 'type="application/ld+json"' in changed_page.text

        demo_response = client.post("/api/scraper-repair/demo", json={"automaticPromotion": False})
        assert demo_response.status_code == 201, demo_response.text
        demo = demo_response.json()
        failure_bundle = demo["failureBundle"]
        run = demo["repairRun"]
        original = demo["collectorVersion"]
        candidate = demo["candidateDraft"]

        assert failure_bundle["lifecycleState"] == "QUALITY_FAILED"
        assert any("missing" in error or "identity" in error for error in failure_bundle["validationErrors"])
        assert run["lifecycleState"] == "AWAITING_POLICY_DECISION"
        assert run["lastKnownGoodVersionId"] == original["id"]
        assert run["candidateVersionId"] == candidate["id"]
        assert run["schemaDiff"]["compatible"] is True
        assert run["goldenReport"]["allPassed"] is True
        assert run["goldenReport"]["caseCount"] == 2
        assert run["canaryReport"]["allPassed"] is True
        assert run["canaryReport"]["caseCount"] == 1
        assert "exact_identifier" in run["repairPrompt"]
        assert "untrusted data" in run["repairPrompt"]
        assert (DATA_DIR / run["testArtifactRef"]).is_file()
        assert (DATA_DIR / run["candidateArtifactRef"]).is_file()

        versions = client.get(
            "/api/scraper-collector-versions",
            params={"collectorId": run["collectorId"]},
        ).json()["collectorVersions"]
        active_before = [version for version in versions if version["active"]]
        assert [version["id"] for version in active_before] == [original["id"]]
        assert next(version for version in versions if version["id"] == candidate["id"])["lifecycleState"] == "CANDIDATE"

        promote = client.post(
            f"/api/scraper-repair-runs/{run['id']}/decision",
            headers={"Idempotency-Key": "controlled-repair-promote"},
            json={"decision": "PROMOTE", "reason": "All fixed golden and canary cases passed."},
        )
        assert promote.status_code == 201, promote.text
        promoted = promote.json()["result"]
        assert promoted["repairRun"]["lifecycleState"] == "PROMOTED"
        assert promoted["activeCollectorVersion"]["id"] == candidate["id"]
        assert promoted["activeCollectorVersion"]["active"] is True

        replay = client.post(
            f"/api/scraper-repair-runs/{run['id']}/decision",
            headers={"Idempotency-Key": "controlled-repair-promote"},
            json={"decision": "PROMOTE", "reason": "All fixed golden and canary cases passed."},
        )
        assert replay.status_code == 201
        assert replay.json()["reused"] is True

        rollback = client.post(
            f"/api/scraper-repair-runs/{run['id']}/rollback",
            headers={"Idempotency-Key": "controlled-repair-rollback"},
            json={"reason": "Exercise last-known-good recovery.", "providerRollbackConfirmed": False},
        )
        assert rollback.status_code == 201, rollback.text
        rolled_back = rollback.json()["result"]
        assert rolled_back["repairRun"]["lifecycleState"] == "ROLLED_BACK"
        assert rolled_back["activeCollectorVersion"]["id"] == original["id"]
        assert rolled_back["activeCollectorVersion"]["active"] is True
        assert rolled_back["rolledBackCollectorVersion"]["id"] == candidate["id"]
        assert rolled_back["rolledBackCollectorVersion"]["lifecycleState"] == "ROLLED_BACK"

        audit = client.get(
            "/api/audit",
            params={"entity_type": "scraper_repair_run", "entity_id": run["id"], "limit": 100},
        ).json()["events"]
        transitions = {(event["fromState"], event["toState"]) for event in audit}
        assert transitions >= {
            (None, "COLLECTING"),
            ("COLLECTING", "QUALITY_FAILED"),
            ("QUALITY_FAILED", "REPAIR_REQUESTED"),
            ("REPAIR_REQUESTED", "DRAFT_READY"),
            ("DRAFT_READY", "GOLDEN_TESTING"),
            ("GOLDEN_TESTING", "CANARY_TESTING"),
            ("CANARY_TESTING", "AWAITING_POLICY_DECISION"),
            ("AWAITING_POLICY_DECISION", "PROMOTED"),
            ("PROMOTED", "ROLLED_BACK"),
        }

        urls = {
            "legacy": "https://robotworld.test/products/rw-b500?layout=v1-schema-check",
            "changed": "https://robotworld.test/products/rw-b500?layout=v2-schema-check",
            "canary": "https://robotworld.test/products/rw-b500?layout=v2-canary-schema-check",
        }
        schema_repair = client.post(
            "/api/scraper-repair-runs",
            headers={"Idempotency-Key": "controlled-unapproved-schema-repair"},
            json={
                "collectorId": run["collectorId"],
                "activeVersionId": original["id"],
                "objectRequestId": run["objectRequestId"],
                "failureBundleId": run["failureBundleId"],
                "providerMode": "controlled_fixture",
                "goldenCases": [
                    {
                        "name": "legacy_schema_case",
                        "url": urls["legacy"],
                        "baselineRows": extract_legacy(page_html("v1"), urls["legacy"]),
                    },
                    {
                        "name": "changed_schema_case",
                        "url": urls["changed"],
                        "baselineRows": extract_legacy(page_html("v2"), urls["changed"]),
                    },
                ],
                "canaryCases": [
                    {
                        "name": "canary_schema_case",
                        "url": urls["canary"],
                        "baselineRows": extract_legacy(page_html("v2-canary"), urls["canary"]),
                    }
                ],
                "automaticPromotion": False,
                "allowSchemaChange": False,
                "maxAttempts": 1,
            },
        )
        assert schema_repair.status_code == 201, schema_repair.text
        schema_run = schema_repair.json()["result"]["repairRun"]
        changed_schema = {
            **COLLECTOR_SCHEMA,
            "properties": {**COLLECTOR_SCHEMA["properties"], "unapproved_new_field": {"type": "string"}},
        }
        draft = client.post(
            f"/api/scraper-repair-runs/{schema_run['id']}/draft",
            headers={"Idempotency-Key": "controlled-unapproved-schema-draft"},
            json={
                "candidateVersionLabel": "controlled-jsonld-schema-change",
                "extractorRevision": "product-jsonld-unapproved-schema",
                "outputSchema": changed_schema,
                "goldenOutputs": [
                    {
                        "name": "legacy_schema_case",
                        "rows": extract_jsonld_candidate(page_html("v1"), urls["legacy"]),
                    },
                    {
                        "name": "changed_schema_case",
                        "rows": extract_jsonld_candidate(page_html("v2"), urls["changed"]),
                    },
                ],
                "canaryOutputs": [
                    {
                        "name": "canary_schema_case",
                        "rows": extract_jsonld_candidate(page_html("v2-canary"), urls["canary"]),
                    }
                ],
            },
        )
        assert draft.status_code == 201, draft.text
        rejected = client.post(
            f"/api/scraper-repair-runs/{schema_run['id']}/test",
            headers={"Idempotency-Key": "controlled-unapproved-schema-test"},
        )
        assert rejected.status_code == 201, rejected.text
        rejected_run = rejected.json()["result"]["repairRun"]
        assert rejected_run["lifecycleState"] == "REJECTED"
        assert rejected_run["schemaDiff"]["compatible"] is False
        assert rejected_run["schemaDiff"]["addedFields"] == ["unapproved_new_field"]
        assert any(
            "schema changed without policy approval" in error
            for case in rejected_run["goldenReport"]["cases"]
            for error in case["errors"]
        )
        active_after_rejection = [
            version
            for version in client.get(
                "/api/scraper-collector-versions",
                params={"collectorId": run["collectorId"]},
            ).json()["collectorVersions"]
            if version["active"]
        ]
        assert [version["id"] for version in active_after_rejection] == [original["id"]]


def test_live_provider_cannot_auto_promote_and_case_names_are_unique() -> None:
    common = {
        "collectorId": "c_contract_repair",
        "activeVersionId": "scraperver_active",
        "objectRequestId": "objreq_repair",
        "failureBundleId": "evb_failed",
        "goldenCases": [{"name": "same", "url": "https://example.com/a", "baselineRows": [{"url": "https://example.com/a"}]}],
        "canaryCases": [{"name": "canary", "url": "https://example.com/b", "baselineRows": [{"url": "https://example.com/b"}]}],
    }
    try:
        ScraperRepairCreate(**common, providerMode="brightdata_live", automaticPromotion=True)
    except ValidationError as exc:
        assert "explicit promotion decision" in str(exc)
    else:
        raise AssertionError("live provider automatic promotion must fail validation")

    duplicate = dict(common)
    duplicate["canaryCases"] = [{"name": "same", "url": "https://example.com/b", "baselineRows": [{"url": "https://example.com/b"}]}]
    try:
        ScraperRepairCreate(**duplicate)
    except ValidationError as exc:
        assert "unique names" in str(exc)
    else:
        raise AssertionError("duplicate golden/canary names must fail validation")


def test_legacy_source_repair_bypass_is_disabled() -> None:
    with TestClient(app) as client:
        repair = client.post(
            "/api/sources/does-not-matter/repair",
            json={"prompt": "Repair a legacy collector without canonical validation."},
        )
        approval = client.post("/api/sources/does-not-matter/repair/approve")
    assert repair.status_code == 410
    assert "golden/canary" in repair.json()["detail"]
    assert approval.status_code == 410
    assert "canonical decision audit" in approval.json()["detail"]


def test_live_provider_failure_exhausts_attempt_budget_without_candidate(monkeypatch) -> None:
    async def unavailable(*_args, **_kwargs):
        raise brightdata.NotConfigured()

    monkeypatch.setattr(brightdata, "dca_heal", unavailable)
    with TestClient(app) as client:
        demo = client.post("/api/scraper-repair/demo", json={"automaticPromotion": False}).json()
        source_run = demo["repairRun"]
        urls = {
            "legacy": "https://robotworld.test/products/rw-b500?layout=v1-live-provider",
            "changed": "https://robotworld.test/products/rw-b500?layout=v2-live-provider",
            "canary": "https://robotworld.test/products/rw-b500?layout=v2-canary-live-provider",
        }
        rejected_demo = client.post(
            f"/api/scraper-repair-runs/{source_run['id']}/decision",
            headers={"Idempotency-Key": "live-provider-exhaustion-close-demo"},
            json={"decision": "REJECT", "reason": "Close the controlled candidate before testing provider exhaustion."},
        )
        assert rejected_demo.status_code == 201, rejected_demo.text
        created = client.post(
            "/api/scraper-repair-runs",
            headers={"Idempotency-Key": "live-provider-exhaustion-create"},
            json={
                "collectorId": source_run["collectorId"],
                "activeVersionId": demo["collectorVersion"]["id"],
                "objectRequestId": source_run["objectRequestId"],
                "failureBundleId": source_run["failureBundleId"],
                "providerMode": "brightdata_live",
                "goldenCases": [
                    {
                        "name": "legacy_live_provider",
                        "url": urls["legacy"],
                        "baselineRows": extract_legacy(page_html("v1"), urls["legacy"]),
                    },
                    {
                        "name": "changed_live_provider",
                        "url": urls["changed"],
                        "baselineRows": extract_legacy(page_html("v2"), urls["changed"]),
                    },
                ],
                "canaryCases": [
                    {
                        "name": "canary_live_provider",
                        "url": urls["canary"],
                        "baselineRows": extract_legacy(page_html("v2-canary"), urls["canary"]),
                    }
                ],
                "automaticPromotion": False,
                "allowSchemaChange": False,
                "maxAttempts": 1,
            },
        )
        assert created.status_code == 201, created.text
        repair_run_id = created.json()["result"]["repairRun"]["id"]
        triggered = client.post(
            f"/api/scraper-repair-runs/{repair_run_id}/provider-request",
            headers={"Idempotency-Key": "live-provider-exhaustion-trigger"},
        )
        assert triggered.status_code == 422
        persisted = client.get(f"/api/scraper-repair-runs/{repair_run_id}").json()["repairRun"]
        assert persisted["lifecycleState"] == "EXHAUSTED"
        assert persisted["attempt"] == persisted["maxAttempts"] == 1
        assert persisted["candidateVersionId"] is None
        assert "BRIGHTDATA_API_TOKEN" in persisted["error"]
