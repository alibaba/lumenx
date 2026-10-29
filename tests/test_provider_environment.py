import os
from pathlib import Path

import dashscope
import pytest

from src.models.factory import ModelFactory
from src.models.image import WanxImageModel
from src.models.kling import KlingModel
from src.models.mulerouter import (
    MuleRouterImageModel,
    MuleRouterVideoModel,
    _get_base_url_for_model,
    _get_site,
    _run_mulerun_studio,
    _use_cli_backend,
)
from src.models.wanx import WanxModel
from src.models.vidu import ViduModel


def test_image_explicit_empty_env_ignores_ambient_dashscope_key(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-dashscope")

    model = WanxImageModel({"params": {"model_name": "wan2.6-t2i"}}, env={})

    assert model.api_key in (None, "")
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        model.generate("demo", "output/provider-env/image.png")


def test_image_uses_captured_env_for_key_base_url_and_oss(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-dashscope")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://ambient.example.invalid")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = dict(headers or {})
        return type(
            "Response",
            (),
            {
                "status_code": 200,
                "text": "{}",
                "json": lambda self: {
                    "output": {
                        "choices": [
                            {"message": {"content": [{"image": "https://example.invalid/out.png"}]}}
                        ]
                    }
                },
            },
        )()

    monkeypatch.setattr("src.models.image.requests.post", fake_post)

    model = WanxImageModel(
        {"params": {"model_name": "wan2.6-t2i"}},
        env={
            "DASHSCOPE_API_KEY": "owned-dashscope",
            "DASHSCOPE_BASE_URL": "https://owned.example.invalid",
        },
    )

    assert model._generate_wan26_http("demo", "1024*1024", 1) == "https://example.invalid/out.png"
    assert captured["url"].startswith("https://owned.example.invalid/")
    assert captured["headers"]["Authorization"] == "Bearer owned-dashscope"


def test_image_sdk_uses_explicit_api_key_without_mutating_global(monkeypatch):
    monkeypatch.setattr(dashscope, "api_key", "ambient-global")
    captured = {}

    class Response:
        status_code = 200
        output = {"url": "https://example.invalid/out.png"}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("src.models.image.ImageSynthesis.call", fake_call)

    model = WanxImageModel({}, env={"DASHSCOPE_API_KEY": "owned-dashscope"})

    assert model._generate_sdk("demo", "legacy-model", "1024*1024", 1, None, [], {}) == (
        "https://example.invalid/out.png"
    )
    assert captured["api_key"] == "owned-dashscope"
    assert dashscope.api_key == "ambient-global"


def test_image_sdk_uses_captured_base_address(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        output = {"url": "https://example.invalid/out.png"}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("src.models.image.ImageSynthesis.call", fake_call)

    model = WanxImageModel(
        {},
        env={
            "DASHSCOPE_API_KEY": "owned-dashscope",
            "DASHSCOPE_BASE_URL": "https://owned.example.invalid",
        },
    )

    model._generate_sdk("demo", "legacy-model", "1024*1024", 1, None, [], {})

    assert captured["api_key"] == "owned-dashscope"
    assert captured["base_address"] == "https://owned.example.invalid/api/v1"


def test_image_sdk_signs_owned_oss_object_key_with_captured_env(monkeypatch):
    monkeypatch.setenv("OSS_BASE_PATH", "ambient-root")
    captured = {}

    class Response:
        status_code = 200
        output = {"url": "https://example.invalid/out.png"}

    class FakeUploader:
        def __init__(self, *, env=None, output_root=None):
            self.base_path = "owned-root"
            self.is_configured = True
            captured["uploader_env"] = dict(env or {})

        def sign_url_for_api(self, object_key):
            captured["signed_object_key"] = object_key
            return f"https://owned-oss.invalid/{object_key}?signed=1"

    def fake_call(**kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("src.models.image.OSSImageUploader", FakeUploader)
    monkeypatch.setattr("src.models.image.ImageSynthesis.call", fake_call)

    model = WanxImageModel(
        {},
        env={
            "DASHSCOPE_API_KEY": "owned-dashscope",
            "OSS_BASE_PATH": "owned-root",
        },
    )

    assert model._generate_sdk(
        "demo",
        "legacy-model",
        "1024*1024",
        1,
        None,
        ["owned-root/ref.png"],
        {},
    ) == "https://example.invalid/out.png"
    assert captured["uploader_env"]["OSS_BASE_PATH"] == "owned-root"
    assert captured["signed_object_key"] == "owned-root/ref.png"
    assert captured["images"] == ["https://owned-oss.invalid/owned-root/ref.png?signed=1"]


def test_kling_explicit_empty_env_ignores_ambient_credentials_and_endpoint(monkeypatch):
    monkeypatch.setenv("KLING_ACCESS_KEY", "ambient-ak")
    monkeypatch.setenv("KLING_SECRET_KEY", "ambient-sk")
    monkeypatch.setenv("KLING_BASE_URL", "https://ambient.example.invalid")

    model = KlingModel({}, env={})

    assert model.access_key == ""
    assert model.secret_key == ""
    assert model._base_url() == "https://api-beijing.klingai.com/v1"
    with pytest.raises(RuntimeError, match="KLING_ACCESS_KEY"):
        model._auth_headers()


def test_vidu_explicit_empty_env_ignores_ambient_key_and_endpoint(monkeypatch):
    monkeypatch.setenv("VIDU_API_KEY", "ambient-vidu")
    monkeypatch.setenv("VIDU_BASE_URL", "https://ambient.example.invalid")

    model = ViduModel({}, env={})

    assert model.api_key == ""
    assert model._base_url() == "https://api.vidu.cn/ent/v2"
    with pytest.raises(RuntimeError, match="VIDU_API_KEY"):
        model._headers()


def test_factory_forwards_explicit_environment():
    env = {"KLING_ACCESS_KEY": "owned-ak", "KLING_SECRET_KEY": "owned-sk"}

    model = ModelFactory.create_model({"model.name": "kling", "model": {}}, env=env)

    assert isinstance(model, KlingModel)
    assert model.access_key == "owned-ak"
    assert model.secret_key == "owned-sk"


def test_factory_forwards_explicit_empty_environment_to_wanx(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-dashscope")

    model = ModelFactory.create_model({"model.name": "wanx", "model": {}}, env={})

    assert isinstance(model, WanxModel)
    with pytest.raises(RuntimeError, match="DashScope API key"):
        _ = model.api_key


def test_image_base_url_requires_env_aware_endpoint_helper(monkeypatch):
    model = WanxImageModel({}, env={"DASHSCOPE_BASE_URL": "https://owned.example.invalid"})
    monkeypatch.setattr("src.models.image.get_provider_base_url", lambda provider: "legacy")

    with pytest.raises(TypeError):
        model._dashscope_base_url()


def test_mulerouter_explicit_empty_env_ignores_ambient_http_and_cli(monkeypatch):
    monkeypatch.setenv("MULEROUTER_API_KEY", "ambient-router")
    monkeypatch.setenv("MULEROUTER_BASE_URL", "https://ambient.example.invalid")
    monkeypatch.setenv("MULEROUTER_SITE", "mulerun")
    monkeypatch.setenv("MULERUN_TOKEN", "ambient-token")
    monkeypatch.setattr("src.models.mulerouter._is_mulerun_cli_available", lambda env=None: True)

    model = MuleRouterVideoModel({}, env={})

    assert model._get_api_key() == ""
    assert model._get_site() == "mulerouter"
    assert model._get_base_url_for_model("bytedance/seedance") == "https://api.mulerouter.ai"
    assert model._use_cli_backend() is False
    with pytest.raises(RuntimeError, match="MULEROUTER_API_KEY"):
        model._auth_headers()


def test_mulerouter_module_helpers_keep_standalone_ambient_behavior(monkeypatch):
    monkeypatch.setenv("MULEROUTER_API_KEY", "ambient-router")
    monkeypatch.setenv("MULEROUTER_BASE_URL", "https://ambient.example.invalid")
    monkeypatch.setenv("MULEROUTER_SITE", "mulerun")

    assert _get_site() == "mulerun"
    assert _get_base_url_for_model("bytedance/seedance") == "https://ambient.example.invalid"
    assert _use_cli_backend() is False


def test_mulerouter_subprocess_env_strips_ambient_provider_credentials(monkeypatch):
    captured = {}

    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-dashscope")
    monkeypatch.setenv("MULEROUTER_API_KEY", "ambient-router")
    monkeypatch.setenv("MULERUN_TOKEN", "ambient-token")

    def fake_run(cmd, capture_output, text, timeout, env):
        captured["env"] = dict(env)
        return type("Result", (), {"returncode": 0, "stdout": '{"videos":["https://x.invalid/v.mp4"]}', "stderr": ""})()

    monkeypatch.setattr("src.models.mulerouter.subprocess.run", fake_run)

    result = _run_mulerun_studio(
        "bytedance/seedance-2.0/text-to-video",
        ["--prompt", "demo"],
        timeout=1,
        env={"MULERUN_TOKEN": "owned-token"},
    )

    assert result["videos"] == ["https://x.invalid/v.mp4"]
    assert captured["env"]["MULERUN_TOKEN"] == "owned-token"
    assert "DASHSCOPE_API_KEY" not in captured["env"]
    assert "MULEROUTER_API_KEY" not in captured["env"]
    assert captured["env"].get("PATH") == os.environ.get("PATH")


def test_mulerouter_cli_uses_explicit_flags_and_isolated_profile(monkeypatch):
    captured = {}
    log_calls = []
    monkeypatch.setenv("HOME", "/Users/example/ambient-home")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/Users/example/ambient-config")
    monkeypatch.setenv("XDG_CACHE_HOME", "/Users/example/ambient-cache")
    monkeypatch.setenv("XDG_DATA_HOME", "/Users/example/ambient-data")
    monkeypatch.setenv("MULERUN_TOKEN", "ambient-token")
    monkeypatch.setenv("MULEROUTER_BASE_URL", "https://ambient.example.invalid")
    monkeypatch.setenv("MULEROUTER_SITE", "mulerouter")

    def fake_run(cmd, capture_output, text, timeout, env):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env)
        return type("Result", (), {"returncode": 0, "stdout": '{"videos":["https://x.invalid/v.mp4"]}', "stderr": ""})()

    monkeypatch.setattr("src.models.mulerouter.subprocess.run", fake_run)
    monkeypatch.setattr("src.models.mulerouter.logger.info", lambda *args: log_calls.append(args))

    result = _run_mulerun_studio(
        "bytedance/seedance-2.0/text-to-video",
        ["--prompt", "demo"],
        timeout=1,
        env={
            "MULERUN_TOKEN": "owned-token",
            "MULERUN_BASE_URL": "https://owned-mulerun.example.invalid",
            "MULEROUTER_BASE_URL": "https://owned-router.example.invalid",
            "MULEROUTER_SITE": "mulerun",
            "MULERUN_SITE": "cn",
        },
    )

    assert result["videos"] == ["https://x.invalid/v.mp4"]
    assert captured["cmd"].count("--token") == 1
    assert captured["cmd"][captured["cmd"].index("--token") + 1] == "owned-token"
    assert captured["cmd"].count("--base-url") == 2
    assert captured["cmd"][captured["cmd"].index("--base-url") + 1] == "https://owned-mulerun.example.invalid"
    assert captured["cmd"][captured["cmd"].index("--base-url", captured["cmd"].index("--base-url") + 1) + 1] == "https://owned-router.example.invalid"
    assert captured["cmd"][captured["cmd"].index("--site") + 1] == "cn"
    assert captured["cmd"][captured["cmd"].index("--site", captured["cmd"].index("--site") + 1) + 1] == "mulerun"
    assert captured["env"]["HOME"] != "/Users/example/ambient-home"
    assert captured["env"]["XDG_CONFIG_HOME"].startswith(captured["env"]["HOME"])
    assert captured["env"]["XDG_CACHE_HOME"].startswith(captured["env"]["HOME"])
    assert captured["env"]["XDG_DATA_HOME"].startswith(captured["env"]["HOME"])
    assert captured["env"]["MULERUN_TOKEN"] == "owned-token"
    assert "MULEROUTER_BASE_URL" not in captured["env"]
    assert "MULEROUTER_SITE" not in captured["env"]
    rendered_logs = "\n".join(" ".join(str(part) for part in call) for call in log_calls)
    assert "owned-token" not in rendered_logs
    assert "<redacted>" in rendered_logs


def test_mulerouter_cli_explicit_mapping_uses_default_flags(monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout, env):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env)
        return type("Result", (), {"returncode": 0, "stdout": '{"images":["https://x.invalid/i.png"]}', "stderr": ""})()

    monkeypatch.setattr("src.models.mulerouter.subprocess.run", fake_run)

    result = _run_mulerun_studio(
        "openai/gpt-image-2/generation",
        ["--prompt", "demo"],
        timeout=1,
        env={"MULERUN_TOKEN": "owned-token"},
    )

    assert result["images"] == ["https://x.invalid/i.png"]
    assert captured["cmd"][captured["cmd"].index("--base-url") + 1] == "https://api.mulerun.com"
    assert captured["cmd"][captured["cmd"].index("--site") + 1] == "sg"
    assert captured["cmd"][captured["cmd"].index("--base-url", captured["cmd"].index("--base-url") + 1) + 1] == "https://api.mulerouter.ai"
    assert captured["cmd"][captured["cmd"].index("--site", captured["cmd"].index("--site") + 1) + 1] == "mulerouter"


def test_mulerouter_cli_errors_redact_token(monkeypatch):
    monkeypatch.setenv("MULERUN_TOKEN", "ambient-token")

    def fake_run(cmd, capture_output, text, timeout, env):
        return type(
            "Result",
            (),
            {
                "returncode": 7,
                "stdout": "",
                "stderr": "failed with owned-token and --token owned-token",
            },
        )()

    monkeypatch.setattr("src.models.mulerouter.subprocess.run", fake_run)

    with pytest.raises(RuntimeError) as raised:
        _run_mulerun_studio(
            "openai/gpt-image-2/generation",
            ["--prompt", "demo"],
            timeout=1,
            env={"MULERUN_TOKEN": "owned-token"},
        )

    message = str(raised.value)
    assert "owned-token" not in message
    assert "<redacted>" in message


def test_provider_media_uploader_receives_same_captured_env(monkeypatch, tmp_path):
    captured = {}
    image_path = tmp_path / "ref.png"
    image_path.write_bytes(b"png")

    class FakeUploader:
        def __init__(self, *, env=None, output_root=None):
            captured["env"] = dict(env or {})
            captured["output_root"] = output_root
            self.is_configured = True

        def upload_file(self, local_path, sub_path="", custom_filename=None):
            return "owned/ref.png"

        def sign_url_for_api(self, object_key):
            return f"https://oss.invalid/{object_key}"

    monkeypatch.setattr("src.models.image.OSSImageUploader", FakeUploader)

    model = WanxImageModel(
        {"output_root": str(tmp_path), "params": {"i2i_model_name": "wan2.6-image"}},
        env={"OSS_BASE_PATH": "owned", "DASHSCOPE_API_KEY": "owned"},
    )

    assert model._resolve_wan26_reference_image(str(image_path)).startswith("https://oss.invalid/")
    assert captured["env"]["OSS_BASE_PATH"] == "owned"
    assert captured["output_root"] == str(tmp_path)


def test_mulerouter_cli_uses_owned_profile_snapshot_without_ambient_token(monkeypatch, tmp_path):
    captured = {}
    profile_home = tmp_path / "owned-profile"
    profile_tree = profile_home / ".mulerun"
    profile_tree.mkdir(parents=True)
    (profile_tree / "opaque-session").write_text("owned-session")
    monkeypatch.setenv("MULERUN_TOKEN", "ambient-token")

    def fake_run(cmd, capture_output, text, timeout, env):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env)
        captured["profile_bytes"] = (Path(env["HOME"]) / ".mulerun" / "opaque-session").read_text()
        return type("Result", (), {"returncode": 0, "stdout": '{"videos":["https://x.invalid/v.mp4"]}', "stderr": ""})()

    monkeypatch.setattr("src.models.mulerouter.subprocess.run", fake_run)

    result = _run_mulerun_studio(
        "bytedance/seedance-2.0/text-to-video",
        ["--prompt", "demo"],
        timeout=1,
        env={
            "LUMENX_MULERUN_HOME": str(profile_home),
            "MULERUN_SITE": "cn",
            "MULEROUTER_SITE": "mulerouter",
        },
    )

    assert result["videos"] == ["https://x.invalid/v.mp4"]
    assert "--token" not in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--base-url") + 1] == "https://api.xiaoluozi.cn"
    assert captured["cmd"][captured["cmd"].index("--site") + 1] == "cn"
    assert "MULERUN_TOKEN" not in captured["env"]
    assert captured["env"]["MULERUN_DISABLE_AUTO_UPGRADE"] == "1"
    assert captured["env"]["MULERUN_SKIP_AUTO_UPGRADE"] == "1"
    assert captured["env"]["HOME"] != str(profile_home)
    assert captured["profile_bytes"] == "owned-session"
    assert (profile_tree / "opaque-session").read_text() == "owned-session"


def test_mulerouter_cli_requires_token_or_owned_profile(monkeypatch):
    monkeypatch.setenv("MULERUN_TOKEN", "ambient-token")

    with pytest.raises(RuntimeError, match="App-owned MuleRun login profile"):
        _run_mulerun_studio(
            "bytedance/seedance-2.0/text-to-video",
            ["--prompt", "demo"],
            timeout=1,
            env={},
        )


def test_mulerouter_cli_backend_uses_owned_profile_status(monkeypatch, tmp_path):
    calls = []

    class FakeSession:
        def __init__(self, profile_home, env):
            calls.append((profile_home, dict(env)))

        def status(self):
            return True

    monkeypatch.setattr("src.models.mulerouter.shutil.which", lambda name: "/bin/mulerun")
    monkeypatch.setattr("src.models.mulerouter.MuleRunLoginSession", FakeSession)

    env = {"LUMENX_MULERUN_HOME": str(tmp_path / "profile")}

    assert _use_cli_backend(env) is True
    assert calls[0][0] == tmp_path / "profile"


def test_mulerouter_cli_backend_rejects_missing_owned_profile_without_ambient(monkeypatch):
    monkeypatch.setenv("MULERUN_TOKEN", "ambient-token")
    monkeypatch.setattr("src.models.mulerouter.shutil.which", lambda name: "/bin/mulerun")

    assert _use_cli_backend({}) is False


def test_mulerouter_image_model_explicit_token_allows_cli_without_browser_profile(monkeypatch):
    monkeypatch.setattr("src.models.mulerouter._is_mulerun_cli_available", lambda env=None: True)

    model = MuleRouterImageModel({}, env={"MULERUN_TOKEN": "owned-token"})

    assert model._use_cli_backend() is True
