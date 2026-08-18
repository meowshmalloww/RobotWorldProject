"""Settings persistence — DB-backed, dot-key access, secret masking."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..config import DEFAULT_SETTINGS, SECRET_KEYS
from ..db import SessionLocal
from ..models import Setting
from ..util import mask_secret


def _flatten(d: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def _nest(flat: dict[str, Any]) -> dict:
    root: dict[str, Any] = {}
    for key, value in flat.items():
        parts = key.split(".")
        node = root
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
    return root


async def get_flat() -> dict[str, Any]:
    """All settings merged over defaults."""
    flat = _flatten(DEFAULT_SETTINGS)
    async with SessionLocal() as s:
        rows = (await s.execute(select(Setting))).scalars().all()
    for row in rows:
        flat[row.key] = row.value
    return flat


async def get_settings(masked: bool = True) -> dict:
    flat = await get_flat()
    if masked:
        flat = {k: (mask_secret(str(v)) if k in SECRET_KEYS and v else v) for k, v in flat.items()}
    return _nest(flat)


async def get_value(key: str, default: Any = None) -> Any:
    flat = await get_flat()
    return flat.get(key, default)


async def put_section(section: str, patch: dict) -> dict:
    """Merge a partial section update (e.g. section='integrations', patch={'signoz': {'region': 'eu'}})."""
    defaults = _flatten(DEFAULT_SETTINGS)
    if section not in DEFAULT_SETTINGS:
        raise KeyError(f"unknown settings section '{section}'")
    async with SessionLocal() as s:
        for k, v in _flatten(patch).items():
            key = f"{section}.{k}"
            if key not in defaults:
                raise KeyError(f"unknown setting '{key}'")
            row = await s.get(Setting, key)
            # GET /settings is intentionally write-only for secrets.  A form
            # save posts the masked display value back; never replace the real
            # credential with that mask.
            current = row.value if row is not None else defaults.get(key)
            if key in SECRET_KEYS and current and isinstance(v, str):
                if v == mask_secret(str(current)) or "•" in v:
                    continue
            if row is None:
                s.add(Setting(key=key, value=v))
            else:
                row.value = v
        await s.commit()
    return await get_settings()


async def put_key(service: str, value: str) -> None:
    mapping = {
        "port": "integrations.port.token",
        "port_client_secret": "integrations.port.clientSecret",
        "brightdata": "integrations.brightdata.apiKey",
        "signoz": "integrations.signoz.ingestionKey",
        "signoz_api": "integrations.signoz.apiKey",
        "openai": "models.openaiKey",
    }
    if service not in mapping:
        raise KeyError(f"unknown key service '{service}'")
    async with SessionLocal() as s:
        row = await s.get(Setting, mapping[service])
        if row is None:
            s.add(Setting(key=mapping[service], value=value))
        else:
            row.value = value
        await s.commit()


async def secret_status() -> dict[str, bool]:
    flat = await get_flat()
    return {
        "openai": bool(flat.get("models.openaiKey")),
        "brightdata": bool(flat.get("integrations.brightdata.apiKey")),
        "signoz": bool(flat.get("integrations.signoz.ingestionKey")),
        "signoz_api": bool(flat.get("integrations.signoz.apiKey")),
        "port": bool(flat.get("integrations.port.token") or (flat.get("integrations.port.clientId") and flat.get("integrations.port.clientSecret"))),
    }
