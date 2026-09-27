"""db 层：单一 update() 入口的 upsert / 幂等 / 流转校验 / 终态锁 / events 流水 / WAL。"""

import pytest

from db import Ledger, LedgerError
from schema import DATA_COLUMN


@pytest.fixture()
def ledger(tmp_path):
    led = Ledger(str(tmp_path / "ledger.db"))
    yield led
    led.close()


def test_wal_mode_enabled(ledger, tmp_path):
    mode = ledger._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    assert (tmp_path / "ledger.db-wal").exists() or mode == "wal"


def test_update_is_single_write_entry_upsert(ledger):
    """同一 asset_id 重复登记 = 更新而非报错/重复行（Zou create_from_import 语义）。"""
    first = ledger.update("SB-001", type="storyboard", description="v1", source="script")
    assert first["status"] == "draft"
    second = ledger.update("SB-001", description="v2", source="script")
    assert second["description"] == "v2"
    rows = ledger.query(type="storyboard")
    assert len(rows) == 1                      # 没有重复行
    assert rows[0]["created_at"] == first["created_at"]
    assert rows[0]["updated_at"] >= first["updated_at"]


def test_noop_update_writes_no_event(ledger):
    ledger.update("CH-01", type="character", description="梁宸", source="script")
    n0 = len(ledger.events("CH-01"))
    ledger.update("CH-01", description="梁宸", source="script")  # 全部同值
    assert len(ledger.events("CH-01")) == n0    # 幂等：无变化不落流水


def test_insert_requires_type(ledger):
    with pytest.raises(LedgerError) as ei:
        ledger.update("SB-002", status="done")
    assert ei.value.code == "type_required_on_insert"


def test_illegal_transition_rejected(ledger):
    ledger.update("SB-001", type="storyboard", source="script")
    with pytest.raises(LedgerError) as ei:
        ledger.update("SB-001", status="done")   # draft 不能直达 done
    assert ei.value.code.startswith("illegal_transition")


def test_attempt_count_increments_on_failed_to_retry(ledger):
    ledger.update("SB-001", type="storyboard", source="script", status="approved")
    ledger.update("SB-001", status="in_production")
    ledger.update("SB-001", status="failed")
    ledger.update("SB-001", status="retry")
    assert ledger.get("SB-001")["attempt_count"] == 1
    for _ in range(2):
        ledger.update("SB-001", status="in_production")
        ledger.update("SB-001", status="failed")
        ledger.update("SB-001", status="retry")
    assert ledger.get("SB-001")["attempt_count"] == 3


def test_terminal_lock_after_three_attempts_manual_override(ledger):
    ledger.update("SB-001", type="storyboard", source="script", status="approved")
    ledger.update("SB-001", status="in_production")
    for _ in range(3):
        ledger.update("SB-001", status="failed")
        ledger.update("SB-001", status="retry")
        ledger.update("SB-001", status="in_production")
    ledger.update("SB-001", status="failed")     # attempt=3，failed 应为终态

    with pytest.raises(LedgerError) as ei:
        ledger.update("SB-001", status="retry")  # 脚本不得解锁
    assert ei.value.code.startswith("terminal_locked")

    ledger.update("SB-001", status="retry", force=True, actor="human")  # 仅人工可解
    row = ledger.get("SB-001")
    assert row["status"] == "retry"
    assert any(e["actor"] == "human" for e in ledger.events("SB-001"))


def test_archived_is_hard_terminal_even_with_force(ledger):
    ledger.update("SB-001", type="storyboard", source="script")
    ledger.update("SB-001", status="approved")
    ledger.update("SB-001", status="in_production")
    ledger.update("SB-001", status="done")
    ledger.update("SB-001", status="archived")
    with pytest.raises(LedgerError):
        ledger.update("SB-001", status="draft", force=True)  # 无出边，force 也不放行


def test_data_json_roundtrip(ledger):
    ledger.update("SB-001", type="storyboard", source="script",
                  data={"episode": 1, "s_no": 1, "dialogues": [{"speaker": "梁宸"}]})
    d = ledger.get("SB-001")[DATA_COLUMN]
    assert d["episode"] == 1 and d["dialogues"][0]["speaker"] == "梁宸"


def test_update_many_atomic_rollback(ledger):
    ledger.update("SB-001", type="storyboard", source="script")
    rows = [
        {"asset_id": "SB-001", "description": "batch-patched"},
        {"asset_id": "SB-002", "status": "done"},  # 新登记缺 type → 整批应回滚
    ]
    with pytest.raises(LedgerError):
        ledger.update_many(rows)
    assert ledger.get("SB-001")["description"] == ""   # 回滚生效
    assert ledger.get("SB-002") is None
    ok_rows = [{"asset_id": "SB-001", "description": "ok"},
               {"asset_id": "SB-002", "type": "storyboard", "source": "script"}]
    out = ledger.update_many(ok_rows)
    assert len(out) == 2 and ledger.counts()["total"] == 2


def test_counts_by_type_and_status(ledger):
    ledger.update("SB-001", type="storyboard", source="script")
    ledger.update("SB-002", type="storyboard", source="script", status="approved")
    ledger.update("CH-01", type="character", source="script")
    c = ledger.counts()
    assert c["total"] == 3
    assert c["by_type"] == {"character": 1, "storyboard": 2}
    assert c["by_status"]["draft"] == 2 and c["by_status"]["approved"] == 1
