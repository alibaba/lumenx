"""Playground Kling workspace video admission for Core provider records."""

from __future__ import annotations

from pathlib import Path

from .models import PlaygroundGeneration, PlaygroundMode
from .video_admission import VideoRuntimeProvider
from ...models.kling_core_contract import validate_kling_image_metadata, validate_kling_intent
from ...models.provider_binding import ProviderCredentialStore
from ...models.provider_recovery import build_accepted_provider_submission_and_record
from ...models.provider_runtime import ProviderRecoveryRuntime
from ...models.provider_tasks import PreparedMedia, ProviderBindingError
from ...utils.generation_intent import GenerationIntent, IntentValidationError, normalize_generation_intent
from ...utils.media_probe import MediaProbeError, probe_frozen_image
from ...utils.media_versions import MediaVersionError, freeze_media, read_verified_media
from ...utils.model_catalog import CatalogAccessor, get_catalog_accessor


_KLING_VIDEO_MODES = {
    PlaygroundMode.I2V: "i2v",
    PlaygroundMode.R2V: "r2v",
}
_KLING_CANONICAL_MODELS = {
    PlaygroundMode.I2V: "kling/kling-v3-video#i2v",
    PlaygroundMode.R2V: "kling/kling-v3-video#r2v",
}
_KLING_LEGACY_MODELS = {
    PlaygroundMode.I2V: "kling-v3-i2v",
    PlaygroundMode.R2V: "kling-v3-r2v",
}
_KLING_KNOWN_MODELS = {
    *_KLING_LEGACY_MODELS.values(),
    *_KLING_CANONICAL_MODELS.values(),
}
_KLING_ALLOWED_PARAMETERS = {
    PlaygroundMode.I2V: {"mode", "duration", "audio", "watermark", "negative_prompt"},
    PlaygroundMode.R2V: {"mode", "duration", "audio", "watermark", "negative_prompt", "aspect_ratio"},
}
_KLING_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_KLING_ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg"}


class KlingVideoAdmission:
    """Freeze Kling Playground video admission records without provider execution."""

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
        if gen.mode not in _KLING_VIDEO_MODES:
            raise IntentValidationError("Playground Kling admission supports only I2V/R2V")
        intent = self._intent(gen)
        provider_model_id, gateway = self._provider_identity(intent, gen.mode)
        media = self._media(gen)
        runtime = self._runtime_provider(provider_model_id=provider_model_id, gateway=gateway)
        if not isinstance(runtime, ProviderRecoveryRuntime):
            raise IntentValidationError("Kling provider runtime is unavailable")
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
        mode = _KLING_VIDEO_MODES[gen.mode]
        params = self._parameters(gen)
        if gen.negative_prompt not in (None, ""):
            params["negative_prompt"] = gen.negative_prompt
        intent = normalize_generation_intent(
            {"mode": mode, "model_id": gen.model_id, "prompt": gen.prompt, "params": params},
            allowed_modes={mode},
            allowed_parameters=_KLING_ALLOWED_PARAMETERS[gen.mode],
            catalog=self._catalog_accessor(),
        )
        try:
            validate_kling_intent(intent)
        except ProviderBindingError as exc:
            raise IntentValidationError("Invalid Kling prompt or parameters") from exc
        return intent

    def _parameters(self, gen: PlaygroundGeneration) -> dict:
        params = dict(gen.parameters or {})
        if gen.mode == PlaygroundMode.R2V and "aspect_ratio" in params:
            aspect_ratio = params.pop("aspect_ratio")
            if "ratio" in params and params["ratio"] != aspect_ratio:
                raise IntentValidationError("Conflicting aliases for parameter: ratio")
            params["aspect_ratio"] = aspect_ratio
        return params

    def _provider_identity(self, intent: GenerationIntent, mode: PlaygroundMode) -> tuple[str, str]:
        expected = _KLING_CANONICAL_MODELS[mode]
        if intent.model_id != expected:
            raise IntentValidationError("Kling provider identity is unavailable")
        catalog = self._catalog_accessor()
        provider_model_id = catalog.get_api_model_id(intent.model_id, "dashscope")
        if not isinstance(provider_model_id, str) or not provider_model_id:
            raise IntentValidationError("Kling provider API model is unavailable")
        gateway = catalog.get_gateway(intent.model_id, "dashscope")
        if gateway != "dashscope_workspace":
            raise IntentValidationError("Kling requires the DashScope workspace gateway")
        return provider_model_id, gateway

    def _media(self, gen: PlaygroundGeneration) -> tuple[PreparedMedia, ...]:
        if gen.mode == PlaygroundMode.I2V:
            if len(gen.input_media) != 1:
                raise IntentValidationError("Kling I2V requires exactly one first frame")
            return (self._freeze_image(gen.input_media[0], role="first_frame", ordinal=0),)
        if not 1 <= len(gen.input_media) <= 7:
            raise IntentValidationError("Kling R2V requires 1 to 7 reference images")
        return tuple(
            self._freeze_image(ref, role="reference_image", ordinal=index)
            for index, ref in enumerate(gen.input_media)
        )

    def _freeze_image(self, ref: str, *, role: str, ordinal: int) -> PreparedMedia:
        try:
            version = freeze_media(
                ref,
                output_root=self.output_root,
                writer_lock=self._writer_lock,
                role=role,
                ordinal=ordinal,
                max_bytes=_KLING_IMAGE_MAX_BYTES,
            )
            if version.declared_media_type not in _KLING_ALLOWED_IMAGE_MIMES:
                raise IntentValidationError("Kling supports only JPEG or PNG images")
            data = read_verified_media(version, output_root=self.output_root, max_bytes=_KLING_IMAGE_MAX_BYTES)
            metadata = probe_frozen_image(version, data)
            validate_kling_image_metadata(metadata)
            return PreparedMedia.from_verified_bytes(version, data, max_bytes=_KLING_IMAGE_MAX_BYTES)
        except (MediaVersionError, MediaProbeError, ProviderBindingError) as exc:
            raise IntentValidationError("Kling image input is invalid") from exc

    def _catalog_accessor(self) -> CatalogAccessor:
        if self._catalog is None:
            self._catalog = get_catalog_accessor()
        return self._catalog


def admitted_kling_video_generation(mode: PlaygroundMode, model_id: str) -> bool:
    return mode in {PlaygroundMode.T2V, PlaygroundMode.I2V, PlaygroundMode.R2V, PlaygroundMode.V2V} and model_id in _KLING_KNOWN_MODELS
