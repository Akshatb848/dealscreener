#!/usr/bin/env python3
"""Production smoke test: Redis, API health, optional async upload."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from env_config import load_env

load_env()


def _headers() -> dict[str, str]:
    import os

    key = os.environ.get("API_KEY")
    return {"X-API-Key": key} if key else {}


def _get(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _post_multipart(url: str, fields: dict, file_field: str, filename: str, content: bytes) -> dict:
    import uuid

    boundary = uuid.uuid4().hex
    body = []
    for key, value in fields.items():
        body.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n")
    body.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
        f"Content-Type: text/plain\r\n\r\n"
    )
    payload = "".join(body).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **_headers()},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser(description="Deal Screener smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument(
        "--job-timeout",
        type=int,
        default=int(os.environ.get("SMOKE_JOB_TIMEOUT", "600")),
        help="Seconds to wait for async upload job (LLM eval can take several minutes)",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    errors: list[str] = []

    from redis_client import ping_redis, redis_required, redis_status, worker_heartbeat_status

    def _wait_worker_alive(seconds: int = 20) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if worker_heartbeat_status().get("alive"):
                return True
            time.sleep(2)
        return False

    ok, detail = ping_redis()
    if ok:
        print(f"Redis: OK ({detail})")
        print(f"  queue_depth={redis_status().get('queue_depth')}")
    elif redis_required():
        print(f"Redis: FAIL ({detail})")
        errors.append(f"Redis: {detail}")
    else:
        print(f"Redis: SKIP - DB queue fallback ({detail})")

    for path in ("/health", "/readiness", "/api/v1/config/status", "/api/v1/ops/summary"):
        try:
            data = _get(f"{base}{path}")
            print(f"GET {path}: OK")
            if path.endswith("ops/summary"):
                hb = data.get("worker_heartbeat") or {}
                print(
                    f"  pending_jobs={data.get('pending_jobs')} "
                    f"processing_jobs={data.get('processing_jobs')} "
                    f"worker_alive={hb.get('alive')} "
                    f"heartbeat_backend={hb.get('backend')} "
                    f"redis_queue={data.get('redis_queue_depth')}"
                )
                if not hb.get("alive"):
                    print("  waiting for worker heartbeat...")
                    if _wait_worker_alive(20):
                        data = _get(f"{base}/api/v1/ops/summary")
                        hb = data.get("worker_heartbeat") or {}
                        print(f"  worker_alive={hb.get('alive')} backend={hb.get('backend')}")
                    if not hb.get("alive"):
                        errors.append("worker not alive - worker.py may have crashed on startup")
        except Exception as exc:
            print(f"GET {path}: FAIL ({exc})")
            errors.append(f"{path}: {exc}")

    if not args.skip_upload:
        try:
            ops = _get(f"{base}/api/v1/ops/summary")
            pending = int(ops.get("pending_jobs") or 0)
            processing = int(ops.get("processing_jobs") or 0)
            if pending or processing:
                print(
                    f"  WARN queue not empty before upload "
                    f"(pending={pending} processing={processing}) - waiting..."
                )
                idle_deadline = time.time() + 120
                while time.time() < idle_deadline and (pending or processing):
                    time.sleep(3)
                    ops = _get(f"{base}/api/v1/ops/summary")
                    pending = int(ops.get("pending_jobs") or 0)
                    processing = int(ops.get("processing_jobs") or 0)
                if pending or processing:
                    errors.append(
                        f"queue blocked before smoke upload "
                        f"(pending={pending} processing={processing}) - run recover_stale_jobs.py --drain"
                    )

            content = f"Smoke test pitch {uuid.uuid4().hex}. Revenue $2M ARR.".encode()
            upload = _post_multipart(
                f"{base}/api/v1/upload?async_processing=true",
                {"company_name": f"SmokeTest-{uuid.uuid4().hex[:8]}"},
                "file",
                f"smoke_{uuid.uuid4().hex[:8]}.txt",
                content,
            )
            print(f"POST /api/v1/upload: OK job_id={upload.get('job_id')}")
            job_id = upload.get("job_id")
            if job_id:
                deadline = time.time() + args.job_timeout
                final = "pending"
                while time.time() < deadline:
                    job = _get(f"{base}/api/v1/jobs/{job_id}")
                    final = job.get("status", "pending")
                    if int(time.time()) % 15 < 3:
                        ops = _get(f"{base}/api/v1/ops/summary")
                        print(
                            f"  job status={final} "
                            f"(queue pending={ops.get('pending_jobs')} processing={ops.get('processing_jobs')})"
                        )
                    else:
                        print(f"  job status={final}")
                    if final in {"completed", "failed"}:
                        break
                    time.sleep(3)
                if final not in {"completed", "failed"}:
                    ops = _get(f"{base}/api/v1/ops/summary")
                    hb = ops.get("worker_heartbeat") or {}
                    if hb.get("alive") and final == "processing":
                        print("  WARN job still processing but worker heartbeat alive - may need more time")
                    errors.append(
                        f"job did not finish within {args.job_timeout}s (last status={final})"
                    )
                elif final == "failed":
                    job = _get(f"{base}/api/v1/jobs/{job_id}")
                    errors.append(f"job failed: {(job.get('message') or '')[:200]}")
                elif final == "completed":
                    print("  OK job completed")
        except Exception as exc:
            print(f"POST /api/v1/upload: FAIL ({exc})")
            errors.append(f"upload: {exc}")

    if errors:
        print("\nSMOKE TEST FAILED")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("\nSMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
