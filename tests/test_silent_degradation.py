"""T-B2 可靠性步骤 1：静默降级失败注入回归测试。

背景（survey V1 结论⑤）：改造前 style 分析失败会静默返回 3 条硬编码 mock
推荐、storyboard 分析未配置时静默返回 1 帧假分镜、pipeline 实体名匹配失败
会被静默丢弃/兜底——用户以为拿到了真实结果，实际是降级数据。本文件锁定
修复后的行为：
  1. style 分析任何失败 → PolishError（带 reason），绝不返回 mock；
  2. storyboard 分析未配置 → PolishError(is_configured_false)；
  3. pipeline 实体名匹配失败 → warning 日志 + 帧上保留原始引用
     （unresolved_scene_ref / unresolved_character_refs / unresolved_prop_refs）。
"""

import logging
import time
import uuid
from unittest.mock import patch

import pytest

from src.apps.comic_gen.llm import PolishError, ScriptProcessor
from src.apps.comic_gen.models import Character, Prop, Scene, Script
from src.apps.comic_gen.pipeline import ComicGenPipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeLLM:
    """ScriptProcessor.llm 的替身：可配置 is_configured 与 chat 行为。"""

    def __init__(self, configured=True, chat_impl=None, chat_exc=None):
        self._configured = configured
        self._chat_impl = chat_impl
        self._chat_exc = chat_exc
        self.calls = []

    @property
    def is_configured(self):
        return self._configured

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self._chat_exc is not None:
            raise self._chat_exc
        return self._chat_impl(messages, **kwargs)


def _make_processor(llm) -> ScriptProcessor:
    """真实 ScriptProcessor，但 LLMAdapter 换成 FakeLLM（不触网、不读 .env）。"""
    sp = ScriptProcessor()
    sp.llm = llm
    return sp


_VALID_STYLE_JSON = """
```json
{"recommendations": [
  {"name": "Cinematic Realism", "description": "电影级写实", "reason": "r1",
   "positive_prompt": "cinematic lighting", "negative_prompt": "cartoon"},
  {"name": "Ink Wash", "description": "水墨", "reason": "r2",
   "positive_prompt": "ink wash", "negative_prompt": "photo"},
  {"name": "Neon Noir", "description": "霓虹黑色电影", "reason": "r3",
   "positive_prompt": "neon, noir", "negative_prompt": "bright"}
]}
```
"""


# ---------------------------------------------------------------------------
# 1. style 分析：任何失败都不再静默返回 mock 推荐
# ---------------------------------------------------------------------------

class TestStyleAnalysisNoSilentMock:

    def test_bad_json_raises_polish_error_not_mock(self):
        """失败注入：LLM 返回彻底坏掉的 JSON。

        T-B2 步骤2升级后走 _structured_llm_call：坏 JSON 连重试 3 次仍
        无法通过 schema 校验 → schema_validation_error（而不是静默 mock）。
        """
        llm = FakeLLM(chat_impl=lambda m, **k: "这不是JSON{{{ 坏掉的输出")
        sp = _make_processor(llm)

        with pytest.raises(PolishError) as exc_info:
            sp.analyze_script_for_styles("剧本内容")

        assert exc_info.value.reason == "schema_validation_error"
        assert len(llm.calls) == 3  # 反馈式重试确实发生

    def test_truncated_json_recovered_by_json_repair(self):
        """失败注入：截断的 JSON。

        T-B2 步骤2：json_repair 兜底能修复截断 → 返回抢救出的部分数据
        （不抛错、更不静默 mock）。修复后仍缺字段则会在 schema 层被拒。
        """
        truncated = '{"recommendations": [{"name": "Cinematic Realism", "desc'
        llm = FakeLLM(chat_impl=lambda m, **k: truncated)
        sp = _make_processor(llm)

        recs = sp.analyze_script_for_styles("剧本内容")

        assert len(recs) == 1
        assert recs[0]["name"] == "Cinematic Realism"

    def test_api_error_raises_polish_error_not_mock(self):
        """失败注入：LLM 调用本身抛错（网络/鉴权/限流）。"""
        llm = FakeLLM(chat_exc=RuntimeError("connection reset by peer"))
        sp = _make_processor(llm)

        with pytest.raises(PolishError) as exc_info:
            sp.analyze_script_for_styles("剧本内容")

        assert exc_info.value.reason == "api_error"

    def test_unconfigured_raises_polish_error_not_mock(self):
        """失败注入：LLM 未配置（缺 key）。"""
        llm = FakeLLM(configured=False)
        sp = _make_processor(llm)

        with pytest.raises(PolishError) as exc_info:
            sp.analyze_script_for_styles("剧本内容")

        assert exc_info.value.reason == "is_configured_false"
        assert llm.calls == []  # 根本不应发起调用

    def test_success_path_still_returns_real_recommendations(self):
        """对照组：正常 JSON 输出仍然正常返回（不被误伤）。"""
        llm = FakeLLM(chat_impl=lambda m, **k: _VALID_STYLE_JSON)
        sp = _make_processor(llm)

        recs = sp.analyze_script_for_styles("剧本内容")

        assert len(recs) == 3
        assert recs[0]["name"] == "Cinematic Realism"
        assert all(rec["id"].startswith("ai-rec-") for rec in recs)


# ---------------------------------------------------------------------------
# 2. storyboard 分析：未配置不再静默返回 1 帧 mock 分镜
# ---------------------------------------------------------------------------

class TestStoryboardNoSilentMock:

    def test_unconfigured_raises_polish_error_not_mock_frames(self):
        llm = FakeLLM(configured=False)
        sp = _make_processor(llm)

        with pytest.raises(PolishError) as exc_info:
            sp.analyze_to_storyboard("剧本文本", {"characters": [], "scenes": [], "props": []})

        assert exc_info.value.reason == "is_configured_false"


# ---------------------------------------------------------------------------
# 3. pipeline 实体名匹配：失败不再静默丢弃/兜底
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline(tmp_path):
    """与 test_pipeline 相同的模式：临时数据文件，生成器全部打桩。"""
    with patch("src.apps.comic_gen.pipeline.ScriptProcessor"), \
         patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        p = ComicGenPipeline()
    p.data_file = str(tmp_path / "projects.json")
    p.series_data_file = str(tmp_path / "series.json")
    p.library_data_file = str(tmp_path / "library_assets.json")
    p.scripts = {}
    p.series_store = {}
    return p


def _make_script() -> Script:
    now = time.time()
    return Script(
        id=str(uuid.uuid4()),
        title="失败注入测试",
        original_text="原文",
        characters=[Character(id="char-leilei", name="李雷", description="主角")],
        scenes=[Scene(id="scene-bedroom", name="卧室", description="夜晚的卧室")],
        props=[Prop(id="prop-phone", name="手机", description="震动中的手机")],
        created_at=now,
        updated_at=now,
    )


class StubScriptProcessor:
    """只暴露 analyze_to_storyboard，返回预置的 LLM 原始帧。"""

    def __init__(self, raw_frames):
        self._raw_frames = raw_frames

    def analyze_to_storyboard(self, text, entities_json, custom_extraction_prompt=""):
        return self._raw_frames


class TestPipelineUnresolvedRefs:

    def test_unmatched_char_and_prop_names_recorded_not_dropped(self, pipeline, caplog):
        script = _make_script()
        pipeline.scripts[script.id] = script
        pipeline.script_processor = StubScriptProcessor([
            {
                "scene_ref_name": "卧室",
                "character_ref_names": ["李雷", "幽灵人"],
                "prop_ref_names": ["手机", "不存在的剑"],
                "action_summary": "李雷拿起手机",
                "duration": 4,
            },
        ])
        caplog.set_level(logging.WARNING)

        result = pipeline.analyze_text_to_frames(script.id, "文本")

        frame = result.frames[0]
        # 匹配成功的照常解析
        assert frame.character_ids == ["char-leilei"]
        assert frame.prop_ids == ["prop-phone"]
        assert frame.scene_id == "scene-bedroom"
        # 匹配失败的不再被静默丢弃：原始引用保留在帧上
        assert frame.unresolved_character_refs == ["幽灵人"]
        assert frame.unresolved_prop_refs == ["不存在的剑"]
        assert frame.unresolved_scene_ref is None
        # 且有 warning 日志
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("幽灵人" in r.getMessage() for r in warnings)
        assert any("不存在的剑" in r.getMessage() for r in warnings)

    def test_unmatched_scene_name_falls_back_with_recorded_ref(self, pipeline, caplog):
        script = _make_script()
        pipeline.scripts[script.id] = script
        pipeline.script_processor = StubScriptProcessor([
            {
                "scene_ref_name": "不存在的场景",
                "character_ref_names": ["李雷"],
                "prop_ref_names": [],
                "action_summary": "李雷站立",
                "duration": 3,
            },
        ])
        caplog.set_level(logging.WARNING)

        result = pipeline.analyze_text_to_frames(script.id, "文本")

        frame = result.frames[0]
        # 兜底行为保留（下游依赖 scene_id 非空），但不再无声
        assert frame.scene_id == "scene-bedroom"
        assert frame.unresolved_scene_ref == "不存在的场景"
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("不存在的场景" in r.getMessage() for r in warnings)

    def test_fully_resolved_frame_has_no_unresolved_refs(self, pipeline):
        """对照组：全部匹配时不产生任何 unresolved 标记。"""
        script = _make_script()
        pipeline.scripts[script.id] = script
        pipeline.script_processor = StubScriptProcessor([
            {
                "scene_ref_name": "卧室",
                "character_ref_names": ["李雷"],
                "prop_ref_names": ["手机"],
                "action_summary": "李雷拿起手机",
                "duration": 3,
            },
        ])

        result = pipeline.analyze_text_to_frames(script.id, "文本")

        frame = result.frames[0]
        assert frame.unresolved_scene_ref is None
        assert frame.unresolved_character_refs == []
        assert frame.unresolved_prop_refs == []

    def test_legacy_frames_roundtrip_with_default_unresolved(self, tmp_path):
        """老项目数据（无 unresolved_* 字段）加载不受影响。"""
        from src.apps.comic_gen.models import StoryboardFrame

        legacy = StoryboardFrame(
            id="f1",
            scene_id="s1",
            character_ids=[],
            prop_ids=[],
            action_description="旧数据",
        )
        dumped = legacy.model_dump()
        assert "unresolved_scene_ref" in dumped
        reloaded = StoryboardFrame(**dumped)
        assert reloaded.unresolved_character_refs == []
