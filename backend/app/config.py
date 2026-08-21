"""Runtime configuration.

Resolution order for every setting:
  1. value stored in the `settings` DB table (written via PUT /api/settings/...)
  2. environment variable / .env file (bootstrap overrides)
  3. hard default below

Secrets (API keys) live in the same settings table but are masked by the
settings API. Nothing secret is ever logged.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
# Packaged desktop builds set ROBOTWORLD_DATA_DIR to Electron's per-user data
# directory.  Keeping generated assets and SQLite outside a PyInstaller bundle
# is required because bundled resources are read-only/ephemeral.
DATA_DIR = Path(os.environ.get("ROBOTWORLD_DATA_DIR", BASE_DIR / "data")).resolve()
ASSETS_DIR = DATA_DIR / "assets"
DEMOS_DIR = DATA_DIR / "demos"
MODELS_DIR = DATA_DIR / "models"
WORLDS_DIR = DATA_DIR / "worlds"
ROBOTS_DIR = DATA_DIR / "robots"
DB_PATH = DATA_DIR / "robotworld.db"

for _d in (DATA_DIR, ASSETS_DIR, DEMOS_DIR, MODELS_DIR, WORLDS_DIR, ROBOTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


class EnvSettings(BaseSettings):
    """Bootstrap-only environment overrides (DB settings take precedence)."""

    model_config = SettingsConfigDict(env_file=(str(BASE_DIR.parent / ".env"), str(BASE_DIR / ".env")), extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-5.6-luna"

    policy_endpoint: str | None = None
    policy_api_key: str | None = None
    policy_model_revision: str | None = None
    policy_model_sha256: str | None = None
    policy_normalization_sha256: str | None = None
    policy_environment_sha256: str | None = None
    trellis_endpoint: str | None = None
    trellis_api_key: str | None = None
    isaac_sim_root: str | None = None
    isaacsim_asset_root: str | None = None

    brightdata_api_key: str | None = None
    brightdata_account_id: str | None = None
    brightdata_serp_zone: str = "serp"
    brightdata_unlocker_zone: str = "web_unlocker"

    signoz_endpoint: str | None = None  # e.g. https://ingest.us.signoz.cloud:443
    signoz_query_endpoint: str | None = None  # e.g. https://my-workspace.us.signoz.cloud
    signoz_ingestion_key: str | None = None
    signoz_api_key: str | None = None   # for the v5 query API
    signoz_region: str = "us"

    port_api_key: str | None = None
    port_client_id: str | None = None
    port_client_secret: str | None = None
    port_endpoint: str = "https://api.port.io"


env = EnvSettings()

# Settings table keys that hold secrets — masked by the settings API.
SECRET_KEYS = {
    "integrations.port.token",
    "integrations.port.clientSecret",
    "integrations.brightdata.apiKey",
    "integrations.signoz.ingestionKey",
    "integrations.signoz.apiKey",
    "models.openaiKey",
    "models.policyApiKey",
    "models.trellisApiKey",
}

DEFAULT_SETTINGS: dict = {
    "general": {
        "workspaceName": "RobotWorld Local",
        "region": "local",
        "autosave": True,
        "telemetry": True,
    },
    "appearance": {"theme": "dark", "accent": "graphite", "density": "comfortable"},
    "integrations": {
        "port": {
            "enabled": False,
            "endpoint": env.port_endpoint,
            "clientId": env.port_client_id or "",
            "clientSecret": env.port_client_secret or "",
            "token": env.port_api_key or "",
        },
        "brightdata": {
            "enabled": True,
            "accountId": env.brightdata_account_id or "",
            "serpZone": env.brightdata_serp_zone,
            "unlockerZone": env.brightdata_unlocker_zone,
            "apiKey": env.brightdata_api_key or "",
        },
        "signoz": {
            "enabled": False,
            "mode": "self_hosted",
            "endpoint": env.signoz_endpoint or "http://127.0.0.1:4318",
            "queryEndpoint": env.signoz_query_endpoint or "http://127.0.0.1:8080",
            "ingestionKey": env.signoz_ingestion_key or "",
            "apiKey": env.signoz_api_key or "",
            "region": "local",
        },
    },
    "simulation": {
        "engine": "mujoco",
        "gravity": -9.81,
        "timestepHz": 500,
        "renderer": "mujoco-offscreen",
        "isaacRoot": env.isaac_sim_root or "",
        "isaacAssetRoot": env.isaacsim_asset_root or "",
        "isaacVersion": "6.0",
    },
    "models": {
        "planner": env.openai_model,
        "vlm": env.openai_model,
        # Evidence extraction is an explicit, audited action.  It is not the
        # planner and it never runs during an asset build by default.
        "assetAnalysisModel": "gpt-5.6-luna",
        "reasoningEffort": "high",
        "verbosity": "medium",
        # Asset validation and learned-policy evaluation are deliberately
        # separate.  The former may use the privileged scripted oracle; the
        # latter must use rendered pixels and an external embodied checkpoint.
        "policy": "asset-validation",
        "policyEndpoint": env.policy_endpoint or "",
        "policyApiKey": env.policy_api_key or "",
        "policyId": "lerobot/VLA-JEPA-Pretrain",
        "policyPath": r"D:\VLA-JEPA-Pretrain",
        "policyEmbodiment": "robotworld-4dof-v1",
        "policyModelRevision": env.policy_model_revision or "",
        "policyModelSha256": env.policy_model_sha256 or "",
        "policyNormalizationSha256": env.policy_normalization_sha256 or "",
        "policyEnvironmentSha256": env.policy_environment_sha256 or "",
        "policyInstruction": "Open the refrigerator door.",
        "policyTimeoutS": 10,
        "policyExecutionHorizon": 8,
        "trellisEndpoint": env.trellis_endpoint or "http://127.0.0.1:8188",
        "trellisApiKey": env.trellis_api_key or "",
        "trellisModel": "microsoft/TRELLIS.2-4B",
        "trellisRuntime": "native",
        "trellisResolution": 1024,
        "trellisSeed": 1048576,
        "trellisBackgroundRemoval": True,
        "trellisNativePath": r"D:\TRELLIS.2-4B",
        "trellisGgufPath": r"D:\TRELLIS.2-4B-Q4-GGUF\q4",
        "trellisCppPath": r"D:\trellis.cpp-v0.6.0-cuda12",
        "trellisTimeoutS": 300,
        "openaiKey": env.openai_api_key or "",
        "openaiBaseUrl": env.openai_base_url or "https://api.openai.com/v1",
        "provider": "openai-compatible",
        "timeoutS": 60,
    },
}
