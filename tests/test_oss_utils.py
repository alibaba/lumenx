from pathlib import Path
from types import SimpleNamespace

from src.utils import oss_utils


class FakeTosClient:
    def __init__(self, ak, sk, endpoint, region, **kwargs):
        self.ak = ak
        self.sk = sk
        self.endpoint = endpoint
        self.region = region
        self.kwargs = kwargs

    def put_object_from_file(self, bucket, key, file_path):
        self.last_put = (bucket, key, file_path)
        return SimpleNamespace(status_code=200)

    def pre_signed_url(self, method, bucket, key=None, expires=3600, **kwargs):
        self.last_sign = (method, bucket, key, expires, kwargs)
        return SimpleNamespace(signed_url=f"http://signed.example/{bucket}/{key}?expires={expires}")

    def head_object(self, bucket, key):
        self.last_head = (bucket, key)
        return SimpleNamespace()


class FailingDirectUploadTosClient(FakeTosClient):
    def put_object_from_file(self, bucket, key, file_path):
        self.last_put = (bucket, key, file_path)
        raise RuntimeError("simulated direct upload failure")


def _configure_tos_env(monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_PROVIDER", "tos")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET_NAME", "ark-auto-2104181120-cn-beijing-default")
    monkeypatch.setenv("OBJECT_STORAGE_ENDPOINT", "tos-cn-beijing.volces.com")
    monkeypatch.setenv("OBJECT_STORAGE_REGION", "cn-beijing")
    monkeypatch.setenv("OBJECT_STORAGE_BASE_PATH", "seedance-inputs")
    monkeypatch.setenv("TOS_ACCESS_KEY_ID", "ak-test")
    monkeypatch.setenv("TOS_SECRET_ACCESS_KEY", "sk-test")


def test_detects_tos_from_generic_env(monkeypatch):
    _configure_tos_env(monkeypatch)
    assert oss_utils.get_object_storage_provider() == "tos"
    assert oss_utils.get_object_storage_bucket_name() == "ark-auto-2104181120-cn-beijing-default"
    assert oss_utils.get_object_storage_endpoint() == "https://tos-cn-beijing.volces.com"
    assert oss_utils.get_object_storage_region() == "cn-beijing"
    assert oss_utils.get_oss_base_path() == "seedance-inputs"
    assert oss_utils.is_oss_configured() is True


def test_tos_uploader_uploads_and_signs(monkeypatch, tmp_path):
    _configure_tos_env(monkeypatch)
    monkeypatch.setattr(oss_utils, "tos", SimpleNamespace(TosClientV2=FakeTosClient))
    monkeypatch.setattr(
        oss_utils,
        "HttpMethodType",
        SimpleNamespace(Http_Method_Get="GET", Http_Method_Put="PUT"),
    )
    oss_utils.OSSImageUploader.reset_instance()


def test_tos_uploader_falls_back_to_presigned_put(monkeypatch, tmp_path):
    _configure_tos_env(monkeypatch)
    monkeypatch.setattr(oss_utils, "tos", SimpleNamespace(TosClientV2=FailingDirectUploadTosClient))
    monkeypatch.setattr(
        oss_utils,
        "HttpMethodType",
        SimpleNamespace(Http_Method_Get="GET", Http_Method_Put="PUT"),
    )

    calls = {}

    def fake_put(url, data=None, headers=None, timeout=None):
        calls["url"] = url
        calls["payload"] = data
        calls["headers"] = headers
        calls["timeout"] = timeout
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(oss_utils.requests, "put", fake_put)
    oss_utils.OSSImageUploader.reset_instance()

    local_file = tmp_path / "frame.png"
    local_file.write_bytes(b"fallback-test")

    uploader = oss_utils.OSSImageUploader()
    object_key = uploader.upload_file(str(local_file), sub_path="temp/provider_media")

    assert object_key == "seedance-inputs/temp/provider_media/frame.png"
    assert calls["url"].startswith("http://signed.example/")
    assert calls["payload"] == b"fallback-test"
    assert calls["headers"]["Content-Type"] == "image/png"
    assert calls["timeout"] == 30

    oss_utils.OSSImageUploader.reset_instance()

    local_file = tmp_path / "frame.png"
    local_file.write_bytes(b"test")

    uploader = oss_utils.OSSImageUploader()

    object_key = uploader.upload_file(str(local_file), sub_path="temp/provider_media")
    signed_url = uploader.sign_url_for_api(object_key)

    assert uploader.provider == "tos"
    assert uploader.is_configured is True
    assert object_key == "seedance-inputs/temp/provider_media/frame.png"
    assert signed_url == "https://signed.example/ark-auto-2104181120-cn-beijing-default/seedance-inputs/temp/provider_media/frame.png?expires=1800"
    assert uploader.object_exists(object_key) is True

    oss_utils.OSSImageUploader.reset_instance()


def test_legacy_oss_base_path_alias_still_works(monkeypatch):
    monkeypatch.delenv("OBJECT_STORAGE_BASE_PATH", raising=False)
    monkeypatch.setenv("OSS_BASE_PATH", "legacy-base")
    assert oss_utils.get_oss_base_path() == "legacy-base"


def test_extract_object_key_from_signed_url(monkeypatch):
    _configure_tos_env(monkeypatch)

    signed_url = (
        "https://ark-auto-2104181120-cn-beijing-default.tos-cn-beijing.volces.com/"
        "seedance-inputs/assets/style/moodboard-01.png"
        "?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Signature=abc"
    )

    assert (
        oss_utils.extract_object_key_from_url(signed_url)
        == "seedance-inputs/assets/style/moodboard-01.png"
    )
