"""Centralized Skill run queue adapters and isolated worker runner."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Callable, Protocol


class QueueConnectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class QueueJob:
    job_id: str
    payload: dict[str, Any]
    lease_seconds: int = 300


class CentralSkillQueue(Protocol):
    def enqueue(self, job: QueueJob) -> str: ...
    def claim(self, worker_id: str, timeout: int = 5) -> QueueJob | None: ...
    def ack(self, worker_id: str, job_id: str) -> None: ...
    def requeue(self, worker_id: str, job: QueueJob) -> None: ...


class RedisStreamQueue:
    """Redis Streams adapter using consumer groups and explicit ack."""

    def __init__(self, url: str, *, stream: str = "lumenx:skill-runs", group: str = "lumenx-workers"):
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise QueueConnectionError("redis package is required for Redis queue") from exc
        self.client = redis.Redis.from_url(url, decode_responses=True)
        self.stream, self.group = stream, group
        self._message_ids: dict[str, str] = {}
        try:
            self.client.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise QueueConnectionError("cannot initialize Redis consumer group") from exc

    def enqueue(self, job: QueueJob) -> str:
        return str(self.client.xadd(self.stream, {"job_id": job.job_id, "payload": json.dumps(job.payload, ensure_ascii=False), "lease_seconds": job.lease_seconds}))

    def claim(self, worker_id: str, timeout: int = 5) -> QueueJob | None:
        try:
            rows = self.client.xreadgroup(self.group, worker_id, {self.stream: ">"}, count=1, block=max(0, timeout * 1000))
        except Exception as exc:
            if exc.__class__.__name__ == "TimeoutError":
                return None
            raise
        if not rows: return None
        _stream, messages = rows[0]; message_id, fields = messages[0]
        payload = json.loads(fields["payload"])
        self._message_ids[fields["job_id"]] = message_id
        return QueueJob(fields["job_id"], payload, int(fields.get("lease_seconds", 300)))

    def ack(self, worker_id: str, job_id: str) -> None:
        message_id = self._message_ids.pop(job_id, None)
        if message_id is None: return
        self.client.xack(self.stream, self.group, message_id)

    def requeue(self, worker_id: str, job: QueueJob) -> None:
        self.enqueue(job)


class PostgresSkillQueue:
    """PostgreSQL adapter using row locks and SKIP LOCKED claims."""

    def __init__(self, dsn: str, *, table: str = "platform_skill_queue"):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise QueueConnectionError("psycopg is required for PostgreSQL queue") from exc
        self.psycopg = psycopg; self.dsn = dsn; self.table = table
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {self.table} (job_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, status TEXT NOT NULL, worker_id TEXT, lease_until TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")

    def enqueue(self, job: QueueJob) -> str:
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(f"INSERT INTO {self.table}(job_id,payload_json,status) VALUES (%s,%s,'queued') ON CONFLICT (job_id) DO NOTHING", (job.job_id, json.dumps({"payload": job.payload, "lease_seconds": job.lease_seconds}, ensure_ascii=False)))
        return job.job_id

    def claim(self, worker_id: str, timeout: int = 5) -> QueueJob | None:
        with self.psycopg.connect(self.dsn) as conn:
            row = conn.execute(f"SELECT job_id,payload_json FROM {self.table} WHERE status='queued' OR (status='running' AND lease_until < NOW()) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
            if not row: return None
            conn.execute(f"UPDATE {self.table} SET status='running',worker_id=%s,lease_until=NOW() + (%s * INTERVAL '1 second') WHERE job_id=%s", (worker_id, timeout, row[0]))
        raw = json.loads(row[1]); return QueueJob(row[0], raw["payload"], int(raw.get("lease_seconds", timeout)))

    def ack(self, worker_id: str, job_id: str) -> None:
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(f"UPDATE {self.table} SET status='succeeded',lease_until=NULL WHERE job_id=%s AND worker_id=%s", (job_id, worker_id))

    def requeue(self, worker_id: str, job: QueueJob) -> None:
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(f"UPDATE {self.table} SET status='queued',worker_id=NULL,lease_until=NULL WHERE job_id=%s AND worker_id=%s", (job.job_id, worker_id))


class IsolatedSkillWorker:
    """Queue runner; handler must be a restricted, side-effect-aware callable."""

    def __init__(self, queue: CentralSkillQueue, *, worker_id: str, handler: Callable[[QueueJob], None]):
        self.queue, self.worker_id, self.handler = queue, worker_id, handler

    def run_once(self, timeout: int = 5) -> bool:
        job = self.queue.claim(self.worker_id, timeout=timeout)
        if job is None: return False
        try:
            self.handler(job)
        except Exception:
            self.queue.requeue(self.worker_id, job)
            raise
        self.queue.ack(self.worker_id, job.job_id)
        return True

