"""账本驱动的批量生成 runner（产线发动机）——提交 / 轮询 / 断点续跑。

设计依据 docs/agents/raw/2026-09-13-survey-V4-batch-orchestration.md §3：
- 处理单元 = 账本资产（默认 VD 视频）；draft→in_production→done/failed 全程
  Ledger.update() 单入口，7 态机流转白名单由 schema 层强制；
- 幂等键 = fingerprint（sha256 前 16 位）：同指纹且已有 done 资产 ⇒ skip，不新增状态；
- provider_task_id 提交成功立即落 assets.data（崩溃窗口最小化，openai-python 幂等键
  语义的客户端账本版）；
- 断点续跑：启动扫描 in_production 有 task_id ⇒ 只查单（GET /tasks?task_ids=）不重提交；
  in_production 无 task_id 且 updated_at 未超时 ⇒ 拒跑防双写（另一实例可能在提交窗口）；
- attempt_count>=3 终态跳过转人工（schema.is_terminal，OpenCue DEAD 语义）；
- KF/VD 子资产懒登记：处理 VD 时前置 KF 不存在/未完成则先登记并生成（Ledger.update）。

provider 两阶段抽象（submit/poll 对齐可灵异步语义）：
- MockAsyncProvider：包装 src/models/mock.py 的 MockModel/MockImageModel，用 JSON 文件
  模拟 provider 服务端任务状态（跨进程持久 ⇒ 断点续跑可离线测试），全程零网络；
- KlingBatchProvider：提交/查单/下载三段式（复用 kling.py 的 Bearer 头与错误分类），
  批量查单 GET /tasks?task_ids=，无 key 不可用（构造即校验）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

import tenacity

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from db import Ledger  # noqa: E402
from schema import PREFIX_TO_TYPE, is_terminal  # noqa: E402

logger = logging.getLogger("ledger.runner")

import prices as prices_mod  # noqa: E402
from pyrate_limiter import Duration, MonotonicClock, Rate, RateItem  # noqa: E402
from pyrate_limiter import SQLiteBucket  # noqa: E402
from src.models.kling import KlingError  # noqa: E402,F401  (retryable 语义判定, 重试层用)
from src.models.mock import MockError, MockImageModel, MockModel  # noqa: E402

# provider 侧任务归一状态（对齐 vidu.py _map_status 思想：账本只存归一态）
POLL_QUEUED = "queued"
POLL_RUNNING = "running"
POLL_SUCCEEDED = "succeeded"
POLL_FAILED = "failed"


class ProviderError(RuntimeError):
    """provider 提交/轮询层错误。retryable 对齐 KlingError.retryable：
    仅官方限频/超并发/服务端/网络类可重试；参数/账户/政策类重试纯烧时间。"""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class PollResult:
    """单次查单的归一状态。"""

    status: str                     # queued / running / succeeded / failed
    video_url: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# 限流（PyRateLimiter SQLite 持久桶：额度跨重启/跨实例保留）
# ---------------------------------------------------------------------------
_RATE_UNITS = {
    "s": Duration.SECOND, "sec": Duration.SECOND, "second": Duration.SECOND,
    "m": Duration.MINUTE, "min": Duration.MINUTE, "minute": Duration.MINUTE,
    "h": Duration.HOUR, "hr": Duration.HOUR, "hour": Duration.HOUR,
}


def parse_rate(rate: str) -> tuple[int, Any]:
    """"6/min" / "30/sec" / "100/hour" → (count, Duration)。"""
    count, _, unit = rate.partition("/")
    unit_key = unit.strip().lower() or "min"
    if unit_key not in _RATE_UNITS:
        raise ValueError(f"--rate 单位不支持: {unit!r}（可用 s/sec/min/hour）")
    return int(count), _RATE_UNITS[unit_key]


class RateGate:
    """提交限流门。桶状态落 SQLite 文件——脚本崩了重开，"这一窗口已用掉的
    配额"不归零（内存限流器重启即失忆，会立刻再撞 1302 限频）。

    跨实例共享语义：多个 RateGate 指向同一 db_path 即共享同一份额度；
    跨多机部署才需要升级 MultiprocessBucket（文件锁版）。
    """

    def __init__(self, rate: str, db_path: str):
        count, unit = parse_rate(rate)
        self.rate_text = rate
        self.db_path = db_path
        self.bucket = SQLiteBucket.init_from_file(
            rates=[Rate(count, unit)], db_path=db_path)
        self._clock = MonotonicClock()

    def acquire(self) -> None:
        """阻塞式获取一个提交额度（排队等待而非抛异常，不污染重试计数）。"""
        while not self.bucket.put(RateItem("kling_submit", self._clock.now())):
            time.sleep(0.05)


# ---------------------------------------------------------------------------
# 重试（tenacity，仅瞬态错误：wait_random_exponential 防惊群）
# ---------------------------------------------------------------------------
def _is_retryable_error(e: BaseException) -> bool:
    """只有 provider 判定为瞬态的错误可重试（KlingError.retryable /
    ProviderError.retryable：限频 1302 / 超并发 1303 / 服务端 5xxx / 网络）。
    参数 / 账户（欠费）/ 政策类与任务级失败重试纯烧时间，原样穿透。"""
    return isinstance(e, (KlingError, ProviderError)) and bool(e.retryable)


def with_transient_retry(fn: Callable[[], Any], *, attempts: int = 3,
                         multiplier: float = 0.05, max_wait: float = 1.0) -> Any:
    """对 fn() 做随机指数退避重试。纪律（survey §3.1）：4xx 永不重试；
    retryable 与否由 provider 异步错误分类决定，这里不做第二套判定。"""
    return tenacity.retry(
        retry=tenacity.retry_if_exception(_is_retryable_error),
        wait=tenacity.wait_random_exponential(multiplier=multiplier, max=max_wait),
        stop=tenacity.stop_after_attempt(attempts),
        before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )(fn)()


# ---------------------------------------------------------------------------
# Provider 实现
# ---------------------------------------------------------------------------
class MockAsyncProvider:
    """MockModel 的异步两阶段包装 + JSON 文件模拟 provider 服务端。

    - submit(): 登记服务端任务（queued），计数器供测试断言提交次数；
    - poll():   queued→running→（调 MockModel.generate 终态化）→succeeded/failed；
      服务端状态持久化到 state_path，跨进程重启后"恢复扫描只查单"语义成立；
    - 失败注入：MockModel 的 fail_sequence 在"任务执行"（poll 推进到终态）时生效，
      对应真实可灵"任务级失败"（status=failed 终态，非网络瞬态，不自动重试）。
    """

    def __init__(self, state_path: str, video_config: Optional[dict] = None,
                 image_config: Optional[dict] = None):
        self.state_path = state_path
        self._video = MockModel(video_config or {})
        self._image = MockImageModel(image_config or {})
        self.submit_calls = 0  # 进程内提交计数（测试/演示断言用）
        self._state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_path):
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        return {"tasks": {}, "task_seq": 0}

    def _save_state(self) -> None:
        parent = os.path.dirname(self.state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)
        shutil.move(tmp, self.state_path)

    # -- 视频两阶段 ---------------------------------------------------------
    def submit(self, prompt: str, output_path: str, *, model: str,
               duration: int = 5, image_url: str = "") -> str:
        self.submit_calls += 1
        self._state["task_seq"] += 1
        task_id = f"mock-task-{self._state['task_seq']:04d}"
        self._state["tasks"][task_id] = {
            "status": "queued",
            "prompt": prompt,
            "output_path": output_path,
            "model": model,
            "duration": duration,
            "image_url": image_url,
            "video_url": "",
            "error": "",
        }
        self._save_state()
        return task_id

    def poll(self, task_id: str) -> PollResult:
        task = self._state["tasks"].get(task_id)
        if task is None:
            return PollResult(POLL_FAILED, error=f"unknown task {task_id}")
        if task["status"] == "queued":
            task["status"] = POLL_RUNNING
            self._save_state()
            return PollResult(POLL_RUNNING)
        if task["status"] == POLL_RUNNING:
            # 任务执行期：MockModel.generate 承载失败注入与假产物落盘
            try:
                self._video.generate(task["prompt"], task["output_path"],
                                     duration=task.get("duration", 5))
            except MockError as e:
                task["status"] = POLL_FAILED
                task["error"] = str(e)
            else:
                task["status"] = POLL_SUCCEEDED
                task["video_url"] = task["output_path"]
            self._save_state()
        return PollResult(task["status"], video_url=task.get("video_url", ""),
                          error=task.get("error", ""))

    # -- 图像（同步语义，秒回无断点需求） ------------------------------------
    def generate_image(self, prompt: str, output_path: str) -> tuple[str, float]:
        return self._image.generate(prompt, output_path)

    # -- 下载（mock 产物已在本地位址，等价拷贝兜底） --------------------------
    @staticmethod
    def download(video_url: str, output_path: str) -> None:
        if os.path.abspath(video_url) != os.path.abspath(output_path):
            parent = os.path.dirname(output_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            shutil.copyfile(video_url, output_path)


class KlingBatchProvider:
    """可灵 API 2.0 提交/查单/下载三段式（复用 kling.py 的鉴权与错误分类）。

    与 KlingModel.generate() 的差异：不把提交-轮询-下载焊死在一次调用里，
    中间态由账本承载（provider_task_id 落库），进程重启可续查。
    未实测（无 key）：请求语义逐字段对齐 kling.py T-B3 版本。
    """

    def __init__(self, api_key: str, model: str = "kling-3.0"):
        if not api_key:
            raise ProviderError("KlingBatchProvider 需要 api_key（或 --provider mock）")
        self.api_key = api_key
        self.model = model
        from src.utils.endpoints import get_provider_base_url
        self.base_url = get_provider_base_url("KLING")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

    def submit(self, prompt: str, output_path: str, *, model: str,
               duration: int = 5, image_url: str = "") -> str:
        import requests
        from src.models.kling import _kling_error_from_response, classify_kling_error
        contents = [{"type": "prompt", "text": prompt}]
        if image_url:
            contents.append({"type": "first_frame", "url": image_url})
        body = {
            "contents": contents,
            "settings": {"resolution": "1080p", "duration": int(duration),
                         "audio": "off"},
            "options": {},
        }
        endpoint = "image-to-video" if image_url else "text-to-video"
        url = f"{self.base_url}/{endpoint}/{model or self.model}"
        try:
            resp = requests.post(url, headers=self._headers(), json=body, timeout=30)
        except requests.RequestException as e:
            raise ProviderError(f"kling submit 网络错误: {e}", retryable=True) from e
        if resp.status_code != 200:
            err = _kling_error_from_response(resp, "submit")
            raise ProviderError(str(err), retryable=err.retryable)
        payload = resp.json()
        if payload.get("code") not in (0, None):
            _, retryable = classify_kling_error(code=payload.get("code"))
            raise ProviderError(
                f"kling submit code={payload.get('code')}: {payload.get('message')}",
                retryable=retryable)
        task_id = (payload.get("data") or {}).get("id")
        if not task_id:
            raise ProviderError(f"kling submit 响应缺 data.id: {str(payload)[:200]}")
        return task_id

    def poll(self, task_id: str) -> PollResult:
        import requests
        from src.models.kling import _TERMINAL_TASK_STATUSES, _kling_error_from_response
        from src.models.kling import classify_kling_error
        url = f"{self.base_url}/tasks?task_ids={task_id}"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=30)
        except requests.RequestException as e:
            raise ProviderError(f"kling poll 网络错误: {e}", retryable=True) from e
        if resp.status_code != 200:
            err = _kling_error_from_response(resp, "poll")
            raise ProviderError(str(err), retryable=err.retryable)
        payload = resp.json()
        if payload.get("code") not in (0, None):
            _, retryable = classify_kling_error(code=payload.get("code"))
            raise ProviderError(
                f"kling poll code={payload.get('code')}: {payload.get('message')}",
                retryable=retryable)
        tasks = payload.get("data") or []
        if not tasks:
            return PollResult(POLL_QUEUED)
        info = tasks[0]
        status = str(info.get("status", "")).lower()
        if status == "succeeded":
            video_url = ""
            for out in info.get("outputs", []):
                if out.get("type") == "video" and out.get("url"):
                    video_url = out["url"]
                    break
            return PollResult(POLL_SUCCEEDED, video_url=video_url)
        if status in _TERMINAL_TASK_STATUSES:
            return PollResult(POLL_FAILED, error=info.get("message", status))
        return PollResult(POLL_RUNNING)

    @staticmethod
    def download(video_url: str, output_path: str) -> None:
        import requests
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            content = requests.get(video_url, timeout=120).content
        except requests.RequestException as e:
            raise ProviderError(f"kling download 网络错误: {e}", retryable=True) from e
        with open(output_path, "wb") as f:
            f.write(content)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def _sha16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _text_sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _parse_cost(cost_text: str) -> float:
    try:
        return float(cost_text)
    except (TypeError, ValueError):
        return 0.0


def _normalize_type(type_or_prefix: str) -> str:
    """"VD" 前缀别名 → 账本 type 名 "video"（PREFIX_TO_TYPE 的逆查入口）。"""
    return PREFIX_TO_TYPE.get(type_or_prefix, type_or_prefix)


@dataclass
class RunReport:
    """单次 run 的结果清单（验收/演示输出直接序列化它）。"""

    asset_type: str = ""
    submitted: list[str] = field(default_factory=list)     # 本次新提交（付费动作）
    completed: list[str] = field(default_factory=list)     # 本次到达 done
    failed: list[str] = field(default_factory=list)        # 本次到达 failed
    skipped_terminal: list[str] = field(default_factory=list)   # attempt>=3 终态转人工
    skipped_duplicate: list[str] = field(default_factory=list)  # 幂等 skip
    recovered_inflight: list[str] = field(default_factory=list)  # 恢复扫描：查单续跑
    guard_inflight: list[str] = field(default_factory=list)      # 防双写拒跑（未超时）
    orphan_reset: list[str] = field(default_factory=list)        # 超时孤儿回收 failed
    still_running: list[str] = field(default_factory=list)       # 轮询超时/查单仍在跑
    cost_spent: float = 0.0               # 本次 run 新增成本（CNY，终态实入账）
    cost_total: float = 0.0               # 账本累计成本（含历史，run 结束时快照）
    unpriced: list[str] = field(default_factory=list)            # 价格表未覆盖
    budget_stop: bool = False             # 预算触顶：完成在途后停止接新提交
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class Runner:
    """账本驱动的串行 runner。所有状态写入走 Ledger.update() 单入口。"""

    def __init__(
        self,
        ledger: Ledger,
        provider: Any,
        *,
        price_table: Optional[dict] = None,
        image_model: str = "mock-image",
        actor: str = "runner",
        output_dir: str = "",
        poll_interval: float = 0.05,
        poll_timeout: float = 10.0,
        stale_minutes: float = 30.0,
        rate_gate: Optional[RateGate] = None,
        submit_attempts: int = 3,
    ):
        self.ledger = ledger
        self.provider = provider
        self.prices = price_table if price_table is not None else prices_mod.load_prices()
        self.image_model = image_model
        self.actor = actor
        self.output_dir = output_dir
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.stale_minutes = stale_minutes
        self.rate_gate = rate_gate
        self.submit_attempts = submit_attempts

    # ------------------------------------------------------------------
    # 账本读取辅助（只读，不扩 db.py）
    # ------------------------------------------------------------------
    def _all_assets(self) -> list[dict]:
        return self.ledger.query()

    def _done_duplicate(self, fingerprint: str, self_id: str) -> Optional[str]:
        """同指纹且已 done 的资产 id（幂等 skip 的依据）；无则 None。"""
        if not fingerprint:
            return None
        for row in self._all_assets():
            if row["asset_id"] != self_id and row["fingerprint"] == fingerprint \
                    and row["status"] == "done":
                return row["asset_id"]
        return None

    @staticmethod
    def _data_of(row: dict) -> dict:
        data = row.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _task_id_of(row: dict) -> str:
        return str(Runner._data_of(row).get("provider_task_id") or "")

    def _spent_total(self) -> float:
        """账本累计成本（cost 列可解析为数字的求和；文本/脏值按 0）。"""
        return sum(_parse_cost(r.get("cost")) for r in self._all_assets())

    def _cost_text(self, model: str, duration: Optional[int], asset_id: str,
                   report: RunReport) -> str:
        """查价 → 成本文本；unpriced 记 "0" 并留痕（宁缺勿错，不阻塞生成）。"""
        cost = prices_mod.cost_for(self.prices, model, duration)
        if cost is None:
            report.unpriced.append(asset_id)
            return "0"
        return str(cost)

    def _updated_age(self, row: dict) -> float:
        """updated_at 距今的分钟数；解析失败返回 +inf（按超时处理）。"""
        try:
            updated = datetime.fromisoformat(row["updated_at"])
        except (TypeError, ValueError):
            return float("inf")
        return (datetime.now() - updated).total_seconds() / 60.0

    # ------------------------------------------------------------------
    # 启动恢复扫描（断点续跑核心）
    # ------------------------------------------------------------------
    def recover_in_production(self, report: RunReport) -> None:
        """扫全部 in_production：
        - 有 task_id → 只查单到终态（或超时留给下一轮），绝不重提交；
        - 无 task_id 且 updated_at 未超时 → 拒跑防双写（可能有另一实例在提交窗口）；
        - 无 task_id 且已超时 → 孤儿回收转 failed（后续 --retry-failed 可 retry）。
        """
        for row in self.ledger.query(status="in_production"):
            aid = row["asset_id"]
            task_id = self._task_id_of(row)
            if task_id:
                report.recovered_inflight.append(aid)
                self._poll_to_terminal(aid, self.ledger.get(aid) or row, task_id, report)
            elif self._updated_age(row) < self.stale_minutes:
                report.guard_inflight.append(aid)
                self.ledger.update(aid, actor=self.actor,
                                   note="guard: in_production 无 task_id 且未超时，拒跑防双写")
            else:
                report.orphan_reset.append(aid)
                self.ledger.update(
                    aid, actor=self.actor, status="failed",
                    detail=f"orphan: in_production 无 task_id 超 {self.stale_minutes:.0f}min 回收")

    # ------------------------------------------------------------------
    # 轮询到终态（含下载 + done 落账）
    # ------------------------------------------------------------------
    def _poll_to_terminal(self, asset_id: str, row: dict, task_id: str,
                          report: RunReport) -> None:
        """对已提交任务轮询到终态。超时保持 in_production（task_id 已在账本，
        下一轮恢复扫描续查）——断点续跑语义：查单免费，重提交烧钱。"""
        start = time.time()
        while time.time() - start < self.poll_timeout:
            try:
                result = self.provider.poll(task_id)
            except ProviderError as e:
                if e.retryable:
                    time.sleep(self.poll_interval)
                    continue
                self.ledger.update(asset_id, actor=self.actor, status="failed",
                                   detail=f"poll fatal: {e}")
                report.failed.append(asset_id)
                report.errors.append(f"{asset_id}: poll fatal {e}")
                return
            if result.status == POLL_SUCCEEDED:
                self._finish_done(asset_id, row, result.video_url, report)
                return
            if result.status == POLL_FAILED:
                self.ledger.update(asset_id, actor=self.actor, status="failed",
                                   detail=f"task failed: {result.error[:200]}")
                report.failed.append(asset_id)
                return
            time.sleep(self.poll_interval)
        # 轮询超时：留在 in_production，下轮恢复扫描只查单
        report.still_running.append(asset_id)
        self.ledger.update(asset_id, actor=self.actor,
                           note=f"poll timeout after {self.poll_timeout}s; 留 in_production 待续查")

    def _finish_done(self, asset_id: str, row: dict, video_url: str,
                     report: RunReport) -> None:
        data = self._data_of(row)
        output_path = row.get("output_path") or data.get("output_path") or ""
        if not output_path:
            output_path = os.path.join(self.output_dir, asset_id, "video.mp4")
        self.provider.download(video_url, output_path)
        with open(output_path, "rb") as f:
            content_hash = _sha16(f.read())
        model = str(data.get("model") or "")
        duration = data.get("duration")
        cost_text = self._cost_text(model, int(duration) if duration else None,
                                    asset_id, report)
        # 权责发生制：任务成功即入账（钱还没扣，预算先记账）；events 记流水
        self.ledger.update(
            asset_id, actor=self.actor, status="done",
            content_hash=content_hash, output_path=output_path, cost=cost_text,
            detail=f"downloaded from task {self._task_id_of(row)};"
                   f" cost={cost_text} CNY model={model} duration={duration}",
        )
        report.cost_spent += _parse_cost(cost_text)
        report.completed.append(asset_id)

    # ------------------------------------------------------------------
    # KF 前置依赖（懒登记 + 同步生成）
    # ------------------------------------------------------------------
    def _ensure_keyframe(self, vd_row: dict, report: RunReport) -> Optional[str]:
        """保证 VD 的前置 KF 已 done，返回 KF 产物路径；失败返回 None。

        懒登记：账本无该 KF 行时按 VD 派生 id（VD-x → KF-x）登记 draft。
        图像生成是本地同步动作（无 task_id 窗口），崩溃重入安全。
        """
        data = self._data_of(vd_row)
        parents = [p for p in (vd_row.get("parent_ids") or "").split(";") if p]
        kf_id = next((p for p in parents if p.startswith("KF")),
                     "KF" + vd_row["asset_id"][2:])
        kf_prompt = data.get("kf_prompt") or vd_row.get("description") or vd_row["asset_id"]
        kf_fp = _text_sha16(f"image|{self.image_model}|{kf_prompt}")

        row = self.ledger.get(kf_id)
        if row is None:
            row = self.ledger.update(
                kf_id, type="keyframe", parent_ids=vd_row["asset_id"],
                description=kf_prompt[:120], source="runner-lazy",
                status="draft", fingerprint=kf_fp, actor=self.actor,
                detail="lazy register from " + vd_row["asset_id"])
        if row["status"] == "done" and row.get("output_path"):
            return row["output_path"]
        if is_terminal(row["status"], row["attempt_count"]):
            report.errors.append(f"{kf_id}: 终态({row['status']})无法作为前置")
            return None
        dup = self._done_duplicate(kf_fp, kf_id)
        if dup:
            # 同指纹已有 done KF：直接借用其产物，不重复生成
            dup_row = self.ledger.get(dup) or {}
            if dup_row.get("output_path"):
                self.ledger.update(kf_id, actor=self.actor, status="done",
                                   content_hash=dup_row.get("content_hash", ""),
                                   output_path=dup_row["output_path"],
                                   detail=f"fingerprint duplicate of {dup}, 免生成")
                return dup_row["output_path"]

        if row["status"] == "draft":
            self.ledger.update(kf_id, actor=self.actor, status="approved",
                               fingerprint=kf_fp)
        elif row["status"] == "failed":
            self.ledger.update(kf_id, actor=self.actor, status="retry")
        self.ledger.update(kf_id, actor=self.actor, status="in_production",
                           source="mock" if self.image_model.startswith("mock") else "kling-image")
        image_path = os.path.join(self.output_dir, kf_id, "keyframe.png")
        try:
            if isinstance(self.provider, MockAsyncProvider):
                self.provider.generate_image(kf_prompt, image_path)
            else:
                raise ProviderError("KF 生成暂仅支持 mock provider（kling 图像另行接入）")
        except (MockError, ProviderError) as e:
            self.ledger.update(kf_id, actor=self.actor, status="failed",
                               detail=f"image generate: {e}")
            report.failed.append(kf_id)
            report.errors.append(f"{kf_id}: {e}")
            return None
        with open(image_path, "rb") as f:
            content_hash = _sha16(f.read())
        cost_text = self._cost_text(self.image_model, None, kf_id, report)
        self.ledger.update(
            kf_id, actor=self.actor, status="done",
            content_hash=content_hash, output_path=image_path, cost=cost_text,
            detail=f"image done model={self.image_model}; cost={cost_text} CNY")
        report.cost_spent += _parse_cost(cost_text)
        report.completed.append(kf_id)
        return image_path

    # ------------------------------------------------------------------
    # 单个 VD 资产处理
    # ------------------------------------------------------------------
    def _submit(self, prompt: str, output_path: str, *, model: str,
                duration: int, image_url: str) -> str:
        """提交单任务。分层纪律（survey §3.2）：限流门在重试体内层管频率
        （每次真实提交都受限流约束），tenacity 在外层管失败重放。"""
        def _once() -> str:
            if self.rate_gate is not None:
                self.rate_gate.acquire()
            return self.provider.submit(prompt, output_path, model=model,
                                        duration=duration, image_url=image_url)
        return with_transient_retry(_once, attempts=self.submit_attempts)

    def _recheck_failed_task(self, row: dict, report: RunReport) -> Optional[str]:
        """failed 资产带 task_id 时先查单（--retry-failed 重跑路径）。

        返回:
        - "done"    → 查单发现任务实际成功（误报/上次下载中断），已下载落账；
        - "skip"    → 任务仍在跑（queued/running），保持 failed 留下轮再查，不重提交；
        - None      → 确认任务已失败终态（或无 task_id），调用方走 retry 重提交。
        """
        task_id = self._task_id_of(row)
        if not task_id:
            return None
        try:
            result = self.provider.poll(task_id)
        except ProviderError as e:
            if e.retryable:
                report.still_running.append(row["asset_id"])
                return "skip"
            return None  # 查单本身失败且不可重试 → 按确认失败处理
        if result.status == POLL_SUCCEEDED:
            self._finish_done(row["asset_id"], row, result.video_url, report)
            return "done"
        if result.status in (POLL_QUEUED, POLL_RUNNING):
            report.still_running.append(row["asset_id"])
            self.ledger.update(row["asset_id"], actor=self.actor,
                               note="recheck: provider 任务仍在跑，保持 failed 待下轮查单")
            return "skip"
        return None  # failed/cancelled/expired 确认终态

    def _process_video(self, row: dict, report: RunReport,
                       max_budget: Optional[float] = None) -> None:
        aid = row["asset_id"]
        data = self._data_of(row)
        prompt = data.get("video_prompt") or row.get("description") or aid
        model = data.get("model") or "mock-video"
        duration = int(data.get("duration") or 5)

        # 终态（failed 且 attempt>=3 / archived）⇒ 跳过转人工
        if is_terminal(row["status"], row["attempt_count"]):
            report.skipped_terminal.append(aid)
            return

        # failed 重跑（--retry-failed）：先查单确认，不盲目重提交烧钱
        if row["status"] == "failed":
            verdict = self._recheck_failed_task(row, report)
            if verdict is not None:
                return
            self.ledger.update(aid, actor=self.actor, status="retry")  # attempt+1

        # 预算粗检：KF+VD 全价预估（unpriced 模型无法预估按 0 放行，终态实入账留痕）
        if max_budget is not None:
            vd_unit = prices_mod.lookup_price(self.prices, model, duration) or 0.0
            kf_unit = prices_mod.lookup_price(self.prices, self.image_model) or 0.0
            if vd_unit + kf_unit > max_budget - self._spent_total():
                report.budget_stop = True
                return

        kf_path = self._ensure_keyframe(row, report)
        if kf_path is None:
            return
        with open(kf_path, "rb") as f:
            kf_hash = _sha16(f.read())
        # 任务指纹 = 模型|时长|prompt|首帧内容（提交前计算并补写）
        fingerprint = _text_sha16(f"video|{model}|{duration}|{prompt}|{kf_hash}")

        # 幂等：同指纹已有 done 资产 ⇒ skip，不新增状态
        dup = self._done_duplicate(fingerprint, aid)
        if dup:
            self.ledger.update(aid, actor=self.actor, fingerprint=fingerprint,
                               note=f"skip: 同指纹 {dup} 已 done（幂等）")
            report.skipped_duplicate.append(f"{aid}=={dup}")
            return

        # 预算细检：提交前按账本现值复核（KF 已入账，余额不够本单就不提交）
        if max_budget is not None:
            vd_unit = prices_mod.lookup_price(self.prices, model, duration)
            if vd_unit is not None and vd_unit > max_budget - self._spent_total():
                report.budget_stop = True
                return

        # 认领流转：draft→approved→in_production（retry/failed→retry 可直接 in_production）
        if row["status"] == "draft":
            self.ledger.update(aid, actor=self.actor, status="approved",
                               fingerprint=fingerprint)
        self.ledger.update(aid, actor=self.actor, status="in_production",
                           source="mock" if model.startswith("mock") else "kling-api")

        try:
            task_id = self._submit(prompt, os.path.join(
                self.output_dir, aid, "video.mp4"),
                model=model, duration=duration, image_url=kf_path)
        except (ProviderError, KlingError) as e:
            # 不可重试（或重试耗尽）→ failed 终态 + events 留痕（Ledger.update 记流水）
            self.ledger.update(aid, actor=self.actor, status="failed",
                               detail=f"submit failed: {e}")
            report.failed.append(aid)
            report.errors.append(f"{aid}: submit {e}")
            return
        # 提交成功立即落 provider_task_id（openai-python 幂等键语义：崩溃窗口最小化）
        new_data = dict(data)
        new_data.update({"provider_task_id": task_id, "model": model,
                         "duration": duration, "video_prompt": prompt})
        self.ledger.update(aid, actor=self.actor, data=new_data,
                           detail=f"submitted task_id={task_id}")
        report.submitted.append(aid)
        self._poll_to_terminal(aid, self.ledger.get(aid) or row, task_id, report)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def run(self, *, asset_type: str = "VD", retry_failed: bool = False,
            max_budget: Optional[float] = None,
            limit: Optional[int] = None) -> RunReport:
        asset_type = _normalize_type(asset_type)
        report = RunReport(asset_type=asset_type)
        self.recover_in_production(report)

        candidates = []
        for row in self._all_assets():
            if row["type"] != asset_type or not row["asset_id"]:
                continue
            st, att = row["status"], row["attempt_count"]
            if is_terminal(st, att):
                if st == "failed":
                    report.skipped_terminal.append(row["asset_id"])
                continue
            if st in ("draft", "approved", "retry"):
                candidates.append(row)
            elif st == "failed" and retry_failed:
                candidates.append(row)

        for row in candidates[:limit if limit is not None else len(candidates)]:
            self._process_video(row, report, max_budget)
            if report.budget_stop:
                break  # 预算触顶：本资产未动，已提交在途的均已轮询完毕（串行模型）

        report.cost_total = self._spent_total()
        return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="账本驱动批量生成 runner（T-B4）")
    ap.add_argument("--db", required=True, help="账本 SQLite 路径（测试一律指向临时副本）")
    ap.add_argument("--provider", choices=["mock", "kling"], default="mock")
    ap.add_argument("--type", default="VD", dest="asset_type",
                    help="处理的资产类型（默认 VD）")
    ap.add_argument("--retry-failed", action="store_true",
                    help="允许 failed(attempt<3) 资产 retry（attempt+1）重跑")
    ap.add_argument("--max-budget", type=float, default=None,
                    help="预算护栏（CNY）：超限完成在途任务后停止接新提交")
    ap.add_argument("--limit", type=int, default=None, help="本轮最多处理资产数")
    ap.add_argument("--rate", default=None,
                    help="提交限流，如 6/min（PyRateLimiter SQLite 持久桶，额度跨重启保留）")
    ap.add_argument("--rate-db", default=None,
                    help="限流桶 SQLite 路径（默认账本同目录 rates.db）")
    ap.add_argument("--poll-interval", type=float, default=0.05)
    ap.add_argument("--poll-timeout", type=float, default=10.0,
                    help="单任务轮询上限秒数；超时留 in_production 待下轮续查")
    ap.add_argument("--stale-minutes", type=float, default=30.0,
                    help="in_production 无 task_id 的防双写保护窗口（分钟）")
    ap.add_argument("--output-dir", default=os.path.join(_REPO_ROOT, "output", "runner"))
    ap.add_argument("--mock-state", default=None,
                    help="mock provider 服务端状态文件（默认 <output-dir>/mock_server_state.json）")
    ap.add_argument("--mock-fail-seq", default="",
                    help="mock 失败注入序列，逗号分隔（传给 MockModel fail_sequence）")
    ap.add_argument("--kling-key-env", default="KLING_API_KEY")
    ap.add_argument("--image-model", default="mock-image")
    args = ap.parse_args()

    ledger = Ledger(args.db)
    os.makedirs(args.output_dir, exist_ok=True)
    if args.provider == "mock":
        provider = MockAsyncProvider(
            args.mock_state or os.path.join(args.output_dir, "mock_server_state.json"),
            video_config={"fail_sequence": [s for s in args.mock_fail_seq.split(",") if s]},
        )
    else:
        api_key = os.getenv(args.kling_key_env, "")
        provider = KlingBatchProvider(api_key)

    runner = Runner(ledger, provider,
                    price_table=prices_mod.load_prices(),
                    image_model=args.image_model,
                    poll_interval=args.poll_interval,
                    poll_timeout=args.poll_timeout,
                    stale_minutes=args.stale_minutes,
                    rate_gate=RateGate(args.rate, args.rate_db or
                                       os.path.join(os.path.dirname(args.db), "rates.db"))
                    if args.rate else None,
                    output_dir=args.output_dir)
    report = runner.run(asset_type=args.asset_type,
                        retry_failed=args.retry_failed,
                        max_budget=args.max_budget,
                        limit=args.limit)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    ledger.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
