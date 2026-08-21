from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import DATA_DIR
from app.main import app


def _request(client: TestClient, suffix: str, *, required: list[str] | None = None) -> dict:
    response = client.post(
        "/api/evidence/requests",
        headers={"Idempotency-Key": f"evidence-request-{suffix}"},
        json={
            "requestedName": "Acme Kitchen Blender 500 blue",
            "manufacturer": "Acme Kitchen",
            "modelNumber": "BLD-500",
            "sku": "AC-BLD500-BLU",
            "category": "countertop_blender",
            "exactIdentity": True,
            "authoritativeDomains": ["acmekitchen.example"],
            "requiredProperties": required or ["manufacturer", "exact_identifier", "dimensions", "mass", "material", "source_url"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["result"]["objectRequest"]


def _good_rows() -> list[dict]:
    return [
        {
            "source_url": "https://acmekitchen.example/products/bld-500-blue",
            "manufacturer": "Acme Kitchen",
            "model_number": "BLD-500",
            "sku": "AC-BLD500-BLU",
            "product_name": "Acme Kitchen Blender 500 - Blue",
            "width_mm": 180,
            "height_cm": 42,
            "depth_in": 7.1,
            "mass_kg": 2.5,
            "material": "ABS plastic and stainless steel",
            "license": "manufacturer terms - metadata only",
            "redistribution": "review_required",
            "images": [
                {
                    "url": "https://acmekitchen.example/media/bld-500-front.png",
                    "view": "front",
                    "mime_type": "image/png",
                    "width": 1200,
                    "height": 1200,
                    "magic_validated": True,
                    "content_sha256": "a" * 64,
                }
            ],
        },
        {
            "source_url": "https://retailer.example/acme-bld-500-blue",
            "source_type": "authorized_retailer",
            "manufacturer": "Acme Kitchen",
            "model": "BLD-500",
            "sku": "AC-BLD500-BLU",
            "width_cm": 18.0,
            "height_cm": 42.0,
            "depth_cm": 18.0,
            "weight": "5.51 lb",
            "material": "ABS plastic and stainless steel",
        },
    ]


def test_recorded_exact_product_builds_immutable_quality_passed_bundle() -> None:
    with TestClient(app) as client:
        request = _request(client, "exact-pass")
        payload = {
            "source": "recorded_brightdata",
            "collectorId": "c_acme_products",
            "collectorVersion": "v17",
            "rows": _good_rows(),
        }
        response = client.post(
            f"/api/evidence/requests/{request['id']}/normalize-recorded",
            headers={"Idempotency-Key": "evidence-normalize-exact-pass"},
            json=payload,
        )
        assert response.status_code == 201, response.text
        output = response.json()["result"]
        bundle = output["bundle"]
        assert output["objectRequest"]["lifecycleState"] == "IDENTITY_VALIDATED"
        assert bundle["lifecycleState"] == "QUALITY_PASSED"
        assert bundle["identity"]["exact"] is True
        assert bundle["identityConfidence"] == 1.0
        assert bundle["completeness"] == 1.0
        assert bundle["validationErrors"] == []
        assert len(bundle["bundleSha256"]) == 64
        assert len(output["records"]) == 2
        assert output["records"][0]["collectorId"] == "c_acme_products"
        assert output["records"][0]["qualityErrors"] == []

        properties = {value["name"]: value for value in bundle["properties"]}
        assert properties["width"]["value"] == 0.18
        assert properties["height"]["value"] == 0.42
        assert abs(properties["depth"]["value"] - 0.18034) < 1e-8
        assert properties["mass"]["value"] == 2.5
        assert properties["mass"]["method"] == "manufacturer_spec"
        assert properties["mass"]["evidenceRecordIds"] == [output["records"][0]["id"]]
        assert (DATA_DIR / bundle["artifactRef"]).is_file()
        assert (DATA_DIR / output["records"][0]["artifactRef"]).is_file()

        replay = client.post(
            f"/api/evidence/requests/{request['id']}/normalize-recorded",
            headers={"Idempotency-Key": "evidence-normalize-exact-pass"},
            json=payload,
        )
        assert replay.status_code == 201
        assert replay.json()["reused"] is True
        assert replay.json()["commandId"] == response.json()["commandId"]

        detail = client.get(f"/api/evidence/requests/{request['id']}")
        assert detail.status_code == 200
        assert detail.json()["bundles"][0]["id"] == bundle["id"]
        assert len(detail.json()["records"]) == 2


def test_mixed_sku_evidence_is_rejected_even_when_other_fields_are_complete() -> None:
    with TestClient(app) as client:
        request = _request(client, "mixed-sku")
        rows = _good_rows()
        rows[1]["sku"] = "AC-BLD700-RED"
        rows[1]["model"] = "BLD-700"
        response = client.post(
            f"/api/evidence/requests/{request['id']}/normalize-recorded",
            json={"source": "recorded_brightdata", "collectorId": "c_acme_products", "rows": rows},
        )
        assert response.status_code == 201, response.text
        output = response.json()["result"]
        assert output["bundle"]["lifecycleState"] == "QUALITY_FAILED"
        assert output["objectRequest"]["lifecycleState"] == "DISCOVERING"
        assert any("mixed or conflicting SKU/model" in error for error in output["bundle"]["validationErrors"])
        assert any("conflicting explicit" in error for error in output["records"][1]["qualityErrors"])


def test_captcha_and_category_prior_are_data_not_identity_evidence() -> None:
    with TestClient(app) as client:
        request = _request(client, "untrusted-data", required=["manufacturer", "exact_identifier", "source_url"])
        response = client.post(
            f"/api/evidence/requests/{request['id']}/normalize-recorded",
            json={
                "source": "recorded_brightdata",
                "collectorId": "c_broken_layout",
                "rows": [
                    {
                        "source_url": "https://acmekitchen.example/products/bld-500-blue",
                        "manufacturer": "Acme Kitchen",
                        "model_number": "BLD-500",
                        "sku": "AC-BLD500-BLU",
                        "title": "Verify you are human - CAPTCHA",
                    },
                    {
                        "source_url": "https://category-priors.example/blenders",
                        "category_prior": True,
                        "manufacturer": "Acme Kitchen",
                        "model_number": "BLD-500",
                        "width_cm": 20,
                        "height_cm": 40,
                    },
                ],
            },
        )
        assert response.status_code == 201
        output = response.json()["result"]
        assert output["bundle"]["lifecycleState"] == "QUALITY_FAILED"
        assert output["bundle"]["identity"]["evidenceRecordIds"] == []
        assert any("semantic error page" in error for error in output["records"][0]["qualityErrors"])
        assert output["records"][1]["sourceType"] == "category_prior"
        assert all(value["method"] == "category_prior" for value in output["bundle"]["properties"])


def test_exact_request_contract_and_url_policy_fail_closed() -> None:
    with TestClient(app) as client:
        invalid = client.post(
            "/api/evidence/requests",
            json={"requestedName": "Unknown exact product", "category": "unknown", "exactIdentity": True},
        )
        assert invalid.status_code == 422

        request = _request(client, "private-url", required=["manufacturer", "exact_identifier", "source_url"])
        result = client.post(
            f"/api/evidence/requests/{request['id']}/normalize-recorded",
            json={
                "source": "recorded_brightdata",
                "rows": [
                    {
                        "source_url": "http://169.254.169.254/latest/meta-data",
                        "manufacturer": "Acme Kitchen",
                        "model_number": "BLD-500",
                    }
                ],
            },
        ).json()["result"]
        assert result["bundle"]["lifecycleState"] == "QUALITY_FAILED"
        assert any("external evidence URLs must use HTTPS" in error for error in result["records"][0]["qualityErrors"])
