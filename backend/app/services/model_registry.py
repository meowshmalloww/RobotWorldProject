"""Model discovery, allowlisted path validation, and bounded inspection.

No function in this module loads model weights or downloads a repository.
Local paths must resolve beneath an explicitly configured root.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import struct
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..config import MODELS_DIR


MODEL_PATH_ENV_VARS = (
    "TRELLIS2_REPO_PATH",
    "TRELLIS2_CHECKPOINT_PATH",
    "DINOV3_REPO_PATH",
    "DINOV3_WEIGHTS_PATH",
    "LEROBOT_REPO_PATH",
    "VLA_JEPA_CHECKPOINT_PATH",
    "ROBOTWORLD_MODEL_ROOTS",
)


def _files(path: Path, pattern: str) -> list[Path]:
    return sorted(value for value in path.glob(pattern) if value.is_file()) if path.is_dir() else []


def _size(files: list[Path]) -> int:
    return sum(value.stat().st_size for value in files)


def _sha256_file(path: Path, *, chunk_bytes: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def configured_model_roots() -> list[Path]:
    """Return resolved roots configured by the server operator.

    ``ROBOTWORLD_MODEL_ROOTS`` is a Windows ``;`` separated list. Specific
    model variables are also accepted as exact allowlisted paths. The local
    artifact-model directory is always safe.
    """

    values: list[str] = [str(MODELS_DIR)]
    for name in MODEL_PATH_ENV_VARS:
        raw = str(os.environ.get(name) or "").strip()
        if not raw:
            continue
        pieces = raw.split(os.pathsep) if name == "ROBOTWORLD_MODEL_ROOTS" else [raw]
        values.extend(piece.strip() for piece in pieces if piece.strip())
    roots: list[Path] = []
    for value in values:
        try:
            root = Path(value).expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if root not in roots:
            roots.append(root)
    return roots


def resolve_allowed_local_path(value: str, *, roots: list[Path] | None = None) -> Path:
    if not value or "\x00" in value:
        raise ValueError("A non-empty local model path is required.")
    try:
        candidate = Path(value).expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise ValueError(f"Configured local model path does not exist: {value}") from exc
    allowed = [root.resolve(strict=False) for root in (roots or configured_model_roots())]
    if not any(candidate == root or root in candidate.parents for root in allowed):
        raise ValueError(
            "Local model path is outside ROBOTWORLD_MODEL_ROOTS and the explicit model path allowlist."
        )
    return candidate


def validate_endpoint_url(value: str) -> str:
    """Validate an inference endpoint while blocking credential URLs and SSRF.

    Plain HTTP is allowed only for loopback inference servers. Private network
    hosts require explicit ``ROBOTWORLD_ALLOWED_PRIVATE_MODEL_HOSTS`` entries.
    """

    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Model endpoint must be an absolute http:// or https:// URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Model endpoint must not contain credentials, query parameters, or fragments.")
    host = parsed.hostname.lower()
    explicit_private = {
        item.strip().lower()
        for item in str(os.environ.get("ROBOTWORLD_ALLOWED_PRIVATE_MODEL_HOSTS") or "").split(",")
        if item.strip()
    }
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        addresses.append(ipaddress.ip_address(host))
    except ValueError:
        try:
            addresses.extend(
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            )
        except OSError as exc:
            raise ValueError(f"Model endpoint host could not be resolved: {host}") from exc
    loopback = bool(addresses) and all(address.is_loopback for address in addresses)
    disallowed = [
        address
        for address in addresses
        if (address.is_private or address.is_link_local or address.is_reserved or address.is_multicast)
        and not address.is_loopback
    ]
    if parsed.scheme == "http" and not loopback and host not in explicit_private:
        raise ValueError("Plain HTTP model endpoints are allowed only on loopback or an explicit private-host allowlist.")
    if disallowed and host not in explicit_private:
        raise ValueError("Private/link-local model endpoint is not in ROBOTWORLD_ALLOWED_PRIVATE_MODEL_HOSTS.")
    return value.strip().rstrip("/")


def _git_revision(path: Path) -> str | None:
    for root in (path, *path.parents):
        git = root / ".git"
        if not git.exists():
            continue
        try:
            head = (git / "HEAD").read_text(encoding="utf8").strip()
            if head.startswith("ref: "):
                ref = git / head.removeprefix("ref: ")
                return ref.read_text(encoding="utf8").strip()[:64]
            return head[:64]
        except OSError:
            return None
    return None


def _huggingface_revision(root: Path) -> str | None:
    """Recover the immutable Hub revision written by ``snapshot_download``.

    A directory downloaded with ``local_dir=...`` is not a Git checkout, but
    Hugging Face records the resolved commit on the first line of each
    ``.metadata`` file.  Requiring all observed files to agree prevents a
    partially updated local directory from being presented as one revision.
    """

    metadata_root = root / ".cache" / "huggingface" / "download"
    if not metadata_root.is_dir():
        return None
    revisions: set[str] = set()
    for metadata in metadata_root.glob("*.metadata"):
        try:
            first_line = metadata.read_text(encoding="utf8", errors="replace").splitlines()[0].strip()
        except (OSError, IndexError):
            continue
        if re.fullmatch(r"[0-9a-f]{40}", first_line):
            revisions.add(first_line)
    return next(iter(revisions)) if len(revisions) == 1 else None


def _safetensor_f32_vectors(path: Path, names: tuple[str, ...]) -> dict[str, list[float]]:
    """Read bounded 1-D float32 metadata tensors without importing weights.

    Processor state files are tiny, but the parser still bounds the JSON header,
    tensor length, and offsets.  It never materializes a model safetensor.
    """

    if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        return {}
    with path.open("rb") as stream:
        length_raw = stream.read(8)
        if len(length_raw) != 8:
            return {}
        header_length = int.from_bytes(length_raw, "little", signed=False)
        if header_length <= 0 or header_length > 1024 * 1024:
            return {}
        header_raw = stream.read(header_length)
        try:
            header = json.loads(header_raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(header, dict):
            return {}
        data_start = 8 + header_length
        output: dict[str, list[float]] = {}
        for name in names:
            spec = header.get(name)
            if not isinstance(spec, dict) or spec.get("dtype") != "F32":
                continue
            shape = spec.get("shape")
            offsets = spec.get("data_offsets")
            if (
                not isinstance(shape, list)
                or len(shape) != 1
                or not isinstance(shape[0], int)
                or shape[0] < 1
                or shape[0] > 128
                or not isinstance(offsets, list)
                or len(offsets) != 2
                or not all(isinstance(value, int) for value in offsets)
            ):
                continue
            start, end = offsets
            if start < 0 or end - start != shape[0] * 4 or data_start + end > path.stat().st_size:
                continue
            stream.seek(data_start + start)
            raw = stream.read(end - start)
            if len(raw) != end - start:
                continue
            output[name] = [float(value) for value in struct.unpack(f"<{shape[0]}f", raw)]
        return output


def _candidate_weights(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    patterns = (
        "*.safetensors",
        "*.gguf",
        "*.bin",
        "*.pt",
        "*.pth",
        "ckpts/**/*.safetensors",
        "checkpoints/**/*.safetensors",
    )
    found: dict[str, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                found[str(path.resolve())] = path.resolve()
                if len(found) >= 512:
                    break
    return sorted(found.values())


def inspect_local_model(path: Path, roles: list[str], *, compute_content_hash: bool = False) -> dict[str, Any]:
    """Inspect configured artifacts without importing or allocating weights."""

    root = path if path.is_dir() else path.parent
    weights = _candidate_weights(path)
    errors: list[str] = []
    warnings: list[str] = []
    config_path = root / "config.json"
    config: dict[str, Any] = {}
    if config_path.is_file():
        try:
            parsed = json.loads(config_path.read_text(encoding="utf8"))
            if isinstance(parsed, dict):
                config = parsed
            else:
                errors.append("config.json is not a JSON object")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"config.json is invalid: {exc}")

    role_set = set(roles)
    if "vla_policy" in role_set:
        required = (
            "config.json",
            "model.safetensors",
            "policy_preprocessor.json",
            "policy_preprocessor_step_3_normalizer_processor.safetensors",
            "policy_postprocessor.json",
            "policy_postprocessor_step_2_unnormalizer_processor.safetensors",
        )
        errors.extend(f"missing required VLA-JEPA file: {name}" for name in required if not (root / name).is_file())
    if "image_to_3d" in role_set and not (root / "pipeline.json").is_file():
        errors.append("missing image-to-3D pipeline.json")
    if role_set.intersection({"platform_agent", "vision_encoder", "world_model", "part_understanding", "embedding"}) and not config_path.is_file():
        errors.append("missing config.json")
    if not weights:
        errors.append("no supported checkpoint weight files were found")

    files_for_manifest = [config_path] if config_path.is_file() else []
    files_for_manifest.extend(weights)
    manifest_rows = []
    for item in files_for_manifest:
        stat = item.stat()
        manifest_rows.append(
            {
                "path": item.relative_to(root).as_posix() if item != root and root in item.parents else item.name,
                "size": stat.st_size,
                "mtimeNs": stat.st_mtime_ns,
            }
        )
    manifest_sha256 = hashlib.sha256(
        json.dumps(manifest_rows, sort_keys=True, separators=(",", ":")).encode("utf8")
    ).hexdigest()

    content_sha256: str | None = None
    if compute_content_hash and weights:
        aggregate = hashlib.sha256()
        for weight in weights:
            aggregate.update(weight.relative_to(root).as_posix().encode("utf8"))
            aggregate.update(bytes.fromhex(_sha256_file(weight)))
        content_sha256 = aggregate.hexdigest()
    elif weights:
        warnings.append("Full checkpoint content hash was not requested; manifest hash covers paths, sizes, and mtimes.")

    inputs = config.get("input_features") or {}
    outputs = config.get("output_features") or {}
    robotworld = config.get("robotworld") if isinstance(config.get("robotworld"), dict) else {}
    cameras = [name for name in inputs if str(name).startswith("observation.images.")]
    if robotworld:
        adapter_revision = robotworld.get("embodimentAdapterRevision")
        if not isinstance(adapter_revision, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", adapter_revision):
            errors.append("config.robotworld.embodimentAdapterRevision is missing or invalid")
        action_representation = robotworld.get("actionRepresentation")
        if action_representation != "end_effector_local_delta":
            errors.append("config.robotworld.actionRepresentation must be 'end_effector_local_delta'")
        mapping = robotworld.get("cameraMapping")
        if not isinstance(mapping, dict) or set(mapping) != set(cameras):
            errors.append("config.robotworld.cameraMapping must bind every checkpoint camera key exactly once")
        elif set(mapping.values()) != {"front", "wrist"} or len(mapping) != 2:
            errors.append("config.robotworld.cameraMapping must map one camera to front and one to wrist")
        robot_hash = robotworld.get("trainedRobotDefinitionSha256")
        if not isinstance(robot_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", robot_hash):
            errors.append("config.robotworld.trainedRobotDefinitionSha256 must be a lowercase SHA-256")
        control_hz = robotworld.get("policyControlHz")
        if not isinstance(control_hz, int) or isinstance(control_hz, bool) or control_hz < 1 or control_hz > 100:
            errors.append("config.robotworld.policyControlHz must be an integer from 1 through 100")
    state_feature = inputs.get("observation.state") if isinstance(inputs, dict) else None
    state_shape = state_feature.get("shape") if isinstance(state_feature, dict) else None
    action = outputs.get("action") or {}
    action_shape = action.get("shape") if isinstance(action, dict) else None
    processor_files = [
        root / name
        for name in (
            "policy_preprocessor.json",
            "policy_preprocessor_step_3_normalizer_processor.safetensors",
            "policy_postprocessor.json",
            "policy_postprocessor_step_2_unnormalizer_processor.safetensors",
        )
        if (root / name).is_file()
    ]
    processor_digest = hashlib.sha256()
    for item in processor_files:
        processor_digest.update(item.name.encode("utf8"))
        processor_digest.update(bytes.fromhex(_sha256_file(item)))
    checkpoint_revision = _huggingface_revision(root)
    normalization_revision = processor_digest.hexdigest() if processor_files else None
    action_statistics: dict[str, list[float]] = {}
    stats_path = root / "policy_postprocessor_step_2_unnormalizer_processor.safetensors"
    try:
        action_statistics = _safetensor_f32_vectors(
            stats_path,
            ("action.mask", "action.min", "action.max", "action.q01", "action.q99"),
        )
    except OSError as exc:
        warnings.append(f"Could not inspect bounded action normalization metadata: {exc}")
    capabilities = {
        "pathKind": "directory" if path.is_dir() else "file",
        "weightFiles": len(weights),
        "weightBytes": sum(weight.stat().st_size for weight in weights),
        "configType": config.get("type"),
        "cameraKeys": cameras,
        "imageSize": config.get("resize_images_to"),
        "stateDimension": config.get("state_dim"),
        "stateFeaturePresent": isinstance(state_shape, list),
        "stateFeatureDimension": state_shape[0] if isinstance(state_shape, list) and state_shape else None,
        "actionDimension": config.get("action_dim") or (action_shape[0] if isinstance(action_shape, list) and action_shape else None),
        "actionHorizon": config.get("n_action_steps"),
        "gripperDimension": config.get("gripper_dim"),
        "preSnapGripper": config.get("pre_snap_gripper_action"),
        "binarizeGripper": config.get("binarize_gripper_action"),
        "worldModelEnabled": config.get("enable_world_model"),
        "checkpointProcessorSha256": normalization_revision,
        "normalizationRevision": normalization_revision,
        "actionNormalizationMode": (config.get("normalization_mapping") or {}).get("ACTION"),
        "actionNormalizationStatistics": action_statistics,
        "checkpointRepositoryRevision": checkpoint_revision,
        "embodimentAdapterRevision": robotworld.get("embodimentAdapterRevision"),
        "actionRepresentation": robotworld.get("actionRepresentation"),
        "cameraMapping": robotworld.get("cameraMapping") if isinstance(robotworld.get("cameraMapping"), dict) else {},
        "trainedRobotDefinitionSha256": robotworld.get("trainedRobotDefinitionSha256"),
        "policyControlHz": robotworld.get("policyControlHz"),
        "codeRevision": _git_revision(root),
        "validationWarnings": warnings,
    }
    return {
        "valid": not errors,
        "resolvedPath": str(path),
        "manifestSha256": manifest_sha256,
        "contentSha256": content_sha256,
        "modelRevision": str(
            config.get("_commit_hash")
            or capabilities["checkpointRepositoryRevision"]
            or capabilities["codeRevision"]
            or "unrecorded"
        ),
        "inputSchema": inputs if isinstance(inputs, dict) else {},
        "outputSchema": outputs if isinstance(outputs, dict) else {},
        "capabilities": capabilities,
        "errors": errors,
    }


def inspect_trellis(native_path: str, gguf_path: str, cpp_path: str = r"D:\trellis.cpp-v0.6.0-cuda12", dino_path: str = r"D:\DINOv3") -> list[dict[str, Any]]:
    native = Path(native_path).resolve()
    native_weights = _files(native / "ckpts", "**/*.safetensors")
    native_ready = (native / "pipeline.json").is_file() and bool(native_weights)
    dino = Path(dino_path).resolve()
    dino_ready = (dino / "config.json").is_file() and bool(_files(dino, "*.safetensors"))

    gguf = Path(gguf_path).resolve()
    gguf_weights = _files(gguf, "**/*.gguf")
    quant_tokens = sorted({match.group(1).upper() for file in gguf_weights if (match := re.search(r"_(f16|q\d(?:_\w+)?)\.gguf$", file.name, re.I))})
    if not quant_tokens and gguf_weights and (gguf.name.lower() == "q4" or any(part.lower() == "q4" for file in gguf_weights for part in file.parts)):
        quant_tokens = ["Q4"]
    cpp = Path(cpp_path).resolve()
    executables = _files(cpp, "**/*.exe")
    gguf_precision = ", ".join(quant_tokens) if quant_tokens else "unknown"
    is_actual_quant = any(token.startswith("Q") for token in quant_tokens)
    helper_names = {file.name.lower() for file in gguf_weights}
    q4_helpers = all(any(name.startswith(prefix) for name in helper_names) for prefix in ("dinov3", "ss_dec", "birefnet"))

    return [
        {
            "id": "trellis-native",
            "label": "TRELLIS.2 4B native",
            "path": str(native),
            "available": native_ready,
            "conditioningPath": str(dino),
            "conditioningReady": dino_ready,
            "precision": "BF16/FP16",
            "weightsBytes": _size(native_weights),
            "supportedResolutions": [512, 1024, 1536],
            "runtime": "Microsoft Python pipeline",
            "status": "ready_low_vram" if native_ready and dino_ready else "conditioning_missing" if native_ready else "missing",
            "notes": [
                "Official minimum is 24 GB VRAM; the detected 12 GB class GPU uses a slower low-VRAM path.",
                "Produces a static PBR GLB. Collision and articulation are separate compilation stages.",
            ],
        },
        {
            "id": "trellis-gguf",
            "label": "TRELLIS.2 GGUF",
            "path": str(gguf),
            "runtimePath": str(cpp),
            "available": bool(gguf_weights),
            "precision": gguf_precision,
            "actuallyQuantized": is_actual_quant,
            "conditioningReady": q4_helpers,
            "weightsBytes": _size(gguf_weights),
            "supportedResolutions": [512, 1024, 1536],
            "runtime": "trellis.cpp-compatible weights",
            "status": "runtime_missing" if gguf_weights and not executables else "incomplete" if executables and not q4_helpers else "installed_unverified" if executables else "missing",
            "blockers": (["No trellis.cpp server/CLI executable is installed in this model directory."] if not executables else []) + (["The trellis.cpp DINOv3/SS decoder/BiRefNet helper GGUF files are incomplete."] if gguf_weights and not q4_helpers else []),
            "notes": [
                f"The installed filenames identify these weights as {gguf_precision}; the folder name does not make them Q4.",
                "The server health endpoint passed, but the first full Q4 generation exited without a GLB; this runtime is inactive and not production-ready.",
                "A valid speed comparison requires the same source image, resolution, steps, warm/cold state, and a compatible runtime.",
            ],
        },
    ]
