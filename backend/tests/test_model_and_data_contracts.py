from __future__ import annotations

import asyncio
import json

import httpx
import numpy as np
import pytest

from app.services import brightdata, remote_policy, simcore


def _policy_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/capabilities":
        return httpx.Response(
            200,
            json={
                "schemaVersion": remote_policy.SCHEMA_VERSION,
                "policyId": "test/open-vla",
                "modelRevision": "test-revision",
                "modelSha256": "a" * 64,
                "normalizationSha256": "c" * 64,
                "embodiment": "robotworld-4dof-v1",
                "checkpointTrainedForEmbodiment": True,
                "environmentSha256": "b" * 64,
                "observation": {
                    "cameras": [{"name": "front"}, {"name": "wrist"}],
                    "stateSize": 5,
                },
                "action": {
                    "size": 5,
                    "representation": "relative_joint_absolute_gripper",
                    "horizon": 8,
                },
            },
        )
    payload = __import__("json").loads(request.content)
    if request.url.path == "/v1/reset":
        return httpx.Response(200, json={"episodeId": payload["episodeId"], "accepted": True})
    if request.url.path == "/v1/actions":
        assert [item["name"] for item in payload["video"]] == ["front", "wrist"]
        assert len(payload["state"]["jointPositionAndGripper"]) == 5
        assert "door" not in payload["state"] and "handle" not in payload["state"]
        return httpx.Response(
            200,
            json={
                "schemaVersion": remote_policy.SCHEMA_VERSION,
                "episodeId": payload["episodeId"],
                "requestSequence": payload["sequence"],
                "actions": [[0.0, 0.0, 0.0, 0.0, 0.0]] * 2,
            },
        )
    return httpx.Response(404)


def test_remote_policy_contract_uses_real_rgb_and_non_privileged_state() -> None:
    cfg = remote_policy.PolicyConfig(
        endpoint="https://policy.invalid",
        policy_id="test/open-vla",
        execution_horizon=8,
        image_size=64,
        model_revision="test-revision",
        model_sha256="a" * 64,
        normalization_sha256="c" * 64,
        environment_sha256="b" * 64,
    )
    transport = httpx.MockTransport(_policy_handler)
    world = simcore.World(simcore.default_scenario_family(np.random.default_rng(1)))
    result = simcore.run_rollout(
        world,
        lambda loaded: remote_policy.RemotePolicyController(loaded, cfg, transport=transport),
        max_s=0.06,
        record=False,
    )
    assert not result.success
    assert result.failure_mode == "no_contact"


def test_remote_policy_rejects_actions_outside_safety_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        normal = _policy_handler(request)
        if request.url.path == "/v1/actions":
            payload = __import__("json").loads(request.content)
            return httpx.Response(
                200,
                json={
                    "schemaVersion": remote_policy.SCHEMA_VERSION,
                    "episodeId": payload["episodeId"],
                    "requestSequence": payload["sequence"],
                    "actions": [[0.5, 0.0, 0.0, 0.0, 0.0]],
                },
            )
        return normal

    cfg = remote_policy.PolicyConfig(
        endpoint="https://policy.invalid",
        policy_id="test/open-vla",
        image_size=32,
        model_revision="test-revision",
        model_sha256="a" * 64,
        normalization_sha256="c" * 64,
        environment_sha256="b" * 64,
    )
    world = simcore.World(simcore.default_scenario_family(np.random.default_rng(2)))
    result = simcore.run_rollout(
        world,
        lambda loaded: remote_policy.RemotePolicyController(loaded, cfg, transport=httpx.MockTransport(handler)),
        max_s=0.06,
        record=False,
    )
    assert result.failure_mode == "policy_action_out_of_range"


def test_brightdata_self_heal_payload_and_human_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[tuple[str, str, dict]] = []

    async def creds():
        return {"key": "secret", "serp_zone": "serp", "unlocker_zone": "unlocker"}

    async def send(method: str, url: str, **kwargs):
        requests.append((method, url, kwargs))
        return httpx.Response(200, json={"status": "pending_answer"})

    monkeypatch.setattr(brightdata, "_creds", creds)
    monkeypatch.setattr(brightdata, "_send", send)
    asyncio.run(brightdata.dca_heal("c_123", "repair required fields", "https://example.com/product"))
    asyncio.run(brightdata.dca_approve("c_123", True, auto_save=True))
    assert requests[0][2]["json"] == {
        "prompt": "repair required fields",
        "custom_input": [{"url": "https://example.com/product"}],
    }
    assert requests[1][2]["json"] == {"message": True, "auto_save": True}


def test_brightdata_unwraps_gateway_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed_serp = {
        "general": {"search_engine": "google", "query": "robot refrigerator"},
        "organic": [{"title": "Product", "link": "https://example.com/product"}],
    }

    async def creds():
        return {"key": "secret", "serp_zone": "serp_api1", "unlocker_zone": "unlocker"}

    async def send(method: str, url: str, **kwargs):
        return httpx.Response(
            200,
            json={"status_code": 200, "headers": {}, "body": json.dumps(parsed_serp)},
        )

    monkeypatch.setattr(brightdata, "_creds", creds)
    monkeypatch.setattr(brightdata, "_send", send)
    result = asyncio.run(brightdata.google_search("robot refrigerator"))
    assert result == parsed_serp


def test_brightdata_rejects_wrapped_upstream_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def creds():
        return {"key": "secret", "serp_zone": "serp_api1", "unlocker_zone": "unlocker"}

    async def send(method: str, url: str, **kwargs):
        return httpx.Response(
            200,
            json={"status_code": 403, "headers": {}, "body": "target access denied"},
        )

    monkeypatch.setattr(brightdata, "_creds", creds)
    monkeypatch.setattr(brightdata, "_send", send)
    with pytest.raises(brightdata.BrightDataError, match=r"upstream target failed \(403\)"):
        asyncio.run(brightdata.google_search("robot refrigerator"))


def test_brightdata_dataset_200_status_object_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    async def creds():
        return {"key": "secret", "serp_zone": "serp_api1", "unlocker_zone": "unlocker"}

    async def send(method: str, url: str, **kwargs):
        return httpx.Response(200, json={"status": "building"})

    monkeypatch.setattr(brightdata, "_creds", creds)
    monkeypatch.setattr(brightdata, "_send", send)
    ready, payload = asyncio.run(brightdata.dca_dataset("j_in_progress"))
    assert ready is False
    assert payload == {"status": "building"}
