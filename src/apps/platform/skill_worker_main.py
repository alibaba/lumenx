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
from .worker_runtime import CredentialBroker, WorkerMetrics, credential_proxy_from_env


logger = logging.getLogger("lumenx.skill-worker")


def _queue(config: SkillQueueConfig):
    if config.backend == "redis":
        return RedisStreamQueue(config.redis_url or "", stream=config.queue_name)
    if config.backend == "postgres":
        return PostgresSkillQueue(config.postgres_url or "")
    raise RuntimeError("skill worker requires redis or postgres queue backend")


def _dry_run(job: QueueJob, *, worker_id: str = "", credential_proxy: CredentialBroker | None = None) -> None:
    forbidden = {"script", "command", "path", "secret", "credential_ref", "provider_handle"}
    if forbidden.intersection(job.payload):
        raise ValueError("skill worker job contains forbidden fields")
    provider = job.payload.get("provider")
    if provider is not None:
        if credential_proxy is None:
            raise ValueError("provider job requires credential proxy")
        credential_ref = credential_proxy.issue(
            worker_id=worker_id,
            job_id=job.job_id,
            provider=str(provider),
            scope=str(job.payload.get("credential_scope", "default")),
        )
        logger.info("credential broker issued ref=%s for job=%s", credential_ref, job.job_id)
    logger.info("validated dry-run Skill job %s", job.job_id)


def main() -> int:
    config = SkillQueueConfig.from_env()
    queue = _queue(config)
    worker_id = os.getenv("LUMENX_SKILL_WORKER_ID", socket.gethostname())
    metrics = WorkerMetrics(worker_id)
    credential_proxy = credential_proxy_from_env()

    def handler(job: QueueJob) -> None:
        metrics.record_claim(job.job_id)
        try:
            _dry_run(job, worker_id=worker_id, credential_proxy=credential_proxy)
        except Exception as exc:
            metrics.record_failure(exc)
            raise
        metrics.record_success()

    worker = IsolatedSkillWorker(queue, worker_id=worker_id, handler=handler)
    once = os.getenv("LUMENX_SKILL_WORKER_ONCE", "0") == "1"
    while True:
        handled = worker.run_once(timeout=5)
        if handled:
            logger.info("worker_metrics=%s", metrics.snapshot())
        if once:
            return 0
        time.sleep(0.1)


if __name__ == "__main__":
    raise SystemExit(main())
