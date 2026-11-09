#!/usr/bin/env python3
"""Verify background worker is alive (file heartbeat when Redis is off)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from env_config import load_env
from redis_client import worker_heartbeat_status

load_env()


def main() -> int:
    wait = int(__import__("os").environ.get("WORKER_VERIFY_WAIT", "20"))
    deadline = time.time() + wait
    last = None
    while time.time() < deadline:
        last = worker_heartbeat_status()
        if last.get("alive"):
            backend = last.get("backend", "?")
            age = last.get("last_seen_seconds_ago")
            print(f"OK worker alive (backend={backend}, last_seen={age}s ago)")
            return 0
        time.sleep(2)

    print("FAIL worker not alive")
    if last:
        print(f"  heartbeat: {last}")
    print("  Check minimized python worker window or run: python worker.py")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
