"""Read-only discovery for local RobotWorld model installations."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _files(path: Path, pattern: str) -> list[Path]:
    return sorted(value for value in path.glob(pattern) if value.is_file()) if path.is_dir() else []


def _size(files: list[Path]) -> int:
    return sum(value.stat().st_size for value in files)


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
