"""Playground HappyHorse video admission for Core provider records."""

from __future__ import annotations

from pathlib import Path

from .models import PlaygroundGeneration, PlaygroundMode
from .video_admission import VideoRuntimeProvider
from ...models.provider_binding import ProviderCredentialStore
from ...models.provider_recovery import (
    MAX_PROVIDER_MEDIA_BYTES,
    MAX_PROVIDER_VIDEO_BYTES,
    build_accepted_provider_submission_and_record,
)
from ...models.provider_runtime import ProviderRecoveryRuntime
from ...models.provider_tasks import PreparedMedia, ProviderBindingError
from ...utils.generation_intent import GenerationIntent, IntentValidationError, normalize_generation_intent
from ...utils.media_probe import FrozenImageMetadata, MediaProbeError, probe_frozen_image, probe_frozen_media
from ...utils.media_versions import MediaVersionError, freeze_media, read_verified_media
from ...utils.model_catalog import CatalogAccessor, get_catalog_accessor
from ...models.wanx import (
    validate_happyhorse_11_intent,
    validate_happyhorse_videoedit_intent,
    validate_happyhorse_videoedit_video_metadata,
)


_HAPPYHORSE_11_VIDEO_MODES = {
    PlaygroundMode.T2V: "t2v",
    PlaygroundMode.I2V: "i2v",
    PlaygroundMode.R2V: "r2v",
}
_HAPPYHORSE_11_CANONICAL_MODELS = {
    PlaygroundMode.T2V: "happyhorse/happyhorse-1.1-t2v#t2v",
    PlaygroundMode.I2V: "happyhorse/happyhorse-1.1-video#i2v",
    PlaygroundMode.R2V: "happyhorse/happyhorse-1.1-video#r2v",
}
_HAPPYHORSE_11_LEGACY_MODELS = {
    PlaygroundMode.T2V: "happyhorse-1.1-t2v",
    PlaygroundMode.I2V: "happyhorse-1.1-i2v",
    PlaygroundMode.R2V: "happyhorse-1.1-r2v",
}
_HAPPYHORSE_11_OLD_T2V_CANONICAL = "happyhorse/happyhorse-1.1-t2v#i2v"
_HAPPYHORSE_11_ALIAS_TO_CANONICAL = {
    _HAPPYHORSE_11_OLD_T2V_CANONICAL: _HAPPYHORSE_11_CANONICAL_MODELS[PlaygroundMode.T2V],
}
_HAPPYHORSE_11_KNOWN_MODELS = {
    *_HAPPYHORSE_11_LEGACY_MODELS.values(),
    *_HAPPYHORSE_11_CANONICAL_MODELS.values(),
    _HAPPYHORSE_11_OLD_T2V_CANONICAL,
}
_HAPPYHORSE_11_ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}
_HAPPYHORSE_11_ALLOWED_PARAMETERS = {
    PlaygroundMode.T2V: {"duration", "resolution", "ratio", "seed", "watermark"},
    PlaygroundMode.I2V: {"duration", "resolution", "seed", "watermark"},
    PlaygroundMode.R2V: {"duration", "resolution", "ratio", "seed", "watermark"},
}
_HAPPYHORSE_VIDEOEDIT_CANONICAL = "happyhorse/happyhorse-1.0-video-edit#v2v"
_HAPPYHORSE_VIDEOEDIT_OLD_CANONICAL = "happyhorse/happyhorse-1.0-video-edit#i2v"
_HAPPYHORSE_VIDEOEDIT_LEGACY = "happyhorse-1.0-video-edit"
_HAPPYHORSE_VIDEOEDIT_KNOWN_MODELS = {
    _HAPPYHORSE_VIDEOEDIT_LEGACY,
    _HAPPYHORSE_VIDEOEDIT_CANONICAL,
    _HAPPYHORSE_VIDEOEDIT_OLD_CANONICAL,
}
_HAPPYHORSE_VIDEOEDIT_ALIAS_TO_CANONICAL = {
    _HAPPYHORSE_VIDEOEDIT_OLD_CANONICAL: _HAPPYHORSE_VIDEOEDIT_CANONICAL,
}
_HAPPYHORSE_VIDEOEDIT_ALLOWED_PARAMETERS = {"resolution", "seed", "watermark", "audio_setting"}


class HappyHorseVideoAdmission:
    """Freeze HappyHorse 1.1 Playground video admission records without provider execution."""

    def __init__(
        self,
        *,
        output_root: str | Path,
        writer_lock,
        runtime_lock,
        runtime_provider: VideoRuntimeProvider,
        credential_store: ProviderCredentialStore | None = None,
        catalog: CatalogAccessor | None = None,
        max_media_bytes: int = MAX_PROVIDER_MEDIA_BYTES,
    ) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self._writer_lock = writer_lock
        self._runtime_lock = runtime_lock
        self._runtime_provider = runtime_provider
        self._credential_store = credential_store
        self._catalog = catalog
        self._max_media_bytes = max_media_bytes

    def admit(self, gen: PlaygroundGeneration) -> None:
        if gen.model_id in _HAPPYHORSE_VIDEOEDIT_KNOWN_MODELS:
            if gen.mode != PlaygroundMode.V2V:
                raise IntentValidationError("Playground HappyHorse VideoEdit admission supports only V2V")
            intent = self._videoedit_intent(gen)
            provider_model_id, gateway = self._videoedit_provider_identity(intent)
            media = self._videoedit_media(gen)
            unavailable_message = "HappyHorse VideoEdit provider runtime is unavailable"
        else:
            if gen.mode not in _HAPPYHORSE_11_VIDEO_MODES:
                raise IntentValidationError("Playground HappyHorse 1.1 admission supports only T2V/I2V/R2V")
            intent = self._intent(gen)
            provider_model_id, gateway = self._provider_identity(intent, gen.mode)
            media = self._media(gen)
            unavailable_message = "HappyHorse 1.1 provider runtime is unavailable"
        self._attach_records(gen, intent, provider_model_id=provider_model_id, gateway=gateway, media=media, unavailable_message=unavailable_message)

    def _attach_records(
        self,
        gen: PlaygroundGeneration,
        intent: GenerationIntent,
        *,
        provider_model_id: str,
        gateway: str,
        media: tuple[PreparedMedia, ...],
        unavailable_message: str,
    ) -> None:
        runtime = self._runtime_provider(provider_model_id=provider_model_id, gateway=gateway)
        if not isinstance(runtime, ProviderRecoveryRuntime):
            raise IntentValidationError(unavailable_message)
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
        mode = _HAPPYHORSE_11_VIDEO_MODES[gen.mode]
        params = self._parameters(gen)
        if gen.negative_prompt not in (None, ""):
            raise IntentValidationError("HappyHorse 1.1 does not support negative_prompt")
        request_model_id = _HAPPYHORSE_11_ALIAS_TO_CANONICAL.get(gen.model_id, gen.model_id)
        intent = normalize_generation_intent(
            {"mode": mode, "model_id": request_model_id, "prompt": gen.prompt, "params": params},
            allowed_modes={mode},
            allowed_parameters=_HAPPYHORSE_11_ALLOWED_PARAMETERS[gen.mode],
            catalog=self._catalog_accessor(),
        )
        try:
            validate_happyhorse_11_intent(intent, mode=mode)
        except ProviderBindingError as exc:
            raise IntentValidationError("Invalid HappyHorse 1.1 prompt or parameters") from exc
        return intent

    def _videoedit_intent(self, gen: PlaygroundGeneration) -> GenerationIntent:
        if gen.negative_prompt not in (None, ""):
            raise IntentValidationError("HappyHorse VideoEdit does not support negative_prompt")
        request_model_id = _HAPPYHORSE_VIDEOEDIT_ALIAS_TO_CANONICAL.get(gen.model_id, gen.model_id)
        intent = normalize_generation_intent(
            {"mode": "v2v", "model_id": request_model_id, "prompt": gen.prompt, "params": dict(gen.parameters or {})},
            allowed_modes={"v2v"},
            allowed_parameters=_HAPPYHORSE_VIDEOEDIT_ALLOWED_PARAMETERS,
            catalog=self._catalog_accessor(),
        )
        try:
            validate_happyhorse_videoedit_intent(intent)
        except ProviderBindingError as exc:
            raise IntentValidationError("Invalid HappyHorse VideoEdit prompt or parameters") from exc
        return intent

    def _parameters(self, gen: PlaygroundGeneration) -> dict:
        params = dict(gen.parameters or {})
        if gen.mode in {PlaygroundMode.T2V, PlaygroundMode.R2V} and "aspect_ratio" in params:
            aspect_ratio = params.pop("aspect_ratio")
            if "ratio" in params and params["ratio"] != aspect_ratio:
                raise IntentValidationError("Conflicting aliases for parameter: ratio")
            params["ratio"] = aspect_ratio
        return params

    def _provider_identity(self, intent: GenerationIntent, mode: PlaygroundMode) -> tuple[str, str]:
        expected = _HAPPYHORSE_11_CANONICAL_MODELS[mode]
        if intent.model_id != expected:
            raise IntentValidationError("HappyHorse 1.1 provider identity is unavailable")
        catalog = self._catalog_accessor()
        provider_model_id = catalog.resolve_canonical_to_legacy(intent.model_id)
        if provider_model_id != _HAPPYHORSE_11_LEGACY_MODELS[mode]:
            raise IntentValidationError("HappyHorse 1.1 provider identity is unavailable")
        assert provider_model_id is not None
        gateway = catalog.get_gateway(intent.model_id, "dashscope")
        if gateway != "dashscope_workspace":
            raise IntentValidationError("HappyHorse 1.1 requires the DashScope workspace gateway")
        return provider_model_id, gateway

    def _videoedit_provider_identity(self, intent: GenerationIntent) -> tuple[str, str]:
        if intent.model_id != _HAPPYHORSE_VIDEOEDIT_CANONICAL:
            raise IntentValidationError("HappyHorse VideoEdit provider identity is unavailable")
        catalog = self._catalog_accessor()
        provider_model_id = catalog.resolve_canonical_to_legacy(intent.model_id)
        if provider_model_id != _HAPPYHORSE_VIDEOEDIT_LEGACY:
            raise IntentValidationError("HappyHorse VideoEdit provider identity is unavailable")
        gateway = catalog.get_gateway(intent.model_id, "dashscope")
        if gateway != "dashscope_workspace":
            raise IntentValidationError("HappyHorse VideoEdit requires the DashScope workspace gateway")
        return provider_model_id, gateway

    def _media(self, gen: PlaygroundGeneration) -> tuple[PreparedMedia, ...]:
        if gen.mode == PlaygroundMode.T2V:
            if gen.input_media:
                raise IntentValidationError("HappyHorse 1.1 T2V does not accept input media")
            return ()
        if gen.mode == PlaygroundMode.I2V:
            if len(gen.input_media) != 1:
                raise IntentValidationError("HappyHorse 1.1 I2V requires exactly one first frame")
            return (self._freeze_image(gen.input_media[0], role="first_frame", ordinal=0, min_width=300, min_height=300, max_ratio=2.5),)
        if not 1 <= len(gen.input_media) <= 9:
            raise IntentValidationError("HappyHorse 1.1 R2V requires 1 to 9 reference images")
        return tuple(
            self._freeze_image(ref, role="reference_image", ordinal=index, min_short_side=400)
            for index, ref in enumerate(gen.input_media)
        )

    def _videoedit_media(self, gen: PlaygroundGeneration) -> tuple[PreparedMedia, ...]:
        if len(gen.input_media) != 1:
            raise IntentValidationError("HappyHorse VideoEdit requires exactly one source video")
        return (self._freeze_video(gen.input_media[0], role="reference_video", ordinal=0),)

    def _freeze_image(
        self,
        ref: str,
        *,
        role: str,
        ordinal: int,
        min_width: int | None = None,
        min_height: int | None = None,
        min_short_side: int | None = None,
        max_ratio: float | None = None,
    ) -> PreparedMedia:
        try:
            version = freeze_media(
                ref,
                output_root=self.output_root,
                writer_lock=self._writer_lock,
                role=role,
                ordinal=ordinal,
                max_bytes=self._max_media_bytes,
            )
            if version.declared_media_type not in _HAPPYHORSE_11_ALLOWED_IMAGE_MIMES:
                raise IntentValidationError("HappyHorse 1.1 supports only JPEG, PNG, or WEBP images")
            data = read_verified_media(version, output_root=self.output_root, max_bytes=self._max_media_bytes)
            metadata = probe_frozen_image(version, data)
            _validate_image_metadata(
                metadata,
                min_width=min_width,
                min_height=min_height,
                min_short_side=min_short_side,
                max_ratio=max_ratio,
            )
            return PreparedMedia.from_verified_bytes(version, data, max_bytes=self._max_media_bytes)
        except (MediaVersionError, MediaProbeError) as exc:
            raise IntentValidationError("HappyHorse 1.1 image input is invalid") from exc

    def _freeze_video(self, ref: str, *, role: str, ordinal: int) -> PreparedMedia:
        try:
            version = freeze_media(
                ref,
                output_root=self.output_root,
                writer_lock=self._writer_lock,
                role=role,
                ordinal=ordinal,
                max_bytes=MAX_PROVIDER_VIDEO_BYTES,
            )
            data = read_verified_media(version, output_root=self.output_root, max_bytes=MAX_PROVIDER_VIDEO_BYTES)
            metadata = probe_frozen_media(version, data)
            validate_happyhorse_videoedit_video_metadata(metadata)
            return PreparedMedia.from_pending_av_bytes(version, data, probe=lambda *_: metadata)
        except (MediaVersionError, MediaProbeError, ProviderBindingError) as exc:
            raise IntentValidationError("HappyHorse VideoEdit video input is invalid") from exc

    def _catalog_accessor(self) -> CatalogAccessor:
        if self._catalog is None:
            self._catalog = get_catalog_accessor()
        return self._catalog


def admitted_happyhorse_11_video_generation(mode: PlaygroundMode, model_id: str) -> bool:
    # Routing predicate only. Known HappyHorse 1.1 identities enter validation even
    # when paired with the wrong Playground mode so they cannot fall through to legacy.
    return mode in {PlaygroundMode.T2V, PlaygroundMode.I2V, PlaygroundMode.R2V, PlaygroundMode.V2V} and model_id in _HAPPYHORSE_11_KNOWN_MODELS


def admitted_happyhorse_videoedit_generation(mode: PlaygroundMode, model_id: str) -> bool:
    # Routing predicate only. Known VideoEdit identities enter validation even
    # when paired with the wrong Playground mode so they cannot fall through to legacy.
    return mode in {PlaygroundMode.T2V, PlaygroundMode.I2V, PlaygroundMode.R2V, PlaygroundMode.V2V} and model_id in _HAPPYHORSE_VIDEOEDIT_KNOWN_MODELS


def admitted_happyhorse_video_generation(mode: PlaygroundMode, model_id: str) -> bool:
    return admitted_happyhorse_11_video_generation(mode, model_id) or admitted_happyhorse_videoedit_generation(mode, model_id)


def _validate_image_metadata(
    metadata: FrozenImageMetadata,
    *,
    min_width: int | None,
    min_height: int | None,
    min_short_side: int | None,
    max_ratio: float | None,
) -> None:
    if min_width is not None and metadata.width < min_width:
        raise IntentValidationError("HappyHorse 1.1 image width is outside supported limits")
    if min_height is not None and metadata.height < min_height:
        raise IntentValidationError("HappyHorse 1.1 image height is outside supported limits")
    if min_short_side is not None and min(metadata.width, metadata.height) < min_short_side:
        raise IntentValidationError("HappyHorse 1.1 reference image short side is outside supported limits")
    if max_ratio is not None and max(metadata.width / metadata.height, metadata.height / metadata.width) > max_ratio:
        raise IntentValidationError("HappyHorse 1.1 image aspect ratio is outside supported limits")
