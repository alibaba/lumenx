"""Minimal isolated Skill worker entrypoint for queue connectivity validation.

The default handler is dry-run only: it validates the job boundary and never
calls a Provider. Provider execution is a separate approved Core boundary.
"""

from __future__ import annotations

import logging
import os
import socket
import time

from .central_queue import IsolatedSkillWorker, PostgresSkillQueue, QueueJob, RedisStreamQueue
from .queue_backend import SkillQueueConfig


logger = logging.getLogger("lumenx.skill-worker")


def _queue(config: SkillQueueConfig):
    if config.backend == "redis":
        return RedisStreamQueue(config.redis_url or "", stream=config.queue_name)
    if config.backend == "postgres":
        return PostgresSkillQueue(config.postgres_url or "")
    raise RuntimeError("skill worker requires redis or postgres queue backend")


def _dry_run(job: QueueJob) -> None:
    forbidden = {"script", "command", "path", "secret", "credential_ref", "provider_handle"}
    if forbidden.intersection(job.payload):
        raise ValueError("skill worker job contains forbidden fields")
    logger.info("validated dry-run Skill job %s", job.job_id)


def main() -> int:
    config = SkillQueueConfig.from_env()
    queue = _queue(config)
    worker = IsolatedSkillWorker(queue, worker_id=os.getenv("LUMENX_SKILL_WORKER_ID", socket.gethostname()), handler=_dry_run)
    once = os.getenv("LUMENX_SKILL_WORKER_ONCE", "0") == "1"
    while True:
        worker.run_once(timeout=5)
        if once:
            return 0
        time.sleep(0.1)


if __name__ == "__main__":
    raise SystemExit(main())
