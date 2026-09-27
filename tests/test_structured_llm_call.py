"""T-B2 可靠性步骤 2：_structured_llm_call + json_repair + 枚举强制。

覆盖：
  - _structured_llm_call 的成功 / 报错回喂重试 / 全败抛 PolishError；
  - think 块剥离、markdown 剥壳、json_repair 兜底的解析链；
  - RawStoryboardFrame 枚举 strict 归一（别名识别 / 非法值触发重试）；
  - StoryboardFrame / CameraMovementData 持久化边界的 tolerant 归一
    （老项目数据永不因枚举炸掉）。
"""

import pytest
from pydantic import ValidationError

from src.apps.comic_gen.llm import (
    PolishError, ScriptProcessor,
    _loads_llm_json, _strip_think_blocks,
)
from src.apps.comic_gen.models import (
    CameraMovementData, RawStoryboardFrame, StoryboardFrame, StoryboardFrameList,
)


class FakeLLM:
    """按脚本逐次返回 chat 输出，记录每次收到的消息。"""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = []

    @property
    def is_configured(self):
        return True

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self._outputs.pop(0)


def _make_processor(llm) -> ScriptProcessor:
    sp = ScriptProcessor()
    sp.llm = llm
    return sp


# ---------------------------------------------------------------------------
# 解析链单元
# ---------------------------------------------------------------------------

class TestParseChain:

    def test_strip_think_blocks_paired(self):
        assert _strip_think_blocks("<think>推理过程</think>{\"a\": 1}") == '{"a": 1}'

    def test_strip_think_blocks_unclosed(self):
        out = _strip_think_blocks('{"a": 1}<think>截断的思考')
        assert out == '{"a": 1}'

    def test_loads_llm_json_fenced(self):
        assert _loads_llm_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_loads_llm_json_repair_truncated(self):
        assert _loads_llm_json('{"a": [1, 2,') == {"a": [1, 2]}

    def test_loads_llm_json_garbage_returns_container_or_none(self):
        # 彻底的垃圾 → json_repair 产出容器壳或 None，都不抛异常
        result = _loads_llm_json("这不是JSON{{{ 坏掉的输出")
        assert result is None or isinstance(result, (dict, list))

    def test_loads_llm_json_empty(self):
        assert _loads_llm_json("") is None
        assert _loads_llm_json("   ") is None


# ---------------------------------------------------------------------------
# _structured_llm_call 核心循环
# ---------------------------------------------------------------------------

class TestStructuredLLMCall:

    def test_success_first_attempt(self):
        llm = FakeLLM(['{"frames": [{"scene_ref_name": "桥边", "action_summary": "叶墨走过"}]}'])
        sp = _make_processor(llm)

        result = sp._structured_llm_call("sys", "user", StoryboardFrameList)

        assert isinstance(result, StoryboardFrameList)
        assert len(result.frames) == 1
        assert len(llm.calls) == 1

    def test_validation_failure_triggers_feedback_retry(self):
        """第 1 次 schema 校验失败（自创景别）→ 错误摘要回喂 → 第 2 次成功。"""
        llm = FakeLLM([
            '{"frames": [{"scene_ref_name": "卧室", "action_summary": "x", "shot_size": "自创景别"}]}',
            '{"frames": [{"scene_ref_name": "卧室", "action_summary": "x", "shot_size": "特写"}]}',
        ])
        sp = _make_processor(llm)

        result = sp._structured_llm_call(
            "sys", "user", StoryboardFrameList,
        )

        assert len(llm.calls) == 2
        assert result.frames[0].shot_size == "特写"
        # 回喂内容包含错误摘要
        feedback = llm.calls[1]["messages"][1]["content"]
        assert "上次输出错误" in feedback
        assert "景别" in feedback

    def test_all_attempts_fail_raises_schema_validation_error(self):
        llm = FakeLLM(["垃圾1", "垃圾2", "垃圾3"])
        sp = _make_processor(llm)

        with pytest.raises(PolishError) as exc_info:
            sp._structured_llm_call("sys", "user", StoryboardFrameList)

        assert exc_info.value.reason == "schema_validation_error"
        assert len(llm.calls) == 3
        # 第 2/3 次调用应携带上一轮的错误摘要（回喂）
        second_user = llm.calls[1]["messages"][1]["content"]
        third_user = llm.calls[2]["messages"][1]["content"]
        assert "上次输出错误" in second_user
        assert "上次输出错误" in third_user

    def test_api_error_surfaces_immediately(self):
        class BoomLLM(FakeLLM):
            def chat(self, messages, **kwargs):
                self.calls.append(messages)
                raise RuntimeError("network down")

        llm = BoomLLM([])
        sp = _make_processor(llm)

        with pytest.raises(PolishError) as exc_info:
            sp._structured_llm_call("sys", "user", StoryboardFrameList)

        assert exc_info.value.reason == "api_error"
        assert len(llm.calls) == 1  # API 错误不重试

    def test_think_wrapped_json_accepted(self):
        llm = FakeLLM(['<think>我先想想……\n多行推理</think>\n{"frames": [{"scene_ref_name": "桥边", "action_summary": "走"}]}'])
        sp = _make_processor(llm)

        result = sp._structured_llm_call("sys", "user", StoryboardFrameList)
        assert result.frames[0].scene_ref_name == "桥边"


# ---------------------------------------------------------------------------
# 枚举强制：LLM 边界 strict / 持久化边界 tolerant
# ---------------------------------------------------------------------------

class TestEnumEnforcement:

    def test_raw_frame_alias_coercion(self):
        raw = RawStoryboardFrame(shot_size="close-up", camera_angle="eye level", duration="5秒")
        assert raw.shot_size == "特写"
        assert raw.camera_angle == "平视"
        assert raw.duration == 5

    def test_raw_frame_canonical_passthrough(self):
        raw = RawStoryboardFrame(shot_size="大远景", camera_angle="荷兰角")
        assert raw.shot_size == "大远景"
        assert raw.camera_angle == "荷兰角"

    def test_raw_frame_unknown_enum_rejected(self):
        """LLM 边界硬校验：自创景别 → ValidationError（触发回喂重试）。"""
        with pytest.raises(ValidationError):
            RawStoryboardFrame(shot_size="微距")

    def test_storyboard_frame_tolerant_unknown_to_none(self):
        """持久化边界：老项目/旧 mock 数据里的非法景别 → None + warning，不炸。"""
        frame = StoryboardFrame(id="f", scene_id="s", shot_size="超超级大特写")
        assert frame.shot_size is None

    def test_storyboard_frame_legacy_english_alias_maps(self):
        """旧数据里的 legacy 英文写法被别名归一而不是被丢弃。"""
        frame = StoryboardFrame(id="f", scene_id="s", shot_size="Medium Shot")
        assert frame.shot_size == "中景"

    def test_storyboard_frame_alias_and_canonical(self):
        frame = StoryboardFrame(id="f", scene_id="s", shot_size="extreme long shot")
        assert frame.shot_size == "大远景"
        frame2 = StoryboardFrame(id="f", scene_id="s", shot_size="全景")
        assert frame2.shot_size == "全景"

    def test_camera_movement_data_tolerant(self):
        cm = CameraMovementData(primary="pan-left", speed="SLOW")
        assert cm.primary == "pan_left"
        assert cm.speed == "slow"
        cm2 = CameraMovementData(primary="缓慢推近这样的自由文本", description="保留描述")
        assert cm2.primary is None
        assert cm2.speed == "normal"
        assert cm2.description == "保留描述"

    def test_camera_movement_data_pure_values_fstring_safe(self):
        """回归锁：Literal 值是纯 str，f-string / dict 查找不被 Enum 污染。"""
        cm = CameraMovementData(primary="push_in", speed="slow")
        assert f"{cm.primary}" == "push_in"
        assert {"push_in": "推近"}.get(cm.primary, cm.primary) == "推近"


# ---------------------------------------------------------------------------
# analyze_to_storyboard 集成（schema 化后帧字典带规范枚举值）
# ---------------------------------------------------------------------------

class TestAnalyzeToStoryboardStructured:

    def test_frames_return_canonical_enum_strings(self):
        llm = FakeLLM(['{"frames": [{"scene_ref_name": "卧室", "character_ref_names": ["叶墨"], '
                       '"prop_ref_names": ["手机"], "action_summary": "手机震动", '
                       '"shot_size": "close-up", "camera_angle": "high angle", '
                       '"camera_movement": "静止", "duration": "4秒"}]}'])
        sp = _make_processor(llm)

        frames = sp.analyze_to_storyboard("剧本", {"characters": [], "scenes": [], "props": []})

        assert frames[0]["shot_size"] == "特写"
        assert frames[0]["camera_angle"] == "俯视"
        assert frames[0]["duration"] == 4

    def test_invalid_enum_retried_then_schema_error(self):
        llm = FakeLLM([
            '{"frames": [{"scene_ref_name": "卧室", "action_summary": "x", "shot_size": "自创景别"}]}',
            '{"frames": [{"scene_ref_name": "卧室", "action_summary": "x", "shot_size": "还是自创"}]}',
            '{"frames": [{"scene_ref_name": "卧室", "action_summary": "x", "shot_size": "仍然自创"}]}',
        ])
        sp = _make_processor(llm)

        with pytest.raises(PolishError) as exc_info:
            sp.analyze_to_storyboard("剧本", {"characters": [], "scenes": [], "props": []})

        assert exc_info.value.reason == "schema_validation_error"
        assert len(llm.calls) == 3

    def test_empty_frames_rejected_and_retried(self):
        llm = FakeLLM([
            '{"frames": []}',
            '{"frames": [{"scene_ref_name": "卧室", "action_summary": "有内容"}]}',
        ])
        sp = _make_processor(llm)

        frames = sp.analyze_to_storyboard("剧本", {"characters": [], "scenes": [], "props": []})
        assert len(frames) == 1
        # 第一次调用后应回喂 "frames 不能为空" 的错误摘要
        assert "frames" in llm.calls[1]["messages"][1]["content"]


# ---------------------------------------------------------------------------
# prompt 与 schema 一张皮
# ---------------------------------------------------------------------------

class TestPromptSchemaAlignment:

    def test_extraction_prompt_enum_lines_match_models(self):
        from src.apps.comic_gen.llm import DEFAULT_STORYBOARD_EXTRACTION_PROMPT
        from src.apps.comic_gen.models import ShotSizeEnum, CameraAngleEnum

        assert "@@" not in DEFAULT_STORYBOARD_EXTRACTION_PROMPT  # 占位符已全部替换
        for value in ShotSizeEnum:
            assert value.value in DEFAULT_STORYBOARD_EXTRACTION_PROMPT
        for value in CameraAngleEnum:
            assert value.value in DEFAULT_STORYBOARD_EXTRACTION_PROMPT

    def test_literal_options_derive_from_enums(self):
        """一张皮锁：Literal 选项集与 Enum 值集严格相等（顺序无关）。"""
        from typing import get_args
        from src.apps.comic_gen import models as m
        from src.apps.comic_gen.models import (
            ShotSizeEnum, CameraAngleEnum, CameraMovementType, CameraSpeed,
        )

        assert set(get_args(m.ShotSizeValue)) == {e.value for e in ShotSizeEnum}
        assert set(get_args(m.CameraAngleValue)) == {e.value for e in CameraAngleEnum}
        assert set(get_args(m.CameraMovementValue)) == {e.value for e in CameraMovementType}
        assert set(get_args(m.CameraSpeedValue)) == {e.value for e in CameraSpeed}
