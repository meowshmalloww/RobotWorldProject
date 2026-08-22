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
import os
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, OpenAIError

from ..telemetry import span
from . import settings_store

log = logging.getLogger(__name__)

MAX_AGENT_TOOL_TURNS = 6
MAX_AGENT_TOOL_CALLS = 16
MAX_TOOL_OUTPUT_CHARS = 24_000

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
    key = str(flat.get("models.openaiKey") or "") or os.environ.get("OPENAI_API_KEY", "")
    base = str(flat.get("models.openaiBaseUrl") or "https://api.openai.com/v1").rstrip("/")
    model = str(flat.get("models.planner") or "gpt-4o-mini")
    provider = str(flat.get("models.provider") or "openai-compatible")
    timeout_s = max(5.0, min(float(flat.get("models.timeoutS") or 60), 300.0))
    fingerprint = _fingerprint(base, model, key)
    if _circuit_fingerprint and _circuit_fingerprint != fingerprint:
        _circuit_fingerprint = None
        _state.update(circuitOpen=False, lastError=None)
    locally_served = _is_local_endpoint(base)
    configured = bool(key or locally_served)
    current_status = str(_state["status"])
    if not configured:
        current_status = "not_configured"
    elif current_status == "not_configured":
        # Configuration is a meaningful startup state even before the first
        # network request.  Reporting ``not_configured`` here made a valid key
        # look absent until somebody happened to send the first chat turn.
        current_status = "configured"
    _state.update(provider=provider, model=model, baseUrl=base, status=current_status)
    if not key and not locally_served:
        return None, model, base, fingerprint
    # The SDK already performs two bounded retries for transient statuses.
    return AsyncOpenAI(api_key=key or "local", base_url=base, timeout=timeout_s, max_retries=2), model, base, fingerprint


async def refresh_status() -> dict[str, Any]:
    """Resolve persisted/environment configuration without making a request."""

    client, _, _, _ = await _config()
    if client is not None:
        await client.close()
    return status()


def _json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("planner output must be a JSON object")
    return value


def _wire_item(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json", exclude_none=True)
    if isinstance(item, dict):
        return dict(item)
    raise TypeError(f"Unsupported Responses output item {type(item).__name__}")


def _bounded_tool_output(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)
    if len(raw) <= MAX_TOOL_OUTPUT_CHARS:
        return raw
    return json.dumps(
        {
            "truncated": True,
            "originalCharacters": len(raw),
            "preview": raw[: MAX_TOOL_OUTPUT_CHARS - 200],
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


async def tool_chat(
    messages: list[dict[str, str]],
    *,
    tools: list[dict[str, Any]],
    execute_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    model_override: str | None = None,
    effort_override: str | None = None,
    span_name: str = "agentic chat",
    max_turns: int = MAX_AGENT_TOOL_TURNS,
) -> tuple[str, str, str, list[dict[str, Any]]]:
    """Run a bounded stateless Responses function-calling loop.

    The caller owns authorization. This function only dispatches names from
    ``tools`` to ``execute_tool`` and feeds the bounded JSON result back to the
    model. Every response item is replayed because ``store=False`` is used.
    """

    global _circuit_fingerprint
    client, model, base, fingerprint = await _config()
    if model_override:
        model = model_override
    if client is None:
        return "", "heuristic:no-provider", model, []
    if _circuit_fingerprint == fingerprint:
        await client.close()
        _state.update(status="circuit_open", circuitOpen=True)
        return "", "heuristic:provider-circuit-open", model, []
    if (urlparse(base).hostname or "").lower() != "api.openai.com":
        await client.close()
        text, provenance, selected = await chat(
            messages,
            model_override=model,
            effort_override=effort_override,
            json_output=True,
            span_name=span_name,
        )
        return text, f"{provenance}:tool-loop-unavailable", selected, []

    flat = await settings_store.get_flat()
    effort = effort_override or str(flat.get("models.reasoningEffort") or "high")
    if effort == "ultra":
        effort = "max"
    if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
        effort = "high"
    verbosity = str(flat.get("models.verbosity") or "medium")
    if verbosity not in {"low", "medium", "high"}:
        verbosity = "medium"

    safe_to_original: dict[str, str] = {}
    provider_tools: list[dict[str, Any]] = []
    for definition in tools:
        original = str(definition["name"])
        safe = original.replace(".", "__")
        if safe in safe_to_original and safe_to_original[safe] != original:
            raise ValueError(f"Tool-name collision after provider normalization: {original}")
        safe_to_original[safe] = original
        provider_tools.append(
            {
                "type": "function",
                "name": safe,
                "description": str(definition.get("description") or "")[:1024],
                "parameters": dict(definition.get("parameters") or {"type": "object", "properties": {}}),
                # Pydantic remains the authority. Some models include defaults
                # and refs outside the provider's strict-schema subset.
                "strict": False,
            }
        )

    input_items: list[dict[str, Any]] = [dict(item) for item in messages]
    transcript: list[dict[str, Any]] = []
    total_calls = 0
    seen: dict[str, dict[str, Any]] = {}
    final_text = ""
    _state.update(status="checking", lastAttemptAt=time.time(), lastError=None, lastRequestId=None)
    with span(
        span_name,
        **{
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.provider.name": _state["provider"],
            "gen_ai.request.model": model,
        },
    ) as sp:
        try:
            for turn in range(max(1, min(max_turns, MAX_AGENT_TOOL_TURNS))):
                response = await client.responses.create(
                    model=model,
                    input=input_items,
                    tools=provider_tools,
                    tool_choice="auto",
                    parallel_tool_calls=False,
                    store=False,
                    include=["reasoning.encrypted_content"],
                    reasoning={"effort": effort},
                    text={"verbosity": verbosity, "format": {"type": "json_object"}},
                )
                request_id = getattr(response, "_request_id", None)
                if request_id:
                    _state["lastRequestId"] = request_id
                    sp.set_attribute("gen_ai.response.id", request_id)
                calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
                transcript.append(
                    {
                        "turn": turn + 1,
                        "responseId": getattr(response, "id", None),
                        "requestId": request_id,
                        "toolCallCount": len(calls),
                    }
                )
                if not calls:
                    final_text = str(response.output_text or "").strip()
                    break
                input_items.extend(_wire_item(item) for item in response.output)
                for call in calls:
                    total_calls += 1
                    safe_name = str(getattr(call, "name", ""))
                    original = safe_to_original.get(safe_name)
                    call_id = str(getattr(call, "call_id", ""))
                    try:
                        arguments = json.loads(str(getattr(call, "arguments", "{}")) or "{}")
                        if not isinstance(arguments, dict):
                            raise ValueError("tool arguments must be a JSON object")
                        if original is None:
                            raise ValueError(f"unknown tool '{safe_name}'")
                        digest = hashlib.sha256(
                            f"{original}\0{json.dumps(arguments, sort_keys=True, separators=(',', ':'))}".encode()
                        ).hexdigest()
                        if digest in seen:
                            output = {**seen[digest], "reusedInAgentTurn": True}
                        elif total_calls > MAX_AGENT_TOOL_CALLS:
                            output = {"status": "blocked", "error": "agent tool-call budget exhausted"}
                        else:
                            output = await execute_tool(original, arguments)
                            seen[digest] = output
                    except Exception as exc:
                        output = {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:1000]}"}
                    transcript.append(
                        {
                            "turn": turn + 1,
                            "tool": original or safe_name,
                            "callId": call_id,
                            "status": output.get("status") if isinstance(output, dict) else None,
                        }
                    )
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": _bounded_tool_output(output),
                        }
                    )
            if not final_text:
                response = await client.responses.create(
                    model=model,
                    input=input_items,
                    tools=provider_tools,
                    tool_choice="none",
                    store=False,
                    include=["reasoning.encrypted_content"],
                    reasoning={"effort": effort},
                    text={"verbosity": verbosity, "format": {"type": "json_object"}},
                )
                final_text = str(response.output_text or "").strip()
                transcript.append(
                    {
                        "turn": len(transcript) + 1,
                        "responseId": getattr(response, "id", None),
                        "requestId": getattr(response, "_request_id", None),
                        "toolCallCount": 0,
                        "forcedFinal": True,
                    }
                )
            if not final_text:
                raise ValueError("agent loop returned no final JSON output")
            _state.update(status="healthy", circuitOpen=False, lastError=None)
            sp.set_attribute("gen_ai.agent.tool_calls", total_calls)
            sp.set_attribute("gen_ai.agent.turns", sum(1 for item in transcript if "responseId" in item))
            return final_text, f"llm:{_state['provider']}:tool-loop", model, transcript
        except APIStatusError as exc:
            code = int(exc.status_code)
            request_id = getattr(exc, "request_id", None)
            permanent = code in {400, 401, 402, 403, 404}
            reason = "billing_or_auth" if code in {401, 402, 403} else "request_rejected" if permanent else "provider_unavailable"
            if permanent:
                _circuit_fingerprint = fingerprint
            _state.update(status="circuit_open" if permanent else "degraded", circuitOpen=permanent, lastError=f"{reason} ({code})", lastRequestId=request_id)
            sp.set_attribute("error.type", reason)
            return "", f"heuristic:{reason}", model, transcript
        except (APITimeoutError, APIConnectionError) as exc:
            _state.update(status="degraded", circuitOpen=False, lastError=type(exc).__name__, lastRequestId=None)
            sp.set_attribute("error.type", type(exc).__name__)
            return "", "heuristic:provider-unreachable", model, transcript
        except (OpenAIError, ValueError, TypeError) as exc:
            _state.update(status="degraded", circuitOpen=False, lastError=type(exc).__name__, lastRequestId=None)
            sp.set_attribute("error.type", type(exc).__name__)
            log.warning("agent tool loop failed: %s", type(exc).__name__)
            return "", f"heuristic:{type(exc).__name__.lower()}", model, transcript
        finally:
            await client.close()


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


async def chat(
    messages: list[dict[str, str]],
    *,
    model_override: str | None = None,
    effort_override: str | None = None,
    json_output: bool = True,
    span_name: str = "ai chat",
) -> tuple[str, str, str]:
    """Free-form multi-turn chat against the configured provider.

    Returns ``(text, provenance, model)``.  Provenance follows the same
    honest scheme as :func:`plan`: ``llm:<provider>`` on success and
    ``heuristic:<reason>`` when the provider cannot be used, so callers can
    surface the degradation instead of inventing an answer.
    """
    global _circuit_fingerprint
    client, model, base, fingerprint = await _config()
    if model_override:
        model = model_override
    if client is None:
        return "", "heuristic:no-provider", model
    if _circuit_fingerprint == fingerprint:
        _state.update(status="circuit_open", circuitOpen=True)
        return "", "heuristic:provider-circuit-open", model

    _state.update(status="checking", lastAttemptAt=time.time(), lastError=None, lastRequestId=None)
    flat = await settings_store.get_flat()
    effort = effort_override or str(flat.get("models.reasoningEffort") or "high")
    if effort == "ultra":
        # Copilot-only ceiling tier: maps to the provider's maximum reasoning.
        effort = "max"
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
                text_format: dict[str, Any] = {"verbosity": verbosity}
                if json_output:
                    text_format["format"] = {"type": "json_object"}
                resp = await client.responses.create(
                    model=model,
                    input=messages,
                    store=False,
                    reasoning={"effort": effort},
                    text=text_format,
                )
                request_id = getattr(resp, "_request_id", None)
                usage = resp.usage
                text = resp.output_text
                input_tokens = getattr(usage, "input_tokens", None) if usage else None
                output_tokens = getattr(usage, "output_tokens", None) if usage else None
            else:
                kwargs: dict[str, Any] = {"temperature": 0.4}
                if json_output:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **kwargs,
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
            _state.update(status="healthy", circuitOpen=False, lastError=None, lastRequestId=request_id)
            return text, f"llm:{_state['provider']}", model
        except APIStatusError as exc:
            code = int(exc.status_code)
            request_id = getattr(exc, "request_id", None)
            permanent = code in {400, 401, 402, 403, 404}
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
            log.warning("chat provider failed: status=%s request_id=%s base=%s", code, request_id or "-", base)
            return "", f"heuristic:{reason}", model
        except (APITimeoutError, APIConnectionError) as exc:
            _state.update(status="degraded", circuitOpen=False, lastError=type(exc).__name__, lastRequestId=None)
            sp.set_attribute("error.type", type(exc).__name__)
            log.warning("chat provider unreachable: %s base=%s", type(exc).__name__, base)
            return "", "heuristic:provider-unreachable", model
        except OpenAIError as exc:
            _state.update(status="degraded", circuitOpen=False, lastError=type(exc).__name__, lastRequestId=None)
            sp.set_attribute("error.type", type(exc).__name__)
            log.warning("chat SDK error: %s", type(exc).__name__)
            return "", "heuristic:provider-error", model
        finally:
            await client.close()
