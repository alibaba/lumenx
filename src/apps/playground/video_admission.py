"""Playground video admission helpers for Core provider execution records.

This module freezes product input, provider scope, and credential binding only.
It deliberately does not submit, query, fetch, dispatch, or call paid provider
operations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .models import PlaygroundGeneration, PlaygroundMode
from ...models.provider_binding import ProviderCredentialStore
from ...models.provider_recovery import (
    MAX_PROVIDER_MEDIA_BYTES,
    MAX_PROVIDER_VIDEO_BYTES,
    build_accepted_provider_submission_and_record,
)
from ...models.provider_runtime import ProviderRecoveryRuntime
from ...models.provider_tasks import PreparedMedia, ProviderBindingError
from ...utils.generation_intent import GenerationIntent, IntentValidationError, normalize_generation_intent
from ...utils.media_probe import FrozenMediaMetadata, probe_frozen_image
from ...utils.media_versions import freeze_media, read_verified_media
from ...utils.model_catalog import CatalogAccessor, get_catalog_accessor


_WAN27_VIDEO_MODES = {
    PlaygroundMode.T2V: "t2v",
    PlaygroundMode.I2V: "i2v",
    PlaygroundMode.R2V: "r2v",
    PlaygroundMode.V2V: "v2v",
}
_WAN27_CANONICAL_MODELS = {
    PlaygroundMode.T2V: "wan/wan2.7-video#t2v",
    PlaygroundMode.I2V: "wan/wan2.7-video#i2v",
    PlaygroundMode.R2V: "wan/wan2.7-video#r2v",
    PlaygroundMode.V2V: "wan/wan2.7-video#v2v",
}
_WAN27_LEGACY_VIDEOEDIT_CANONICAL = "wan/wan2.7-video#videoedit"
_WAN27_VIDEOEDIT_CANONICAL = "wan/wan2.7-video#v2v"
_WAN27_ALIAS_TO_CANONICAL = {
    _WAN27_LEGACY_VIDEOEDIT_CANONICAL: _WAN27_VIDEOEDIT_CANONICAL,
}
_WAN27_MIGRATED_MODELS = {
    "wan2.7-t2v", "wan/wan2.7-video#t2v",
    "wan2.7-i2v", "wan/wan2.7-video#i2v",
    "wan2.7-r2v", "wan/wan2.7-video#r2v",
    "wan2.7-videoedit", _WAN27_VIDEOEDIT_CANONICAL, _WAN27_LEGACY_VIDEOEDIT_CANONICAL,
}
_T2V_ALLOWED_PARAMETERS = {
    "duration", "resolution", "ratio", "seed", "negative_prompt", "prompt_extend", "watermark",
}
_I2V_ALLOWED_PARAMETERS = {
    "duration", "resolution", "seed", "negative_prompt", "prompt_extend", "watermark",
}
_R2V_ALLOWED_PARAMETERS = _T2V_ALLOWED_PARAMETERS
_V2V_ALLOWED_PARAMETERS = {
    "duration", "resolution", "ratio", "seed", "negative_prompt", "prompt_extend", "watermark",
    "audio_setting",
}


class VideoRuntimeProvider(Protocol):
    def __call__(self, *, provider_model_id: str, gateway: str) -> ProviderRecoveryRuntime: ...


class PlaygroundVideoAdmission:
    """Freeze Wan 2.7 video admission records for Playground attempts."""

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
        """Attach accepted provider execution records to every batch attempt."""
        if gen.mode not in _WAN27_VIDEO_MODES:
            raise IntentValidationError("Playground Core video admission supports only Wan 2.7 T2V/I2V/R2V/V2V")
        intent = self._intent(gen)
        if gen.mode == PlaygroundMode.V2V:
            _validate_videoedit_intent_parameters(intent.params)
        provider_model_id, gateway = self._provider_identity(intent, gen.mode)
        media = self._media(gen)
        runtime = self._runtime_provider(provider_model_id=provider_model_id, gateway=gateway)
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
        mode = _WAN27_VIDEO_MODES[gen.mode]
        params = self._parameters(gen)
        if gen.negative_prompt is not None:
            params["negative_prompt"] = gen.negative_prompt
        allowed = {
            PlaygroundMode.T2V: _T2V_ALLOWED_PARAMETERS,
            PlaygroundMode.I2V: _I2V_ALLOWED_PARAMETERS,
            PlaygroundMode.R2V: _R2V_ALLOWED_PARAMETERS,
            PlaygroundMode.V2V: _V2V_ALLOWED_PARAMETERS,
        }[gen.mode]
        request_model_id = _WAN27_ALIAS_TO_CANONICAL.get(gen.model_id, gen.model_id)
        return normalize_generation_intent(
            {"mode": mode, "model_id": request_model_id, "prompt": gen.prompt, "params": params},
            allowed_modes={mode},
            allowed_parameters=allowed,
            catalog=self._catalog_accessor(),
        )

    def _parameters(self, gen: PlaygroundGeneration) -> dict:
        params = dict(gen.parameters or {})
        if gen.mode in {PlaygroundMode.T2V, PlaygroundMode.R2V, PlaygroundMode.V2V} and "aspect_ratio" in params:
            aspect_ratio = params.pop("aspect_ratio")
            if "ratio" in params and params["ratio"] != aspect_ratio:
                raise IntentValidationError("Conflicting aliases for parameter: ratio")
            params["ratio"] = aspect_ratio
        return params

    def _provider_identity(self, intent: GenerationIntent, mode: PlaygroundMode) -> tuple[str, str]:
        expected = _WAN27_CANONICAL_MODELS[mode]
        if intent.model_id != expected:
            raise IntentValidationError("Playground Core video admission supports only the current Wan 2.7 catalog mode")
        catalog = self._catalog_accessor()
        provider_model_id = catalog.resolve_canonical_to_legacy(intent.model_id)
        if provider_model_id not in {"wan2.7-t2v", "wan2.7-i2v", "wan2.7-r2v", "wan2.7-videoedit"}:
            raise IntentValidationError("Wan 2.7 provider identity is unavailable")
        gateway = catalog.get_gateway(intent.model_id, "dashscope")
        if not isinstance(gateway, str) or not gateway:
            raise IntentValidationError("Wan 2.7 provider gateway is unavailable")
        return provider_model_id, gateway

    def _media(self, gen: PlaygroundGeneration) -> tuple[PreparedMedia, ...]:
        if gen.mode == PlaygroundMode.T2V:
            if gen.input_media:
                raise IntentValidationError("t2v video generation does not accept input media")
            return ()
        if gen.mode == PlaygroundMode.R2V:
            if not 1 <= len(gen.input_media) <= 5:
                raise IntentValidationError("r2v video generation requires 1 to 5 reference images")
            return tuple(
                self._freeze_image(ref, role="reference_image", ordinal=index)
                for index, ref in enumerate(gen.input_media)
            )
        if gen.mode == PlaygroundMode.V2V:
            if len(gen.input_media) != 1:
                raise IntentValidationError("v2v video generation requires exactly one video")
            return (self._freeze_video(gen.input_media[0], role="reference_video", ordinal=0),)
        if len(gen.input_media) != 1:
            raise IntentValidationError("i2v video generation requires exactly one first frame")
        return (self._freeze_image(gen.input_media[0], role="first_frame", ordinal=0),)

    def _freeze_image(self, ref: str, *, role: str, ordinal: int) -> PreparedMedia:
        version = freeze_media(
            ref,
            output_root=self.output_root,
            writer_lock=self._writer_lock,
            role=role,
            ordinal=ordinal,
            max_bytes=self._max_media_bytes,
        )
        data = read_verified_media(version, output_root=self.output_root, max_bytes=self._max_media_bytes)
        probe_frozen_image(version, data)
        return PreparedMedia.from_verified_bytes(version, data, max_bytes=self._max_media_bytes)

    def _freeze_video(self, ref: str, *, role: str, ordinal: int) -> PreparedMedia:
        version = freeze_media(
            ref,
            output_root=self.output_root,
            writer_lock=self._writer_lock,
            role=role,
            ordinal=ordinal,
            max_bytes=MAX_PROVIDER_VIDEO_BYTES,
        )
        data = read_verified_media(version, output_root=self.output_root, max_bytes=MAX_PROVIDER_VIDEO_BYTES)
        try:
            prepared = PreparedMedia.from_pending_av_bytes(version, data)
        except ProviderBindingError as exc:
            raise IntentValidationError("v2v video input is invalid") from exc
        _validate_videoedit_source_metadata(prepared.frozen_metadata)
        return prepared

    def _catalog_accessor(self) -> CatalogAccessor:
        if self._catalog is None:
            self._catalog = get_catalog_accessor()
        return self._catalog


def admitted_wan27_video_generation(mode: PlaygroundMode, model_id: str) -> bool:
    # This is a routing predicate, not validation. Known models must reach the
    # Core validator even for an unsupported video mode instead of falling back.
    return mode in {PlaygroundMode.T2V, PlaygroundMode.I2V, PlaygroundMode.R2V, PlaygroundMode.V2V} and model_id in _WAN27_MIGRATED_MODELS


def _validate_videoedit_source_metadata(metadata: FrozenMediaMetadata | None) -> None:
    if (
        metadata is None
        or metadata.kind != "video"
        or not 2 <= metadata.duration <= 10
        or metadata.width is None
        or metadata.height is None
        or not 240 <= metadata.width <= 4096
        or not 240 <= metadata.height <= 4096
        or max(metadata.width / metadata.height, metadata.height / metadata.width) > 8
    ):
        raise IntentValidationError("v2v video metadata is outside supported limits")


def _validate_videoedit_intent_parameters(params) -> None:
    duration = params.get("duration")
    if duration is not None and (
        type(duration) is not int or not (duration == 0 or 2 <= duration <= 10)
    ):
        raise IntentValidationError("duration must be 0 or 2 to 10 seconds for v2v")
    seed = params.get("seed")
    if seed is not None and (type(seed) is not int or not 0 <= seed <= 2147483647):
        raise IntentValidationError("seed must be a nonnegative integer for v2v")
