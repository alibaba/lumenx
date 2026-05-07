import pytest

from src.utils.oss_utils import OSSImageUploader


OBJECT_STORAGE_ENV_KEYS = (
    "OBJECT_STORAGE_PROVIDER",
    "OBJECT_STORAGE_BASE_PATH",
    "OBJECT_STORAGE_BUCKET_NAME",
    "OBJECT_STORAGE_ENDPOINT",
    "OBJECT_STORAGE_REGION",
    "OSS_BASE_PATH",
    "OSS_BUCKET_NAME",
    "OSS_ENDPOINT",
    "ALIBABA_CLOUD_ACCESS_KEY_ID",
    "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    "TOS_BASE_PATH",
    "TOS_BUCKET_NAME",
    "TOS_ENDPOINT",
    "TOS_REGION",
    "TOS_ACCESS_KEY_ID",
    "TOS_SECRET_ACCESS_KEY",
    "VOLCENGINE_ACCESS_KEY_ID",
    "VOLCENGINE_ACCESS_KEY_SECRET",
)


@pytest.fixture(autouse=True)
def isolate_object_storage_env(monkeypatch):
    for key in OBJECT_STORAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    OSSImageUploader.reset_instance()
    yield
    OSSImageUploader.reset_instance()
