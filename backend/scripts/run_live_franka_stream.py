"""Exercise the live Worlds Franka stream against a running local API."""
from __future__ import annotations

import argparse
import base64
import json
from urllib.request import Request, urlopen

from websockets.sync.client import connect


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--asset-version-id")
    parser.add_argument("--instruction", default="Pick up the object and place it in the target.")
    parser.add_argument("--active-world-id")
    parser.add_argument("--task", choices=("pick_place", "drop_off_table"), default="pick_place")
    parser.add_argument("--seed", type=int, default=6203)
    args = parser.parse_args()
    payload = {
        "robotId": args.robot_id,
        "instruction": args.instruction,
        "backend": "mujoco",
        "controller": "oracle",
        "task": args.task,
        "seed": args.seed,
    }
    if args.asset_version_id:
        payload["assetVersionId"] = args.asset_version_id
    if args.active_world_id:
        payload["executionScope"] = "active_world"
        payload["worldId"] = args.active_world_id
    request = Request(
        f"{args.api}/api/worlds/live-sessions",
        data=json.dumps(payload).encode("utf8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        session = json.load(response)
    ws_url = args.api.replace("http://", "ws://").replace("https://", "wss://")
    frames = 0
    phases: set[str] = set()
    first_time = None
    last_time = None
    jpeg_bytes = 0
    asset_visual_frames = 0
    pbr_visual_frames = 0
    geometry_names: set[str] = set()
    evaluation = None
    with connect(f"{ws_url}/ws/worlds/live/{session['sessionId']}", open_timeout=10) as socket:
        while True:
            message = json.loads(socket.recv(timeout=120))
            if message["type"] == "frame":
                frames += 1
                phases.add(str(message["phase"]))
                first_time = message["simTimeSeconds"] if first_time is None else first_time
                last_time = message["simTimeSeconds"]
                jpeg_bytes += len(base64.b64decode(message["jpegBase64"]))
                geometries = ((message.get("state") or {}).get("renderGeometries") or [])
                geometry_names.update(str(item.get("name")) for item in geometries)
                if any(item.get("assetVersionId") for item in geometries):
                    asset_visual_frames += 1
                if any(item.get("sourcePbrTransform") for item in geometries):
                    pbr_visual_frames += 1
            elif message["type"] == "end":
                evaluation = message["evaluation"]
                break
            elif message["type"] == "error":
                raise RuntimeError(message["message"])
    result = {
        "sessionId": session["sessionId"],
        "frames": frames,
        "phases": sorted(phases),
        "firstSimTimeSeconds": first_time,
        "lastSimTimeSeconds": last_time,
        "jpegBytes": jpeg_bytes,
        "assetVisualFrames": asset_visual_frames,
        "pbrVisualFrames": pbr_visual_frames,
        "geometryNames": sorted(geometry_names),
        "evaluationId": evaluation.get("id") if evaluation else None,
        "success": evaluation.get("success") if evaluation else None,
        "status": evaluation.get("status") if evaluation else None,
    }
    print(json.dumps(result, indent=2))
    return 0 if frames >= 30 and len(phases) >= 5 and evaluation and evaluation.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
