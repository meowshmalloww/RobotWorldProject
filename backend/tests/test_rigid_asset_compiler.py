from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import time
from pathlib import Path

import pytest
import numpy as np
import trimesh
from fastapi.testclient import TestClient
from PIL import Image

from app.config import ASSETS_DIR, DATA_DIR
from app.db import SessionLocal
from app.main import app
from app.models import ModelRegistrationRecord
from app.services import control_catalog, franka_pick_place, franka_vla_evaluation, usda, vla_bridge, vla_policy_worker


def _source_glb(name: str = "compiled-source", extents: tuple[float, float, float] = (0.20, 0.40, 0.10)) -> Path:
    root = ASSETS_DIR / "compiler-test-inputs"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.glb"
    # glTF dimensions are X=width, Y=height, Z=depth.
    mesh = trimesh.creation.box(extents=extents)
    path.write_bytes(mesh.export(file_type="glb"))
    assert path.read_bytes()[:4] == b"glTF"
    return path


def _pbr_source_glb(
    name: str,
    color: tuple[int, int, int, int],
    extents: tuple[float, float, float] = (0.20, 0.40, 0.10),
) -> Path:
    root = ASSETS_DIR / "compiler-test-inputs"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.glb"
    mesh = trimesh.creation.box(extents=extents)
    uv = np.column_stack(
        (
            np.linspace(0.0, 1.0, len(mesh.vertices), dtype=np.float32),
            np.linspace(1.0, 0.0, len(mesh.vertices), dtype=np.float32),
        )
    )
    mesh.visual = trimesh.visual.TextureVisuals(
        uv=uv,
        material=trimesh.visual.material.PBRMaterial(
            baseColorTexture=Image.new("RGBA", (8, 8), color),
            metallicRoughnessTexture=Image.new("RGB", (8, 8), (255, 128, 64)),
        ),
    )
    path.write_bytes(mesh.export(file_type="glb"))
    return path


def _payload(path: Path, *, dimensions: list[float] | None = None, source_asset_id: str = "compiler-box") -> dict:
    return {
        "displayName": "Measured compiler test box",
        "category": "test_rigid_object",
        "sourceGlbPath": str(path),
        "expectedSourceSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "sourceAssetId": source_asset_id,
        "sourceIdentityScope": "category_prior",
        "dimensionsM": dimensions or [0.20, 0.40, 0.10],
        "dimensionMethod": "controlled_test_measurement",
        "dimensionConfidence": 1.0,
        "massKg": 1.2,
        "massMethod": "controlled_test_measurement",
        "massConfidence": 1.0,
        "frictionRange": [0.4, 0.8],
        "restitutionRange": [0.0, 0.05],
        "semantics": ["test_object"],
        "affordances": ["top_grasp"],
        "licenseMetadata": {"source": "generated in test", "license": "CC0", "redistribution": "allowed"},
        "maxAspectResidual": 0.05,
    }


def test_openusd_visual_preserves_trellis_pbr_channels() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        mesh = trimesh.creation.box(extents=(0.2, 0.3, 0.1))
        uv = np.column_stack(
            (
                np.linspace(0.0, 1.0, len(mesh.vertices), dtype=np.float32),
                np.linspace(1.0, 0.0, len(mesh.vertices), dtype=np.float32),
            )
        )
        base_color = Image.new("RGBA", (8, 8), (210, 80, 30, 255))
        metallic_roughness = Image.new("RGB", (8, 8), (255, 128, 64))
        material = trimesh.visual.material.PBRMaterial(
            baseColorTexture=base_color,
            metallicRoughnessTexture=metallic_roughness,
            metallicFactor=0.25,
            roughnessFactor=0.5,
        )
        mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
        source = root / "trellis-pbr.glb"
        source.write_bytes(mesh.export(file_type="glb"))

        usd_path, report = usda.write_visual_usdc(source, root / "visual.usdc")
        assert usd_path.is_file()
        assert {item["role"] for item in report["textures"]} == {"base_color", "metallic_roughness"}
        assert (root / "basecolor.png").is_file()
        assert (root / "metallic_roughness.png").is_file()
        assert report["sourcePbrPreserved"] is True

        from pxr import Usd, UsdShade

        stage = Usd.Stage.Open(str(usd_path))
        shader = UsdShade.Shader.Get(stage, "/Visual/GeneratedMaterial/PreviewSurface")
        assert shader.GetInput("diffuseColor").HasConnectedSource()
        assert shader.GetInput("roughness").HasConnectedSource()
        assert shader.GetInput("metallic").HasConnectedSource()


def test_openusd_visual_authors_selectable_appearance_variant_set() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        mesh = trimesh.creation.box(extents=(0.2, 0.3, 0.1))
        uv = np.column_stack(
            (
                np.linspace(0.0, 1.0, len(mesh.vertices), dtype=np.float32),
                np.linspace(1.0, 0.0, len(mesh.vertices), dtype=np.float32),
            )
        )

        def write_appearance(path: Path, color: tuple[int, int, int, int]) -> None:
            appearance = mesh.copy()
            appearance.visual = trimesh.visual.TextureVisuals(
                uv=uv.copy(),
                material=trimesh.visual.material.PBRMaterial(
                    baseColorTexture=Image.new("RGBA", (8, 8), color),
                    metallicRoughnessTexture=Image.new("RGB", (8, 8), (255, 128, 64)),
                    metallicFactor=0.25,
                    roughnessFactor=0.5,
                ),
            )
            path.write_bytes(appearance.export(file_type="glb"))

        primary = root / "ripe.glb"
        alternate = root / "green.glb"
        write_appearance(primary, (235, 190, 20, 255))
        write_appearance(alternate, (55, 160, 60, 255))

        usd_path, report = usda.write_visual_usdc(
            primary,
            root / "visual.usdc",
            appearance_variants=[
                {"id": "green", "displayName": "Unripe green", "sourcePath": str(alternate)}
            ],
        )

        from pxr import Usd, UsdShade

        stage = Usd.Stage.Open(str(usd_path))
        variant_set = stage.GetPrimAtPath("/Visual").GetVariantSet("appearance")
        assert set(variant_set.GetVariantNames()) == {"generated", "green"}
        assert variant_set.GetVariantSelection() == "generated"
        assert report["defaultAppearanceVariantId"] == "generated"
        assert {item["id"] for item in report["appearanceVariants"]} == {"generated", "green"}
        assert (root / "green_base_color.png").is_file()

        variant_set.SetVariantSelection("green")
        bound, _ = UsdShade.MaterialBindingAPI(stage.GetPrimAtPath("/Visual/Mesh")).ComputeBoundMaterial()
        assert str(bound.GetPath()) == "/Visual/AppearanceMaterials/green"


def test_openusd_appearance_rejects_changed_geometry() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        primary = root / "primary.glb"
        changed = root / "changed.glb"
        primary.write_bytes(trimesh.creation.box(extents=(0.2, 0.3, 0.1)).export(file_type="glb"))
        changed.write_bytes(trimesh.creation.box(extents=(0.25, 0.3, 0.1)).export(file_type="glb"))
        with pytest.raises(RuntimeError, match="compile it as a new asset version"):
            usda.write_visual_usdc(
                primary,
                root / "visual.usdc",
                appearance_variants=[
                    {"id": "changed", "displayName": "Changed", "sourcePath": str(changed)}
                ],
            )


def test_rigid_compiler_authors_separate_physical_artifacts_and_runs_mujoco() -> None:
    source = _source_glb("valid")
    payload = _payload(source)
    with TestClient(app) as client:
        response = client.post(
            "/api/asset-versions/rigid",
            headers={"Idempotency-Key": "compile-controlled-box-v1"},
            json=payload,
        )
        assert response.status_code == 201, response.text
        envelope = response.json()
        assert envelope["status"] == "SUCCEEDED", envelope
        version = envelope["result"]["assetVersion"]
        assert version["lifecycleState"] == "PHYSICS_VALIDATED"
        assert version["promotionEligible"] is False
        assert "deterministic_oracle_validation_pending" in version["promotionBlockers"]
        assert "source_identity_is_category_prior" in version["promotionBlockers"]
        assert version["validationErrors"] == []

        manifest = version["manifest"]
        assert manifest["schemaVersion"] == "robotworld.asset-manifest.v1"
        assert manifest["coordinateConvention"]["dimensionsOrder"] == "width,height,depth"
        assert manifest["sourceVisual"]["mediaType"] == "model/gltf-binary"
        assert manifest["sourceVisual"]["sha256"] == payload["expectedSourceSha256"]
        assert manifest["collisionArtifacts"][0]["sha256"] != manifest["sourceVisual"]["sha256"]
        assert manifest["runtimeArtifacts"][0]["kind"] == "mujoco_runtime"
        assert any(item["kind"] == "openusd_layer" for item in manifest["openusdArtifacts"])
        assert all(item["immutable"] is True for item in (
            [manifest["sourceVisual"]]
            + manifest["visualArtifacts"]
            + manifest["collisionArtifacts"]
            + manifest["openusdArtifacts"]
            + manifest["runtimeArtifacts"]
            + manifest["validationArtifacts"]
        ))

        report = version["validationReport"]
        assert report["staticValidation"]["passed"] is True
        assert report["collision"]["watertight"] is True
        assert report["collision"]["triangles"] < 1000
        assert report["openusd"]["visualResolved"] is True
        assert report["runtime"]["visualCollisionSeparated"] is True
        assert report["physicsValidation"]["passed"] is True
        assert report["physicsValidation"]["contactObserved"] is True
        assert report["physicsValidation"]["finite"] is True
        assert report["physicsValidation"]["deterministicRepeatMaxQposError"] <= 1e-9

        root = DATA_DIR / version["artifactRoot"]
        assert (root / "source" / "source.glb").is_file()
        assert (root / "visual" / "model.obj").is_file()
        assert (root / "collision" / "convex_hull.obj").is_file()
        assert (root / "openusd" / "asset.usdc").is_file()
        assert (root / "runtime" / "mujoco" / "asset.xml").is_file()
        assert (root / "validation" / "drop_test.xml").is_file()
        saved_manifest = json.loads((root / "manifest.json").read_text(encoding="utf8"))
        assert saved_manifest["manifestSha256"] == version["manifestSha256"]

        replay = client.post(
            "/api/asset-versions/rigid",
            headers={"Idempotency-Key": "compile-controlled-box-v1"},
            json=payload,
        )
        assert replay.status_code == 201
        assert replay.json()["reused"] is True
        assert replay.json()["commandId"] == envelope["commandId"]

        detail = client.get(f"/api/asset-versions/{version['id']}")
        assert detail.status_code == 200
        assert detail.json()["assetVersion"]["manifestSha256"] == version["manifestSha256"]
        listed = client.get("/api/asset-versions")
        assert any(item["id"] == version["id"] for item in listed.json()["assetVersions"])


def test_rigid_compiler_persists_and_serves_texture_only_appearance() -> None:
    primary = _pbr_source_glb("appearance-primary", (235, 190, 20, 255))
    alternate = _pbr_source_glb("appearance-green", (55, 160, 60, 255))
    payload = _payload(primary, source_asset_id="compiler-appearance-box")
    payload["appearanceVariants"] = [
        {
            "id": "green",
            "displayName": "Unripe green",
            "sourceGlbPath": str(alternate),
            "expectedSourceSha256": hashlib.sha256(alternate.read_bytes()).hexdigest(),
        }
    ]
    with TestClient(app) as client:
        response = client.post(
            "/api/asset-versions/rigid",
            headers={"Idempotency-Key": "compile-controlled-appearance-v1"},
            json=payload,
        )
        assert response.status_code == 201, response.text
        version = response.json()["result"]["assetVersion"]
        manifest = version["manifest"]
        assert manifest["defaultAppearanceVariantId"] == "generated"
        assert {item["id"] for item in manifest["appearanceVariants"]} == {"generated", "green"}
        green = next(item for item in manifest["appearanceVariants"] if item["id"] == "green")
        assert green["geometryInvariant"] is True
        assert green["openUsdVariantSet"] == "appearance"
        assert {item["role"] for item in green["textures"]} == {"base_color", "metallic_roughness"}

        selected = client.get(f"/api/asset-versions/{version['id']}/source.glb?appearance=green")
        assert selected.status_code == 200
        assert hashlib.sha256(selected.content).hexdigest() == hashlib.sha256(alternate.read_bytes()).hexdigest()
        missing = client.get(f"/api/asset-versions/{version['id']}/source.glb?appearance=missing")
        assert missing.status_code == 404


def test_rigid_compiler_rejects_nonuniform_shape_without_stretching() -> None:
    source = _source_glb("bad-aspect")
    with TestClient(app) as client:
        response = client.post(
            "/api/asset-versions/rigid",
            json=_payload(source, dimensions=[0.20, 0.40, 0.30], source_asset_id="compiler-bad-aspect"),
        )
        assert response.status_code == 201
        envelope = response.json()
        assert envelope["status"] == "FAILED"
        version = envelope["result"]["assetVersion"]
        assert version["lifecycleState"] == "REJECTED"
        assert version["manifest"] == {}
        assert any("uniform-scale aspect residual" in item for item in version["validationErrors"])
        report = version["validationReport"]
        assert report["stage"] == "STATIC_VALIDATION"
        assert report["passed"] is False
        assert report["sourceGeometry"]["uniformScale"] == pytest.approx(1.0, abs=1e-6)
        assert (DATA_DIR / version["artifactRoot"] / "source" / "source.glb").is_file()


def test_rigid_compiler_blocks_paths_outside_allowlist_and_hash_mismatch() -> None:
    source = _source_glb("hash-guard")
    payload = _payload(source, source_asset_id="compiler-hash-guard")
    payload["expectedSourceSha256"] = "0" * 64
    with TestClient(app) as client:
        mismatch = client.post("/api/asset-versions/rigid", json=payload)
        assert mismatch.status_code == 422
        assert "SHA-256 mismatch" in mismatch.text

        with tempfile.TemporaryDirectory(prefix="robotworld-untrusted-") as temporary:
            external = Path(temporary) / "outside.glb"
            external.write_bytes(source.read_bytes())
            outside_payload = _payload(external, source_asset_id="compiler-outside-root")
            outside = client.post("/api/asset-versions/rigid", json=outside_payload)
            assert outside.status_code == 422
            assert "outside the server allowlist" in outside.text


def test_rigid_compiler_tool_is_schema_validated_and_approval_gated() -> None:
    source = _source_glb("agent-tool")
    payload = _payload(source, source_asset_id="compiler-agent-tool")
    with TestClient(app) as client:
        definitions = client.get("/api/agent/tools").json()["tools"]
        definition = next(item for item in definitions if item["name"] == "assets.rigid.compile")
        assert definition["approvalRequired"] is True
        assert definition["inputSchema"]["additionalProperties"] is False

        denied = client.post(
            "/api/agent/tools/invoke",
            json={
                "toolName": "assets.rigid.compile",
                "arguments": payload,
                "autonomyMode": "EXECUTE_WITH_APPROVAL",
                "actor": "compiler-test-agent",
                "idempotencyKey": "compiler-agent-tool-command",
            },
        )
        assert denied.status_code == 403
        assert "one-use approval" in denied.text


def test_compiler_asset_runs_real_franka_contact_lift_and_place_oracle(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source_glb("franka-graspable", extents=(0.05, 0.05, 0.05))
    payload = _payload(source, dimensions=[0.05, 0.05, 0.05], source_asset_id="compiler-franka-graspable")
    payload["massKg"] = 0.04
    with TestClient(app) as client:
        robot_response = client.post(
            "/api/robots/franka/mujoco",
            json={},
            headers={"Idempotency-Key": "compiler-oracle-franka-registration"},
        )
        assert robot_response.status_code == 201, robot_response.text
        robot_view = robot_response.json()["result"]["robot"]
        robot_id = robot_view["id"]
        compiled = client.post(
            "/api/asset-versions/rigid",
            headers={"Idempotency-Key": "compiler-franka-graspable-v1"},
            json=payload,
        ).json()
        assert compiled["status"] == "SUCCEEDED", compiled
        version_id = compiled["result"]["assetVersion"]["id"]

        response = client.post(
            "/api/evaluations/oracle/compiled-asset-pick-place",
            headers={"Idempotency-Key": "compiler-franka-graspable-oracle-seed-19"},
            json={"robotId": robot_id, "assetVersionId": version_id, "seed": 19},
        )
        assert response.status_code == 201, response.text
        envelope = response.json()
        assert envelope["status"] == "SUCCEEDED", envelope
        evaluation = envelope["result"]["evaluation"]
        result = evaluation["result"]
        assert evaluation["status"] == "SUCCEEDED", evaluation
        assert evaluation["success"] is True
        assert result["policy"] == "deterministic_differential_ik_compiled_asset_oracle_v13"
        assert result["predicate"]["assetVersionId"] == version_id
        assert result["predicate"]["onSupportSurface"] is True
        assert result["predicate"]["settled"] is True
        assert result["predicate"]["released"] is True
        assert result["predicate"]["targetErrorM"] < 0.02
        assert result["predicate"]["settleRotationSpanRad"] < 0.01
        assert result["contactSummary"]["sampledPairs"]["left_finger|pick_object"] > 0
        assert result["contactSummary"]["sampledPairs"]["pick_object|right_finger"] > 0
        assert all(item["finite"] for item in result["trajectory"])

        asset = envelope["result"]["assetVersion"]
        assert asset["lifecycleState"] == "ORACLE_VALIDATED"
        assert "deterministic_oracle_validation_pending" not in asset["promotionBlockers"]
        assert asset["promotionEligible"] is False
        assert asset["validationReport"]["oracleValidation"]["evaluationId"] == evaluation["id"]
        assert asset["validationReport"]["oracleValidation"]["success"] is True

        replay = client.post(
            "/api/evaluations/oracle/compiled-asset-pick-place",
            headers={"Idempotency-Key": "compiler-franka-graspable-oracle-seed-19"},
            json={"robotId": robot_id, "assetVersionId": version_id, "seed": 19},
        )
        assert replay.status_code == 201
        assert replay.json()["reused"] is True
        assert replay.json()["result"]["evaluation"]["id"] == evaluation["id"]

        asset_version = replay.json()["result"]["assetVersion"]

    inference_calls: list[dict] = []

    def bounded_stationary_policy(**arguments):
        inference_calls.append(arguments)
        assert set(arguments["images"]) == {
            "observation.images.exterior_1_left",
            "observation.images.exterior_2_left",
        }
        assert all(Path(path).is_file() for path in arguments["images"].values())
        assert arguments["state"] is None
        return {
            "normalizedAction": [0.0] * 7,
            "checkpointAction": [0.0] * 6 + [1.0],
            "inferenceDurationSeconds": 0.001,
            "checkpointConfigSha256": "b" * 64,
        }

    model = {
        "id": "vla-integration-fixture",
        "revision": 1,
        "modelRevision": "test-only-bounded-policy",
        "contentSha256": "a" * 64,
        "capabilities": {
            "cameraKeys": [
                "observation.images.exterior_1_left",
                "observation.images.exterior_2_left",
            ],
            "imageSize": [64, 64],
            "normalizationRevision": "c" * 64,
        },
    }
    bridge = {
        "executable": True,
        "blockers": [],
        "adapterRevision": vla_bridge.DROID_ADAPTER_REVISION,
        "observationContract": {
            "cameraMapping": {
                "observation.images.exterior_1_left": "front",
                "observation.images.exterior_2_left": "wrist",
            },
            "stateRequired": False,
        },
        "actionContract": {
            "policyControlHz": 50,
            "checkpointRepresentation": "droid_base_cartesian_velocity",
        },
    }
    vla_result, vla_world = franka_vla_evaluation.run_compiled_asset_policy(
        robot_id=robot_id,
        asset_version=asset_version,
        model=model,
        bridge=bridge,
        run_id="vla-controlled-stationary-policy",
        seed=19,
        instruction="Pick up the object and place it in the target.",
        max_policy_steps=2,
        infer_action=bounded_stationary_policy,
    )
    assert vla_result["success"] is False
    assert vla_result["failureCode"] == "grasp_miss"
    assert vla_result["predicate"]["policySteps"] == 2
    assert len(vla_result["trajectory"]) == 2
    assert len(inference_calls) == 2
    assert all(item["finite"] for item in vla_result["trajectory"])
    assert all(item["normalizedAction"] == [0.0] * 7 for item in vla_result["trajectory"])
    assert all(item["controller"]["translationFrame"] == "robot_base" for item in vla_result["trajectory"])
    assert set(vla_result["frameHashes"]) == {"reset", "step_0000", "final"}
    assert set(vla_result["frameHashes"]["reset"]) == {"front", "wrist"}
    assert vla_result["policy"] == "vla-jepa:vla-integration-fixture:r1"
    assert vla_result["worldRuntimeSha256"] == vla_world["runtimeSha256"]
    assert vla_result["predicate"]["assetVersionId"] == version_id
    evaluation_artifact = (
        DATA_DIR
        / "worlds"
        / franka_pick_place.TEMPLATE_ID
        / "evaluations"
        / "vla-controlled-stationary-policy"
        / "evaluation.json"
    )
    assert evaluation_artifact.is_file()
    persisted_vla = json.loads(evaluation_artifact.read_text(encoding="utf8"))
    assert persisted_vla["failureCode"] == "grasp_miss"
    assert persisted_vla["predicate"]["normalizationRevision"] == "c" * 64
    assert persisted_vla["predicate"]["adapterRevision"] == vla_bridge.DROID_ADAPTER_REVISION

    robot_definition_sha256 = hashlib.sha256(
        json.dumps(robot_view["definition"], sort_keys=True, separators=(",", ":")).encode("utf8")
    ).hexdigest()
    model_id = "mdl_vla_evaluation_fixture"
    baseline_model_id = "mdl_vla_baseline_scenario_fixture"
    autonomous_model_id = "mdl_vla_autonomous_oracle_fixture"

    async def seed_loaded_policy() -> None:
        async with SessionLocal() as session:
            session.add(
                ModelRegistrationRecord(
                    id=model_id,
                    revision=1,
                    display_name="Bounded VLA evaluation fixture",
                    roles=["vla_policy"],
                    provider_type="local_path",
                    local_path=str(ASSETS_DIR),
                    model_revision="test-only-bounded-policy",
                    expected_device="cpu",
                    precision="float32",
                    input_schema={},
                    output_schema={"action": {"shape": [7]}},
                    capabilities={
                        "configType": "vla_jepa",
                        "cameraKeys": model["capabilities"]["cameraKeys"],
                        "cameraMapping": bridge["observationContract"]["cameraMapping"],
                        "imageSize": [64, 64],
                        "stateFeaturePresent": False,
                        "stateFeatureDimension": None,
                        "actionDimension": 7,
                        "normalizationRevision": "c" * 64,
                        "embodimentAdapterRevision": vla_bridge.ADAPTER_REVISION,
                        "actionRepresentation": "end_effector_local_delta",
                        "trainedRobotDefinitionSha256": robot_definition_sha256,
                        "policyControlHz": 50,
                    },
                    lifecycle_state="LOADED",
                    health_status="healthy",
                    enabled=True,
                    content_sha256="a" * 64,
                )
            )
            session.add(
                ModelRegistrationRecord(
                    id=baseline_model_id,
                    revision=1,
                    display_name="Untried VLA baseline scenario fixture",
                    roles=["vla_policy"],
                    provider_type="local_path",
                    local_path=str(ASSETS_DIR),
                    model_revision="test-only-untried-policy",
                    expected_device="cpu",
                    precision="float32",
                    input_schema={},
                    output_schema={"action": {"shape": [7]}},
                    capabilities={},
                    lifecycle_state="AVAILABLE",
                    health_status="healthy",
                    enabled=True,
                    content_sha256="d" * 64,
                )
            )
            session.add(
                ModelRegistrationRecord(
                    id=autonomous_model_id,
                    revision=1,
                    display_name="Autonomous oracle-only policy fixture",
                    roles=["vla_policy"],
                    provider_type="local_path",
                    local_path=str(ASSETS_DIR),
                    model_revision="test-only-autonomous-oracle",
                    expected_device="cpu",
                    precision="float32",
                    input_schema={},
                    output_schema={"action": {"shape": [7]}},
                    capabilities={},
                    lifecycle_state="AVAILABLE",
                    health_status="healthy",
                    enabled=True,
                    content_sha256="e" * 64,
                )
            )
            await session.commit()

    async def preserve_test_worker_state() -> int:
        return 0

    asyncio.run(seed_loaded_policy())
    monkeypatch.setattr(control_catalog, "reconcile_local_worker_state", preserve_test_worker_state)
    monkeypatch.setattr(vla_policy_worker, "infer_action", bounded_stationary_policy)
    with TestClient(app) as client:
        vla_response = client.post(
            "/api/evaluations/vla/compiled-asset-pick-place",
            headers={"Idempotency-Key": "compiled-vla-stationary-policy-seed-19"},
            json={
                "robotId": robot_id,
                "assetVersionId": version_id,
                "modelId": model_id,
                "instruction": "Pick up the object and place it in the target.",
                "maxPolicySteps": 2,
                "seed": 19,
            },
        )
        assert vla_response.status_code == 201, vla_response.text
        vla_envelope = vla_response.json()
        assert vla_envelope["status"] == "SUCCEEDED"
        persisted_evaluation = vla_envelope["result"]["evaluation"]
        assert persisted_evaluation["status"] == "FAILED"
        assert persisted_evaluation["success"] is False
        assert persisted_evaluation["failureCode"] == "grasp_miss"
        assert persisted_evaluation["result"]["policy"] == f"vla-jepa:{model_id}:r1"
        assert persisted_evaluation["result"]["predicate"]["policySteps"] == 2
        assert vla_envelope["result"]["assetVersion"]["lifecycleState"] == "ORACLE_VALIDATED"
        assert vla_envelope["result"]["bridge"]["executable"] is True

        vla_replay = client.post(
            "/api/evaluations/vla/compiled-asset-pick-place",
            headers={"Idempotency-Key": "compiled-vla-stationary-policy-seed-19"},
            json={
                "robotId": robot_id,
                "assetVersionId": version_id,
                "modelId": model_id,
                "instruction": "Pick up the object and place it in the target.",
                "maxPolicySteps": 2,
                "seed": 19,
            },
        )
        assert vla_replay.status_code == 201
        assert vla_replay.json()["reused"] is True
        assert vla_replay.json()["result"]["evaluation"]["id"] == persisted_evaluation["id"]

        audit = client.get(
            "/api/audit",
            params={"entity_type": "evaluation", "entity_id": persisted_evaluation["id"]},
        ).json()["events"]
        transitions = {(event["fromState"], event["toState"]) for event in audit if event["action"] == "evaluation.transition"}
        assert transitions >= {("QUEUED", "STARTING"), ("STARTING", "RUNNING"), ("RUNNING", "FAILED")}

        analyzed = client.post(
            f"/api/evaluations/{persisted_evaluation['id']}/analyze",
            headers={"Idempotency-Key": "analyze-compiled-vla-stationary-policy"},
        )
        assert analyzed.status_code == 201, analyzed.text
        analysis_result = analyzed.json()["result"]
        failure_event = analysis_result["classification"]["failureEvent"]
        assert failure_event["code"] == "grasp_miss"
        assert failure_event["certainty"] == "direct_signal"
        assert failure_event["evidence"]["oracleCounterpartEvaluationId"] == evaluation["id"]
        assert failure_event["evidence"]["oracleCounterpartPassed"] is True
        assert failure_event["recommendedAction"]["action"] == "REUSE_VALID_ASSET_TARGETED_VARIATION"
        assert analysis_result["coverageObservation"]["assetVersionId"] == version_id
        assert analysis_result["coverageObservation"]["dimensions"]["size"] == "small"

        plan_response = client.post(
            "/api/curriculum/plan-next",
            headers={"Idempotency-Key": "plan-after-compiled-vla-grasp-miss"},
            json={
                "robotId": robot_id,
                "modelId": model_id,
                "taskFamily": "pick_place",
                "targetSuccessRate": 0.8,
                "minimumAttempts": 5,
                "maxEvaluationEpisodes": 10,
                "maxNewScenarios": 1,
                "lookbackLimit": 50,
                "allowedAssetVersionIds": [version_id],
                "seed": 2301,
            },
        )
        assert plan_response.status_code == 201, plan_response.text
        plan_envelope = plan_response.json()
        plan = plan_envelope["result"]["plan"]
        scenario = plan_envelope["result"]["scenario"]
        assert plan["status"] == "PLANNED"
        assert plan["analysis"]["sampleCount"] == 1
        assert plan["analysis"]["successRate"] == 0.0
        assert plan["analysis"]["topFailureCode"] == "grasp_miss"
        assert plan["decision"]["action"] == "REUSE_EXISTING_VALID_ASSET"
        assert plan["decision"]["nextGate"] == "DETERMINISTIC_ORACLE"
        assert scenario["assetVersionId"] == version_id
        assert scenario["oracleRequired"] is True
        assert scenario["specification"]["assetReuseRequired"] is True
        assert scenario["specification"]["variationDimensions"] == ["object_pose", "orientation"]

        coverage = client.get(
            "/api/coverage",
            params={"robotId": robot_id, "modelId": model_id, "taskFamily": "pick_place"},
        )
        assert coverage.status_code == 200
        coverage_body = coverage.json()
        assert coverage_body["sampleCount"] == 1
        assert coverage_body["uniqueScenarioCount"] == 1
        assert coverage_body["failureCounts"] == {"grasp_miss": 1}
        assert coverage_body["dimensions"]["size"]["counts"]["small"] == 1

        plan_replay = client.post(
            "/api/curriculum/plan-next",
            headers={"Idempotency-Key": "plan-after-compiled-vla-grasp-miss"},
            json={
                "robotId": robot_id,
                "modelId": model_id,
                "taskFamily": "pick_place",
                "targetSuccessRate": 0.8,
                "minimumAttempts": 5,
                "maxEvaluationEpisodes": 10,
                "maxNewScenarios": 1,
                "lookbackLimit": 50,
                "allowedAssetVersionIds": [version_id],
                "seed": 2301,
            },
        )
        assert plan_replay.status_code == 201
        assert plan_replay.json()["reused"] is True
        assert plan_replay.json()["result"]["scenario"]["id"] == scenario["id"]

        independently_replanned = client.post(
            "/api/curriculum/plan-next",
            headers={"Idempotency-Key": "plan-after-compiled-vla-grasp-miss-independent"},
            json={
                "robotId": robot_id,
                "modelId": model_id,
                "taskFamily": "pick_place",
                "targetSuccessRate": 0.8,
                "minimumAttempts": 5,
                "maxEvaluationEpisodes": 10,
                "maxNewScenarios": 1,
                "lookbackLimit": 50,
                "allowedAssetVersionIds": [version_id],
                "seed": 2301,
            },
        )
        assert independently_replanned.status_code == 201
        assert independently_replanned.json()["reused"] is False
        assert independently_replanned.json()["result"]["plan"]["decision"]["scenarioReused"] is True
        assert independently_replanned.json()["result"]["scenario"]["id"] == scenario["id"]

        targeted_execution = client.post(
            f"/api/scenario-specs/{scenario['id']}/oracle",
            headers={"Idempotency-Key": "execute-targeted-pose-orientation-oracle"},
        )
        assert targeted_execution.status_code == 201, targeted_execution.text
        targeted_result = targeted_execution.json()["result"]
        assert targeted_result["execution"]["status"] in {"SUCCEEDED", "FAILED"}
        assert targeted_result["scenario"]["lifecycleState"] in {"ORACLE_VALIDATED", "REJECTED"}
        assert targeted_result["evaluation"]["status"] in {"SUCCEEDED", "FAILED"}
        targeted_placement = targeted_result["evaluation"]["result"]["predicate"]["placementEvidence"]
        assert targeted_placement["requestedPositionVariation"] is True
        assert targeted_placement["requestedOrientationVariation"] is True
        assert targeted_placement["selectionMode"] == "seeded_graspable_yaw_variation"
        assert targeted_placement["seed"] == 2301
        assert targeted_placement["initialSeverePenetrations"] == 0
        assert min(targeted_placement["clearanceM"]) >= 0.015
        assert targeted_placement["sampledSupportPositionM"] != result["predicate"]["placementEvidence"]["sampledSupportPositionM"]
        assert targeted_result["evaluation"]["result"]["worldRuntimeSha256"] != evaluation["result"]["worldRuntimeSha256"]
        targeted_world = targeted_result["worldTemplate"]
        assert targeted_world["placementRequest"]["scenarioFingerprint"] == scenario["scenarioFingerprint"]
        assert len(targeted_world["placementFingerprint"]) == 64
        assert targeted_world["runtimeSha256"] == targeted_result["evaluation"]["result"]["worldRuntimeSha256"]

        asset_after_targeted = client.get(f"/api/asset-versions/{version_id}").json()["assetVersion"]
        assert asset_after_targeted["lifecycleState"] == "ORACLE_VALIDATED"
        assert asset_after_targeted["validationReport"]["oracleValidation"]["evaluationId"] == evaluation["id"]

        baseline_plan = client.post(
            "/api/curriculum/plan-next",
            headers={"Idempotency-Key": "plan-untried-policy-baseline-scenario"},
            json={
                "robotId": robot_id,
                "modelId": baseline_model_id,
                "taskFamily": "pick_place",
                "targetSuccessRate": 0.8,
                "minimumAttempts": 5,
                "maxEvaluationEpisodes": 10,
                "maxNewScenarios": 1,
                "lookbackLimit": 50,
                "allowedAssetVersionIds": [version_id],
                "seed": 2317,
            },
        )
        assert baseline_plan.status_code == 201, baseline_plan.text
        baseline_scenario = baseline_plan.json()["result"]["scenario"]
        assert baseline_scenario["lifecycleState"] == "PLANNED"
        assert baseline_scenario["specification"]["variationDimensions"] == ["baseline_policy_evaluation"]

        oracle_execution = client.post(
            f"/api/scenario-specs/{baseline_scenario['id']}/oracle",
            headers={"Idempotency-Key": "execute-untried-policy-baseline-oracle"},
        )
        assert oracle_execution.status_code == 201, oracle_execution.text
        oracle_envelope = oracle_execution.json()
        assert oracle_envelope["status"] == "SUCCEEDED"
        execution_result = oracle_envelope["result"]
        assert execution_result["scenario"]["lifecycleState"] == "ORACLE_VALIDATED"
        assert execution_result["execution"]["status"] == "SUCCEEDED"
        assert execution_result["execution"]["evaluationId"] == execution_result["evaluation"]["id"]
        assert execution_result["evaluation"]["status"] == "SUCCEEDED"
        assert execution_result["evaluation"]["success"] is True
        assert execution_result["analysis"]["classification"]["failureEvent"] is None
        assert execution_result["analysis"]["coverageObservation"]["success"] is True

        execution_replay = client.post(
            f"/api/scenario-specs/{baseline_scenario['id']}/oracle",
            headers={"Idempotency-Key": "execute-untried-policy-baseline-oracle"},
        )
        assert execution_replay.status_code == 201
        assert execution_replay.json()["reused"] is True
        assert (
            execution_replay.json()["result"]["evaluation"]["id"]
            == execution_result["evaluation"]["id"]
        )

        repeated_execution = client.post(
            f"/api/scenario-specs/{baseline_scenario['id']}/oracle",
            headers={"Idempotency-Key": "execute-untried-policy-baseline-oracle-again"},
        )
        assert repeated_execution.status_code == 409
        assert "must be PLANNED" in repeated_execution.text

        executions = client.get("/api/scenario-executions").json()["executions"]
        persisted_execution = next(item for item in executions if item["id"] == execution_result["execution"]["id"])
        assert persisted_execution["status"] == "SUCCEEDED"
        execution_audit = client.get(
            "/api/audit",
            params={"entity_type": "scenario_execution", "entity_id": persisted_execution["id"]},
        ).json()["events"]
        execution_transitions = {(event["fromState"], event["toState"]) for event in execution_audit}
        assert execution_transitions >= {
            (None, "STARTING"),
            ("STARTING", "RUNNING"),
            ("RUNNING", "SUCCEEDED"),
        }
        scenario_audit = client.get(
            "/api/audit",
            params={"entity_type": "scenario_spec", "entity_id": baseline_scenario["id"]},
        ).json()["events"]
        scenario_transitions = {(event["fromState"], event["toState"]) for event in scenario_audit}
        assert scenario_transitions >= {
            ("PLANNED", "ORACLE_VALIDATING"),
            ("ORACLE_VALIDATING", "ORACLE_VALIDATED"),
        }

        def wait_for_autonomous_terminal(run_id: str) -> dict:
            # This exercises real MuJoCo rendering plus the persisted oracle
            # and VLA phases on Windows. Keep a bounded but production-realistic
            # ceiling; 20 seconds is shorter than one valid oracle+VLA cycle on
            # the supported laptop GPU/GL stack.
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                current = client.get(f"/api/autonomous-runs/{run_id}")
                assert current.status_code == 200, current.text
                run = current.json()["run"]
                if run["lifecycleState"] in {"SUCCEEDED", "STOPPED", "BLOCKED", "CANCELLED", "CRASHED"}:
                    return run
                time.sleep(0.05)
            raise AssertionError(f"Autonomous run {run_id} did not reach a terminal state")

        autonomous_start = client.post(
            "/api/autonomous-runs",
            headers={"Idempotency-Key": "autonomous-oracle-only-controlled-asset"},
            json={
                "autonomyMode": "AUTONOMOUS_WITH_BUDGETS",
                "robotId": robot_id,
                "modelId": autonomous_model_id,
                "taskFamily": "pick_place",
                "executeVla": False,
                "allowedAssetVersionIds": [version_id],
                "seed": 2331,
                "budgets": {
                    "maxWorlds": 1,
                    "maxScrapeRequests": 0,
                    "maxGpuMinutes": 0,
                    "maxEvaluationEpisodes": 1,
                    "maxRetries": 0,
                    "maxIterations": 1,
                    "maxConsecutiveFailures": 1,
                },
            },
        )
        assert autonomous_start.status_code == 202, autonomous_start.text
        autonomous_id = autonomous_start.json()["result"]["run"]["id"]
        autonomous_run = wait_for_autonomous_terminal(autonomous_id)
        assert autonomous_run["lifecycleState"] == "SUCCEEDED"
        assert autonomous_run["stopReason"] == "oracle_gate_complete"
        assert autonomous_run["state"]["consumed"] == {
            "worlds": 1,
            "scrapeRequests": 0,
            "gpuMinutes": 0.0,
            "evaluationEpisodes": 1,
        }
        assert [item["phase"] for item in autonomous_run["state"]["history"]] == ["PLAN_NEXT", "ORACLE"]
        assert autonomous_run["state"]["history"][-1]["success"] is True

        autonomous_replay = client.post(
            "/api/autonomous-runs",
            headers={"Idempotency-Key": "autonomous-oracle-only-controlled-asset"},
            json={
                "autonomyMode": "AUTONOMOUS_WITH_BUDGETS",
                "robotId": robot_id,
                "modelId": autonomous_model_id,
                "taskFamily": "pick_place",
                "executeVla": False,
                "allowedAssetVersionIds": [version_id],
                "seed": 2331,
                "budgets": {
                    "maxWorlds": 1,
                    "maxScrapeRequests": 0,
                    "maxGpuMinutes": 0,
                    "maxEvaluationEpisodes": 1,
                    "maxRetries": 0,
                    "maxIterations": 1,
                    "maxConsecutiveFailures": 1,
                },
            },
        )
        assert autonomous_replay.status_code == 202
        assert autonomous_replay.json()["reused"] is True
        assert autonomous_replay.json()["result"]["run"]["id"] == autonomous_id

        closed_loop_start = client.post(
            "/api/autonomous-runs",
            headers={"Idempotency-Key": "autonomous-oracle-vla-analysis-controlled-asset"},
            json={
                "autonomyMode": "AUTONOMOUS_WITH_BUDGETS",
                "robotId": robot_id,
                "modelId": model_id,
                "taskFamily": "pick_place",
                "executeVla": True,
                "maxPolicySteps": 2,
                "allowedAssetVersionIds": [version_id],
                "seed": 2351,
                "budgets": {
                    "maxWorlds": 1,
                    "maxScrapeRequests": 0,
                    "maxGpuMinutes": 1,
                    "maxEvaluationEpisodes": 2,
                    "maxRetries": 0,
                    "maxIterations": 1,
                    "maxConsecutiveFailures": 1,
                },
            },
        )
        assert closed_loop_start.status_code == 202, closed_loop_start.text
        closed_loop_id = closed_loop_start.json()["result"]["run"]["id"]
        closed_loop_run = wait_for_autonomous_terminal(closed_loop_id)
        assert closed_loop_run["lifecycleState"] == "STOPPED"
        assert closed_loop_run["stopReason"] == "consecutive_failure_stop"
        assert closed_loop_run["state"]["consumed"]["worlds"] == 1
        assert closed_loop_run["state"]["consumed"]["evaluationEpisodes"] == 2
        assert [item["phase"] for item in closed_loop_run["state"]["history"]] == [
            "PLAN_NEXT",
            "ORACLE",
            "VLA",
        ]
        assert closed_loop_run["state"]["history"][1]["success"] is True
        vla_activity = closed_loop_run["state"]["history"][2]
        assert vla_activity["success"] is False
        assert vla_activity["failureCode"] == "grasp_miss"
        assert vla_activity["analysisCommandId"]
        autonomous_vla_evaluation = client.get(
            f"/api/evaluations/{vla_activity['evaluationId']}"
        ).json()
        assert autonomous_vla_evaluation["status"] == "FAILED"
        assert autonomous_vla_evaluation["policy"] == f"vla-jepa:{model_id}:r1"

        blocked_start = client.post(
            "/api/autonomous-runs",
            headers={"Idempotency-Key": "autonomous-vla-fail-closed-controlled-asset"},
            json={
                "autonomyMode": "AUTONOMOUS_WITH_BUDGETS",
                "robotId": robot_id,
                "modelId": baseline_model_id,
                "taskFamily": "pick_place",
                "executeVla": True,
                "allowedAssetVersionIds": [version_id],
                "seed": 2317,
                "budgets": {
                    "maxWorlds": 1,
                    "maxScrapeRequests": 0,
                    "maxGpuMinutes": 1,
                    "maxEvaluationEpisodes": 2,
                    "maxRetries": 0,
                    "maxIterations": 1,
                    "maxConsecutiveFailures": 1,
                },
            },
        )
        assert blocked_start.status_code == 202, blocked_start.text
        blocked_id = blocked_start.json()["result"]["run"]["id"]
        blocked_run = wait_for_autonomous_terminal(blocked_id)
        assert blocked_run["lifecycleState"] == "BLOCKED"
        assert blocked_run["stopReason"] == "vla_bridge_unavailable"
        assert blocked_run["state"]["consumed"]["evaluationEpisodes"] == 0
        assert blocked_run["state"]["history"][-1]["reusedValidatedScenario"] is True
        assert blocked_run["state"]["blockers"]
        assert blocked_run["error"] == "; ".join(blocked_run["state"]["blockers"])
        assert any(
            "expected 7" in blocker or "expected 2" in blocker or "Policy model is AVAILABLE" in blocker
            for blocker in blocked_run["state"]["blockers"]
        )

        run_audit = client.get(
            "/api/audit",
            params={"entity_type": "autonomous_curriculum_run", "entity_id": autonomous_id},
        ).json()["events"]
        run_transitions = {(event["fromState"], event["toState"]) for event in run_audit}
        assert run_transitions >= {
            (None, "QUEUED"),
            ("QUEUED", "STARTING"),
            ("STARTING", "RUNNING"),
            ("RUNNING", "SUCCEEDED"),
        }
