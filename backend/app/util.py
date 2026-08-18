"""Small shared helpers: ids, time, display formatting."""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def now_ms() -> int:
    return int(time.time() * 1000)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def rel_time(dt: datetime | None) -> str:
    """Human relative time used across the UI ('4 h ago')."""
    if dt is None:
        return "never"
    delta = utcnow() - (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
    s = int(delta.total_seconds())
    if s < 5:
        return "just now"
    if s < 60:
        return f"{s} s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = float(seconds)
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def fmt_size(nbytes: int | None) -> str:
    if not nbytes:
        return "0 B"
    f = float(nbytes)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.1f} {unit}" if unit != "B" else f"{int(f)} B"
        f /= 1024
    return f"{f:.1f} GB"


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "•" * len(value)
    return value[:3] + "•" * 8 + value[-3:]
