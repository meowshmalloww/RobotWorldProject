# RobotWorld real-system test runbook

Updated 2026-08-18. A green world-build gate is not a robot-task pass. RobotWorld
persists the distinction and will not convert a missing model, timeout, or
incompatible adapter into success.

## 1. Run the source build on this Windows computer

Requirements: Python 3.11, Node.js 20+, npm, and a Vulkan-capable GPU/driver.

```powershell
cd D:\RobotWorldProject\backend
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

cd ..\frontend
npm ci
npm run dev:electron
```

`dev:electron` starts Vite and Electron; Electron owns the FastAPI sidecar. For
browser-only development, use two terminals:

```powershell
cd D:\RobotWorldProject\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
cd D:\RobotWorldProject\frontend
npm run dev
```

Open `http://127.0.0.1:5173/#/`. Verify the API:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/render/vulkan/probe
```

The Vulkan response must say `available: true`, `backend: Vulkan`,
`browser3dApi: none`, and name a hardware adapter. There is no WebGL fallback.

## 2. Test the two acceptance buttons now

Open **Worlds → Scene Editor**.

1. Enter or randomize a seed.
2. Press **Kitchen acceptance**.
3. In the bottom **Console**, require passed stages for native Vulkan, MuJoCo
   compile/stability, and persisted evidence.
4. Inspect the manifest and MJCF SHA-256 values on the right.
5. Repeat with **Logistics acceptance** and a different seed.

The current expected terminal state is `blocked`, with `taskSuccess: null` and
`policy_not_configured`. That is a correct result until a compatible VLA is
connected. It proves that each click built and compiled a fresh physical world;
it does not pretend the robot completed the task.

The kitchen MJCF contains manipulable fruit and cup bodies, an open sink and
blender jar, and named cabinet-door, blender-lid, and switch joints. The
logistics MJCF contains randomized free parcels and open-front collision bays.
The run fails if those bodies/joints are missing or physics becomes non-finite.

Generated evidence is stored under the configured RobotWorld data directory in
`demos/<scenario>-<job-id>/manifest.json` and `world.mjcf.xml`.

## 3. Verify the repository

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

The integration suite executes both acceptance builders, the native Vulkan
probe/frame endpoint, MuJoCo and OpenUSD compilation, settings/secrets, and a
live WebSocket evaluation session.

## 4. Verify Bright Data without exposing the key

Rotate the API key that was pasted into chat before a public demo. Store the new
key only under **Settings → API Keys → Bright Data**.

Under **Settings → Integrations**:

1. enable Bright Data;
2. set **SERP zone** to `serp_api1`;
3. save;
4. press **Run paid SERP check** once.

A pass is a provider response with parsed organic rows and sanitized sample
domains. This action is explicitly billable and never returns the key or raw
authorization headers. SERP is discovery—not a complete structured product
source. For catalog ingestion, create a custom Scraper Studio collector, retain
its non-secret `c_*` ID, register it under **Sources**, and require the source
schema in `PRODUCTION_ACCEPTANCE.md`.

## 5. Install self-hosted SigNoz Community

SigNoz's supported Windows path is a normal WSL2 Ubuntu distribution with
Docker Engine installed inside Ubuntu. Do not run this stack in Docker Desktop's
`docker-desktop` distribution; SigNoz documents ClickHouse Keeper exit-139
restart loops on that path.

From an elevated PowerShell window, install Ubuntu if it is not already present:

```powershell
wsl --install -d Ubuntu-24.04
```

Finish Ubuntu's first-run user setup and reboot if Windows requests it. Then run
inside the Ubuntu shell:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
exit
```

Reopen Ubuntu and continue:

```bash
sudo service docker start
docker info
curl -fsSL https://signoz.io/foundry.sh | bash
bash /mnt/d/RobotWorldProject/ops/signoz/install-in-wsl.sh
```

Open `http://127.0.0.1:8080` and create the local administrator. In RobotWorld:

1. set OTLP/HTTP to `http://127.0.0.1:4318`;
2. set the SigNoz UI/query endpoint to `http://127.0.0.1:8080`;
3. enable and save SigNoz;
4. restart RobotWorld so all OpenTelemetry providers attach at process startup;
5. press **Verify local SigNoz**;
6. press **Open SigNoz UI** in Settings or **Open SigNoz Community** from
   Observability.

OTLP ingestion to self-hosted Community is keyless. A local service-account API
key is only needed for RobotWorld's programmatic `/api/v5/query_range` queries;
store it under **API Keys → SigNoz query API**.

Reference: <https://signoz.io/docs/install/docker/>

## 6. Connect the VLA only after choosing it

Do not install GR00T or any other checkpoint just because an adapter file exists
in this repository. First choose the exact open-source checkpoint and confirm:

- its license permits this use;
- its native observation schema (camera names/count, resolution, proprioception);
- its action representation, dimension, rate, and horizon;
- a compatible robot embodiment/checkpoint;
- the GPU/runtime it actually supports;
- a pinned model revision and model/normalization hashes.

Then implement a task-specific gateway and embodiment adapter. The current
`robotworld.policy.v1` client is a strict door-task boundary and is intentionally
rejected by the two new acceptance worlds. Renaming fields is not compatibility.

For the eventual real pass, preserve raw camera observations, actions, latency,
contacts, violations, terminal predicates, seed, manifest hash, model revision,
and checkpoint hashes. Run held-out seeds. Any timeout, invalid/non-finite
action, schema mismatch, manual takeover, or fallback is a failed episode.

No training runs in this build. The workstation can demonstrate world/data
generation and evaluation readiness without claiming it trained a large VLA.

The Jetson Nano should be treated as a later sensor/robot I/O and watchdog node,
not as the assumed host for a modern 3B VLA. Test the selected model on this
computer first; deployment hardware is decided only after measuring its actual
memory and latency.

## 7. TRELLIS.2 and interactable parts

TRELLIS.2 is optional image-to-PBR-GLB generation, not a VLA and not an
articulation system. Microsoft's documented setup is Linux, CUDA 12.4, and an
NVIDIA GPU with at least 24 GB; the official verified GPUs are A100/H100. The
RTX 4080 Laptop GPU in this computer does not meet that stated memory floor.

A single generated blender mesh cannot safely become interactable by inventing
joints. Use this reviewed pipeline instead:

1. retain product identity, manual, dimensions, and source images;
2. generate or retrieve the visual mesh;
3. propose semantic parts from multi-view evidence (base, jar, lid, switch);
4. require human approval when evidence is incomplete;
5. author separate collision meshes, mass/inertia, joints, limits, axes, and
   attachment frames;
6. compile to MJCF/OpenUSD and run penetration, stability, range, and contact
   tests;
7. quarantine the asset if any required part or physical parameter is unknown.

The kitchen acceptance world implements this contract with authored primitives;
it does not claim that TRELLIS.2 separated the parts.

Reference: <https://github.com/microsoft/TRELLIS.2>

## 8. Build and smoke-test the installer

```powershell
cd D:\RobotWorldProject\frontend
npm run dist
```

Install `frontend/release/RobotWorld Setup 1.0.0.exe`, launch it, repeat the
health/Vulkan probes, and run both acceptance buttons. The installed app uses a
separate per-user settings database, so credentials must be entered again.

## Honest final pass criteria

- Native viewport: the backend reports hardware `Vulkan`; no browser 3D API.
- Bright Data: a real paid response and retained provenance.
- SigNoz: local UI/OTLP reachable and a RobotWorld trace visible after restart.
- Environment: randomized manifest and hashed MJCF compile with finite physics.
- VLA: selected real checkpoint runs closed loop; evaluator-only state and
  scripted fallback remain unavailable.
- Kitchen: every ordered state/contact predicate passes on held-out seeds.
- Logistics: every parcel finishes fully inside the correct physical bay on
  held-out seeds.
- Training: reported as disabled, never implied by evaluation.
