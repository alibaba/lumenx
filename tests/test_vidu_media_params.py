"""T-B3: Vidu reference2video 多参考 + start-end2video 首尾帧单测（全 mock HTTP）。

覆盖:
- reference2video: 1-7 张参考图、@主题名寻址校验（subjects 每主体 ≤3 图）、
  movement_amplitude / seed 透传
- start-end2video: 首尾帧独立端点, images=[首帧, 尾帧], 不与多参考混用
- 构造层校验非法组合（不发请求）
"""
import pytest

from src.models.vidu import ViduModel, _validate_reference_inputs


class _FakeResponse:
    def __init__(self, payload=None, content=b""):
        self.status_code = 200
        self._payload = payload or {}
        self.content = content
        self.text = str(self._payload)

    def json(self):
        return self._payload


def _install_mocks(monkeypatch, captured):
    def fake_post(url, headers=None, json=None, timeout=None):
        captured["post_called"] = True
        captured["submit_url"] = url
        captured["body"] = json
        return _FakeResponse(payload={"task_id": "vidu-task-1"})

    def fake_get(url, headers=None, timeout=None):
        if "/tasks" in url:
            return _FakeResponse(payload={
                "state": "success",
                "creations": [{"url": "https://example.com/v.mp4"}],
            })
        return _FakeResponse(content=b"video-bytes")

    monkeypatch.setattr("src.models.vidu.requests.post", fake_post)
    monkeypatch.setattr("src.models.vidu.requests.get", fake_get)
    monkeypatch.setattr("src.models.vidu.time.sleep", lambda _: None)


def _make_model() -> ViduModel:
    return ViduModel({"api_key": "test-key"})


def _run_generate(tmp_path, monkeypatch, captured, **kwargs):
    _install_mocks(monkeypatch, captured)
    out = str(tmp_path / "out.mp4")
    kwargs.setdefault("prompt", "demo")
    return _make_model().generate(output_path=out, **kwargs)


# ── 构造层校验（纯函数） ──────────────────────────────────────────────────

class TestReferenceValidation:
    def test_eight_references_rejected(self):
        refs = [f"https://example.com/{i}.png" for i in range(8)]
        with pytest.raises(ValueError, match="1..7"):
            _validate_reference_inputs(refs, None, "prompt")

    def test_zero_references_rejected(self):
        with pytest.raises(ValueError, match="1..7"):
            _validate_reference_inputs([], None, "prompt")

    def test_subjects_length_mismatch_rejected(self):
        refs = ["https://example.com/a.png", "https://example.com/b.png"]
        with pytest.raises(ValueError, match="一一对应"):
            _validate_reference_inputs(refs, ["角色A"], "prompt")

    def test_more_than_three_images_per_subject_rejected(self):
        refs = [f"https://example.com/{i}.png" for i in range(4)]
        subjects = ["角色A"] * 4
        with pytest.raises(ValueError, match="每主体最多 3"):
            _validate_reference_inputs(refs, subjects, "prompt @角色A")

    def test_valid_seven_references_pass(self):
        refs = [f"https://example.com/{i}.png" for i in range(7)]
        subjects = ["角色A"] * 3 + ["角色B"] * 3 + [None]
        _validate_reference_inputs(refs, subjects, "@角色A 与 @角色B 对视")


# ── generate 层互斥与端点分派 ────────────────────────────────────────────

class TestGenerateModeDispatch:
    def test_r2v_mixed_with_tail_rejected_without_request(self, monkeypatch, tmp_path):
        """验收语义: 首尾帧与 reference2video 不能混用。"""
        captured = {"post_called": False}
        _install_mocks(monkeypatch, captured)
        with pytest.raises(ValueError, match="混用"):
            _make_model().generate(
                prompt="demo",
                output_path=str(tmp_path / "o.mp4"),
                ref_image_urls=["https://example.com/a.png"],
                tail_img_url="https://example.com/last.png",
                img_url="https://example.com/first.png",
            )
        assert captured["post_called"] is False

    def test_tail_without_first_frame_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="成对"):
            _make_model().generate(
                prompt="demo",
                output_path=str(tmp_path / "o.mp4"),
                tail_img_url="https://example.com/last.png",
            )


# ── reference2video 请求体 ───────────────────────────────────────────────

class TestReference2Video:
    def test_multi_reference_body_and_endpoint(self, monkeypatch, tmp_path):
        captured = {}
        _run_generate(tmp_path, monkeypatch, captured,
                      prompt="@角色A 与 @场景B 对视",
                      ref_image_urls=[
                          "https://example.com/a.png",
                          "https://example.com/b.png",
                      ],
                      ref_subjects=["角色A", "场景B"],
                      movement_amplitude="large",
                      seed=123)
        assert captured["submit_url"].endswith("/reference2video")
        body = captured["body"]
        assert body["model"] == "viduq2"
        assert body["images"] == ["https://example.com/a.png", "https://example.com/b.png"]
        assert "@角色A" in body["prompt"]
        assert body["movement_amplitude"] == "large"
        assert body["seed"] == 123

    def test_seven_references_accepted(self, monkeypatch, tmp_path):
        captured = {}
        refs = [f"https://example.com/{i}.png" for i in range(7)]
        _run_generate(tmp_path, monkeypatch, captured, ref_image_urls=refs)
        assert len(captured["body"]["images"]) == 7

    def test_full_flow_r2v(self, monkeypatch, tmp_path):
        captured = {}
        out_path, _ = _run_generate(
            tmp_path, monkeypatch, captured,
            prompt="@角色A 回头",
            ref_image_urls=["https://example.com/a.png"],
        )
        assert out_path.endswith("out.mp4")
        assert (tmp_path / "out.mp4").read_bytes() == b"video-bytes"


# ── start-end2video 请求体 ───────────────────────────────────────────────

class TestStartEnd2Video:
    def test_first_last_pair_body_and_endpoint(self, monkeypatch, tmp_path):
        captured = {}
        _run_generate(tmp_path, monkeypatch, captured,
                      prompt="从黄昏到夜晚",
                      img_url="https://example.com/first.png",
                      tail_img_url="https://example.com/last.png",
                      seed=7)
        assert captured["submit_url"].endswith("/start-end2video")
        body = captured["body"]
        assert body["model"] == "viduq1"
        assert body["images"] == ["https://example.com/first.png",
                                  "https://example.com/last.png"]
        assert body["seed"] == 7

    def test_full_flow_startend(self, monkeypatch, tmp_path):
        captured = {}
        out_path, _ = _run_generate(
            tmp_path, monkeypatch, captured,
            img_url="https://example.com/first.png",
            tail_img_url="https://example.com/last.png",
        )
        assert (tmp_path / "out.mp4").read_bytes() == b"video-bytes"
