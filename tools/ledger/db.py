"""SQLite(WAL) 账本存储层：assets + events 双表，单一写入口 update()。

设计抄自调研结论（docs/agents/raw/2026-09-13-survey-V3-asset-ledger.md §3）：
- MoneyPrinterTurbo state.py：写入口收敛为单一 update(asset_id, ...)，check-then-set 守卫，
  事务 + WAL 达到原子性；
- Zou create_from_import：同一 asset_id 重复登记 = upsert 更新，不报错、不产生重复行；
- Zou BaseMixin：created_at/updated_at 审计字段，字段/状态变更必刷 updated_at；
- OpenCue DEAD：attempt_count >= 3 后 failed 为终态，仅人工（force=True）可解锁。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager, nullcontext
from datetime import datetime
from typing import Any, Iterable, Iterator, Optional

from schema import (
    DATA_COLUMN,
    MANIFEST_COLUMNS,
    SCHEMA_VERSION,
    STATES,
    is_terminal,
    validate_transition,
)

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS assets (
    asset_id       TEXT PRIMARY KEY,
    type           TEXT NOT NULL,
    parent_ids     TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'draft',
    attempt_count  INTEGER NOT NULL DEFAULT 0,
    fingerprint    TEXT NOT NULL DEFAULT '',
    content_hash   TEXT NOT NULL DEFAULT '',
    output_path    TEXT NOT NULL DEFAULT '',
    cost           TEXT NOT NULL DEFAULT '0',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT '{SCHEMA_VERSION}',
    note           TEXT NOT NULL DEFAULT '',
    data           TEXT NOT NULL DEFAULT '{{}}'
);
CREATE INDEX IF NOT EXISTS ix_assets_type_status ON assets(type, status);
CREATE INDEX IF NOT EXISTS ix_assets_fingerprint ON assets(fingerprint);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    asset_id    TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    actor       TEXT NOT NULL DEFAULT 'system',
    detail      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_events_asset ON events(asset_id);
"""


class LedgerError(Exception):
    """账本写入错误。code 为机器可读错误码（如 illegal_transition / terminal_locked）。"""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dump_data(data: Optional[dict]) -> str:
    if not data:
        return "{}"
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


class Ledger:
    """账本句柄。所有写入必须走 update()（或其批量形态 update_many）。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._batch_depth = 0
        self._conn = sqlite3.connect(db_path, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        # WAL：多会话读不阻塞写；synchronous=NORMAL 是 WAL 下的推荐档位
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """批量原子写：with ledger.transaction(): 内多次 update 全部成功或全部回滚。

        sqlite3 无真嵌套事务：批量内 update() 不再各自开启连接级事务，
        只有最外层 transaction() 持有 `with self._conn`（成功 commit / 异常 rollback）。
        """
        self._batch_depth += 1
        try:
            if self._batch_depth == 1:
                with self._conn:
                    yield
            else:
                yield
        finally:
            self._batch_depth -= 1

    # ------------------------------------------------------------------
    # 唯一写入口
    # ------------------------------------------------------------------
    def update(
        self,
        asset_id: str,
        *,
        type: Optional[str] = None,
        parent_ids: str = "",
        description: str = "",
        source: str = "",
        status: Optional[str] = None,
        fingerprint: str = "",
        content_hash: str = "",
        output_path: str = "",
        cost: Optional[str] = None,
        note: Optional[str] = None,
        data: Optional[dict] = None,
        actor: str = "system",
        detail: str = "",
        force: bool = False,
    ) -> dict:
        """登记或更新一个资产（upsert），返回更新后的完整行。

        - asset_id 不存在 → 新登记（insert），status 缺省 draft；
        - asset_id 已存在 → 只补写显式提供的字段（patch），状态变化走流转校验；
        - 终态行（archived / failed 且 attempt>=3）拒绝改状态，force=True 表示人工介入；
        - failed→retry 自动 attempt_count+1（Zou retake_count 语义）；
        - 每次实际变更追加 events 流水并刷新 updated_at；无变化则不动（幂等）。
        """
        now = _now()
        # 批量事务内不开自己的连接级事务（sqlite3 无嵌套事务，内层 commit 会使外层回滚失效）
        tx = nullcontext() if self._batch_depth else self._conn
        with tx:
            row = self._conn.execute(
                "SELECT * FROM assets WHERE asset_id = ?", (asset_id,)
            ).fetchone()

            if row is None:
                new_status = status or "draft"
                if new_status not in STATES:
                    raise LedgerError(f"unknown_status:{new_status}")
                if type is None:
                    raise LedgerError("type_required_on_insert", f"{asset_id}: 新登记必须提供 type")
                cursor = self._conn.execute(
                    "INSERT INTO assets (asset_id, type, parent_ids, description, source,"
                    " status, attempt_count, fingerprint, content_hash, output_path, cost,"
                    " created_at, updated_at, schema_version, note, data)"
                    " VALUES (?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?)",
                    (
                        asset_id, type, parent_ids, description, source, new_status,
                        fingerprint, content_hash, output_path,
                        cost if cost is not None else "0",
                        now, now, SCHEMA_VERSION,
                        note if note is not None else "",
                        _dump_data(data),
                    ),
                )
                if cursor.rowcount != 1:
                    raise LedgerError("insert_failed", asset_id)
                self._log_event(now, asset_id, None, new_status, actor,
                                detail or "registered")
                return self.get(asset_id)  # type: ignore[return-value]

            # ---- 已存在：patch + 流转校验 ----
            old = dict(row)
            fields: dict[str, Any] = {}
            if type is not None and type != old["type"]:
                fields["type"] = type
            for name, val in (
                ("parent_ids", parent_ids), ("description", description),
                ("source", source), ("fingerprint", fingerprint),
                ("content_hash", content_hash), ("output_path", output_path),
            ):
                if val and val != old[name]:
                    fields[name] = val
            if cost is not None and cost != old["cost"]:
                fields["cost"] = cost
            if note is not None and note != old["note"]:
                fields["note"] = note
            if data is not None and _dump_data(data) != old[DATA_COLUMN]:
                fields[DATA_COLUMN] = _dump_data(data)

            from_status = old["status"]
            to_status = status if (status and status != from_status) else None
            attempt_delta = 0

            if to_status is not None:
                ok, reason = validate_transition(from_status, to_status, old["attempt_count"])
                if not ok and force and reason.startswith("terminal_locked"):
                    # 人工介入解锁（OpenCue DEAD 只能人工处理）
                    reason = "manual_override"
                elif not ok:
                    raise LedgerError(reason, f"{asset_id}: {from_status}->{to_status}")
                if to_status == "retry" and from_status == "failed":
                    attempt_delta = 1
                fields["status"] = to_status

            if not fields and attempt_delta == 0:
                return old  # 无变化，幂等返回现状

            if attempt_delta:
                fields["attempt_count"] = old["attempt_count"] + attempt_delta

            sets = ", ".join(f"{name} = ?" for name in fields)
            params = list(fields.values()) + [now, asset_id]
            self._conn.execute(
                f"UPDATE assets SET {sets}, updated_at = ? WHERE asset_id = ?", params
            )
            self._log_event(
                now, asset_id,
                from_status, to_status, actor,
                detail or ("; ".join(sorted(fields)) or "noop"),
            )
            return self.get(asset_id)  # type: ignore[return-value]

    def update_many(self, rows: Iterable[dict], actor: str = "system") -> list[dict]:
        """批量 upsert：单事务，全成或全回滚（导入原子性）。"""
        results: list[dict] = []
        with self.transaction():
            for row in rows:
                payload = dict(row)
                aid = payload.pop("asset_id")
                results.append(self.update(aid, actor=actor, **payload))
        return results

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def get(self, asset_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM assets WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        return self._to_dict(row) if row else None

    def query(self, *, type: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM assets WHERE 1=1"
        params: list[Any] = []
        if type is not None:
            sql += " AND type = ?"
            params.append(type)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY asset_id"
        return [self._to_dict(r) for r in self._conn.execute(sql, params)]

    def counts(self) -> dict:
        """统计：by_status / by_type / total（导入报告与验收用）。"""
        by_status = {s: 0 for s in STATES}
        by_type: dict[str, int] = {}
        total = 0
        for r in self._conn.execute("SELECT type, status, COUNT(*) AS n FROM assets GROUP BY type, status"):
            by_status[r["status"]] = by_status.get(r["status"], 0) + r["n"]
            by_type[r["type"]] = by_type.get(r["type"], 0) + r["n"]
            total += r["n"]
        return {"total": total, "by_status": by_status, "by_type": dict(sorted(by_type.items()))}

    def events(self, asset_id: Optional[str] = None) -> list[dict]:
        if asset_id is None:
            cur = self._conn.execute("SELECT * FROM events ORDER BY id")
        else:
            cur = self._conn.execute("SELECT * FROM events WHERE asset_id = ? ORDER BY id", (asset_id,))
        return [dict(r) for r in cur]

    # ------------------------------------------------------------------
    def _log_event(self, at: str, asset_id: str, from_status: Optional[str],
                   to_status: Optional[str], actor: str, detail: str) -> None:
        self._conn.execute(
            "INSERT INTO events (at, asset_id, from_status, to_status, actor, detail)"
            " VALUES (?,?,?,?,?,?)",
            (at, asset_id, from_status, to_status, actor, detail),
        )

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d[DATA_COLUMN] = json.loads(d.get(DATA_COLUMN) or "{}")
        except json.JSONDecodeError:
            pass
        return d
