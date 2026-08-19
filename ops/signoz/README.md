# RobotWorld SigNoz Community

This directory uses SigNoz's supported Foundry deployment path. The SigNoz
image is pinned to `v0.137.1`; Foundry generates and owns the Compose files.
Do not edit generated files under `pours/`.

Windows requires a normal WSL2 Linux distribution with Docker Engine installed
inside that distribution. Do not use the `docker-desktop` WSL distribution for
this stack: SigNoz documents ClickHouse Keeper exit-139 restart loops there.

From an Ubuntu WSL shell:

1. Install Docker Engine and the Compose v2 plugin inside Ubuntu.
2. Disable Docker Desktop integration for that Ubuntu distribution.
3. Install Foundry with `curl -fsSL https://signoz.io/foundry.sh | bash`.
4. Run `bash /mnt/d/RobotWorldProject/ops/signoz/install-in-wsl.sh`.
5. Create the first local admin at `http://127.0.0.1:8080`.
6. In SigNoz, create a service account and API key if RobotWorld's agent should
   query `/api/v5/query_range`. OTLP ingestion itself does not need a key.

RobotWorld defaults:

- UI/query host: `http://127.0.0.1:8080`
- OTLP/HTTP: `http://127.0.0.1:4318`
- OTLP/gRPC: `http://127.0.0.1:4317`

The installation needs at least 4 GB of Docker memory and ports 8080, 4317,
and 4318 available. SigNoz MCP is intentionally disabled because its default
port 8000 conflicts with RobotWorld's API.
