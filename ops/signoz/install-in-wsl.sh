#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f /etc/os-release ]] || grep -qi docker-desktop /etc/os-release; then
  echo "Run this inside a normal WSL2 Ubuntu distribution, not docker-desktop." >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Engine is missing. Install it inside this WSL distribution first:" >&2
  echo "  curl -fsSL https://get.docker.com | sh" >&2
  echo "  sudo usermod -aG docker \$USER" >&2
  exit 3
fi

if ! docker info >/dev/null 2>&1; then
  echo "The native WSL Docker daemon is not running. Try: sudo service docker start" >&2
  exit 4
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required." >&2
  exit 5
fi

if ! command -v foundryctl >/dev/null 2>&1; then
  echo "foundryctl is missing. Install the official SigNoz Foundry CLI:" >&2
  echo "  curl -fsSL https://signoz.io/foundry.sh | bash" >&2
  exit 6
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
foundryctl gauge -f "${script_dir}/casting.yaml"
foundryctl cast -f "${script_dir}/casting.yaml"
docker compose \
  -f "${script_dir}/pours/deployment/compose.yaml" \
  -f "${script_dir}/compose.override.yaml" \
  up -d
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/health?live=1 >/dev/null
echo "SigNoz Community is healthy: http://127.0.0.1:8080"
