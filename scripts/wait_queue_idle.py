#!/usr/bin/env python3
"""Wait until job queue is idle (single-worker deploy)."""

from __future__ import annotations

import os
import sys
import time
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from env_config import load_env

load_env()

BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
WAIT = int(os.environ.get("QUEUE_IDLE_TIMEOUT", "120"))


def _headers() -> dict[str, str]:
    key = os.environ.get("API_KEY")
    return {"X-API-Key": key} if key else {}


def _ops() -> dict:
    req = urllib.request.Request(f"{BASE}/api/v1/ops/summary", headers=_headers())
    with urllib.request.urlopen(req, timeout=15) as resp:
        import json

        return json.loads(resp.read().decode())


def main() -> int:
    deadline = time.time() + WAIT
    while time.time() < deadline:
        ops = _ops()
        pending = int(ops.get("pending_jobs") or 0)
        processing = int(ops.get("processing_jobs") or 0)
        if pending == 0 and processing == 0:
            print("OK queue idle")
            return 0
        print(f"  waiting: pending={pending} processing={processing}")
        time.sleep(3)
    ops = _ops()
    print(
        f"FAIL queue not idle after {WAIT}s "
        f"(pending={ops.get('pending_jobs')} processing={ops.get('processing_jobs')})"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
