"""Bright Data client — SERP API (Google Search / Images / Lens), Web Unlocker,
and Scraper Studio (DCA collectors with the run -> heal -> approve -> rerun
lifecycle). Raw REST per https://docs.brightdata.com — no SDK dependency.

Auth: `Authorization: Bearer <API_KEY>` against https://api.brightdata.com.
"""
from __future__ import annotations

import asyncio
import logging
import random
import urllib.parse
from typing import Any

import httpx

from ..telemetry import span
from . import settings_store

log = logging.getLogger(__name__)

API = "https://api.brightdata.com"


class BrightDataError(RuntimeError):
    pass


class NotConfigured(BrightDataError):
    def __init__(self) -> None:
        super().__init__(
            "Bright Data is not configured — set the API key in Settings → Integrations "
            "(or BRIGHTDATA_API_KEY in backend/.env)."
        )


async def _creds() -> dict[str, str]:
    flat = await settings_store.get_flat()
    key = flat.get("integrations.brightdata.apiKey") or ""
    if not key:
        raise NotConfigured()
    return {
        "key": key,
        "serp_zone": flat.get("integrations.brightdata.serpZone") or "serp",
        "unlocker_zone": flat.get("integrations.brightdata.unlockerZone") or "web_unlocker",
    }


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=20.0))


async def _send(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """Bounded retry for transport errors, rate limits, and upstream 5xx.

    Authentication, validation, and billing failures are returned immediately;
    retrying those only creates request storms and obscures the real cause.
    """
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            async with _client() as client:
                response = await client.request(method, url, **kwargs)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            last_error = exc
            if attempt == 3:
                raise BrightDataError(f"Bright Data transport failed after {attempt + 1} attempts") from exc
        else:
            if response.status_code not in {408, 429, 500, 502, 503, 504} or attempt == 3:
                return response
            retry_after = response.headers.get("retry-after", "")
            try:
                delay = min(float(retry_after), 20.0)
            except ValueError:
                delay = min(0.75 * (2**attempt) + random.uniform(0.0, 0.35), 8.0)
            log.warning("Bright Data transient status %s; retrying in %.1fs", response.status_code, delay)
            await asyncio.sleep(delay)
            continue
        await asyncio.sleep(min(0.75 * (2**attempt) + random.uniform(0.0, 0.35), 8.0))
    raise BrightDataError("Bright Data request failed") from last_error


async def _request(zone: str, url: str, fmt: str = "json") -> Any:
    """POST /request — used by both SERP API and Web Unlocker (zone decides)."""
    creds = await _creds()
    r = await _send(
        "POST",
        f"{API}/request",
        headers={"Authorization": f"Bearer {creds['key']}", "Content-Type": "application/json"},
        json={"zone": zone, "url": url, "format": fmt},
    )
    if r.status_code == 401:
        raise BrightDataError("Bright Data authentication failed — check the API key.")
    if r.status_code >= 400:
        raise BrightDataError(f"Bright Data request failed ({r.status_code}): {r.text[:300]}")
    if fmt == "raw":
        return r.text
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text}


async def google_search(query: str, *, country: str = "us", language: str = "en") -> dict:
    with span("brightdata.search", query=query):
        creds = await _creds()
        url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&hl={language}&gl={country}&brd_json=1"
        return await _request(creds["serp_zone"], url)


async def google_images(query: str, *, country: str = "us", language: str = "en", large: bool = True) -> list[dict]:
    """Parsed Google Images results (udm=2 per the post-2026 SERP API)."""
    with span("brightdata.image.search", query=query):
        creds = await _creds()
        url = (
            f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
            f"&hl={language}&gl={country}&udm=2"
            + ("&tbs=isz:l" if large else "")
        )
        data = await _request(creds["serp_zone"], url)
        images = data.get("images") if isinstance(data, dict) else None
        if not images:
            return []
        return [
            {
                "title": im.get("title"),
                "page": im.get("link"),
                "thumb": im.get("image"),
                "original": im.get("original_image") or im.get("image"),
                "width": im.get("original_width") or im.get("image_width"),
                "height": im.get("original_height") or im.get("image_height"),
            }
            for im in images
        ]


async def google_lens(image_url: str, *, tab: str = "exact_matches") -> dict:
    """Verify identity via Google Lens (visual_matches / exact_matches / products)."""
    with span("brightdata.lens", tab=tab):
        creds = await _creds()
        url = (
            "https://lens.google.com/uploadbyurl?url="
            + urllib.parse.quote(image_url, safe="")
            + f"&brd_json=1&brd_lens={urllib.parse.quote(tab)}"
        )
        return await _request(creds["serp_zone"], url)


async def unlock(url: str, *, render: bool = False, markdown: bool = False) -> Any:
    """Web Unlocker — fetch an anti-bot protected page."""
    with span("brightdata.scrape", url=url[:120]):
        creds = await _creds()
        body: dict[str, Any] = {"zone": creds["unlocker_zone"], "url": url, "format": "raw"}
        if render:
            body["render"] = "true"
        if markdown:
            body["data_format"] = "markdown"
        creds2 = await _creds()
        r = await _send(
            "POST",
            f"{API}/request",
            headers={"Authorization": f"Bearer {creds2['key']}", "Content-Type": "application/json"},
            json=body,
        )
        if r.status_code >= 400:
            raise BrightDataError(f"Web Unlocker failed ({r.status_code}): {r.text[:300]}")
        return r.text


# ---- Scraper Studio (DCA) -------------------------------------------------

async def dca_trigger(collector: str, inputs: list[dict]) -> str:
    """Batch collection: POST /dca/trigger?collector=c_*&queue_next=1 → collection_id (j_*)."""
    creds = await _creds()
    r = await _send(
        "POST",
        f"{API}/dca/trigger",
        params={"collector": collector, "queue_next": "1"},
        headers={"Authorization": f"Bearer {creds['key']}", "Content-Type": "application/json"},
        json=inputs,
    )
    if r.status_code >= 400:
        raise BrightDataError(f"DCA trigger failed ({r.status_code}): {r.text[:300]}")
    return r.json()["collection_id"]


async def dca_dataset(collection_id: str) -> tuple[bool, list[dict] | dict]:
    """Poll GET /dca/dataset?id=j_* → (ready, rows). 202 while building."""
    creds = await _creds()
    r = await _send(
        "GET",
        f"{API}/dca/dataset",
        params={"id": collection_id},
        headers={"Authorization": f"Bearer {creds['key']}"},
    )
    if r.status_code == 202:
        return False, r.json() if r.text else {}
    if r.status_code >= 400:
        raise BrightDataError(f"DCA dataset failed ({r.status_code}): {r.text[:300]}")
    return True, r.json()


async def dca_run_and_wait(collector: str, inputs: list[dict], *, timeout_s: float = 180.0) -> list[dict]:
    with span("brightdata.collector.run", collector=collector):
        cid = await dca_trigger(collector, inputs)
        deadline = asyncio.get_event_loop().time() + timeout_s
        wait = 4.0
        while asyncio.get_event_loop().time() < deadline:
            ready, rows = await dca_dataset(cid)
            if ready:
                return rows if isinstance(rows, list) else []
            await asyncio.sleep(wait)
            wait = min(wait * 1.5, 15.0)
        raise BrightDataError(f"Collector {collector} timed out after {timeout_s:.0f}s")


async def dca_heal(collector: str, note: str, url: str) -> dict:
    """Self-healing: POST /dca/collectors/{c_*}/refactor_template → awaiting_approval + preview."""
    creds = await _creds()
    r = await _send(
        "POST",
        f"{API}/dca/collectors/{collector}/refactor_template",
        headers={"Authorization": f"Bearer {creds['key']}", "Content-Type": "application/json"},
        json={"note": note, "url": url},
    )
    if r.status_code >= 400:
        raise BrightDataError(f"DCA heal failed ({r.status_code}): {r.text[:300]}")
    return r.json()


async def dca_approve(collector: str, url: str, approve: bool = True) -> dict:
    """POST /dca/collectors/{c_*}/resume_automation_job — approve or reject a heal."""
    creds = await _creds()
    r = await _send(
        "POST",
        f"{API}/dca/collectors/{collector}/resume_automation_job",
        headers={"Authorization": f"Bearer {creds['key']}", "Content-Type": "application/json"},
        json={"url": url, "approve": approve},
    )
    if r.status_code >= 400:
        raise BrightDataError(f"DCA approve failed ({r.status_code}): {r.text[:300]}")
    return r.json()


async def dca_list_collectors(search: str | None = None) -> list[dict]:
    creds = await _creds()
    params = {"search": search} if search else {}
    r = await _send(
        "GET",
        f"{API}/dca/collectors_list",
        params=params,
        headers={"Authorization": f"Bearer {creds['key']}"},
    )
    if r.status_code >= 400:
        raise BrightDataError(f"DCA collectors_list failed ({r.status_code}): {r.text[:300]}")
    data = r.json()
    return data.get("data", []) if isinstance(data, dict) else []
