"""T-B3: Kling API 2.0 媒体参数层单测（全 mock HTTP, 不发真实请求）。

覆盖:
- 媒体互斥三模式校验（单图 / 首尾帧对 / 多图参考, 构造层拒绝非法组合）
- multi_shot 校验（intelligent / custom 1-6 shots / 时长加总=总时长）
- options.watermark_info.enabled 透传
- 提交路径三分法（无媒体 → text-to-video, 有媒体 → image-to-video）
- contents 结构（reference_image 带 id 供 prompt @id 寻址）
"""
import pytest

from src.models.kling import KlingError, KlingModel


class _FakeResponse:
    def __init__(self, payload=None, content=b""):
        self.status_code = 200
        self._payload = payload or {}
        self.content = content
        self.text = str(self._payload)

    def json(self):
        return self._payload


def _install_mocks(monkeypatch, captured):
    """Mock 提交（记录 URL+body）/ 轮询（直接 succeeded）/ 下载。"""

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["post_called"] = True
        captured["submit_url"] = url
        captured["body"] = json
        return _FakeResponse(payload={"code": 0, "data": {"id": "task-1"}})

    def fake_get(url, headers=None, timeout=None):
        if "/tasks" in url:
            return _FakeResponse(payload={
                "code": 0,
                "data": [{
                    "status": "succeeded",
                    "outputs": [{"type": "video", "url": "https://example.com/v.mp4"}],
                }],
            })
        return _FakeResponse(content=b"video-bytes")

    monkeypatch.setattr("src.models.kling.requests.post", fake_post)
    monkeypatch.setattr("src.models.kling.requests.get", fake_get)
    monkeypatch.setattr("src.models.kling.time.sleep", lambda _: None)


def _make_model() -> KlingModel:
    return KlingModel({"api_key": "test-key"})


def _run_generate(tmp_path, monkeypatch, captured, **kwargs):
    _install_mocks(monkeypatch, captured)
    out = str(tmp_path / "out.mp4")
    kwargs.setdefault("prompt", "demo")
    return _make_model().generate(output_path=out, **kwargs)


# ── 媒体互斥三模式 ────────────────────────────────────────────────────────

class TestMediaModeValidation:
    def test_image_plus_references_rejected(self, monkeypatch, tmp_path):
        """验收: image（first_frame）与多图参考同传必须被拒, 且不发请求。"""
        captured = {"post_called": False}
        _install_mocks(monkeypatch, captured)
        with pytest.raises(KlingError, match="互斥"):
            _make_model().generate(
                prompt="demo",
                output_path=str(tmp_path / "o.mp4"),
                img_url="https://example.com/first.png",
                reference_images=["https://example.com/ref1.png"],
            )
        assert captured["post_called"] is False

    def test_tail_plus_references_rejected(self, tmp_path):
        captured = {"post_called": False}
        with pytest.raises(KlingError, match="互斥"):
            _make_model().generate(
                prompt="demo",
                output_path=str(tmp_path / "o.mp4"),
                tail_img_url="https://example.com/last.png",
                reference_images=["https://example.com/ref1.png"],
            )
        assert captured["post_called"] is False

    def test_last_frame_without_first_frame_rejected(self, tmp_path):
        """首尾帧对模式: last_frame 必须配 first_frame。"""
        with pytest.raises(KlingError, match="成对"):
            _make_model().generate(
                prompt="demo",
                output_path=str(tmp_path / "o.mp4"),
                tail_img_url="https://example.com/last.png",
            )

    def test_more_than_nine_references_rejected(self, tmp_path):
        refs = [f"https://example.com/ref{i}.png" for i in range(10)]
        with pytest.raises(KlingError, match="最多 9 条"):
            _make_model().generate(
                prompt="demo",
                output_path=str(tmp_path / "o.mp4"),
                reference_images=refs,
            )

    def test_duplicate_reference_ids_rejected(self, tmp_path):
        refs = [
            {"url": "https://example.com/a.png", "reference_id": "hero"},
            {"url": "https://example.com/b.png", "reference_id": "hero"},
        ]
        with pytest.raises(KlingError, match="唯一"):
            _make_model().generate(
                prompt="demo @hero",
                output_path=str(tmp_path / "o.mp4"),
                reference_images=refs,
            )

    def test_non_url_reference_rejected_without_request(self, tmp_path):
        with pytest.raises(KlingError, match="远程 URL"):
            _make_model().generate(
                prompt="demo",
                output_path=str(tmp_path / "o.mp4"),
                reference_images=["output/uploads/local.png"],
            )

    def test_local_file_first_frame_rejected(self, tmp_path):
        """API 2.0 语义: 本地文件不能直接进 contents, 必须先上传。"""
        with pytest.raises(KlingError, match="本地文件请先上传"):
            _make_model().generate(
                prompt="demo",
                output_path=str(tmp_path / "o.mp4"),
                img_path=str(tmp_path / "nonexistent.png"),
            )


# ── 三模式合法路径的 contents / 端点结构 ──────────────────────────────────

class TestValidMediaModes:
    def test_single_image_mode(self, monkeypatch, tmp_path):
        captured = {}
        _run_generate(tmp_path, monkeypatch, captured,
                      img_url="https://example.com/first.png")
        contents = captured["body"]["contents"]
        types = [c["type"] for c in contents]
        assert types == ["prompt", "first_frame"]
        assert captured["submit_url"].endswith("/image-to-video/kling-3.0")

    def test_first_last_frame_pair_mode(self, monkeypatch, tmp_path):
        captured = {}
        _run_generate(tmp_path, monkeypatch, captured,
                      img_url="https://example.com/first.png",
                      tail_img_url="https://example.com/last.png")
        contents = captured["body"]["contents"]
        types = [c["type"] for c in contents]
        assert types == ["prompt", "first_frame", "last_frame"]
        assert contents[1]["url"] == "https://example.com/first.png"
        assert contents[2]["url"] == "https://example.com/last.png"

    def test_multi_reference_mode_with_ids(self, monkeypatch, tmp_path):
        captured = {}
        _run_generate(tmp_path, monkeypatch, captured,
                      prompt="英雄 @hero 走进 @scene",
                      reference_images=[
                          {"url": "https://example.com/a.png", "reference_id": "hero"},
                          {"url": "https://example.com/b.png", "reference_id": "scene"},
                      ])
        contents = captured["body"]["contents"]
        ref_entries = [c for c in contents if c["type"] == "reference_image"]
        assert len(ref_entries) == 2
        assert ref_entries[0] == {"type": "reference_image",
                                  "url": "https://example.com/a.png", "id": "hero"}
        assert ref_entries[1] == {"type": "reference_image",
                                  "url": "https://example.com/b.png", "id": "scene"}
        assert captured["submit_url"].endswith("/image-to-video/kling-3.0")

    def test_plain_url_references_have_no_id(self, monkeypatch, tmp_path):
        captured = {}
        _run_generate(tmp_path, monkeypatch, captured,
                      reference_images=["https://example.com/a.png"])
        ref_entries = [c for c in captured["body"]["contents"]
                       if c["type"] == "reference_image"]
        assert ref_entries == [{"type": "reference_image",
                                "url": "https://example.com/a.png"}]

    def test_no_media_goes_text_to_video(self, monkeypatch, tmp_path):
        captured = {}
        _run_generate(tmp_path, monkeypatch, captured)
        assert captured["submit_url"].endswith("/text-to-video/kling-3.0")
        assert captured["post_called"] is True


# ── multi_shot ───────────────────────────────────────────────────────────

class TestMultiShot:
    def test_intelligent_mode_passes_through(self, monkeypatch, tmp_path):
        captured = {}
        _run_generate(tmp_path, monkeypatch, captured,
                      multi_shot={"mode": "intelligent"})
        assert captured["body"]["settings"]["multi_shot"] == {"mode": "intelligent"}

    def test_custom_shots_summing_to_duration_accepted(self, monkeypatch, tmp_path):
        """验收: custom 各 shot 时长加总=总时长时放行。"""
        captured = {}
        _run_generate(tmp_path, monkeypatch, captured,
                      duration=10,
                      multi_shot={"mode": "custom", "shots": [
                          {"prompt": "镜头一", "duration": 4},
                          {"prompt": "镜头二", "duration": 6},
                      ]})
        settings = captured["body"]["settings"]
        assert settings["duration"] == 10
        assert settings["multi_shot"]["shots"][0]["duration"] == 4

    def test_custom_shots_sum_mismatch_rejected(self, tmp_path):
        """验收: 时长加总≠总时长必须被拒。"""
        with pytest.raises(KlingError, match="加总"):
            _make_model().generate(
                prompt="demo",
                output_path=str(tmp_path / "o.mp4"),
                duration=10,
                multi_shot={"mode": "custom", "shots": [
                    {"prompt": "镜头一", "duration": 4},
                    {"prompt": "镜头二", "duration": 4},
                ]},
            )

    def test_custom_more_than_six_shots_rejected(self, tmp_path):
        shots = [{"prompt": f"s{i}", "duration": 1} for i in range(7)]
        with pytest.raises(KlingError, match="1..6"):
            _make_model().generate(
                prompt="demo",
                output_path=str(tmp_path / "o.mp4"),
                duration=7,
                multi_shot={"mode": "custom", "shots": shots},
            )

    def test_invalid_mode_rejected(self, tmp_path):
        with pytest.raises(KlingError, match="intelligent/custom"):
            _make_model().generate(
                prompt="demo",
                output_path=str(tmp_path / "o.mp4"),
                multi_shot={"mode": "turbo"},
            )

    def test_non_positive_shot_duration_rejected(self, tmp_path):
        with pytest.raises(KlingError, match="正数"):
            _make_model().generate(
                prompt="demo",
                output_path=str(tmp_path / "o.mp4"),
                duration=5,
                multi_shot={"mode": "custom", "shots": [{"prompt": "s", "duration": 0}]},
            )

    def test_multi_shot_omitted_not_in_settings(self, monkeypatch, tmp_path):
        """不传 multi_shot 时 settings 不应有该字段（官方字段出现即开启语义）。"""
        captured = {}
        _run_generate(tmp_path, monkeypatch, captured)
        assert "multi_shot" not in captured["body"]["settings"]


# ── watermark ────────────────────────────────────────────────────────────

class TestWatermark:
    def test_watermark_false_writes_disabled(self, monkeypatch, tmp_path):
        captured = {}
        _run_generate(tmp_path, monkeypatch, captured, watermark=False)
        assert captured["body"]["options"]["watermark_info"] == {"enabled": False}

    def test_watermark_true_writes_enabled(self, monkeypatch, tmp_path):
        captured = {}
        _run_generate(tmp_path, monkeypatch, captured, watermark=True)
        assert captured["body"]["options"]["watermark_info"] == {"enabled": True}

    def test_watermark_omitted_not_in_options(self, monkeypatch, tmp_path):
        captured = {}
        _run_generate(tmp_path, monkeypatch, captured)
        assert "watermark_info" not in captured["body"]["options"]


# ── 全流程 happy path（提交+轮询+下载） ──────────────────────────────────

class TestEndToEnd:
    def test_full_flow_multi_reference(self, monkeypatch, tmp_path):
        captured = {}
        out_path, _elapsed = _run_generate(
            tmp_path, monkeypatch, captured,
            prompt="角色A与角色B对视",
            reference_images=[
                {"url": "https://example.com/a.png", "reference_id": "role_a"},
                {"url": "https://example.com/b.png", "reference_id": "role_b"},
            ],
            watermark=False,
        )
        assert out_path.endswith("out.mp4")
        assert (tmp_path / "out.mp4").read_bytes() == b"video-bytes"
        # Bearer 头
        # （headers 在 fake_post 里未记录, 这里验证 body 完整性即可）
        body = captured["body"]
        assert body["settings"]["duration"] == 5
        assert body["settings"]["audio"] == "off"
        assert body["settings"]["resolution"] == "1080p"
