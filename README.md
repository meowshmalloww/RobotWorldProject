# RobotWorld

RobotWorld is a local-first desktop system for compiling provenance-tagged articulated assets, validating them in MuJoCo, evaluating a real remote vision-language-action policy, and using measured failures to expand a curriculum.

This repository contains the complete React/Electron client and FastAPI service. The application does not depend on Fireworks AI. Model-assisted planning is optional and uses an OpenAI-compatible endpoint; without one, RobotWorld continues with a deterministic planner derived from persisted evaluation data.

## What is real in this build

- MuJoCo contact-physics rollouts and 20 Hz WebSocket telemetry.
- Two persisted acceptance-scenario builders with fresh randomized manifests, real MuJoCo compile/stability gates, artifact hashes, and fail-closed learned-policy gates. Training is disabled.
- Parametric GLB geometry, MJCF validation, and schema-checked OpenUSD output through `usd-core`.
- SQLite persistence for assets, scenes, scenarios, evaluations, jobs, telemetry, and settings.
- Keyless OTLP export to self-hosted SigNoz Community plus a durable local telemetry mirror.
- Bright Data REST collection with bounded retries and provenance retention.
- Custom Scraper Studio collection, required-field validation, and human-approved self-heal/rerun jobs.
- A separate, fail-closed learned-policy gate using MuJoCo front/wrist RGB, 5-D proprioception, language, and bounded action chunks.
- A real Microsoft TRELLIS.2 4B gateway; generated PBR geometry is validated before the separate physical compiler runs.
- Electron packaging with an owned FastAPI sidecar and per-user writable data directory.
- A native `pygfx → wgpu-native → Vulkan` viewport. The React client displays PNG frames and creates no browser WebGL/WebGPU 3D context.

The current simulator is MuJoCo. The physical compiler is parametric; the optional TRELLIS.2 path replaces its visual mesh only and still uses validated structured data for articulation, collision, mass, and joint physics. The UI does not claim Isaac Sim or PhysX execution. External integrations remain unconfigured until valid credentials/endpoints are supplied, and missing integrations never produce synthetic source records or model actions.

## Architecture

```text
React/Electron editor + native Vulkan frame client
        │ HTTP / WebSocket on loopback
        ▼
FastAPI application
  ├─ SQLite catalog and local telemetry
  ├─ MuJoCo asset and acceptance-world validator
  ├─ MuJoCo RGB policy evaluator ──► user-selected VLA gateway
  ├─ Parametric or TRELLIS.2 GLB ──► MJCF + OpenUSD compiler
  ├─ OpenAI-compatible planner (optional)
  ├─ Bright Data collector (optional)
  ├─ self-hosted SigNoz Community OTLP + Query API (optional)
  └─ Port client retained but outside the current acceptance path
```

Electron starts the API sidecar, waits for `/health`, and then loads the UI from the API's loopback origin. This avoids the development-only Vite proxy and `file://` differences that caused the former 502 failures.

## Development on Windows

Requirements: Python 3.11, Node.js 20 or newer, and npm.

```powershell
cd D:\RobotWorldProject\backend
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

cd ..\frontend
npm ci
npm run dev:electron
```

`dev:electron` starts Vite; Electron starts the backend automatically when port 8000 is free. For browser-only development, run `npm run dev:api` and `npm run dev` in separate terminals, then open `http://127.0.0.1:5173`.

## Verification

```powershell
cd D:\RobotWorldProject\backend
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q

cd ..\frontend
npm run typecheck
npm run lint
npm run build
npm audit --omit=dev
```

The backend suite exercises every primary read route, settings and secret persistence, world mutations, a real GLB/OpenUSD compile, and a live MuJoCo WebSocket session.

## Desktop installer

```powershell
cd D:\RobotWorldProject\frontend
npm run dist
```

This builds the web client, freezes the Python API into an onedir sidecar with PyInstaller, and creates an NSIS installer under `frontend/release`. Runtime state is stored under Electron's per-user application data directory rather than inside the installed bundle.

## Optional language-model provider

In Settings → Models, set any OpenAI-compatible `/v1` endpoint and the exact model ID served by it.

- OpenAI: `https://api.openai.com/v1` plus an API key.
- Ollama on the same machine: `http://127.0.0.1:11434/v1`; no key is required.
- vLLM or llama.cpp: use its `/v1` base URL and the authentication required by that server.

Permanent provider failures such as billing suspension, 401, 402, or 403 open a circuit until the configuration changes. Jobs fall back to the data-derived planner instead of cascading into HTTP 502 responses.

Language-model fallback applies only to high-level planning. It is prohibited in learned robot-policy evaluation.

## Real VLA and TRELLIS.2 model services

GPU inference is isolated from the Windows desktop application. Use the production gateways in [`model_services`](./model_services/README.md), store gateway tokens through Settings → API Keys, then save and verify each contract under Settings → Models.

No VLA is selected by default. Choose the checkpoint first, then implement its exact observation/action adapter and pin its revision, model hash, normalization hash, and frozen environment hash. A policy episode receives only declared camera frames, proprioception, and language. It never receives evaluator-only success state, the oracle planner, or scripted fallback actions.

The TRELLIS.2 gateway runs Microsoft's published 4B image-to-3D pipeline and returns a real PBR GLB. The desktop validates the binary, then compiles physical proxies and articulation independently. Configure both services only after their live endpoint, pinned revision, hashes, and tokens are available.

## External integration setup

- Bright Data: create a **custom Scraper Studio collector** first and retain its non-secret `c_*` ID. Register that ID on Sources and run it until the required-field validator passes. SERP Full JSON is supplementary discovery; Web Unlocker is supplementary page retrieval. Store the Bearer API key under API Keys—never in chat or source control.
- SigNoz Community: follow [`ops/signoz/README.md`](./ops/signoz/README.md). On Windows, use a normal WSL2 Ubuntu distribution with native Docker Engine. RobotWorld exports keyless OTLP/HTTP to `http://127.0.0.1:4318`; programmatic v5 queries use `http://127.0.0.1:8080` plus a local service-account key.
- Port is intentionally outside the current acceptance path.

Secrets are write-only through the API: reads return masked values, and saving a settings section preserves the original secret. For a deployed machine, prefer environment injection or an OS-managed secret store and restrict the per-user RobotWorld data directory to that account.

## Acceptance gates and truthful status

- **Asset validation:** available now. It is real MuJoCo contact physics driven by a privileged scripted oracle and is labelled as such.
- **Policy protocol and safety:** available now and covered by contract tests. It renders real MuJoCo cameras, validates model/checkpoint/embodiment metadata, rejects unsafe output, and has no fallback.
- **Live VLA pass:** pending a reachable fine-tuned checkpoint. RobotWorld will not report a VLA pass before closed-loop episodes run.
- **Kitchen and logistics acceptance buttons:** real randomized manifests, Vulkan frames, MJCF artifacts, and physics stability gates pass now. Robot task success remains `null`/blocked until the user-selected VLA and task adapter execute the state-based predicates.
- **Training:** intentionally disabled. This workstation does not create or claim a trained policy.
- **TRELLIS.2 generation:** gateway and validation path are available; a live generation pass is pending its Linux GPU endpoint.
- **Bright Data SERP:** a real paid Google request passed with parsed organic rows and the official manufacturer domain. The UI exposes a clearly billable one-request probe.
- **Bright Data Scraper Studio/self-heal:** correctly wired, but pending a custom `c_*` collector ID and explicit approval of the billable repair.
- **SigNoz Community evidence:** integration and pinned Foundry configuration are complete; live telemetry evidence is pending installation of WSL2 Ubuntu/native Docker and the local stack.

See [`Doc/PRODUCTION_ACCEPTANCE.md`](./Doc/PRODUCTION_ACCEPTANCE.md) for the exact real-world release matrix.
Use [`Doc/REAL_SYSTEM_TEST_RUNBOOK.md`](./Doc/REAL_SYSTEM_TEST_RUNBOOK.md) for the exact Windows, Bright Data, SigNoz, VLA-selection, and TRELLIS.2 test order.

## Data and API

Development data defaults to `backend/data`. Set `ROBOTWORLD_DATA_DIR` to relocate the SQLite database, generated assets, demos, and model checkpoints. The health endpoints are `GET /health` and `GET /api/health`; interactive API documentation is available at `/docs` while the service is running.

## Project layout

```text
backend/
  app/main.py              FastAPI contract and WebSockets
  app/services/            simulation, Vulkan rendering, compilation, providers
  tests/                   production-path integration tests
  robotworld-api.spec      PyInstaller sidecar build
frontend/
  electron/                secure desktop host and API ownership
  src/                     React client, 3D viewport, pages, design system
Doc/                       product and hackathon research notes
model_services/            optional model gateways; no VLA is selected by default
ops/signoz/                pinned self-hosted Community deployment
```
