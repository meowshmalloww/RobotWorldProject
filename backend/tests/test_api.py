from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.contracts import WorldOperateRequest
from app.main import ChatIn, ChatMessageIn, ManualJogIn, _chat_offline_intent, _chat_tool_catalog, _select_vla_status_registration, app
from app.services import brightdata, franka_live, llm


def test_manual_jog_contract_and_bounded_live_summary() -> None:
    assert ManualJogIn(deltaM=(0.02, 0.0, 0.0)).deltaM == (0.02, 0.0, 0.0)
    for invalid in ((0.0, 0.0, 0.0), (0.031, 0.0, 0.0), (0.03, 0.03, 0.0)):
        try:
            ManualJogIn(deltaM=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe manual jog was accepted: {invalid}")
    summary = franka_live.evaluation_summary({
        "id": "eval_test",
        "status": "SUCCEEDED",
        "success": True,
        "result": {"predicate": {"settled": True}, "trajectory": [{"large": "payload"}] * 1000},
    })
    assert summary["result"] == {"predicate": {"settled": True}, "contactSummary": None}
    assert "trajectory" not in summary["result"]


def test_vla_status_selects_resident_checkpoint_not_first_registration() -> None:
    candidate = {
        "id": "candidate",
        "roles": ["vla_policy"],
        "localPath": r"D:\RobotWorldProject\data\training_runs\candidate",
        "lifecycleState": "AVAILABLE",
        "capabilities": {"configType": "vla_jepa"},
    }
    base = {
        "id": "base",
        "roles": ["vla_policy"],
        "localPath": r"D:\VLA-JEPA-Pretrain",
        "lifecycleState": "LOADED",
        "capabilities": {"configType": "vla_jepa"},
    }
    selected = _select_vla_status_registration(
        [candidate, base],
        {
            "running": True,
            "loaded": True,
            "resident": {"checkpointPath": r"d:\VLA-JEPA-Pretrain"},
        },
    )
    assert selected is base


def test_grounded_chat_rejects_measured_failed_candidate() -> None:
    context = _grounded_chat_context(loaded=True)
    context["models"].append({
        "id": "candidate-model",
        "name": "Fine-tuned candidate",
        "roles": ["vla_policy"],
        "lifecycle": "AVAILABLE",
        "health": "healthy",
        "sourceTrainingRunId": "training-run",
        "baseModelId": "vla-test",
    })
    context["latestTrainingPreflights"] = [{
        "id": "training-run",
        "lifecycle": "SUCCEEDED",
        "baseModelId": "vla-test",
        "candidateCheckpointSha256": "a" * 64,
    }]
    context["latestEvaluations"] = [{
        "id": "candidate-evaluation",
        "success": False,
        "failureCode": "grasp_miss",
        "policy": "vla-jepa:candidate-model:r2",
    }]
    context["latestPolicyDecisions"] = []
    response = _chat_offline_intent(
        ChatIn(messages=[ChatMessageIn(role="user", content="Reject the candidate; do not promote it.")]),
        context,
        "planner:typed-workspace",
        "typed-control-planner",
    )
    assert response["actions"][0]["tool"] == "training.policy_candidates.decide"
    arguments = response["actions"][0]["arguments"]
    assert arguments["decision"] == "REJECT"
    assert arguments["evaluationIds"] == ["candidate-evaluation"]
    assert arguments["previousModelId"] == "vla-test"


def _grounded_chat_context(*, loaded: bool) -> dict:
    return {
        "robots": [
            {
                "id": "franka-test",
                "name": "Franka Panda",
                "format": "mjcf",
                "physicsReady": True,
                "cameras": ["front", "wrist"],
                "blockers": [],
            }
        ],
        "models": [
            {
                "id": "vla-test",
                "name": "VLA-JEPA",
                "roles": ["vla_policy"],
                "lifecycle": "LOADED" if loaded else "AVAILABLE",
                "health": "healthy" if loaded else "worker_stopped",
            }
        ],
        "physicalAssets": [
            {"id": "assetver-test", "name": "Known-good cube", "lifecycle": "ORACLE_VALIDATED"}
        ],
        "latestEvaluations": [],
        "latestCurriculumRuns": [],
        "latestDatasets": [],
        "latestTrainingPreflights": [],
        "integrations": {
            "brightDataConfigured": False,
            "sigNozOtlpConfigured": False,
            "sigNozQueryConfigured": False,
            "trellisQ4ArtifactAvailable": True,
            "leRobotDatasetWriterImplemented": True,
            "fineTuningPreflightImplemented": True,
            "fineTuningWorkerImplemented": True,
        },
    }


def test_grounded_chat_asks_for_training_goal_before_actions() -> None:
    response = _chat_offline_intent(
        ChatIn(messages=[ChatMessageIn(role="user", content="Help me train my current robot.")]),
        _grounded_chat_context(loaded=True),
        "disabled:not_configured",
        "planner",
    )
    assert response["actions"] == []
    assert "what task" in response["reply"].lower()
    assert "fine-tuning" in response["reply"].lower()


def test_grounded_chat_advances_pick_place_to_load_or_bounded_run() -> None:
    payload = ChatIn(messages=[ChatMessageIn(role="user", content="Pick up the object and place it in the target.")])
    stopped = _chat_offline_intent(payload, _grounded_chat_context(loaded=False), "disabled:not_configured", "planner")
    assert [action["tool"] for action in stopped["actions"]] == ["models.load"]

    loaded = _chat_offline_intent(payload, _grounded_chat_context(loaded=True), "disabled:not_configured", "planner")
    assert [action["tool"] for action in loaded["actions"]] == ["curriculum.runs.start"]
    arguments = loaded["actions"][0]["arguments"]
    assert arguments["robotId"] == "franka-test"
    assert arguments["modelId"] == "vla-test"
    assert arguments["allowedAssetVersionIds"] == ["assetver-test"]
    assert arguments["budgets"]["maxEvaluationEpisodes"] == 2
    assert arguments["instruction"] == "Pick up the object and place it in the target."


def test_grounded_chat_keeps_original_instruction_after_tool_result() -> None:
    response = _chat_offline_intent(
        ChatIn(messages=[
            ChatMessageIn(role="user", content="Pick up the object and place it in the target."),
            ChatMessageIn(role="user", content="Authoritative RobotWorld tool result — models.load succeeded. Continue."),
        ]),
        _grounded_chat_context(loaded=True),
        "planner:typed-workspace",
        "typed-control-planner",
    )
    assert response["actions"][0]["arguments"]["instruction"] == "Pick up the object and place it in the target."


def test_grounded_chat_runs_oracle_instead_of_repeating_known_invalid_vla() -> None:
    context = _grounded_chat_context(loaded=True)
    context["latestEvaluations"] = [{
        "id": "eval-vla-invalid",
        "success": False,
        "failureCode": "invalid_action",
        "policy": "VLA-JEPA:test",
        "robotId": "franka-test",
        "observationFrameCount": 4,
    }]
    response = _chat_offline_intent(
        ChatIn(messages=[ChatMessageIn(role="user", content="Pick up the object and place it in the target.")]),
        context,
        "planner:typed-workspace",
        "typed-control-planner",
    )
    assert [action["tool"] for action in response["actions"]] == ["evaluations.run_oracle_compiled_asset"]
    assert response["actions"][0]["arguments"]["assetVersionId"] == "assetver-test"

    stopped = _chat_offline_intent(
        ChatIn(messages=[
            ChatMessageIn(role="user", content="Pick up the object and place it in the target."),
            ChatMessageIn(role="user", content="Authoritative RobotWorld tool result — evaluations.run_oracle_compiled_asset succeeded."),
        ]),
        context,
        "planner:typed-workspace",
        "typed-control-planner",
    )
    assert stopped["actions"] == []
    assert "did not re-run" in stopped["reply"]


def test_chat_tool_catalog_exposes_required_input_schema() -> None:
    catalog = _chat_tool_catalog()
    assert "vla.attach_franka_zero_shot_bridge" in catalog
    assert '"acknowledgeZeroShotRisk"' in catalog
    assert '"cameraMapping"' in catalog
    assert '"maxEvaluationEpisodes"' in catalog
    assert "training.datasets.create_from_evaluation" in catalog
    assert '"evaluationId"' in catalog
    assert "training.vla_jepa.execute_fine_tune" in catalog
    assert '"acknowledgeCandidateOnly"' in catalog


def test_agentic_chat_converts_model_mutation_to_approval_card(monkeypatch) -> None:
    async def tool_chat(messages, *, tools, execute_tool, **kwargs):
        result = await execute_tool("curriculum.runs.cancel", {"runId": "autorun_pending"})
        assert result["status"] == "approval_required"
        return '{"reply":"Cancellation is ready for approval.","actions":[]}', "llm:test:tool-loop", "gpt-test", [
            {"turn": 1, "tool": "curriculum.runs.cancel", "status": "approval_required"}
        ]

    monkeypatch.setattr(llm, "tool_chat", tool_chat)
    with TestClient(app) as client:
        before = len(client.get("/api/agent/tool-calls").json()["toolCalls"])
        response = client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "Cancel the active run."}]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["provenance"] == "llm:test:tool-loop"
        assert body["actions"] == [
            {
                "label": "Cancel",
                "tool": "curriculum.runs.cancel",
                "arguments": {"runId": "autorun_pending"},
                "effect": "MUTATION",
                "approvalRequired": True,
            }
        ]
        after = len(client.get("/api/agent/tool-calls").json()["toolCalls"])
        assert after == before


def test_grounded_chat_advances_fine_tune_from_preflight_to_execution() -> None:
    context = _grounded_chat_context(loaded=True)
    context["latestTrainingPreflights"] = [
        {
            "id": "trainrun-ready",
            "lifecycle": "READY",
            "datasetId": "dataset-test",
            "baseModelId": "vla-test",
            "error": None,
        }
    ]
    response = _chat_offline_intent(
        ChatIn(messages=[ChatMessageIn(role="user", content="Fine-tune the current VLA now.")]),
        context,
        "disabled:not_configured",
        "planner",
    )
    assert [action["tool"] for action in response["actions"]] == ["training.vla_jepa.execute_fine_tune"]
    assert response["actions"][0]["arguments"] == {
        "runId": "trainrun-ready",
        "acknowledgeCandidateOnly": True,
    }


def test_grounded_chat_exports_only_recorded_successful_oracle_demonstration() -> None:
    context = _grounded_chat_context(loaded=True)
    context["latestEvaluations"] = [
        {
            "id": "eval-recorded-oracle",
            "success": True,
            "failureCode": None,
            "policy": "deterministic_differential_ik_oracle_v1",
            "robotId": "franka-test",
            "observationFrameCount": 42,
        }
    ]
    response = _chat_offline_intent(
        ChatIn(messages=[ChatMessageIn(role="user", content="Export a LeRobot dataset from the demonstration.")]),
        context,
        "disabled:not_configured",
        "planner",
    )
    assert [action["tool"] for action in response["actions"]] == ["training.datasets.create_from_evaluation"]
    assert response["actions"][0]["arguments"]["evaluationId"] == "eval-recorded-oracle"

    context["latestEvaluations"][0]["observationFrameCount"] = 0
    blocked = _chat_offline_intent(
        ChatIn(messages=[ChatMessageIn(role="user", content="Export a LeRobot dataset.")]),
        context,
        "disabled:not_configured",
        "planner",
    )
    assert blocked["actions"] == []
    assert "recorded observations" in blocked["reply"]


def test_world_operator_rejects_implicit_or_unsupported_execution_contracts() -> None:
    automatic = WorldOperateRequest.model_validate({
        "robotId": "franka",
        "instruction": "Put the apple inside the sink.",
        "backend": "mujoco",
        "controller": "oracle",
        "task": "auto",
        "executionScope": "active_world",
        "worldId": "kitchen",
    })
    assert automatic.task == "auto"
    drop = WorldOperateRequest.model_validate({
        "robotId": "franka",
        "instruction": "Pick up the apple and throw it off the table.",
        "backend": "mujoco",
        "controller": "oracle",
        "task": "drop_off_table",
        "executionScope": "active_world",
        "worldId": "kitchen",
    })
    assert drop.task == "drop_off_table"
    cases = [
        {
            "robotId": "franka",
            "instruction": "Pick up the object and throw it off the table.",
            "backend": "mujoco",
            "controller": "oracle",
            "task": "pick_place",
        },
        {
            "robotId": "franka",
            "instruction": "Pick up the object and place it outside the target.",
            "backend": "mujoco",
            "controller": "oracle",
            "task": "pick_place",
        },
        {
            "robotId": "franka",
            "instruction": "pick the cube",
            "backend": "mujoco",
            "controller": "vla_jepa",
            "task": "pick_place",
        },
        {
            "robotId": "franka",
            "instruction": "run agent",
            "backend": "isaac_sim",
            "controller": "agent",
            "task": "pick_place",
            "assetVersionId": "assetver_test",
            "modelId": "model_test",
        },
        {
            "robotId": "franka",
            "instruction": "open drawer with policy",
            "backend": "mujoco",
            "controller": "vla_jepa",
            "task": "open_drawer",
            "assetVersionId": "assetver_test",
            "modelId": "model_test",
        },
    ]
    with TestClient(app) as client:
        for payload in cases:
            response = client.post("/api/worlds/operate", json=payload)
            assert response.status_code == 422, response.text
            if "throw" in payload["instruction"] or "outside" in payload["instruction"]:
                assert "No simulation was started" in response.text

        live = client.post("/api/worlds/live-sessions", json=cases[0])
        assert live.status_code == 422
        assert "No simulation was started" in live.text

        active_world_id = client.get("/api/worlds/scene").json()["worldId"]
        active_agent = client.post(
            "/api/worlds/operate",
            json={
                "robotId": "franka",
                "modelId": "model_test",
                "assetVersionId": "assetver_test",
                "instruction": "Pick up the apple and place it on top of the blender.",
                "backend": "mujoco",
                "controller": "agent",
                "task": "pick_place",
                "executionScope": "active_world",
                "worldId": active_world_id,
            },
        )
        assert active_agent.status_code == 422
        assert "No validation-bench evaluation was substituted" in active_agent.text

        authored = client.post(
            "/api/worlds/live-sessions",
            json={
                "robotId": "franka",
                "instruction": "Pick up the apple and place it on top of the blender.",
                "backend": "mujoco",
                "controller": "oracle",
                "task": "pick_place",
                "executionScope": "active_world",
                "worldId": active_world_id,
            },
        )
        assert authored.status_code == 422
        assert "No simulation was started" in authored.text


def test_primary_read_contract() -> None:
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["database"] == "healthy"
        assert health.json()["simulation"]["engine"] == "MuJoCo"

        paths = [
            "/api/overview",
            "/api/skills",
            "/api/assets",
            "/api/worlds/scene",
            "/api/sources",
            "/api/training",
            "/api/observability/stats",
            "/api/observability/services",
            "/api/observability/traces",
            "/api/observability/metrics",
            "/api/observability/logs",
            "/api/observability/alerts",
            "/api/settings",
        ]
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
        training = client.get("/api/training").json()
        assert isinstance(training["datasets"], list)
        assert isinstance(training["canonicalRuns"], list)
        assert client.get("/api/skills/open-refrigerator").status_code == 404
        legacy_agent = client.post("/api/agent/run", json={"skillId": "open-refrigerator"})
        assert legacy_agent.status_code == 410
        assert "legacy parameterized-skill agent is disabled" in legacy_agent.text
        services = client.get("/api/observability/services").json()
        canonical_agent = next(item for item in services if item["name"] == "curriculum-planner")
        assert canonical_agent["kind"] == "agent-tool-service"
        assert canonical_agent["status"] == "healthy"


def test_write_only_secrets_survive_section_save() -> None:
    with TestClient(app) as client:
        assert client.put("/api/settings/keys/openai", json={"key": "test-secret-value"}).status_code == 200
        settings = client.get("/api/settings").json()
        assert "test-secret-value" not in settings["models"]["openaiKey"]
        models = settings["models"]
        models["planner"] = "local-model"
        response = client.put("/api/settings/models", json=models)
        assert response.status_code == 200
        assert response.json()["models"]["planner"] == "local-model"
        assert client.get("/api/health").json()["openai"] in {
            "not_configured",
            "configured",
            "checking",
            "healthy",
            "degraded",
        }


def test_brightdata_probe_returns_only_sanitized_evidence(monkeypatch) -> None:
    async def search(query: str):
        return {
            "general": {"search_engine": "google", "query": query},
            "organic": [
                {"title": "One", "link": "https://example.com/product", "secret": "raw-provider-field"},
                {"title": "Two", "link": "https://docs.example.org/item"},
            ],
        }

    monkeypatch.setattr(brightdata, "google_search", search)
    with TestClient(app) as client:
        response = client.post("/api/integrations/brightdata/probe", json={})
        assert response.status_code == 200
        assert response.json() == {
            "connected": True,
            "provider": "Bright Data SERP API",
            "searchEngine": "google",
            "queryMatched": True,
            "organicCount": 2,
            "sampleDomains": ["example.com", "docs.example.org"],
        }
        assert "raw-provider-field" not in response.text


def test_world_mutations_and_checks() -> None:
    with TestClient(app) as client:
        scene = client.get("/api/worlds/scene").json()
        assert scene["sceneTree"]
        for placement in scene["placements"]:
            assert isinstance(placement["rotationZDeg"], (int, float))
            assert placement["mobility"] in {"movable", "fixed"}
            assert placement["collisionApproximation"] in {"convexHull", "none"}
            assert placement["massKg"] > 0
            actual = [placement["worldBounds"][1][i] - placement["worldBounds"][0][i] for i in range(3)]
            assert max(abs(actual[i] - placement["targetDimensions"][i]) for i in range(3)) < 0.002
            assert isinstance(placement["anchor"]["surface"], str) and placement["anchor"]["surface"]
            assert len(placement["baseScale"]) == 3
            assert len(placement["scaleMultiplier"]) == 3
        assert client.put("/api/worlds/scene", json={"sceneTree": scene["sceneTree"], "variants": scene["variants"]}).status_code == 200
        checks = client.post("/api/worlds/checks/run", json={}).json()["physicsChecks"]
        assert checks and all(item["status"] in {"pass", "warn", "fail"} for item in checks)
        if scene["placements"]:
            assert any(item["check"].startswith("Measured mesh fit") for item in checks)
            first = scene["placements"][0]
            moved = [first["translation"][0] + 0.01, first["translation"][1], first["translation"][2]]
            assert client.patch(f"/api/worlds/placements/{first['assetId']}", json={"translation": moved, "rotationZDeg": 15, "scaleMultiplier": [1.1, 1.0, 1.0]}).status_code == 200
            updated = client.get("/api/worlds/scene").json()
            updated_first = next(item for item in updated["placements"] if item["assetId"] == first["assetId"])
            actual = updated_first["translation"]
            assert actual == moved
            assert updated_first["rotationZDeg"] == 15
            assert updated_first["scaleMultiplier"] == [1.1, 1.0, 1.0]
            assert client.patch(f"/api/worlds/placements/{first['assetId']}", json={"scaleMultiplier": [0, 1, 1]}).status_code == 422
            assert client.put("/api/worlds/scene", json={"sceneTree": scene["sceneTree"], "variants": scene["variants"]}).status_code == 200
        camera_probe = client.post("/api/worlds/cameras/probe", json={})
        assert camera_probe.status_code == 200
        for camera in camera_probe.json()["cameras"].values():
            assert camera["shape"] == [256, 256, 3]
            assert camera["dtype"] == "uint8"
            assert camera["nonzero"] > 0
        variant = client.post("/api/worlds/variants", json={"name": "Test clearance", "desc": "API contract test"})
        assert variant.status_code == 200
        assert client.post(f"/api/worlds/variants/{variant.json()['id']}/activate", json={}).status_code == 200


def test_frontend_diagnostics_and_isaac_reports_explicit_runtime_state() -> None:
    with TestClient(app) as client:
        recorded = client.post("/api/diagnostics/frontend-errors", json={
            "source": "react",
            "message": "Cannot read properties of undefined (reading 'toFixed')",
            "stack": "GeneratedAssetInspector",
            "componentStack": "Worlds",
            "route": "/#/worlds",
            "userAgent": "pytest",
        })
        assert recorded.status_code == 202
        diagnostics = client.get("/api/diagnostics/runtime")
        assert diagnostics.status_code == 200
        assert any(row["service"] == "frontend.react" for row in diagnostics.json()["events"])

        status = client.get("/api/simulation/isaac")
        assert status.status_code == 200
        isaac = status.json()
        assert isinstance(isaac["installed"], bool)
        assert isaac["version"] == "6.0.1"
        assert isaac["isaacLabRevision"] == "ffff603eafc6b74264a5261cc0183d6a65390d78"
        assert isinstance(isaac["eulaAcceptedForApiProcess"], bool)
        assert isaac["franka"]["cameras"] == ["front", "wrist"]
        franka = client.post("/api/robots/franka/isaac", json={})
        assert franka.status_code == 201
        assert franka.json()["format"] == "isaac-openusd-reference"

        scene = client.get("/api/worlds/scene").json()
        if scene["placements"]:
            prepared = client.post("/api/simulation/isaac/prepare", json={})
            assert prepared.status_code in {200, 409}
            if prepared.status_code == 200:
                assert prepared.json()["prepared"] is True


def test_robot_import_camera_mapping_and_world_command_gate(monkeypatch) -> None:
    async def no_provider(*args, **kwargs):
        return None, "heuristic:test"

    monkeypatch.setattr(llm, "plan", no_provider)
    urdf = b'''<robot name="test_arm"><link name="base"/><link name="tool"/><joint name="j1" type="revolute"><parent link="base"/><child link="tool"/><limit lower="-1" upper="1" effort="10" velocity="1"/></joint><gazebo reference="tool"><sensor name="wrist"><camera name="wrist_cam"/></sensor></gazebo></robot>'''
    with TestClient(app) as client:
        imported = client.post(
            "/api/robots/import?filename=test_arm.urdf",
            content=urdf,
            headers={"content-type": "application/octet-stream"},
        )
        assert imported.status_code == 201, imported.text
        robot = imported.json()
        assert robot["format"] == "urdf" and robot["joints"] == 1
        assert robot["readiness"]["executable"] is False
        mapped = client.put(f"/api/robots/{robot['id']}", json={"cameraMappings": {
            "observation.images.exterior_1_left": "wrist_cam",
            "observation.images.exterior_2_left": "wrist_cam",
        }})
        assert mapped.status_code == 200
        assert any("fine-tuned checkpoint" in item for item in mapped.json()["readiness"]["blockers"])
        plan = client.post("/api/worlds/commands", json={
            "instruction": "grab the apple and put it in the blender",
            "robotId": robot["id"],
            "mode": "plan",
        })
        assert plan.status_code == 200, plan.text
        assert plan.json()["executionAllowed"] is False
        assert plan.json()["plan"]["steps"]


def test_native_vulkan_frame_and_acceptance_run_fail_closed_without_policy() -> None:
    with TestClient(app) as client:
        probe = client.get("/api/render/vulkan/probe")
        assert probe.status_code == 200, probe.text
        assert probe.json()["backend"] == "Vulkan"
        assert probe.json()["browser3dApi"] == "none"

        frame = client.get("/api/render/vulkan/frame?scene=kitchen&width=480&height=270")
        assert frame.status_code == 200, frame.text
        assert frame.headers["content-type"] == "image/png"
        assert frame.headers["x-robotworld-renderer"] == "Vulkan"
        assert frame.content.startswith(b"\x89PNG\r\n\x1a\n")

        catalog = client.get("/api/demo-scenarios")
        assert catalog.status_code == 200
        assert {item["id"] for item in catalog.json()["scenarios"]} == {"kitchen-juice", "factory-sort"}
        assert catalog.json()["readiness"]["trainingEnabled"] is False

        for scenario_id, seed in (("kitchen-juice", 1048576), ("factory-sort", 2097152)):
            queued = client.post(f"/api/demo-scenarios/{scenario_id}/runs", json={"seed": seed})
            assert queued.status_code == 202, queued.text
            job_id = queued.json()["jobId"]
            job = None
            for _ in range(120):
                response = client.get(f"/api/jobs/{job_id}")
                assert response.status_code == 200, response.text
                job = response.json()
                if job["status"] in {"success", "failed", "blocked"}:
                    break
                time.sleep(0.1)
            assert job is not None
            assert job["status"] == "blocked", job
            assert job["detail"]["result"]["taskSuccess"] is None
            assert job["detail"]["result"]["reason"] == "policy_not_configured"
            assert [stage["status"] for stage in job["detail"]["stages"][:3]] == ["passed", "passed", "passed"]
            assert "joints" in job["detail"]["stages"][1]["detail"]


def test_real_asset_compile_and_openusd_download() -> None:
    with TestClient(app) as client:
        queued = client.post(
            "/api/assets/build",
            json={"query": "Samsung RF28T5001SR refrigerator", "kind": "articulated", "generator": "parametric", "families": []},
        )
        assert queued.status_code == 202, queued.text
        asset_id = queued.json()["assetId"]
        asset = None
        for _ in range(120):
            response = client.get(f"/api/assets/{asset_id}")
            assert response.status_code == 200
            asset = response.json()
            if asset["status"] != "building":
                break
            time.sleep(0.1)
        assert asset is not None
        assert asset["status"] == "ready", asset
        assert asset["lastEvalResult"] == "passed", asset
        assert asset["partGraph"]["lifecycleState"] == "PHYSICS_VALIDATED"
        assert {part["id"] for part in asset["partGraph"]["parts"]} == {"body", "door", "handle"}
        assert {joint["id"] for joint in asset["partGraph"]["joints"]} == {"door_hinge", "handle_mount"}
        assert len(asset["partGraph"]["graphSha256"]) == 64
        sweep = asset["physicsValidation"]["jointSweep"]
        assert sweep["passed"] is True
        assert sweep["handleAttachedToMovingPart"] is True
        assert sweep["handlePathSpanM"] > 0.05
        assert sweep["severePenetrationCount"] == 0
        assert any(item["file"] == "asset.usda" for item in asset["artifacts"])
        usd = client.get(f"/api/assets/{asset_id}/files/asset.usda")
        assert usd.status_code == 200
        assert b"PhysicsRevoluteJoint" in usd.content


def test_trellis_monolithic_mesh_cannot_claim_articulation() -> None:
    with TestClient(app) as client:
        queued = client.post(
            "/api/assets/build",
            json={"query": "one door cabinet", "kind": "articulated", "generator": "trellis2", "families": []},
        )
        assert queued.status_code == 202
        asset_id = queued.json()["assetId"]
        job_id = queued.json()["jobId"]
        job = None
        for _ in range(60):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"failed", "blocked"}:
                break
            time.sleep(0.05)
        assert job is not None
        assert job["status"] == "failed"
        assert "validated PartGraph" in str(job["detail"].get("error"))
        asset = client.get(f"/api/assets/{asset_id}").json()
        assert asset["status"] == "blocked"
        assert "validated PartGraph" in asset["properties"]["pipelineError"]


def test_legacy_preview_session_is_disabled_in_production() -> None:
    with TestClient(app) as client:
        created = client.post("/api/eval/sessions", json={})
        assert created.status_code == 410
        assert "/api/worlds/live-sessions" in created.text
        replay = client.get("/api/eval/sessions/legacy/replay")
        assert replay.status_code == 410
