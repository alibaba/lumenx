"""T-B6: 导演方法论 prompt 注入快照测试。

防未来误删注入段：四类跨镜连续性清单必须仍在分镜润色 prompt 里，
R2V 第 9 条跨镜连续规则、质检评审 prompt 常量的关键结构必须完整。
（方法论来源 zenstory-ai/drama-skills，MIT——只抄规范语义。）
"""
from src.apps.comic_gen.llm import (
    CONTINUITY_CHECKLIST,
    DEFAULT_QA_REVIEW_PROMPT,
    DEFAULT_R2V_POLISH_PROMPT,
    DEFAULT_STORYBOARD_EXTRACTION_PROMPT,
    DEFAULT_STORYBOARD_POLISH_PROMPT,
    format_continuity_checklist_block,
)


class TestContinuityChecklistInjection:
    def test_checklist_has_four_categories(self):
        """清单 = 轴线/站位/视线/持物四类，语义要点齐全。"""
        assert len(CONTINUITY_CHECKLIST) == 4
        categories = [cat for cat, _ in CONTINUITY_CHECKLIST]
        assert categories == ["轴线", "站位", "视线", "持物"]
        by_cat = dict(CONTINUITY_CHECKLIST)
        # 轴线：180° / 屏幕方向
        assert "180°" in by_cat["轴线"] and "屏幕方向" in by_cat["轴线"]
        # 站位：禁止镜外瞬移
        assert "瞬移" in by_cat["站位"]
        # 视线：与实际方位一致
        assert "方位" in by_cat["视线"]
        # 持物：交接写接触者与落点
        assert "落点" in by_cat["持物"]

    def test_storyboard_polish_prompt_contains_injected_block(self):
        """注入必须真的发生：清单块原文完整出现在分镜润色 prompt 中。"""
        prompt = DEFAULT_STORYBOARD_POLISH_PROMPT
        assert "【跨镜连续性】" in prompt
        assert format_continuity_checklist_block() in prompt
        for cat, _ in CONTINUITY_CHECKLIST:
            assert f"- {cat}：" in prompt

    def test_no_residual_token(self):
        """@@TOKEN@@ 占位必须全部被替换，不允许残留到运行时 prompt。"""
        assert "@@CONTINUITY_CHECKLIST@@" not in DEFAULT_STORYBOARD_POLISH_PROMPT
        assert "@@SHOT_SIZES@@" not in DEFAULT_STORYBOARD_EXTRACTION_PROMPT
        assert "@@CAMERA_ANGLES@@" not in DEFAULT_STORYBOARD_EXTRACTION_PROMPT


class TestR2VContinuityRule:
    def test_r2v_polish_has_cross_shot_rule(self):
        """R2V 润色 prompt 必须保留第 9 条跨镜连续规则。"""
        assert "跨镜连续" in DEFAULT_R2V_POLISH_PROMPT
        for token in ("轴线", "站位", "视线", "持物"):
            assert token in DEFAULT_R2V_POLISH_PROMPT


class TestQAReviewPrompt:
    def test_verdict_and_severity_levels_complete(self):
        """评审结论四级 + 严重程度四级必须齐全（drama-skills review 语义）。"""
        for verdict in ("APPROVE", "APPROVE_WITH_NOTES", "REVISE", "PROVISIONAL"):
            assert verdict in DEFAULT_QA_REVIEW_PROMPT
        for severity in ("blocker", "major", "minor", "note"):
            assert severity in DEFAULT_QA_REVIEW_PROMPT

    def test_evidence_based_findings_required(self):
        """带证据 finding 与跨文档综合链是评审核心，不可删。"""
        assert "证据" in DEFAULT_QA_REVIEW_PROMPT
        assert "修订结果" in DEFAULT_QA_REVIEW_PROMPT
        assert "跨文档综合" in DEFAULT_QA_REVIEW_PROMPT
        # 链条次序：剧本事实 → 视觉设定 → 镜头职责 → 冻结关键帧 → 视频运动
        chain = ["剧本事实", "视觉设定", "镜头职责", "冻结关键帧", "视频运动"]
        pos = [DEFAULT_QA_REVIEW_PROMPT.index(token) for token in chain]
        assert pos == sorted(pos), "跨文档综合链次序被破坏"

    def test_json_output_format_braces_escaped(self):
        """输出格式 JSON 花括号按项目惯例写成 {{}}，未来 .format 注入安全。"""
        assert "findings" in DEFAULT_QA_REVIEW_PROMPT
        assert '"verdict"' in DEFAULT_QA_REVIEW_PROMPT
