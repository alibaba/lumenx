"""Verify Redis pending-entry recovery after a simulated worker handoff."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.apps.platform.central_queue import QueueJob, RedisStreamQueue


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.getenv("LUMENX_SKILL_REDIS_URL", "redis://127.0.0.1:6379/0"))
    parser.add_argument("--wait", type=int, default=2)
    args = parser.parse_args()
    stream = f"lumenx:recovery-smoke:{uuid4().hex[:8]}"
    first = RedisStreamQueue(args.url, stream=stream, group="lumenx-recovery-workers")
    job = QueueJob(f"recovery-{uuid4().hex}", {"tenant_id": "recovery-tenant"})
    first.enqueue(job)
    claimed = first.claim("crashed-worker", timeout=1)
    if claimed is None:
        return 1
    time.sleep(args.wait)
    replacement = RedisStreamQueue(args.url, stream=stream, group="lumenx-recovery-workers")
    recovered = replacement.claim("replacement-worker", timeout=1)
    if recovered is None or recovered.job_id != job.job_id:
        return 1
    replacement.ack("replacement-worker", recovered.job_id)
    print("redis recovery smoke: pending handoff observed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
