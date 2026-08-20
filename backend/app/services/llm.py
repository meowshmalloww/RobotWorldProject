"""Resilient OpenAI-compatible planner access.

The configured endpoint may be OpenAI, Ollama, llama.cpp, vLLM, or another
compatible service.  Model access is an enhancement, never a liveness
dependency: permanent billing/authentication failures open a circuit until
settings change, while transient failures fall back to the deterministic,
data-derived curriculum planner.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any
from urllib.parse import urlparse

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, OpenAIError

from ..telemetry import span
from . import settings_store

log = logging.getLogger(__name__)

_state: dict[str, Any] = {
    "status": "not_configured",
    "provider": "openai-compatible",
    "model": None,
    "baseUrl": None,
    "lastError": None,
    "lastRequestId": None,
    "lastAttemptAt": None,
    "circuitOpen": False,
}
_circuit_fingerprint: str | None = None


def _is_local_endpoint(base: str) -> bool:
    host = (urlparse(base).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def status() -> dict[str, Any]:
    """Return secret-free provider health for the API health/readiness view."""
    return dict(_state)


def _fingerprint(base: str, model: str, key: str) -> str:
    return hashlib.sha256(f"{base}\0{model}\0{key}".encode()).hexdigest()


async def _config() -> tuple[AsyncOpenAI | None, str, str, str]:
    global _circuit_fingerprint
    flat = await settings_store.get_flat()
    key = str(flat.get("models.openaiKey") or "")
    base = str(flat.get("models.openaiBaseUrl") or "https://api.openai.com/v1").rstrip("/")
    model = str(flat.get("models.planner") or "gpt-4o-mini")
    provider = str(flat.get("models.provider") or "openai-compatible")
    timeout_s = max(5.0, min(float(flat.get("models.timeoutS") or 60), 300.0))
    fingerprint = _fingerprint(base, model, key)
    if _circuit_fingerprint and _circuit_fingerprint != fingerprint:
        _circuit_fingerprint = None
        _state.update(circuitOpen=False, lastError=None)
    locally_served = _is_local_endpoint(base)
    _state.update(provider=provider, model=model, baseUrl=base, status="not_configured" if not key and not locally_served else _state["status"])
    if not key and not locally_served:
        return None, model, base, fingerprint
    # The SDK already performs two bounded retries for transient statuses.
    return AsyncOpenAI(api_key=key or "local", base_url=base, timeout=timeout_s, max_retries=2), model, base, fingerprint


def _json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("planner output must be a JSON object")
    return value


async def plan(system: str, user: str, *, span_name: str = "chat planner") -> tuple[dict[str, Any] | None, str]:
    """Return ``(plan, provenance)`` without allowing provider failure to abort a job."""
    global _circuit_fingerprint
    client, model, base, fingerprint = await _config()
    if client is None:
        return None, "heuristic:no-provider"
    if _circuit_fingerprint == fingerprint:
        _state.update(status="circuit_open", circuitOpen=True)
        return None, "heuristic:provider-circuit-open"

    _state.update(status="checking", lastAttemptAt=time.time(), lastError=None, lastRequestId=None)
    flat = await settings_store.get_flat()
    effort = str(flat.get("models.reasoningEffort") or "high")
    if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
        effort = "high"
    verbosity = str(flat.get("models.verbosity") or "medium")
    if verbosity not in {"low", "medium", "high"}:
        verbosity = "medium"
    with span(
        span_name,
        **{
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": _state["provider"],
            "gen_ai.request.model": model,
        },
    ) as sp:
        try:
            if (urlparse(base).hostname or "").lower() == "api.openai.com":
                resp = await client.responses.create(
                    model=model,
                    instructions=system,
                    input=user,
                    store=False,
                    reasoning={"effort": effort},
                    text={"verbosity": verbosity, "format": {"type": "json_object"}},
                )
                request_id = getattr(resp, "_request_id", None)
                usage = resp.usage
                text = resp.output_text
                input_tokens = getattr(usage, "input_tokens", None) if usage else None
                output_tokens = getattr(usage, "output_tokens", None) if usage else None
            else:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )
                request_id = getattr(resp, "_request_id", None)
                usage = resp.usage
                text = resp.choices[0].message.content or ""
                input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
                output_tokens = getattr(usage, "completion_tokens", None) if usage else None
            if request_id:
                sp.set_attribute("gen_ai.response.id", request_id)
            if input_tokens is not None:
                sp.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            if output_tokens is not None:
                sp.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            parsed = _json_object(text)
            _state.update(status="healthy", circuitOpen=False, lastError=None, lastRequestId=request_id)
            return parsed, f"llm:{_state['provider']}"
        except APIStatusError as exc:
            code = int(exc.status_code)
            request_id = getattr(exc, "request_id", None)
            permanent = code in {400, 401, 402, 403, 404}
            # Fireworks reports suspended/spending-limit accounts as a
            # permanent billing failure even when routed through a compatible
            # gateway.  Retrying it only creates more noise and 5xx cascades.
            reason = "billing_or_auth" if code in {401, 402, 403} else "request_rejected" if permanent else "provider_unavailable"
            if permanent:
                _circuit_fingerprint = fingerprint
            _state.update(
                status="circuit_open" if permanent else "degraded",
                circuitOpen=permanent,
                lastError=f"{reason} ({code})",
                lastRequestId=request_id,
            )
            sp.set_attribute("error.type", reason)
            sp.set_attribute("http.response.status_code", code)
            log.warning("planner provider failed: status=%s request_id=%s base=%s", code, request_id or "-", base)
            return None, f"heuristic:{reason}"
        except (APITimeoutError, APIConnectionError) as exc:
            _state.update(status="degraded", circuitOpen=False, lastError=type(exc).__name__, lastRequestId=None)
            sp.set_attribute("error.type", type(exc).__name__)
            log.warning("planner provider unreachable: %s base=%s", type(exc).__name__, base)
            return None, "heuristic:provider-unreachable"
        except (json.JSONDecodeError, ValueError, IndexError) as exc:
            _state.update(status="degraded", circuitOpen=False, lastError="invalid_json", lastRequestId=None)
            sp.set_attribute("error.type", "invalid_json")
            log.warning("planner returned invalid structured output: %s", type(exc).__name__)
            return None, "heuristic:invalid-provider-output"
        except OpenAIError as exc:
            _state.update(status="degraded", circuitOpen=False, lastError=type(exc).__name__, lastRequestId=None)
            sp.set_attribute("error.type", type(exc).__name__)
            log.warning("planner SDK error: %s", type(exc).__name__)
            return None, "heuristic:provider-error"
        finally:
            await client.close()
