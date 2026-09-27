"""账本 schema 与状态机定义（manifest v1.1）。

抄自调研结论（docs/agents/raw/2026-09-13-survey-V3-asset-ledger.md §3）：
- 列结构 = workflow 01-asset-numbering.md v1.0 的 9 列 + 6 个新列
  （parent_ids / fingerprint / content_hash / attempt_count / output_path / schema_version）
- 7 态状态机 + 流转白名单（非法流转 = error，不是 warning）
- attempt_count >= 3 后 failed 为终态（OpenCue DEAD 语义：只能人工介入）
- archived 恒为终态
- fingerprint = 幂等键（同输入必同指纹）；content_hash = 产物内容哈希
"""

from __future__ import annotations

# 账本自身版本（schema_version 列）
SCHEMA_VERSION = "1.1"

# 重试上限：failed 后转 retry 的次数上限，达到即终态转人工（Zou retake_count + OpenCue DEAD）
MAX_ATTEMPTS = 3

# ---------------------------------------------------------------------------
# 资产类型：workflow 01-asset-numbering.md 的 14 前缀 → 13 个 type
# （LOG 前缀不进 assets 表——日志由 events 表结构化承载，即 LOG-XXX 的账本化）
# ---------------------------------------------------------------------------
PREFIX_TO_TYPE = {
    "PRJ": "project",
    "WLD": "worldview",
    "SCR": "script",
    "CH": "character",    # 项目实际使用 CH-（角色卡/分镜表均如此），CHR 为 workflow 旧写法
    "CHR": "character",
    "SCN": "scene",
    "SC": "scene",        # 项目实际使用 SC-
    "PRO": "prop",
    "PR": "prop",         # 项目实际使用 PR-
    "STY": "style",
    "SB": "storyboard",
    "KF": "keyframe",
    "VD": "video",
    "AU": "audio",
    "SUB": "subtitle",
    "FIN": "final",
}

ASSET_TYPES = sorted(set(PREFIX_TO_TYPE.values()))

# ---------------------------------------------------------------------------
# 状态机（7 态，workflow 01-asset-numbering.md + V3 §3.5 流转白名单）
# ---------------------------------------------------------------------------
STATES = ("draft", "approved", "in_production", "done", "failed", "retry", "archived")

TRANSITIONS: dict[str, set[str]] = {
    "draft": {"approved", "failed"},
    "approved": {"in_production", "draft"},
    "in_production": {"done", "failed"},
    "done": {"archived"},
    "failed": {"retry", "draft"},
    "retry": {"in_production", "failed"},
    "archived": set(),
}


def is_terminal(status: str, attempt_count: int = 0) -> bool:
    """终态判定：archived 恒终态；failed 达到重试上限即终态（转人工）。"""
    if status == "archived":
        return True
    if status == "failed" and attempt_count >= MAX_ATTEMPTS:
        return True
    return False


def validate_transition(from_status: str, to_status: str, attempt_count: int = 0) -> tuple[bool, str]:
    """流转校验。同态自环视为 no-op 放行（重登记/字段补写不算流转）。

    返回 (ok, reason)；ok=False 时 reason 为机器可读错误码。
    """
    if from_status not in STATES:
        return False, f"unknown_status:{from_status}"
    if to_status not in STATES:
        return False, f"unknown_status:{to_status}"
    if from_status == to_status:
        return True, "noop"
    if to_status not in TRANSITIONS[from_status]:
        return False, f"illegal_transition:{from_status}->{to_status}"
    if is_terminal(from_status, attempt_count):
        return False, f"terminal_locked:{from_status}(attempt={attempt_count})"
    return True, "ok"


def is_valid_asset_id(asset_id: str) -> bool:
    """asset_id 前缀必须可映射到已知类型（编号纪律的机器执行点）。"""
    prefix = asset_id.split("-", 1)[0]
    return prefix in PREFIX_TO_TYPE


# ---------------------------------------------------------------------------
# 列定义
# ---------------------------------------------------------------------------
# assets 表 = manifest v1.1 列（顺序即 CSV 导出顺序）+ data JSON 扩展列
# （扩展字段放 JSON 列不加列，抄 Zou entity.data JSONB 思想）
MANIFEST_COLUMNS = (
    "asset_id",        # PK, {TYPE}-{项目号}-{序列号}[-V{n}]
    "type",            # ASSET_TYPES 之一
    "parent_ids",      # 直接上游 asset_id，分号分隔（追溯链显式化）
    "description",
    "source",          # script / manual / kling-api / tts / ffmpeg ...
    "status",          # STATES 之一
    "attempt_count",   # 默认 0；failed→retry 时 +1；>=3 终态转人工
    "fingerprint",     # sha256 前 16 位，幂等键
    "content_hash",    # 产物文件 sha256 前 16 位（无产物为空）
    "output_path",     # 产物相对路径（每资产 id 一个子目录、固定产物名）
    "cost",            # 文本（积分/元/美元均可）
    "created_at",      # iso8601
    "updated_at",      # iso8601，状态/字段变更必刷
    "schema_version",  # 账本自身版本
    "note",            # 纯备注，不承担文件路径职责
)

DATA_COLUMN = "data"   # JSON TEXT：分镜结构化详情等扩展字段（不进 CSV 导出）
