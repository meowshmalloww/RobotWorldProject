"""SigNoz Query API v5 client — the agent's programmatic window into telemetry.

Docs: https://signoz.io/docs/traces-management/trace-api/search-traces/
      https://signoz.io/docs/metrics-management/query-range-api/
Endpoint: POST {tenant}/api/v5/query_range with header SIGNOZ-API-KEY.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from . import settings_store


class SigNozError(RuntimeError):
    pass


class NotConfigured(SigNozError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "SigNoz query API not configured — set the API key in Settings → Integrations.")


async def probe() -> dict[str, Any]:
    """Verify the Community UI and OTLP listener independently."""
    flat = await settings_store.get_flat()
    ui = str(flat.get("integrations.signoz.queryEndpoint") or "").strip().rstrip("/")
    otlp = str(flat.get("integrations.signoz.endpoint") or "").strip().rstrip("/")
    if not ui or not otlp:
        raise NotConfigured("Set both the SigNoz UI and OTLP HTTP endpoints.")
    for label, value in (("UI", ui), ("OTLP", otlp)):
        if not value.startswith(("http://", "https://")):
            raise NotConfigured(f"SigNoz {label} endpoint must use http:// or https://.")

    try:
        async with httpx.AsyncClient(timeout=4.0, trust_env=False) as client:
            response = await client.get(f"{ui}/api/v1/health?live=1")
    except httpx.HTTPError as exc:
        raise SigNozError(f"SigNoz Community UI is unreachable at {ui}: {exc}") from exc
    if response.status_code >= 400:
        raise SigNozError(f"SigNoz health check failed ({response.status_code}).")

    try:
        async with httpx.AsyncClient(timeout=4.0, trust_env=False) as client:
            # Empty ExportTraceServiceRequest encoded as protobuf. A 2xx here
            # proves that the HTTP OTLP route accepts an ingestion request;
            # opening the TCP port alone does not.
            otlp_response = await client.post(
                f"{otlp}/v1/traces",
                content=b"\x0a\x00",
                headers={"Content-Type": "application/x-protobuf"},
            )
    except httpx.HTTPError as exc:
        raise SigNozError(f"SigNoz OTLP ingestion is unreachable at {otlp}: {exc}") from exc
    if not otlp_response.is_success:
        raise SigNozError(f"SigNoz OTLP ingestion rejected the probe ({otlp_response.status_code}).")

    version: Any = None
    try:
        async with httpx.AsyncClient(timeout=4.0, trust_env=False) as client:
            version_response = await client.get(f"{ui}/api/v1/version")
        if version_response.is_success:
            body = version_response.json()
            if isinstance(body, dict):
                version = body.get("version") or body.get("data")
    except (httpx.HTTPError, ValueError):
        pass
    return {
        "connected": True,
        "deployment": "community-self-hosted",
        "uiEndpoint": ui,
        "otlpEndpoint": otlp,
        "otlpHttpStatus": otlp_response.status_code,
        "version": version,
        "queryKeyConfigured": bool(flat.get("integrations.signoz.apiKey")),
    }


async def _creds() -> tuple[str, str]:
    flat = await settings_store.get_flat()
    endpoint = (flat.get("integrations.signoz.queryEndpoint") or "").rstrip("/")
    api_key = flat.get("integrations.signoz.apiKey") or ""
    if not endpoint or not api_key:
        raise NotConfigured(
            "SigNoz query API not configured — set the workspace query URL and service-account API key; "
            "the OTLP ingestion URL is a different host."
        )
    if not endpoint.startswith(("https://", "http://")):
        endpoint = f"https://{endpoint}"
    return endpoint, api_key


async def query_range(payload: dict) -> dict:
    base, api_key = await _creds()
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(
            f"{base}/api/v5/query_range",
            headers={"SIGNOZ-API-KEY": api_key, "Content-Type": "application/json"},
            json=payload,
        )
    if r.status_code == 401:
        raise SigNozError("SigNoz API key rejected (401).")
    if r.status_code >= 400:
        raise SigNozError(f"SigNoz query failed ({r.status_code}): {r.text[:300]}")
    return r.json()


def traces_filter_payload(*, minutes: int = 60, filter_expr: str = "", limit: int = 50) -> dict:
    now = int(time.time() * 1000)
    return {
        "start": now - minutes * 60_000,
        "end": now,
        "requestType": "raw",
        "variables": {},
        "compositeQuery": {
            "queries": [
                {
                    "type": "builder_query",
                    "spec": {
                        "name": "A",
                        "signal": "traces",
                        "filter": {"expression": filter_expr or "service.name = 'robotworld-backend'"},
                        "selectFields": [
                            {"name": "service.name", "fieldContext": "resource"},
                            {"name": "name", "fieldContext": "span"},
                            {"name": "duration_nano", "fieldContext": "span"},
                            {"name": "has_error", "fieldContext": "span"},
                        ],
                        "order": [{"key": {"name": "timestamp"}, "direction": "desc"}],
                        "limit": limit,
                        "offset": 0,
                        "disabled": False,
                    },
                }
            ]
        },
    }


async def search_traces(*, minutes: int = 60, filter_expr: str = "", limit: int = 50) -> dict:
    return await query_range(traces_filter_payload(minutes=minutes, filter_expr=filter_expr, limit=limit))


async def metric_timeseries(metric: str, *, minutes: int = 60, step: int = 60, agg: str = "avg") -> dict:
    now = int(time.time() * 1000)
    payload: dict[str, Any] = {
        "start": now - minutes * 60_000,
        "end": now,
        "requestType": "time_series",
        "compositeQuery": {
            "queries": [
                {
                    "type": "builder_query",
                    "spec": {
                        "name": "A",
                        "signal": "metrics",
                        "stepInterval": step,
                        "aggregations": [
                            {"metricName": metric, "temporality": "Unspecified", "timeAggregation": agg, "spaceAggregation": "avg"}
                        ],
                        "filter": {"expression": "service.name = 'robotworld-backend'"},
                        "disabled": False,
                    },
                }
            ]
        },
    }
    return await query_range(payload)
