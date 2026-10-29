from types import SimpleNamespace

import pytest

from src.utils import oss_utils


def environment(label, **extra):
    return {
        "ALIBABA_CLOUD_ACCESS_KEY_ID": f"{label}-example-id",
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET": f"{label}-example-secret",
        "OSS_ENDPOINT": f"https://{label}.example.invalid",
        "OSS_BUCKET_NAME": label,
        "OSS_BASE_PATH": f"{label}-root",
        **extra,
    }


@pytest.fixture
def sdk(monkeypatch):
    created = []

    class Bucket:
        def __init__(self, auth, endpoint, bucket, **kwargs):
            self.auth, self.endpoint, self.name = auth, endpoint, bucket
            self.signs, self.uploads = [], []
            created.append(self)

        def sign_url(self, method, key, expires, **kwargs):
            self.signs.append(key)
            return f"https://{self.name}.example.invalid/{key}?signed=example"

        def put_object(self, key, stream):
            self.uploads.append((key, stream.read()))
            return SimpleNamespace(status=200)

    monkeypatch.setattr(oss_utils.oss2, "Auth", lambda key, secret: (key, secret))
    monkeypatch.setattr(oss_utils.oss2, "Bucket", Bucket)
    monkeypatch.setattr(oss_utils.OSSImageUploader, "_instance", None)
    return created


def test_explicit_uploaders_have_independent_configuration_and_signing_cache(sdk):
    first = oss_utils.OSSImageUploader(env=environment("first"))
    second = oss_utils.OSSImageUploader(env=environment("second"))
    assert first is not second
    assert first.bucket.auth == ("first-example-id", "first-example-secret")
    assert second.endpoint == "https://second.example.invalid"
    assert first.base_path == "first-root" and second.base_path == "second-root"
    assert first.generate_signed_url("shared/file") != second.generate_signed_url("shared/file")
    first.generate_signed_url("shared/file")
    assert first.bucket.signs == second.bucket.signs == ["shared/file"]


def test_empty_mapping_cannot_use_process_oss_configuration(monkeypatch, sdk):
    for key, value in environment("ambient").items():
        monkeypatch.setenv(key, value)
    uploader = oss_utils.OSSImageUploader(env={})
    assert not uploader.is_configured
    assert uploader.base_path == "lumenx"
    assert sdk == []


def test_explicit_input_is_snapshotted_and_legacy_reset_does_not_change_it(sdk):
    config = environment("owned")
    uploader = oss_utils.OSSImageUploader(env=config)
    config.update(environment("changed"))
    oss_utils.OSSImageUploader.reset_instance()
    assert uploader.bucket_name == "owned"
    assert uploader.generate_signed_url("owned-root/file").startswith("https://owned.example.invalid/")


def test_upload_enable_flag_belongs_to_uploader(monkeypatch, sdk, tmp_path):
    monkeypatch.setenv("OSS_ENABLE", "true")
    disabled = oss_utils.OSSImageUploader(env=environment("disabled", OSS_ENABLE="false"))
    enabled = oss_utils.OSSImageUploader(env=environment("enabled", OSS_ENABLE="true"))
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"offline video")
    assert disabled.upload_file(str(media)) is None
    monkeypatch.setenv("OSS_ENABLE", "false")
    assert enabled.upload_file(str(media)) == "enabled-root/clip.mp4"
    assert disabled.bucket.uploads == []
    assert enabled.bucket.uploads == [("enabled-root/clip.mp4", b"offline video")]


@pytest.mark.parametrize("signer", [oss_utils.sign_media_refs_in_response, oss_utils.sign_oss_urls_in_data])
def test_response_signing_uses_owned_namespace(monkeypatch, sdk, signer):
    monkeypatch.setenv("OSS_BASE_PATH", "ambient-root")
    uploader = oss_utils.OSSImageUploader(env=environment("owned"))
    result = signer({"video_url": "owned-root/clip.mp4", "image_url": "ambient-root/image.png"}, uploader=uploader)
    assert result["video_url"].startswith("https://owned.example.invalid/")
    assert result["image_url"] == "ambient-root/image.png"
    assert uploader.bucket.signs == ["owned-root/clip.mp4"]


def test_upload_checks_explicit_output_root_and_rejects_other_directory(monkeypatch, sdk, tmp_path):
    root, other, temp = [tmp_path / name for name in ("owned", "other", "temp")]
    for directory in (root, other, temp):
        directory.mkdir()
    monkeypatch.setattr(oss_utils.tempfile, "gettempdir", lambda: str(temp))
    uploader = oss_utils.OSSImageUploader(env=environment("owned"), output_root=root)
    owned, foreign = root / "clip.mp4", other / "clip.mp4"
    owned.write_bytes(b"owned")
    foreign.write_bytes(b"foreign")
    assert uploader.upload_file(str(owned)) == "owned-root/clip.mp4"
    assert uploader.upload_file(str(foreign)) is None
    assert uploader.bucket.uploads == [("owned-root/clip.mp4", b"owned")]


def test_legacy_no_argument_constructor_keeps_standalone_singleton(monkeypatch, sdk):
    for key, value in environment("standalone").items():
        monkeypatch.setenv(key, value)
    first = oss_utils.OSSImageUploader()
    assert first is oss_utils.OSSImageUploader()
    assert first.bucket_name == "standalone"
