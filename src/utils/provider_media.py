import base64
import mimetypes
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from .media_refs import (
    MEDIA_REF_BLOB_URL,
    MEDIA_REF_DATA_URI,
    MEDIA_REF_LOCAL_PATH,
    MEDIA_REF_OBJECT_KEY,
    MEDIA_REF_REMOTE_URL,
    classify_media_ref,
    resolve_local_media_path,
)
from .provider_registry import (
    SUPPORTED_PROVIDER_BACKENDS,
    ProviderRegistry,
    get_default_provider_registry,
)


RESOLVE_HEADER_DASHSCOPE_OSS_RESOURCE = "X-DashScope-OssResourceResolve"
_DASHSCOPE_TEMP_SUB_PATH = "temp/provider_media"


@dataclass(frozen=True)
class ResolvedMediaInput:
    value: str
    headers: Mapping[str, str] = field(default_factory=dict)
    source_ref: Optional[str] = None
    media_ref_type: Optional[str] = None

    def __post_init__(self):
        immutable_headers = MappingProxyType(dict(self.headers or {}))
        object.__setattr__(self, "headers", immutable_headers)


def _normalize_modality(modality: str) -> str:
    value = (modality or "").strip().lower()
    if value in {"image", "audio", "video", "reference_video"}:
        return value
    raise ValueError(f"Unsupported modality '{modality}'")


def _mode_for_modality(family_config, backend: str, modality: str) -> str:
    if modality == "image":
        mode = family_config.image_input_mode.get(backend)
    elif modality == "audio":
        mode = family_config.audio_input_mode.get(backend)
    else:
        mode = family_config.reference_video_input_mode.get(backend)

    if mode:
        return mode
    raise ValueError(
        f"Model family '{family_config.model_family}' does not support backend "
        f"'{backend}' for modality '{modality}'"
    )


def _encode_image_as_data_uri(local_path: str) -> str:
    mime_type, _ = mimetypes.guess_type(local_path)
    if not mime_type:
        mime_type = "image/png"
    with open(local_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _encode_local_file_base64(local_path: str) -> str:
    with open(local_path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _strip_data_uri_prefix(value: str) -> str:
    if ";base64," in value and value.startswith("data:"):
        return value.split(";base64,", 1)[1]
    return value


def _signed_url_from_object_key(ref: str, uploader) -> Optional[str]:
    if not uploader or not getattr(uploader, "is_configured", False):
        return None
    return uploader.sign_url_for_api(ref)


def _upload_then_sign(local_path: str, uploader, sub_path: str = _DASHSCOPE_TEMP_SUB_PATH) -> Optional[str]:
    if not uploader or not getattr(uploader, "is_configured", False):
        return None
    object_key = uploader.upload_file(local_path, sub_path=sub_path)
    if not object_key:
        return None
    return uploader.sign_url_for_api(object_key)


def _resolved(value: str, *, source_ref: str, media_ref_type: str, headers: Optional[Dict[str, str]] = None) -> ResolvedMediaInput:
    return ResolvedMediaInput(
        value=value,
        headers=headers or {},
        source_ref=source_ref,
        media_ref_type=media_ref_type,
    )


def _resolve_dashscope_image(
    ref: str,
    ref_type: str,
    *,
    uploader,
    local_path: Optional[str],
) -> ResolvedMediaInput:
    if ref_type == MEDIA_REF_REMOTE_URL:
        return _resolved(ref, source_ref=ref, media_ref_type=ref_type)
    if ref_type == MEDIA_REF_DATA_URI:
        return _resolved(ref, source_ref=ref, media_ref_type=ref_type)
    if ref_type == MEDIA_REF_OBJECT_KEY:
        signed_url = _signed_url_from_object_key(ref, uploader)
        if signed_url:
            return _resolved(signed_url, source_ref=ref, media_ref_type=ref_type)
        raise ValueError(
            "DashScope image input received an object-storage key but object storage is not configured. "
            "Configure object storage (OSS/TOS) or pass a local/remote image reference."
        )
    if ref_type == MEDIA_REF_LOCAL_PATH:
        if not local_path:
            raise ValueError(f"Unable to resolve local media path for '{ref}'")
        signed_url = _upload_then_sign(local_path, uploader)
        if signed_url:
            return _resolved(signed_url, source_ref=ref, media_ref_type=ref_type)
        return _resolved(_encode_image_as_data_uri(local_path), source_ref=ref, media_ref_type=ref_type)
    if ref_type == MEDIA_REF_BLOB_URL:
        raise ValueError("Blob URLs are ephemeral and unsupported for backend media resolution.")
    raise ValueError(f"Unsupported media reference for DashScope image input: '{ref}'")


def _resolve_dashscope_temp_url(
    ref: str,
    ref_type: str,
    *,
    uploader,
    local_path: Optional[str],
    dashscope_temp_url_resolver: Optional[Callable[[str], str]],
) -> ResolvedMediaInput:
    if ref_type == MEDIA_REF_REMOTE_URL:
        return _resolved(ref, source_ref=ref, media_ref_type=ref_type)
    if ref_type == MEDIA_REF_OBJECT_KEY:
        signed_url = _signed_url_from_object_key(ref, uploader)
        if signed_url:
            return _resolved(signed_url, source_ref=ref, media_ref_type=ref_type)
        raise ValueError(
            "DashScope URL-based media input received an object-storage key but object storage is not configured. "
            "Configure object storage (OSS/TOS) or use a local path that can be resolved via temporary URL."
        )
    if ref_type == MEDIA_REF_LOCAL_PATH:
        if not local_path:
            raise ValueError(f"Unable to resolve local media path for '{ref}'")
        signed_url = _upload_then_sign(local_path, uploader)
        if signed_url:
            return _resolved(signed_url, source_ref=ref, media_ref_type=ref_type)

        if dashscope_temp_url_resolver is None:
            raise ValueError(
                "DashScope URL-based media input requires object storage or a dashscope_temp_url_resolver "
                "for local media. Configure object storage (OSS/TOS) or provide a DashScope temp-url resolver."
            )
        resolver = dashscope_temp_url_resolver
        temp_url = resolver(local_path)
        if not isinstance(temp_url, str) or not temp_url.strip():
            raise ValueError("dashscope_temp_url_resolver returned an empty URL.")
        headers = {}
        if temp_url.startswith("oss://"):
            headers[RESOLVE_HEADER_DASHSCOPE_OSS_RESOURCE] = "enable"
        return _resolved(temp_url, source_ref=ref, media_ref_type=ref_type, headers=headers)
    if ref_type == MEDIA_REF_BLOB_URL:
        raise ValueError("Blob URLs are ephemeral and unsupported for backend media resolution.")
    if ref_type == MEDIA_REF_DATA_URI:
        raise ValueError("Data URI is not supported for DashScope URL-based media input.")
    raise ValueError(f"Unsupported media reference for DashScope URL-based media input: '{ref}'")


def _resolve_vendor_kling_image(
    ref: str,
    ref_type: str,
    *,
    local_path: Optional[str],
) -> ResolvedMediaInput:
    if ref_type == MEDIA_REF_LOCAL_PATH:
        if not local_path:
            raise ValueError(f"Unable to resolve local media path for '{ref}'")
        return _resolved(_encode_local_file_base64(local_path), source_ref=ref, media_ref_type=ref_type)
    if ref_type == MEDIA_REF_DATA_URI:
        return _resolved(_strip_data_uri_prefix(ref), source_ref=ref, media_ref_type=ref_type)
    raise ValueError(
        "Kling vendor image input requires local file or data URI so it can be sent as base64."
    )


def _resolve_vendor_url_mode(
    ref: str,
    ref_type: str,
    *,
    uploader,
    local_path: Optional[str],
    provider_label: str,
    modality: str,
) -> ResolvedMediaInput:
    if ref_type == MEDIA_REF_REMOTE_URL:
        return _resolved(ref, source_ref=ref, media_ref_type=ref_type)
    if ref_type == MEDIA_REF_OBJECT_KEY:
        signed_url = _signed_url_from_object_key(ref, uploader)
        if signed_url:
            return _resolved(signed_url, source_ref=ref, media_ref_type=ref_type)
    if ref_type == MEDIA_REF_LOCAL_PATH and local_path:
        signed_url = _upload_then_sign(local_path, uploader)
        if signed_url:
            return _resolved(signed_url, source_ref=ref, media_ref_type=ref_type)

    raise ValueError(
        f"{provider_label} vendor {modality} input requires a URL-compatible media source. "
        "Configure object storage (OSS/TOS) for local/object-key references, or switch provider mode to dashscope."
    )


def resolve_media_input(
    ref: str,
    *,
    model_name: str,
    modality: str,
    backend: Optional[str] = None,
    uploader=None,
    registry: Optional[ProviderRegistry] = None,
    project_root: Optional[str] = None,
    oss_base_path: Optional[str] = None,
    dashscope_temp_url_resolver: Optional[Callable[[str], str]] = None,
) -> ResolvedMediaInput:
    """
    Resolve a stable project-side media reference to a provider-ready input payload.
    This function is pure with respect to caller state: it does not mutate `ref`.
    """
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("ref must be a non-empty string")

    active_registry = registry or get_default_provider_registry()
    family = active_registry.get_family_config(model_name)
    resolved_backend = (backend or active_registry.resolve_backend(model_name)).strip().lower()
    if resolved_backend not in SUPPORTED_PROVIDER_BACKENDS:
        raise ValueError(f"Unsupported backend '{resolved_backend}'")

    normalized_modality = _normalize_modality(modality)
    mode = _mode_for_modality(family, resolved_backend, normalized_modality)

    ref_type = classify_media_ref(
        ref,
        project_root=project_root,
        oss_base_path=oss_base_path,
    )
    local_path = resolve_local_media_path(ref, project_root=project_root)

    if mode in {"dashscope_multimodal_message", "dashscope_image_to_video"}:
        return _resolve_dashscope_image(
            ref,
            ref_type,
            uploader=uploader,
            local_path=local_path,
        )
    if mode == "dashscope_temp_file_url":
        return _resolve_dashscope_temp_url(
            ref,
            ref_type,
            uploader=uploader,
            local_path=local_path,
            dashscope_temp_url_resolver=dashscope_temp_url_resolver,
        )
    if mode == "kling_vendor_base64_image":
        return _resolve_vendor_kling_image(ref, ref_type, local_path=local_path)
    if (
        mode.startswith("vidu_vendor_")
        or mode.startswith("kling_vendor_")
        or mode.startswith("pixverse_vendor_")
        or mode.startswith("seedance_vendor_")
    ):
        if mode.startswith("vidu_vendor_"):
            provider_label = "Vidu"
        elif mode.startswith("kling_vendor_"):
            provider_label = "Kling"
        elif mode.startswith("seedance_vendor_"):
            provider_label = "Seedance"
        else:
            provider_label = "Pixverse"
        return _resolve_vendor_url_mode(
            ref,
            ref_type,
            uploader=uploader,
            local_path=local_path,
            provider_label=provider_label,
            modality=normalized_modality,
        )

    raise ValueError(
        f"Unsupported provider media input mode '{mode}' for model '{model_name}' "
        f"(backend={resolved_backend}, modality={normalized_modality})."
    )


def resolve_media_inputs(
    refs: Sequence[str],
    *,
    model_name: str,
    modality: str,
    backend: Optional[str] = None,
    uploader=None,
    registry: Optional[ProviderRegistry] = None,
    project_root: Optional[str] = None,
    oss_base_path: Optional[str] = None,
    dashscope_temp_url_resolver: Optional[Callable[[str], str]] = None,
) -> List[ResolvedMediaInput]:
    return [
        resolve_media_input(
            ref,
            model_name=model_name,
            modality=modality,
            backend=backend,
            uploader=uploader,
            registry=registry,
            project_root=project_root,
            oss_base_path=oss_base_path,
            dashscope_temp_url_resolver=dashscope_temp_url_resolver,
        )
        for ref in list(refs)
    ]
