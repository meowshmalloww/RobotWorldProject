# RobotWorld

RobotWorld is a local-first desktop system for compiling provenance-tagged articulated assets, evaluating a robot controller in MuJoCo, training a behavior-cloning policy, and using measured failures to expand a curriculum.

This repository contains the complete React/Electron client and FastAPI service. The application does not depend on Fireworks AI. Model-assisted planning is optional and uses an OpenAI-compatible endpoint; without one, RobotWorld continues with a deterministic planner derived from persisted evaluation data.

## What is real in this build

- MuJoCo contact-physics rollouts and 20 Hz WebSocket telemetry.
- PyTorch behavior-cloning training and saved checkpoints.
- Parametric GLB geometry, MJCF validation, and schema-checked OpenUSD output through `usd-core`.
- SQLite persistence for assets, scenes, scenarios, evaluations, jobs, telemetry, and settings.
- OpenTelemetry export to SigNoz plus a durable local telemetry mirror.
- Bright Data REST collection with bounded retries and provenance retention.
- Port entity upserts with client-credential token exchange and expiry-aware caching.
- Electron packaging with an owned FastAPI sidecar and per-user writable data directory.

The current simulator is MuJoCo and the current asset compiler is parametric. The UI does not claim Isaac Sim, PhysX, TRELLIS, or Stable Fast 3D execution. External integrations are shown as unconfigured until valid credentials are supplied, and missing integrations never produce synthetic source records.

## Architecture

```text
React + Three.js client
        │ HTTP / WebSocket on loopback
        ▼
FastAPI application
  ├─ SQLite catalog and local telemetry
  ├─ MuJoCo evaluator ──► PyTorch trainer
  ├─ GLB + MJCF + OpenUSD compiler
  ├─ OpenAI-compatible planner (optional)
  ├─ Bright Data collector (optional)
  ├─ SigNoz OTLP + Query API (optional)
  └─ Port catalog sync (optional)
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

## Model provider and DGX Spark

In Settings → Models, set any OpenAI-compatible `/v1` endpoint and the exact model ID served by it.

- OpenAI: `https://api.openai.com/v1` plus an API key.
- Ollama on the same machine: `http://127.0.0.1:11434/v1`; no key is required.
- vLLM or llama.cpp: use its `/v1` base URL. For a DGX Spark on the LAN, use the Spark's address and enter the server's key (or a non-empty local placeholder if that server ignores authentication).

Permanent provider failures such as billing suspension, 401, 402, or 403 open a circuit until the configuration changes. Jobs fall back to the data-derived planner instead of cascading into HTTP 502 responses.

## External integration setup

- Bright Data: enable the integration, configure the zone names, and store the Bearer API key under API Keys.
- SigNoz: set the OTLP ingestion endpoint and ingestion key. Programmatic queries additionally require the workspace URL and a service-account query key.
- Port: set the regional endpoint (`https://api.port.io` or `https://api.us.port.io`), client ID, and client secret. A manually generated temporary token is also supported.

Secrets are write-only through the API: reads return masked values, and saving a settings section preserves the original secret.

## Data and API

Development data defaults to `backend/data`. Set `ROBOTWORLD_DATA_DIR` to relocate the SQLite database, generated assets, demos, and model checkpoints. The health endpoints are `GET /health` and `GET /api/health`; interactive API documentation is available at `/docs` while the service is running.

## Project layout

```text
backend/
  app/main.py              FastAPI contract and WebSockets
  app/services/            simulation, compilation, training, providers
  tests/                   production-path integration tests
  robotworld-api.spec      PyInstaller sidecar build
frontend/
  electron/                secure desktop host and API ownership
  src/                     React client, 3D viewport, pages, design system
Doc/                       product and hackathon research notes
```
