"""Model-bound limits for frozen images that require workspace uploads.

No credentials, catalog mutation or network access. Unknown models retain the
existing recovery ceiling and cannot opt into this upload transport.
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

DEFAULT_RECOVERY_IMAGE_MAX_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class ProviderImagePolicy:
    role: str
    mime_suffixes: Mapping[str, str]
    max_bytes: int


_KLING_MIMES = MappingProxyType({"image/png": "png", "image/jpeg": "jpg"})
_WEB_IMAGE_MIMES = MappingProxyType({**_KLING_MIMES, "image/webp": "webp"})
_POLICIES = MappingProxyType({
    "kling/kling-v3-video-generation": ProviderImagePolicy("first_frame", _KLING_MIMES, 10 * 1024 * 1024),
    "kling/kling-v3-omni-video-generation": ProviderImagePolicy("reference_image", _KLING_MIMES, 10 * 1024 * 1024),
    "vidu/viduq3-pro_img2video": ProviderImagePolicy("first_frame", _WEB_IMAGE_MIMES, 50 * 1024 * 1024),
    "vidu/viduq3-turbo_img2video": ProviderImagePolicy("first_frame", _WEB_IMAGE_MIMES, 50 * 1024 * 1024),
    "vidu/viduq3-mix_reference2video": ProviderImagePolicy("reference_image", _WEB_IMAGE_MIMES, 50 * 1024 * 1024),
    "vidu/viduq3-turbo_reference2video": ProviderImagePolicy("reference_image", _WEB_IMAGE_MIMES, 50 * 1024 * 1024),
    "pixverse/pixverse-v6-it2v": ProviderImagePolicy("first_frame", _WEB_IMAGE_MIMES, 20 * 1024 * 1024),
    "pixverse/pixverse-c1-it2v": ProviderImagePolicy("first_frame", _WEB_IMAGE_MIMES, 20 * 1024 * 1024),
    "pixverse/pixverse-c1-r2v": ProviderImagePolicy("reference_image", _WEB_IMAGE_MIMES, 20 * 1024 * 1024),
    "pixverse/pixverse-v5.6-r2v": ProviderImagePolicy("reference_image", _WEB_IMAGE_MIMES, 20 * 1024 * 1024),
})


def get_provider_image_policy(provider_model_id: str) -> ProviderImagePolicy | None:
    return _POLICIES.get(provider_model_id) if isinstance(provider_model_id, str) else None


def recovery_image_max_bytes(provider_model_id: str, ceiling: int | None = None) -> int:
    """Resolve model limits; an explicit caller ceiling can only restrict them."""
    policy = get_provider_image_policy(provider_model_id)
    limit = policy.max_bytes if policy else DEFAULT_RECOVERY_IMAGE_MAX_BYTES
    if ceiling is None:
        return limit
    if type(ceiling) is not int or ceiling <= 0:
        raise ValueError("Image recovery ceiling must be a positive integer")
    return min(limit, ceiling)
