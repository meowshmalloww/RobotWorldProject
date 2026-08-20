"""Small Windows-native performance sampler with no extra resident package."""
from __future__ import annotations

import ctypes
import subprocess
import time
from typing import Any


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

    @property
    def value(self) -> int:
        return (self.dwHighDateTime << 32) | self.dwLowDateTime


_previous_cpu: tuple[int, int] | None = None
_gpu_cache: tuple[float, dict[str, Any]] = (0.0, {"available": False})


def _cpu_percent() -> float | None:
    global _previous_cpu
    idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    total = kernel.value + user.value
    busy = total - idle.value
    previous = _previous_cpu
    _previous_cpu = (busy, total)
    if previous is None or total <= previous[1]:
        return None
    return round(max(0.0, min(100.0, 100 * (busy - previous[0]) / (total - previous[1]))), 1)


def _memory() -> dict[str, float]:
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {"usedGb": 0.0, "totalGb": 0.0, "percent": 0.0}
    total = status.ullTotalPhys / (1024 ** 3)
    used = (status.ullTotalPhys - status.ullAvailPhys) / (1024 ** 3)
    return {"usedGb": round(used, 1), "totalGb": round(total, 1), "percent": round(100 * used / total, 1) if total else 0.0}


def _gpu() -> dict[str, Any]:
    global _gpu_cache
    now = time.monotonic()
    if now - _gpu_cache[0] < 2:
        return _gpu_cache[1]
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, check=True,
        )
        fields = [item.strip() for item in completed.stdout.splitlines()[0].split(",")]
        result: dict[str, Any] = {"available": True, "name": fields[0], "memoryUsedMb": int(fields[1]), "memoryTotalMb": int(fields[2]), "utilizationPercent": int(fields[3])}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        result = {"available": False}
    _gpu_cache = (now, result)
    return result


def snapshot() -> dict[str, Any]:
    return {"cpuPercent": _cpu_percent(), "memory": _memory(), "gpu": _gpu(), "sampledAt": time.time()}
