# Codex execution state

Last updated: 2026-08-22 (America/Los_Angeles)

## Resume point

- Branch: `master`. The worktree is intentionally dirty with prior Codex/user work. Preserve all unrelated changes and never use blanket staging.
- Frontend: `http://127.0.0.1:5173/` (HTTP 200 for `#/worlds` and `#/assets`).
- Backend: `http://127.0.0.1:8000/api/health` is healthy.
- The original full Worlds editor, original Assets page, original graphite tokens, and full sidebar navigation have been restored. The later compact replacement pages are no longer the production UI.
- The old World Execute failure was caused by calling `/api/worlds/commands` in `execute` mode; that endpoint intentionally returns 501. Execute now calls the typed persisted `/api/worlds/operate` route.
- The active Kitchen Juice Workspace now resolves the exact instruction `Pick up the apple and place it on top of the blender.` to the persisted apple and blender placements, compiles their task-relevant physical subset with the counter and Panda, and runs it through the same live MuJoCo stream. It does not substitute the cube validation bench.
- Production run `live_c54c9a8f` / `eval_a5e8369b` succeeded with 1,636 streamed frames across 44 controller phases and 66.242 simulated seconds. Bilateral gripper contact, lift, transport, release, target-support contact, containment, settling, and a 4.787 mm target error were measured.
- The validation-bench stream remains a continuous authoritative MuJoCo view rather than phase-by-phase recorded-image navigation. Panda links now use MuJoCo-compiled mesh vertices plus current `geom_xpos/geom_xmat`; the previous raw-OBJ pairing and stale async callback were the causes of the exploded arm in the user screenshots.
- Worlds now uses one `auto` instruction action. A deterministic active-world compiler grounds named movable/fixed placements and the measured relations `inside`, `on_top_of`, or `outside_support`; the compiled relation selects the distinct pick/place or off-table controller and predicate before MuJoCo starts. Unknown/ambiguous relations still fail closed. General synthesis of a brand-new controller skill remains incomplete.
- `#/simulation` now redirects to `#/worlds`; the separate legacy refrigerator runtime is no longer exposed as a second authoritative simulator page.
- Runtime Diagnostics now scopes ERROR/WARN rows to the current backend process. Historical frontend/exporter errors remain in the durable log store but no longer make a restarted healthy process appear degraded.
- AI chat is grounded in current robot/model/asset/evaluation state. High-confidence robot/dataset/fine-tuning intents use a typed workspace planner before free-form LLM reasoning, so the exact prompt cannot be derailed by redundant questions or invalid camera-map keys. After an approved tool result, chat automatically requests the next grounded action.
- Real local VLA-JEPA base checkpoint is `LOADED/healthy` on CUDA in worker PID 46056. Current zero-shot run `eval_020eaf4e` inferred 40 finite two-camera actions but failed `grasp_miss`; candidate `eval_58c7456c` failed the workspace safety gate after 72 actions. Neither is represented as task success.
- Successful recorded oracle evaluations export into locally validated LeRobot datasets. A real bounded one-step optimizer run also completed into a separate candidate checkpoint. Promotion/held-out evaluation and a resumable long-run worker remain incomplete.
- SigNoz Community `v0.137.1` is live at `http://127.0.0.1:8080`; RobotWorld exports OTLP to `http://127.0.0.1:4318`. ClickHouse contains live `robotworld-backend` spans.

## 2026-08-21 production agent-loop completion

- `/api/chat` now runs a bounded OpenAI Responses function-calling loop instead of a one-shot text completion. It exposes the registered typed tools, validates every argument through the existing Pydantic contracts, executes read-only tools durably, and returns mutation requests as approval cards without executing them. The loop is capped at six model turns, sixteen tool calls, and 24,000 output characters per tool result; provider responses use `store=false` and replay encrypted reasoning items where supplied.
- Live production proof after the final backend restart: the prompt asking the agent to inspect the latest evaluation produced provenance `llm:openai-compatible:tool-loop`; response IDs `resp_00e75ec3f9b60362016a8938e2bd3887d0aeb034e97c6ae8e4` and `resp_00e75ec3f9b60362016a8938e6c6a087d0858ea0e3c9fec4bf`; request IDs `req_bc36e2bb7e39403e884fbd081e13a892` and `req_2ddf40b5aebb48cb815e25ce5766b3bb`; and a successful real `evaluations.list` call `call_4pRl1RGbbciIeXAc4vwRJi8T`. It grounded the answer in `eval_868de5ec/SUCCEEDED`, returned no mutation, and `/api/health` then reported OpenAI `healthy`.
- A second live grounded proof earlier in the pass queried `eval_7f72f749/FAILED` with failure `unreachable_target`; its durable query record was `toolcall_f4c2596a`, actor `openai-copilot`, permission `OBSERVE_ONLY`. These different outcomes demonstrate that the model reports persisted evidence rather than a canned success path.
- Real autonomous run `autorun_f044e7cd` planned `curriculum_3747dcea` / `scenario_d3960b95`, passed deterministic oracle `eval_d174a033`, then ran the resident 2,593,879,303-parameter VLA-JEPA checkpoint on CUDA for 15 policy steps. Learned-policy evaluation `eval_32d9b19d` failed honestly with `grasp_miss`; the controller stopped cleanly with `consecutive_failure_stop` after two evaluation episodes and `0.09372964` GPU minutes. The API remained responsive during inference.
- The production SQLite database now uses WAL plus a 30,000 ms busy timeout. This repairs the reproduced `database is locked` crash from `autorun_e2523c3d`. Terminal evaluation indexing now skips already-indexed immutable rows and commits missing observations/failures in one batch. Nested scenario-oracle retries derive a fresh child idempotency key from the parent attempt, preventing a failed child command from being replayed forever.
- Manual Franka sessions keep MuJoCo/GL context creation, jog, gripper, rendering, and close operations on one session-owned worker thread. The previously observed `glfwMakeContextCurrent` access violation no longer reproduced: the real live-stream test passed in 29.38 s and the heavyweight compiler -> oracle -> autonomous integration test passed in 126.83 s.
- Final verification: focused agent/database/curriculum tests `17 passed`; API plus agent-loop tests `23 passed`; non-live API suite `22 passed`; real live-stream test `1 passed`; heavyweight integration `1 passed`; frontend typecheck, lint, and production build passed (95 modules); Python compileall and `git diff --check` passed.
- SigNoz trace ingestion is live, but server-side graph/table query tools remain credential-blocked until the local SigNoz onboarding screen is completed and a read-only service-account key is saved under Settings. No credential was fabricated. Port remains intentionally deferred.

## Latest live evidence

### 2026-08-21 real active-world Panda control and drop task

Status: **IMPLEMENTED_AND_TESTED** for deterministic apple pick/place, apple inside-sink, apple-on-orange stacking, apple drop-off-table, active-world live streaming, persisted Panda base translation, and manual Cartesian/gripper control. Learned VLA task success remains **BROKEN** (`grasp_miss`); banana is reachable but remains **BROKEN** (`object_dropped`) because the current top-grasp frame causes finger/counter contact.

- Added a typed `drop_off_table` task with its own compiler family and oracle policy. It carries the compiled movable asset beyond the measured counter support polygon, releases it under gravity, and requires `outsideSupportPolygon`, `belowCounterTop`, `released`, and `settled`; it does not reuse the in-target predicate.
- Browser-run evidence: `live_fefac91d` streamed 1,048 continuous authoritative frames in the actual Kitchen Juice Workspace and persisted `eval_868de5ec/SUCCEEDED` at seed `1048576`. At 30.80 simulated seconds it was in `transport_off_table_segment_08`; at 42.68 seconds it finished `settle_after_drop`, finite, with the task predicate passed.
- Earlier direct evidence `live_76170b7b` / `eval_b6fa4611/SUCCEEDED` measured final apple position `[-0.4773451086, 0.6940897199, -0.0005435093]`, floor settling, and approximately 0.065 mm maximum penetration.
- Added an active-world manual session with a dedicated single worker thread (MuJoCo/OpenGL objects remain thread-affine), bounded ±3 cm Cartesian jog requests, compiled workspace/joint limits, and real gripper actuator commands. Browser session `manual_124a7923` advanced from frame 1/sim 0.30 s to frame 61/sim 2.70 s after X+ and frame 66/sim 2.90 s after Close gripper; all reported finite physics and zero browser console errors. API session `manual_7a2b1677` closed the gripper to 0.0086467 m.
- The live viewport retains every authored kitchen GLB as textured visual context while MuJoCo streams the physical Panda/source/counter transforms. Source geometry is not duplicated. The corrected default camera frames the full Panda and counter.
- Panda base translation is selectable/movable in the editor and persisted through `PATCH /api/worlds/robot-spawn`; the next active runtime consumes that mount. Orientation remains locked to the calibrated +90° yaw until a new controller/camera calibration is validated.
- Restored the exact calibrated base x coordinate `-0.15`. The previously persisted `-0.150003961892...` (3.96 micrometres different) changed rounded-hull contact from bilateral to unilateral and reproduced `grasp_miss`; apple-to-blender rerun `eval_4421031c/SUCCEEDED` after restoration.
- A real banana-to-blender attempt resolved `assetver_7aa76e7d` (TRELLIS banana, `PHYSICS_VALIDATED`) but persisted `eval_7f72f749/FAILED/unreachable_target`, with 0.017299 m pre-grasp residual. This is not reported as task success.
- Interactive command responses and WebSocket terminal messages now omit the full trajectory; the complete trajectory remains durable in the catalog. This prevents multi-megabyte responses from stalling the API and telemetry exporter.
- The unused `/api/eval/sessions` + `/ws/live` refrigerator preview is now disabled by default (HTTP 410 / WebSocket 4403). `/#/simulation` already redirects to Worlds; `/ws/worlds/live` is the only production live simulator surface.
- The bottom Console switches to current manual/run state and measured predicate values during live operation instead of showing a stale selected-asset “physical evaluation pending” message.
- Backend restarted from `backend/run_server.py`; health is `healthy`, MuJoCo 3.11.0/500 Hz, SigNoz `exporting`, and current-process diagnostics have zero events.

Files changed for this slice:

- `backend/app/contracts.py`
- `backend/app/main.py`
- `backend/app/services/evaluation_catalog.py`
- `backend/app/services/franka_live.py`
- `backend/app/services/franka_pick_place.py`
- `backend/scripts/run_live_franka_stream.py`
- `backend/tests/test_api.py`
- `backend/tests/test_franka_oracle.py`
- `frontend/src/components/three/AuthoritativeSimulationCanvas.tsx`
- `frontend/src/components/three/WorldEditorCanvas.tsx`
- `frontend/src/pages/Worlds.tsx`
- `frontend/src/styles/ui2.css`

Commands/results:

```powershell
# full backend regression
cd D:\RobotWorldProject\backend
.\.venv\Scripts\python.exe -m pytest -q
# 92 passed in 217.71s

# focused new/changed contracts
.\.venv\Scripts\python.exe -m pytest tests/test_api.py::test_legacy_preview_session_is_disabled_in_production tests/test_franka_oracle.py::test_worlds_live_stream_is_continuous_and_persists_same_evaluation -q
# 2 passed in 27.70s

# frontend production checks
cd D:\RobotWorldProject\frontend
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
# all passed; Vite built 95 modules

# browser exercised http://127.0.0.1:5173/#/worlds
# manual_124a7923: X+ and Close gripper, 66 frames, finite, no console errors
# live_fefac91d / eval_868de5ec: drop-off-table, 1,048 frames, SUCCEEDED
```

Exact next executable task: make the banana pose reachable (move it inside the measured Panda workspace or run the deterministic placement planner), rerun its compiled-asset oracle, then collect multi-seed apple/banana oracle demonstrations and fine-tune/evaluate a new VLA candidate. Do not represent the current base VLA as working; its measured result is still `grasp_miss`.

### Active kitchen execution completed (2026-08-21)

- `compile_authored_scene_asset_world` composes the selected `PHYSICS_VALIDATED` TRELLIS apple (`assetver_246364a3`), the authored blender target, primary counter collision, and registered Panda into one immutable MuJoCo runtime. Other visual-only kitchen assets are explicitly omitted from physics until validated.
- The active-world resolver requires one unambiguous movable source, one fixed target, and the implemented `on top of` relation. Unsupported throw/toss/off-table/outside-target instructions return HTTP 422 with `No simulation was started`.
- The deterministic oracle uses bounded numerical IK, actuator tracking, bilateral contact validation, contact-feedback recentering, five lift segments, and 28 transport segments. It does not teleport joints or the object.
- Production stream `live_c54c9a8f` delivered 1,636 continuous frames, 26,251,117 bytes of synchronized front/wrist JPEG evidence, 44 phases, and persisted `eval_a5e8369b/SUCCEEDED`.
- Every streamed frame identifies the actual generated apple version and carries its PBR transform. The 313,605 source GLB vertices transformed through the manifest matched the metric runtime OBJ bounds within `7.81e-10 m`; the browser now loads the 16.5 MB textured GLB at the authoritative MuJoCo body pose while contacts use the separate convex collider.
- The Panda editor preview returns 58 compiled geometries from the registered immutable MJCF at spawn `[-0.1500039619,-0.2895051834,0.9]`. Live Panda links use MuJoCo-compiled mesh buffers, fixing the exploded raw-OBJ/compiled-transform mismatch.
- Self-hosted SigNoz Community `v0.137.1` is healthy at `127.0.0.1:8080`, OTLP probe is connected at `127.0.0.1:4318`, and current-process diagnostics contain zero events.
- Base VLA `mdl_1a88cd40` loaded 2,593,879,303 parameters on `cuda:0` from `D:\VLA-JEPA-Pretrain` with downloads disabled. `eval_020eaf4e` failed `grasp_miss` after 40 finite actions. Candidate `mdl_3394f1ab` produced `eval_58c7456c/invalid_action` after 72 actions and remains unpromoted.
- Loading a new VLA checkpoint now atomically demotes the previously resident registration. Live swap test `cmd_e1c4560e` then `cmd_66149b83` showed exactly one `LOADED` row each time and restored the base checkpoint; the catalog can no longer show two active brains for one worker.
- Active-kitchen VLA now resolves through the same authored runtime and blender-support predicate as the oracle; no cube bench is substituted. `cmd_6d2048b8` / `eval_3d3211dc` ran 100 real two-camera actions from the base checkpoint and failed honestly with `grasp_miss` (no finger contact, maximum lift 4.35 micrometres, target error 0.865 m). Active-world Agent curriculum remains disabled until its scenario planner understands authored-world revisions.
- Browser/computer-control providers were unavailable, so no visual click-through is claimed. The live API/WebSocket, generated artifacts, streamed geometry metadata, frontend build, and persisted evaluation were exercised directly.

Commands/results:

```powershell
# backend regression
.\.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_franka_oracle.py tests/test_rigid_asset_compiler.py -q
# 32 passed in 111.66s

.\.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_vla_policy_worker.py tests/test_franka_oracle.py -q
# 29 passed in 41.10s

# production active-world stream
.\.venv\Scripts\python.exe scripts\run_live_franka_stream.py --robot-id franka-panda-mujoco-f9a4918f6663 --active-world-id door-validation-lab --instruction "Pick up the apple and place it on top of the blender."
# live_c54c9a8f; 1,636 frames; pbrVisualFrames=1,636; eval_a5e8369b/SUCCEEDED

# frontend
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
# all passed; Vite built 95 modules
```

### 2026-08-21 screenshot-driven Worlds correction

- User screenshots proved three production correctness defects: separated Panda links, an `outside the target` instruction passing the fixed in-target predicate, and the kitchen editor switching to an unrelated calibration world during Run live.
- `GET /api/runtime/franka-compiled-meshes/{mesh}.obj` now serializes `mjModel.mesh_vert/mesh_face`. The `link1` regression test confirms served vertices match MuJoCo's compiled buffer and that the source has a non-zero `mesh_pos` transform.
- `AuthoritativeSimulationCanvas` reads the latest geometry frame when asynchronous OBJ loads finish. Links can no longer freeze at different oracle phases after the stream ends.
- `outside the target`, `outside of the target`, throw/toss, and off-table instructions fail validation with HTTP 422 and `No simulation was started` for the in-target pick/place contract.
- Active-world execution is explicit (`executionScope=active_world`, `worldId=...`) and the supported apple-on-blender contract now runs the actual authored kitchen subset. Unsupported or ambiguous intents still fail closed; no validation bench is substituted.
- `GET /api/worlds/scene/robot-preview` loads the selected immutable MJCF, applies the exposed counter mount, resets the real `home` keyframe, runs `mj_forward`, and returns 58 compiled mesh poses. Live spawn is `[-0.1500039619,-0.2895051834,0.9]`, yaw quaternion `[0.707106781187,0,0,0.707106781187]`.
- The editor consumes those 58 poses and the same compiled mesh endpoint. This is truthfully marked `authoritativeForExecution=false` / `mountValidatedForExecution=false`; it is an authoring FK preview, not a completed rollout.
- The real active-scene collision-subset compiler and persisted live route are now connected and passed the apple-to-blender run. This supersedes the earlier unilateral-grasp/transport probes.
- Self-hosted SigNoz Community is running in Docker: UI `:8080`, OTLP `:4318`, version `v0.137.1`. The live RobotWorld probe returned `connected=true`; current-process diagnostics returned `healthy`, zero events. Docker is required for this current SigNoz deployment, not for MuJoCo/RobotWorld itself.
- UI click-through could not be performed because both Windows computer control and the in-app browser reported unavailable. API readback, live services, compiler tests, TypeScript, and lint were run; visual completion remains `IMPLEMENTED_NOT_LIVE_TESTED` pending user refresh/screenshot or restored browser control.

Commands/results for this correction:

```powershell
# backend
.\.venv\Scripts\python.exe -m pytest tests/test_api.py::test_world_operator_rejects_implicit_or_unsupported_execution_contracts tests/test_franka_oracle.py::test_worlds_live_stream_is_continuous_and_persists_same_evaluation -q
# 2 passed in 14.61s
.\.venv\Scripts\python.exe -m pytest tests/test_rigid_asset_compiler.py -q
# 9 passed in 61.35s
.\.venv\Scripts\python.exe -m compileall -q app
# passed

# frontend
npm.cmd run typecheck
npm.cmd run lint
# passed

Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/integrations/signoz/probe
# connected=true; community-self-hosted; v0.137.1

Invoke-RestMethod http://127.0.0.1:8000/api/diagnostics/runtime
# healthy; 0 current-process events
```

### Current-process diagnostics and interactive 3D correction

- Backend restarted from `backend/run_server.py`; `/api/health` returned `healthy`, MuJoCo 3.11.0, 500 Hz, SigNoz `exporting`.
- `/api/diagnostics/runtime` returned `healthy`, `events=0` after 200.6 seconds and after a full real oracle stream. Pre-restart rows are intentionally excluded by `STARTED_AT_WALL_MS`.
- Self-hosted SigNoz probe performed a real OTLP HTTP/protobuf POST to `http://127.0.0.1:4318/v1/traces`: HTTP 200, Community `v0.137.1`. This replaces the previous TCP-port-only probe.
- OTLP root log export now filters `opentelemetry.*` internal records so an exporter transport failure cannot recursively submit itself to the failed exporter.
- Live run `live_b314f67c` delivered 138 frames from simulator time 0.300 s to 6.012 s across 10 continuous phases and persisted `eval_3c30c0f1/SUCCEEDED`.
- Each live frame now includes visual geom identity, exact `data.geom_xpos` pose, quaternion converted from `data.geom_xmat`, shape/size, and RGBA. Collision-only group 3 is not rendered. The UI loads the pinned MuJoCo Menagerie OBJ visual meshes and applies only these authoritative transforms.
- The pinned Panda visual endpoint served `link1.obj` with HTTP 200 (3,362,025 bytes); paths are regex-constrained and remain inside the immutable Menagerie asset directory.
- Unsupported throw request returned HTTP 422 with `No simulation was started`; it did not create a disguised pick/place evaluation.
- After the backend restart, approved base registration `mdl_1a88cd40` was explicitly reloaded: command `cmd_837c2350/SUCCEEDED`, worker PID 10868, CUDA 0, 2,593,879,303 parameters, 10.304 s load, offline/no downloads. This proves the checkpoint is resident; it does not change the prior measured zero-shot `grasp_miss` result.

### Continuous Worlds simulation stream

- Browser/Vite-proxied stream: `live_bbd70f00`, selected physical version `assetver_2882ab27`.
- Received 330 live composite JPEG frames from simulator time 0.500 s through 13.922 s (approximately 24.6 sampled FPS) across reset, pre-grasp, approach, axis correction, close, lift, transport, place, release, retract, and settle.
- The same run persisted `eval_daee839e/SUCCEEDED`; the asset was contained, released, settled, and passed its task predicate.
- WebSocket messages are bounded: frames contain camera/state/contact samples; the terminal message contains the evaluation summary only. The complete trajectory remains in the internal catalog.
- A discovered 1 MiB terminal-message overflow was reproduced through the Vite proxy and repaired instead of raising the client limit.

### Direct World instruction

Request:

```text
Pick up the object and place it in the target.
```

Selection: MuJoCo, deterministic oracle, `franka-panda-mujoco-f9a4918f6663`, built-in known-good cube, seed 6203.

- Command: `cmd_2cb929b5`, `SUCCEEDED`.
- Evaluation: `eval_6a859d30`, `SUCCEEDED`, `success=true`.
- Final target error: `0.0058790349 m`.
- Final predicates: contained, on support surface, settled, and released all true.
- Recorded `settle/front` and `settle/wrist` PNG routes both returned HTTP 200.
- Visual inspection of the recorded frames shows the real simulated Panda, table, object, target, and wrist view.

### Exact AI-chat prompt after repair

- Chat first proposed approval-gated `models.load`; `toolcall_f46259aa` loaded `mdl_1a88cd40` as `LOADED/healthy` in the isolated CUDA worker.
- The typed planner saw prior learned-policy failure `eval_d47dac1d/invalid_action` and did not waste a second VLA episode or claim success.
- It proposed `evaluations.run_oracle_compiled_asset` for `assetver_2882ab27`.
- Approval-gated tool call `toolcall_43f32173`, command `cmd_c690a6d8`, produced `eval_fa78ceae/SUCCEEDED`.
- Measured result: 1.106 s simulator wall time, 1,198 sampled contacts, contained/settled predicates true, target error `0.0040681797 m`, and 12 recorded front/wrist phases.
- The automatic follow-up returned no further mutation and stated that the base VLA still needs repair or a validated candidate before another learned-policy episode.

### Grounded AI-chat conversation

Live turn 1, `gpt-5.6-luna`:

```text
Help me train my current robot.
```

The reply asked for the task, object/scene, and evaluation budget, returned zero actions, and stated that LeRobot fine-tuning is not implemented.

Live turn 2:

```text
Pick up the object and place it in the target. Use the known-good cube,
one world, two evaluation episodes, and ten GPU minutes.
```

- With the worker stopped, chat proposed exactly one valid action: `models.load` for `mdl_1a88cd40`.
- Approval `approval_33e2c07f` and tool call `toolcall_6673f40d` loaded the real checkpoint.
- Model load command `cmd_a23c8133` succeeded in about 10 seconds on the RTX 4080 Laptop GPU.
- The isolated worker reported 2,593,879,303 parameters, two 224x224 image inputs, 7D actions, CUDA 12.8, and no network downloads.
- After refresh, chat proposed exactly one valid `curriculum.runs.start` action with the requested IDs and budgets.

### Autonomous oracle -> VLA -> diagnose run

- Approval: `approval_4078f176`.
- Agent tool call: `toolcall_14b5b2a8`.
- Start command: `cmd_7196c750`.
- Run: `autorun_61bf494c`, terminal `STOPPED/evaluation_budget_exhausted`.
- Planner reused `assetver_2882ab27` and persisted `scenario_2a73b7a1`.
- Oracle evaluation `eval_ff20c4c4`: `SUCCEEDED`, target error `0.0124938136 m`.
- Real VLA evaluation `eval_d47dac1d`: `FAILED/invalid_action` after 132 policy steps.
- Exact failure: the accumulated Cartesian target left the configured Franka safety box at `[0.8598422565, -0.0000342067, 0.3990430161]`.
- Failure event `failure_32f92187` records finite state, 528 contact samples, the passing oracle counterpart, classifier revision `structured-failure-v2`, and recommendation `REPAIR_POLICY_RUNTIME`.
- This proves the local model, two-camera observation path, 7D decoder, IK adapter, safety gate, telemetry, persisted failure analysis, and bounded stop policy execute. It does **not** prove VLA task success or training.

### Validated LeRobot demonstration

- Source oracle command/evaluation: `cmd_a14d3e3b` / `eval_6ad08a38`, `SUCCEEDED`, 242 synchronized front/wrist observations.
- Export command: `cmd_0a0846f1`, `SUCCEEDED`.
- Dataset: `dataset_bf1181b4`, `VALIDATED`, repo ID `robotworld/dataset_bf1181b4`.
- Contract: 1 episode, 92 frames at 10 Hz, 184 embedded image observations, two 224x224 cameras, 8-D state, 7-D local Cartesian action.
- Worker performed LeRobot readback of first/last samples and verified image/state/action shapes. `readbackValidated=true`; `pushedToHub=false`.
- Source manifest SHA-256: `aa6dc84108c44b52cddf3ae199fe2aa6b1f95977c48e4e9a7ed8b98ccfa0ca53`.
- Dataset-info SHA-256: `60fb856e97a1003a23ef1b163481cf4ef07fb5e978316ce7381aaa4ec9a568bb`.
- Older export `dataset_0fc1185d` predates readback validation and is now surfaced as `LEGACY_UNVERIFIED`; it is not accepted as training evidence.

### Corrected DROID action bridge and live World agent

- Official DROID source confirms the pretrained action is normalized base-frame Cartesian velocity, not an end-effector-local physical delta. The bridge now records `droid-franka-cartesian-velocity-v1`, applies the official 0.075 m / 0.15 rad limits, and uses base-frame translation/extrinsic rotation.
- Corrected live evaluation: `cmd_bef30993` / `eval_b7eb9dac`, 100 finite learned-policy steps, no invalid action, recorded front/wrist observations. It failed `grasp_miss`; minimum hand-to-object distance was 0.146763 m.
- Failure analysis: `cmd_dd571237` / `failure_2dabce4d`, with passing oracle counterpart `eval_fa78ceae` and a targeted pose/orientation recommendation.
- Exact Worlds prompt in Agent mode created `cmd_9dcd0683` / `autorun_2ba734af`. It planned `scenario_8d712325`, passed oracle `eval_22b68846`, ran VLA `eval_e6f0ca04`, diagnosed `grasp_miss`, and stopped at its configured one-iteration budget.
- The expanded live Worlds run `cmd_416a4a83` / `autorun_266cd0c7` exercised all three configured iterations and six evaluation episodes. Oracle evaluations `eval_305e4519`, `eval_49eb2f6c`, and `eval_561dcfcb` all succeeded on distinct persisted scenarios. Learned-policy evaluations `eval_d9a12458`, `eval_de6445f9`, and `eval_f9424abf` all executed finite base-frame actions and failed `grasp_miss`.
- The run stopped cleanly with `consecutive_failure_stop` after consuming three worlds, six episodes, and 0.2135 GPU minutes. No invalid-action fault or success fabrication occurred.

### Self-hosted SigNoz Community

- Official Foundry `v0.2.17` was downloaded under ignored `.downloads/`; archive SHA-256 `625c7985b8ac6f3e4a99576c1dceaa4fa46fa4a54b2c53f515dff7f63da8dd4a` matched the published release.
- Live stack: SigNoz `v0.137.1`, ClickHouse/Keeper `25.12.5`, Postgres `16`, collector digest `sha256:6d1a59bc553e041014597eff0970608948c5c7447aaa984c4d109f2bc9f4062c`.
- Foundry's generated OpAMP address incorrectly selected the Postgres service and then supplied no-op pipelines before onboarding. `ops/signoz/compose.override.yaml` pins the collector and runs the checked-in OTLP pipelines directly.
- SigNoz health returns 200; 4317/4318 are listening inside the collector; the RobotWorld probe reports `connected=true`, `community-self-hosted`, `v0.137.1`.
- Direct ClickHouse evidence after restart: `robotworld-backend` had 1,231 spans, latest timestamp `2026-08-21 20:15:13.058118900`. No cloud ingestion key is used.
- Initial local admin/service-account creation remains a human credential action. Ingestion and display work now; server-side SigNoz API queries need that local service-account key.

### Post-dataset VLA runtime verification

- Installing the official local `D:\LeRobot[dataset]` extra added the dataset stack without downloading a checkpoint.
- Load command `cmd_53bb7226` succeeded after that environment change.
- Worker PID `43432` loaded 2,593,879,303 parameters on the RTX 4080 Laptop GPU in 11.12 seconds, offline, with CUDA 12.8.
- Live status reports `LOADED/healthy`, resident worker, compatible Franka bridge, two cameras, state 8, action 7.
- Live AI chat was asked to export the recorded oracle demonstration; it found `dataset_bf1181b4` and correctly returned no duplicate mutation.

### Real bounded VLA-JEPA fine-tuning candidate

- Installed the official local `D:\LeRobot[training]` extra into the isolated VLA environment; no checkpoint was downloaded.
- Preflight command `cmd_ba95ba47` produced `trainrun_26a4f2b4/READY` after reading a real dataset sample and validating action `[7,7]`, two camera tensors `[1,3,224,224]`, state `[1,8]`, CUDA/bfloat16, LeRobot 0.6.2, Accelerate 1.14.0, and offline/no-Hub/no-W&B configuration.
- The first direct optimizer smoke run wrote its checkpoint but failed the official Windows `last` symlink with WinError 1314. It is preserved as `trainrun_e508ec70/FAILED`; it is not presented as success.
- The worker now uses a scoped Windows completion-pointer fallback without modifying `D:\LeRobot`.
- `trainrun_89c6a7f8` completed one real optimizer step and is durably `SUCCEEDED`.
- Candidate checkpoint: `backend/data/training-runs/trainrun_89c6a7f8/candidate/checkpoints/000001/pretrained_model`.
- Candidate weights: 5,498,243,572 bytes, SHA-256 `9bfa07b9519b1d26620780c1eec6bcec4a8f7f35c4de14f1aad78f451f5392c5`.
- Active checkpoint overwritten: false. Hub push: false. Automatic promotion: false.
- Recovery/execute command `cmd_b6d1f76c` verified the immutable candidate through the new API.
- Preflight and execution are separate approval-gated agent tools. Execution is intentionally bounded to 1-10 steps, batch 1, frozen Qwen, and world model disabled on the verified 12 GiB profile. Durable cancellation/resume and held-out candidate promotion are not yet implemented.

### Candidate registration and held-out evaluation

- Registration command `cmd_d72fa9a1` created separate model `mdl_3394f1ab` for `trainrun_89c6a7f8`; the base `mdl_1a88cd40` was not changed.
- Validation command `cmd_ea971d6a` produced `AVAILABLE/healthy`; manifest SHA-256 `daabcb649bf8c6cabbe7293448c10b8b069b8ab3c5cbee08b40e8250b851fcbf`, two cameras, 8-D state, 7-D action, world-model inference disabled.
- The first candidate inference load exposed a real metadata-only Qwen detection bug. The probe now distinguishes metadata from full weights and uses the scoped structure-loader for base and candidate checkpoints. No Qwen/model download occurred.
- Load command `cmd_85851e77` loaded 2,593,879,303 parameters on CUDA in 10.85 s from the candidate checkpoint.
- The exact Franka bridge was attached with command `cmd_cb3bfda6`.
- Held-out command/evaluation `cmd_9428e49b` / `eval_26568722` ran the same robot, asset, instruction, and seed as the base comparison.
- Result: `FAILED/grasp_miss`, 150 finite trajectory steps, 600 contact samples, no gripper/object contact, target error `0.3 m`. Unlike the base run, it did not leave the workspace with `invalid_action`, but it still failed the task.
- Failure event `failure_5c63d864` links the passing oracle counterpart `eval_fa78ceae` and recommends targeted pose/orientation variation.
- Candidate promotion was rejected by evidence: it remains `AVAILABLE/healthy`, not active. Base model `mdl_1a88cd40` was reloaded as `LOADED/healthy` after the comparison.
- A live status defect discovered after that reload was repaired: `/api/models/vla-jepa/status` had selected the first VLA registration even when a different checkpoint was resident. It now matches the worker's normalized resident checkpoint path first, then falls back to the `LOADED` catalog row.
- Live post-restart evidence: registration `mdl_1a88cd40`, lifecycle `LOADED`, health `healthy`, resident path `D:\VLA-JEPA-Pretrain`, bridge compatible, zero contract blockers. Reload command: `cmd_82d93021`.

### Governed policy candidate decision

- Added durable `PolicyCandidateDecisionRecord` state, exact evaluation evidence, immutable audit transitions, promotion gates, active-model swap recovery, and rollback handling.
- Promotion requires at least 3 successful held-out evaluations with distinct seeds (configurable by `ROBOTWORLD_POLICY_PROMOTION_MIN_EVALUATIONS`) and refuses any supplied failure.
- AI chat phrase `Reject the candidate; do not promote it.` resolved the current training run, candidate registration, base registration, and exact failed evaluation without invented IDs.
- Approval `approval_c123f665` authorized tool `training.policy_candidates.decide`; tool call `toolcall_3e4e8fd0` and command `cmd_9b86127d` created `policydecision_df5f0dcb/REJECTED` from `eval_26568722/grasp_miss`.
- The rejected candidate remains separate. Reload command `cmd_e5e578dd` restored/verified base `mdl_1a88cd40` as `LOADED`, worker-resident at `D:\VLA-JEPA-Pretrain`.
- Training UI shows the measured decision inline with the candidate instead of adding another dashboard panel.

### Local TRELLIS.2 Q4 proof

- The existing local Q4 CUDA artifact is exposed on Assets as an optional interactive PBR GLB preview, with conditioning and baked base-color images.
- Recorded run: 134.2 s, seed 6204, 91,506 vertices, 144,174 faces, 512px PBR texture, one PBR material, 9,428,452-byte GLB.
- This remains visual geometry only until a specific version passes the separate physical compiler and Franka gates.

## UI corrections completed

- `frontend/src/pages/Worlds.tsx`
  - restored the original hierarchy/editor/inspector/console layout;
  - preserved real asset placement editing;
  - removed invented Samsung/physics/provenance inspector values;
  - added backend/task/robot/controller/physical-asset/policy selection in the Agent inspector;
  - sends Execute to `/api/worlds/operate`;
  - supports oracle, VLA, autonomous loop, drawer, MuJoCo, and the fail-closed Isaac adapter;
  - displays authoritative recorded front/wrist evaluation frames rather than animating success.
- `frontend/src/pages/Assets.tsx`: restored the tracked original asset library; advanced physical-version evidence is collapsed by default instead of dominating the page; added an optional real local TRELLIS Q4 GLB/PBR proof viewer.
- `frontend/src/components/shell/Sidebar.tsx`: restored all original pages/navigation.
- `frontend/src/styles/tokens.css`: restored the original graphite/dark-gray/white palette.
- `frontend/src/styles/ui2.css`: removes the white active-navigation rail and decorative status dots, and styles recorded World results without rounded white side rails.
- `frontend/src/components/ai/AiChatPanel.tsx`: automatically continues planning after a tool result; hidden tool context is retained for reasoning but not rendered as an extra user message.
- `frontend/src/pages/Training.tsx`: shows validated LeRobot demonstrations, READY/FAILED/SUCCEEDED fine-tuning candidates, immutable candidate hash/size, and approval-gated candidate-only language.
  - now also shows durable PROMOTED/REJECTED decision state and measured evaluation count inline for each candidate.
- `frontend/src/pages/Settings.tsx`: removed the stale “Isaac deferred/disabled” claim and shows the detected Isaac Sim/Isaac Lab/OpenUSD runtime plus the exact remaining EULA blocker.

## Backend corrections completed

- `backend/app/main.py`
  - `/api/worlds/operate` is the typed shared human/agent command surface for the World UI;
  - chat receives bounded real workspace context;
  - offline intent handling still identifies current robot/model/asset and fails closed;
  - chat tool catalog now includes exact JSON input schemas, so proposed actions contain required fields;
  - broad training requests ask questions before proposing mutations;
  - typed high-confidence intents preserve the original instruction across hidden tool-result turns and route known `invalid_action` policy failures to the real oracle rather than repeating a broken VLA;
  - Isaac World commands validate that the selected robot is the registered Isaac OpenUSD Franka;
  - product prompt distinguishes dataset/preflight/optimization/candidate/promotion stages;
  - `/api/models/vla-jepa/status` now reports the canonical registered Franka bridge instead of the obsolete 5-D legacy contract.
  - VLA status is bound to the checkpoint actually resident in the isolated worker, so an inactive candidate cannot be misreported as the active base policy.
- `backend/app/services/lerobot_dataset.py` and `backend/workers/lerobot_dataset_worker.py`
  - export synchronized successful oracle trajectories through the exact installed LeRobot API;
  - validate paths/hashes, camera shapes, state/action dimensions, metadata counts, and dataset readback;
  - keep artifacts local and immutable; no Hub push.
- `backend/app/services/agent_tools.py`: exposes `training.datasets.create_from_evaluation` as an approval-gated typed mutation.
- `backend/app/services/lerobot_training.py`, `backend/workers/lerobot_training_worker.py`, and `backend/workers/lerobot_training_execute_worker.py`: durable preflight catalog, bounded offline optimizer execution, immutable candidate hashing, Windows-safe completion pointer, and direct-run reconciliation.
- Agent tools now include approval-gated `training.vla_jepa.validate_fine_tune` and `training.vla_jepa.execute_fine_tune`.
- `backend/app/services/policy_lifecycle.py`: measured candidate promotion/rejection gates, rollback model preservation, activation recovery, command envelopes, and audit events.
- Agent tools now also include approval-gated `training.policy_candidates.decide` and `training.policy_candidates.rollback`.
- `backend/tests/test_api.py`
  - covers unsupported World execution contracts;
  - covers grounded training clarification;
  - covers stopped-model -> load and loaded-model -> bounded-run progression;
  - verifies chat exposes required tool-schema fields.
  - verifies the chat proposes dataset export only for recorded successful oracle evidence.
  - verifies worker-resident checkpoint selection wins over catalog ordering in the VLA status API.

## Current architecture from code

- FastAPI control plane plus React/Vite UI.
- Internal SQLite catalog and filesystem artifact store; critical state does not depend on SigNoz.
- 54 versioned JSON-schema agent tools with approval, audit, durable IDs, and no arbitrary shell tool.
- MuJoCo is the currently validated authoritative backend through the simulation boundary.
- Franka uses seven arm joints, a parallel gripper, deterministic reset, differential IK, front RGB, and hand-mounted wrist RGB.
- Canonical asset manifests compile immutable visual, collision, OpenUSD, and MuJoCo artifacts.
- VLA-JEPA runs in `D:\RobotWorldRuntimes\vla-env` against the local checkpoint and LeRobot checkout.
- LeRobot datasets are written under `backend/data/datasets/<dataset_id>` by an isolated worker and listed in Training.
- Fine-tuning candidates are written under `backend/data/training-runs/<run_id>` and never replace the active checkpoint.
- NVIDIA Isaac Sim 6.0.1/Isaac Lab are isolated behind the Isaac adapter. Runtime launch remains blocked by the unaccepted NVIDIA EULA; the project does not set acceptance automatically.
- OpenTelemetry spans persist locally and export to the live self-hosted SigNoz Community deployment.
- Port remains deferred and disabled.

## Requirement status

| Feature | Status | Evidence / remaining limitation |
| --- | --- | --- |
| Original Worlds/Assets/navigation restored | IMPLEMENTED_AND_TESTED | Files restored; frontend typecheck/lint/build and HTTP routes pass. Browser attachment was unavailable for click QA. |
| World natural-language instruction -> real physics | IMPLEMENTED_AND_TESTED | Active Kitchen Juice Workspace `live_c54c9a8f` / `eval_a5e8369b` resolved apple + blender, streamed 1,636 frames, and passed measured contact/containment predicates. |
| Models page/local VLA registration/load | IMPLEMENTED_AND_TESTED | `mdl_1a88cd40` is `LOADED/healthy`; real CUDA load executed. |
| Robots page/default Franka | IMPLEMENTED_AND_TESTED | Real Panda MJCF, gripper, cameras, reset, and oracle. |
| Authoritative Franka pick/place | IMPLEMENTED_AND_TESTED | Direct and autonomous oracle evaluations passed. |
| Real VLA-JEPA bridge/evaluation | IMPLEMENTED_AND_TESTED | Exact active-kitchen `eval_3d3211dc` ran 100 finite base-checkpoint actions with front/wrist observations against the same apple/blender runtime as the oracle. |
| VLA-JEPA task success | BROKEN | Active-kitchen zero-shot run `eval_3d3211dc` failed `grasp_miss`; no finger contact and 0.865 m final target error. A one-step candidate also remains rejected. Oracle success is reported separately. |
| LeRobot dataset writer | IMPLEMENTED_AND_TESTED | `dataset_bf1181b4` passed exact LeRobot write/readback validation; agent tool and Training UI are connected. |
| VLA-JEPA fine-tuning worker | PARTIAL | Real one-step candidate `trainrun_89c6a7f8` completed and was hashed; execution is limited to a verified 1-10 step profile and lacks durable cancel/resume and promotion gates. |
| Candidate registration/held-out comparison | IMPLEMENTED_AND_TESTED | `mdl_3394f1ab` loaded offline; `eval_26568722` failed `grasp_miss`, so candidate was not promoted and base was restored. |
| Policy promotion/rejection/rollback lifecycle | IMPLEMENTED_AND_TESTED | Agent-created `policydecision_df5f0dcb/REJECTED` is bound to failed evaluation evidence; promotion has multi-seed success gates and active-model recovery; base remained active. |
| Continuous Worlds Franka viewer | IMPLEMENTED_AND_TESTED | `live_c54c9a8f` streamed 1,636 active-kitchen MuJoCo frames with compiled Panda meshes, generated-apple PBR/body-pose metadata, two camera views, and persisted `eval_a5e8369b/SUCCEEDED`. Browser attachment was unavailable for visual click QA. |
| Arbitrary prompt -> robot behavior | PARTIAL | Pick/place, drawer, VLA, and bounded agent routes are explicit. Unsupported intents such as throw now fail closed before execution; they are not yet compiled into new task predicates/controllers. |
| Autonomous oracle-before-VLA diagnosis loop | IMPLEMENTED_AND_TESTED | Live run `autorun_266cd0c7` completed three persisted scenarios, three passing oracle episodes, three finite VLA episodes, and stopped on the configured consecutive-failure policy. |
| OpenUSD + runtime compilation | IMPLEMENTED_AND_TESTED | Physical versions contain canonical OpenUSD plus MuJoCo artifacts. |
| Real TRELLIS.2 Q4 generation | IMPLEMENTED_AND_TESTED | Existing CUDA proof GLB is 9,428,452 bytes with PBR textures and recorded provenance. |
| Multiple texture/appearance selection | PARTIAL | Assets can display the real embedded PBR GLB and texture artifact; only one recorded Q4 appearance exists, so no second appearance is claimed or selectable. |
| Bright Data exact evidence live call | IMPLEMENTED_NOT_LIVE_TESTED | Server reports Bright Data configured; no billable/live exact-object request was issued in this correction pass. |
| Governed scraper repair | IMPLEMENTED_AND_TESTED | Controlled golden/canary/promotion/rollback path exists. |
| Articulated product cabinet/drawer | PARTIAL | Controlled real drawer oracle works; evidence-backed multipart product asset remains missing. |
| Isaac Sim authoritative run | BLOCKED_BY_LICENSE | Isaac Sim 6.0.1 and Isaac Lab are detected/configured; correct OpenUSD Franka dispatch reaches the isolated adapter, but the API process cannot accept NVIDIA's EULA for the operator. |
| Self-hosted SigNoz export/display | IMPLEMENTED_AND_TESTED | Community `v0.137.1` is healthy on 8080, OTLP is live on 4317/4318, and ClickHouse contains RobotWorld spans. |
| Server-side SigNoz read queries | BLOCKED_BY_CREDENTIAL | Adapter/probe exists; create a local SigNoz service-account API key after first-admin onboarding. No cloud key is needed. |
| Port | MISSING | Intentionally deferred. |

## Commands run in this correction pass

Most recent current-process/3D correction (2026-08-21):

```powershell
# from D:\RobotWorldProject\backend
.\.venv\Scripts\python.exe -m pytest tests/test_api.py::test_world_operator_rejects_implicit_or_unsupported_execution_contracts tests/test_api.py::test_frontend_diagnostics_and_isaac_reports_explicit_runtime_state tests/test_franka_oracle.py::test_worlds_live_stream_is_continuous_and_persists_same_evaluation -q
# 3 passed in 14.68s

.\.venv\Scripts\python.exe -m pytest -q
# 86 passed in 113.48s

# from D:\RobotWorldProject
backend\.venv\Scripts\python.exe backend/scripts/run_live_franka_stream.py --robot-id franka-panda-mujoco-f9a4918f6663
# live_b314f67c; 138 frames; eval_3c30c0f1; SUCCEEDED

Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/integrations/signoz/probe
# connected=true; v0.137.1; otlpHttpStatus=200

Invoke-RestMethod http://127.0.0.1:8000/api/diagnostics/runtime
# healthy; zero current-process ERROR/WARN events

Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/models/mdl_1a88cd40/load
# cmd_837c2350/SUCCEEDED; CUDA worker PID 10868; 2,593,879,303 parameters resident

# from D:\RobotWorldProject\frontend
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
# all passed; Vite built 95 modules
```

From `D:\RobotWorldProject\backend`:

```powershell
.\.venv\Scripts\python.exe -m py_compile app\main.py
.\.venv\Scripts\python.exe -m pytest tests\test_api.py -q
# 14 passed in 8.00s

.\.venv\Scripts\python.exe -m pytest -q
# 86 passed in 54.82s after the continuous Worlds stream integration

.\.venv\Scripts\python.exe scripts\run_live_franka_stream.py --api http://127.0.0.1:5173 --robot-id franka-panda-mujoco-f9a4918f6663 --asset-version-id assetver_2882ab27
# 330 frames, 11 motion phases, eval_daee839e SUCCEEDED

.\.venv\Scripts\python.exe -m pytest tests\test_api.py tests\test_vla_policy_worker.py -q
# 25 passed in 10.92s

.\.venv\Scripts\python.exe -m pytest tests\test_agent_tools.py tests\test_api.py tests\test_lerobot_dataset.py -q
# 22 passed in 9.44s

D:\RobotWorldRuntimes\vla-env\Scripts\python.exe -m ensurepip --upgrade
D:\RobotWorldRuntimes\vla-env\Scripts\python.exe -m pip install -e 'D:\LeRobot[dataset]'
# installed the official local LeRobot dataset extra; no model/checkpoint download

D:\RobotWorldRuntimes\vla-env\Scripts\python.exe -m pip install -e 'D:\LeRobot[training]'
# installed official local training dependencies; no checkpoint download

D:\RobotWorldRuntimes\vla-env\Scripts\python.exe -u workers\lerobot_training_execute_worker.py --manifest data\training-runs\trainrun_89c6a7f8\preflight_input.json
# one real optimizer step completed; immutable candidate saved and later reconciled into the catalog
```

From `D:\RobotWorldProject\frontend`:

```powershell
npm.cmd run typecheck
# passed

npm.cmd run lint
# passed

npm.cmd run build
# passed; Vite built 94 modules
```

Live checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-WebRequest http://127.0.0.1:5173/#/worlds -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:5173/#/assets -UseBasicParsing
# backend healthy; both frontend routes HTTP 200

Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/models/mdl_1a88cd40/load
# cmd_82d93021; CUDA worker resident/healthy after backend restart

Invoke-RestMethod http://127.0.0.1:8000/api/models/vla-jepa/status
# mdl_1a88cd40 / LOADED / healthy / D:\VLA-JEPA-Pretrain / bridge compatible / zero blockers

Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/chat
# resolved "Reject the candidate; do not promote it." to the exact failed candidate/evaluation

Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/agent/tools/invoke
# toolcall_3e4e8fd0 / cmd_9b86127d / policydecision_df5f0dcb REJECTED

Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/worlds/operate
# cmd_2cb929b5 / eval_6a859d30 SUCCEEDED for the exact user instruction

Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/agent/tools/invoke
# toolcall_43f32173 / cmd_c690a6d8 / eval_fa78ceae SUCCEEDED through the AI-chat action chain

Invoke-RestMethod http://127.0.0.1:8000/api/simulation/isaac
# Isaac Sim 6.0.1 installed/configured; launch blocked only by operator EULA acceptance

Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/integrations/signoz/probe
# connected=true / community-self-hosted / v0.137.1 / OTLP 4318

docker exec robotworld-signoz-telemetrystore-clickhouse-0-0 clickhouse-client --query `
  "SELECT serviceName, count(), max(timestamp) FROM signoz_traces.signoz_index_v3 WHERE serviceName='robotworld-backend' GROUP BY serviceName"
# robotworld-backend / 1231 spans / latest 2026-08-21 20:15:13.058118900

Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/worlds/operate
# cmd_9dcd0683 / autorun_2ba734af / real oracle -> VLA -> structured diagnosis
```

One initial verification command was intentionally retained as evidence of an invocation mistake: running `python -m pytest backend/tests/...` from the repository root failed because `app` was not on `PYTHONPATH`, and bare `npm` was blocked by PowerShell script policy. Re-running from `backend` with `.\.venv\Scripts\python.exe` and from `frontend` with `npm.cmd` produced the passing results above.

`git diff --check` passed; only expected LF/CRLF warnings were emitted.

The in-app browser provider returned `No browser is available`, so this pass does not claim visual click-through of the rebuilt UI. Recorded physics images were inspected directly and API/UI routes were exercised live.

## 2026-08-22 one-action active-world task compiler and live evidence

Status: **IMPLEMENTED_AND_TESTED** for automatic named-entity grounding and three measured apple task relations. **PARTIAL/BROKEN** for thin-object grasping (banana). VLA-JEPA training/task success was not fabricated or started in this slice.

Implemented:

- `WorldOperateRequest.task="auto"` is valid only for the active MuJoCo world with the deterministic oracle. `/api/worlds/live-sessions` compiles the instruction once into a durable `compiledGoal` containing source/target IDs, names, relation, and grounding revision.
- The compiler is noun-independent across registered active-world placements and recognizes `on top of`/`onto`, `inside`/`into`, and drop/throw/toss off the measured table/counter. It rejects ambiguous entities, unsupported relations, missing physical source versions, and task-contract conflicts before physics.
- Worlds has one `Run instruction` action (Ctrl/Cmd+Enter also runs). The separate Plan button/task selector was removed. The 3D editor and authoritative live stream use the same persisted Panda mount and active asset placements.
- Inside-sink composition now cuts the counter collider around the measured sink AABB and authors a basin floor plus four walls. Inside uses rectangular full-object containment. `on_top_of` uses centre-of-mass-inside-support plus actual contact/release/stability gates, allowing physically stable overhang without loosening settle/contact requirements.
- Long-flat grasp clearance and joint-command pacing are gated by measured planar aspect ratio. Compact apple/cube objects retain their validated controller profile.
- Durable trajectory samples no longer repeat `renderGeometries`; those are streamed only to the live viewport. `backend/scripts/compact_evaluation_results.py` verifies each immutable `evaluation.json` before compacting the SQLite duplicate.
- The live-stream CLI request timeout is now 60 s because compiling the generated GLB/collision/counter/Panda scene can legitimately exceed the old 10 s client timeout.

Live authoritative results (all active `door-validation-lab`, registered Panda `franka-panda-mujoco-f9a4918f6663`, seed `1048577`):

- `Pick up the apple and put it inside the sink.` -> `live_f1fc531e` / `eval_d191bc93` **SUCCEEDED**; 749 WebSocket frames, 30.722 simulated seconds, front+wrist JPEG observations, PBR source geometry in every frame, sink containment/contact/release/settle passed.
- `Pick up the apple and drop it off the table.` -> `live_6b51f1ba` / `eval_fcc2f710` **SUCCEEDED**; 1,046 frames, 42.582 simulated seconds, distinct `transport_off_table`, `release_off_table`, and `settle_after_drop` phases.
- `Pick up the apple and put it on top of the orange.` -> `live_9d64753b` / `eval_0b2ee738` **SUCCEEDED**; 755 frames, support contact and settle passed, COM residual `-0.0333986 m` under `center_of_mass_inside_support_polygon_with_2mm_margin`.
- `Pick up the banana and put it on top of the blender.` -> `live_f71647d7` / `eval_ee1ffa2c` **FAILED/object_dropped**; the source is reachable and bilateral contact occurred, but the current thin-object top grasp recorded 427 left-finger/object, 34 right-finger/object, and 213 left-finger/counter samples before lift failure. Next repair is an oriented side-grasp affordance, not a success-label or predicate change.
- The orange source GLB was compiled separately as `assetver_831e43af` and correctly **REJECTED** because its convex body did not settle within the configured stability window. In apple-on-orange, orange is therefore a fixed measured-AABB support proxy corresponding to the authored target, not a promoted dynamic orange asset.

Persistence repair evidence:

```powershell
cd D:\RobotWorldProject\backend
.\.venv\Scripts\python.exe scripts\compact_evaluation_results.py
# dry-run: 46 verified rows; 45,358 repeated geometry copies; 1,504,382,660 -> 67,940,465 bytes; skipped=[]

.\.venv\Scripts\python.exe scripts\compact_evaluation_results.py --apply
# same counts applied; immutable evaluation.json artifacts retained

.\.venv\Scripts\python.exe scripts\compact_evaluation_results.py
# changedRows=0; skipped=[]
```

Verification:

```powershell
cd D:\RobotWorldProject\backend
.\.venv\Scripts\python.exe -m pytest -q
# 92 passed in 82.63s

.\.venv\Scripts\python.exe -m compileall -q app scripts
# passed

cd D:\RobotWorldProject\frontend
npm.cmd run lint
# passed
npm.cmd run build
# passed; TypeScript + Vite, 95 modules

Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-WebRequest http://127.0.0.1:5173/#/worlds -UseBasicParsing
# backend healthy, MuJoCo 3.11.0/500 Hz, SigNoz exporting; frontend HTTP 200
```

Files changed in this slice: `backend/app/contracts.py`, `backend/app/main.py`, `backend/app/services/franka_live.py`, `backend/app/services/franka_pick_place.py`, `backend/scripts/run_live_franka_stream.py`, `backend/scripts/compact_evaluation_results.py`, `backend/tests/test_api.py`, `backend/tests/test_franka_oracle.py`, `frontend/src/pages/Worlds.tsx`, and this state file. Other dirty-worktree changes are preserved user/prior work.

## Exact next executable task

Implement a geometry-derived side-grasp candidate for long, thin rigid assets. Compile at least top and side grasp frames with approach vector, closing axis, required width, table-clearance sweep, IK reachability, and finger/support collision preview; select a frame only after the deterministic pre-grasp/grasp/lift gate. Re-run `assetver_7aa76e7d` banana at the current persisted pose and keep `eval_ee1ffa2c/object_dropped` visible until a new run passes.

Then add frame callbacks and bounded WebSocket messages to the authored-kitchen VLA evaluator so Worlds can display real learned actions continuously. Do not label oracle demonstrations as VLA learning or task success.

Then run the active kitchen apple-to-blender oracle over at least three distinct persisted placement seeds and add those runs as an automated integration gate. The one current production seed passes, but variation robustness is not yet proven.

The typed manual-control session and explicit off-table task now exist. The next manual-control hardening is persisted pause/resume/reset/cancellation plus controller-state audit events.

After those interaction contracts pass, build a targeted pose/orientation scenario and demonstration dataset from `failure_5c63d864` plus passing oracle `eval_fa78ceae`, then create and evaluate a fresh candidate across at least three distinct held-out seeds. Do not reuse or promote rejected `mdl_3394f1ab`.

After that:

1. Add durable cancellation/resume around optimizer execution before running beyond the 1-10 step verified profile.
2. Run live Bright Data only for a user-selected exact product; the configured provider call may be billable.
3. Compile a real evidence-backed multipart drawer/cabinet.
4. Create the first local SigNoz admin/service account, store its key as a server-only secret reference, and live-test the existing read-only query adapter.
5. After reading NVIDIA's license, accept it as the operator with `[Environment]::SetEnvironmentVariable("OMNI_KIT_ACCEPT_EULA","YES","User")`, restart RobotWorld, then execute the bounded Isaac Franka worker from Worlds.

Do not claim VLA task success, candidate promotion, a new live Bright Data request, product articulation, Isaac execution, or SigNoz integration until their recorded gates pass.

## 2026-08-21 — Lighthouse remediation pass (frontend only, additive/scoped)

Lighthouse 13.4.0 against the Vite dev server (127.0.0.1:5173) reported Perf 61 / A11y 85 / BP 92 / SEO 73.
Key context: the "Minify JavaScript ~3,983 KiB" and "unused JavaScript" findings are dev-mode artifacts
(unminified, unbundled modules); the production build is code-split (main chunk 289 KB / 92 KB gzip,
per-page lazy chunks, GLTFLoader isolated). No backend, physics, policy, or evidence path was touched.

Changes (all frontend-only):
- frontend/index.html: added <meta name="description"> (SEO: document lacks meta description).
- frontend/public/robots.txt: NEW valid robots.txt (User-agent: * / Allow: /); dev server previously
  served HTML fallback at /robots.txt which parsed as invalid (20 errors).
- frontend/public/llms.txt: NEW agent-facing summary with hash-route map (Agentic Browsing llms.txt audit).
- Titlebar.tsx: user-menu button got ria-label="Account menu" (button-has-accessible-name).
- ai-chat-input.tsx: gallery close button got ria-label="Close image gallery" (same audit; these were
  the only two icon-only buttons without an accessible name in source — all others have visible text or title).
- tokens.css: --text-3 #929292 -> #9E9E9E so muted text meets WCAG AA >= 4.5:1 on bg-panel-2/panel-3
  (previously ~4.0-4.5:1, flagged contrast).
- components.css: non-composited animations converted to composited transforms:
  .busy-bar > i left-keyframes -> translateX(-100%..286%); .skl background-position shimmer ->
  ::after sheen translateX sweep; .loading-label background-clip:text transparent shimmer ->
  solid var(--text-2) + opacity pulse (also removes transparent-text contrast flag).
  Visual behavior preserved; prefers-reduced-motion rules unchanged.

Verification:
npm.cmd run typecheck OK;
npm.cmd run lint 0 warnings/0 errors;
pm.cmd run build
OK (95 modules, dist emitted). Backend pytest not re-run (no backend change).

Not addressed (intentional): tabindex>0 and console-error findings are runtime/library artifacts of the
dev session (no tabIndex>0 exists in source); long-task/TBT numbers are dominated by dev-mode serving;
"User Timing marks" come from intentional runtimeDiagnostics performance.mark calls and do not affect score.
Re-measure with a production preview build before drawing conclusions from Performance category deltas.
