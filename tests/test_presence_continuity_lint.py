"""T-B6: 在场性/跨镜连续性 lint 纯函数测试。

check_presence_and_continuity 输入输出全是数据：同输入同输出、
时间码缺失可退化为场内顺序、warning 级永不拦截。
"""
from src.apps.comic_gen.consistency import (
    ContinuityWarning,
    SceneCast,
    ShotRef,
    check_presence_and_continuity,
)


def by_kind(warnings, kind):
    return [w for w in warnings if w.kind == kind]


class TestEntityGap:
    def test_prop_gap_with_timecodes(self):
        """道具出现→消失→复现（时间码齐备）→ 报缺失镜头。"""
        shots = [
            ShotRef("SB-001", "SC-01", prop_ids=("PR-01",), start_s=0.0, end_s=4.0),
            ShotRef("SB-002", "SC-01", start_s=4.0, end_s=9.0),
            ShotRef("SB-003", "SC-01", prop_ids=("PR-01",), start_s=9.0, end_s=14.0),
        ]
        ws = check_presence_and_continuity(shots)
        gaps = by_kind(ws, "entity_gap")
        assert len(gaps) == 1
        assert gaps[0].entity_kind == "prop"
        assert gaps[0].entity_id == "PR-01"
        assert gaps[0].shot_ids == ("SB-002",)
        assert "瞬移" in gaps[0].message

    def test_character_mid_scene_disappearance_without_timecodes(self):
        """无时间码：角色场内中段消失又回归 → 仍按输入顺序查出。"""
        shots = [
            ShotRef("A", "SC-02", character_ids=("CH-01",)),
            ShotRef("B", "SC-02"),
            ShotRef("C", "SC-02", character_ids=("CH-01",)),
        ]
        ws = check_presence_and_continuity(shots)
        gaps = by_kind(ws, "entity_gap")
        assert len(gaps) == 1
        assert gaps[0].entity_kind == "character"
        assert gaps[0].entity_id == "CH-01"
        assert gaps[0].shot_ids == ("B",)

    def test_continuous_presence_is_clean(self):
        """道具/角色全程在场或只出现一次 → 无 warning。"""
        shots = [
            ShotRef("A", "S", character_ids=("c1",), prop_ids=("p1",), start_s=0.0, end_s=3.0),
            ShotRef("B", "S", character_ids=("c1",), prop_ids=("p1",), start_s=3.0, end_s=6.0),
            ShotRef("C", "S", character_ids=("c1",), start_s=6.0, end_s=9.0),
        ]
        assert check_presence_and_continuity(shots) == []

    def test_cross_scene_isolation(self):
        """道具只在同场景内比对：跨场景断续不算瞬移。"""
        shots = [
            ShotRef("A", "S1", prop_ids=("p1",)),
            ShotRef("B", "S2"),
            ShotRef("C", "S1", prop_ids=("p1",)),
        ]
        ws = check_presence_and_continuity(shots)
        # S1 内 A→C 相邻（中间没有其他 S1 镜头），不产生 gap
        assert by_kind(ws, "entity_gap") == []


class TestSceneCastPresence:
    def test_declared_but_never_on_screen(self):
        """角色表声明却无任何镜头到场 → warning。"""
        shots = [ShotRef("A", "SC-01", character_ids=("CH-01",))]
        casts = [SceneCast("SC-01", ("CH-01", "CH-02"))]
        ws = check_presence_and_continuity(shots, casts)
        missing = by_kind(ws, "cast_member_never_on_screen")
        assert len(missing) == 1
        assert missing[0].entity_id == "CH-02"

    def test_outside_cast_reference(self):
        """镜头引用表外角色 → warning（表缺失时跳过）。"""
        shots = [ShotRef("A", "SC-01", character_ids=("CH-01", "CH-09"))]
        casts = [SceneCast("SC-01", ("CH-01",))]
        ws = check_presence_and_continuity(shots, casts)
        outside = by_kind(ws, "character_outside_cast")
        assert len(outside) == 1
        assert outside[0].entity_id == "CH-09"
        assert outside[0].shot_ids == ("A",)

    def test_no_cast_declaration_skips_cast_checks(self):
        """无角色表数据（如账本 SB 场景卡）→ 声明类检查整体跳过。"""
        shots = [ShotRef("A", "S", character_ids=("anyone",))]
        assert check_presence_and_continuity(shots) == []


class TestPurityAndDeterminism:
    def test_empty_input(self):
        assert check_presence_and_continuity([], []) == []

    def test_same_input_same_output(self):
        shots = [
            ShotRef("A", "S1", prop_ids=("p1",)),
            ShotRef("B", "S1"),
            ShotRef("C", "S1", prop_ids=("p1",)),
        ]
        assert check_presence_and_continuity(shots) == check_presence_and_continuity(shots)

    def test_output_sorted_for_stable_snapshot(self):
        """输出按 (scene_id, kind, entity_id) 排序，可快照比对。"""
        shots = [
            ShotRef("A", "S2", character_ids=("c2",)),
            ShotRef("B", "S1", character_ids=("c1",), prop_ids=("p1",)),
        ]
        casts = [SceneCast("S1", ("ghost",)), SceneCast("S2", ("ghost2",))]
        ws = check_presence_and_continuity(shots, casts)
        keys = [(w.scene_id, w.kind, w.entity_id) for w in ws]
        assert keys == sorted(keys)

    def test_warnings_are_dataclasses_not_exceptions(self):
        """warning 是数据（ContinuityWarning），检查本身不抛业务异常。"""
        assert issubclass(ContinuityWarning, object)
        ws = check_presence_and_continuity([ShotRef("A", "S"), ShotRef("B", "S")], [])
        assert all(isinstance(w, ContinuityWarning) for w in ws)
