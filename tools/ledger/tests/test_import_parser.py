"""import_storyboard：固定样例解析、校验门禁、幂等重跑（二次 import 行数不变）。"""

import pytest

import import_storyboard as imp
from db import Ledger

# ---------------------------------------------------------------------------
# 固定样例：两集 + 三类资产卡（覆盖多场景头/变体场景引用/NPC 台词/Keyframe insert）
# ---------------------------------------------------------------------------
CARDS_CHAR = """# 角色卡合集 — 测试

# Character Card — 梁宸（CH-01）
- 外观锚点: 测试用

# Character Card — 沈雨晴（CH-02）
- 外观锚点: 测试用
"""
CARDS_SCENE = """# 场景卡合集 — 测试

# Scene Card — 夜桥（SC-01）

# Scene Card — 店门口（SC-02）
"""
CARDS_PROP = """# 道具卡合集 — 测试

# Prop Card — 裂屏手机（PR-01）
"""

EP1 = """---
# 《你是说我被人捡走了？》第 1 集「测试」
> **画幅**: 9:16 竖屏 | **时长**: 约 9 秒 | **场景数**: 1
> **衔接模式**: keyframe pair | 目标模型: Kling | 类型: urban
---

### 【Scene 1】夜桥（对应场景卡 SC-01）

**Environment**: 深夜河桥，测试环境描述。

---

**[00:00.0 – 00:04.0] 开场 · 翻栏**

`[medium shot|fixed|梁宸站在桥边，攥着手机]`

夜风很冷。他站在桥边，手里是那部裂屏手机。

（**Keyframe insert · new prop**: 裂屏手机 PR-01 首现，用作本镜首帧。）

> **梁宸**（沙哑，几乎没什么力气）：你走。

`[Emotion: 悬念起 | Hook: 视觉反常 | Connection: jump cut（本集首镜·用 SC-01 定场图）| Sound: 风声]`

---

**[00:04.0 – 00:09.0] 屏幕光**

`[close-up|slow push-in|手机屏幕亮起又熄灭]`

屏幕光映在他脸上。

`[Emotion: 压制 | Hook: 信息缺口 | Connection: keyframe pair | Sound: 心跳声]`

---

## 资产扫描表（测试）

| CH-01 梁宸 | 角色 | S01 |
"""

EP2 = """---
# 《你是说我被人捡走了？》第 2 集「续」
> **画幅**: 9:16 竖屏 | **时长**: 约 5 秒 | **场景数**: 2
---

### 【Scene 1】店门口（SC-02）

**[00:00.0 – 00:05.0] 相遇**

`[medium close-up|fixed|沈雨晴递出一杯奶茶]`

她把奶茶往前递了递。

> **主管**（不耐烦）：快点。

`[Emotion: 转机 | Hook: 行为谜 | Connection: keyframe pair（承接 SC-01b）| Sound: 门铃]`
"""


@pytest.fixture()
def script_dir(tmp_path):
    (tmp_path / "asset-character-cards.md").write_text(CARDS_CHAR, encoding="utf-8")
    (tmp_path / "asset-scene-cards.md").write_text(CARDS_SCENE, encoding="utf-8")
    (tmp_path / "asset-prop-cards.md").write_text(CARDS_PROP, encoding="utf-8")
    (tmp_path / "第1集-测试-分镜剧本.md").write_text(EP1, encoding="utf-8")
    (tmp_path / "第2集-续-分镜剧本.md").write_text(EP2, encoding="utf-8")
    return str(tmp_path)


def _run(script_dir, db_path):
    return imp.import_storyboard(script_dir, db_path, report_path=None)


def test_parse_counts_match_fixture(script_dir, tmp_path):
    report = _run(script_dir, str(tmp_path / "l.db"))
    p = report["parsed"]
    assert p["episodes"] == 2
    assert p["shots_total"] == 3
    assert p["shots_per_episode"] == {"E01": 2, "E02": 1}
    assert p["cards"] == {"character": 2, "scene": 2, "prop": 1}
    assert report["errors"] == []


def test_shot_fields_parsed(script_dir, tmp_path):
    db = str(tmp_path / "l.db")
    _run(script_dir, db)
    led = Ledger(db)
    row = led.get("SB-001")
    d = row["data"]
    assert row["description"].startswith("E01-S01")
    assert (d["tc_start"], d["tc_end"]) == (0.0, 4.0)
    assert d["shot_size"] == "medium shot"
    assert d["camera_move"] == "fixed"
    assert d["emotion"] == "悬念起"
    assert d["hook"] == "视觉反常"
    assert d["sound"] == "风声"
    assert d["dialogues"] == [
        {"speaker": "梁宸", "emotion": "沙哑，几乎没什么力气", "line": "你走。"}
    ]
    assert d["keyframe_inserts"][0]["kind"] == "new prop"
    assert d["prop_refs"] == ["PR-01"]
    assert d["char_refs"] == ["CH-01"]
    # parent_ids = 场景 + 角色 + 道具，排序分号分隔
    assert row["parent_ids"] == "CH-01;PR-01;SC-01"
    led.close()


def test_variant_scene_ref_falls_back_to_base(script_dir, tmp_path):
    db = str(tmp_path / "l.db")
    _run(script_dir, db)
    led = Ledger(db)
    d = led.get("SB-003")["data"]          # E02-S01，Connection 引用 SC-01b
    assert "SC-01" in d["scene_refs"]       # 回退基卡，无悬空引用
    assert "SC-01b" not in d["scene_refs"]
    assert "SC-01" in led.get("SB-003")["parent_ids"]
    led.close()


def test_npc_speaker_warning_not_error(script_dir, tmp_path):
    report = _run(script_dir, str(tmp_path / "l.db"))
    assert any("主管" in w and "NPC" in w for w in report["warnings"])
    assert report["errors"] == []
    assert report["parsed"]["npc_speakers"] == {"主管": 1}


def test_scene_count_declaration_mismatch_warns(script_dir, tmp_path):
    report = _run(script_dir, str(tmp_path / "l.db"))
    assert any("E02" in w and "场景数" in w for w in report["warnings"])


def _make_broken_dir(tmp_path):
    (tmp_path / "asset-character-cards.md").write_text(CARDS_CHAR, encoding="utf-8")
    (tmp_path / "asset-scene-cards.md").write_text(CARDS_SCENE, encoding="utf-8")
    (tmp_path / "asset-prop-cards.md").write_text(CARDS_PROP, encoding="utf-8")
    broken = EP1.replace(
        "**[00:04.0 – 00:09.0] 屏幕光**", "**[00:06.0 – 00:09.0] 屏幕光**"
    ).replace("PR-01 首现", "PR-99 首现")
    (tmp_path / "第1集-坏-分镜剧本.md").write_text(broken, encoding="utf-8")
    return str(tmp_path)


def test_broken_fixture_reports_gaps_and_unknown_refs(tmp_path):
    d = _make_broken_dir(tmp_path)
    report = imp.import_storyboard(d, str(tmp_path / "l.db"), report_path=None)
    assert any("时间码不连续" in e and "间隙 2.0s" in e for e in report["errors"])
    assert any("PR-99" in e and "道具卡" in e for e in report["errors"])
    assert report["delta_vs_expected"]["shots"] != 0   # 与预期 179 的差异如实呈现


def test_idempotent_rerun_same_rows_no_duplicates(script_dir, tmp_path):
    db = str(tmp_path / "l.db")
    r1 = _run(script_dir, db)
    led = Ledger(db)
    n_assets_1 = led.counts()["total"]
    n_events_1 = len(led.events())
    ids_1 = sorted(a["asset_id"] for a in led.query())
    fps_1 = {a["asset_id"]: a["fingerprint"] for a in led.query()}
    led.close()

    r2 = _run(script_dir, db)              # 二次 import：upsert，不新增不重复
    led = Ledger(db)
    assert led.counts()["total"] == n_assets_1 == r1["parsed"]["rows_total"] == r2["parsed"]["rows_total"]
    ids_2 = sorted(a["asset_id"] for a in led.query())
    assert ids_1 == ids_2
    assert len(ids_2) == len(set(ids_2))   # 无重复 asset_id
    fps_2 = {a["asset_id"]: a["fingerprint"] for a in led.query()}
    assert fps_1 == fps_2                  # 同内容同指纹（幂等键稳定）
    assert len(led.events()) == n_events_1  # 二次重跑零变化：无新流水
    led.close()


def test_fingerprint_changes_when_content_changes(script_dir, tmp_path):
    db = str(tmp_path / "l.db")
    _run(script_dir, db)
    led = Ledger(db)
    fp_before = led.get("SB-001")["fingerprint"]
    led.close()

    src = None
    for f in ("第1集-测试-分镜剧本.md",):
        src = open(f"{script_dir}/{f}", encoding="utf-8").read()
    changed = src.replace("夜风很冷。", "夜风更冷了。")
    with open(f"{script_dir}/第1集-测试-分镜剧本.md", "w", encoding="utf-8") as fh:
        fh.write(changed)
    _run(script_dir, db)

    led = Ledger(db)
    assert led.counts()["total"] == 8       # 行数不变（8 = 5 卡 + 3 镜）
    assert led.get("SB-001")["fingerprint"] != fp_before   # 内容变了 → 指纹变 → 可检出重做需求
    led.close()


def test_real_corpu_expected_totals_guard(script_dir):
    """EXPECTED 常量与调研口径一致（15 集 / 179 镜），防止有人悄悄改预期硬凑。"""
    assert imp.EXPECTED_EPISODES == 15
    assert imp.EXPECTED_SHOTS == 179


# ---------------------------------------------------------------------------
# T-B6 修复回归：场景头括注带限定词（外/打烊后）不再整头解析失败
# （真实样本：E07「对应场景卡 SC-02 外」/ E10「SC-02 · 打烊后」/ E12「SC-02 外」）
# ---------------------------------------------------------------------------
def test_split_scene_annotation_qualifier_forms():
    assert imp._split_scene_annotation("对应场景卡 SC-01") == ("SC-01", "")
    assert imp._split_scene_annotation("对应场景卡 SC-02 外") == ("SC-02", "外")
    assert imp._split_scene_annotation("SC-02 · 打烊后") == ("SC-02", "打烊后")
    assert imp._split_scene_annotation("SC-02 外") == ("SC-02", "外")
    assert imp._split_scene_annotation("SC-01b") == ("SC-01b", "")
    assert imp._split_scene_annotation("暂无对应卡") == ("", "暂无对应卡")


def test_scene_header_with_qualifier_parses(tmp_path):
    """带限定词的场景头：镜头归属 SC-02，qualifier 透传到 shot。"""
    ep_md = EP1.replace(
        "### 【Scene 1】夜桥（对应场景卡 SC-01）",
        "### 【Scene 1】店门口（对应场景卡 SC-02 外）",
    )
    path = tmp_path / "第3集-限定词-分镜剧本.md"
    path.write_text(ep_md, encoding="utf-8")
    ep = imp.parse_episode(str(path))
    assert ep["scenes"][0]["sc_ref"] == "SC-02"
    assert ep["scenes"][0]["qualifier"] == "外"
    assert len(ep["shots"]) == 2
    assert all(sh["scene"] == "SC-02" for sh in ep["shots"])
    assert {sh["scene_qualifier"] for sh in ep["shots"]} == {"外"}


def test_scene_header_without_reference_still_counts(tmp_path):
    """括注里没有 SC 引用：场景块仍计数、镜头照常入账（scene 为空）。"""
    ep_md = EP1.replace(
        "### 【Scene 1】夜桥（对应场景卡 SC-01）",
        "### 【Scene 1】无名地点（临时场景）",
    )
    path = tmp_path / "第4集-无引用-分镜剧本.md"
    path.write_text(ep_md, encoding="utf-8")
    ep = imp.parse_episode(str(path))
    assert ep["scenes"][0]["sc_ref"] == ""
    assert ep["scenes"][0]["qualifier"] == "临时场景"
    assert len(ep["shots"]) == 2
    assert all(sh["scene"] == "" for sh in ep["shots"])
