"""T-B3: Seedance r2v 锁定行提示词 + @图片N 别名生成器单测。

覆盖五分型锁定行文本、别名映射、prompt 组装（含空参考边界）。
"""
from src.apps.comic_gen.consistency import (
    ReferenceLock,
    ReferenceLockKind,
    assemble_r2v_prompt,
    build_reference_aliases,
    build_reference_lock_line,
    build_reference_lock_lines,
)
from src.apps.comic_gen.prompt_assembly import assemble_r2v_prompt_with_locks


# ── 五分型锁定行文本 ─────────────────────────────────────────────────────

class TestLockLinePerKind:
    def test_character_identity_locks_face_shape_age(self):
        """角色身份: 全程 100% 锁定五官/脸型/发型/年龄感/体态, 禁止换脸换发型变年龄。"""
        line = build_reference_lock_line(
            ReferenceLock(name="林晚", kind=ReferenceLockKind.CHARACTER_IDENTITY),
            alias="@图片1",
        )
        assert line.startswith("@图片1（林晚｜角色身份参考）")
        assert "100% 锁定" in line
        for token in ("五官", "脸型", "发型", "年龄感", "体态"):
            assert token in line
        assert "不得换脸、换发型或变年龄" in line

    def test_character_outfit_locks_clothing_only(self):
        """服装变装: 只锁服装体态, 脸以身份参考图为准。"""
        line = build_reference_lock_line(
            ReferenceLock(name="林晚", kind=ReferenceLockKind.CHARACTER_OUTFIT),
            alias="@图片2",
        )
        assert line.startswith("@图片2（林晚｜服装变装参考）")
        assert "仅锁定本图中的服装与体态" in line
        assert "脸部仍以该角色的身份参考图为准" in line

    def test_faceless_reference_forbids_portrait_reading(self):
        """无脸参考: 禁止读取脸/五官/肖像。"""
        line = build_reference_lock_line(
            ReferenceLock(name="背影剪影", kind=ReferenceLockKind.FACELESS),
            alias="@图片3",
        )
        assert line.startswith("@图片3（背影剪影｜无脸参考）")
        assert "禁止读取本图中的脸、五官与肖像信息" in line

    def test_scene_locks_space_lighting_color_camera_axis(self):
        """场景: 锁 space/lighting/color/camera-axis。"""
        line = build_reference_lock_line(
            ReferenceLock(name="老渡口", kind=ReferenceLockKind.SCENE),
            alias="@图片4",
        )
        assert line.startswith("@图片4（老渡口｜场景参考）")
        for token in ("空间结构", "光照", "色彩", "镜头轴线"):
            assert token in line

    def test_group_does_not_require_same_face(self):
        """群像: 不要求个体同脸。"""
        line = build_reference_lock_line(
            ReferenceLock(name="集市人群", kind=ReferenceLockKind.GROUP),
            alias="@图片5",
        )
        assert line.startswith("@图片5（集市人群｜群像参考）")
        assert "不要求画面中每个个体与参考图同脸" in line

    def test_description_appended(self):
        line = build_reference_lock_line(
            ReferenceLock(
                name="林晚", kind=ReferenceLockKind.CHARACTER_IDENTITY,
                description="17-year-old girl, East Asian, 2020s casual wear",
            ),
            alias="@图片1",
        )
        assert "外观锁定：17-year-old girl" in line


# ── @图片N 别名系统 ──────────────────────────────────────────────────────

class TestAliases:
    def test_names_map_to_numbered_aliases_in_order(self):
        aliases = build_reference_aliases(["林晚", "老渡口", "集市人群"])
        assert aliases == {"林晚": "@图片1", "老渡口": "@图片2", "集市人群": "@图片3"}

    def test_single_name_starts_at_one(self):
        assert build_reference_aliases(["A"]) == {"A": "@图片1"}

    def test_empty_names_empty_map(self):
        assert build_reference_aliases([]) == {}

    def test_lock_lines_get_sequential_aliases(self):
        refs = [
            ReferenceLock(name="林晚", kind=ReferenceLockKind.CHARACTER_IDENTITY),
            ReferenceLock(name="老渡口", kind=ReferenceLockKind.SCENE),
        ]
        lines = build_reference_lock_lines(refs)
        assert len(lines) == 2
        assert lines[0].startswith("@图片1")
        assert lines[1].startswith("@图片2")


# ── prompt 组装（r2v 路径） ──────────────────────────────────────────────

class TestAssembleR2VPrompt:
    def test_prompt_contains_base_and_lock_block(self):
        refs = [
            ReferenceLock(name="林晚", kind=ReferenceLockKind.CHARACTER_IDENTITY),
            ReferenceLock(name="老渡口", kind=ReferenceLockKind.SCENE),
        ]
        prompt, aliases = assemble_r2v_prompt("林晚在渡口回头", refs)
        assert prompt.startswith("林晚在渡口回头")
        assert "参考图锁定：" in prompt
        assert "@图片1（林晚｜角色身份参考）" in prompt
        assert "@图片2（老渡口｜场景参考）" in prompt
        assert aliases == {"林晚": "@图片1", "老渡口": "@图片2"}

    def test_empty_refs_returns_base_prompt_only(self):
        prompt, aliases = assemble_r2v_prompt("纯文生视频", [])
        assert prompt == "纯文生视频"
        assert aliases == {}

    def test_prompt_assembly_entry_point_delegates(self):
        """prompt_assembly 的 r2v 组装入口与 consistency 行为一致。"""
        refs = [ReferenceLock(name="A", kind=ReferenceLockKind.GROUP)]
        prompt, aliases = assemble_r2v_prompt_with_locks("base", refs)
        expected, expected_aliases = assemble_r2v_prompt("base", refs)
        assert prompt == expected
        assert aliases == expected_aliases
