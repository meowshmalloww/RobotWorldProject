# Codex Master Prompt — Build the Complete RobotWorldProject

You are the lead robotics, simulation, 3D asset, AI-agent, and platform engineer working directly inside the repository:

`meowshmalloww/RobotWorldProject`

Your job is not to produce another architecture essay, mock dashboard, toy animation, or disconnected proof of concept. Your job is to inspect the existing repository, preserve what is already useful, repair what is broken, and implement the complete working system incrementally until the highest-priority end-to-end path is real and testable.

Do not stop after writing plans. Audit first, then immediately implement, run, debug, test, and continue phase by phase. Keep an evidence-based progress file so another Codex session can resume without losing state.

---

## 1. Product definition

RobotWorldProject is an autonomous **evidence-to-interactive-world compiler and failure-driven curriculum engine for physical AI**.

The central product is not model training itself. The product is the system that builds, validates, and continuously improves the exact interactive worlds, objects, and task variations a robot or embodied policy needs.

The intended loop is:

1. A user registers or connects an AI model, such as a VLA, VLM, LLM, or world model.
2. A user registers a robot embodiment, such as a robot arm, mobile manipulator, or full-body robot.
3. The system loads the robot into an interactive physics world or a reusable world template.
4. The user or autonomous agent gives a task such as:
   - “Pick up the apple and place it in the blender cup.”
   - “Open this cabinet.”
   - “Pick the package and place it in the blue bin.”
5. The policy attempts the task using real synchronized observations and real simulation actions.
6. The system records contacts, transforms, joint states, camera frames, policy actions, timing, errors, and task predicates.
7. The platform agent diagnoses the actual failure category.
8. When world coverage is missing, the agent uses Bright Data discovery and Scraper Studio to find and extract evidence for the needed real-world object or variation.
9. TRELLIS.2 or an exact existing CAD/3D source produces visual geometry.
10. RobotWorldProject compiles that geometry into a physically meaningful, interactive asset with metric scale, collision geometry, mass, inertia, friction, semantics, affordances, and—when applicable—parts and joints.
11. The asset is validated in the physics backend before a learned policy is evaluated.
12. The system adds the validated asset to the world at a physically valid location, runs the task again, measures the result, and selects the next useful scenario.
13. When sufficient validated demonstrations exist and training is enabled, the system can fine-tune or adapt the VLA, version the checkpoint, evaluate it, and promote it only when measured gates pass.
14. The loop stops when the target is reached, the configured budget is exhausted, no meaningful improvement is occurring, or a human stop policy is triggered.

Example autonomous progression:

```text
apple pick succeeds
  -> coverage planner selects banana as a useful shape/grasp variation
  -> banana asset does not exist
  -> Bright Data discovers exact sources and Scraper Studio extracts evidence
  -> TRELLIS.2 generates visual geometry
  -> asset compiler creates collision, mass, semantics, and OpenUSD/MJCF artifacts
  -> placement planner puts the banana on the counter without penetration
  -> deterministic oracle validates reachability and graspability
  -> VLA-JEPA attempts the task
  -> failure is diagnosed from structured telemetry
  -> targeted pose/material/shape variations are generated
  -> evaluate, optionally adapt, and repeat
```

This must be a real pipeline. A dashboard animation that says these steps happened is not acceptable.

---

## 2. Scope corrections that override older documents

These decisions are authoritative even when older repository documents say otherwise.

### 2.1 Port is deferred

Port.io is **not part of the current implementation**.

Do not:

- install a Port SDK;
- require a Port account or API key;
- create Port blueprints, entities, actions, scorecards, or workflows;
- query Port for world coverage;
- use Port for scraper approval;
- add Port screens to the current UI;
- make any current acceptance gate depend on Port.

Use RobotWorldProject’s own backend, database, event log, catalog APIs, and UI for:

- robots;
- models and policy revisions;
- skills;
- object identities and evidence;
- assets and asset versions;
- world templates and world versions;
- scenarios;
- evaluations;
- coverage;
- scraper versions and repair runs;
- promotion and rollback;
- audit history.

If the repository already contains harmless Port placeholders, mark them deferred and place them behind a disabled feature flag. Do not delete unrelated user work. Do not spend implementation time completing Port. A future catalog-provider interface may be documented, but the current implementation must use the internal catalog.

### 2.2 SigNoz means self-hosted open-source SigNoz

Do not use SigNoz Cloud and do not require a SigNoz Cloud ingestion key.

The current observability strategy is:

1. Instrument all backend services and workers with OpenTelemetry from the beginning.
2. Persist critical run state and structured evaluation events in RobotWorldProject’s own database so the system remains correct when SigNoz is offline.
3. Connect later to a **self-hosted SigNoz Community/open-source deployment** through configurable OTLP endpoints.
4. When the local SigNoz instance is available, let the platform agent query it through a server-side read-only adapter using the official API or self-hosted SigNoz MCP capability supported by the pinned SigNoz version.
5. Never expose SigNoz credentials or direct telemetry-query authority to the browser.

Do not spend the first implementation phase installing SigNoz. Add correct OpenTelemetry instrumentation and configuration now; enable and validate the self-hosted SigNoz deployment after the core simulation vertical slice works, unless a working local instance already exists.

### 2.3 Do not install NVIDIA Isaac Sim

NVIDIA Isaac Sim and Isaac Lab are references for good robotics/simulation architecture, asset semantics, and validation practices. They are not current runtime dependencies.

Do not:

- download or install Isaac Sim;
- require Omniverse Launcher;
- require an Isaac container;
- claim that a browser animation is Isaac Sim;
- copy proprietary or license-incompatible NVIDIA code;
- block the project on Isaac availability.

Use open standards and license-compatible open-source components. The default current physics recommendation is **MuJoCo** with a pinned Franka Panda model from MuJoCo Menagerie or another properly licensed source, unless the repository already contains a demonstrably better physics backend that passes the required tests.

OpenUSD/UsdPhysics remains an important canonical interchange and authoring format. Because the runtime physics backend may not execute arbitrary USD directly, implement a backend compiler that produces a backend-specific representation such as MJCF from the same canonical asset manifest and part graph. Do not pretend that writing a `.usd` file alone makes the asset executable.

Keep a `SimulationBackend` interface so Isaac Sim or another engine can be added later without rewriting product logic.

### 2.4 Large models already exist locally

The user already has large model repositories/checkpoints on the local `D:` drive, including TRELLIS.2, DINOv3, and LeRobot/VLA-JEPA-related files. Do not download duplicate multi-gigabyte repositories or checkpoints automatically.

Implement path-based configuration and validation, for example:

- `TRELLIS2_REPO_PATH`
- `TRELLIS2_CHECKPOINT_PATH`
- `DINOV3_REPO_PATH`
- `DINOV3_WEIGHTS_PATH`
- `LEROBOT_REPO_PATH`
- `VLA_JEPA_CHECKPOINT_PATH`
- `ROBOT_ASSET_ROOT`
- `ROBOTWORLD_MODEL_ROOTS`

Support Windows host paths and explicit worker/container/WSL path mappings. Validate that a configured path exists and contains the expected files. Record model revision and hashes. Do not silently substitute a different model.

The browser must not upload a 20 GB checkpoint. A local backend can receive a path reference, validate it against an allowlisted root, and load it in the correct worker environment.

### 2.5 User-supplied model names are configuration, not assumptions

Names such as `gpt-luna`, `gpt-terra`, or a custom “high-thinking” model may be user-defined aliases or model identifiers routed through an OpenAI-compatible endpoint.

Do not assume that they are official model names. Implement a provider/model registry with:

- provider type;
- base URL;
- model ID or alias;
- API key environment-variable name;
- modality and tool capabilities;
- structured-output support;
- image support;
- context limits when known;
- health-check result;
- enabled/disabled state.

Never commit API keys.

---

## 3. Working method: inspect, then build—not documentation-only

Before major architectural changes:

1. Check `git status`, current branch, ignored files, and uncommitted work.
2. Read the complete repository tree, source, configs, scripts, docs, tests, and package manifests. Exclude generated dependency caches and large model binaries from text inspection.
3. Find all entry points and run instructions.
4. Run the existing frontend, backend, tests, type checks, linters, and build commands.
5. Search for:
   - TODO/FIXME;
   - mock data;
   - fake percentages;
   - `sleep()`-based fake jobs;
   - hard-coded demo success;
   - silent exception swallowing;
   - disconnected buttons;
   - unused routes;
   - duplicated state;
   - hard-coded local paths;
   - exposed secrets;
   - frontend-only features with no backend action;
   - physics/rendering code that is only visual animation.
6. Create or update one concise persistent progress file, preferably:

   `docs/CODEX_EXECUTION_STATE.md`

   It must contain:
   - exact commands run;
   - current architecture discovered from code;
   - requirement status with file evidence;
   - current blockers;
   - completed patches;
   - test results;
   - next executable task.

7. Then begin implementation. Do not spend an entire session generating audit documents without improving code.

For every feature, report one of:

- `IMPLEMENTED_AND_TESTED`
- `IMPLEMENTED_NOT_LIVE_TESTED`
- `PARTIAL`
- `BROKEN`
- `MOCK_ONLY`
- `MISSING`
- `BLOCKED_BY_CREDENTIAL`
- `BLOCKED_BY_HARDWARE`

Never call a feature complete when only the UI exists.

Preserve unrelated user changes. Never use blanket staging such as `git add .`, `git add -A`, or `git add --all`. Keep patches reviewable.

Do not repeatedly ask the user questions that the repository, local config, environment, or official documentation can answer. Make a reasonable documented choice and continue. Ask only when a destructive decision or truly unavailable secret/path makes safe progress impossible.

---

## 4. Core architecture invariants

Adapt names and directories to the existing repository rather than rewriting everything blindly, but enforce these logical boundaries.

### 4.1 One backend command surface for both humans and AI

Every meaningful UI action must call a typed backend command or query. The autonomous platform agent must use the same command/query layer.

Do not make the AI click its own UI as the primary control mechanism.

Implement a tool/skill registry with JSON-schema-validated inputs and outputs. At minimum, expose capabilities for:

- list/register/validate/load/unload models;
- list/register/validate/load robots;
- inspect robot joints, links, sensors, and action space;
- create/load/save world templates;
- import/build/validate/promote/rollback assets;
- place/remove/move assets in a world;
- create and run tasks;
- start/stop/pause/resume evaluations;
- query run state, logs, traces, metrics, contacts, trajectories, and camera frames;
- diagnose failures;
- search and collect evidence with Bright Data;
- request and validate scraper repair;
- trigger TRELLIS.2 generation;
- trigger rigid or articulated asset compilation;
- run deterministic validation;
- run VLA evaluation;
- create training datasets;
- launch fine-tuning when policy and budget permit;
- compare and promote policy revisions;
- generate targeted next scenarios;
- cancel a runaway autonomous loop.

All commands must produce durable IDs, state transitions, and audit events.

### 4.2 Internal catalog and source of truth

Use the existing database if suitable. Otherwise implement a clean metadata store, preferably PostgreSQL for production with a lightweight local-development option only if needed.

The internal catalog must store:

- model registrations and revisions;
- robot definitions and revisions;
- skills/tasks;
- object identities;
- evidence records and bundles;
- assets and versions;
- world templates and versions;
- scenario specifications;
- pipeline runs and state transitions;
- evaluations and failure events;
- coverage dimensions and bins;
- scraper collectors, versions, and repairs;
- model-training runs and policy revisions;
- promotion and rollback history;
- idempotency keys;
- provenance and licenses.

Large files belong in an artifact store abstraction with a local-filesystem implementation and an optional S3-compatible implementation. Do not put GLBs, USD files, videos, checkpoints, or image datasets directly in database rows.

### 4.3 Durable orchestration

Long-running work must survive browser refreshes and backend restarts.

Implement persisted state machines and resumable jobs. Reuse a robust existing queue/orchestration mechanism if the repository already has one. Do not introduce a large new platform only because an older document mentioned it.

Requirements:

- idempotent activity execution;
- retry policy by failure type;
- cancellation;
- timeout;
- heartbeat/progress based on real work;
- persisted input/output artifact IDs;
- explicit terminal states;
- restart/resume tests;
- no fake progress timers.

### 4.4 Isolated model/runtime workers

Do not force React, the API server, TRELLIS.2, DINOv3, LeRobot/VLA-JEPA, and the physics runtime into one dependency environment.

Use worker boundaries with pinned environments and typed messages. A local-process worker is acceptable initially if it is isolated and restartable; containers are optional, not mandatory.

Suggested logical workers:

- API/control-plane service;
- agent service;
- Bright Data/evidence worker;
- image-understanding worker;
- TRELLIS.2 worker;
- articulation worker;
- asset compiler;
- physics/simulation worker;
- VLA policy worker;
- evaluator/training worker.

---

## 5. User-configurable AI model registry

Add a real **Models** page to the sidebar and corresponding backend APIs.

Support these model connection modes:

1. Local repository/checkpoint path.
2. Hugging Face repository ID.
3. OpenAI-compatible remote API.
4. Native provider adapter where one already exists.
5. Local inference server endpoint.

Support model roles:

- platform agent LLM/VLM;
- VLA policy;
- vision encoder;
- world model;
- image-to-3D generator;
- segmentation/part-understanding model;
- embedding or retrieval model.

A model registration must include:

- immutable registration ID and revision;
- display name;
- role(s);
- provider/adapter type;
- local path or remote endpoint;
- model ID/revision;
- expected device and precision;
- input/output schema;
- camera/view requirements;
- state/action dimensions for policies;
- health status;
- last successful load;
- license metadata;
- secret references, never raw secrets.

Implement capability probing and a dry-run health check. A model that fails to load must show a real error and remain unavailable; do not silently switch to a mock.

---

## 6. User-configurable robot and embodiment registry

Add a real **Robots & Embodiments** page to the sidebar and corresponding backend APIs.

The architecture must support more than one robot arm. It must be able to represent:

- fixed robot arms;
- mobile manipulators;
- dual-arm robots;
- humanoids or other full-body robots;
- robots with multiple end effectors;
- custom sensors and camera mounts.

Support import adapters for:

- MJCF;
- URDF;
- OpenUSD where the current parser supports it;
- backend-native model definitions;
- GLB/gltf as **visual geometry only** unless kinematics, joints, actuators, and collisions are supplied separately.

A visual GLB is not a controllable robot.

Create canonical contracts for:

- links and parent-child hierarchy;
- joints, limits, damping, friction, and actuation;
- base type (`fixed`, `mobile`, `floating`);
- end-effectors;
- grippers;
- sensors;
- camera intrinsics/extrinsics;
- collision groups;
- controller configuration;
- observation schema;
- action schema;
- reset pose;
- safety limits.

### 6.1 Required default embodiment: Franka Panda

Implement a known-good default Franka Panda 7-DoF arm with parallel two-finger gripper using a pinned, properly licensed open-source model, preferably the Panda model in MuJoCo Menagerie loaded directly or through `robot_descriptions`.

Preserve the model’s license and attribution.

Required Franka setup:

- seven arm joints;
- two-finger gripper;
- correct joint and actuator limits;
- known home pose;
- named end-effector frame;
- fixed base for the first milestone;
- one static/front camera;
- one wrist camera attached to the correct end-effector/hand link;
- configurable wrist-camera transform;
- collision groups that prevent obvious self-collision errors without hiding real collisions;
- deterministic reset.

Do not guess the wrist camera location silently. Store the mount transform in the robot definition, render a calibration view, and provide a validation test that the gripper and working area are visible.

---

## 7. Physics runtime and rendering

### 7.1 Source of physical truth

The backend physics simulation is authoritative for:

- transforms;
- velocities;
- contacts;
- gravity;
- rigid-body motion;
- friction;
- restitution/bounce;
- joint constraints;
- actuator commands;
- collision response;
- sensor timing;
- task predicates.

The frontend viewer mirrors authoritative state. It is not allowed to invent motion.

An object released by the gripper must fall, collide, settle, slide, roll, or bounce according to its physical configuration. It must not hover because a UI animation ended.

### 7.2 Default backend

Unless the repository already contains a tested production-quality backend, implement a MuJoCo backend first because it is open source, cross-platform, mature for articulated contact dynamics, and has a high-quality Franka Panda model available.

Create a `SimulationBackend` interface so future backends can be added.

The first backend must implement:

- load robot;
- load world;
- load rigid and articulated assets;
- reset with seed;
- step at a fixed control and physics rate;
- apply joint/Cartesian/gripper actions;
- query link/joint/body state;
- contact events;
- raycasts or equivalent spatial queries;
- offscreen RGB rendering for policy observations;
- optional depth/segmentation if available;
- deterministic snapshot/restore where practical;
- video/trajectory recording;
- success/failure predicates.

### 7.3 Viewer

Preserve the repository’s current viewer if useful. Do not rewrite the whole UI simply to chase a graphics API.

If the current app has a native Vulkan or wgpu-based renderer, preserve and connect it to authoritative simulation state. If it is a browser renderer, describe it truthfully as a browser viewer; do not call WebGL/WebGPU “Vulkan.”

Use WebSocket or an equivalent streaming channel for transforms, run state, contacts, and preview frames.

---

## 8. Canonical OpenUSD plus backend-specific compilation

OpenUSD/UsdPhysics is the canonical scene and physics interchange representation, not the sole runtime engine.

Create an asset compiler that authors and validates:

- stage units;
- up axis and coordinate convention;
- visual prim hierarchy;
- rigid bodies;
- collision prims;
- physics materials;
- explicit mass, center of mass, and inertia;
- semantics;
- affordances;
- revolute and prismatic joints;
- limits and drives when known;
- articulation roots where appropriate;
- provenance and asset version metadata.

Then compile the same canonical `AssetManifest` and `PartGraph` into the active backend representation, such as MJCF.

Do not claim that MuJoCo loads arbitrary USD unless a tested converter/adapter in the repository actually proves it.

Keep source geometry immutable and derive versioned artifacts:

```text
asset/<asset_id>/<version>/
  evidence/
  source/
  generated/
  visual/
  collision/
  openusd/
  runtime/mujoco/
  validation/
  previews/
```

---

## 9. World templates and physically valid placement

The user may manually prepare reusable background/world templates. Implement a real world-template system rather than hard-coding one kitchen.

A `WorldTemplate` must describe:

- coordinate system and units;
- static geometry;
- collision geometry;
- semantic regions;
- support surfaces such as counters, tables, shelves, floors, and bins;
- containers and target volumes;
- robot spawn anchors;
- camera anchors;
- lighting/render settings;
- allowed object categories;
- navigation/manipulation clearance;
- source and license metadata.

### 9.1 Placement planner

Do not ask an LLM to guess an XYZ coordinate and trust it.

Implement deterministic geometric placement:

1. Resolve the requested semantic support surface.
2. Determine the candidate asset’s oriented bounding box and stable poses.
3. Sample or plan a pose inside the allowed support polygon.
4. Check robot reachability when relevant.
5. Check collision/penetration against the world and other objects.
6. Drop/settle the object under physics for a validation period.
7. Reject unstable or intersecting placements.
8. Store the accepted pose, seed, and placement evidence.

The platform agent may choose the semantic target and constraints; the geometry/physics planner determines the valid pose.

---

## 10. Bright Data discovery, Scraper Studio, and self-healing

Bright Data is the real-world evidence layer. Use current official Bright Data APIs and Scraper Studio behavior after checking the pinned/current documentation.

Secrets must be server-side, such as:

- `BRIGHTDATA_API_TOKEN`
- collector IDs and version IDs stored as metadata, not secrets.

### 10.1 Discovery and exact identity

For an exact product/object request:

1. Search manufacturer and exact model/SKU.
2. Find authoritative product/specification/manual pages.
3. Find front, side, rear, open, and detail imagery when relevant.
4. Use image/exact-match verification where supported.
5. OCR labels and model numbers.
6. Cluster images by identity.
7. reject mixed-SKU/model evidence.
8. Label category-level priors explicitly.

Source priority:

1. manufacturer page;
2. manufacturer manual/specification;
3. licensed official CAD/3D asset;
4. authorized retailer;
5. corroborating exact-model imagery;
6. category-level priors only for missing values.

Do not scrape random Google HTML when supported Bright Data search/image products exist.

### 10.2 Canonical extraction

Use explicit schemas. Do not implement a meaningless “scrape everything” endpoint.

Collect and normalize, when available:

- manufacturer;
- exact model/SKU/UPC/EAN;
- dimensions with units;
- mass;
- material;
- images and view labels;
- manuals;
- parts;
- joint/operation information;
- feature/state information;
- source URLs;
- retrieval time;
- collector ID/version;
- confidence;
- licensing/redistribution state.

Every physical estimate must include source, method, confidence, and an uncertainty range when inferred.

### 10.3 Semantic quality gate

HTTP 200 is not success.

Validate:

- required fields;
- types and units;
- plausible category bounds;
- exact identity match;
- image MIME and dimensions;
- no CAPTCHA/login/error-page content;
- no unresolved authoritative-source conflict;
- schema compatibility;
- content hashes;
- provenance.

### 10.4 Governed self-healing state machine

Bright Data’s self-healing draft is untrusted candidate code until validated.

Implement:

```text
COLLECTING
  -> QUALITY_PASSED
  -> QUALITY_FAILED
  -> REPAIR_REQUESTED
  -> DRAFT_READY
  -> GOLDEN_TESTING
  -> CANARY_TESTING
  -> AWAITING_POLICY_DECISION
  -> PROMOTED | REJECTED | ROLLED_BACK | EXHAUSTED
```

Requirements:

- precise repair prompt naming failing fields and examples;
- draft/version tracking;
- fixed golden URL or captured-snapshot suite;
- old/new schema and record diff;
- canary collection;
- last-known-good collector continuity;
- automatic promotion only when configured policy gates pass;
- internal approval UI when automatic promotion is disabled;
- rollback;
- full audit events;
- no Port dependency.

Build a controlled public/local test page with real product-shaped data whose layout can be changed intentionally. Use it to demonstrate an actual semantic break and repair flow rather than waiting for a third-party website to redesign itself.

---

## 11. Image understanding and DINOv3

Use the user’s local DINOv3 installation as an optional image-understanding component, not as magic articulation.

Potential uses:

- dense visual features;
- cross-view matching;
- duplicate/near-duplicate detection;
- exact-object consistency scoring;
- candidate part-region similarity;
- image-quality and viewpoint scoring;
- support for downstream segmentation heads when the configured checkpoint actually provides one.

Do not claim that a bare DINOv3 backbone directly produces perfect semantic masks or joints. Wrap it behind an adapter with explicit capabilities and tests.

Keep image segmentation/foreground removal swappable. Record exact model/weight revision and preprocessing settings.

---

## 12. TRELLIS.2 worker

Use the local official Microsoft TRELLIS.2 repository/checkpoint when configured. Do not redownload it without permission.

The worker must:

- run in an isolated compatible environment;
- validate Linux/WSL, CUDA, package, and VRAM requirements;
- expose a typed job API or CLI wrapper;
- accept a versioned preprocessed image artifact;
- record seed, resolution, model revision, code revision, parameters, hardware, runtime, and logs;
- produce immutable GLB and preview artifacts;
- preserve PBR material outputs;
- surface real OOM/dependency errors;
- support cancellation and retry where safe;
- never return a bundled demo mesh as if it were generated.

If the machine cannot execute TRELLIS.2, implement and test the adapter against a previously generated real artifact, mark live inference `BLOCKED_BY_HARDWARE`, and continue with compiler work. Do not call live generation complete.

---

## 13. Rigid asset compiler

The rigid pipeline is:

```text
verified evidence
  -> image selection and preprocessing
  -> exact CAD/3D asset if available, otherwise TRELLIS.2
  -> immutable raw GLB
  -> geometry QA
  -> orientation and units
  -> dimension/aspect validation
  -> visual optimization
  -> separate collision generation
  -> mass/COM/inertia/material authoring
  -> semantics and affordances
  -> OpenUSD authoring
  -> active-backend compilation
  -> static and physics validation
  -> deterministic robot oracle
  -> candidate promotion
```

Required geometry checks:

- empty mesh;
- NaN/Inf vertices;
- degenerate and duplicate faces;
- disconnected components;
- invalid normals/tangents/UV/material references;
- extreme triangle count;
- bounding box and aspect ratio;
- coordinate orientation;
- open/non-manifold topology report;
- visual and collision hashes.

Use uniform scale by default. Do not blindly stretch each axis to force exact dimensions when that destroys shape. Reject, regenerate, or use a controlled deformation path and revalidate.

Dynamic collision geometry must use primitives, convex hulls, convex decomposition, or another backend-supported stable approximation. Do not use the high-detail visual triangle mesh as the default dynamic collider.

Mass properties must be explicit. Use exact evidence when available and clearly marked material priors otherwise. Uncertain friction, restitution, or resistance should be ranges used for domain variation, not fake exact values.

Physics smoke tests:

- loads without parser errors;
- correct units;
- valid mass and inertia;
- no initial severe penetration;
- drop and settle;
- no NaNs or energy explosion;
- expected contact response;
- stable reset under fixed seed;
- gripper can reach intended grasp surfaces.

---

## 14. Articulated and multi-part assets

TRELLIS.2 produces visual geometry, not trustworthy joints.

Create a swappable `ArticulationAdapter` interface. Evaluate and integrate current license-compatible open-source candidates such as SIMART first, with PAct or other research systems as optional adapters. Record each adapter’s:

- license;
- checkpoint source;
- hardware requirement;
- coordinate convention;
- input/output formats;
- known limitations;
- confidence;
- failure modes.

Do not auto-download a model or Blender binary from untrusted code without review and explicit configuration.

The articulated pipeline is:

```text
exact evidence + generated/static mesh
  -> orientation normalization
  -> part candidates / decomposition
  -> semantic part graph
  -> kinematic joint hypotheses
  -> manual/image/spec cross-validation
  -> rigid link meshes
  -> joint origins, axes, limits, drives/resistance
  -> collision filtering
  -> affordances and grasp frames
  -> OpenUSD + backend runtime compilation
  -> joint sweep and stability tests
  -> deterministic robot interaction test
```

Canonical `PartGraph` requirements:

- root part;
- part IDs and semantic labels;
- parent-child hierarchy;
- visual and collision artifacts per part;
- joint type;
- parent and child;
- axis and origin;
- limits;
- confidence and evidence;
- affordances;
- candidate grasp frames;
- review/rejection reason.

Examples:

- cabinet body + door + handle with a revolute hinge;
- drawer body + drawer + handle with a prismatic joint;
- blender base + removable cup + lid + cup handle;
- refrigerator body + doors + handles + drawers.

A handle must move with the correct parent part. A visually rotating monolithic mesh is not articulation.

First articulated acceptance target: one real cabinet or drawer with one moving link. Do not begin a full refrigerator until the one-joint pipeline is physically validated.

---

## 15. Franka deterministic oracle

A deterministic controller is required even though the final learned policy is VLA-JEPA. It is a validation oracle, not the product.

Implement:

- differential IK or another proven Cartesian controller;
- joint and Cartesian safety limits;
- gripper open/close control;
- collision-aware approach waypoints;
- reachability checks;
- fixed control rate;
- deterministic reset;
- task state machine;
- real success and failure predicates.

Pick/place oracle phases:

1. pre-grasp;
2. grasp approach;
3. close gripper;
4. verify contact and lift;
5. transport;
6. place/release;
7. verify final containment/pose.

Open-articulation oracle phases:

1. pre-grasp handle;
2. grasp;
3. confirm contact;
4. follow constrained arc/line;
5. verify actual joint displacement;
6. release.

The oracle must distinguish an invalid world/asset from a learned-policy failure.

---

## 16. LeRobot VLA-JEPA integration

Use the user’s local LeRobot/VLA-JEPA code and checkpoint when configured. Read the exact installed code and official documentation before adapting it.

VLA-JEPA must connect to the Franka embodiment through explicit contracts. Do not pass arbitrary raw joint values merely because the checkpoint has a seven-dimensional action head.

Recommended first action contract:

```text
[dx, dy, dz, droll, dpitch, dyaw, gripper_command]
```

A controller/IK adapter converts the Cartesian delta action into valid Franka actuator commands.

Recommended observation contract:

- static/front RGB view;
- wrist RGB view;
- stable camera ordering;
- end-effector pose;
- gripper state/width;
- required joint state if the configured policy expects it;
- language instruction;
- timestamps;
- episode and step IDs;
- normalization revision.

Requirements:

- inspect checkpoint config for exact view count, resolution, action chunk, state/action dimensions, and normalization;
- implement encode/decode round-trip tests;
- enforce action bounds and frame convention;
- map gripper convention explicitly;
- do not accidentally apply dataset-specific gripper binarization;
- reinitialize embodiment-specific state/action modules when the checkpoint/config requires it;
- preserve compatible vision/language weights;
- keep inference and training environments isolated;
- record every policy revision and checkpoint hash;
- report oracle and VLA results separately.

The V-JEPA world-model component may be training-only depending on the exact implementation. Do not invent an inference dependency if the installed implementation does not use it at inference.

---

## 17. Demonstration collection, fine-tuning, and policy promotion

Training is optional evidence of value, not a substitute for valid assets.

Create LeRobot-compatible episodes from:

- successful deterministic-oracle trajectories;
- teleoperation, if implemented;
- approved prior demonstrations;
- validated scenario variations.

Each episode must link:

- synchronized observations;
- actions;
- robot state;
- instruction;
- asset/world/scenario revisions;
- seed;
- success/failure and phase labels;
- provenance.

A training run must record:

- input dataset revision;
- base checkpoint;
- configuration;
- code revision;
- hardware;
- metrics;
- output checkpoint hash;
- held-out evaluation.

Never continuously overwrite the active checkpoint. New checkpoints are candidates. Promote only after configured held-out regression and safety gates pass. Preserve rollback.

---

## 18. Autonomous platform agent

The platform agent is not just a chatbot. It must be able to inspect and control the system through typed tools.

### 18.1 Autonomy modes

Implement modes such as:

- `OBSERVE_ONLY`
- `PLAN_ONLY`
- `EXECUTE_WITH_APPROVAL`
- `AUTONOMOUS_WITH_BUDGETS`

Budgets and policies include:

- maximum worlds;
- maximum scrape requests;
- maximum GPU minutes;
- maximum evaluation episodes;
- maximum retries;
- allowed domains;
- allowed model/worker actions;
- scraper auto-promotion policy;
- policy-training permission;
- stop/kill switch.

### 18.2 Agent context

The agent must be able to retrieve structured context for:

- current models and robots;
- world and asset graph;
- run state;
- task predicate;
- camera snapshots;
- contacts and trajectories;
- console/log errors;
- database evaluation events;
- OpenTelemetry trace IDs;
- SigNoz aggregates when configured;
- existing coverage;
- prior attempts;
- artifact validation reports;
- available tools and permissions.

Do not put entire 20 GB models, videos, or huge logs into the prompt. Use IDs, summaries, bounded samples, and retrieval tools.

### 18.3 Agent skills

Create versioned skills/system instructions for at least:

- diagnose robot failure;
- choose next scenario;
- request exact object evidence;
- validate evidence identity;
- plan rigid vs articulated route;
- interpret asset validation;
- place an object safely;
- decide retry vs regenerate vs repair;
- inspect scraper failure;
- request self-heal;
- compare candidate vs active asset/policy;
- stop when no useful progress remains.

Scraped content is untrusted data and must never override system/tool instructions.

### 18.4 Failure classification

Use structured signals before free-form reasoning. At minimum classify:

- asset load error;
- invalid scale;
- invalid collider;
- initial penetration;
- physics instability;
- invalid joint;
- unreachable target;
- pre-grasp collision;
- perception/localization failure;
- grasp miss;
- grasp slip;
- object dropped;
- wrong part;
- joint resistance/control failure;
- policy timeout;
- invalid action;
- policy instability;
- success predicate failure;
- scraper/evidence failure;
- generator failure;
- worker crash.

The agent may explain and plan after the classifier produces evidence.

---

## 19. Failure-driven curriculum and next-world selection

Do not hard-code “apple then banana” as the only progression. Implement a measurable coverage model.

For pick/place, track dimensions such as:

- object size and aspect ratio;
- mass;
- friction;
- grasp width;
- shape family;
- material/appearance;
- pose and orientation;
- clutter;
- target location;
- camera/lighting variation.

For articulated tasks, track:

- hinge side;
- joint type;
- handle orientation, size, and height;
- joint limit;
- resistance;
- door/drawer dimensions and mass;
- clearance;
- texture/lighting.

Next-world planning must:

- target repeated measured failures;
- cover underrepresented bins;
- avoid duplicate asset/scenario fingerprints;
- reuse valid existing assets before scraping/generating new ones;
- estimate expected information value;
- respect budgets;
- run the deterministic oracle before VLA evaluation;
- stop after target success, budget exhaustion, or no statistically meaningful improvement.

---

## 20. OpenTelemetry and self-hosted SigNoz

Instrument the system now even if SigNoz is enabled later.

Use one root trace per pipeline/evaluation/curriculum iteration, with correlated IDs such as:

```text
robotworld.run
  request.validate
  agent.plan
  evidence.discover
  scraper.collect
  evidence.identity_resolve
  evidence.normalize
  asset.generate
  asset.articulation_infer
  asset.compile
  asset.validate
  world.place
  simulation.reset
  robot.oracle_evaluate
  robot.vla_evaluate
  failure.classify
  curriculum.plan_next
  training.run
  candidate.promote_or_reject
```

Record bounded metrics such as:

- scraper success and field completeness;
- identity confidence;
- repair count;
- generation duration;
- asset validation errors;
- scale error;
- collider complexity;
- simulation step time/FPS;
- physics crashes;
- oracle success;
- VLA success;
- failure counts by code;
- workflow retry count;
- coverage score;
- training duration and evaluation result.

Rules:

- critical state remains in the application database;
- export traces, metrics, and logs through OTLP when configured;
- self-hosted SigNoz uses configurable local OTLP endpoints;
- do not require a cloud ingestion key;
- do not send images, full prompts, manuals, GLBs, secrets, or unbounded attributes as telemetry;
- expose a server-side read-only telemetry query tool for the agent;
- prefer the official self-hosted SigNoz MCP/API supported by the pinned version;
- degrade gracefully when SigNoz is unavailable while clearly showing observability status;
- no Port spans or dependencies.

If installing self-hosted SigNoz later, follow current official Community/self-host instructions rather than old deprecated deployment files. Pin the version and document Windows/WSL requirements. Do not alter the user’s Docker environment destructively.

---

## 21. Required UI

Keep and improve the existing design instead of replacing it with a generic admin template.

All UI data must come from real backend APIs. Required pages or equivalent sections:

1. **Overview** — real system health, configured workers, current run, and blockers.
2. **Models** — add local/remote model, validate, load/unload, capabilities, revision, health.
3. **Robots & Embodiments** — import robot, inspect kinematics/sensors/controllers, load Franka default.
4. **Skills/Tasks** — task definitions, instructions, predicates, policy/robot binding.
5. **Assets** — evidence, generated source, visual/collision geometry, dimensions, mass, part graph, validation, versions.
6. **Worlds** — templates, support surfaces, object instances, placement, robot spawn, scene graph.
7. **Simulation/Evaluation** — live/recorded camera views, world view, trajectory, contacts, joints, actions, success/failure.
8. **Failure Analysis** — actual failure categories, oracle-vs-VLA comparison, prior attempts, recommended next world.
9. **Evidence** — identity, sources, conflicts, confidence, manuals/images, licenses.
10. **Scraper Repair** — collector version, failure, proposed repair, golden/canary results, promote/reject/rollback.
11. **Training/Policies** — datasets, runs, checkpoints, held-out evaluation, candidate/active/rollback.
12. **Observability** — local telemetry status, trace links/queries, SigNoz self-host status when enabled.
13. **Agent Control** — instruction, autonomy mode, budgets, plan, tool calls, approvals, kill switch.
14. **Settings** — model paths, worker environments, API secret references, path mappings, physics and telemetry endpoints.

Add a persistent visible distinction between:

- frontend preview;
- authoritative physics simulation;
- recorded result;
- mock/fixture mode used only in tests.

Production UI must never show fixture data as live data.

---

## 22. Canonical contracts

Implement versioned Pydantic/JSON Schema or equivalent contracts for at least:

- `ModelRegistration`
- `ModelCapability`
- `RobotDefinition`
- `LinkSpec`
- `JointSpec`
- `SensorSpec`
- `EmbodimentContract`
- `ObjectRequest`
- `EvidenceRecord`
- `EvidenceBundle`
- `PropertyEstimate`
- `ObjectIdentity`
- `AssetManifest`
- `PartGraph`
- `Affordance`
- `WorldTemplate`
- `WorldInstance`
- `PlacementRequest`
- `ScenarioSpec`
- `TaskDefinition`
- `PolicyRevision`
- `EvaluationResult`
- `FailureEvent`
- `CoverageState`
- `ScraperCollectorVersion`
- `ScraperRepairRun`
- `PipelineRun`
- `ArtifactReference`
- `AgentToolCall`
- `ApprovalDecision`

Every mutable entity needs IDs, revisions, timestamps, creator/source, hashes, lifecycle state, and validation errors.

---

## 23. Required lifecycle state machines

### Model

```text
REGISTERED -> VALIDATING -> AVAILABLE -> LOADED
                         -> INVALID
LOADED -> UNLOADING -> AVAILABLE
```

### Robot

```text
IMPORTED -> PARSED -> KINEMATICS_VALIDATED -> PHYSICS_VALIDATED -> AVAILABLE
        -> REJECTED
```

### Evidence and asset

```text
REQUESTED
 -> DISCOVERING
 -> IDENTITY_VALIDATED
 -> GENERATING_OR_IMPORTING
 -> COMPILED
 -> STATIC_VALIDATED
 -> PHYSICS_VALIDATED
 -> ORACLE_VALIDATED
 -> ACTIVE
```

with explicit `REJECTED`, `SUPERSEDED`, and `ROLLED_BACK` paths.

### Evaluation

```text
QUEUED -> STARTING -> RUNNING -> SUCCEEDED | FAILED | CANCELLED | CRASHED
```

### Curriculum

```text
EVALUATE
 -> CLASSIFY
 -> QUERY_COVERAGE
 -> PLAN_NEXT
 -> BUILD_OR_REUSE_SCENARIO
 -> ORACLE_VALIDATE
 -> VLA_EVALUATE
 -> UPDATE_COVERAGE
 -> TRAIN_OPTIONALLY
 -> STOP_OR_REPEAT
```

### Scraper repair

Use the state machine in section 10.4.

Transitions must be persisted, validated, idempotent, and tested.

---

## 24. Security and correctness

Implement and test:

- server-side secret storage/environment references;
- no secrets in React bundles;
- local-path allowlists and path traversal protection;
- safe Windows/WSL path mapping;
- SSRF protection;
- URL/domain policy;
- MIME and magic-byte validation;
- download size/time limits;
- archive bomb and path traversal defense;
- sanitized filenames;
- sandboxed processing of untrusted media/meshes;
- prompt-injection isolation for scraped content;
- no execution of downloaded scripts/macros;
- signed webhooks where supported;
- role/autonomy policy for agent tools;
- cancellation/kill switch;
- immutable audit trail;
- dependency and secret scanning;
- source/license/redistribution metadata;
- no arbitrary shell access exposed to the platform agent.

Do not automatically execute code emitted by a website or scraper repair model outside the intended isolated provider/runtime.

---

## 25. Test strategy

### Unit tests

- schemas and migrations;
- units and conversions;
- identity scoring and mixed-SKU rejection;
- content hashing;
- lifecycle transition guards;
- path mapping and allowlists;
- image ranking/dedup;
- mesh QA;
- mass/inertia calculations;
- placement geometry;
- action/state encode/decode;
- task predicates;
- failure classification;
- agent tool authorization.

### Contract tests

- frontend/backend API contracts;
- worker messages;
- artifact metadata;
- model and robot adapters;
- Bright Data client;
- self-hosted SigNoz/OTel configuration;
- schema compatibility;
- idempotency and error envelopes.

### Integration tests

- database plus artifact store;
- resumable workflow after restart;
- real or recorded Bright Data result normalization;
- controlled scraper schema/layout break;
- repair candidate, golden, canary, promotion, rollback;
- local model-path detection;
- Franka model load;
- simulation reset/step/contact/render;
- frontend command execution.

### GPU/model tests

- real TRELLIS.2 inference when hardware is available;
- compiler imports real TRELLIS output;
- real DINOv3 adapter health check;
- real VLA-JEPA checkpoint load;
- correctly shaped bounded VLA actions;
- no hidden mock fallback.

### Physics tests

- known-good box drop;
- friction/slide comparison;
- restitution/bounce comparison;
- Franka self-collision sanity;
- gripper open/close;
- grasp/lift known-good object;
- generated rigid asset load and settle;
- articulated joint sweep;
- deterministic seed repeatability;
- object does not hover after release.

### End-to-end gates

#### E2E 1 — known-good Franka

Load the default Franka, two cameras, a known-good object, and run deterministic pick/place repeatedly.

#### E2E 2 — user model and robot registration

Register a local model path and the Franka embodiment from the UI/API, validate, load, and expose them to the agent tool registry.

#### E2E 3 — VLA-JEPA

Run VLA-JEPA on the same validated scenario and record bounded actions and real evaluation results separately from the oracle.

#### E2E 4 — exact rigid web object

Bright Data evidence -> identity -> TRELLIS.2 or exact 3D source -> asset compiler -> OpenUSD/MJCF -> placement -> deterministic Franka test -> VLA test.

#### E2E 5 — scraper self-heal

Controlled page layout change -> semantic quality failure -> self-heal candidate -> golden/canary -> promotion -> evidence rebuild -> asset revalidation -> robot re-test, while last-known-good remains available.

#### E2E 6 — autonomous next world

A measured VLA failure or coverage gap causes the agent to select/reuse/build a targeted next scenario without the user manually clicking every step.

#### E2E 7 — articulated cabinet/drawer

Real evidence/static or generated mesh -> part decomposition -> part graph -> joint compilation -> joint sweep -> Franka deterministic open task -> VLA evaluation.

---

## 26. Implementation order

Do not jump to the refrigerator first. Work through these gates and continue coding after each successful gate.

### Phase 0 — repository truth and repair

- inspect everything;
- run current app/tests;
- fix build-breaking and runtime-blocking defects;
- establish progress/status file;
- remove fake data from production paths;
- add missing basic tests;
- preserve current UI where useful.

**Gate:** current repository starts reproducibly and its real/missing features are known.

### Phase 1 — shared contracts, internal catalog, durable runs

- model/robot/asset/world/run schemas;
- database migrations;
- artifact store;
- persisted state machines;
- typed command/query API;
- agent tool registry foundation.

**Gate:** commands survive restart and report real state.

### Phase 2 — Models and Robots pages plus Franka

- local/remote model registry;
- path validation;
- robot registry/import;
- Franka Panda model and gripper;
- front/wrist cameras;
- settings/path mappings.

**Gate:** user can register, validate, and load a model and Franka from the software.

### Phase 3 — authoritative physics baseline

- MuJoCo or proven existing backend;
- world template;
- fixed timestep;
- contacts/cameras;
- deterministic controller;
- real task predicates;
- frontend state streaming.

**Gate:** known-good Franka pick/place works repeatedly and released objects obey physics.

### Phase 4 — VLA-JEPA bridge

- explicit observation/action contract;
- local checkpoint load;
- policy worker;
- IK/action adapter;
- run/evaluation UI;
- oracle-vs-VLA comparison.

**Gate:** VLA-JEPA controls the simulated Franka with valid bounded actions on the known-good world.

### Phase 5 — Bright Data evidence and exact identity

- discovery;
- Scraper Studio collector client;
- normalization;
- evidence/provenance;
- identity resolver;
- quality gates;
- Evidence UI.

**Gate:** one exact product produces a reproducible, identity-consistent evidence bundle.

### Phase 6 — TRELLIS.2 and rigid asset compiler

- local TRELLIS worker;
- image selection/preprocessing;
- immutable GLB;
- geometry QA;
- scale/collision/mass/inertia;
- OpenUSD plus MJCF compilation;
- validators;
- Asset UI.

**Gate:** a generated real product asset loads, settles, and passes deterministic robot validation without hand replacement.

### Phase 7 — world placement and autonomous control

- semantic world surfaces;
- stable placement planner;
- complete agent tools;
- autonomy modes/budgets;
- failure diagnosis;
- coverage and next-world planning.

**Gate:** the agent can run a task, diagnose a gap, place or request the missing asset, and re-run it.

### Phase 8 — Bright Data self-healing

- semantic failure detector;
- repair state machine;
- golden/canary;
- internal policy decision;
- rollback;
- Repair UI;
- controlled layout-change demo.

**Gate:** real break -> repaired candidate -> validated promotion -> asset/robot re-test.

### Phase 9 — training and policy lifecycle

- LeRobot dataset recording;
- fine-tuning adapter;
- candidate checkpoints;
- held-out evaluation;
- promotion/rollback;
- no continuous overwrite.

**Gate:** one measured targeted dataset/policy update is versioned and evaluated honestly.

### Phase 10 — articulated asset

- articulation adapter;
- SIMART or best validated current adapter;
- part graph and joint compiler;
- cabinet/drawer;
- handle affordance;
- joint sweep;
- deterministic and VLA opening tests.

**Gate:** real physics-driven one-joint interaction succeeds; no visual-only rotation.

### Phase 11 — self-hosted SigNoz enablement

- connect existing local instance or add current official self-host setup instructions/config;
- OTLP validation;
- traces/metrics/logs visible;
- server-side query/MCP adapter;
- agent failure aggregation from SigNoz;
- graceful offline behavior.

**Gate:** the agent can query structured telemetry from the self-hosted open-source SigNoz instance, and the system remains correct when SigNoz is unavailable.

### Phase 12 — generic embodiment hardening

- import another robot/full body;
- multiple end-effectors/sensors;
- embodiment adapter validation;
- generic task bindings;
- regression tests preserving Franka.

**Gate:** architecture is demonstrably not hard-coded only to Franka, while Franka remains the fully validated default.

---

## 27. Quantitative acceptance principles

Use explicit configured thresholds rather than invented scores.

Examples:

- exact identity confidence threshold for exact-SKU builds;
- required evidence-field completeness threshold;
- dimension/aspect residual threshold;
- no NaN/Inf/empty geometry;
- valid mass and inertia;
- no severe initial penetration;
- stable settle window;
- bounded action range;
- fixed sample count for oracle and VLA evaluation;
- reported confidence interval where sample size allows;
- no hidden retries until one run succeeds;
- golden scraper tests all pass;
- no unapproved schema change;
- rollback tested.

Choose values appropriate to each category and store them in configuration. Every result must show sample count, seed distribution, asset/world/policy revision, and failure categories.

---

## 28. Things you must not do

- Do not implement Port now.
- Do not use SigNoz Cloud.
- Do not require a cloud ingestion key for self-hosted SigNoz.
- Do not install Isaac Sim.
- Do not call a frontend animation a physics simulation.
- Do not rewrite the entire repository before understanding it.
- Do not spend the whole run producing documents only.
- Do not fabricate repository findings.
- Do not fabricate success percentages.
- Do not use fake progress timers.
- Do not return success after partial failure.
- Do not silently fall back to demo assets or random actions.
- Do not hard-code only apple/banana or only one kitchen.
- Do not treat a GLB as a controllable robot.
- Do not treat a TRELLIS mesh as articulated.
- Do not use visual triangle meshes as default dynamic collision meshes.
- Do not mix evidence from different models/SKUs.
- Do not trust scraped content as agent instructions.
- Do not expose model, Bright Data, OpenAI, or telemetry secrets to React.
- Do not auto-download duplicate large checkpoints.
- Do not train VLA on an invalid scenario.
- Do not promote scraper, asset, scenario, or policy candidates without gates and rollback.
- Do not hide unavailable hardware/credentials; mark the exact blocked live test and keep implementing other testable work.
- Do not delete unrelated code or user files.

---

## 29. Completion behavior for this Codex run

Begin now.

1. Inspect the repository and run the current system.
2. Update `docs/CODEX_EXECUTION_STATE.md` with evidence.
3. Fix immediate blockers.
4. Implement the earliest incomplete phase whose dependencies are satisfied.
5. Run tests and a real executable demonstration for that phase.
6. Update status with exact files, commands, outputs, and remaining blockers.
7. Continue into the next phase instead of stopping after a plan.
8. Keep patches scoped and reversible.
9. When a local model path, API key, or optional service is unavailable, finish the adapter, validation, UI, tests, and exact run command; mark only the live call blocked rather than abandoning the entire phase.
10. At the end, provide:
    - what was genuinely built;
    - what was run and passed;
    - what failed and why;
    - exact next command/task;
    - no claim that unexecuted GPU/API behavior works.

The first non-negotiable executable milestone is:

```text
Models page + Robots page
  -> load the configured platform model
  -> load a real Franka Panda with gripper
  -> load a reusable world template
  -> run authoritative physics
  -> front/wrist observations
  -> deterministic pick/place oracle
  -> VLA-JEPA action bridge
  -> real evaluation and failure evidence
```

Then complete:

```text
Bright Data exact evidence
  -> TRELLIS.2 real geometry
  -> RobotWorld asset compiler
  -> OpenUSD + runtime asset
  -> valid placement
  -> Franka oracle
  -> VLA evaluation
  -> autonomous diagnosis and next-world loop
  -> governed scraper repair
  -> articulated cabinet/drawer
  -> self-hosted open-source SigNoz query integration
```

Build the system. Do not merely describe it.
