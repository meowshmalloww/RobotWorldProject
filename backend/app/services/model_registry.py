"""Read-only discovery for local RobotWorld model installations."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _files(path: Path, pattern: str) -> list[Path]:
    return sorted(value for value in path.glob(pattern) if value.is_file()) if path.is_dir() else []


def _size(files: list[Path]) -> int:
    return sum(value.stat().st_size for value in files)


def inspect_trellis(native_path: str, gguf_path: str) -> list[dict[str, Any]]:
    native = Path(native_path).resolve()
    native_weights = _files(native / "ckpts", "**/*.safetensors")
    native_ready = (native / "pipeline.json").is_file() and bool(native_weights)

    gguf = Path(gguf_path).resolve()
    gguf_weights = _files(gguf, "*.gguf")
    quant_tokens = sorted({match.group(1).upper() for file in gguf_weights if (match := re.search(r"_(f16|q\d(?:_\w+)?)\.gguf$", file.name, re.I))})
    executables = _files(gguf, "*.exe")
    gguf_precision = ", ".join(quant_tokens) if quant_tokens else "unknown"
    is_actual_quant = any(token.startswith("Q") for token in quant_tokens)

    return [
        {
            "id": "trellis-native",
            "label": "TRELLIS.2 4B native",
            "path": str(native),
            "available": native_ready,
            "precision": "BF16/FP16",
            "weightsBytes": _size(native_weights),
            "supportedResolutions": [512, 1024, 1536],
            "runtime": "Microsoft Python pipeline",
            "status": "ready_low_vram" if native_ready else "missing",
            "notes": [
                "Official minimum is 24 GB VRAM; the detected 12 GB class GPU uses a slower low-VRAM path.",
                "Produces a static PBR GLB. Collision and articulation are separate compilation stages.",
            ],
        },
        {
            "id": "trellis-gguf",
            "label": "TRELLIS.2 GGUF",
            "path": str(gguf),
            "available": bool(gguf_weights),
            "precision": gguf_precision,
            "actuallyQuantized": is_actual_quant,
            "weightsBytes": _size(gguf_weights),
            "supportedResolutions": [512, 1024, 1536],
            "runtime": "trellis.cpp-compatible weights",
            "status": "runtime_missing" if gguf_weights and not executables else "ready" if executables else "missing",
            "blockers": [] if executables else ["No trellis.cpp server/CLI executable is installed in this model directory."],
            "notes": [
                f"The installed filenames identify these weights as {gguf_precision}; the folder name does not make them Q4.",
                "A valid speed comparison requires the same source image, resolution, steps, warm/cold state, and a compatible runtime.",
            ],
        },
    ]
