# Codex execution state

Last updated: 2026-08-21 (America/Los_Angeles)

## Resume point

- Branch/HEAD: `master` at pre-Codex commit `7d65e354425936e9bab880fefd3654c21dc3aff6` (`v0.0.6`). All Codex changes are unstaged. Preserve them and all unrelated user work; do not blanket-stage.
- Current executable slice: API-backed Models, Robots & Embodiments, Simulation & Evaluation, Evidence, Scraper Repair, Failure Analysis, and Agent Control views; internal catalog and audited command/tool surface; pinned real Franka; authoritative MuJoCo worlds and deterministic pick/place; front/wrist observations; fail-closed VLA-JEPA worker/bridge plus an authoritative policy-evaluation path; exact-object evidence normalization; durable Bright Data collection runs; immutable rigid GLB -> OpenUSD/MJCF compilation, stable semantic placement, Franka grasp/lift/place validation; persisted budgeted curriculum control; and governed collector repair with real semantic quality, golden/canary, promotion, rollback, and restart evidence.
- Phase 5 provider status: the exact-identity and semantic-quality pipeline is implemented and tested with controlled and recorded provider rows. A live Scraper Studio call is `BLOCKED_BY_CREDENTIAL` because no server-side `BRIGHTDATA_API_TOKEN` is configured. The live blocked run is persisted as `evcollect_6e159bf0` and made no provider snapshot/fallback.
- Proof data is isolated under `backend/data/codex-live`. The proof API used port 8010 with `ROBOT_ASSET_ROOT=D:\RobotWorldProject\backend\data\assets`; it was shut down cleanly after the latest HTTP readback on 2026-08-21.
- Phase 6 oracle result: real persisted TRELLIS banana `assetver_20e79f28` is `ORACLE_VALIDATED` after authoritative Franka evaluation `eval_c12f4fb7`; exact-evidence and redistribution gates still correctly prevent promotion.
- Phase 4 implementation status: the production VLA endpoint, typed agent tool, synchronized front/wrist observation capture, bounded action decoding, differential-IK application, durable lifecycle, real predicates, failure classification, and oracle-vs-VLA UI are implemented and physics-tested. Live inference remains blocked by missing local LeRobot source/package and Qwen base artifacts, plus the unmodified DROID checkpoint's lack of a proven Franka adapter binding.
- Phase 7 status: structured diagnosis/coverage, configured target/budget stops, valid-asset-first planning, duplicate rejection, semantic `PlacementRequest`, immutable seeded v6 pose/orientation worlds, persisted scenario execution, restart recovery, persisted multi-iteration plan/oracle/VLA/analyze orchestration, aggregate budgets, and a cooperative kill switch are implemented/tested/live. Live policy iterations remain blocked by the configured VLA checkpoint; evidence acquisition is not yet dispatched from the controller, and generic placement beyond the Franka tabletop remains `PARTIAL`.
- Phase 8 status: the canonical governed repair lifecycle, immutable test/candidate artifacts, schema/record diffs, exact identity/completeness gates, manual/controlled-auto policy, last-known-good continuity, rollback, typed tools, and API-backed UI are implemented and tested. Controlled repair `scraperrepair_4af09484` was promoted, survived restart, rolled back, and survived a second restart. Live provider trigger `scraperrepair_d221d663` failed closed as `EXHAUSTED` before a candidate because the server has no Bright Data token. Post-promotion evidence rebuild and asset/robot revalidation are not connected yet, so the overall phase remains `PARTIAL`.
- Next executable task: add a persisted post-promotion follow-up that rebuilds an evidence bundle from the promoted collector output and, only when an already-linked source geometry/asset exists, queues asset revalidation and the deterministic oracle. Then connect that same guarded build/reuse branch to the autonomous controller while charging scrape/GPU budgets. Live calls remain credential/hardware gated and must not substitute fixtures.

## Architecture established from code

- Client: React 19 / TypeScript 6 / Vite 8 / Electron 43. All new pages call real FastAPI endpoints; fixture/recorded evidence is visibly distinguished from live provider state.
- Control plane: FastAPI 0.141.1 / Pydantic 2.13.4 / SQLAlchemy 2.0.52 / SQLite. `CommandExecution` provides durable IDs, input hashes, idempotency replay, terminal errors, and audit correlation.
- Shared agent/human surface: `agent_tools.py` exposes 47 versioned JSON-schema tools, including approval-gated compiled-asset oracle/VLA evaluation, structured diagnosis, curriculum planning, persisted scenario materialization/execution, autonomous-run start/list, the policy-allowed kill switch, collector-version inspection, and governed scraper repair request/test/decision/rollback. Mutations are denied in observe/plan modes and require exact-arguments, expiring, one-use approval unless explicitly policy-allowed. No shell tool is exposed.
- Artifact storage: immutable/versioned local filesystem below `ROBOTWORLD_DATA_DIR`; database rows contain references and hashes, not checkpoints, images, GLBs, or videos.
- Physics: MuJoCo 3.11.0 is authoritative. `SimulationBackend` defines the engine boundary. The Franka baseline runs fixed 500 Hz physics / 50 Hz control.
- Robot source: MuJoCo Menagerie Panda at exact git revision `feadf76d42f8a2162426f7d226a3b539556b3bf5`; Apache-2.0 attribution is copied into each immutable registration.
- VLA runtime: isolated hidden JSONL worker process is prepared to invoke the pinned LeRobot `VLAJEPAPolicy` and official processor factory. It defaults offline, strips unrelated secret environment variables, validates CUDA/packages/source/checkpoint/base-model dependencies before loading multi-GB weights, disables the training-only world model for inference, and never substitutes a mock.
- Evidence: `ObjectRequestRecord`, `EvidenceRecordRow`, `EvidenceBundleRecord`, and `EvidenceCollectionRunRecord` persist exact identity, provenance, property estimates, semantic failures, provider snapshot/heartbeat/cancellation, and bundle linkage.
- Repair governance: `ScraperCollectorVersionRecord` and `ScraperRepairRunRecord` persist active/last-known-good/candidate versions, attempts, provider mode, precise prompt, immutable baseline/candidate references and hashes, schema/record diffs, golden/canary reports, policy, errors, and every lifecycle transition.
- Failure/curriculum state: `FailureEventRecord`, `CoverageObservationRecord`, `ScenarioSpecRecord`, `ScenarioExecutionRecord`, `CurriculumPlanRecord`, and `AutonomousCurriculumRunRecord` persist immutable diagnoses, versioned coverage bins, duplicate-resistant scenario fingerprints, explicit budgets/thresholds, executable oracle state, oracle-before-VLA gates, phase heartbeats, consumption, blockers, cancellation, and terminal reasons.
- Rigid assets: `CompiledAssetVersionRecord` and `AssetManifest` persist immutable source/visual/collision/OpenUSD/MJCF/validation references and hashes. `rigid_asset_compiler.py` enforces allowlisted paths, GLB magic/size/hash, uniform-only scaling, mesh QA, a separate convex collider, explicit mass/COM/inertia, OpenUSD units/physics schemas, and deterministic MuJoCo drop/settle.
- Compiled-asset worlds: `franka_pick_place.py` composes versioned immutable Franka/MJCF worlds, derives stable poses from real collision geometry, measures the Panda finger closing axis, checks clearance/reachability/penetration, settles under physics, and tracks the compiler-authored grasp/COM frame through lift, transport, placement, release, and containment predicates.
- Bright Data: current official APIs verified on 2026-08-20 against the [Scraper Studio quickstart](https://docs.brightdata.com/datasets/scraper-studio/quickstart), [self-healing workflow](https://docs.brightdata.com/datasets/scraper-studio/self-healing-tool), and [AI-flow API overview](https://docs.brightdata.com/api-reference/scraper-studio-api/ai-flow/overview): bearer token, `POST /request`, `POST /dca/trigger`, `GET /dca/dataset`, and self-heal/refactor/resume endpoints. `BRIGHTDATA_API_TOKEN` is primary; the older API-key env name remains a compatibility alias.
- Observability: critical state remains in SQLite. OpenTelemetry uses keyless OTLP HTTP for self-hosted SigNoz Community; no cloud ingestion key is required.
- Deferred scope: Port and Isaac routes are disabled legacy placeholders behind `ROBOTWORLD_ENABLE_DEFERRED_PORT` / `ROBOTWORLD_ENABLE_DEFERRED_ISAAC`; they are absent from production navigation and health gates.

## Genuine implementation evidence

### Models, workers, and VLA bridge

- `backend/app/contracts.py`, `models.py`, `command_store.py`, `control_catalog.py`, and `model_registry.py` implement strict registrations, path allowlists, Windows path validation, endpoint SSRF policy, bounded manifests, content hashes, lifecycle guards, and audit events.
- Live registration `mdl_e3701396` references `D:\VLA-JEPA-Pretrain`; it remains `AVAILABLE` with `healthStatus=worker_unavailable`, not falsely `LOADED`.
- Manifest SHA-256: `c7accb37b5ebe24c7bb772d6d6059acb86c219c8d301bfecf9c91b662de12f39`.
- Full content SHA-256: `7dfc57c97e6b896fddd27708cc46da746d4f5c1000962b41a96927e87604dca0` over 3 safetensor files / 6,163,215,182 bytes.
- Exact Hugging Face repository revision recovered from the local cache metadata: `e946c3e5b538d760f4b4ff239d1b1c12090c041d`. Validation now replaces a legacy literal `unrecorded` revision and preserves an existing full-content hash when a later validation intentionally skips re-hashing; both behaviors are regression-tested and were verified over live HTTP.
- Revalidation found config `stateDimension=8` but no `observation.state` input feature. Official LeRobot inference treats state as optional, so the bridge correctly reports `shapeCompatible=true` and `stateRequired=false`; it does not fabricate a state blocker.
- Processor SHA-256: `aa51dd93443f01777096d151e70c1a41b0f3564392a519120b412954a7b1d940`.
- Bounded safetensors metadata inspection records the real action mask/min/max/q01/q99 statistics without loading model weights; the processor hash is the normalization revision.
- Live worker probe used `D:\TRELLIS.2-runtime\.venv\Scripts\python.exe` and detected CUDA PyTorch 2.7.0+cu128, `NVIDIA GeForce RTX 4080 Laptop GPU`, and 12,878,086,144 bytes VRAM. `transformers`, `safetensors`, and Pillow are present; `lerobot` and `LEROBOT_REPO_PATH` are absent. Offline resolution also proved `Qwen/Qwen3-VL-2B-Instruct` absent from local paths/cache. V-JEPA2 is absent but explicitly not required for inference.
- Live load returned HTTP 409 with those exact blockers and left the model `AVAILABLE/worker_unavailable`; no network download, random policy, or bundled demo was used.
- `vla_bridge.py` defines `franka-cartesian-delta-v1`: `[dx,dy,dz,droll,dpitch,dyaw,gripper]`, translation bound +/-0.05 m, rotation bound +/-0.2 rad, finite normalized input in `[-1,1]`, explicit non-binarized gripper mapping, and encode/decode round-trip tests. Execution additionally requires one-to-one checkpoint-camera mapping, exact robot-definition SHA-256, normalization revision, `end_effector_local_delta`, and a policy rate that divides 500 Hz.

### Franka, world, and authoritative evaluation

- Registered compiler-v2 robot: `franka-panda-mujoco-f9a4918f6663`.
- Contract: seven arm joints, two gripper joints, eight actuators, deterministic home keyframe, named `franka_ee`, front RGB camera, and wrist RGB camera attached to `hand`.
- Wrist mount is explicit: translation `[0.04,0,0.055]`, quaternion WXYZ `[0,0.70710678,0.70710678,0]`, `calibrated=false`.
- Measured validation: 0 severe initial penetrations; max home drift 0.006553 rad; closed/open widths 0.000202/0.079799 m; front robot/workspace pixels 5,054/9,460; wrist gripper/workspace pixels 451/65,085.
- World `franka-tabletop-pick-place-v1` has a semantic support surface, target volume, free 0.04 kg object with explicit inertia/friction, deterministic seed/reset, real contacts, and real predicates.
- Live evaluation `eval_9a63023a` (seed 4242) persisted across restart and idempotent replay: `SUCCEEDED`, 0.005879 m target error, settled speed `6.7569e-11` m/s, 2,468 sampled contact observations, distinct front/wrist phase frames.
- Agent-approved live evaluation `eval_c3bf42a9` (seed 5150) also `SUCCEEDED`: approval `approval_2f8366fa`, tool call `toolcall_34c9a447`, command `cmd_bc876062`; the same approval was rejected on second use.
- Physics tests release an unheld object from 0.62 m and verify fall, support contact, and settling. The frontend never invents object motion.

### VLA-JEPA authoritative evaluation path

- Primary-source review on 2026-08-21 used the official LeRobot VLA-JEPA docs/config/model/processor sources, official `ginwind/VLA-JEPA` repository/config and DROID modality mapping, and the official `lerobot/VLA-JEPA-Pretrain` model metadata. Current remote heads observed (not installed automatically): LeRobot `d451fe4f1f1b00a812f95aa9534389b5e42ab155`, ginwind VLA-JEPA `0dd5281951046b17e1e3653f5661a406306a4a03`.
- The official implementation confirms that `observation.state` is optional and the V-JEPA world-model branch is training-only for this inference path. The worker therefore disables `enable_world_model` during inference unless explicitly requested and does not invent a V-JEPA2 runtime dependency.
- `backend/app/services/franka_vla_evaluation.py` loads the same immutable compiled-asset MuJoCo world as the deterministic oracle, captures real front/wrist RGB observations at the checkpoint resolution, and passes server-side artifact paths to the isolated worker.
- Each normalized seven-dimensional action is schema-validated in `[-1,1]`, decoded through `franka-cartesian-delta-v1`, transformed from end-effector-local translation/intrinsic XYZ rotation into bounded differential-IK joint targets, clipped to workspace/joint/gripper safety limits, and stepped at an exact policy/physics divisor. There is no production scripted/random fallback.
- The evaluator records checkpoint, normalization, adapter, model, robot, asset, world, seed, instruction, frame hashes, normalized/physical actions, actuator commands, contacts, state, timing, settle signals, and task predicates. Structured failures include `worker_crash`, `invalid_action`, `policy_instability`, `grasp_miss`, `grasp_slip`, and `policy_timeout`.
- `evaluation_catalog.py` persists `QUEUED -> STARTING -> RUNNING -> SUCCEEDED|FAILED|CRASHED`, idempotent command replay, immutable artifacts, and `robot.vla_evaluate` telemetry. It never changes asset lifecycle or promotion based on a learned-policy result.
- Integration coverage runs real MuJoCo with synchronized 64x64 front/wrist frames and an explicitly injected test-only bounded stationary policy. Two actions are consumed and durably recorded; the run correctly terminates `FAILED/grasp_miss`, while the enclosing command succeeds and idempotently replays. Audit assertions prove all three transitions through `RUNNING -> FAILED`.
- Live preflight against `mdl_e3701396`, `assetver_20e79f28`, and `franka-panda-mujoco-f9a4918f6663` returned HTTP 409 because the policy was not loaded. No evaluation row or fabricated action was emitted.
- `frontend/src/pages/Assets.tsx` selects only registered VLA policies, enables execution only for enabled/healthy/`LOADED` models and `ORACLE_VALIDATED` assets, and sends a real instruction to the new endpoint. `Simulation.tsx` renders persisted oracle and VLA results separately, including recorded frames, policy/model identity, actions, predicates, and actual failure evidence.

### Agent tool registry and autonomous controller

- `backend/app/services/agent_tools.py` persists bounded `AgentToolCallRecord` and `ApprovalDecisionRecord` rows.
- Current registry: 47 tools covering models, local worker probes/stop, robots, world templates, oracle and VLA evaluations, structured diagnosis/coverage/curriculum planning, persisted scenario oracle execution, autonomous-run start/list/cancel, VLA compatibility, audit history, exact-object requests, recorded evidence, live durable Bright Data collection/list/get/cancel, immutable bundles, governed collector repair/list/test/decision/rollback, asset-version list/get, and approval-gated rigid compilation.
- `evaluations.run_vla_compiled_asset` is a schema-validated, idempotent mutation requiring one-use approval and is not enabled for autonomous execution before budget-policy work is complete.
- `backend/app/services/autonomous_curriculum.py` runs the canonical `plan_next -> deterministic oracle -> VLA -> failure analysis -> repeat/stop` path with persisted phase state, phase-specific idempotency keys, separate world/evaluation/GPU/scrape/retry/iteration/consecutive-failure budgets, real activity heartbeats, startup rescheduling, and cooperative cancellation. It does not use progress timers or invent a policy action.
- The controller validates an active `AVAILABLE` robot and only accepts `ORACLE_VALIDATED` allowed assets. It reuses an already oracle-validated scenario without charging a world or episode, probes the exact VLA bridge before dispatch, and terminates `BLOCKED/vla_bridge_unavailable` without creating an evaluation when the checkpoint contract is unavailable.
- `frontend/src/pages/AgentControl.tsx` is a real API-backed control surface for robot/model/asset binding, autonomy mode, budgets, instruction, seed, run state, phase history, durable IDs, blockers, and the kill switch. Scrape budget is visibly fixed at zero until evidence dispatch is wired; no fixture data is shown as live.
- Integration coverage now drives the full controller through real MuJoCo oracle execution, a test-only injected bounded VLA worker action, persisted `grasp_miss` analysis, and the configured consecutive-failure stop. Separate tests prove queued cancellation, restart rescheduling, strict schemas, tool policy, idempotent replay, and lifecycle audit.
- The old `/api/agent/run` parameterized-skill loop is retained only behind disabled `ROBOTWORLD_ENABLE_LEGACY_SKILL_AGENT`; production UI entry points now route to Agent Control or Failure Analysis instead of launching that pre-canonical path.

### Structured failure analysis and coverage-driven planning

- Primary-source design grounding on 2026-08-21: OpenAI's Automatic Domain Randomization work describes an automatically expanding environment distribution, while the Automatic Curriculum Learning survey frames curricula as tasks adapted to measured agent capacity. RobotWorld applies the conservative subset relevant here: explicit observed bins, repeated failure counts, configured budgets, and no LLM-selected coordinates or opaque reward score.
- `backend/app/services/curriculum_catalog.py` classifies only terminal authoritative evaluations. It preserves direct simulator codes, derives `worker_crash` or `policy_instability` only from terminal/no-result or non-finite-state signals, records bounded evidence and a deterministic repair route, and creates no failure row for a successful episode.
- Pick/place coverage taxonomy `pick-place-coverage-v1` explicitly bins size, aspect ratio, mass, and friction and reports count, unknown count, configured-bin fraction, dynamic shape/pose/orientation/camera dimensions, sample count, unique scenario fingerprints, and failures. No unobserved success percentage is filled in.
- The planner records request thresholds, episode/new-scenario budgets, sample count, exact success ratio, Wilson 95% interval when samples exist, repeated failure histogram, reusable asset IDs, stop/block reason, and the next gate. It stops at the configured target/budget, blocks policy-runtime failures before world generation, and requests exact evidence only when no allowed `ORACLE_VALIDATED` asset exists.
- Real historical indexing: command `cmd_844e3023` mapped successful oracle `eval_c12f4fb7` to coverage observation `coverage_2cd41872`, fingerprint `e0a0022d92de144454697b7a08fbab0f10b201a8f6ede5ced0769a1e79449fb3`, with measured bins `large/slender/light/medium-friction`, banana shape, stable pose 0, and no failure event.
- Planning command `cmd_12a52420` created plan `curriculum_2777414d` and scenario `scenario_ce0cf9f3`, reusing `assetver_20e79f28` for its first untried VLA baseline. It targets only the asset's actual `large/slender/light/medium-friction` bins, requires semantic placement/reachability/no penetration/drop-settle, and gates execution on the deterministic oracle.
- A first live draft (`scenario_b43deb1f`) incorrectly combined immutable asset reuse with size/aspect variation. The validator detected the contradiction, transitioned it audibly to `REJECTED`, and the corrected plan reports `rejectedInvalidScenarioCount=1`. A separate command `cmd_2d229615` reused `scenario_ce0cf9f3` by fingerprint instead of creating a duplicate.
- Indexing the real Franka history produced 11 immutable failure events: 4 `success_predicate_failure`, 2 `grasp_miss`, and one each of `policy_timeout`, `unreachable_target`, `object_dropped`, `grasp_slip`, and `pre_grasp_collision`. Historical failed evaluations remain unchanged.
- `PlacementRequest` accepts semantic support/seed/variation constraints but rejects caller-supplied unchecked XYZ poses. World compiler revision 6 samples a conservative reachable subregion, chooses a seeded graspable stable orientation when requested, checks support clearance/reachability/penetration, drops and settles under MuJoCo, and emits a placement-fingerprinted immutable world rather than mutating the baseline runtime.
- Scenario execution persists `STARTING -> RUNNING -> SUCCEEDED|FAILED|CRASHED` plus `PLANNED -> ORACLE_VALIDATING -> ORACLE_VALIDATED|REJECTED`. On restart, incomplete wrappers become auditable `CRASHED` records and their scenario returns to retryable `PLANNED`; no interrupted episode is relabeled successful.
- Live baseline scenario command `cmd_38fdf33d` executed `scenario_ce0cf9f3` as `scenarioexec_35d0200b` / `eval_a8860bb8`; it passed and idempotently replayed after an API restart. A second approval-gated agent call `toolcall_910c50a6` ran baseline scenario `scenario_bc5aa8ee` as `eval_63f15e0d`.
- Live targeted placement plan `curriculum_0e3fa41c` produced `scenario_6c884cf7` (seed 2401, `object_pose`). Approval `approval_fe64bccd` and tool call `toolcall_113766bd` materialized world `franka-compiled-asset-pick-v6:assetver_20e79f28:p373b8451f122`, runtime SHA-256 `517716caf0a02c10d8a94def50299c98ac43fe09708b00a93e9f9cc757d699d9`, and evaluation `eval_81814555`. It passed with sampled XY `[0.5199102074,-0.0926438730]`, zero severe initial penetration, 554 bounded contact samples (`left=147`, `right=55`), release/containment/settle true, and final linear speed `0.00152460` m/s. Disk hash matched the catalog/evaluation after restart.
- Targeted scenario results do not overwrite or demote the asset's canonical oracle validation. Live readback kept `assetver_20e79f28` `ORACLE_VALIDATED` with canonical evaluation `eval_63f15e0d` after the targeted run.
- Classifier revision 2 fixes temporal leakage: a VLA failure can only cite a successful oracle created at or before that policy evaluation, and an already-persisted failure event is returned immutably even after newer oracle runs exist.
- Live autonomous oracle run `autorun_2fe5e643` / start command `cmd_9e9771dd` planned `curriculum_70abe140`, materialized `scenario_ed7236c8` at seed 2501, and completed `SUCCEEDED/oracle_gate_complete` with authoritative evaluation `eval_39651d2f`. The evaluation recorded runtime SHA-256 `15213612fa81c8dcc22d9ee944c2e672302353ab7f21ed01209ec59c981ed491`, zero severe initial penetration, bilateral finger contacts (`left=192`, `right=70`), release/containment/settle true, and target error `0.00537694` m.
- Live VLA continuation `autorun_b08467ff` / `cmd_512b515c` reused `scenario_ed7236c8` and oracle `eval_39651d2f`; it charged zero worlds, episodes, and GPU minutes, then terminated `BLOCKED/vla_bridge_unavailable` with the exact load/adapter/camera/robot-binding blockers. It created no fake evaluation or action.
- Live kill-switch run `autorun_5a7f576f` / `cmd_99a434bc` stopped `CANCELLED/kill_switch_requested` after planning and before its oracle, with zero evaluation episodes. All three rows and their `QUEUED -> STARTING -> RUNNING -> terminal` transitions were read back after API restart; replaying the first start idempotency key returned `reused=true` and the original run ID.
- `frontend/src/pages/FailureAnalysis.tsx` analyzes terminal runs, plans with configured budgets, displays measured bins/Wilson intervals/deferred variations, and executes supported baseline/pose/orientation scenarios through the authoritative oracle. Multi-iteration execution is explicitly owned by the separate persisted Agent Control surface.
- Inputs/outputs are versioned JSON Schema. Scraped content is data only and cannot widen tool permissions.

### Exact evidence and Bright Data

- `evidence_catalog.py` implements exact manufacturer/model/SKU claims, authoritative-domain priority, mixed-SKU rejection, category-prior labeling, CAPTCHA/login/error-page detection, URL/SSRF policy, unit conversion, image metadata gates, field completeness, identity confidence thresholds, property provenance, content hashes, and immutable raw/bundle artifacts.
- Live controlled pass: request `objreq_691b5d51`, bundle `evb_7dc4bcd0`, `QUALITY_PASSED`, identity confidence 1.0, completeness 1.0, 2 records, properties `depth/height/mass/material/width`, artifact SHA-256 `93b30060e1327b1ef311e45fd39c63fc39a6bffa7edfcc59e9c77b5d0ecee95d` at `backend/data/codex-live/evidence/objreq_691b5d51/evb_7dc4bcd0/bundle.json`.
- Live mixed-SKU rejection: request `objreq_28e84281`, bundle `evb_3731ed44`, `QUALITY_FAILED`; explicit conflict `AC-BLD500-BLU/BLD-500` vs `AC-BLD700-RED/BLD-700`.
- `brightdata.py` now correctly treats HTTP 200 `{"status":"building"}` as in progress; only a JSON array is a ready dataset.
- `evidence_collection.py` owns persisted `QUEUED -> STARTING -> RUNNING -> SUCCEEDED|FAILED|CANCELLED` runs, provider snapshot IDs, real heartbeats, timeouts, cancellation, normalization attempts, restart resume, and fail-closed uncertain-trigger recovery.
- Restart tests prove a known `j_*` snapshot resumes without retriggering. A `STARTING` run without a persisted snapshot fails as uncertain and does not issue a duplicate billable request.
- Live credential-block proof: request `objreq_8b6994c1`, run `evcollect_6e159bf0`, command `cmd_d92fdadd`, provider attempt 1, state `FAILED`, snapshot `null`, exact missing `BRIGHTDATA_API_TOKEN` error. No provider call or mock bundle occurred.

### Governed scraper repair

- `backend/app/services/scraper_repair.py` implements `COLLECTING -> QUALITY_PASSED|QUALITY_FAILED -> REPAIR_REQUESTED -> DRAFT_READY -> GOLDEN_TESTING -> CANARY_TESTING -> AWAITING_POLICY_DECISION -> PROMOTED|REJECTED`, plus attempt `EXHAUSTED` and `PROMOTED -> ROLLED_BACK`. Transitions, commands, inputs, outputs, errors, artifacts, and version activation are durable and idempotent.
- `backend/app/services/scraper_repair_demo.py` serves controlled product-shaped v1/v2 pages. V1 exposes legacy data attributes; v2 actually removes them while retaining Product JSON-LD. The legacy extractor produced bundle `evb_becc00ab` as `QUALITY_FAILED` with identity confidence 0, completeness 0.167, and explicit missing manufacturer/identifier/dimensions/mass/material errors. No fake status flag creates the failure.
- Controlled live proof: repair `scraperrepair_4af09484`, collector `c_robotworld_controlled_9b21d439`, last-known-good `scraperver_bc16fd47`, candidate `scraperver_d2623b14`. Two golden cases and one canary all passed canonical exact-identity/completeness checks with a compatible schema. Manual promotion made only the candidate active; restart readback preserved it. Rollback restored only `scraperver_bc16fd47`; second restart readback preserved `ROLLED_BACK`. Candidate artifact SHA-256 is `9ab4edcc04c086332cba52979d1ce440dceeca6e71f14e27cfc7897568c4a368`.
- Regression tests reject unapproved output-schema fields, duplicate test case names, live automatic promotion, and legacy source endpoints. `ROBOTWORLD_ENABLE_LEGACY_SOURCE_REPAIR` defaults off; both bypass routes return HTTP 410 and the Sources page links to the governed UI.
- Live provider fail-closed proof: `scraperrepair_d221d663` entered `REPAIR_REQUESTED`, consumed exactly its configured one attempt, received the exact missing-token error before network/provider work, transitioned to `EXHAUSTED`, and created no candidate version. The adapter does not execute provider-generated code; provider output must be captured and submitted as an explicit candidate for local validation.
- `frontend/src/pages/ScraperRepair.tsx` reads real runs, collector versions, and audit events; exposes the controlled break; shows prompts/diffs/golden/canary evidence; requires explicit promote/reject; supports rollback; and warns before the potentially billable live provider trigger.

### Canonical rigid asset compiler

- `backend/app/services/rigid_asset_compiler.py` implements the production compiler path; `backend/app/services/usda.py` now accepts explicit uniform scale/coordinate translation for real generated topology.
- The server accepts only a `.glb` below the artifact store, `ROBOT_ASSET_ROOT`, or explicit `ROBOTWORLD_ASSET_IMPORT_ROOTS`; it validates resolved path, `glTF` magic bytes, configured byte limit, and optional expected SHA-256 before parsing.
- Synthetic E2E tests prove successful compilation, immutable/idempotent replay, real MuJoCo contact/settle, path/hash defenses, approval gating, and fail-closed aspect-ratio rejection without per-axis stretching.
- Real source: `backend/data/assets/ast_9aae33a6/model.glb`, a prior actual TRELLIS.2 blender output; source SHA-256 `889bc66362f3fa274d82676596e4ced59c4238a954cc97d421692390c84d5c03`, 19,926,824 bytes, 357,860 vertices, 490,429 source triangles.
- Live command `cmd_1efe9a2e` created `assetver_efd1d8a1` / manifest SHA-256 `bc545b6afe99af9f278203de669dfcfe4d0fb4c528d0c4ab57c26892f4013a5a` in 15.075 s.
- Uniform scale `0.4163154110` produced W/H/D `[0.2, 0.4169018233, 0.2125492095]` m with maximum aspect residual `0.062746`; no anisotropic stretch was applied.
- Derived collision is a separate 302-vertex/600-triangle watertight convex hull (38,346-byte OBJ), not the 490k-triangle visual. The runtime contains two MuJoCo mesh/geoms with visual `contype/conaffinity=0/0` and collision `1/1`.
- Independent reopen verified OpenUSD default prim `/Asset`, Z-up, metres/kilograms = 1, resolved generated visual, `PhysicsRigidBodyAPI`, `PhysicsMassAPI`, and `PhysicsCollisionAPI`; MuJoCo reloaded 4.0 kg explicit mass.
- Real physics result: initial contacts 0, floor contact observed, maximum penetration `0.00294345` m, settle-position span `2.05475e-7` m, maximum settle speed `0.00583025`, finite state, deterministic repeat max qpos error `0.0`, and a recorded 320x320 preview.
- Promotion is correctly false with blockers: deterministic oracle pending, category-prior identity, dimension confidence 0.30, mass confidence 0.25, and unknown redistribution. This is not represented as an exact product or ACTIVE asset.
- `frontend/src/pages/Assets.tsx` now shows canonical physical versions from `/api/asset-versions`, measured geometry/physics, hashes, blockers, and recorded preview separately from legacy asset records; it also submits a real allowlisted compile request.

### Compiled-asset placement and Franka oracle

- `backend/app/services/franka_pick_place.py` implements the backend-specific composition/placement/oracle path. World revision 5 uses MuJoCo's elliptic contact cone, Newton solver, tighter tolerance, high impedance ratio, and NoSlip refinement; the settings are explicit in the immutable runtime rather than being UI animation parameters.
- Stable placement samples real mesh poses, aligns the graspable cross-section with the measured Panda jaw axis, verifies support clearance and initial penetration, and settles for 6 simulated seconds. Accepted pose, seed, stable-pose probability, dimensions, clearance, penetration count, and settle measurements are durable evaluation evidence.
- Real source: prior actual TRELLIS banana version `assetver_20e79f28`. Command `cmd_84443b5a` produced evaluation `eval_c12f4fb7` against robot `franka-panda-mujoco-f9a4918f6663` and immutable world `franka-compiled-asset-pick-v5:assetver_20e79f28` (SHA-256 `15213612fa81c8dcc22d9ee944c2e672302353ab7f21ed01209ec59c981ed491`).
- The real run is `SUCCEEDED` under `deterministic_differential_ik_compiled_asset_oracle_v13`: both fingers contacted the authored collider (`left=192`, `right=70` bounded samples), target-center error `0.00537694` m, containment residual `-0.0341844` m, final linear speed `0.00377510` m/s, settle position span `0.0000827008` m, and authoritative quaternion rotation span `0.00391493` rad over 6 seconds.
- The run preserves a solver/collider limitation instead of hiding it: MuJoCo body angular cvel p95/final were `0.188788/0.172428` rad/s, so the angular-velocity gate failed; the independently measured transform-rotation gate passed below its configured `0.01` rad limit. Both raw signals and the selected gate are persisted.
- The asset transitioned durably to `ORACLE_VALIDATED`. Promotion remains false for `source_identity_is_category_prior`, dimension confidence below 0.80, mass confidence below 0.70, and unknown redistribution rights.
- Earlier failed evaluations remain in the catalog (unreachable width, wrong assumed grasp axis, grasp slip, and predicate/settle revisions). They were not overwritten or relabeled as successes.
- `frontend/src/pages/Assets.tsx` lists active `AVAILABLE` robots, invokes the real compiled-asset oracle endpoint, shows the evaluation ID/seed/result, and refreshes lifecycle/promotion gates from the backend.
- Immutable-world handling is regression-tested: an inadvertently touched v1 artifact was reconstructed from compiler revision 1 and restored byte-for-byte to SHA-256 `5190de5c7d2829c8eed6d9930a88b6c6423aea4e8e496981d8578cbdef3135b6`; new behavior is emitted as v5 instead of mutating old IDs.

## Exact commands and latest results

```powershell
cd D:\RobotWorldProject\backend
.\.venv\Scripts\python.exe -m py_compile app\config.py app\contracts.py app\models.py app\services\brightdata.py app\services\evidence_catalog.py app\services\evidence_collection.py app\services\agent_tools.py app\main.py
# passed

.\.venv\Scripts\python.exe -m pytest -q tests\test_evidence_collection.py tests\test_evidence_catalog.py tests\test_agent_tools.py tests\test_model_and_data_contracts.py
# 18 passed in 8.06s

ruff check app tests workers
# All checks passed (also fixed the previously undefined SigNoz service import)

.\.venv\Scripts\python.exe -m pytest -q
# 58 passed in 40.20s

.\.venv\Scripts\python.exe -m pytest -q tests\test_scraper_repair.py -x
# 4 passed in 5.24s: semantic break, full transition audit, promotion/replay/rollback,
# schema-change rejection, disabled legacy bypass, and live-provider attempt exhaustion

.\.venv\Scripts\python.exe -m pytest -q tests\test_curriculum_catalog.py tests\test_agent_tools.py
# 12 passed in 7.98s

.\.venv\Scripts\python.exe -m pytest -q tests\test_rigid_asset_compiler.py::test_compiler_asset_runs_real_franka_contact_lift_and_place_oracle -x
# passed in 29.24s; includes controller plan -> real oracle -> bounded VLA -> analysis -> stop

.\.venv\Scripts\python.exe -m pytest -q tests\test_rigid_asset_compiler.py tests\test_model_registry.py tests\test_vla_bridge.py tests\test_vla_policy_worker.py tests\test_agent_tools.py
# 20 passed in 18.23s before the durable VLA endpoint test was added; the final full suite includes it

cd D:\RobotWorldProject\frontend
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
# all passed; Vite 8.2.1 built 89 modules including AgentControl and ScraperRepair. Existing Worlds chunk-size warning only.

# Real compiler proof (API on port 8010):
POST /api/asset-versions/rigid
GET  /api/asset-versions/assetver_efd1d8a1
GET  /api/asset-versions/assetver_efd1d8a1/previews/drop-settled.png
POST /api/evaluations/oracle/compiled-asset-pick-place
GET  /api/evaluations/eval_c12f4fb7
GET  /api/asset-versions/assetver_20e79f28
GET  /api/agent/tools
POST /api/models/mdl_e3701396/validate
GET  /api/models/mdl_e3701396/worker-probe
GET  /api/models/mdl_e3701396/bridges/franka/franka-panda-mujoco-f9a4918f6663
POST /api/models/mdl_e3701396/load
POST /api/evaluations/vla/compiled-asset-pick-place
POST /api/evaluations/eval_c12f4fb7/analyze
GET  /api/failure-events
GET  /api/coverage
POST /api/curriculum/plan-next
GET  /api/curriculum/plans
GET  /api/scenario-specs
POST /api/scenario-specs/{scenario_id}/oracle
GET  /api/scenario-executions
POST /api/agent/approvals
POST /api/agent/tools/invoke   # scenarios.oracle_validate
POST /api/autonomous-runs
GET  /api/autonomous-runs/{run_id}
POST /api/autonomous-runs/{run_id}/cancel
GET  /api/audit?entity_type=autonomous_curriculum_run&entity_id={run_id}
POST /api/scraper-repair/demo
GET  /api/scraper-repair/demo/page/v1
GET  /api/scraper-repair/demo/page/v2
GET  /api/scraper-repair-runs/{run_id}
POST /api/scraper-repair-runs/{run_id}/decision
POST /api/scraper-repair-runs/{run_id}/rollback
POST /api/scraper-repair-runs/{run_id}/provider-request
GET  /api/scraper-collector-versions?collectorId={collector_id}
GET  /api/audit?entity_type=scraper_repair_run&entity_id={run_id}

# Live VLA outcomes:
# validation: revision e946c3e5..., content hash preserved with computeContentHash=false
# worker probe: CUDA available; load blocked on local LeRobot source/package + local Qwen
# evaluation: HTTP 409 before run creation because the model was not LOADED
# scenario execution: eval_a8860bb8 replayed after restart; targeted eval_81814555 hash matched disk/catalog
# autonomous oracle: autorun_2fe5e643 -> eval_39651d2f SUCCEEDED; replayed after restart
# autonomous VLA: autorun_b08467ff BLOCKED before evaluation with exact bridge blockers
# kill switch: autorun_5a7f576f CANCELLED before oracle; cancellation audit persisted
# controlled repair: scraperrepair_4af09484 promoted candidate scraperver_d2623b14,
# survived restart, rolled back to scraperver_bc16fd47, and survived a second restart
# live repair adapter: scraperrepair_d221d663 EXHAUSTED at attempt 1/1 on missing token;
# no candidate/provider fallback; legacy bypass returned HTTP 410

# Independent artifact reopen:
.\.venv\Scripts\python.exe - # pxr Usd/UsdPhysics + mujoco.MjModel.from_xml_path
# passed: source hash, USD units/schemas/reference, MJCF meshes/collision masks, explicit 4 kg mass

cd D:\RobotWorldProject\backend
$env:ROBOTWORLD_DATA_DIR='D:\RobotWorldProject\backend\data\codex-live'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010

# Live HTTP paths exercised:
POST /api/evidence/requests
POST /api/evidence/requests/{id}/normalize-recorded
GET  /api/evidence/requests/{id}
POST /api/evidence/requests/{id}/collections
GET  /api/evidence/collections/{run_id}
GET  /api/agent/tools
```

Use `npm.cmd`, not `npm`, because this host's PowerShell execution policy blocks `npm.ps1`.

## Requirement status

| Capability | Status | Evidence |
|---|---|---|
| Phase 0 reproducible repository | IMPLEMENTED_AND_TESTED | Ruff clean, 58 pytest + frontend typecheck/lint/production build (89 modules) |
| Internal catalog / audited idempotent commands | IMPLEMENTED_AND_TESTED | SQLite models, command hash/replay tests, live restart/readback |
| Durable provider/evaluation runs | IMPLEMENTED_AND_TESTED | evaluation persistence, resumable evidence snapshots, uncertain-trigger protection, scenario transition audit, and restart-to-retryable reconciliation |
| Models page and provider/path registry | IMPLEMENTED_AND_TESTED | real APIs/UI; 6.16 GB local checkpoint validation |
| Robots page and Franka default | IMPLEMENTED_AND_TESTED | pinned licensed MJCF, activation, camera, gripper, physics tests |
| Generic robot import | PARTIAL | canonical inspection exists; only Franka is fully physics validated |
| Authoritative MuJoCo pick/place | IMPLEMENTED_AND_TESTED | 3-seed regression plus two live successful seeds |
| Front/wrist observations | IMPLEMENTED_AND_TESTED | calibration metrics, PNG hashes, frame API; browser visual QA unavailable |
| VLA-JEPA worker, bridge, and authoritative evaluation | IMPLEMENTED_NOT_LIVE_TESTED | production worker/endpoint/IK/predicates/durable failure path are physics-tested; live checkpoint load is blocked and no live policy output is claimed |
| Agent typed tool registry | IMPLEMENTED_AND_TESTED | 47 tools; durable approvals/tool calls; scenario/controller/repair execution proved live; one-use approval and policy-allowed kill switch |
| Exact evidence normalization/catalog/UI | IMPLEMENTED_AND_TESTED | controlled pass + mixed-SKU fail + immutable artifacts |
| Live Bright Data collection | BLOCKED_BY_CREDENTIAL | durable adapter/UI/tests complete; live run failed before snapshot on missing token |
| Scraper self-healing governance | PARTIAL | canonical state machine, exact quality failure, versioned golden/canary, schema diff, policy decision, rollback, restart persistence, tools/UI, and disabled legacy bypass are tested/live; live provider is credential-blocked and downstream evidence/asset/robot revalidation is not yet orchestrated |
| TRELLIS.2 live generation | BLOCKED_BY_HARDWARE | local paths exist; no live inference run; do not claim generation |
| Rigid OpenUSD + MuJoCo asset compiler | IMPLEMENTED_AND_TESTED | canonical manifest, immutable artifacts, separate convex collision, explicit mass/inertia, USD/MJCF, synthetic + real TRELLIS GLB drop/settle proof |
| Generated-asset Franka oracle gate | IMPLEMENTED_AND_TESTED | real TRELLIS banana `assetver_20e79f28`, stable placement, bilateral physical grasp, lift/place/release/containment, durable `eval_c12f4fb7` |
| Semantic placement planner | IMPLEMENTED_AND_TESTED | semantic request rejects unchecked XYZ; seeded baseline/position/orientation worlds pass clearance/reachability/penetration/drop-settle and immutable hash checks for compiled Franka pick/place |
| Structured failure/coverage/next-scenario planner | IMPLEMENTED_AND_TESTED | real history indexed; configured bins/budgets/Wilson interval; validated asset reuse; duplicate fingerprint and stop gates tested/live |
| Autonomous next-world execution loop | PARTIAL | persisted plan/oracle/VLA/analyze/repeat state machine, aggregate budgets, restart rescheduling, idempotency, Agent Control UI, and kill switch are tested; oracle/block/cancel paths are live, but live VLA is externally blocked and evidence-build dispatch is not yet connected |
| Articulated cabinet/drawer | MISSING | no accepted physics-driven PartGraph pipeline yet |
| Self-hosted SigNoz | IMPLEMENTED_NOT_LIVE_TESTED | keyless OTLP config; no local instance connected |
| Port | DEFERRED | disabled feature flag; no production UI/gate |
| Isaac Sim | DEFERRED | disabled feature flag; not installed |

## Current blockers

- `BLOCKED_BY_CREDENTIAL`: no server-side `BRIGHTDATA_API_TOKEN`; exact live collection fails explicitly before a provider snapshot, and canonical repair `scraperrepair_d221d663` persisted `EXHAUSTED` at attempt 1/1 with no candidate or fallback.
- `BLOCKED_BY_HARDWARE`: official TRELLIS.2 documents a Linux environment and at least 24 GB VRAM; configured code revision `65d1e13b4a92296036044df0633242bb9e95abf6` exists locally with user modifications, but this host exposes an RTX 4080 Laptop GPU with about 12 GB VRAM. No live generation was attempted or claimed; a prior real output was compiled instead.
- `IMPLEMENTED_NOT_LIVE_TESTED`: VLA-JEPA checkpoint cannot load because no compatible `LEROBOT_REPO_PATH`/`lerobot` package or local `Qwen/Qwen3-VL-2B-Instruct` dependency is configured. CUDA is available in the selected worker environment. The checkpoint's absent state feature is valid/optional, but the unmodified DROID revision still lacks a proven Franka camera mapping, adapter/action representation, robot-definition hash, control rate, and Franka-specific normalization/training provenance.
- Interactive UI QA: no controllable in-app browser session is connected. Typecheck/lint/production build pass.
- Self-hosted SigNoz Community is not running locally; no installation was attempted before the core simulation/evidence slices.

## Next executable task

1. Implement a persisted post-promotion follow-up for `ScraperRepairRunRecord`: normalize the promoted candidate output into a new evidence-bundle revision, link it to the prior failed bundle, and persist its semantic quality outcome. Do not mutate the old evidence bundle.
2. Only when that evidence request has a genuinely matching source geometry/asset lineage, queue existing asset static/physics revalidation and the deterministic Franka oracle; otherwise stop at an explicit `asset_source_missing` blocker instead of pairing unrelated geometry with the repaired evidence.
3. Route the autonomous controller's `BUILD_OR_REUSE_SCENARIO` evidence branch through these durable commands and charge `scrapeRequests`/`gpuMinutes` from actual activities. Keep Bright Data/TRELLIS failures terminal and visible; never replace them with controlled fixtures.
4. Independently, point `LEROBOT_REPO_PATH` at an exact compatible local LeRobot/VLA-JEPA checkout and configure the local Qwen dependency/cache without downloads. A DROID checkpoint must remain unavailable for Franka execution until a genuinely adapted revision supplies `franka-cartesian-delta-v1`, exact front/wrist mapping, robot-definition and normalization hashes, control rate, and training provenance.
