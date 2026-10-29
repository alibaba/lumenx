"""Frozen runtime authority for future sync image/TTS media admission.

This module binds non-secret runtime identity to the existing private credential
store.  It does not submit, generate, upload, download, mutate app state, or
construct product wire responses.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..audio.tts import PreparedTTS
from ..utils.generation_intent import GenerationIntent
from ..utils.model_catalog import CatalogAccessor, get_catalog_accessor
from ..utils.mulerun_profile import DEFAULT_MULERUN_SITE, MULERUN_SITE_BASE_URLS, mulerun_profile_home
from ..utils.runtime_environment import RuntimeEnvironment
from .provider_binding import (
    MULEROUTER_HTTP_BASE_URL,
    MULEROUTER_HTTP_SOURCE_KEY,
    MULERUN_PROFILE_SOURCE_KEY,
    MULERUN_TOKEN_SOURCE_KEY,
    SOURCE_KEY,
    MuleRunCliRouteIdentity,
    ProviderCredentialBindingError,
    ProviderCredentialStore,
    ResolvedMuleRunProfileCredential,
    ResolvedMuleRunTokenCredential,
)
from .provider_runtime import build_classic_dashscope_scope_from_snapshot
from .provider_tasks import ProviderBindingError, ProviderScope, ResolvedCredential
from .wan27_image_contract import (
    WAN27_IMAGE_CONTRACT_VERSIONS, WAN27_IMAGE_PROVIDER_MODELS,
    validate_wan27_image_intent, wan27_image_scope_from_snapshot,
)
from .qwen20_image_contract import (
    QWEN20_IMAGE_CONTRACT_VERSIONS, QWEN20_IMAGE_PROVIDER_MODELS, validate_qwen20_image_intent,
)
from .dashscope_image_scope import classic_image_scope_from_snapshot, validate_classic_image_scope
from .gpt_image2_edit_contract import (
    GPT_IMAGE2_EDIT_CONTRACT_VERSIONS, GPT_IMAGE2_EDIT_PROVIDER_MODEL,
    validate_gpt_image2_edit_intent, validate_gpt_image2_edit_scope,
)

_UNSET_IMAGE_CONTRACT = object()
_MEDIA_KIND = Literal["image", "audio"]
_DASHSCOPE_WEBSOCKET_KEYS = (
    "DASHSCOPE_TTS_WEBSOCKET_URL",
    "DASHSCOPE_WEBSOCKET_URL",
    "DASHSCOPE_BASE_WEBSOCKET_API_URL",
)
_WORKSPACE_KEYS = ("DASHSCOPE_WORKSPACE", "DASHSCOPE_WORKSPACE_ID")
_ACCOUNT_IDENTITY_KEYS = ("DASHSCOPE_ACCOUNT_IDENTITY", "DASHSCOPE_ACCOUNT_ID")
_DASHSCOPE_IMAGE_PROVIDER_MODELS = frozenset({
    "wan2.7-image",
    "wan2.7-image-pro",
    "qwen-image-2.0",
    "qwen-image-2.0-pro",
})
_MULEROUTER_IMAGE_PROVIDER_MODELS = frozenset({"gpt-image-2"})


class MediaRuntimeAuthorityError(ValueError):
    """The media request cannot be bound to a supported runtime authority."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def mulerun_routes_from_snapshot(snapshot: Mapping[str, str]) -> MuleRunCliRouteIdentity:
    """Build the existing app routes from one snapshot, rejecting alias conflicts."""
    for first, second, site in (
        ("MULERUN_API_BASE_URL", "MULERUN_BASE_URL", False),
        ("MULEROUTER_API_BASE_URL", "MULEROUTER_BASE_URL", False),
        ("MULERUN_STUDIO_SITE", "MULEROUTER_SITE", True),
    ):
        left, right = snapshot.get(first), snapshot.get(second)
        if left and right:
            values = (left.strip().lower(), right.strip().lower()) if site else (left.rstrip("/"), right.rstrip("/"))
            if values[0] != values[1]:
                raise MediaRuntimeAuthorityError("conflicting_mulerun_routes")
    login_site = (snapshot.get("MULERUN_SITE") or DEFAULT_MULERUN_SITE).strip().lower()
    login_base_url = (snapshot.get("MULERUN_API_BASE_URL") or snapshot.get("MULERUN_BASE_URL")
                      or MULERUN_SITE_BASE_URLS.get(login_site, "")).rstrip("/")
    studio_site = (snapshot.get("MULERUN_STUDIO_SITE") or "mulerouter").strip().lower()
    studio_base_url = (snapshot.get("MULEROUTER_API_BASE_URL") or snapshot.get("MULEROUTER_BASE_URL")
                       or MULEROUTER_HTTP_BASE_URL).rstrip("/")
    return MuleRunCliRouteIdentity(schema_version=1, login_site=login_site, login_base_url=login_base_url,
                                   studio_site=studio_site, studio_base_url=studio_base_url)


@dataclass(frozen=True)
class MediaRuntimeAuthority:
    """Private authority returned to the immediate admission/execution caller."""

    media_kind: _MEDIA_KIND
    provider_model_id: str
    scope: ProviderScope
    credential_ref: str
    source_key: str
    credential: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_media_runtime_authority(self)

    def public_identity(self) -> dict[str, object]:
        """Secret-free fields suitable for embedding in provider execution records."""
        return {
            "media_kind": self.media_kind,
            "provider_model_id": self.provider_model_id,
            "scope": self.scope.to_dict(),
            "credential_ref": self.credential_ref,
            "source_key": self.source_key,
        }


def validate_media_runtime_authority(authority: MediaRuntimeAuthority) -> MediaRuntimeAuthority:
    """Validate an in-memory media runtime authority without store or environment I/O."""
    if not isinstance(authority, MediaRuntimeAuthority):
        raise MediaRuntimeAuthorityError("unsupported_runtime_authority")
    if authority.media_kind not in {"image", "audio"}:
        raise MediaRuntimeAuthorityError("unsupported_media_kind")
    _text(authority.provider_model_id, "provider_model_id")
    _text(authority.credential_ref, "credential_ref")
    _text(authority.source_key, "source_key")
    if not isinstance(authority.scope, ProviderScope):
        raise MediaRuntimeAuthorityError("unsupported_provider_scope")

    expected_source_key = _source_key_for_scope(authority.scope)
    if authority.source_key != expected_source_key:
        raise MediaRuntimeAuthorityError("source_key_scope_mismatch")
    if authority.media_kind == "image":
        _validate_image_provider_scope_support(authority.provider_model_id, authority.scope)
    else:
        if authority.source_key != SOURCE_KEY:
            raise MediaRuntimeAuthorityError("unsupported_tts_scope")
        if (
            authority.scope.provider != "dashscope"
            or authority.scope.backend != "dashscope"
            or authority.scope.gateway != "dashscope"
        ):
            raise MediaRuntimeAuthorityError("unsupported_tts_scope")
    _validate_authority_credential(authority)
    return authority


def _validate_authority_credential(authority: MediaRuntimeAuthority) -> None:
    credential = authority.credential
    if authority.source_key in {SOURCE_KEY, MULEROUTER_HTTP_SOURCE_KEY}:
        if not isinstance(credential, ResolvedCredential):
            raise MediaRuntimeAuthorityError("credential_type_mismatch")
        if credential.scope != authority.scope or credential.credential_ref != authority.credential_ref:
            raise MediaRuntimeAuthorityError("credential_identity_mismatch")
        return
    if authority.source_key == MULERUN_TOKEN_SOURCE_KEY:
        if not isinstance(credential, ResolvedMuleRunTokenCredential):
            raise MediaRuntimeAuthorityError("credential_type_mismatch")
        _validate_mulerun_credential_identity(authority, credential.scope, credential.credential_ref, credential.routes)
        return
    if authority.source_key == MULERUN_PROFILE_SOURCE_KEY:
        if not isinstance(credential, ResolvedMuleRunProfileCredential):
            raise MediaRuntimeAuthorityError("credential_type_mismatch")
        _validate_mulerun_credential_identity(authority, credential.scope, credential.credential_ref, credential.routes)
        return
    raise MediaRuntimeAuthorityError("unsupported_credential_source")


def _validate_mulerun_credential_identity(
    authority: MediaRuntimeAuthority,
    scope: ProviderScope,
    credential_ref: str,
    routes: MuleRunCliRouteIdentity,
) -> None:
    if scope != authority.scope or credential_ref != authority.credential_ref:
        raise MediaRuntimeAuthorityError("credential_identity_mismatch")
    if not isinstance(routes, MuleRunCliRouteIdentity):
        raise MediaRuntimeAuthorityError("credential_route_mismatch")
    if authority.scope.provider != "mulerouter" or authority.scope.backend != "mulerun_cli":
        raise MediaRuntimeAuthorityError("credential_route_mismatch")
    if authority.scope.base_url != routes.studio_base_url:
        raise MediaRuntimeAuthorityError("credential_route_mismatch")
    if authority.source_key == MULERUN_TOKEN_SOURCE_KEY and authority.scope.gateway != "mulerun_token":
        raise MediaRuntimeAuthorityError("credential_route_mismatch")
    if authority.source_key == MULERUN_PROFILE_SOURCE_KEY and authority.scope.gateway != "mulerun_profile":
        raise MediaRuntimeAuthorityError("credential_route_mismatch")


@contextmanager
def bind_media_runtime_authority(
    runtime_environment: RuntimeEnvironment,
    credential_store: ProviderCredentialStore,
    *,
    image_intent: GenerationIntent | None = None,
    image_contract_version: object = _UNSET_IMAGE_CONTRACT,
    prepared_tts: PreparedTTS | None = None,
    mulerun_routes: MuleRunCliRouteIdentity | None = None,
    mulerun_profile_home: str | Path | None = None,
) -> Iterator[MediaRuntimeAuthority]:
    """Bind a new accepted media request to one explicit runtime snapshot."""
    _validate_one_input(image_intent=image_intent, prepared_tts=prepared_tts)
    if image_contract_version is not _UNSET_IMAGE_CONTRACT:
        if image_contract_version not in (
            *WAN27_IMAGE_CONTRACT_VERSIONS, *QWEN20_IMAGE_CONTRACT_VERSIONS, *GPT_IMAGE2_EDIT_CONTRACT_VERSIONS,
        ) or image_intent is None:
            raise MediaRuntimeAuthorityError("unsupported_image_contract")
    snapshot = dict(runtime_environment.snapshot())
    if image_intent is not None:
        provider_model_id, scope, source_key = _image_runtime_identity(
            image_intent,
            snapshot,
            mulerun_routes=mulerun_routes,
            mulerun_profile_home=mulerun_profile_home,
            image_contract_version=image_contract_version,
        )
        with _bind_for_scope(
            credential_store,
            snapshot,
            scope,
            source_key,
            mulerun_routes=mulerun_routes,
            mulerun_profile_home=mulerun_profile_home,
        ) as credential:
            yield MediaRuntimeAuthority(
                media_kind="image",
                provider_model_id=provider_model_id,
                scope=scope,
                credential_ref=credential.credential_ref,
                source_key=source_key,
                credential=credential,
            )
        return

    assert prepared_tts is not None
    provider_model_id, scope = _tts_runtime_identity(prepared_tts, snapshot)
    secret = _required_secret(snapshot, SOURCE_KEY)
    credential = credential_store.bind(scope, secret, source_key=SOURCE_KEY)
    yield MediaRuntimeAuthority(
        media_kind="audio",
        provider_model_id=provider_model_id,
        scope=scope,
        credential_ref=credential.credential_ref,
        source_key=SOURCE_KEY,
        credential=credential,
    )

@contextmanager
def resolve_media_runtime_authority(
    runtime_environment: RuntimeEnvironment,
    credential_store: ProviderCredentialStore,
    *,
    media_kind: _MEDIA_KIND,
    provider_model_id: str,
    scope: ProviderScope,
    credential_ref: str,
    image_contract_version: object = _UNSET_IMAGE_CONTRACT,
    prepared_tts: PreparedTTS | None = None,
    mulerun_profile_home: str | Path | None = None,
) -> Iterator[MediaRuntimeAuthority]:
    """Resolve a persisted authority by original scope/ref without reselection."""
    if image_contract_version is not _UNSET_IMAGE_CONTRACT:
        if image_contract_version not in (
            *WAN27_IMAGE_CONTRACT_VERSIONS, *QWEN20_IMAGE_CONTRACT_VERSIONS, *GPT_IMAGE2_EDIT_CONTRACT_VERSIONS,
        ) or media_kind != "image":
            raise MediaRuntimeAuthorityError("unsupported_image_contract")
        if image_contract_version in GPT_IMAGE2_EDIT_CONTRACT_VERSIONS:
            if provider_model_id != GPT_IMAGE2_EDIT_PROVIDER_MODEL:
                raise MediaRuntimeAuthorityError("unsupported_provider_model")
            validate_gpt_image2_edit_scope(scope)
        else:
            models = WAN27_IMAGE_PROVIDER_MODELS if image_contract_version in WAN27_IMAGE_CONTRACT_VERSIONS else QWEN20_IMAGE_PROVIDER_MODELS
            if provider_model_id not in models:
                raise MediaRuntimeAuthorityError("unsupported_provider_model")
            validate_classic_image_scope(scope)
    if media_kind not in {"image", "audio"}:
        raise MediaRuntimeAuthorityError("unsupported_media_kind")
    _text(provider_model_id, "provider_model_id")
    _text(credential_ref, "credential_ref")
    if not isinstance(scope, ProviderScope):
        raise MediaRuntimeAuthorityError("unsupported_provider_scope")
    if media_kind == "audio":
        if not isinstance(prepared_tts, PreparedTTS):
            raise MediaRuntimeAuthorityError("missing_prepared_tts")
        if prepared_tts.model != provider_model_id:
            raise MediaRuntimeAuthorityError("provider_model_mismatch")
        _validate_tts_scope_for_prepared(prepared_tts, scope)
    else:
        _validate_image_provider_scope_support(provider_model_id, scope)

    snapshot = dict(runtime_environment.snapshot())
    source_key = _source_key_for_scope(scope)
    with _resolve_for_scope(
        credential_store,
        snapshot,
        scope,
        credential_ref,
        source_key,
        mulerun_profile_home=mulerun_profile_home,
    ) as credential:
        yield MediaRuntimeAuthority(
            media_kind=media_kind,
            provider_model_id=provider_model_id,
            scope=scope,
            credential_ref=credential_ref,
            source_key=source_key,
            credential=credential,
        )


def _validate_one_input(
    *, image_intent: GenerationIntent | None, prepared_tts: PreparedTTS | None,
) -> None:
    if (image_intent is None) == (prepared_tts is None):
        raise MediaRuntimeAuthorityError(
            "missing_media_runtime_input" if image_intent is None else "conflicting_media_runtime_input"
        )


def _image_runtime_identity(
    intent: GenerationIntent,
    snapshot: Mapping[str, str],
    *,
    mulerun_routes: MuleRunCliRouteIdentity | None,
    mulerun_profile_home: str | Path | None,
    image_contract_version: object = _UNSET_IMAGE_CONTRACT,
) -> tuple[str, ProviderScope, str]:
    if (
        not isinstance(intent, GenerationIntent)
        or intent.catalog_mode != "image"
        or intent.mode not in {"t2i", "i2i"}
    ):
        raise MediaRuntimeAuthorityError("unsupported_image_intent")
    if image_contract_version is not _UNSET_IMAGE_CONTRACT:
        if image_contract_version in GPT_IMAGE2_EDIT_CONTRACT_VERSIONS:
            identity = _mulerouter_image_runtime_identity(
                GPT_IMAGE2_EDIT_PROVIDER_MODEL, snapshot,
                mulerun_routes=mulerun_routes, mulerun_profile_home=mulerun_profile_home,
            )
            validate_gpt_image2_edit_intent(intent, identity[0], identity[1], contract_version=image_contract_version)
            return identity
        if image_contract_version in QWEN20_IMAGE_CONTRACT_VERSIONS:
            provider_model_id = next((model for model in QWEN20_IMAGE_PROVIDER_MODELS if intent.model_id == f"qwen/{model}#image"), "")
            scope = classic_image_scope_from_snapshot(snapshot)
            validate_qwen20_image_intent(intent, provider_model_id, scope, contract_version=image_contract_version)
            return provider_model_id, scope, SOURCE_KEY
        provider_model_id = next((model for model in WAN27_IMAGE_PROVIDER_MODELS if intent.model_id == f"wan/{model}#image"), "")
        scope = wan27_image_scope_from_snapshot(snapshot)
        validate_wan27_image_intent(intent, provider_model_id, scope, contract_version=image_contract_version)
        return provider_model_id, scope, SOURCE_KEY
    catalog = get_catalog_accessor()
    entry = catalog.get_mode_entry(intent.model_id)
    if not _active_image_entry(entry):
        raise MediaRuntimeAuthorityError("unsupported_image_intent")
    runtime = dict(entry.get("runtime", {}))
    if "dashscope" in runtime:
        backend = dict(runtime["dashscope"])
        if backend.get("gateway") != "dashscope":
            raise MediaRuntimeAuthorityError("unsupported_image_runtime")
        provider_model_id = _provider_model_id(entry, backend, catalog)
        scope_result = build_classic_dashscope_scope_from_snapshot(snapshot)
        if scope_result.scope is None:
            raise MediaRuntimeAuthorityError(scope_result.reason or "unsupported_dashscope_scope")
        _validate_image_provider_scope_support(provider_model_id, scope_result.scope)
        return provider_model_id, scope_result.scope, SOURCE_KEY
    if "mulerouter" in runtime:
        backend = dict(runtime["mulerouter"])
        if backend.get("gateway") != "mulerouter":
            raise MediaRuntimeAuthorityError("unsupported_image_runtime")
        provider_model_id = _provider_model_id(entry, backend, catalog)
        return _mulerouter_image_runtime_identity(
            provider_model_id, snapshot,
            mulerun_routes=mulerun_routes, mulerun_profile_home=mulerun_profile_home,
        )
    raise MediaRuntimeAuthorityError("unsupported_image_runtime")


def _mulerouter_image_runtime_identity(
    provider_model_id: str, snapshot: Mapping[str, str], *,
    mulerun_routes: MuleRunCliRouteIdentity | None,
    mulerun_profile_home: str | Path | None,
) -> tuple[str, ProviderScope, str]:
    """Share the existing HTTP-key, token, then profile admission priority."""
    if _has_secret(snapshot, MULEROUTER_HTTP_SOURCE_KEY):
        scope = _mulerouter_http_scope()
        _validate_image_provider_scope_support(provider_model_id, scope)
        return provider_model_id, scope, MULEROUTER_HTTP_SOURCE_KEY
    if _has_secret(snapshot, "MULERUN_TOKEN"):
        routes = _required_routes(mulerun_routes)
        scope = _mulerun_scope(routes, "mulerun_token")
        _validate_image_provider_scope_support(provider_model_id, scope)
        return provider_model_id, scope, MULERUN_TOKEN_SOURCE_KEY
    if mulerun_profile_home is not None or _has_secret(snapshot, "LUMENX_MULERUN_HOME"):
        routes = _required_routes(mulerun_routes)
        scope = _mulerun_scope(routes, "mulerun_profile")
        _validate_image_provider_scope_support(provider_model_id, scope)
        return provider_model_id, scope, MULERUN_PROFILE_SOURCE_KEY
    scope = _mulerouter_http_scope()
    _validate_image_provider_scope_support(provider_model_id, scope)
    return provider_model_id, scope, MULEROUTER_HTTP_SOURCE_KEY


def _tts_runtime_identity(
    prepared: PreparedTTS,
    snapshot: Mapping[str, str],
) -> tuple[str, ProviderScope]:
    if not isinstance(prepared, PreparedTTS):
        raise MediaRuntimeAuthorityError("unsupported_tts_intent")
    if any(_has_secret(snapshot, key) for key in _WORKSPACE_KEYS + _ACCOUNT_IDENTITY_KEYS):
        raise MediaRuntimeAuthorityError("unsupported_tts_scope")
    scope_result = build_classic_dashscope_scope_from_snapshot(snapshot)
    if scope_result.scope is None:
        raise MediaRuntimeAuthorityError(scope_result.reason or "unsupported_dashscope_scope")
    scope = scope_result.scope
    _validate_tts_scope_for_prepared(prepared, scope)
    if prepared.family == "cosyvoice" and any(_has_secret(snapshot, key) for key in _DASHSCOPE_WEBSOCKET_KEYS):
        raise MediaRuntimeAuthorityError("unsupported_tts_endpoint")
    return prepared.model, scope


def _validate_image_provider_scope_support(provider_model_id: str, scope: ProviderScope) -> None:
    _text(provider_model_id, "provider_model_id")
    if scope.provider == "dashscope" and scope.backend == "dashscope" and scope.gateway == "dashscope":
        if provider_model_id in _DASHSCOPE_IMAGE_PROVIDER_MODELS:
            return
        if provider_model_id in _MULEROUTER_IMAGE_PROVIDER_MODELS:
            raise MediaRuntimeAuthorityError("provider_model_scope_mismatch")
        raise MediaRuntimeAuthorityError("unsupported_provider_model")
    if (
        scope.provider == "mulerouter"
        and (
            (scope.backend == "mulerouter" and scope.gateway == "mulerouter_http")
            or (scope.backend == "mulerun_cli" and scope.gateway in {"mulerun_token", "mulerun_profile"})
        )
    ):
        if provider_model_id in _MULEROUTER_IMAGE_PROVIDER_MODELS:
            return
        if provider_model_id in _DASHSCOPE_IMAGE_PROVIDER_MODELS:
            raise MediaRuntimeAuthorityError("provider_model_scope_mismatch")
        raise MediaRuntimeAuthorityError("unsupported_provider_model")
    raise MediaRuntimeAuthorityError("unsupported_provider_scope")


def _validate_tts_scope_for_prepared(prepared: PreparedTTS, scope: ProviderScope) -> None:
    if not isinstance(prepared, PreparedTTS):
        raise MediaRuntimeAuthorityError("unsupported_tts_intent")
    if scope.provider != "dashscope" or scope.backend != "dashscope" or scope.gateway != "dashscope":
        raise MediaRuntimeAuthorityError("unsupported_tts_scope")
    if prepared.family == "cosyvoice":
        if scope.region != "cn-beijing" or scope.base_url != "https://dashscope.aliyuncs.com":
            raise MediaRuntimeAuthorityError("unsupported_tts_endpoint")
    elif prepared.family == "qwen3":
        # Qwen3 TTS uses DashScope HTTP; the scope's official classic base URL is the endpoint identity.
        return
    else:
        raise MediaRuntimeAuthorityError("unsupported_tts_intent")

@contextmanager
def _bind_for_scope(
    credential_store: ProviderCredentialStore,
    snapshot: Mapping[str, str],
    scope: ProviderScope,
    source_key: str,
    *,
    mulerun_routes: MuleRunCliRouteIdentity | None,
    mulerun_profile_home: str | Path | None,
) -> Iterator[object]:
    if source_key == SOURCE_KEY:
        yield credential_store.bind(scope, _required_secret(snapshot, SOURCE_KEY), source_key=SOURCE_KEY)
    elif source_key == MULEROUTER_HTTP_SOURCE_KEY:
        yield credential_store.bind(
            scope, _required_secret(snapshot, MULEROUTER_HTTP_SOURCE_KEY), source_key=MULEROUTER_HTTP_SOURCE_KEY,
        )
    elif source_key == MULERUN_TOKEN_SOURCE_KEY:
        yield credential_store.bind_mulerun_token(
            scope, _required_secret(snapshot, MULERUN_TOKEN_SOURCE_KEY), routes=_required_routes(mulerun_routes),
        )
    elif source_key == MULERUN_PROFILE_SOURCE_KEY:
        profile_home = mulerun_profile_home or _profile_home_from_snapshot(snapshot)
        with credential_store.bind_mulerun_profile(
            scope, profile_home, routes=_required_routes(mulerun_routes),
        ) as credential:
            yield credential
    else:
        raise MediaRuntimeAuthorityError("unsupported_credential_source")

@contextmanager
def _resolve_for_scope(
    credential_store: ProviderCredentialStore,
    snapshot: Mapping[str, str],
    scope: ProviderScope,
    credential_ref: str,
    source_key: str,
    *,
    mulerun_profile_home: str | Path | None,
) -> Iterator[object]:
    if source_key == SOURCE_KEY:
        yield credential_store.resolve_binding(
            scope, credential_ref, _required_secret(snapshot, SOURCE_KEY), source_key=SOURCE_KEY,
        )
    elif source_key == MULEROUTER_HTTP_SOURCE_KEY:
        yield credential_store.resolve_binding(
            scope, credential_ref, _required_secret(snapshot, MULEROUTER_HTTP_SOURCE_KEY),
            source_key=MULEROUTER_HTTP_SOURCE_KEY,
        )
    elif source_key == MULERUN_TOKEN_SOURCE_KEY:
        yield credential_store.resolve_mulerun_token(
            scope, credential_ref, _required_secret(snapshot, MULERUN_TOKEN_SOURCE_KEY),
        )
    elif source_key == MULERUN_PROFILE_SOURCE_KEY:
        profile_home = mulerun_profile_home or _profile_home_from_snapshot(snapshot)
        with credential_store.resolve_mulerun_profile(scope, credential_ref, profile_home) as credential:
            yield credential
    else:
        raise MediaRuntimeAuthorityError("unsupported_credential_source")


def _active_image_entry(entry: Mapping[str, object] | None) -> bool:
    return (
        isinstance(entry, Mapping)
        and entry.get("mode") == "image"
        and entry.get("status") == "active"
        and set(entry.get("capabilities", ())).issubset({"t2i", "i2i"})
        and bool(set(entry.get("capabilities", ())) & {"t2i", "i2i"})
    )


def _provider_model_id(
    entry: Mapping[str, object], backend: Mapping[str, object], catalog: CatalogAccessor,
) -> str:
    value = backend.get("api_model_id") or entry.get("legacy_model_id") or catalog.resolve_canonical_to_legacy(str(entry["id"]))
    return _text(value, "provider_model_id")


def _source_key_for_scope(scope: ProviderScope) -> str:
    if scope.provider == "dashscope" and scope.backend == "dashscope" and scope.gateway == "dashscope":
        return SOURCE_KEY
    if scope.provider == "mulerouter" and scope.backend == "mulerouter" and scope.gateway == "mulerouter_http":
        return MULEROUTER_HTTP_SOURCE_KEY
    if scope.provider == "mulerouter" and scope.backend == "mulerun_cli" and scope.gateway == "mulerun_token":
        return MULERUN_TOKEN_SOURCE_KEY
    if scope.provider == "mulerouter" and scope.backend == "mulerun_cli" and scope.gateway == "mulerun_profile":
        return MULERUN_PROFILE_SOURCE_KEY
    raise MediaRuntimeAuthorityError("unsupported_provider_scope")


def _mulerouter_http_scope() -> ProviderScope:
    return ProviderScope(
        provider="mulerouter",
        backend="mulerouter",
        gateway="mulerouter_http",
        base_url=MULEROUTER_HTTP_BASE_URL,
        region=None,
        workspace_id=None,
        account_identity=None,
    )


def _mulerun_scope(routes: MuleRunCliRouteIdentity, gateway: Literal["mulerun_token", "mulerun_profile"]) -> ProviderScope:
    return ProviderScope(
        provider="mulerouter",
        backend="mulerun_cli",
        gateway=gateway,
        base_url=routes.studio_base_url,
        region=None,
        workspace_id=None,
        account_identity=None,
    )


def _required_routes(routes: MuleRunCliRouteIdentity | None) -> MuleRunCliRouteIdentity:
    if not isinstance(routes, MuleRunCliRouteIdentity):
        raise MediaRuntimeAuthorityError("missing_mulerun_routes")
    return routes


def _profile_home_from_snapshot(snapshot: Mapping[str, str]) -> Path:
    home = mulerun_profile_home(snapshot)
    if home is None:
        raise MediaRuntimeAuthorityError("missing_mulerun_profile")
    return home


def _required_secret(snapshot: Mapping[str, str], source_key: str) -> str:
    secret = snapshot.get(source_key, "")
    if not isinstance(secret, str) or not secret.strip():
        raise ProviderCredentialBindingError("Invalid provider credential")
    return secret


def _has_secret(snapshot: Mapping[str, str], key: str) -> bool:
    value = snapshot.get(key)
    return isinstance(value, str) and bool(value.strip())


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderBindingError(f"Invalid {label}")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ProviderBindingError(f"Invalid {label}") from exc
    return value.strip()
