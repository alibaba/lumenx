"""Playground Vidu workspace video admission for Core provider records."""

from __future__ import annotations

from pathlib import Path

from .models import PlaygroundGeneration, PlaygroundMode
from .video_admission import VideoRuntimeProvider
from ...models.provider_binding import ProviderCredentialStore
from ...models.provider_image_policy import get_provider_image_policy
from ...models.provider_recovery import build_accepted_provider_submission_and_record
from ...models.provider_runtime import ProviderRecoveryRuntime
from ...models.provider_tasks import PreparedMedia, ProviderBindingError, ProviderScope
from ...models.vidu_core_contract import (
    VIDU_PROVIDER_CONTRACT,
    validate_vidu_image_metadata,
    validate_vidu_intent,
)
from ...utils.generation_intent import GenerationIntent, IntentValidationError, normalize_generation_intent
from ...utils.media_probe import MediaProbeError, probe_frozen_image
from ...utils.media_versions import MediaVersionError, freeze_media, read_verified_media
from ...utils.model_catalog import CatalogAccessor, get_catalog_accessor


_VIDU_VIDEO_MODES = {
    PlaygroundMode.I2V: "i2v",
    PlaygroundMode.R2V: "r2v",
}
_VIDU_CANONICAL_MODELS = {
    PlaygroundMode.I2V: {
        "viduq3-pro-i2v": "vidu/viduq3-pro-video#i2v",
        "viduq3-turbo-i2v": "vidu/viduq3-turbo-video#i2v",
    },
    PlaygroundMode.R2V: {
        "viduq3-pro-r2v": "vidu/viduq3-pro-video#r2v",
        "viduq3-turbo-r2v": "vidu/viduq3-turbo-video#r2v",
    },
}
_VIDU_LEGACY_MODELS = {
    legacy
    for per_mode in _VIDU_CANONICAL_MODELS.values()
    for legacy in per_mode
}
_VIDU_CANONICAL_IDS = {
    canonical
    for per_mode in _VIDU_CANONICAL_MODELS.values()
    for canonical in per_mode.values()
}
_VIDU_KNOWN_MODELS = {*_VIDU_LEGACY_MODELS, *_VIDU_CANONICAL_IDS}
_VIDU_PROVIDER_BY_CANONICAL = {
    canonical: provider_model_id
    for provider_model_id, (canonical, _mode) in VIDU_PROVIDER_CONTRACT.items()
}
_VIDU_ALLOWED_PARAMETERS = {
    PlaygroundMode.I2V: {"duration", "resolution", "audio", "watermark", "seed"},
    PlaygroundMode.R2V: {"duration", "resolution", "audio", "watermark", "seed", "aspect_ratio"},
}


class ViduVideoAdmission:
    """Freeze Vidu Playground video admission records without provider execution."""

    def __init__(
        self,
        *,
        output_root: str | Path,
        writer_lock,
        runtime_lock,
        runtime_provider: VideoRuntimeProvider,
        credential_store: ProviderCredentialStore | None = None,
        catalog: CatalogAccessor | None = None,
    ) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self._writer_lock = writer_lock
        self._runtime_lock = runtime_lock
        self._runtime_provider = runtime_provider
        self._credential_store = credential_store
        self._catalog = catalog

    def admit(self, gen: PlaygroundGeneration) -> None:
        if gen.mode not in _VIDU_VIDEO_MODES:
            raise IntentValidationError("Playground Vidu admission supports only I2V/R2V")
        intent = self._intent(gen)
        provider_model_id, gateway = self._provider_identity(intent, gen.mode)
        media = self._media(gen, provider_model_id=provider_model_id)
        runtime = self._runtime_provider(provider_model_id=provider_model_id, gateway=gateway)
        if not isinstance(runtime, ProviderRecoveryRuntime):
            raise IntentValidationError("Vidu provider runtime is unavailable")
        _validate_vidu_runtime_scope(runtime.scope)
        credential = self._credential_store_instance().bind(runtime.scope, runtime.resolve_secret())
        request_fields = tuple(sorted(intent.to_dict()["request_fields"]))
        for idx in range(gen.batch_size):
            attempt_id = gen.attempts[idx].attempt_id
            _, record = build_accepted_provider_submission_and_record(
                intent,
                provider_model_id=provider_model_id,
                media=media,
                scope=runtime.scope,
                credential_ref=credential.credential_ref,
                attempt_id=attempt_id,
                admission_request_fields=request_fields,
            )
            gen.attempts[idx].provider_execution = record.model_dump(mode="json")

    def _credential_store_instance(self) -> ProviderCredentialStore:
        if self._credential_store is None:
            self._credential_store = ProviderCredentialStore(
                self.output_root,
                writer_lock=self._writer_lock,
                runtime_lock=self._runtime_lock,
            )
        return self._credential_store

    def _intent(self, gen: PlaygroundGeneration) -> GenerationIntent:
        mode = _VIDU_VIDEO_MODES[gen.mode]
        params = self._parameters(gen)
        if gen.negative_prompt not in (None, ""):
            raise IntentValidationError("Vidu does not support negative_prompt")
        intent = normalize_generation_intent(
            {"mode": mode, "model_id": gen.model_id, "prompt": gen.prompt, "params": params},
            allowed_modes={mode},
            allowed_parameters=_VIDU_ALLOWED_PARAMETERS[gen.mode],
            catalog=self._catalog_accessor(),
        )
        try:
            validate_vidu_intent(intent)
        except ProviderBindingError as exc:
            raise IntentValidationError("Invalid Vidu prompt or parameters") from exc
        return intent

    def _parameters(self, gen: PlaygroundGeneration) -> dict:
        params = dict(gen.parameters or {})
        if gen.mode == PlaygroundMode.R2V and "ratio" in params:
            ratio = params.pop("ratio")
            if "aspect_ratio" in params and params["aspect_ratio"] != ratio:
                raise IntentValidationError("Conflicting aliases for parameter: ratio")
            params["aspect_ratio"] = ratio
        return params

    def _provider_identity(self, intent: GenerationIntent, mode: PlaygroundMode) -> tuple[str, str]:
        if intent.model_id not in set(_VIDU_CANONICAL_MODELS[mode].values()):
            raise IntentValidationError("Vidu provider identity is unavailable")
        catalog = self._catalog_accessor()
        provider_model_id = catalog.get_api_model_id(intent.model_id, "dashscope")
        expected_provider_model_id = _VIDU_PROVIDER_BY_CANONICAL.get(intent.model_id)
        if not isinstance(provider_model_id, str) or provider_model_id != expected_provider_model_id:
            raise IntentValidationError("Vidu provider API model is unavailable")
        gateway = catalog.get_gateway(intent.model_id, "dashscope")
        if gateway != "dashscope_workspace":
            raise IntentValidationError("Vidu requires the DashScope workspace gateway")
        return provider_model_id, gateway

    def _media(self, gen: PlaygroundGeneration, *, provider_model_id: str) -> tuple[PreparedMedia, ...]:
        policy = get_provider_image_policy(provider_model_id)
        if policy is None:
            raise IntentValidationError("Vidu image policy is unavailable")
        if gen.mode == PlaygroundMode.I2V:
            if len(gen.input_media) != 1:
                raise IntentValidationError("Vidu I2V requires exactly one first frame")
            return (
                self._freeze_image(
                    gen.input_media[0],
                    role="first_frame",
                    ordinal=0,
                    provider_model_id=provider_model_id,
                ),
            )
        if not 1 <= len(gen.input_media) <= 7:
            raise IntentValidationError("Vidu R2V requires 1 to 7 reference images")
        return tuple(
            self._freeze_image(
                ref,
                role="reference_image",
                ordinal=index,
                provider_model_id=provider_model_id,
            )
            for index, ref in enumerate(gen.input_media)
        )

    def _freeze_image(
        self,
        ref: str,
        *,
        role: str,
        ordinal: int,
        provider_model_id: str,
    ) -> PreparedMedia:
        policy = get_provider_image_policy(provider_model_id)
        if policy is None or policy.role != role:
            raise IntentValidationError("Vidu image policy is unavailable")
        try:
            version = freeze_media(
                ref,
                output_root=self.output_root,
                writer_lock=self._writer_lock,
                role=role,
                ordinal=ordinal,
                max_bytes=policy.max_bytes,
            )
            if version.declared_media_type not in policy.mime_suffixes:
                raise IntentValidationError("Vidu supports only JPEG, PNG, or WEBP images")
            data = read_verified_media(version, output_root=self.output_root, max_bytes=policy.max_bytes)
            metadata = probe_frozen_image(version, data, max_bytes=policy.max_bytes)
            validate_vidu_image_metadata(metadata)
            return PreparedMedia.from_verified_bytes(version, data, max_bytes=policy.max_bytes)
        except (MediaVersionError, MediaProbeError, ProviderBindingError) as exc:
            raise IntentValidationError("Vidu image input is invalid") from exc

    def _catalog_accessor(self) -> CatalogAccessor:
        if self._catalog is None:
            self._catalog = get_catalog_accessor()
        return self._catalog


def admitted_vidu_video_generation(mode: PlaygroundMode, model_id: str) -> bool:
    # Routing predicate only. Known Vidu identities enter validation even when
    # paired with the wrong Playground mode so they cannot fall through to legacy.
    return mode in {PlaygroundMode.T2V, PlaygroundMode.I2V, PlaygroundMode.R2V, PlaygroundMode.V2V} and model_id in _VIDU_KNOWN_MODELS


def _validate_vidu_runtime_scope(scope: ProviderScope) -> None:
    if (
        not isinstance(scope, ProviderScope)
        or scope.gateway != "dashscope_workspace"
        or scope.region != "cn-beijing"
        or scope.account_identity is not None
    ):
        raise IntentValidationError("Vidu requires a Beijing DashScope workspace scope")
    try:
        from ...models.wanx import _dashscope_scope_headers

        _dashscope_scope_headers(scope)
    except ProviderBindingError as exc:
        raise IntentValidationError("Vidu requires a Beijing DashScope workspace scope") from exc
