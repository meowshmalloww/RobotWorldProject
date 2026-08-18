"""In-process event bus -> WebSocket broadcast (/ws/events).

Every long-running job publishes progress events here; the frontend turns
them into notifications/toasts and live status updates.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

_subscribers: set[asyncio.Queue] = set()
_history: list[dict[str, Any]] = []


def publish(kind: str, title: str, msg: str = "", **extra: Any) -> None:
    event = {"type": "event", "kind": kind, "title": title, "msg": msg, "ts": time.time(), **extra}
    _history.append(event)
    del _history[:-200]
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            _subscribers.discard(q)


def history() -> list[dict[str, Any]]:
    return list(_history)


async def subscribe():
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    _subscribers.add(q)
    try:
        yield q
    finally:
        _subscribers.discard(q)


def encode(event: dict[str, Any]) -> str:
    return json.dumps(event, default=str)
