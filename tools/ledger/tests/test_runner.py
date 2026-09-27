"""runner.py 离线测试（T-B4 步骤 7）。

纪律：全走 mock- provider，db 一律 tmp_path 临时副本，绝不触碰
C:\\Users\\YY\\剧本项目\\ledger\\ledger.db；零网络请求。
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import prices as prices_mod  # noqa: E402
from db import Ledger  # noqa: E402
from runner import (  # noqa: E402
    MockAsyncProvider,
    ProviderError,
    RateGate,
    Runner,
    with_transient_retry,
)
from src.models.kling import KlingError  # noqa: E402


def make_ledger(tmp_path, shots=3, model="mock-video", duration=5):
    """临时账本 + N 个 draft VD 资产（模拟 import_storyboard 后的账本形态）。"""
    led = Ledger(str(tmp_path / "ledger.db"))
    for i in range(1, shots + 1):
        led.update(
            f"VD-{i:03d}", type="video", status="draft",
            description=f"第{i}镜",
            data={"video_prompt": f"shot {i}: slow push",
                  "kf_prompt": f"keyframe {i}",
                  "model": model, "duration": duration},
        )
    return led


def make_runner(led, tmp_path, *, fail_seq=(), poll_timeout=5.0, rate=None,
                price_table=None):
    state = str(tmp_path / "mock_server_state.json")
    prov = MockAsyncProvider(state, video_config={
        "fail_sequence": list(fail_seq)})
    gate = RateGate(rate, str(tmp_path / "rates.db")) if rate else None
    runner = Runner(led, prov,
                    price_table=price_table if price_table is not None
                    else prices_mod.load_prices(),
                    output_dir=str(tmp_path / "media"),
                    poll_interval=0.01, poll_timeout=poll_timeout,
                    stale_minutes=30.0, rate_gate=gate)
    return runner, prov


# ---------------------------------------------------------------------------
# a) 正常小批量：3 shot 跑通 + 成本累计
# ---------------------------------------------------------------------------
def test_a_happy_path_batch_and_cost(tmp_path):
    led = make_ledger(tmp_path, shots=3)
    runner, prov = make_runner(led, tmp_path)
    rep = runner.run()

    assert rep.submitted == ["VD-001", "VD-002", "VD-003"]
    assert rep.completed == ["KF-001", "VD-001", "KF-002", "VD-002", "KF-003", "VD-003"]
    assert rep.failed == [] and rep.errors == []
    assert prov.submit_calls == 3

    counts = led.counts()["by_status"]
    assert counts["done"] == 6  # 3 KF + 3 VD
    # 成本累计：mock-video 5s=0.5/条 + mock-image default=0.1/张 → 3*(0.5+0.1)=1.8
    assert abs(rep.cost_spent - 1.8) < 1e-9
    assert abs(rep.cost_total - 1.8) < 1e-9
    for i in (1, 2, 3):
        vd = led.get(f"VD-{i:03d}")
        assert vd["status"] == "done"
        assert vd["data"]["provider_task_id"].startswith("mock-task-")
        assert vd["content_hash"] and vd["output_path"]
        assert float(vd["cost"]) == 0.5
        assert os.path.exists(vd["output_path"])
    led.close()


# ---------------------------------------------------------------------------
# b) 失败注入 → 重跑；断点恢复只查单不重提交
# ---------------------------------------------------------------------------
def test_b1_fail_injection_then_retry_rerun(tmp_path):
    led = make_ledger(tmp_path, shots=2)
    runner1, prov1 = make_runner(led, tmp_path, fail_seq=["boom"])
    rep1 = runner1.run()
    # VD-001 的任务执行失败（fail_sequence 弹出）→ failed；VD-002 正常 done
    assert led.get("VD-001")["status"] == "failed"
    assert "VD-002" in rep1.completed
    assert led.get("VD-001")["data"]["provider_task_id"]  # task_id 已落库
    submit_after_run1 = prov1.submit_calls  # = 2（两个资产都提交过）

    # 重跑：--retry-failed → 先查单确认终态 → retry(attempt+1) → 重新提交成功
    runner2, prov2 = make_runner(led, tmp_path)
    rep2 = runner2.run(retry_failed=True)
    vd1 = led.get("VD-001")
    assert vd1["status"] == "done"
    assert vd1["attempt_count"] == 1        # failed→retry 恰好 +1
    assert prov2.submit_calls == 1          # 旧任务已确认 failed，重提交 1 次
    assert submit_after_run1 == 2
    led.close()


def test_b2_crash_recovery_polls_only_never_resubmits(tmp_path):
    """断点续跑核心：提交成功后进程"崩溃"（轮询未做）→ 重跑恢复扫描只查单，
    provider 提交调用计数不增。"""
    led = make_ledger(tmp_path, shots=1)
    # poll_timeout=0 → 提交后一轮轮询都不做，模拟提交后立即崩溃
    runner1, prov1 = make_runner(led, tmp_path, poll_timeout=0.0)
    rep1 = runner1.run()
    vd = led.get("VD-001")
    assert vd["status"] == "in_production"
    assert vd["data"]["provider_task_id"] == "mock-task-0001"
    assert rep1.submitted == ["VD-001"] and rep1.still_running == ["VD-001"]

    # 重启：全新 Runner/provider 实例（读同一服务端状态文件）
    runner2, prov2 = make_runner(led, tmp_path, poll_timeout=5.0)
    rep2 = runner2.run()
    vd = led.get("VD-001")
    assert vd["status"] == "done"
    assert rep2.recovered_inflight == ["VD-001"]
    assert prov2.submit_calls == 0          # ★ 只查单，不重提交
    assert len(json.load(open(runner2.provider.state_path,
                              encoding="utf-8"))["tasks"]) == 1  # 无新任务
    led.close()


def test_b3_guard_rejects_inflight_without_task_id(tmp_path):
    """防双写：in_production 无 task_id 且未超时 → 拒跑，资产不动。"""
    led = make_ledger(tmp_path, shots=1)
    # 手工制造"提交窗口期崩溃"形态：已 in_production 但 task_id 未落库
    led.update("VD-001", status="approved")
    led.update("VD-001", status="in_production")
    runner, prov = make_runner(led, tmp_path)
    rep = runner.run()
    assert rep.guard_inflight == ["VD-001"]
    assert rep.submitted == [] and prov.submit_calls == 0
    assert led.get("VD-001")["status"] == "in_production"  # 拒跑不流转
    led.close()


def test_b4_stale_inflight_orphan_reset(tmp_path):
    """超时孤儿：in_production 无 task_id 且 updated_at 超时 → 回收转 failed。"""
    led = make_ledger(tmp_path, shots=1)
    led.update("VD-001", status="approved")
    led.update("VD-001", status="in_production")
    # 把 updated_at 拨老（直接 SQL，绕过 update 的"变更才刷新"）
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "ledger.db"))
    conn.execute("UPDATE assets SET updated_at = ?", ("2020-01-01T00:00:00",))
    conn.commit(); conn.close()
    runner, prov = make_runner(led, tmp_path)
    rep = runner.run()
    assert rep.orphan_reset == ["VD-001"]
    assert led.get("VD-001")["status"] == "failed"
    led.close()


# ---------------------------------------------------------------------------
# c) attempt_count>=3 终态跳过转人工
# ---------------------------------------------------------------------------
def test_c_terminal_failed_skipped(tmp_path):
    led = make_ledger(tmp_path, shots=2)
    # VD-001 造到 attempt=3 的 failed（OpenCue DEAD 语义）
    led.update("VD-001", status="approved")
    led.update("VD-001", status="in_production")
    for _ in range(3):
        led.update("VD-001", status="failed")
        led.update("VD-001", status="retry")
    led.update("VD-001", status="failed")
    assert led.get("VD-001")["attempt_count"] == 3

    runner, prov = make_runner(led, tmp_path)
    rep = runner.run(retry_failed=True)     # 即使显式 retry 也不得解锁
    assert "VD-001" in rep.skipped_terminal
    assert prov.submit_calls == 1           # 只有 VD-002 正常提交
    assert led.get("VD-001")["status"] == "failed"  # 原样留给人工
    led.close()


# ---------------------------------------------------------------------------
# d) --max-budget 预算截断
# ---------------------------------------------------------------------------
def test_d_budget_guard_truncates(tmp_path):
    led = make_ledger(tmp_path, shots=4)
    runner, prov = make_runner(led, tmp_path)
    # mock-video 5s=0.5 + mock-image 0.1 = 0.6/shot；1.25 只够 2 shot
    rep = runner.run(max_budget=1.25)
    assert rep.budget_stop is True
    assert abs(rep.cost_spent - 1.2) < 1e-9
    assert led.get("VD-003")["status"] == "draft"   # 触顶资产完全未动
    assert led.get("VD-004")["status"] == "draft"
    assert prov.submit_calls == 2
    led.close()


# ---------------------------------------------------------------------------
# e) 限流桶跨实例共享（两个 runner 实例额度不翻倍）
# ---------------------------------------------------------------------------
def test_e_rate_bucket_shared_across_instances(tmp_path):
    import time

    from pyrate_limiter import MonotonicClock, RateItem

    led = make_ledger(tmp_path, shots=4)
    # rate=3/sec：两实例共享 3/s 额度；4 次提交必须至少跨一个完整窗口
    runner1, prov1 = make_runner(led, tmp_path, rate="3/sec")
    runner2, prov2 = make_runner(led, tmp_path, rate="3/sec")
    assert runner1.rate_gate.bucket is not runner2.rate_gate.bucket
    assert runner1.rate_gate.db_path == runner2.rate_gate.db_path

    t0 = time.time()
    rep1 = runner1.run(limit=2)             # 实例1 提交 2 次（耗 2 额度）
    mid = time.time() - t0
    rep2 = runner2.run(limit=2)             # 实例2 提交 2 次（同桶续用）
    total = time.time() - t0

    assert rep1.submitted == ["VD-001", "VD-002"]
    assert rep2.submitted == ["VD-003", "VD-004"]
    # 若额度翻倍（不共享），4 连发会在 ~0s 内完成；共享 3/s 则必然 ≥1s
    assert total - mid >= 0.5 and total >= 1.0, (
        f"两实例提交总耗时 {total:.2f}s，限流桶未共享（额度被翻倍）")
    # 桶层确定性断言（独立桶，无跨窗口时序干扰）：3/sec 连发 3 次后第 4 次必拒
    pure = RateGate("3/sec", str(tmp_path / "pure_rates.db"))
    for _ in range(3):
        pure.acquire()
    assert pure.bucket.put(RateItem("kling_submit",
                                    MonotonicClock().now())) is False
    led.close()


# ---------------------------------------------------------------------------
# 重试语义单测（tenacity 仅 retryable）
# ---------------------------------------------------------------------------
def test_retry_semantics(tmp_path):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderError("1302 rate limited", retryable=True)
        return "task-ok"

    assert with_transient_retry(flaky, multiplier=0.01, max_wait=0.02) == "task-ok"
    assert calls["n"] == 3

    calls["n"] = 0

    def params_err():
        calls["n"] += 1
        raise KlingError("1201 invalid param", retryable=False)

    with pytest.raises(KlingError):
        with_transient_retry(params_err, multiplier=0.01, max_wait=0.02)
    assert calls["n"] == 1                  # 不可重试：一次即穿透
