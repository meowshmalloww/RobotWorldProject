"""Control-plane client for the isolated VLA-JEPA JSON-lines worker."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from ..config import BASE_DIR, DATA_DIR, WORKERS_DIR


class VlaWorkerError(RuntimeError):
    pass


class VlaWorkerUnavailable(VlaWorkerError):
    pass


WORKER_SCRIPT = (BASE_DIR / "workers" / "vla_policy_worker.py").resolve()
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_VLA_PYTHON = Path(r"D:\RobotWorldRuntimes\vla-env\Scripts\python.exe")
DEFAULT_LEROBOT_REPO = Path(r"D:\LeRobot")


def _configured_python() -> Path:
    value = os.environ.get("VLA_JEPA_PYTHON") or (str(DEFAULT_VLA_PYTHON) if DEFAULT_VLA_PYTHON.is_file() else sys.executable)
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file():
        raise VlaWorkerUnavailable(f"VLA_JEPA_PYTHON is not a file: {path}")
    return path


def _configured_lerobot_repo() -> Path | None:
    value = os.environ.get("LEROBOT_REPO_PATH")
    defaults_disabled = os.environ.get("ROBOTWORLD_DISABLE_LOCAL_RUNTIME_DEFAULTS", "").lower() in {"1", "true", "yes"}
    if not value and not defaults_disabled:
        value = str(DEFAULT_LEROBOT_REPO) if DEFAULT_LEROBOT_REPO.is_dir() else ""
    if not value:
        return None
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise VlaWorkerUnavailable(f"LEROBOT_REPO_PATH is not a directory: {path}")
    return path


def _worker_environment() -> dict[str, str]:
    # Deliberately omit application/provider API keys from the worker process.
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "USERNAME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "CUDA_PATH",
        "CUDA_VISIBLE_DEVICES",
        "HF_HOME",
        "HF_HUB_CACHE",
        "TRANSFORMERS_CACHE",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    allow_downloads = os.environ.get("ROBOTWORLD_ALLOW_MODEL_DOWNLOADS", "").lower() in {"1", "true", "yes"}
    env["ROBOTWORLD_WORKER_ALLOW_MODEL_DOWNLOADS"] = "1" if allow_downloads else "0"
    if not allow_downloads:
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
    return env


class JsonLineWorker:
    def __init__(self, python_path: Path):
        self.python_path = python_path
        self._process: subprocess.Popen[str] | None = None
        self._pending: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._reader: threading.Thread | None = None
        self._stderr_handle = None
        self.log_path: Path | None = None

    def _start(self) -> None:
        with self._lifecycle_lock:
            if self._process is not None and self._process.poll() is None:
                return
            if not WORKER_SCRIPT.is_file():
                raise VlaWorkerUnavailable(f"VLA-JEPA worker script is missing: {WORKER_SCRIPT}")
            WORKERS_DIR.mkdir(parents=True, exist_ok=True)
            self.log_path = WORKERS_DIR / f"vla-policy-worker-{uuid.uuid4().hex[:10]}.log"
            self._stderr_handle = self.log_path.open("a", encoding="utf8")
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                self._process = subprocess.Popen(
                    [str(self.python_path), "-u", str(WORKER_SCRIPT)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=self._stderr_handle,
                    text=True,
                    encoding="utf8",
                    errors="replace",
                    bufsize=1,
                    env=_worker_environment(),
                    creationflags=creationflags,
                )
            except OSError as exc:
                self._stderr_handle.close()
                self._stderr_handle = None
                raise VlaWorkerUnavailable(f"Could not launch VLA-JEPA worker: {exc}") from exc
            self._reader = threading.Thread(target=self._read_loop, name="vla-worker-reader", daemon=True)
            self._reader.start()

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            try:
                response = json.loads(line)
                request_id = str(response.get("id") or "")
            except (json.JSONDecodeError, AttributeError):
                continue
            with self._pending_lock:
                waiter = self._pending.get(request_id)
            if waiter is not None:
                waiter.put(response)
        with self._pending_lock:
            pending = list(self._pending.values())
        for waiter in pending:
            waiter.put({"ok": False, "error": "VLA-JEPA worker exited before responding.", "errorType": "WorkerExit"})

    def request(self, operation: str, payload: dict[str, Any] | None = None, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
        self._start()
        request_id = uuid.uuid4().hex
        waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        request = {"id": request_id, "operation": operation, **(payload or {})}
        with self._pending_lock:
            self._pending[request_id] = waiter
        try:
            with self._write_lock:
                process = self._process
                if process is None or process.poll() is not None or process.stdin is None:
                    raise VlaWorkerUnavailable("VLA-JEPA worker is not running.")
                process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
                process.stdin.flush()
            try:
                response = waiter.get(timeout=max(0.1, timeout))
            except queue.Empty as exc:
                self.stop(force=True)
                raise VlaWorkerUnavailable(f"VLA-JEPA worker timed out during {operation} after {timeout:g} seconds.") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if not response.get("ok"):
            error_type = response.get("errorType") or "WorkerError"
            trace = str(response.get("traceback") or "").strip()
            detail = f"{error_type}: {response.get('error') or 'unknown worker failure'}"
            if trace:
                detail += f"\n{trace}"
            raise VlaWorkerError(detail)
        result = response.get("result")
        if not isinstance(result, dict):
            raise VlaWorkerError("VLA-JEPA worker returned a malformed result.")
        return result

    def status(self) -> dict[str, Any]:
        process = self._process
        if process is None or process.poll() is not None:
            return {
                "running": False,
                "loaded": False,
                "pid": None,
                "python": str(self.python_path),
                "logPath": str(self.log_path) if self.log_path else None,
            }
        result = self.request("status", timeout=5)
        return {
            "running": True,
            "pid": process.pid,
            "python": str(self.python_path),
            "logPath": str(self.log_path) if self.log_path else None,
            **result,
        }

    def stop(self, *, force: bool = False) -> None:
        with self._lifecycle_lock:
            process = self._process
            if process is None:
                return
            if process.poll() is None and not force:
                try:
                    self.request("shutdown", timeout=3)
                except VlaWorkerError:
                    force = True
            if process.poll() is None:
                if force:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
            if self._stderr_handle:
                self._stderr_handle.close()
            self._stderr_handle = None
            self._process = None


_client: JsonLineWorker | None = None
_client_lock = threading.Lock()


def _get_client() -> JsonLineWorker:
    global _client
    python_path = _configured_python()
    with _client_lock:
        if _client is not None and _client.python_path != python_path:
            _client.stop(force=True)
            _client = None
        if _client is None:
            _client = JsonLineWorker(python_path)
        return _client


def _request_payload(checkpoint_path: str, expected_device: str) -> dict[str, Any]:
    checkpoint = Path(checkpoint_path).expanduser().resolve(strict=True)
    repo = _configured_lerobot_repo()
    device = expected_device if expected_device and expected_device != "auto" else "cuda"
    qwen_metadata = os.environ.get("QWEN3_VL_METADATA_PATH")
    default_qwen_metadata = Path(r"D:\RobotWorldRuntimes\model-metadata\Qwen3-VL-2B-Instruct")
    if (
        not qwen_metadata
        and os.environ.get("ROBOTWORLD_DISABLE_LOCAL_RUNTIME_DEFAULTS") != "1"
        and default_qwen_metadata.is_dir()
    ):
        # This directory contains tokenizer/config metadata only. The scoped
        # worker loader reconstructs Qwen structure and fills weights from the
        # selected VLA checkpoint, whether it is the base or a candidate.
        qwen_metadata = str(default_qwen_metadata)
    return {
        "checkpointPath": str(checkpoint),
        "lerobotRepoPath": str(repo) if repo else None,
        "device": device,
        "cudaDevice": 0,
        "loadWorldModelForInference": False,
        "qwenMetadataPath": qwen_metadata,
    }


def probe_checkpoint(checkpoint_path: str, expected_device: str = "cuda") -> dict[str, Any]:
    result = _get_client().request("probe", _request_payload(checkpoint_path, expected_device), timeout=30)
    return {**result, "worker": _get_client().status()}


def load_checkpoint(checkpoint_path: str, expected_device: str = "cuda") -> dict[str, Any]:
    timeout = float(os.environ.get("VLA_JEPA_LOAD_TIMEOUT_S") or 900)
    result = _get_client().request("load", _request_payload(checkpoint_path, expected_device), timeout=max(30, min(timeout, 3600)))
    return {**result, "worker": _get_client().status()}


def infer_action(
    *,
    images: dict[str, str],
    state: list[float] | None,
    instruction: str,
    adapter_revision: str,
    normalization_revision: str,
) -> dict[str, Any]:
    """Run one bounded inference request against the resident worker.

    Observation paths remain server-side references and are constrained to the
    RobotWorld artifact root by the worker. The returned vector is the policy's
    normalized output before any checkpoint-specific physical unnormalizer; the
    embodiment bridge owns the physical action conversion.
    """

    timeout = float(os.environ.get("VLA_JEPA_INFERENCE_TIMEOUT_S") or 60)
    result = _get_client().request(
        "infer",
        {
            "images": dict(images),
            "state": list(state) if state is not None else None,
            "instruction": instruction,
            "adapterRevision": adapter_revision,
            "normalizationRevision": normalization_revision,
            "allowedArtifactRoots": [str(DATA_DIR.resolve())],
        },
        timeout=max(1, min(timeout, 300)),
    )
    return {**result, "worker": _get_client().status()}


def unload_checkpoint() -> dict[str, Any]:
    client = _get_client()
    if not client.status().get("running"):
        return {"unloaded": False, "worker": client.status()}
    result = client.request("unload", timeout=30)
    return {**result, "worker": client.status()}


def status() -> dict[str, Any]:
    try:
        client = _get_client()
        return {
            **client.status(),
            "lerobotRepoPath": str(_configured_lerobot_repo()) if _configured_lerobot_repo() else None,
            "offlineByDefault": os.environ.get("ROBOTWORLD_ALLOW_MODEL_DOWNLOADS", "").lower() not in {"1", "true", "yes"},
            "artifactRoot": str(DATA_DIR),
        }
    except (OSError, VlaWorkerError) as exc:
        return {"running": False, "loaded": False, "error": str(exc), "offlineByDefault": True}


def stop() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.stop(force=False)
            _client = None


def kill() -> None:
    """Immediate local kill switch for a runaway load or inference call."""
    global _client
    with _client_lock:
        if _client is not None:
            _client.stop(force=True)
            _client = None
