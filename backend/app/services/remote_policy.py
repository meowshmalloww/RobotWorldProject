"""Strict remote VLA client and MuJoCo controller.

This is the production learned-policy boundary.  It consumes only named RGB
cameras, 5-D proprioception, and an instruction.  It never calls the oracle
planner, never activates the sticky-grasp equality, and never falls back to a
scripted action when the remote model is unavailable or returns invalid data.

The remote gateway may wrap the user-selected open-source VLA, but it must
implement ``robotworld.policy.v1`` exactly for the declared embodiment.
"""
from __future__ import annotations

import base64
import io
import math
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
from PIL import Image


SCHEMA_VERSION = "robotworld.policy.v1"
CAMERAS = ("front", "wrist")
STATE_SIZE = 5
ACTION_SIZE = 5


class PolicyError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PolicyConfig:
    endpoint: str
    api_key: str = ""
    policy_id: str = "unconfigured"
    embodiment: str = "robotworld-4dof-v1"
    model_revision: str = ""
    model_sha256: str = ""
    normalization_sha256: str = ""
    environment_sha256: str = ""
    instruction: str = "Open the refrigerator door."
    timeout_s: float = 10.0
    execution_horizon: int = 8
    image_size: int = 256

    @classmethod
    def from_settings(cls, flat: dict[str, Any]) -> "PolicyConfig":
        endpoint = str(flat.get("models.policyEndpoint") or "").strip().rstrip("/")
        if not endpoint:
            raise PolicyError("policy_not_configured", "Remote VLA endpoint is not configured in Settings -> Models.")
        if not endpoint.startswith(("http://", "https://")):
            raise PolicyError("policy_not_configured", "Remote VLA endpoint must use http:// or https://.")
        config = cls(
            endpoint=endpoint,
            api_key=str(flat.get("models.policyApiKey") or ""),
            policy_id=str(flat.get("models.policyId") or ""),
            embodiment=str(flat.get("models.policyEmbodiment") or ""),
            model_revision=str(flat.get("models.policyModelRevision") or ""),
            model_sha256=str(flat.get("models.policyModelSha256") or ""),
            normalization_sha256=str(flat.get("models.policyNormalizationSha256") or ""),
            environment_sha256=str(flat.get("models.policyEnvironmentSha256") or ""),
            instruction=str(flat.get("models.policyInstruction") or "Open the refrigerator door."),
            timeout_s=float(flat.get("models.policyTimeoutS") or 10),
            execution_horizon=max(1, min(int(flat.get("models.policyExecutionHorizon") or 8), 40)),
        )
        for name, value in (("model", config.model_sha256), ("normalization", config.normalization_sha256), ("environment", config.environment_sha256)):
            if len(value) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
                raise PolicyError("policy_not_configured", f"Configured {name} SHA-256 must be exactly 64 hexadecimal characters.")
        if not config.model_revision:
            raise PolicyError("policy_not_configured", "A pinned policy model revision is required.")
        return config


class PolicyClient:
    def __init__(self, config: PolicyConfig, *, transport: httpx.BaseTransport | None = None):
        self.config = config
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        self.http = httpx.Client(timeout=config.timeout_s, headers=headers, transport=transport)
        self.capabilities: dict[str, Any] | None = None

    def close(self) -> None:
        self.http.close()

    def _json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.http.request(method, f"{self.config.endpoint}{path}", json=payload)
        except httpx.TimeoutException as exc:
            raise PolicyError("policy_timeout", f"Policy request exceeded {self.config.timeout_s:g}s.") from exc
        except httpx.HTTPError as exc:
            raise PolicyError("policy_unavailable", f"Policy transport failed: {exc}") from exc
        if response.status_code >= 400:
            raise PolicyError("policy_unavailable", f"Policy gateway returned HTTP {response.status_code}.")
        try:
            value = response.json()
        except ValueError as exc:
            raise PolicyError("policy_schema_mismatch", "Policy gateway returned non-JSON data.") from exc
        if not isinstance(value, dict):
            raise PolicyError("policy_schema_mismatch", "Policy gateway response must be a JSON object.")
        return value

    def probe(self) -> dict[str, Any]:
        caps = self._json("GET", "/v1/capabilities")
        if caps.get("schemaVersion") != SCHEMA_VERSION:
            raise PolicyError("policy_schema_mismatch", f"Expected {SCHEMA_VERSION} capabilities.")
        if caps.get("policyId") != self.config.policy_id:
            raise PolicyError("policy_checkpoint_mismatch", "Gateway policyId does not match the configured checkpoint.")
        if caps.get("embodiment") != self.config.embodiment:
            raise PolicyError("policy_checkpoint_mismatch", "Gateway embodiment does not match RobotWorld's configured embodiment.")
        if caps.get("modelRevision") != self.config.model_revision:
            raise PolicyError("policy_checkpoint_mismatch", "Gateway model revision does not match the configured revision.")
        expected_hashes = {
            "modelSha256": self.config.model_sha256,
            "normalizationSha256": self.config.normalization_sha256,
            "environmentSha256": self.config.environment_sha256,
        }
        for field, expected in expected_hashes.items():
            value = str(caps.get(field) or "")
            if len(value) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
                raise PolicyError("policy_checkpoint_mismatch", f"Gateway did not declare a valid {field}.")
            if value.lower() != expected.lower():
                raise PolicyError("policy_checkpoint_mismatch", f"Gateway {field} does not match the configured hash.")
        obs = caps.get("observation") or {}
        camera_names = [item.get("name") for item in obs.get("cameras", []) if isinstance(item, dict)]
        if camera_names != list(CAMERAS) or int(obs.get("stateSize", -1)) != STATE_SIZE:
            raise PolicyError("policy_schema_mismatch", "Gateway camera order or proprioception size is incompatible.")
        action = caps.get("action") or {}
        if int(action.get("size", -1)) != ACTION_SIZE or action.get("representation") != "relative_joint_absolute_gripper":
            raise PolicyError("policy_schema_mismatch", "Gateway action space is incompatible.")
        if not bool(caps.get("checkpointTrainedForEmbodiment")):
            raise PolicyError("policy_checkpoint_mismatch", "Checkpoint is not declared fine-tuned for this embodiment.")
        if int(action.get("horizon", 0)) < self.config.execution_horizon:
            raise PolicyError("policy_schema_mismatch", "Execution horizon exceeds the checkpoint action horizon.")
        self.capabilities = caps
        return caps

    def reset(self, episode_id: str, *, seed: int, environment_sha256: str) -> None:
        result = self._json("POST", "/v1/reset", {
            "schemaVersion": SCHEMA_VERSION,
            "episodeId": episode_id,
            "seed": seed,
            "instruction": self.config.instruction,
            "environmentSha256": environment_sha256,
        })
        if result.get("episodeId") != episode_id or result.get("accepted") is not True:
            raise PolicyError("policy_schema_mismatch", "Policy reset was not acknowledged for this episode.")

    @staticmethod
    def _png(frame: np.ndarray) -> str:
        buf = io.BytesIO()
        Image.fromarray(frame, mode="RGB").save(buf, format="PNG", optimize=False)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def actions(self, *, episode_id: str, sequence: int, sim_time_s: float, state: np.ndarray, frames: dict[str, np.ndarray]) -> list[np.ndarray]:
        if self.capabilities is None:
            raise PolicyError("policy_schema_mismatch", "Policy capabilities were not verified.")
        if state.shape != (STATE_SIZE,) or not np.isfinite(state).all():
            raise PolicyError("policy_invalid_observation", "Proprioception must be finite float32[5].")
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "episodeId": episode_id,
            "sequence": sequence,
            "simTimeNs": int(sim_time_s * 1e9),
            "deadlineNs": time.time_ns() + int(self.config.timeout_s * 1e9),
            "instruction": self.config.instruction,
            "video": [
                {"name": name, "mimeType": "image/png", "dataBase64": self._png(frames[name])}
                for name in CAMERAS
            ],
            "state": {"jointPositionAndGripper": state.astype(float).tolist()},
            "requestedHorizon": self.config.execution_horizon,
        }
        result = self._json("POST", "/v1/actions", payload)
        if result.get("schemaVersion") != SCHEMA_VERSION or result.get("episodeId") != episode_id or int(result.get("requestSequence", -1)) != sequence:
            raise PolicyError("policy_stale_observation", "Policy response does not match the current episode/sequence.")
        raw = result.get("actions")
        if not isinstance(raw, list) or not (1 <= len(raw) <= self.config.execution_horizon):
            raise PolicyError("policy_invalid_output", "Policy must return a non-empty bounded action chunk.")
        parsed: list[np.ndarray] = []
        max_delta = np.array([0.08, 0.08, 0.08, 0.08], dtype=np.float32)
        for item in raw:
            arr = np.asarray(item, dtype=np.float32)
            if arr.shape != (ACTION_SIZE,) or not np.isfinite(arr).all():
                raise PolicyError("policy_nonfinite_action", "Every policy action must be finite float32[5].")
            if np.any(np.abs(arr[:4]) > max_delta) or not 0.0 <= float(arr[4]) <= 1.0:
                raise PolicyError("policy_action_out_of_range", "Policy action violates the embodiment safety envelope.")
            parsed.append(arr)
        return parsed


class RemotePolicyController:
    """Synchronous receding-horizon controller for ``simcore.run_rollout``."""

    requires_success_hold = True

    def __init__(self, world, config: PolicyConfig, *, transport: httpx.BaseTransport | None = None):
        self.world = world
        self.config = config
        self.client = PolicyClient(config, transport=transport)
        self.episode_id = str(uuid.uuid4())
        self.sequence = 0
        self.t = 0.0
        self.queue: deque[np.ndarray] = deque()
        self.policy_error: PolicyError | None = None
        try:
            caps = self.client.probe()
            self.client.reset(self.episode_id, seed=0, environment_sha256=self.config.environment_sha256)
        except PolicyError as exc:
            self.policy_error = exc

    def close(self) -> None:
        self.client.close()

    def act(self, dt: float) -> tuple[np.ndarray, float, bool]:
        self.t += dt
        if self.policy_error is not None:
            return self.world.arm_qpos(), self.world.grip(), True
        try:
            if not self.queue:
                size = self.config.image_size
                frames = {
                    "front": self.world.render_rgb("debug", width=size, height=size),
                    "wrist": self.world.render_rgb("wrist", width=size, height=size),
                }
                actions = self.client.actions(
                    episode_id=self.episode_id,
                    sequence=self.sequence,
                    sim_time_s=self.t,
                    state=self.world.policy_state(),
                    frames=frames,
                )
                self.sequence += 1
                self.queue.extend(actions)
            action = self.queue.popleft()
        except PolicyError as exc:
            self.policy_error = exc
            return self.world.arm_qpos(), self.world.grip(), True
        q = self.world.arm_qpos() + action[:4]
        for i, (lo, hi) in enumerate(self.world.JOINT_LIM):
            if not lo <= float(q[i]) <= hi:
                self.policy_error = PolicyError("policy_safety_reject", f"Action would exceed joint {i} limits.")
                return self.world.arm_qpos(), self.world.grip(), True
        return q, float(action[4]), False
