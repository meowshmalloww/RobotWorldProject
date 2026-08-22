# CLAUDE.md — RobotWorldProject

Last full-codebase audit: 2026-08-21. Evidence basis: direct read of every backend service, worker,
test file, frontend page/component, ops config, and the three governing docs
(`Doc/RobotWorldProject_Codex_Master_Prompt.md`, `Doc/DGX Spark hackathon WE ARE WINNING.md`,
`docs/CODEX_EXECUTION_STATE.md`).

---

## 1. What this project is

**RobotWorld** is a local-first desktop system for **physical AI world-building**: it compiles
provenance-tagged (web-evidence → generated) 3D objects into physically valid interactive assets,
validates them in real MuJoCo contact physics with a pinned Franka Panda embodiment and a
deterministic IK oracle, evaluates a real VLA-JEPA vision-language-action policy in an isolated GPU
worker, diagnoses measured failures into a structured taxonomy, and drives a failure-driven
curriculum that plans the next most-informative scenario.

One-sentence pitch: *an autonomous evidence-to-interactive-world compiler and failure-driven
curriculum engine for physical AI.*

**Training is not the product — building the exact worlds the robot needs is the product.**

### Governing documents (read order)

| Document | Role |
|---|---|
| `Doc/RobotWorldProject_Codex_Master_Prompt.md` | **Authoritative scope**. Overrides older docs: Port deferred; self-hosted SigNoz only (no cloud); no Isaac Sim install requirement; MuJoCo default physics; path-based local models; 12-phase implementation order; "things you must not do" list. |
| `Doc/DGX Spark hackathon WE ARE WINNING.md` | Original hackathon vision: Bright Data + SigNoz + Port sponsor loop, self-healing scraper pipeline, TRELLIS.2 asset generation, OpenUSD SimReady compilation. Some decisions in it are superseded by the master prompt (Port, SigNoz Cloud). |
| `Doc/PRODUCTION_ACCEPTANCE.md` | Release evidence gate matrix (what may be claimed as passed vs pending). |
| `Doc/REAL_SYSTEM_TEST_RUNBOOK.md` | Exact Windows test order incl. WSL2 SigNoz install, Bright Data probes, installer smoke. |
| `docs/CODEX_EXECUTION_STATE.md` | Live progress journal with exact commands, run IDs, and honest status per feature. Update this when completing work. |

### Hard scope rules (never violate)

- **Port.io is deferred/disabled.** Route `/api/integrations/port/sync` returns 404 unless
  `ROBOTWORLD_ENABLE_DEFERRED_PORT` is truthy (`backend/app/main.py:63`, `main.py:4094`).
- **SigNoz = self-hosted Community**, keyless OTLP at `http://127.0.0.1:4318`; query API needs a
  *local* service-account key stored server-side only. Never SigNoz Cloud.
- **No fake success.** Missing model / timeout / bad adapter ⇒ episode fails honestly
  (`policy_not_configured`, `grasp_miss`, `invalid_action`, …). Never convert a blocked gate into a
  pass. Never animate success in UI.
- **Training is disabled by product decision**: `POST /api/training/runs` always 409
  (`main.py:3792`). Only the approval-gated preflight → bounded-execute (1–10 steps) → candidate
  decision path exists.
- **No silent mock fallbacks anywhere in production paths** (policy inference, rendering, evidence).
- Status vocabulary to use everywhere: `IMPLEMENTED_AND_TESTED`, `IMPLEMENTED_NOT_LIVE_TESTED`,
  `PARTIAL`, `BROKEN`, `MOCK_ONLY`, `MISSING`, `BLOCKED_BY_CREDENTIAL`, `BLOCKED_BY_HARDWARE`,
  `BLOCKED_BY_LICENSE`.

---

## 2. Architecture

```text
React 19 + TypeScript + Electron desktop shell (secure host, contextIsolation/sandbox)
        │ HTTP :8000 + WebSocket (loopback)          dev UI: Vite :5173
        ▼
FastAPI control plane (backend/app/main.py, ~4,300 lines)
  ├─ SQLite catalog (async SQLAlchemy/aiosqlite) + filesystem artifact store (backend/data/)
  ├─ Durable command store: idempotency keys, one-use approvals bound to exact arg hashes, audit events
  ├─ MuJoCo 3.11 physics (500 Hz physics / 50 Hz control) via SimulationBackend interface
  ├─ Pinned Franka Panda (MuJoCo Menagerie) + deterministic differential-IK oracles
  ├─ Isolated VLA-JEPA policy worker subprocess (own venv, offline-forced, secrets-stripped)
  ├─ Rigid asset compiler: GLB QA → collision/mass/inertia → OpenUSD + MJCF → drop/settle physics test
  ├─ Bright Data client (SERP probe + Scraper Studio DCA collectors) + governed self-heal state machine
  ├─ OpenTelemetry → durable local mirror + OTLP export to self-hosted SigNoz Community
  └─ Native pygfx/wgpu Vulkan offscreen renderer (browser displays PNG frames, no WebGL claim)
```

### Ports & processes

| Port | Service |
|---|---|
| 8000 | FastAPI API + WebSockets (`/ws/events`, `/ws/live/{id}`, `/ws/worlds/live/{id}`) |
| 5173 | Vite dev server |
| 8080 | SigNoz Community UI/query (Docker inside WSL2 Ubuntu) |
| 4317/4318 | SigNoz OTLP gRPC/HTTP receivers |
| 8188 | Default TRELLIS gateway endpoint setting (`backend/trellis_gateway.py`) |
| 8090/8091 | Reference gateways `model_services/groot_gateway.py` / `trellis2_gateway.py` |

### Directory map

```text
backend/
  app/main.py               # ALL HTTP routes (~150+), WebSockets, chat planner, lifespan/bootstrap
  app/config.py             # EnvSettings + DEFAULT_SETTINGS (model roots, integrations, simulation)
  app/contracts.py          # Pydantic contracts (EvaluationResultContract, VlaNormalizedAction, ...)
  app/models.py             # SQLAlchemy records (assets, evaluations, commands, approvals, failures...)
  app/db.py                 # sqlite+aiosqlite engine at DATA_DIR/robotworld.db
  app/telemetry.py          # OTel tracer/meter/logger + SQLite mirror + drain loop
  app/services/             # ~50 service modules (see §5)
  workers/                  # subprocess workers: vla_policy_worker, lerobot_dataset_worker,
                            #   lerobot_training_worker/_execute_worker, isaac_lab_pick_place
  scripts/                  # diag + live-run drivers (run_live_franka_stream.py etc.)
  tests/                    # 16 files, 86 tests passing (pytest)
  trellis_gateway.py        # Local TRELLIS.2-4B CUDA gateway (runs in its own venv)
  isaac_bridge.py           # Isaac-side stage loader (run under Isaac's own python; wants Sim 5.1!)
  run_server.py             # PyInstaller entry point
  robotworld-api.spec       # onedir sidecar build; torch excluded
frontend/
  electron/main.cjs         # secure host: sandbox, no nodeIntegration, deny-all permissions,
                            #   owns FastAPI sidecar lifecycle, loads http://127.0.0.1:8000
  src/pages/                # Overview, Models, Robots, Worlds, Assets, AssetDetail, Skills,
                            #   SkillDetail, Sources, Evidence, ScraperRepair, Simulation (redirect),
                            #   FailureAnalysis, Training, Observability, AgentControl, Settings
  src/components/three/     # WorldEditorCanvas + AuthoritativeSimulationCanvas (three.js/WebGL for
                            #   editor/orbit), NativeVulkanCanvas + Viewport (2D blit of server PNGs)
  src/components/ai/AiChatPanel.tsx   # grounded chat w/ tool cards + approve&run flow
  release/                  # built NSIS installer + win-unpacked (build output; ignore)
model_services/             # reference gateways: groot_gateway (GR00T→ZMQ), trellis2_gateway
ops/signoz/                 # Foundry casting.yaml pinning signoz v0.137.1 + collector digest;
                            #   compose.override.yaml; install-in-wsl.sh guards
ops/install_dinov3.ps1      # DINOv3 ViT-L/16 conditioner installer for TRELLIS gateway
docs/CODEX_EXECUTION_STATE.md   # progress journal — keep updated
.downloads/                 # ignored downloads (foundry tarball, trellis cuda zip, benchmark image)
backend/data/               # runtime artifacts: worlds/, assets/, robots/, datasets/,
                            #   training-runs/, evidence/, demos/, models/, workers/
```

### External model/runtime locations (path-based config, never auto-download)

| Path var / default | Purpose |
|---|---|
| `VLA_JEPA_PYTHON` → `D:\RobotWorldRuntimes\vla-env\Scripts\python.exe` | Isolated VLA/training interpreter |
| `LEROBOT_REPO_PATH` → `D:\LeRobot` | LeRobot checkout (+ installed `[dataset]`, `[training]` extras) |
| VLA checkpoint `D:\VLA-JEPA-Pretrain` (base `mdl_1a88cd40`) | VLA-JEPA 2.59B-param policy, LOADED on cuda:0 |
| `D:\RobotWorldRuntimes\model-metadata\Qwen3-VL-2B-Instruct` | Qwen metadata-only bootstrap dir |
| `D:\TRELLIS.2-runtime\.venv` + `D:\TRELLIS.2-4B` + `D:\DINOv3` | TRELLIS.2 gateway runtime |
| `models.modelRoots` in settings + env allowlists (`ROBOTWORLD_MODEL_ROOTS`, `ROBOT_ASSET_ROOT`, `ROBOTWORLD_ASSET_IMPORT_ROOTS`) | Only these roots may supply model/source files |

---

## 3. Run / test / build

```powershell
# Backend dev (from backend/)
py -3.11 -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Frontend dev (from frontend/) — Electron owns the sidecar
npm ci ; npm run dev:electron      # or: npm run dev + separate API terminal

# Tests / checks
cd backend  ; .\.venv\Scripts\python.exe -m pytest -q            # 86 passed as of 2026-08-21
cd frontend ; npm.cmd run typecheck ; npm.cmd run lint ; npm.cmd run build

# Desktop installer (NSIS + PyInstaller sidecar)
cd frontend ; npm run dist    # -> release/RobotWorld Setup 1.0.0.exe

# Live authoritative stream demo
backend\.venv\Scripts\python.exe backend/scripts/run_live_franka_stream.py `
  --robot-id franka-panda-mujoco-f9a4918f6663 `
  [--active-world-id door-validation-lab --instruction "Pick up the apple and place it on top of the blender."]
```

Notes: run pytest from `backend/` (repo-root invocation breaks `app` import path); use `npm.cmd`
under PowerShell execution policy. Health: `GET /api/health`; Vulkan probe: `GET /api/render/vulkan/probe`
(must report `backend: Vulkan`, `browser3dApi: none`, hardware adapter).

---

## 4. Core invariants enforced by tests (do not regress)

1. **Determinism**: oracle repeats bit-stable (≤1e-9 qpos drift across repeats); seeded placement
   randomization derives from SHA-256 digests of `{seed}:{fingerprint}` at compile time; same seed ⇒
   identical runtime XML hash.
2. **Hash-chained provenance end-to-end**: Menagerie revision + source SHA → immutable derived Franka
   runtime SHA (re-checked every load) → compiled artifact SHA verified before loading → world XML
   reopened post-settle so hash covers validated artifact → frame PNG hashes recorded → dataset
   manifest/info SHA → training input-manifest SHA → candidate weights SHA → promotion-decision evidence.
3. **Fail-closed gates**: VLA runner requires model `LOADED/healthy/enabled` + asset
   `ORACLE_VALIDATED` + bridge `executable`; oracle-before-VLA is structural (authored-scene VLA needs
   prior passing oracle on the exact template; scenario spec must set `oracleBeforeVla=true`).
4. **Privileged-information hygiene**: policies receive only declared cameras + proprioception +
   instruction; never evaluator predicates/target state. Oracle uses privileged state and is labelled.
5. **One-use approvals bound to exact normalized arguments** (hash-compared); mutations denied in
   `OBSERVE_ONLY`; unbudgeted autonomous evaluation refused.
6. **Worker isolation**: VLA worker is a real separate process with sanitized env (provider secrets
   stripped, `HF_HUB_OFFLINE=1`), images pass as server-side paths under allowlisted roots, never bytes.
7. **Write-only secrets**: reads return masked values; masked value can't overwrite real secret;
   section saves preserve secrets.
8. **Restart reconciliation**: stale RUNNING executions → CRASHED/"retryable PLANNED"; autonomous runs
   resume exact persisted phase; evidence collections resume persisted snapshots without re-billing;
   uncertain provider triggers fail explicitly rather than double-charging.

---

## 5. Subsystem cheat-sheet (services → what they do)

### Physics & robot
- `simulation_backend.py` — tiny ABC: load_world/reset/step/apply_action/state/contacts/render_rgb/close.
  Sole implementer: `MujocoFrankaBackend` in `franka_pick_place.py`.
- `franka.py` — pinned Menagerie rev `feadf76d42f8a2162426f7d226a3b539556b3bf5`; derives immutable
  runtime scene (front cam `[1.15,-1.15,0.95]→[0.35,0,0.42]`, wrist cam parented to `hand`,
  site `franka_ee`, calibration table/target, `home` keyframe); registration-time validation:
  penetration scan, 250-step stability, gripper stroke, per-camera pixel-content calibration.
- `franka_pick_place.py` (~98 KB) — `MujocoFrankaBackend`, compile-time placement sampling/settle
  (3000-step drop validation; accepted pose rewritten into XML), elliptic/Newton solver tuning,
  DLS differential IK, three oracle classes (baseline, compiled-asset, authored-scene) with phases
  reset→pre_grasp→approach→axis-alignment→close(bilateral gate)→lift→transport→place(support-contact
  early stop)→release→retract→adaptive settle; final predicates containment ≤1 mm residual ∧
  persistent support contact ∧ released ∧ settled.
- `franka_articulation.py` — controlled prismatic drawer fixture (`controlled_not_product_evidence`),
  handle rigidly parented to moving body, joint sweep test, drawer-open oracle.
- `simcore.py` — legacy fridge-door rig + ScriptedController + sticky-grasp convention (legacy stack).
- `live.py` / `franka_live.py` — legacy session streaming vs authoritative Franka stream (25 Hz cap,
  front 640×360 + wrist 256×144 composite JPEG q84, queue maxsize 4 drop-oldest, runs the SAME
  persisted oracle as batch evaluations so streamed evidence == recorded evidence).

### Assets
- `rigid_asset_compiler.py` — stages STATIC_VALIDATION (NaN/degenerate/dup-face/component census/
  aspect-residual uniform-scale gate) → COLLISION+MASS (≤512-pt convex hull, watertight, inertia
  eigen-decomposition + triangle inequality) → OpenUSD `.usdc` (RigidBody+Mass APIs, convexHull
  MeshCollisionAPI, material binding, reopen-validated) → MJCF runtime + drop_test.xml → real MuJoCo
  6 s drop×2 determinism test. Version dirs `ASSETS/<id>/vNNNN/{source,visual,collision,openusd,
  runtime,validation,previews}` + self-hashed `manifest.json`. Promotion blockers include
  `dimension_confidence_below_0.80`, `mass_confidence_below_0.70`, license/redistribution gates.
- `usda.py` — USDPhysics layer, visual layer converting GLB (Y-up→Z-up) with extracted
  basecolor/metallic-roughness/normal/emissive textures, appearance VariantSet (geometry-hash-gated),
  visual_only wrapper stamped `visual_only_pending_measurement`, world assembly layers with
  convex hull dynamic colliders.
- `geometry.py`, `world_geometry.py`, `mjcf.py` — parametric helpers + legacy fridge appliance MJCF.

### Models / VLA / training
- `model_registry.py` — stateless inspection: path allowlist resolution, SSRF policy for endpoints,
  role-based required-file checks, safetensors header stats reading (normalization min/max/q01/q99),
  HF revision from etag metadata, embodiment contract enforcement
  (`actionRepresentation=end_effector_local_delta`, cameraMapping exactly {front,wrist}, etc.).
  Also `inspect_trellis` (native BF16 low-VRAM vs GGUF Q4 descriptors; GGUF marked inactive/not
  production-ready).
- `local_vla.py` + `workers/vla_policy_worker.py` — protocol `robotworld.vla-worker.v1`;
  ops probe/load/infer/unload/status/shutdown over JSON lines; fails closed on any probe blocker;
  metadata-only Qwen bootstrap via scoped loader patch (weights come from VLA checkpoint safetensors);
  normalized-action clamp with clip-evidence; gripper postprocessor output must be exactly −1/+1.
- `vla_bridge.py` — adapters `franka-cartesian-delta-v1` (physical EE-local delta ≤0.05 m/0.20 rad)
  and `droid-franka-cartesian-velocity-v1` (normalized base-frame velocity × 0.075 m / 0.15 rad);
  `shapeCompatible` vs `executable` distinction; zero-shot bridge attach is user-authorized and
  carries `calibrationValidated: False`.
- `franka_vla_evaluation.py` — obs contract (checkpoint camera keys @224², optional 8-D proprio
  [xyz,quat,gripper], instruction, adapter+normalization revisions), safety workspace box, 0.12 rad
  joint clamp, failure codes `worker_crash|invalid_action|policy_instability|grasp_miss|grasp_slip|
  policy_timeout`.
- `remote_policy.py` + `evaluator.py` — legacy external `robotworld.policy.v1` HTTP contract
  (front/wrist PNG, 5-D relative-joint actions, hash-pinned capabilities) against simcore rig.
- `lerobot_dataset.py` + worker — export successful oracle episodes → LeRobot dataset (8-D state,
  7-D local Cartesian action, cameras renamed exterior_1_left/exterior_2_left, sha-checked frames,
  resample 1–50 Hz, mandatory readback validation; pre-readback exports downgraded LEGACY_UNVERIFIED).
- `lerobot_training.py` + 2 workers — preflight validates dataset/model/paths and writes
  `robotworld.vla-jepa-training-preflight.v1` manifest; execute bounded to steps ≤10, batch 1,
  freeze_qwen, world-model off ("verified 12 GiB profile"); Windows WinError-1314 symlink fallback
  writes `last_checkpoint.txt` pointer; crash recovery rebuilds result from immutable artifacts.
- `policy_lifecycle.py` — PROMOTE requires ≥3 held-out successes with distinct seeds and zero
  failures (env-tunable); REJECT requires a measured failure; activation swap has compensating
  rollback; states CANDIDATE→ACTIVATING→PROMOTED/ACTIVATION_FAILED/ROLLED_BACK.

### Agent & curriculum
- `agent_tools.py` — **54 versioned tools** (`robotworld.agent-tool-definition.v1`), QUERY/MUTATION
  effects, strict JSON schemas (`additionalProperties:false`), approval gating, persisted calls with
  arg hashes. Domains: models.*, robots.register_default_franka, assets.rigid.compile,
  evaluations.run_{oracle,vla}_*, failures.list, coverage.get, curriculum.plan_next/runs.start/cancel,
  scrapers.* (incl. self-heal), vla.bridge_status/attach_franka_zero_shot_bridge,
  training.datasets.create_from_evaluation, training.vla_jepa.{validate,execute}_fine_tune,
  training.policy_candidates.{decide,rollback}.
- `curriculum_catalog.py` — scenario specs (semantic PlacementRequest — raw xyz rejected), fingerprint
  dedupe, plan-next decision ladder (budget stop → target-reached stop (Wilson interval) →
  REPAIR_POLICY_RUNTIME block on worker_crash/invalid_action/policy_instability → new-scenario budget
  → REQUEST_EXACT_OBJECT_EVIDENCE when no ORACLE_VALIDATED asset → REUSE_EXISTING_VALID_ASSET with
  variation dims), coverage taxonomy `pick-place-coverage-v1`: configured dims
  `size, aspectRatio, mass, friction`; dynamic `shapeFamily, pose, orientation, clutter,
  targetLocation, cameraSet`. Restart reconciliation marks interrupted work retryable, never done.
- `autonomous_curriculum.py` — durable runs `autorun_*`, phases PLAN_NEXT→ORACLE→VLA, budgets
  (maxWorlds/maxNewScenarios/maxEvaluationEpisodes/maxGpuMinutes/maxScrapeRequests), stop reasons
  incl. `consecutive_failure_stop`, `target_success_rate_reached`, `*_budget_exhausted`,
  `kill_switch_requested`, `oracle_gate_complete`, `vla_bridge_unavailable`; resume-after-restart.
- `agent.py` + `llm.py` — grounded chat context builder; OpenAI-compatible client (default model id
  `gpt-5.6-luna`) with circuit breaker on permanent provider failure; typed-intent workspace planner
  bypasses LLM for high-confidence intents; deterministic offline fallback clearly labeled
  (`planner:typed-workspace`, `:deterministic-workspace-intent`). LLM fallback applies ONLY to
  planning — never to learned-policy evaluation.
- `evaluation_catalog.py` — immutable world templates (runtime_sha drift ⇒ conflict), runners for 4
  oracle kinds + compiled/authored VLA, status machine QUEUED→STARTING→RUNNING→{SUCCEEDED,FAILED,
  CRASHED,CANCELLED}, frame serving, oracle success flips asset to ORACLE_VALIDATED (failure appends
  blocker), analysis delegated to curriculum_catalog (`failure.classify`, `coverage.observe`).
- `command_store.py`, `events.py` — durable command envelopes + in-memory event bus (UI toasts).

### Evidence & scrapers
- `brightdata.py` — real REST client: SERP (search/images), Web Unlocker page fetch, Scraper Studio
  DCA trigger/dataset/heal/approve with retries and gateway-envelope unwrapping. Probe endpoint
  performs ONE real billable SERP call and returns sanitized domains only.
- `evidence_catalog.py` — unit normalization (mm/cm/in/lb→SI), identity scoring, mixed-SKU rejection,
  CAPTCHA/error-page detection, HTTPS+SSRF URL policy, category priors contribute properties but
  never identity; bundle sha256; quarantine semantics.
- `evidence_collection.py` — durable collection runs; snapshot-resume without re-trigger; billing-
  uncertainty guard on ambiguous trigger state.
- `asset_evidence.py` — paid OpenAI Responses-API physical-property extraction pinned to
  api.openai.com, store=False.
- `scraper_repair.py` + `scraper_repair_demo.py` — canonical governed flow
  COLLECTING→QUALITY_FAILED→REPAIR_REQUESTED→DRAFT_READY→GOLDEN_TESTING→CANARY_TESTING→
  AWAITING_POLICY_DECISION→PROMOTED|REJECTED|ROLLED_BACK|EXHAUSTED; golden(≥2)+canary(≥1) case suites;
  unapproved schema change ⇒ rejected draft; brightdata_live mode forbids auto-promotion; controlled
  fixture demo pages served at `/api/scraper-repair/demo/page/{layout}`. Legacy repair endpoints
  disabled with 410.

### Rendering & observability
- `vulkan_renderer.py` — forces `WGPU_BACKEND_TYPE=Vulkan`; refuses cpu/unknown adapters (no software
  fallback); offscreen kitchen/factory procedural scenes are viewport-context only; evidence paths
  (`render_glb_png`, `render_world_glb_png`) rasterize real GLBs and refuse procedural fallback;
  probe exposes vendor/device/driver.
- `signoz.py` + `telemetry.py` — OTel SDK; every span/log/metric mirrored to SQLite; keyless OTLP
  export when enabled; real protobuf POST probe; v5 `query_range` adapter exists but needs the local
  service-account key (blocked-by-credential).
- `port.py` — client exists but integration hard-disabled (deferred).

### Isaac Sim adapter
- `isaac_sim.py` — never imports Kit in-process; inspects isolated Sim 6.0.1 env, writes launch
  manifest `isaac-launch.json` (schemaVersion 2), hardened subprocess (`--headless --enable_cameras`)
  running `workers/isaac_lab_pick_place.py` (Isaac-Lift-Cube-Franka-IK-Abs-v0, bilateral force
  >0.25 N grasp evidence, absolute-IK oracle, result.json contract). Fail-closed EULA gating
  (`OMNI_KIT_ACCEPT_EULA` must be set by the human operator). **Known skew:** `isaac_bridge.py`
  hard-requires simulatorVersion `"5.1"` while detected/configured Sim is 6.0.1.

---

## 6. Known quirks / tech debt (found in code audit 2026-08-21)

- `GET /api/health` hardcodes `engine: MuJoCo, timestepHz 500` contradicting `DEFAULT_SETTINGS`
  (`engine isaac_sim`, `timestepHz 100` — settings describe intent, MuJoCo is reality).
- CORS allowlist lacks `PATCH` although `PATCH /api/worlds/placements/{id}` exists (dev cross-origin
  PATCH would preflight-fail; Electron origin unaffected).
- `Overview.tsx` LOCAL_PIPELINE/PIPELINE_FLOW blocks are static narrative arrays; overview deltas are
  placeholder strings ("0"/"0.0pp") in main.py.
- `SkillDetail.tsx:352` RolloutStrip renders 3 hardcoded synthetic rollout thumbnails.
- `Titlebar.tsx` hardcodes "RobotWorld 1.0.0" instead of using `/health.version`.
- `/api/observability/services` rows are partly synthesized (fixed restarts/latency strings).
- TRELLIS Q4 proof endpoint mixes real file hashes/sizes with recorded-but-static narrative metadata
  (device/seed/duration/vertex counts of one specific run).
- Menu items "Set as skill target" / "Edit skill definition" push toasts without any API call.
- Git history: 7 commits, bare `v0.0.1…v0.0.7` tags; working tree far ahead of last commit (keep
  patches scoped; never blanket-stage).
- `backend_launch.err/log`, `frontend_launch.err/log`, `backend_runtime.err/out`,
  `trellis_gateway*.err/out` are untracked runtime logs at repo root.

---

## 7. Feature status checklist (evidence-audited 2026-08-21)

Legend: ✅ IMPLEMENTED_AND_TESTED · 🟡 PARTIAL · 🟠 IMPLEMENTED_NOT_LIVE_TESTED · ❌ BROKEN ·
⛔ BLOCKED (credential/hardware/license) · ⬜ MISSING / intentionally absent.

### Built ✅
- [x] FastAPI control plane, SQLite catalog, durable idempotent commands, audit trail, write-only secrets
- [x] React/Electron desktop app (secure host, sidecar ownership); NSIS installer built + packaged smoke passed
- [x] Native Vulkan renderer (pygfx/wgpu): hardware probe, GLB + world-composited frames, no browser-WebGL claims
- [x] MuJoCo 3.11 authoritative physics behind `SimulationBackend`; 500 Hz; contacts; offscreen RGB cams; deterministic seeds
- [x] Pinned Franka Panda (Menagerie) registration/validation: 7 joints + parallel gripper, front+wrist cams, home keyframe, calibration probes
- [x] Deterministic pick-place oracles (baseline / compiled-asset / authored-scene) — repeated live SUCCEEDED runs (`eval_a5e8369b`, 1,636 streamed frames, apple→blender)
- [x] Drawer-open oracle on controlled prismatic fixture with joint-sweep proof
- [x] Authoritative live WebSocket stream (25 Hz composite JPEG) — streamed == recorded evidence
- [x] Rigid asset compiler (GLB QA → convex collision → mass/inertia → OpenUSD+MJCF → real drop/settle ×2) with versioned artifacts + manifest hashing
- [x] OpenUSD authoring: physics layer, PBR visual layer, appearance variant sets, world assemblies (reopen-validated)
- [x] Model registry: path allowlists, SSRF policy, capability probing, lifecycle REGISTERED→AVAILABLE→LOADED
- [x] Isolated VLA-JEPA worker (offline, secret-free env, metadata-Qwen bootstrap fix); real CUDA load of 2.59 B-param checkpoint
- [x] VLA bridge contracts (EE-local delta + DROID velocity), executable-vs-shapeCompatible gates, normalization revision threading
- [x] Real VLA evaluation loop w/ safety box, bounded actions, structured failure classification; honest failures persisted (`grasp_miss` etc.)
- [x] LeRobot dataset export w/ readback validation; LEGACY_UNVERIFIED downgrade
- [x] Bounded fine-tune preflight + 1–10-step executor + Windows symlink fallback + crash recovery + immutable candidate hashing
- [x] Governed policy-candidate lifecycle: register → held-out eval → promote/reject/rollback (≥3 seeds, zero failures) — exercised live (`policydecision_df5f0dcb/REJECTED`)
- [x] Autonomous curriculum engine: budgets, kill switch, oracle-before-VLA, restart-resume — live 3-iteration run completed cleanly
- [x] Failure analysis + coverage tracking (`pick-place-coverage-v1`) + plan-next ladder
- [x] Grounded AI chat: 54 approval-gated tools, typed intents, deterministic offline fallback
- [x] Bright Data clients (SERP/unlocker/DCA heal+approve) — SERP paid probe passed previously; envelope/retry logic tested
- [x] Evidence pipeline: identity resolution, mixed-SKU rejection, quality gates, quarantines, hashed bundles
- [x] Governed scraper self-heal state machine w/ golden/canary/promote/rollback + controlled layout-break fixture demo
- [x] Self-hosted SigNoz Community (Foundry-pinned v0.137.1) deployed in WSL2 Docker; keyless OTLP export live; ClickHouse spans confirmed
- [x] Observability UI data: traces/logs/metrics/alerts from local mirror + runtime diagnostics scoped to current process
- [x] Worlds editor: placements w/ measured bounds checks, camera probe, variants, kitchen/logistics acceptance builders that fail-closed `blocked/policy_not_configured`
- [x] Isaac adapter readiness/OpenUSD prep/EULA-gated fail-closed worker dispatch
- [x] 86-test pytest suite locking in all of the above

### Partial 🟡
- [ ] **VLA task success itself — ❌ BROKEN by measurement**: zero-shot base fails `grasp_miss` (0.865 m target error); 1-step candidate also failed and was correctly REJECTED. Oracle passes; learned policy does not yet.
- [ ] Fine-tuning beyond the 1–10-step verified profile (durable cancel/resume, long-run worker, multi-seed datasets)
- [ ] Articulated *product* assets: only the controlled drawer fixture works; evidence-backed multipart cabinet/drawer not yet compiled (Franka pick-place accepts only `small_rigid_graspable`)
- [ ] Arbitrary prompt→new task contracts: unsupported intents (throw/off-table) correctly 422 fail-closed but no prompt-to-skill compilation exists
- [ ] Multiple appearance variants: only one recorded Q4 PBR appearance exists
- [ ] Authored-kitchen VLA evaluator lacks continuous frame callbacks in Worlds (shows terminal summary only)
- [ ] Multi-seed robustness gate for apple→blender oracle (single production seed proven)
- [ ] Manual teleop/jog control session in viewport (typed pause/resume/reset/jog) — designed, not implemented
- [ ] Visual click-through QA of rebuilt UI (browser automation unavailable; API/typecheck/build verified)

### Not-live-tested 🟠 (implemented, awaiting real-world execution)
- [ ] Bright Data exact-object collector run on a user-chosen product (billable)
- [ ] Live TRELLIS.2 native generation on this GPU (12 GB < official 24 GB floor; Q4 GGUF runtime present but declared inactive; one real Q4 artifact exists as proof)

### Blocked ⛔
- [ ] Server-side SigNoz queries — needs local admin/service-account key creation (human step)
- [ ] Isaac Sim authoritative run — NVIDIA EULA must be accepted by operator (`OMNI_KIT_ACCEPT_EULA`);
      plus fix `isaac_bridge.py` 5.1-vs-6.0.1 version skew first

### Missing ⬜
- [ ] Port.io integration (intentionally deferred; flag-gated off)
- [ ] Gaussian-splat room layer (stretch goal, not started)
- [ ] Generic embodiment hardening (URDF/MJCF/USD/GLB import inspectors exist; second validated robot does not)
- [ ] Training-scale loops / continuous improvement claims (disabled by design)

---

## 8. When you change this repo

1. Run backend pytest from `backend/` and frontend typecheck/lint/build after every change.
2. Update `docs/CODEX_EXECUTION_STATE.md` with exact commands, IDs, and honest statuses.
3. Never claim a gate passed without recorded evidence; keep `PRODUCTION_ACCEPTANCE.md` truthful.
4. Preserve unrelated dirty-tree work; scope your diffs; never `git add .`.
5. Do not add mock fallbacks to production inference/rendering/evidence paths — fail closed instead.
