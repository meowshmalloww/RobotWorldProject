from __future__ import annotations

import asyncio
import json
from typing import Any

from app.services import llm


class _FunctionCall:
    type = "function_call"

    def __init__(self, name: str, arguments: dict[str, Any], call_id: str) -> None:
        self.name = name
        self.arguments = json.dumps(arguments)
        self.call_id = call_id

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "arguments": self.arguments,
            "call_id": self.call_id,
        }


class _Response:
    def __init__(self, response_id: str, output: list[Any], output_text: str = "") -> None:
        self.id = response_id
        self.output = output
        self.output_text = output_text
        self._request_id = f"req_{response_id}"


class _Responses:
    def __init__(self, values: list[_Response]) -> None:
        self.values = values
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Response:
        self.requests.append(kwargs)
        return self.values.pop(0)


class _Client:
    def __init__(self, values: list[_Response]) -> None:
        self.responses = _Responses(values)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_responses_agent_loop_executes_tool_and_feeds_result_back(monkeypatch) -> None:
    monkeypatch.setattr(llm, "_state", dict(llm._state))
    client = _Client(
        [
            _Response("one", [_FunctionCall("catalog__list", {}, "call_1")]),
            _Response("two", [], '{"reply":"Observed one asset.","actions":[]}'),
        ]
    )

    async def config():
        return client, "gpt-test", "https://api.openai.com/v1", "fingerprint"

    async def settings():
        return {"models.reasoningEffort": "low", "models.verbosity": "low"}

    calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, arguments))
        return {"status": "SUCCEEDED", "data": {"assets": [{"id": "asset_1"}]}}

    monkeypatch.setattr(llm, "_config", config)
    monkeypatch.setattr(llm.settings_store, "get_flat", settings)
    monkeypatch.setattr(llm, "_circuit_fingerprint", None)
    result = asyncio.run(
        llm.tool_chat(
            [{"role": "user", "content": "Inspect the asset catalog."}],
            tools=[
                {
                    "name": "catalog.list",
                    "description": "List assets.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                }
            ],
            execute_tool=execute,
        )
    )

    text, provenance, model, transcript = result
    assert json.loads(text)["reply"] == "Observed one asset."
    assert provenance == "llm:openai-compatible:tool-loop"
    assert model == "gpt-test"
    assert calls == [("catalog.list", {})]
    assert transcript[1]["tool"] == "catalog.list"
    second_input = client.responses.requests[1]["input"]
    tool_output = next(item for item in second_input if item.get("type") == "function_call_output")
    assert json.loads(tool_output["output"])["data"]["assets"][0]["id"] == "asset_1"
    assert client.responses.requests[0]["store"] is False
    assert client.responses.requests[0]["parallel_tool_calls"] is False
    assert client.closed is True
