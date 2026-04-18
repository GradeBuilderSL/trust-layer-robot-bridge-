#!/usr/bin/env python3
"""Collects an E1 startup snapshot from the local HTTP stack.

The script is intentionally dependency-free so it can run on a fresh Jetson
before the full Python environment is prepared.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def wait_for_ready(url: str, timeout_s: float) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last_error = None
    while time.time() < deadline:
        try:
            health = fetch_json(url)
            if health.get("transport_ready"):
                return health
            last_error = health
        except Exception as exc:  # pragma: no cover - best-effort bootstrap path
            last_error = {"error": str(exc)}
        time.sleep(1.0)
    if isinstance(last_error, dict):
        return last_error
    return {"error": "timeout waiting for transport"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot-url", default="http://127.0.0.1:8083")
    parser.add_argument("--bridge-url", default="http://127.0.0.1:8080")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--wait-sec", type=float, default=15.0)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    robot_health = wait_for_ready(f"{args.robot_url}/health", args.wait_sec)

    snapshot = {
        "collected_at": timestamp,
        "robot_url": args.robot_url,
        "bridge_url": args.bridge_url,
        "robot_health": robot_health,
    }

    for name, url in (
        ("robot_state", f"{args.robot_url}/api/state"),
        ("robot_capabilities", f"{args.robot_url}/api/capabilities"),
        ("bridge_health", f"{args.bridge_url}/health"),
        ("bridge_brain", f"{args.bridge_url}/brain/status"),
    ):
        try:
            snapshot[name] = fetch_json(url)
        except urllib.error.URLError as exc:
            snapshot[name] = {"error": str(exc)}
        except Exception as exc:  # pragma: no cover - bootstrap diagnostics
            snapshot[name] = {"error": str(exc)}

    output_path = output_dir / f"telemetry_{timestamp}.json"
    output_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {
        "file": str(output_path),
        "transport_ready": snapshot["robot_health"].get("transport_ready"),
        "transport_error": snapshot["robot_health"].get("transport_error"),
        "status_received": snapshot.get("robot_state", {}).get("status_received"),
        "mode_e1": snapshot.get("robot_state", {}).get("mode_e1"),
        "motors_count": snapshot.get("robot_state", {}).get("motors_count"),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
