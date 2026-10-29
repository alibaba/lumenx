"""Bounded Seedance HTTP admission; no CLI execution or provider I/O."""
from __future__ import annotations

from pathlib import Path

from .models import PlaygroundGeneration, PlaygroundMode
from .video_admission import VideoRuntimeProvider
from ...models.provider_binding import MULEROUTER_HTTP_SOURCE_KEY, ProviderCredentialStore
from ...models.provider_recovery import ProviderRecoveryError, build_accepted_provider_submission_and_record
from ...models.provider_runtime import ProviderRecoveryRuntime
from ...models.provider_tasks import PreparedMedia, ProviderBindingError
from ...models.seedance_core_contract import (
    SEEDANCE_PROVIDER_CONTRACT,
    SEEDANCE_IMAGE_MAX_BYTES,
    SEEDANCE_IMAGE_MIMES,
    validate_seedance_intent,
    validate_seedance_scope,
)
from ...utils.generation_intent import GenerationIntent, IntentValidationError, normalize_generation_intent
from ...utils.media_probe import MediaProbeError, probe_frozen_image
from ...utils.media_versions import MediaVersionError, freeze_media, read_verified_media
from ...utils.model_catalog import CatalogAccessor, get_catalog_accessor

SEEDANCE_CAPABILITY_REASONS = frozenset({
    'seedance_http_key_required',
    'seedance_http_endpoint_unsupported',
    'seedance_cli_submission_unsafe',
})
_SEEDANCE_MODES = {PlaygroundMode.T2V: 't2v', PlaygroundMode.I2V: 'i2v', PlaygroundMode.R2V: 'r2v'}
_SEEDANCE_PROVIDER_BY_CANONICAL = {
    canonical: provider_id for provider_id, (canonical, _) in SEEDANCE_PROVIDER_CONTRACT.items()
}
_SEEDANCE_KNOWN_MODELS = frozenset(SEEDANCE_PROVIDER_CONTRACT) | frozenset(_SEEDANCE_PROVIDER_BY_CANONICAL)
_SEEDANCE_PARAMETERS = {'duration', 'resolution', 'aspect_ratio', 'audio', 'seed'}


class SeedanceCapabilityError(RuntimeError):
    """A known channel gate rejected a new request before admission persistence."""

    def __init__(self, reason: str):
        if reason not in SEEDANCE_CAPABILITY_REASONS:
            raise ValueError('Unknown Seedance capability reason')
        self.reason = reason
        super().__init__(reason)


class SeedanceVideoAdmission:
    """Freeze three standard modes under one exact HTTP credential binding."""

    def __init__(
        self, *, output_root: str | Path, writer_lock, runtime_lock,
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
        if gen.mode not in _SEEDANCE_MODES:
            raise IntentValidationError('Seedance admission supports only T2V/I2V/R2V')
        intent = self._intent(gen)
        provider_model_id, gateway = self._provider_identity(intent, gen.mode)
        # A channel gate must run before even frozen-media blobs are persisted.
        try:
            runtime = self._runtime_provider(provider_model_id=provider_model_id, gateway=gateway)
        except ProviderRecoveryError as exc:
            if exc.reason in SEEDANCE_CAPABILITY_REASONS:
                raise SeedanceCapabilityError(exc.reason) from exc
            raise
        if not isinstance(runtime, ProviderRecoveryRuntime):
            raise ProviderRecoveryError('provider_recovery_runtime_missing')
        try:
            validate_seedance_scope(runtime.scope)
        except ProviderBindingError as exc:
            raise IntentValidationError('Seedance requires its exact HTTP scope') from exc
        media = self._media(gen)
        credential = self._credential_store_instance().bind(
            runtime.scope, runtime.resolve_secret(), source_key=MULEROUTER_HTTP_SOURCE_KEY,
        )
        request_fields = tuple(sorted(intent.to_dict()['request_fields']))
        for attempt in gen.attempts:
            _, record = build_accepted_provider_submission_and_record(
                intent, provider_model_id=provider_model_id, media=media,
                scope=runtime.scope, credential_ref=credential.credential_ref,
                attempt_id=attempt.attempt_id, admission_request_fields=request_fields,
            )
            attempt.provider_execution = record.model_dump(mode='json')

    def _intent(self, gen: PlaygroundGeneration) -> GenerationIntent:
        if gen.negative_prompt not in (None, ''):
            raise IntentValidationError('Seedance does not support negative_prompt')
        params = dict(gen.parameters or {})
        if 'ratio' in params:
            ratio = params.pop('ratio')
            if 'aspect_ratio' in params and params['aspect_ratio'] != ratio:
                raise IntentValidationError('Conflicting aliases for parameter: ratio')
            params['aspect_ratio'] = ratio
        allowed = _SEEDANCE_PARAMETERS | ({'watermark'} if gen.mode == PlaygroundMode.T2V else set())
        mode = _SEEDANCE_MODES[gen.mode]
        intent = normalize_generation_intent(
            {'mode': mode, 'model_id': gen.model_id, 'prompt': gen.prompt, 'params': params},
            allowed_modes={mode}, allowed_parameters=allowed, catalog=self._catalog_accessor(),
        )
        try:
            validate_seedance_intent(intent)
        except ProviderBindingError as exc:
            raise IntentValidationError('Invalid Seedance prompt or parameters') from exc
        return intent

    def _provider_identity(self, intent: GenerationIntent, mode: PlaygroundMode) -> tuple[str, str]:
        expected = _SEEDANCE_PROVIDER_BY_CANONICAL.get(intent.model_id)
        if expected is None or SEEDANCE_PROVIDER_CONTRACT[expected][1] != _SEEDANCE_MODES[mode]:
            raise IntentValidationError('Seedance provider identity is unavailable')
        catalog = self._catalog_accessor()
        provider_id = catalog.get_api_model_id(intent.model_id, 'mulerouter')
        gateway = catalog.get_gateway(intent.model_id, 'mulerouter')
        if provider_id != expected or gateway != 'mulerouter':
            raise IntentValidationError('Seedance provider API identity is unavailable')
        return expected, gateway

    def _media(self, gen: PlaygroundGeneration) -> tuple[PreparedMedia, ...]:
        if gen.mode == PlaygroundMode.T2V:
            if gen.input_media:
                raise IntentValidationError('Seedance T2V does not accept input media')
            return ()
        if gen.mode == PlaygroundMode.I2V:
            if len(gen.input_media) != 1:
                raise IntentValidationError('Seedance I2V requires exactly one first frame')
            role = 'first_frame'
        else:
            if not 1 <= len(gen.input_media) <= 9:
                raise IntentValidationError('Seedance R2V requires 1 to 9 reference images')
            role = 'reference_image'
        return tuple(self._freeze_image(ref, role=role, ordinal=index) for index, ref in enumerate(gen.input_media))

    def _freeze_image(self, ref: str, *, role: str, ordinal: int) -> PreparedMedia:
        try:
            version = freeze_media(
                ref, output_root=self.output_root, writer_lock=self._writer_lock,
                role=role, ordinal=ordinal, max_bytes=SEEDANCE_IMAGE_MAX_BYTES,
            )
            if version.declared_media_type not in SEEDANCE_IMAGE_MIMES:
                raise IntentValidationError('Seedance accepts only JPEG, PNG, or WEBP images')
            data = read_verified_media(version, output_root=self.output_root, max_bytes=SEEDANCE_IMAGE_MAX_BYTES)
            probe_frozen_image(version, data, max_bytes=SEEDANCE_IMAGE_MAX_BYTES)
            return PreparedMedia.from_verified_bytes(version, data, max_bytes=SEEDANCE_IMAGE_MAX_BYTES)
        except (MediaVersionError, MediaProbeError, ProviderBindingError) as exc:
            raise IntentValidationError('Seedance image input is invalid') from exc

    def _credential_store_instance(self) -> ProviderCredentialStore:
        if self._credential_store is None:
            self._credential_store = ProviderCredentialStore(
                self.output_root, writer_lock=self._writer_lock, runtime_lock=self._runtime_lock,
            )
        return self._credential_store

    def _catalog_accessor(self) -> CatalogAccessor:
        if self._catalog is None:
            self._catalog = get_catalog_accessor()
        return self._catalog


def admitted_seedance_video_generation(mode: PlaygroundMode, model_id: str) -> bool:
    # Route known identities through validation even with a wrong video mode.
    return mode in {*_SEEDANCE_MODES, PlaygroundMode.V2V} and model_id in _SEEDANCE_KNOWN_MODELS
