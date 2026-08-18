"""Port catalog client with short-lived access-token management.

Port client credentials are exchanged at ``/v1/auth/access_token`` and cached
until shortly before expiry. A manually generated bearer token remains
supported for short sessions. Entity writes use upsert+merge so retries and
repeated curriculum runs are idempotent.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from typing import Any

import httpx

from . import settings_store

log = logging.getLogger(__name__)


class PortError(RuntimeError):
    pass


class NotConfigured(PortError):
    pass


_token_cache: dict[str, Any] = {"fingerprint": None, "token": None, "expiresAt": 0.0}


async def _config() -> tuple[str, str, str, str]:
    flat = await settings_store.get_flat()
    if not bool(flat.get("integrations.port.enabled")):
        raise NotConfigured("Port is disabled in Settings → Integrations.")
    endpoint = str(flat.get("integrations.port.endpoint") or "https://api.port.io").rstrip("/")
    explicit_token = str(flat.get("integrations.port.token") or "")
    client_id = str(flat.get("integrations.port.clientId") or "")
    client_secret = str(flat.get("integrations.port.clientSecret") or "")
    if not explicit_token and not (client_id and client_secret):
        raise NotConfigured("Port requires either a temporary token or a client ID and client secret.")
    return endpoint, explicit_token, client_id, client_secret


async def _access_token(endpoint: str, explicit: str, client_id: str, client_secret: str, *, force: bool = False) -> str:
    if explicit:
        return explicit
    fingerprint = hashlib.sha256(f"{endpoint}\0{client_id}\0{client_secret}".encode()).hexdigest()
    if not force and _token_cache["fingerprint"] == fingerprint and time.time() < float(_token_cache["expiresAt"]):
        return str(_token_cache["token"])
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
            response = await client.post(
                f"{endpoint}/v1/auth/access_token",
                json={"clientId": client_id, "clientSecret": client_secret},
            )
    except httpx.RequestError as exc:
        raise PortError("Port authentication endpoint is unreachable") from exc
    if response.status_code in {401, 403}:
        raise PortError("Port rejected the configured client credentials.")
    if response.status_code >= 400:
        raise PortError(f"Port authentication failed ({response.status_code}): {response.text[:240]}")
    payload = response.json()
    token = str(payload.get("accessToken") or "")
    if not token:
        raise PortError("Port authentication response did not contain an access token")
    expires_in = max(60, int(payload.get("expiresIn") or 10800))
    _token_cache.update(fingerprint=fingerprint, token=token, expiresAt=time.time() + expires_in - 60)
    return token


async def _request(method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict:
    endpoint, explicit, client_id, client_secret = await _config()
    force_refresh = False
    for attempt in range(3):
        token = await _access_token(endpoint, explicit, client_id, client_secret, force=force_refresh)
        force_refresh = False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                response = await client.request(
                    method,
                    f"{endpoint}{path}",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=json,
                    params=params,
                )
        except httpx.RequestError as exc:
            if attempt == 2:
                raise PortError("Port is unreachable after 3 attempts") from exc
            await asyncio.sleep((2**attempt) + random.uniform(0, 0.25))
            continue
        if response.status_code == 401 and not explicit and attempt < 2:
            force_refresh = True
            continue
        if response.status_code in {408, 429, 500, 502, 503, 504} and attempt < 2:
            await asyncio.sleep((2**attempt) + random.uniform(0, 0.25))
            continue
        if response.status_code in {401, 403}:
            raise PortError(f"Port rejected authorization ({response.status_code}).")
        if response.status_code >= 400:
            raise PortError(f"Port request failed ({response.status_code}): {response.text[:240]}")
        return response.json() if response.content else {}
    raise PortError("Port request failed")


async def upsert_entity(
    blueprint: str,
    identifier: str,
    title: str,
    properties: dict[str, Any],
    relations: dict[str, Any] | None = None,
) -> dict:
    return await _request(
        "POST",
        f"/v1/blueprints/{blueprint}/entities",
        params={"upsert": "true", "merge": "true"},
        json={
            "identifier": identifier,
            "title": title,
            "properties": properties,
            **({"relations": relations} if relations else {}),
        },
    )


async def sync_curriculum_result(skill_id: str, skill_name: str, result: dict[str, Any]) -> dict:
    before = result.get("before", {})
    after = result.get("after", {})
    decision = result.get("decision", {})
    return await upsert_entity(
        "robotworldSkill",
        skill_id,
        skill_name,
        {
            "successBefore": float(before.get("success_rate", 0.0)),
            "successAfter": float(after.get("success_rate", 0.0)),
            "evaluationEpisodes": int(after.get("episodes", 0)),
            "latestDecision": str(decision.get("decision", "")),
            "decisionProvenance": str(decision.get("provenance", "")),
            "status": "ready" if float(after.get("success_rate", 0.0)) >= 85 else "improving",
        },
    )


async def probe() -> dict[str, Any]:
    try:
        await _request("GET", "/v1/blueprints", params={"page": 1, "per_page": 1})
        return {"status": "healthy"}
    except NotConfigured:
        return {"status": "not_configured"}
    except PortError as exc:
        log.warning("Port health probe failed: %s", exc)
        return {"status": "degraded", "error": str(exc)}
