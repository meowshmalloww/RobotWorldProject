from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import CommandExecution, EvidenceCollectionRunRecord
from app.services import brightdata, evidence_collection


def _request(client: TestClient, suffix: str) -> dict:
    response = client.post(
        "/api/evidence/requests",
        headers={"Idempotency-Key": f"provider-request-{suffix}"},
        json={
            "requestedName": "Acme Kitchen Blender 500 blue",
            "manufacturer": "Acme Kitchen",
            "modelNumber": "BLD-500",
            "sku": "AC-BLD500-BLU",
            "category": "countertop_blender",
            "exactIdentity": True,
            "authoritativeDomains": ["acmekitchen.example"],
            "requiredProperties": ["manufacturer", "exact_identifier", "dimensions", "mass", "material", "source_url"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["result"]["objectRequest"]


def _provider_rows() -> list[dict]:
    return [
        {
            "source_url": "https://acmekitchen.example/products/bld-500-blue",
            "source_type": "manufacturer_page",
            "manufacturer": "Acme Kitchen",
            "model_number": "BLD-500",
            "sku": "AC-BLD500-BLU",
            "width_mm": 180,
            "height_cm": 42,
            "depth_cm": 18,
            "mass_kg": 2.5,
            "material": "ABS plastic and stainless steel",
        }
    ]


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/evidence/collections/{run_id}")
        assert response.status_code == 200
        row = response.json()["collectionRun"]
        if row["lifecycleState"] not in {"QUEUED", "STARTING", "RUNNING"}:
            return row
        time.sleep(0.01)
    raise AssertionError(f"collection {run_id} did not reach a terminal state")


def test_durable_collection_normalizes_real_provider_boundary(monkeypatch) -> None:
    triggers: list[tuple[str, list[dict]]] = []

    async def trigger(collector: str, inputs: list[dict]) -> str:
        triggers.append((collector, inputs))
        return "j_exact_product_001"

    async def dataset(snapshot_id: str):
        assert snapshot_id == "j_exact_product_001"
        return True, _provider_rows()

    monkeypatch.setattr(brightdata, "dca_trigger", trigger)
    monkeypatch.setattr(brightdata, "dca_dataset", dataset)
    with TestClient(app) as client:
        request = _request(client, "durable-success")
        body = {
            "collectorId": "c_exact_products",
            "collectorVersion": "v17",
            "inputUrls": ["https://acmekitchen.example/products/bld-500-blue"],
            "timeoutSeconds": 30,
        }
        response = client.post(
            f"/api/evidence/requests/{request['id']}/collections",
            headers={"Idempotency-Key": "provider-collect-success"},
            json=body,
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["result"]["collectionRun"]["id"]
        terminal = _wait_terminal(client, run_id)
        assert terminal["lifecycleState"] == "SUCCEEDED"
        assert terminal["snapshotId"] == "j_exact_product_001"
        assert terminal["bundleId"].startswith("evb_")
        assert terminal["providerAttempt"] == 1
        assert terminal["normalizationAttempt"] == 1
        assert triggers == [("c_exact_products", [{"url": "https://acmekitchen.example/products/bld-500-blue"}])]

        detail = client.get(f"/api/evidence/requests/{request['id']}").json()
        assert detail["objectRequest"]["lifecycleState"] == "IDENTITY_VALIDATED"
        assert detail["bundles"][0]["lifecycleState"] == "QUALITY_PASSED"

        replay = client.post(
            f"/api/evidence/requests/{request['id']}/collections",
            headers={"Idempotency-Key": "provider-collect-success"},
            json=body,
        )
        assert replay.status_code == 202
        assert replay.json()["reused"] is True
        assert replay.json()["result"]["collectionRun"]["id"] == run_id
        assert len(triggers) == 1


def test_missing_provider_token_is_explicit_terminal_failure(monkeypatch) -> None:
    async def trigger(collector: str, inputs: list[dict]) -> str:
        raise brightdata.NotConfigured()

    monkeypatch.setattr(brightdata, "dca_trigger", trigger)
    with TestClient(app) as client:
        request = _request(client, "no-token")
        response = client.post(
            f"/api/evidence/requests/{request['id']}/collections",
            json={
                "collectorId": "c_exact_products",
                "inputUrls": ["https://acmekitchen.example/products/bld-500-blue"],
                "timeoutSeconds": 30,
            },
        )
        assert response.status_code == 202
        terminal = _wait_terminal(client, response.json()["result"]["collectionRun"]["id"])
        assert terminal["lifecycleState"] == "FAILED"
        assert "BRIGHTDATA_API_TOKEN" in terminal["error"]
        assert terminal["snapshotId"] is None


def test_restart_resumes_persisted_snapshot_without_retrigger(monkeypatch) -> None:
    trigger_called = False

    async def trigger(collector: str, inputs: list[dict]) -> str:
        nonlocal trigger_called
        trigger_called = True
        return "j_must_not_be_created"

    async def dataset(snapshot_id: str):
        assert snapshot_id == "j_persisted_snapshot"
        return True, _provider_rows()

    monkeypatch.setattr(brightdata, "dca_trigger", trigger)
    monkeypatch.setattr(brightdata, "dca_dataset", dataset)
    with TestClient(app) as client:
        request = _request(client, "resume-snapshot")

        async def seed() -> str:
            async with SessionLocal() as session:
                command = CommandExecution(
                    id="cmd_resume_snapshot",
                    kind="evidence.brightdata.collect",
                    target_type="object_request",
                    target_id=request["id"],
                    status="RUNNING",
                    input={},
                    output={},
                    actor="test",
                )
                row = EvidenceCollectionRunRecord(
                    id="evcollect_resume_snapshot",
                    request_id=request["id"],
                    collector_id="c_exact_products",
                    collector_version="v17",
                    input_urls=["https://acmekitchen.example/products/bld-500-blue"],
                    lifecycle_state="RUNNING",
                    snapshot_id="j_persisted_snapshot",
                    command_id=command.id,
                    provider_attempt=1,
                    timeout_seconds=30,
                    started_at=evidence_collection._now(),
                    created_by="test",
                )
                session.add(command)
                session.add(row)
                await session.commit()
                return row.id

        run_id = asyncio.run(seed())
        asyncio.run(evidence_collection._run(run_id))
        terminal = client.get(f"/api/evidence/collections/{run_id}").json()["collectionRun"]
        assert terminal["lifecycleState"] == "SUCCEEDED"
        assert terminal["bundleId"].startswith("evb_")
        assert trigger_called is False


def test_restart_does_not_duplicate_uncertain_provider_trigger(monkeypatch) -> None:
    trigger_called = False

    async def trigger(collector: str, inputs: list[dict]) -> str:
        nonlocal trigger_called
        trigger_called = True
        return "j_duplicate"

    monkeypatch.setattr(brightdata, "dca_trigger", trigger)
    with TestClient(app) as client:
        request = _request(client, "uncertain-trigger")

        async def seed() -> str:
            async with SessionLocal() as session:
                command = CommandExecution(
                    id="cmd_uncertain_trigger",
                    kind="evidence.brightdata.collect",
                    target_type="object_request",
                    target_id=request["id"],
                    status="RUNNING",
                    input={},
                    output={},
                    actor="test",
                )
                row = EvidenceCollectionRunRecord(
                    id="evcollect_uncertain_trigger",
                    request_id=request["id"],
                    collector_id="c_exact_products",
                    input_urls=["https://acmekitchen.example/products/bld-500-blue"],
                    lifecycle_state="STARTING",
                    snapshot_id=None,
                    command_id=command.id,
                    provider_attempt=1,
                    timeout_seconds=30,
                    started_at=evidence_collection._now(),
                    created_by="test",
                )
                session.add(command)
                session.add(row)
                await session.commit()
                return row.id

        run_id = asyncio.run(seed())
        asyncio.run(evidence_collection._run(run_id))
        terminal = client.get(f"/api/evidence/collections/{run_id}").json()["collectionRun"]
        assert terminal["lifecycleState"] == "FAILED"
        assert "uncertain" in terminal["error"]
        assert trigger_called is False

        async def command_state() -> tuple[str, str | None]:
            async with SessionLocal() as session:
                row = (
                    await session.execute(select(CommandExecution).where(CommandExecution.id == "cmd_uncertain_trigger"))
                ).scalar_one()
                return row.status, row.error

        status, error = asyncio.run(command_state())
        assert status == "FAILED"
        assert error and "uncertain" in error
