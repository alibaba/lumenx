import base64

import requests

from src.models.image import WanxImageModel


PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+"
    "X2VINQAAAABJRU5ErkJggg=="
)


class TestWan26ImageLocalReferenceFallback:
    def test_local_reference_image_uses_base64_payload_without_oss(self, monkeypatch, tmp_path):
        ref_path = tmp_path / "reference.png"
        ref_path.write_bytes(base64.b64decode(PNG_1X1_BASE64))

        captured_payload = {}

        class FakeUploader:
            def __init__(self, *args, **kwargs):
                self.is_configured = False
                self.base_path = "lumenx"

        class FakeCreateResponse:
            status_code = 200
            text = (
                '{"request_id":"req-1","output":{"task_id":"task-1","task_status":"PENDING"}}'
            )

            def json(self):
                return {
                    "request_id": "req-1",
                    "output": {"task_id": "task-1", "task_status": "PENDING"},
                }

        class FakePollResponse:
            status_code = 200

            def json(self):
                return {
                    "output": {
                        "task_status": "SUCCEEDED",
                        "choices": [
                            {
                                "message": {
                                    "content": [{"image": "https://example.com/generated.png"}]
                                }
                            }
                        ],
                    }
                }

        def fake_post(url, headers=None, json=None, timeout=None):
            captured_payload["json"] = json
            return FakeCreateResponse()

        def fake_get(url, headers=None, timeout=None):
            return FakePollResponse()

        monkeypatch.setattr("src.models.image.OSSImageUploader", FakeUploader)
        monkeypatch.setattr(
            "src.models.image.get_provider_base_url",
            lambda _, **kwargs: "https://dashscope.test",
        )
        monkeypatch.setattr(requests, "post", fake_post)
        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr("time.sleep", lambda _: None)

        model = WanxImageModel(
            {"params": {"i2i_model_name": "wan2.6-image"}},
            env={"DASHSCOPE_API_KEY": "test-dashscope-key"},
        )

        image_url = model._generate_wan26_image_http(
            prompt="keep the same character",
            size="1280*1280",
            n=1,
            negative_prompt="bad anatomy",
            ref_image_paths=[str(ref_path)],
        )

        assert image_url == "https://example.com/generated.png"

        content = captured_payload["json"]["input"]["messages"][0]["content"]
        image_entries = [item for item in content if "image" in item]

        assert len(image_entries) == 1
        assert image_entries[0]["image"].startswith("data:image/png;base64,")
        assert content[-1]["text"] == "keep the same character"
