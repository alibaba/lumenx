"""Run an explicit PostgreSQL Skill queue enqueue/claim/ack smoke test."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from collections import Counter
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.apps.platform.central_queue import PostgresSkillQueue, QueueJob


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.getenv("LUMENX_SKILL_POSTGRES_URL"))
    parser.add_argument("--jobs", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--run-id", default=f"{uuid4().hex[:8]}")
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn or LUMENX_SKILL_POSTGRES_URL is required")
    queue = PostgresSkillQueue(args.dsn, table="platform_skill_queue_live_smoke")
    jobs = [
        QueueJob(
            f"live-pg-{args.run_id}-{i}",
            {"tenant_id": f"tenant-{i % 4}", "index": i},
        )
        for i in range(args.jobs)
    ]
    for job in jobs:
        queue.enqueue(job)

    def consume(worker_index: int) -> list[str]:
        worker_id = f"live-pg-worker-{worker_index}"
        claimed: list[str] = []
        while True:
            job = queue.claim(worker_id, timeout=1)
            if job is None:
                return claimed
            claimed.append(job.job_id)
            queue.ack(worker_id, job.job_id)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        claimed = [job_id for batch in pool.map(consume, range(args.workers)) for job_id in batch]
    counts = Counter(claimed)
    result = {
        "jobs": len(jobs),
        "claimed": len(claimed),
        "unique": len(counts),
        "duplicates": sum(v - 1 for v in counts.values() if v > 1),
        "workers": args.workers,
    }
    print(json.dumps(result, sort_keys=True))
    if result["claimed"] != args.jobs or result["unique"] != args.jobs or result["duplicates"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
