"""schema 层：7 态状态机、流转白名单、终态语义、前缀映射。"""

from schema import (
    ASSET_TYPES,
    MAX_ATTEMPTS,
    STATES,
    TRANSITIONS,
    is_terminal,
    is_valid_asset_id,
    validate_transition,
)


def test_seven_states_and_transition_whitelist():
    assert len(STATES) == 7
    assert set(TRANSITIONS) == set(STATES)
    # 抄自 workflow 01-asset-numbering.md 的白名单
    assert validate_transition("draft", "approved") == (True, "ok")
    assert validate_transition("approved", "in_production")[0]
    assert validate_transition("in_production", "done")[0]
    assert validate_transition("in_production", "failed")[0]
    assert validate_transition("failed", "retry")[0]
    assert validate_transition("failed", "draft")[0]
    assert validate_transition("retry", "in_production")[0]
    assert validate_transition("retry", "failed")[0]
    assert validate_transition("done", "archived")[0]


def test_illegal_transitions_rejected():
    assert not validate_transition("draft", "done")[0]
    assert not validate_transition("retry", "done")[0]
    assert not validate_transition("approved", "done")[0]
    assert not validate_transition("failed", "in_production")[0]  # 必须先经 retry
    assert not validate_transition("draft", "nonsense")[0]
    assert validate_transition("draft", "nonsense")[1].startswith("unknown_status")


def test_same_state_is_noop():
    ok, reason = validate_transition("in_production", "in_production")
    assert ok and reason == "noop"


def test_failed_terminal_at_attempt_cap():
    assert MAX_ATTEMPTS == 3
    # attempt < 3：failed 可流转
    assert not is_terminal("failed", 0)
    assert not is_terminal("failed", 2)
    assert validate_transition("failed", "retry", 2)[0]
    # attempt >= 3：failed 终态（OpenCue DEAD 语义），转人工
    assert is_terminal("failed", 3)
    ok, reason = validate_transition("failed", "retry", 3)
    assert not ok
    assert reason.startswith("terminal_locked")


def test_archived_is_hard_terminal():
    assert is_terminal("archived", 0)
    ok, reason = validate_transition("archived", "draft", 0)
    assert not ok
    # archived 是非法流转（无出边），force 也不该按 terminal_locked 语义放行
    assert reason.startswith("illegal_transition")


def test_done_is_not_terminal_but_cannot_go_back():
    assert not is_terminal("done", 0)
    assert not validate_transition("done", "draft")[0]
    assert validate_transition("done", "archived")[0]


def test_asset_id_prefix_validation():
    assert is_valid_asset_id("SB-001")
    assert is_valid_asset_id("CH-01")
    assert is_valid_asset_id("SC-01b")
    assert is_valid_asset_id("PR-09")
    assert is_valid_asset_id("KF-01-001")
    assert not is_valid_asset_id("XX-999")
    assert len(ASSET_TYPES) == 13  # 14 前缀去重后 13 类（LOG 由 events 表承载）
