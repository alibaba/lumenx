"""Studio-owned image admission on existing aggregate transactions and Core records.

Internal original-worker execution and local proof repair share the retained
request. Public pipeline/API routing remains separate wiring.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import time
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeGuard, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .asset_image_models import CharacterAllRequestState, AssetImageRequestMember, AssetImageRequestRecord, STUDIO_ASSET_IMAGE_ENTRYPOINT, STUDIO_SCENE_PROP_IMAGE_ENTRYPOINT, STUDIO_FULL_BODY_IMAGE_ENTRYPOINT, STUDIO_STORYBOARD_IMAGE_ENTRYPOINT, STUDIO_THREE_VIEW_IMAGE_ENTRYPOINT, STUDIO_HEADSHOT_IMAGE_ENTRYPOINT, STUDIO_GPT_IMAGE_MODELS, STUDIO_WAN_IMAGE_MODELS, STUDIO_QWEN_IMAGE_MODELS, is_studio_gpt_image_execution, studio_image_material_complete
from .assets import ASPECT_RATIO_TO_SIZE, THREE_VIEW_NEGATIVE_SUFFIX, prepare_character_reference_sheet, prepare_character_full_body, prepare_character_three_view, prepare_character_headshot, prepare_scene_image, prepare_prop_image, has_full_body_reference_input, selected_three_view_reference, derived_image_fallback_reference, uploaded_full_body_reference, prepare_full_body_edit_prompt
from .models import AssetUnit, Character, GenerationStatus, ImageAsset, ImageVariant, Prop, Scene, Script, StoryboardFrame
from ...models.provider_media_intents import build_accepted_async_image_record, validate_async_image_record_intent, build_accepted_sync_artifact_record, sync_artifact_proof_from_record, validate_sync_artifact_record_intent
from ...models.provider_media_execution import execute_sync_artifact_to_private_file, submit_async_image, query_async_image, fetch_async_image_to_private_file
from ...models.provider_recovery import record_with_changes, image_query_handle_from_record, require_matching_handle, observation_from_record, observation_to_dict, require_matching_artifact_proof
from ...models.provider_tasks import PreparedMedia, ProviderScope, ProviderArtifact, ProviderImageQueryContract, ProviderTaskStatus, ProviderQueryError
from ...models.wan27_image_http import _POLL_ATTEMPTS, _POLL_INTERVAL_SECONDS
from ...models.mulerouter import POLL_INTERVAL, MAX_WAIT
from ...models.provider_media_runtime import bind_media_runtime_authority, resolve_media_runtime_authority, mulerun_routes_from_snapshot
from ...utils import media_versions
from ...utils.private_media_directory import OwnedPrivateMediaDirectory
from ...models.qwen20_image_contract import (
    QWEN20_IMAGE_CONTRACT_VERSION, QWEN20_IMAGE_EDIT_CONTRACT_VERSION, QWEN20_IMAGE_CONTRACT_VERSIONS,
    QWEN20_IMAGE_PROVIDER_MODELS, QWEN20_IMAGE_REFERENCE_MAX_BYTES,
)
from ...models.wan27_image_contract import (
    WAN27_IMAGE_CONTRACT_VERSION, WAN27_IMAGE_EDIT_CONTRACT_VERSION, WAN27_IMAGE_CONTRACT_VERSIONS,
    WAN27_IMAGE_PROVIDER_MODELS, WAN27_IMAGE_REFERENCE_MAX_BYTES, validate_wan27_image_reference,
)
from ...models.gpt_image2_edit_contract import GPT_IMAGE2_EDIT_CONTRACT_VERSION, GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION, GPT_IMAGE2_EDIT_CONTRACT_VERSIONS
from ...utils.generation_intent import normalize_generation_intent
from ...utils.json_storage import StorageConflictError, StorageError
from ...utils.media_probe import probe_frozen_image
from ...utils.media_refs import classify_media_ref, MEDIA_REF_LOCAL_PATH
from ...utils.request_identity import REQUEST_IDENTITY_CONTRACT_VERSION, fingerprint_request, validate_client_request_id
from ...utils.runtime_environment import RuntimeEnvironment

if TYPE_CHECKING:
    from .pipeline import ComicGenPipeline

logger = logging.getLogger(__name__)

_OWNER_FILES = {'script': 'projects.json', 'series': 'series.json', 'global': 'library_assets.json'}
_IMAGE_FIELDS = ('selected_image_id', 'image_variants', 'image_prompt', 'image_updated_at')
_TARGET_TYPES = {'character': Character, 'scene': Scene, 'prop': Prop, 'storyboard_frame': StoryboardFrame}
_POINTER_FIELDS = {
    ('character', 'reference_sheet'): 'reference_sheet_request_id',
    ('character', 'full_body'): 'full_body_request_id',
    ('character', 'three_view'): 'three_view_request_id',
    ('character', 'headshot'): 'headshot_request_id',
    ('scene', 'image'): 'image_request_id', ('prop', 'image'): 'image_request_id',
    ('storyboard_frame', 'image'): 'image_request_id',
}
_ENTRYPOINTS = {
    ('character', 'reference_sheet'): STUDIO_ASSET_IMAGE_ENTRYPOINT,
    ('character', 'full_body'): STUDIO_FULL_BODY_IMAGE_ENTRYPOINT,
    ('character', 'three_view'): STUDIO_THREE_VIEW_IMAGE_ENTRYPOINT,
    ('character', 'headshot'): STUDIO_HEADSHOT_IMAGE_ENTRYPOINT,
    ('scene', 'image'): STUDIO_SCENE_PROP_IMAGE_ENTRYPOINT,
    ('prop', 'image'): STUDIO_SCENE_PROP_IMAGE_ENTRYPOINT,
    ('storyboard_frame', 'image'): STUDIO_STORYBOARD_IMAGE_ENTRYPOINT,
}


class AssetImageRequestNotFound(ValueError):
    """No retained receipt proves what happened to this request; never retry advice."""


class AssetImageRequestCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    client_request_id: str
    asset_id: str
    asset_type: Literal['character', 'scene', 'prop', 'storyboard_frame'] = 'character'
    generation_type: Literal['reference_sheet', 'full_body', 'three_view', 'headshot', 'all'] = 'reference_sheet'
    style_preset: str | None = 'Cinematic'
    reference_image_url: str | None = None
    reference_variant_id: str | None = None
    reference_source: Literal['full_body_image_url', 'three_view_upload', 'headshot_upload'] | None = None
    style_prompt: str | None = None
    prompt: str | None = None
    apply_style: bool = True
    negative_prompt: str | None = None
    batch_size: int = Field(default=1, ge=1)
    model_name: str | None = None
    aspect_ratio: str | None = None
    seed: int | None = None
    watermark: bool = False
    expected_revision: int | None = Field(default=None, ge=0)
    composition_data: dict | None = None

    @model_validator(mode='after')
    def _channel(self):
        if (self.asset_type, self.generation_type) not in {
            ('character', 'reference_sheet'), ('character', 'full_body'), ('character', 'three_view'),
            ('character', 'headshot'), ('scene', 'all'), ('prop', 'all'),
            ('storyboard_frame', 'all'),
        }:
            raise ValueError('studio_image_channel_not_supported')
        if ('reference_variant_id' in self.model_fields_set
                and (self.asset_type, self.generation_type) not in {('character', 'three_view'), ('character', 'headshot'), ('character', 'full_body')}):
            raise ValueError('studio_image_reference_variant_not_supported')
        if 'reference_source' in self.model_fields_set and (
                (self.asset_type, self.generation_type) not in {('character', 'three_view'), ('character', 'headshot')}
                or self.reference_source is None):
            raise ValueError('studio_image_reference_source_not_supported')
        if self.asset_type == 'storyboard_frame':
            forbidden = {'style_preset', 'reference_image_url', 'style_prompt', 'apply_style',
                         'negative_prompt', 'model_name', 'aspect_ratio', 'seed', 'watermark'}
            if (self.model_fields_set & forbidden or self.expected_revision is None
                    or self.prompt is None or self.batch_size != 1 or self.composition_data):
                raise ValueError('studio_storyboard_image_request_not_supported')
        elif self.model_fields_set & {'expected_revision', 'composition_data'}:
            raise ValueError('studio_image_frame_fields_not_supported')
        return self

    @field_validator('client_request_id')
    @classmethod
    def _key(cls, value):
        return validate_client_request_id(value)

    @field_validator('seed', mode='before')
    @classmethod
    def _seed(cls, value):
        if type(value) is not int or not 0 <= value <= 2147483647:
            raise ValueError('seed must be omitted or an integer from 0 to 2147483647')
        return value


@dataclass(frozen=True)
class AssetImageAdmission:
    request: AssetImageRequestRecord
    replayed: bool
    reason: str | None


def _is_target(target: object, kind: str) -> TypeGuard[Character | Scene | Prop | StoryboardFrame]:
    return isinstance(target, _TARGET_TYPES[kind])


def image_input_guard(
    target: Character | Scene | Prop, channel: str = 'reference_sheet', *, image_contract_version=None,
) -> str:
    if isinstance(target, Character) and channel in {'three_view', 'headshot'}:
        reference = selected_three_view_reference(target)
        fallback = derived_image_fallback_reference(target, channel) if reference is None else None
        # Keep selected-reference fingerprints byte-compatible with retained requests.
        source_fields = ({'reference_source': fallback[0], 'reference_variant_id': fallback[1],
                          'reference_image_url': fallback[2]} if fallback is not None else {
            'reference_variant_id': reference.id if reference else None,
            'reference_image_url': reference.url if reference else None,
        })
        return fingerprint_request({
            'name': target.name, 'description': target.description,
            'base_character_id': target.base_character_id, **source_fields,
        }, entrypoint='studio.asset.three-view-input.v1' if channel == 'three_view' else 'studio.asset.headshot-input.v1')
    if isinstance(target, Character) and channel == 'full_body':
        if image_contract_version == GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION:
            source = uploaded_full_body_reference(target)
            return fingerprint_request({
                'name': target.name, 'description': target.description,
                'base_character_id': target.base_character_id,
                'reference_channel': source[0] if source else None,
                'reference_variant_id': source[1].id if source else None,
                'reference_image_url': source[1].url if source else None,
                'is_uploaded_source': source[1].is_uploaded_source if source else None,
            }, entrypoint='studio.asset.full-body-reference-input.v1')
        return fingerprint_request({
            'name': target.name, 'description': target.description,
            'base_character_id': target.base_character_id,
            'has_reference_input': has_full_body_reference_input(target),
        }, entrypoint='studio.asset.full-body-input.v1')
    return fingerprint_request({'name': target.name, 'description': target.description}, entrypoint='studio.asset.image-input.v1')


def image_projection_guard(target: Character | Scene | Prop | StoryboardFrame, channel: str = 'reference_sheet') -> str:
    if isinstance(target, StoryboardFrame):
        return fingerprint_request(target.model_dump(mode='json', include={
            'image_asset', 'rendered_image_asset', 'image_url', 'rendered_image_url', 'image_prompt',
        }), entrypoint='studio.storyboard.image-projection.v1')
    if not isinstance(target, Character):
        images = None if target.image_asset is None else target.image_asset.model_dump(mode='json')
        return fingerprint_request({'image_asset': images, 'image_url': target.image_url}, entrypoint='studio.asset.scene-prop-projection.v1')
    if channel == 'full_body':
        return fingerprint_request({
            'full_body': None if target.full_body is None else target.full_body.model_dump(mode='json', include=set(_IMAGE_FIELDS)),
            'full_body_asset': None if target.full_body_asset is None else target.full_body_asset.model_dump(mode='json'),
            'full_body_image_url': target.full_body_image_url,
            'full_body_prompt': target.full_body_prompt, 'full_body_updated_at': float(target.full_body_updated_at),
            'is_consistent': target.is_consistent,
        }, entrypoint='studio.asset.full-body-projection.v1')
    if channel == 'three_view':
        return fingerprint_request({
            'three_views': None if target.three_views is None else target.three_views.model_dump(mode='json', include=set(_IMAGE_FIELDS)),
            'three_view_asset': None if target.three_view_asset is None else target.three_view_asset.model_dump(mode='json'),
            'three_view_image_url': target.three_view_image_url,
            'three_view_prompt': target.three_view_prompt, 'three_view_updated_at': float(target.three_view_updated_at),
            'is_consistent': target.is_consistent,
        }, entrypoint='studio.asset.three-view-projection.v1')
    if channel == 'headshot':
        return fingerprint_request({
            'head_shot': None if target.head_shot is None else target.head_shot.model_dump(mode='json', include=set(_IMAGE_FIELDS)),
            'headshot_asset': None if target.headshot_asset is None else target.headshot_asset.model_dump(mode='json'),
            'headshot_image_url': target.headshot_image_url, 'avatar_url': target.avatar_url,
            'headshot_prompt': target.headshot_prompt, 'headshot_updated_at': float(target.headshot_updated_at),
        }, entrypoint='studio.asset.headshot-projection.v1')
    unit = target.reference_sheet
    images = None if unit is None else unit.model_dump(mode='json', include=set(_IMAGE_FIELDS))
    return fingerprint_request({'reference_sheet': images, 'image_url': target.image_url}, entrypoint='studio.asset.image-projection.v1')


def _requires_complete_batch(record: AssetImageRequestRecord) -> bool:
    return (record.entrypoint != STUDIO_ASSET_IMAGE_ENTRYPOINT
            or is_studio_gpt_image_execution(record.members[0].execution))


class StudioAssetImageService:
    """Persist one request at its actual owner without a second task database."""

    def __init__(self, pipeline: ComicGenPipeline, *, executor=None, authority_resolver=None, max_bytes=100 * 1024 * 1024):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError('max_bytes must be a positive integer')
        self.pipeline = pipeline
        self._executor = executor or execute_sync_artifact_to_private_file
        self._authority_resolver = authority_resolver or self._resolve_authority
        self._max_bytes = max_bytes

    def _input_guard(self, target, channel, *, view=None, base_prompt=None, image_contract_version=None):
        if (isinstance(target, Character) and channel == 'full_body' and target.base_character_id
                and image_contract_version == GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION):
            root = view[0] if view else None
            parent, owner = (self.pipeline._saved_asset_image_target(view, target.base_character_id, 'character')
                             if isinstance(root, Script) else (None, None))
            reference = (selected_three_view_reference(parent)
                         if isinstance(root, Script) and isinstance(parent, Character) and owner == ('script', root.id) else None)
            return fingerprint_request({
                'name': target.name, 'description': target.description,
                'base_character_id': target.base_character_id, 'reference_owner': owner,
                'reference_variant_id': reference.id if reference else None,
                'reference_image_url': reference.url if reference else None,
            }, entrypoint='studio.asset.full-body-reference-input.v1')
        if isinstance(target, StoryboardFrame):
            dependency = self.pipeline._storyboard_image_dependency(
                view, target, {'ref_image_paths': [], 'prompt': base_prompt})
            return fingerprint_request({
                'frame': target.model_dump(mode='json', exclude={
                    'image_asset', 'rendered_image_asset', 'image_url', 'rendered_image_url', 'image_prompt',
                    'status', 'updated_at', 'image_request_id',
                }), 'dependency': dependency,
            }, entrypoint='studio.storyboard.image-input.v1')
        return image_input_guard(target, channel, image_contract_version=image_contract_version)

    def read_request(self, root_kind: str, root_id: str, client_request_id: str) -> AssetImageAdmission:
        self._validate_root(root_kind, root_id)
        validate_client_request_id(client_request_id)
        with self.pipeline._save_lock:
            self.pipeline.assert_storage_available()
            record = self._find_request(root_kind, root_id, client_request_id)
            if record is None:
                raise AssetImageRequestNotFound('studio_image_request_not_found')
            return self._ack(record, replayed=True)

    def admit(self, root_kind: str, root_id: str, command: AssetImageRequestCommand) -> AssetImageAdmission:
        return self._admit(root_kind, root_id, command)

    def _admit_prepared_context(self, command: AssetImageRequestCommand, context: dict) -> AssetImageAdmission:
        """Accept one internal frozen bulk context without preparing current inputs."""
        return self._admit(context['root_kind'], context['root_id'], command, prepared_context=context)

    def admit_character_all(self, root_kind, root_id, view, asset_id, params, *, prepared_context=None):
        """Use the existing three channels for the migrated legacy all invocation."""
        root = view[0]
        if (root_kind != 'project' or root is None or params.get('batch_size') != 1
                or (params.get('model_name') or root.model_settings.t2i_model) not in STUDIO_GPT_IMAGE_MODELS
                or root.model_settings.i2i_model not in STUDIO_GPT_IMAGE_MODELS):
            return None
        target, _ = self.pipeline._saved_asset_image_target(view, asset_id, 'character', root_kind=root_kind)
        if not isinstance(target, Character) or has_full_body_reference_input(target):
            return None
        runtime = self.pipeline.runtime_environment
        if runtime is None or not runtime.snapshot().get('MULEROUTER_API_KEY', '').strip():
            return None
        context = (prepared_context if prepared_context is not None else
                   self.pipeline._prepare_asset_image(view, asset_id, 'character', params, root_kind=root_kind))
        command = AssetImageRequestCommand.model_validate({
            **params, 'client_request_id': str(uuid4()), 'asset_id': asset_id,
            'asset_type': 'character', 'generation_type': 'full_body',
        })
        full_body_context = None
        if prepared_context is not None:
            # Keep the batch's original target, owner, options and scope; only
            # the first compound stage changes from all to full_body.
            full_body_context = {
                **context,
                'params': {**context['params'], 'generation_type': 'full_body'},
                'options': {**context['options'], 'generation_type': 'full_body'},
            }
        return self._admit(root_kind, root_id, command, prepared_context=full_body_context,
                           character_all_context=context)

    def run_character_all_original(self, request_id):
        """Only the first live worker may advance the fixed paid stages."""
        pipeline = self.pipeline
        with pipeline._save_lock:
            task = pipeline.asset_generation_tasks.get(request_id)
            if (not task or task.get('status') != 'pending'
                    or task.get('_character_all_started') or not task.get('_character_all_context')):
                return
            task['_character_all_started'] = True
            context = task['_character_all_context']
        try:
            current_id = request_id
            for next_channel in ('three_view', 'headshot', None):
                admission = self.run_original_request(current_id)
                with pipeline._save_lock:
                    if admission is None or self.request_state(admission.request)['status'] != 'completed':
                        return
                    target = cast(Character, pipeline._check_asset_image(context))
                    parent = self._find_request(request_id=request_id)
                    if parent is None:
                        raise AssetImageRequestNotFound('studio_image_request_not_found')
                    parent = self._retained_request(parent)
                    if parent.character_all is None:
                        raise AssetImageRequestNotFound('studio_image_request_not_found')
                    if next_channel is None:
                        target.is_consistent = True
                        target.status = GenerationStatus.COMPLETED
                        parent.character_all.completed = True
                        self._refresh_character_all_guards(parent, target)
                        pipeline._commit_storage(_OWNER_FILES[parent.owner_kind])
                        context['expected'] = target.model_dump()
                        return
                    reference = selected_three_view_reference(target, reject_ambiguous=True)
                    if reference is None:
                        raise StorageConflictError('studio_character_all_reference_missing')
                    params = {key: value for key, value in context['params'].items()
                              if key not in {'model_name', 'aspect_ratio'}}
                    command = AssetImageRequestCommand.model_validate({
                        **params, 'client_request_id': str(uuid4()), 'asset_id': parent.asset_id,
                        'asset_type': 'character', 'generation_type': next_channel, 'prompt': None,
                        'model_name': context['options']['i2i_model_name'],
                        'reference_variant_id': reference.id, 'reference_image_url': reference.url,
                    })
                    child = self._admit(parent.request_root_kind, parent.request_root_id, command,
                                        character_all_context=context)
                    current_id = child.request.id
        except StorageConflictError:
            if pipeline._storage_failed is not None:
                raise
            logger.warning('Studio character all inputs changed (%s)', request_id)
        except StorageError:
            raise
        except ValueError:
            logger.warning('Studio character all stopped before its next stage (%s)', request_id)
        finally:
            with pipeline._save_lock:
                parent = self._find_request(request_id=request_id)
                if parent is not None:
                    parent = self._retained_request(parent)
                if parent is not None and parent.character_all is not None and not parent.character_all.completed:
                    try:
                        target = cast(Character, pipeline._check_asset_image(context))
                    except StorageConflictError:
                        pass
                    else:
                        pipeline._reconcile_asset_image_result(context['target'], target, 'character', 'all', failed=True)
                        target.is_consistent = context['target'].is_consistent
                        target.status = 'needs_confirmation'
                        self._refresh_character_all_guards(parent, target, after_rollback=True)
                        pipeline._commit_storage(_OWNER_FILES[parent.owner_kind])
                        context['expected'] = target.model_dump()

    def _character_all_records(self, parent):
        state = parent.character_all
        if state is None:
            return [parent]
        records = [parent]
        for channel, request_id in (('three_view', state.three_view_request_id), ('headshot', state.headshot_request_id)):
            if request_id is None:
                continue
            child = self._find_request(request_id=request_id)
            if (child is None or child.channel != channel or child.asset_type != 'character'
                    or (child.owner_kind, child.owner_id, child.request_root_kind, child.request_root_id, child.asset_id)
                    != (parent.owner_kind, parent.owner_id, parent.request_root_kind, parent.request_root_id, parent.asset_id)):
                raise StorageConflictError('studio_character_all_stage_changed')
            records.append(self._retained_request(child))
        return records

    def _refresh_character_all_guards(self, parent, target, *, after_rollback=False):
        # Only after this worker's own guarded selection/consistency writes.
        for record in self._character_all_records(parent):
            if after_rollback and record.channel in {'three_view', 'headshot'}:
                # This is the owner publication precondition, not the frozen
                # provider input. Recovery still uses the original execution/media
                # and only appends a candidate; later author edits must match again.
                record.input_guard = self._input_guard(target, record.channel)
            record.projection_guard = image_projection_guard(target, record.channel)

    def _incomplete_character_all(self, record):
        owner = self._image_owner(record)
        return bool(owner and any(
            parent.character_all is not None and not parent.character_all.completed
            and record.id in {parent.id, parent.character_all.three_view_request_id, parent.character_all.headshot_request_id}
            for parent in owner.asset_image_requests
        ))

    def _admit(self, root_kind, root_id, command, *, prepared_context=None, character_all_context=None) -> AssetImageAdmission:
        self._validate_root(root_kind, root_id)
        # Revalidate mutable caller objects; retain raw field presence for replay.
        command = AssetImageRequestCommand.model_validate(command.model_dump(exclude_unset=True))
        if (prepared_context is not None and command.asset_type not in {'scene', 'prop', 'storyboard_frame'}
                and not (character_all_context is not None and command.asset_type == 'character'
                         and command.generation_type == 'full_body')):
            raise ValueError('studio_image_prepared_channel_not_supported')
        raw = command.model_dump(exclude={'client_request_id'}, exclude_unset=True)
        channel = command.generation_type if command.asset_type == 'character' else 'image'
        entrypoint = _ENTRYPOINTS[(command.asset_type, channel)]
        fingerprint = fingerprint_request(
            {'request_root_kind': root_kind, 'request_root_id': root_id, 'body': raw},
            entrypoint=entrypoint,
        )
        pipeline = self.pipeline
        with pipeline._save_lock:
            pipeline.assert_storage_available()
            previous = self._find_request(root_kind, root_id, command.client_request_id)
            if previous is not None:
                if previous.request_fingerprint != fingerprint:
                    raise StorageConflictError('studio_image_request_payload_conflict')
                return self._ack(previous, replayed=True)
            if prepared_context is None:
                context, intent, image_contract_version = self._prepare(root_kind, root_id, command)
            else:
                context = prepared_context
                self._validate_prepared_command(command, context)
                if command.asset_type != 'storyboard_frame':
                    pipeline._check_asset_image(context)
                intent, image_contract_version = self._intent_from_context(context, command)
            root, target, owner_object, live_owner = self._acceptance_target(context)
            if context['key'] in getattr(pipeline, '_asset_image_runs', {}):
                raise StorageConflictError('studio_image_target_busy')
            if command.asset_type == 'storyboard_frame' and (root_id, command.asset_id) in getattr(pipeline, '_storyboard_image_runs', {}):
                raise StorageConflictError('studio_image_target_busy')
            if pipeline.runtime_environment is None:
                raise ValueError('studio_image_runtime_environment_required')
            contract_options = ({'image_contract_version': image_contract_version}
                                if image_contract_version is not None else {})
            runtime = pipeline.runtime_environment
            binding_options = {}
            if image_contract_version in (None, *GPT_IMAGE2_EDIT_CONTRACT_VERSIONS):
                snapshot = dict(runtime.snapshot())
                routes = mulerun_routes_from_snapshot(snapshot)
                if (routes.studio_site != 'mulerouter'
                        or (snapshot.get('MULEROUTER_SITE') or 'mulerouter').strip().lower() != 'mulerouter'
                        or (snapshot.get('MULEROUTER_API_KEY', '').strip()
                            and routes.studio_base_url != 'https://api.mulerouter.ai')):
                    raise ValueError('studio_gpt_image_runtime_not_supported')
                runtime = RuntimeEnvironment(snapshot)
                binding_options = {'mulerun_routes': routes, 'mulerun_profile_home': snapshot.get('LUMENX_MULERUN_HOME') or None}
            media = []
            if image_contract_version in (*GPT_IMAGE2_EDIT_CONTRACT_VERSIONS, WAN27_IMAGE_EDIT_CONTRACT_VERSION, QWEN20_IMAGE_EDIT_CONTRACT_VERSION):
                reference_image_url = context['reference_image_url']
                if not isinstance(reference_image_url, str):
                    raise ValueError(f'studio_{channel}_selected_local_reference_required')
                max_bytes = min(self._max_bytes, {
                    WAN27_IMAGE_EDIT_CONTRACT_VERSION: WAN27_IMAGE_REFERENCE_MAX_BYTES,
                    QWEN20_IMAGE_EDIT_CONTRACT_VERSION: QWEN20_IMAGE_REFERENCE_MAX_BYTES,
                }.get(image_contract_version, 50 * 1024 * 1024))
                version = media_versions.freeze_media(
                    reference_image_url, output_root=pipeline.data_dir,
                    writer_lock=pipeline._writer_lock, role='reference_image', ordinal=0, max_bytes=max_bytes,
                )
                data = media_versions.read_verified_media(version, output_root=pipeline.data_dir, max_bytes=max_bytes)
                metadata = probe_frozen_image(version, data, max_bytes=max_bytes)
                if image_contract_version == WAN27_IMAGE_EDIT_CONTRACT_VERSION:
                    validate_wan27_image_reference(metadata, len(data))
                media.append(PreparedMedia.from_verified_bytes(version, data, max_bytes=max_bytes))
            with bind_media_runtime_authority(
                runtime, pipeline._provider_credential_store,
                image_intent=intent, **contract_options, **binding_options,
            ) as authority:
                members = []
                for _ in range(command.batch_size):
                    attempt_id = str(uuid4())
                    async_image = (image_contract_version in WAN27_IMAGE_CONTRACT_VERSIONS or (
                        authority.provider_model_id == 'gpt-image-2'
                        and (authority.scope.backend, authority.scope.gateway) == ('mulerouter', 'mulerouter_http')))
                    if async_image:
                        execution = build_accepted_async_image_record(
                            intent=intent, media=media,
                            provider_model_id=authority.provider_model_id, scope=authority.scope,
                            credential_ref=authority.credential_ref, attempt_id=attempt_id,
                            output_ref=f'assets/studio/{attempt_id}.png', **contract_options,
                        )
                    else:
                        execution = build_accepted_sync_artifact_record(
                            intent=intent, media_kind='image', media=media,
                            provider_model_id=authority.provider_model_id, scope=authority.scope,
                            credential_ref=authority.credential_ref, attempt_id=attempt_id,
                            output_ref=f'assets/studio/{attempt_id}.png', **contract_options,
                        )
                    members.append(AssetImageRequestMember(variant_id=attempt_id, execution=execution))
            if prepared_context is not None and command.asset_type != 'storyboard_frame':
                pipeline._check_asset_image(context)
            root, target, owner_object, live_owner = self._acceptance_target(context)
            scope_id = root.asset_image_request_scope_id or str(uuid4())
            record = AssetImageRequestRecord.model_validate(dict(
                schema_version=1, id=str(uuid4()), created_at=time.time(),
                request_contract_version=REQUEST_IDENTITY_CONTRACT_VERSION,
                entrypoint=entrypoint,
                client_request_id=command.client_request_id, request_fingerprint=fingerprint,
                request_root_kind=root_kind, request_root_id=root_id, request_root_scope_id=scope_id,
                request_root_series_id=root.series_id if isinstance(root, Script) else None,
                owner_kind=live_owner[0], owner_id=live_owner[1], asset_type=command.asset_type,
                asset_id=command.asset_id, channel=channel, base_prompt=context.get('base_prompt'),
                input_guard=self._input_guard(target, channel, view=context.get('view'), base_prompt=context.get('base_prompt'),
                                              image_contract_version=image_contract_version),
                projection_guard=image_projection_guard(target, channel), members=members,
            ))
            if character_all_context is not None:
                parent_id = character_all_context.get('character_all_request_id')
                if parent_id is None:
                    record.character_all = CharacterAllRequestState.model_validate({})
                    character_all_context['character_all_request_id'] = record.id
                else:
                    parent = self._find_request(request_id=parent_id)
                    if parent is None:
                        raise AssetImageRequestNotFound('studio_image_request_not_found')
                    parent = self._retained_request(parent)
                    if parent.character_all is None:
                        raise AssetImageRequestNotFound('studio_image_request_not_found')
                    setattr(parent.character_all, f'{channel}_request_id', record.id)
            root.asset_image_request_scope_id = scope_id
            setattr(target, _POINTER_FIELDS[(record.asset_type, record.channel)], record.id)
            target.status = GenerationStatus.PROCESSING
            owner_object.asset_image_requests.append(record)
            files = {_OWNER_FILES[live_owner[0]], 'projects.json' if root_kind == 'project' else 'series.json'}
            pipeline._commit_storage(*sorted(files))
            # Only this process's first successful admission creates a paid claim.
            token = object()
            if not hasattr(pipeline, '_asset_image_runs'):
                pipeline._asset_image_runs = {}
            pipeline._asset_image_runs[context['key']] = token
            pipeline.asset_generation_tasks[record.id] = {
                'status': 'pending', 'progress': 0, 'error': None,
                'script_id': root_id, 'root_kind': root_kind, 'asset_id': command.asset_id,
                'asset_type': record.asset_type, 'created_at': record.created_at, 'is_series': root_kind == 'series',
                'client_request_id': command.client_request_id, '_core_image_request_id': record.id,
                '_token': token, '_owner_key': context['key'],
            }
            if record.members[0].execution.schema_version == 3:
                pipeline.asset_generation_tasks[record.id].update(
                    _image_owner_identity=owner_object, _image_request_identity=record,
                    _image_member_identities=tuple(record.members),
                )
            if isinstance(target, StoryboardFrame):
                pipeline.asset_generation_tasks[record.id]['_frame_context'] = {
                    'root_identity': root, 'target_identity': target, 'expected': target.model_dump(),
                }
            if image_contract_version in (*GPT_IMAGE2_EDIT_CONTRACT_VERSIONS, QWEN20_IMAGE_EDIT_CONTRACT_VERSION):
                # Only the original in-process claim owns these live objects.
                # Restart repair still uses the persisted receipt/proof guards.
                pipeline.asset_generation_tasks[record.id]['_character_edit_context'] = {
                    'root_identity': root, 'owner_identity': owner_object, 'target_identity': target,
                }
            if character_all_context is not None:
                pipeline.asset_generation_tasks[record.id]['_character_all_context'] = character_all_context
                character_all_context['expected'] = target.model_dump()
            return self._ack(record, replayed=False)

    def abandon_pending_original_claim(self, request_id: str) -> None:
        """Release a never-dispatched claim without changing retained history."""
        with self.pipeline._save_lock:
            task = self.pipeline.asset_generation_tasks.get(request_id)
            if (not task or task.get('_core_image_request_id') != request_id
                    or task.get('status') != 'pending'):
                return
            key, token = task.get('_owner_key'), task.get('_token')
            runs = getattr(self.pipeline, '_asset_image_runs', {})
            if not isinstance(key, tuple) or token is None or runs.get(key) is not token:
                return
            del runs[key]
            del self.pipeline.asset_generation_tasks[request_id]

    @staticmethod
    def _owner_key(record):
        return record.owner_kind, record.owner_id, record.asset_type, record.asset_id

    def _active_task(self, record):
        task = self.pipeline.asset_generation_tasks.get(record.id)
        if (task and task.get('_core_image_request_id') == record.id
                and task.get('status') in {'pending', 'processing'}
                and task.get('root_kind') == record.request_root_kind
                and task.get('script_id') == record.request_root_id
                and task.get('client_request_id') == record.client_request_id
                and task.get('asset_type') == record.asset_type
                and getattr(self.pipeline, '_asset_image_runs', {}).get(self._owner_key(record)) is task.get('_token')):
            return task
        return None

    def _retained_request(self, original):
        self.pipeline.assert_storage_available()
        saved = self._find_request(original.request_root_kind, original.request_root_id, original.client_request_id)
        if saved is None:
            raise AssetImageRequestNotFound('studio_image_request_not_found')
        if _requires_complete_batch(original):
            expected = [(m.variant_id, m.execution.fingerprint) for m in original.members]
            retained = [(m.variant_id, m.execution.fingerprint) for m in saved.members]
            if retained != expected:
                raise StorageConflictError('studio_image_request_members_changed')
        excluded = {'members', 'projection_guard'}
        if saved.model_dump(exclude=excluded) != original.model_dump(exclude=excluded):
            raise StorageConflictError('studio_image_request_identity_changed')
        owner = (self.pipeline.scripts.get(saved.owner_id) if saved.owner_kind == 'script' else
                 self.pipeline.series_store.get(saved.owner_id) if saved.owner_kind == 'series' else self.pipeline.library_store)
        current = next((r for r in owner.asset_image_requests if r.id == saved.id), None) if owner else None
        if current is None or current.model_dump() != saved.model_dump():
            raise StorageConflictError('studio_image_request_history_changed')
        return current

    def _retained_member(self, original, execution):
        current = self._retained_request(original)
        member = next((m for m in current.members if m.execution.attempt_id == execution.attempt_id), None)
        if member is None or member.execution.fingerprint != execution.fingerprint:
            raise StorageConflictError('studio_image_member_changed')
        return current, member

    def _projection_target(self, record):
        reason = self.association_reason(record)
        root, target, owner_object, owner = self.pipeline._live_asset_image_target(
            record.request_root_kind, record.request_root_id, record.asset_id, record.asset_type)
        if (reason or root is None or not _is_target(target, record.asset_type)
                or owner != (record.owner_kind, record.owner_id)
                or root.asset_image_request_scope_id != record.request_root_scope_id
                or (isinstance(root, Script) and root.series_id != record.request_root_series_id)
                or getattr(target, _POINTER_FIELDS[(record.asset_type, record.channel)]) != record.id
                or self._input_guard(target, record.channel, base_prompt=record.base_prompt,
                                     image_contract_version=record.members[0].execution.intent.get('intent_contract_version'),
                                     view=(root, self.pipeline.series_store.get(root.series_id or ''), self.pipeline.library_store)
                                     if isinstance(root, Script) else None) != record.input_guard
                or image_projection_guard(target, record.channel) != record.projection_guard):
            raise StorageConflictError(reason or 'studio_image_request_target_changed')
        if isinstance(target, StoryboardFrame):
            task = self.pipeline.asset_generation_tasks.get(record.id)
            context = task.get('_frame_context') if task else None
            if context and (root is not context['root_identity'] or target is not context['target_identity']):
                raise StorageConflictError('studio_image_request_target_changed')
        if record.members[0].execution.intent.get('intent_contract_version') in (*GPT_IMAGE2_EDIT_CONTRACT_VERSIONS, QWEN20_IMAGE_EDIT_CONTRACT_VERSION):
            task = self.pipeline.asset_generation_tasks.get(record.id)
            context = task.get('_character_edit_context') if task else None
            if context and (root is not context['root_identity'] or owner_object is not context['owner_identity']
                            or target is not context['target_identity']):
                raise StorageConflictError('studio_image_request_target_changed')
        return target

    def _require_original(self, record, task):
        if self._active_task(record) is not task or task['status'] != 'processing':
            raise StorageConflictError('studio_image_original_worker_missing')
        target = self._projection_target(record)
        if task.get('_character_all_context') is not None:
            self.pipeline._check_asset_image(task['_character_all_context'])
        if record.members[0].execution.schema_version == 3:
            current = self._retained_request(record)
            if (self._image_owner(current) is not task.get('_image_owner_identity')
                    or current is not task.get('_image_request_identity')
                or tuple(id(m) for m in current.members) != tuple(id(m) for m in task.get('_image_member_identities', ()))):
                raise StorageConflictError('studio_image_original_owner_changed')
        if isinstance(target, StoryboardFrame):
            context = task.get('_frame_context')
            if (not context or target.model_dump() != context['expected']
                    or (record.request_root_id, record.asset_id) in getattr(self.pipeline, '_storyboard_image_runs', {})):
                raise StorageConflictError('studio_image_request_target_changed')
        if (record.members[0].execution.intent.get('intent_contract_version') in (*GPT_IMAGE2_EDIT_CONTRACT_VERSIONS, QWEN20_IMAGE_EDIT_CONTRACT_VERSION)
                and not task.get('_character_edit_context')):
            raise StorageConflictError('studio_image_request_target_changed')
        return target

    def _resolve_authority(self, execution):
        if execution.schema_version == 3:
            if execution.handle is not None:
                return self._resolve_query_authority(execution)
            validate_async_image_record_intent(execution)
        image_contract_version = execution.intent.get('intent_contract_version')
        gpt = is_studio_gpt_image_execution(execution)
        if not gpt and image_contract_version not in (*WAN27_IMAGE_CONTRACT_VERSIONS, *QWEN20_IMAGE_CONTRACT_VERSIONS, *GPT_IMAGE2_EDIT_CONTRACT_VERSIONS):
            raise ValueError('studio_image_contract_not_supported')
        if execution.schema_version != 3:
            validate_sync_artifact_record_intent(execution)
        if self.pipeline.runtime_environment is None:
            raise ValueError('studio_image_runtime_environment_required')
        runtime = self.pipeline.runtime_environment
        mulerun_profile_home = None
        if gpt or image_contract_version in GPT_IMAGE2_EDIT_CONTRACT_VERSIONS:
            snapshot = dict(runtime.snapshot())
            runtime = RuntimeEnvironment(snapshot)
            mulerun_profile_home = snapshot.get('LUMENX_MULERUN_HOME') or None
        if gpt:
            return resolve_media_runtime_authority(
                runtime, self.pipeline._provider_credential_store,
                media_kind='image', provider_model_id=execution.provider_model_id,
                scope=ProviderScope.from_dict(execution.scope), credential_ref=execution.credential_ref,
                mulerun_profile_home=mulerun_profile_home,
            )
        return resolve_media_runtime_authority(
            runtime, self.pipeline._provider_credential_store,
            media_kind='image', provider_model_id=execution.provider_model_id,
            scope=ProviderScope.from_dict(execution.scope), credential_ref=execution.credential_ref,
            image_contract_version=image_contract_version,
            mulerun_profile_home=mulerun_profile_home,
        )

    def _file_state(self, execution):
        proof = execution.prepared_artifact
        if proof is None:
            return 'unproved'
        if execution.schema_version == 3:
            image_query_handle_from_record(execution)
        else:
            validate_sync_artifact_record_intent(execution)
        try:
            if not media_versions.managed_media_file_exists(
                    execution.output_ref, output_root=self.pipeline.data_dir, writer_lock=self.pipeline._writer_lock,
                    storage_guard=self.pipeline._provider_media_storage_guard, media_kind='image'):
                return 'missing'
            media_versions.verify_managed_media_file(
                execution.output_ref, output_root=self.pipeline.data_dir, writer_lock=self.pipeline._writer_lock,
                storage_guard=self.pipeline._provider_media_storage_guard, media_kind='image', expected_sha256=proof['sha256'],
                expected_size=proof['size_bytes'], max_bytes=self._max_bytes)
        except (media_versions.MediaVersionError, OSError):
            return 'invalid'
        return 'ready'

    def request_state(self, record):
        """Derive current member state; never turn history into execution rights."""
        with self.pipeline._save_lock:
            current = self._retained_request(record)
            task = self._active_task(current)
            members = []
            for member in current.members:
                execution = member.execution
                ready = self._file_state(execution)
                reason = None
                if execution.phase == 'cancelled':
                    status = 'cancelled'
                elif execution.phase == 'completed' and ready == 'ready':
                    status = 'completed'
                elif execution.phase == 'accepted' and task:
                    status = 'pending'
                elif (execution.phase in {'submitting', 'submitted', 'observed'}
                      and task and task.get('status') == 'processing'
                      and task.get('_active_attempt_id') == execution.attempt_id
                      and (execution.observation is None or observation_from_record(execution).status not in {
                          ProviderTaskStatus.FAILED, ProviderTaskStatus.CANCELED})):
                    status = 'processing'
                else:
                    status = 'needs_confirmation'
                    reason = (self.association_reason(current) if ready == 'ready' else None) or (
                        'studio_image_original_worker_missing' if execution.phase == 'accepted' else
                        'provider_sync_execution_unknown' if ready == 'unproved' else
                        f'provider_sync_artifact_{ready}')
                if execution.schema_version == 3 and status == 'needs_confirmation':
                    if execution.handle is None:
                        reason = 'provider_image_submission_unknown' if execution.phase == 'submitting' else reason
                    elif ready == 'ready':
                        reason = self.association_reason(current) or 'studio_image_projection_pending'
                    elif execution.observation is not None:
                        observed = observation_from_record(execution)
                        reason = ('provider_image_remote_failed' if observed.status is ProviderTaskStatus.FAILED else
                                  'provider_image_remote_canceled' if observed.status is ProviderTaskStatus.CANCELED else
                                  'provider_image_result_pending' if observed.status is ProviderTaskStatus.SUCCEEDED else
                                  'provider_image_query_pending')
                    else:
                        reason = 'provider_image_query_pending'
                item = dict(variant_id=member.variant_id, status=status, reason=reason)
                if ready == 'ready':
                    item['output_ref'] = execution.output_ref
                members.append(item)
            statuses = [m['status'] for m in members]
            complete = (all(s == 'completed' for s in statuses) if _requires_complete_batch(current)
                        else 'completed' in statuses)
            status = ('processing' if task and task['status'] == 'processing' else 'pending' if task else
                      'needs_confirmation' if 'needs_confirmation' in statuses else
                      'completed' if complete else 'cancelled')
            return dict(status=status, progress=100 * statuses.count('completed') // len(statuses), members=members)

    def read_task_status(self, request_id: str) -> dict | None:
        """Read legacy task metadata from receipts, including after process restart."""
        with self.pipeline._save_lock:
            self.pipeline.assert_storage_available()
            record = self._find_request(request_id=request_id)
            if record is None:
                return None
            state = self.request_state(record)
            reason = self.association_reason(record)
            if record.character_all is not None:
                stages = [self.request_state(stage) for stage in self._character_all_records(record)]
                done = sum(stage['status'] == 'completed' for stage in stages)
                active = any(stage['status'] in {'pending', 'processing'} for stage in stages)
                state = dict(
                    status='completed' if record.character_all.completed and done == 3 else
                           'processing' if active else 'needs_confirmation',
                    progress=100 * done // 3,
                    members=[member for stage in stages for member in stage['members']],
                )
                if state['status'] == 'needs_confirmation' and reason is None:
                    reason = 'studio_character_all_incomplete'
            return dict(
                task_id=record.id, **state, error=None, reason=reason,
                asset_id=record.asset_id, asset_type=record.asset_type,
                script_id=record.request_root_id, created_at=record.created_at,
                is_series=record.request_root_kind == 'series',
            )

    def run_original_request(self, request_id: str) -> AssetImageAdmission | None:
        """Consume only the claim created by this process after first admission."""
        pipeline = self.pipeline
        with pipeline._save_lock:
            pipeline.assert_storage_available()
            task = pipeline.asset_generation_tasks.get(request_id)
            if not task or task.get('_core_image_request_id') != request_id or task.get('status') != 'pending':
                return None
            key, token = task['_owner_key'], task['_token']
            if getattr(pipeline, '_asset_image_runs', {}).get(key) is not token:
                return None
            task['status'] = 'processing'
        original = None
        stopped = False
        try:
            with pipeline._save_lock:
                original = self._find_request(task['root_kind'], task['script_id'], task['client_request_id'])
                if original is None or original.id != request_id:
                    return None
            for initial in original.members:
                with pipeline._save_lock:
                    current, member = self._retained_member(original, initial.execution)
                    if member.execution.phase != 'accepted':
                        continue
                    self._require_original(current, task)
                    task['_active_attempt_id'] = member.execution.attempt_id
                    snapshot = current.model_copy(deep=True)
                    execution = member.execution.model_copy(deep=True)
                if not self._run_member(snapshot, execution, task):
                    stopped = True
                    break
                with pipeline._save_lock:
                    task.pop('_active_attempt_id', None)
        except AssetImageRequestNotFound:
            pass
        except StorageConflictError:
            if pipeline._storage_failed is not None:
                raise
            stopped = True
        finally:
            with pipeline._save_lock:
                try:
                    if stopped and original is not None and _requires_complete_batch(original):
                        self._cancel_unstarted(original, task)
                finally:
                    if getattr(pipeline, '_asset_image_runs', {}).get(key) is token:
                        del pipeline._asset_image_runs[key]
                    if pipeline.asset_generation_tasks.get(request_id) is task:
                        task['status'] = 'needs_confirmation'
                        task.pop('_active_attempt_id', None)
        if original is None:
            return None
        try:
            with pipeline._save_lock:
                current = self._retained_request(original)
                if any(m.execution.schema_version == 3 for m in current.members) and (
                        self._image_owner(current) is not task.get('_image_owner_identity')
                        or current is not task.get('_image_request_identity')
                        or tuple(id(m) for m in current.members) != tuple(id(m) for m in task.get('_image_member_identities', ()))):
                    return None
                self._finish_status(current)
                state = self.request_state(current)
                if pipeline.asset_generation_tasks.get(request_id) is task:
                    task.update(status=state['status'], progress=state['progress'])
                return self._ack(current, replayed=False)
        except AssetImageRequestNotFound:
            return None

    def _cancel_unstarted(self, original, task):
        try:
            current = self._retained_request(original)
        except AssetImageRequestNotFound:
            return
        except StorageConflictError:
            if self.pipeline._storage_failed is not None:
                raise
            return
        if self._active_task(current) is not task:
            return
        if any(m.execution.schema_version == 3 for m in current.members) and (
                self._image_owner(current) is not task.get('_image_owner_identity')
                or current is not task.get('_image_request_identity')
                or tuple(id(m) for m in current.members) != tuple(id(m) for m in task.get('_image_member_identities', ()))):
            return
        changed = False
        for member in current.members:
            if member.execution.phase == 'accepted':
                member.execution = record_with_changes(member.execution, phase='cancelled')
                changed = True
        if changed:
            self.pipeline._commit_storage(_OWNER_FILES[current.owner_kind])

    def _run_member(self, original, execution, task):
        started = False
        try:
            with self._authority_resolver(execution) as authority:
                with self.pipeline._save_lock:
                    current, member = self._retained_member(original, execution)
                    self._require_original(current, task)
                    if member.execution.phase != 'accepted':
                        raise StorageConflictError('studio_image_member_already_started')
                    if media_versions.managed_media_file_exists(
                            execution.output_ref, output_root=self.pipeline.data_dir,
                            writer_lock=self.pipeline._writer_lock,
                            storage_guard=self.pipeline._provider_media_storage_guard, media_kind='image'):
                        raise StorageConflictError('provider_sync_artifact_unrecorded')
                    member.execution = record_with_changes(member.execution, phase='submitting')
                    self.pipeline._commit_storage(_OWNER_FILES[current.owner_kind])
                    execution = member.execution.model_copy(deep=True)
                    started = True
                if execution.schema_version == 3:
                    self._execute_async_member(original, execution, authority, task)
                else:
                    self._execute_member(original, execution, authority, task['_token'])
                if _requires_complete_batch(original):
                    with self.pipeline._save_lock:
                        _, member = self._retained_member(original, execution)
                        return member.execution.phase == 'completed'
            return True
        except StorageError:
            raise
        except AssetImageRequestNotFound:
            return False
        except Exception:
            logger.warning('Studio image member stopped (%s, submitted=%s)', execution.attempt_id, started)
            return False if _requires_complete_batch(original) else started

    def _image_owner(self, record):
        return (self.pipeline.scripts.get(record.owner_id) if record.owner_kind == 'script' else
                self.pipeline.series_store.get(record.owner_id) if record.owner_kind == 'series' else self.pipeline.library_store)

    def _async_context(self, original, execution, token, *, task=None):
        with self.pipeline._save_lock:
            current, member = self._retained_member(original, execution)
            context = dict(original=original, initial=execution, owner=self._image_owner(current),
                           request=current, member=member, token=token, task=task,
                           expected=member.execution.model_dump(mode='json'))
            self._async_owned(context)
            return context

    def _async_owned(self, context):
        """Caller holds _save_lock; author edits do not revoke original receipts."""
        self.pipeline.assert_storage_available()
        self.pipeline._writer_lock.assert_owned()
        current, member = self._retained_member(context['original'], context['initial'])
        task = context['task']
        if (self._image_owner(current) is not context['owner'] or current is not context['request']
                or member is not context['member']
                or member.execution.model_dump(mode='json') != context['expected']
                or getattr(self.pipeline, '_asset_image_runs', {}).get(self._owner_key(current)) is not context['token']):
            raise StorageConflictError('studio_image_async_owner_changed')
        if task is not None and (
                self.pipeline.asset_generation_tasks.get(current.id) is not task
                or task.get('_token') is not context['token'] or task.get('status') != 'processing'
                or task.get('_image_owner_identity') is not context['owner']
                or task.get('_image_request_identity') is not current
                or tuple(id(m) for m in current.members) != tuple(id(m) for m in task.get('_image_member_identities', ()))):
            raise StorageConflictError('studio_image_async_owner_changed')
        return current, member

    def _save_async_execution(self, context, **changes):
        current, member = self._async_owned(context)
        member.execution = record_with_changes(member.execution, **changes)
        self.pipeline._commit_storage(_OWNER_FILES[current.owner_kind])
        context['expected'] = member.execution.model_dump(mode='json')
        return member.execution.model_copy(deep=True)

    @contextmanager
    def _async_storage_guard(self, context):
        with self.pipeline._provider_media_storage_guard():
            self._async_owned(context)
            yield

    def _resolve_query_authority(self, execution):
        handle = image_query_handle_from_record(execution)
        if self.pipeline.runtime_environment is None:
            raise ValueError('studio_image_runtime_environment_required')
        return resolve_media_runtime_authority(
            self.pipeline.runtime_environment, self.pipeline._provider_credential_store,
            media_kind='image', provider_model_id=handle.provider_model_id,
            scope=cast(ProviderScope, handle.scope), credential_ref=cast(str, handle.credential_ref),
        )

    def _wait_async_owned(self, context, seconds):
        deadline = time.monotonic() + seconds
        while True:
            with self.pipeline._save_lock:
                self._async_owned(context)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.25))

    def _execute_async_member(self, original, execution, authority, task):
        context = self._async_context(original, execution, task['_token'], task=task)
        directory = OwnedPrivateMediaDirectory(prefix=f'lumenx-studio-submit-{execution.attempt_id}-')
        try:
            def submitted(handle):
                with self.pipeline._save_lock:
                    _, member = self._async_owned(context)
                    if member.execution.phase != 'submitting':
                        raise StorageConflictError('studio_image_submit_checkpoint_changed')
                    require_matching_handle(member.execution, handle)
                    self._save_async_execution(context, phase='submitted', handle=handle.to_dict())
            handle = submit_async_image(
                execution, authority, output_root=self.pipeline.data_dir, scratch_dir=directory.path,
                max_bytes=self._max_bytes, on_submitted=submitted,
            )
        finally:
            directory.cleanup()
        # Retain the old adapters' bounded polling cadence, with lifecycle checks.
        wan = handle.query_contract is ProviderImageQueryContract.WAN27_IMAGE
        interval = _POLL_INTERVAL_SECONDS if wan else POLL_INTERVAL
        attempts = _POLL_ATTEMPTS if wan else max(1, MAX_WAIT // POLL_INTERVAL)
        deadline = time.monotonic() + interval * attempts
        for _ in range(attempts):
            self._wait_async_owned(context, min(interval, max(0, deadline - time.monotonic())))
            if time.monotonic() > deadline:
                break
            self._async_step(context, recovering=False, action='query')
            with self.pipeline._save_lock:
                _, member = self._async_owned(context)
                observation = observation_from_record(member.execution)
            if observation.status is ProviderTaskStatus.SUCCEEDED:
                self._async_step(context, recovering=False, action='fetch')
                return
            if observation.status in {ProviderTaskStatus.FAILED, ProviderTaskStatus.CANCELED}:
                return
        raise ProviderQueryError('timeout')

    def _async_next_action(self, current, execution):
        """One shared derivation for the step and the scheduler's read-only view."""
        image_query_handle_from_record(execution)
        state = self._file_state(execution)
        if execution.phase == 'completed':
            return 'terminal' if state == 'ready' else 'blocked'
        if state == 'invalid':
            return 'blocked'
        if state == 'ready':
            return 'repair' if self.association_reason(current) is None else 'blocked'
        observed = observation_from_record(execution) if execution.observation is not None else None
        if observed is not None and observed.status in {ProviderTaskStatus.FAILED, ProviderTaskStatus.CANCELED}:
            return 'terminal'
        return 'fetch' if observed is not None and observed.status is ProviderTaskStatus.SUCCEEDED else 'query'

    def recovery_member_state(self, request_id, attempt_id):
        """Read busy/query/fetch/repair/blocked/terminal/unknown without a claim."""
        with self.pipeline._save_lock:
            self.pipeline.assert_storage_available()
            original = self._find_request(request_id=request_id)
            if original is None:
                raise AssetImageRequestNotFound('studio_image_request_not_found')
            initial = next((m.execution for m in original.members if m.execution.attempt_id == attempt_id), None)
            if initial is None:
                raise AssetImageRequestNotFound('studio_image_member_not_found')
            current, member = self._retained_member(original, initial)
            if self._owner_key(current) in getattr(self.pipeline, '_asset_image_runs', {}):
                return 'busy'
            if member.execution.schema_version != 3 or member.execution.handle is None:
                return 'terminal' if member.execution.phase == 'cancelled' else 'unknown'
            return self._async_next_action(current, member.execution)

    def _async_step(self, context, *, recovering, action=None):
        with self.pipeline._save_lock:
            current, member = self._async_owned(context)
            execution = member.execution.model_copy(deep=True)
            next_action = self._async_next_action(current, execution)
            if next_action in {'terminal', 'blocked'}:
                return next_action
            if next_action == 'repair':
                if recovering:
                    self._complete_async_recovery(current, execution, context['token'])
                else:
                    self._complete_member(current, execution, context['token'])
                context['expected'] = member.execution.model_dump(mode='json')
                return 'repaired'
            if action == 'repair':
                return 'blocked'
            selected = action or next_action
            if selected == 'fetch' and next_action != 'fetch':
                raise ValueError('studio_image_success_observation_required')
            if selected not in {'query', 'fetch'}:
                raise ValueError('studio_image_recovery_action_invalid')
        with self._resolve_query_authority(execution) as authority:
            with self.pipeline._save_lock:
                self._async_owned(context)
            if selected == 'query':
                observed = query_async_image(image_query_handle_from_record(execution), authority)
                with self.pipeline._save_lock:
                    self._save_async_execution(context, phase='observed', observation=observation_to_dict(observed))
                return 'queried'
            self._fetch_async_member(context, execution, authority, recovering=recovering)
            return 'fetched'

    def _fetch_async_member(self, context, execution, authority, *, recovering):
        directory = OwnedPrivateMediaDirectory(prefix=f'lumenx-studio-image-{execution.attempt_id}-')
        result, verified, durable = None, False, False
        try:
            result = fetch_async_image_to_private_file(execution, authority, scratch_dir=directory.path, max_bytes=self._max_bytes)
            private_result = result
            if result.media_kind != 'image' or result.encoding != 'image/png' or Path(result.path).parent != directory.path:
                raise media_versions.MediaVersionError('Invalid private image result')
            with media_versions.verified_private_media_file(
                    result.path, scratch_root=directory.path, expected_sha256=result.sha256,
                    expected_size=result.size_bytes, max_bytes=self._max_bytes) as source:
                verified = True
                def prepared(path, sha256, size):
                    if (sha256, size) != (private_result.sha256, private_result.size_bytes):
                        raise media_versions.MediaVersionError('Private image changed during publication')
                    with self.pipeline._save_lock:
                        _, member = self._async_owned(context)
                        artifact = ProviderArtifact(image_query_handle_from_record(member.execution), path, sha256, size)
                        proof = require_matching_artifact_proof(member.execution, artifact, member.execution.prepared_artifact)
                        self._save_async_execution(context, prepared_artifact=proof)
                media_versions.store_media_stream(
                    iter(lambda: source.read(64 * 1024), b''), output_ref=execution.output_ref,
                    output_root=self.pipeline.data_dir, writer_lock=self.pipeline._writer_lock,
                    storage_guard=lambda: self._async_storage_guard(context), max_bytes=self._max_bytes,
                    on_prepared=prepared, publish_policy='no_overwrite', media_kind='image')
            with self.pipeline._save_lock:
                current, member = self._async_owned(context)
                if self._file_state(member.execution) != 'ready':
                    raise media_versions.MediaVersionError('Published image proof is not ready')
                durable = True
                if recovering:
                    self._complete_async_recovery(current, member.execution, context['token'])
                else:
                    self._complete_member(current, member.execution, context['token'])
                context['expected'] = member.execution.model_dump(mode='json')
            self._mirror_completed_image(context['original'], execution, result, directory.path)
        finally:
            keep = False
            if verified and not durable and result is not None:
                try:
                    directory.assert_owned()
                    with media_versions.verified_private_media_file(
                            result.path, scratch_root=directory.path, expected_sha256=result.sha256,
                            expected_size=result.size_bytes, max_bytes=self._max_bytes):
                        keep = True
                except (media_versions.MediaVersionError, OSError):
                    pass
            directory.cleanup(keep_name=Path(result.path).name if keep and result is not None else None)

    def recover_member_once(self, request_id, attempt_id, *, action=None):
        """One explicit provider query/fetch or local repair; no paid claim or dispatcher."""
        pipeline = self.pipeline
        with pipeline._save_lock:
            pipeline.assert_storage_available()
            original = self._find_request(request_id=request_id)
            if original is None:
                raise AssetImageRequestNotFound('studio_image_request_not_found')
            initial = next((m.execution for m in original.members if m.execution.attempt_id == attempt_id), None)
            if initial is None:
                raise AssetImageRequestNotFound('studio_image_member_not_found')
            if initial.schema_version != 3 or initial.handle is None:
                return self._ack(original, replayed=True)
            image_query_handle_from_record(initial)
            key, token = self._owner_key(original), object()
            if not hasattr(pipeline, '_asset_image_runs'):
                pipeline._asset_image_runs = {}
            if key in pipeline._asset_image_runs:
                return self._ack(original, replayed=True)
            pipeline._asset_image_runs[key] = token
            try:
                # Claim and live identity must be captured before the lock opens.
                context = self._async_context(original, initial, token)
            except BaseException:
                if pipeline._asset_image_runs.get(key) is token:
                    del pipeline._asset_image_runs[key]
                raise
        try:
            self._async_step(context, recovering=True, action=action)
            with pipeline._save_lock:
                current, _ = self._async_owned(context)
                return self._ack(current, replayed=True)
        finally:
            with pipeline._save_lock:
                if pipeline._asset_image_runs.get(key) is token:
                    del pipeline._asset_image_runs[key]

    def _complete_async_recovery(self, original, execution, token):
        """Append proven history only; selection and author fields remain untouched."""
        current, member = self._retained_member(original, execution)
        if (member.execution.phase != 'observed' or self._file_state(member.execution) != 'ready'
                or getattr(self.pipeline, '_asset_image_runs', {}).get(self._owner_key(current)) is not token):
            return
        try:
            target = self._projection_target(current)
        except StorageConflictError:
            return
        variant = ImageVariant.model_validate(dict(id=member.variant_id, url=member.execution.output_ref,
            created_at=current.created_at, prompt_used=member.execution.intent['prompt']))
        assignments = []
        if isinstance(target, Character) and current.channel in {'full_body', 'three_view', 'headshot'}:
            legacy, modern = {'full_body': ('full_body_asset', 'full_body'), 'three_view': ('three_view_asset', 'three_views'),
                              'headshot': ('headshot_asset', 'head_shot')}[current.channel]
            image_asset = getattr(target, legacy) or ImageAsset.model_validate({})
            image_unit = getattr(target, modern) or AssetUnit.model_validate({})
            histories = [image_asset.variants, image_unit.image_variants]
            assignments = [(legacy, image_asset), (modern, image_unit)]
        elif isinstance(target, Character):
            image_unit = target.reference_sheet or AssetUnit.model_validate({})
            histories = [image_unit.image_variants]
            assignments = [('reference_sheet', image_unit)]
        else:
            image_asset = target.image_asset or ImageAsset.model_validate({})
            histories = [image_asset.variants]
            assignments = [('image_asset', image_asset)]
        for history in histories:
            existing = next((v for v in history if v.id == variant.id), None)
            if existing is not None and existing.model_dump() != variant.model_dump():
                raise StorageConflictError('studio_image_variant_conflict')
        for history in histories:
            if not any(v.id == variant.id for v in history):
                history.append(variant.model_copy(deep=True))
        for field, value in assignments:
            setattr(target, field, value)
        member.execution = record_with_changes(member.execution, phase='completed', artifact=member.execution.prepared_artifact)
        current.projection_guard = image_projection_guard(target, current.channel)
        target.status = (GenerationStatus.COMPLETED if all(m.execution.phase == 'completed' for m in current.members)
                         and not self._incomplete_character_all(current) else 'needs_confirmation')
        self.pipeline._commit_storage(_OWNER_FILES[current.owner_kind])

    def _execute_member(self, original, execution, authority, token):
        directory = OwnedPrivateMediaDirectory(prefix=f'lumenx-studio-image-{execution.attempt_id}-')
        scratch = directory.path
        result = None
        verified = False
        durable = False
        try:
            result = self._executor(execution, authority, output_root=self.pipeline.data_dir,
                                    scratch_dir=scratch, max_bytes=self._max_bytes)
            if (result.media_kind != 'image' or result.encoding != 'image/png'
                    or Path(result.path).parent != scratch):
                raise media_versions.MediaVersionError('Invalid private image result')
            with media_versions.verified_private_media_file(
                    result.path, scratch_root=scratch, expected_sha256=result.sha256,
                    expected_size=result.size_bytes, max_bytes=self._max_bytes) as source:
                verified = True
                def prepared(path, sha256, size):
                    if (sha256, size) != (result.sha256, result.size_bytes):
                        raise media_versions.MediaVersionError('Private image changed during publication')
                    with self.pipeline._save_lock:
                        current, member = self._retained_member(original, execution)
                        proof = sync_artifact_proof_from_record(member.execution, relative_path=path, sha256=sha256, size_bytes=size)
                        member.execution = record_with_changes(member.execution, prepared_artifact=proof)
                        self.pipeline._commit_storage(_OWNER_FILES[current.owner_kind])
                media_versions.store_media_stream(
                    iter(lambda: source.read(64 * 1024), b''), output_ref=execution.output_ref,
                    output_root=self.pipeline.data_dir, writer_lock=self.pipeline._writer_lock,
                    storage_guard=self.pipeline._provider_media_storage_guard, max_bytes=self._max_bytes,
                    on_prepared=prepared, publish_policy='no_overwrite', media_kind='image')
            with self.pipeline._save_lock:
                current, member = self._retained_member(original, execution)
                if self._file_state(member.execution) != 'ready':
                    raise media_versions.MediaVersionError('Published image proof is not ready')
                self._complete_member(current, member.execution, token)
                durable = True
            self._mirror_completed_image(original, execution, result, scratch)
        finally:
            keep = False
            if verified and not durable and result is not None:
                try:
                    directory.assert_owned()
                    with media_versions.verified_private_media_file(
                            result.path, scratch_root=scratch, expected_sha256=result.sha256,
                            expected_size=result.size_bytes, max_bytes=self._max_bytes):
                        keep = True
                except (media_versions.MediaVersionError, OSError):
                    pass
            directory.cleanup(keep_name=Path(result.path).name if keep and result is not None else None)

    def _mirror_completed_image(self, original, execution, result, scratch):
        """Best-effort mirror after local proof/projection; never part of repair."""
        if original.entrypoint not in {STUDIO_SCENE_PROP_IMAGE_ENTRYPOINT, STUDIO_FULL_BODY_IMAGE_ENTRYPOINT, STUDIO_STORYBOARD_IMAGE_ENTRYPOINT, STUDIO_THREE_VIEW_IMAGE_ENTRYPOINT, STUDIO_HEADSHOT_IMAGE_ENTRYPOINT}:
            return
        pipeline = self.pipeline
        with pipeline._save_lock:
            pipeline.assert_storage_available()
            _, member = self._retained_member(original, execution)
            saved = member.execution
            if (saved.phase != 'completed' or saved.artifact is None
                    or saved.artifact != saved.prepared_artifact or self._file_state(saved) != 'ready'):
                return
            proof = deepcopy(saved.artifact)
        try:
            with media_versions.verified_private_media_file(
                    result.path, scratch_root=scratch, expected_sha256=proof['sha256'],
                    expected_size=proof['size_bytes'], max_bytes=self._max_bytes) as source:
                payload = source.read(self._max_bytes + 1)
            if len(payload) != proof['size_bytes'] or hashlib.sha256(payload).hexdigest() != proof['sha256']:
                raise media_versions.MediaVersionError('Private mirror bytes changed after verification')
            pipeline.assert_storage_available()
            uploader = pipeline.oss_uploader()
            pipeline.assert_storage_available()
            sub_path = 'storyboard' if original.asset_type == 'storyboard_frame' else f'assets/{original.asset_type}s'
            uploader.upload_bytes(payload, sub_path=sub_path,
                                  custom_filename=f'{execution.attempt_id}.png', max_bytes=self._max_bytes)
        except StorageError:
            raise
        except Exception:
            pipeline.assert_storage_available()
            logger.warning('Studio image optional mirror did not complete (%s)', execution.attempt_id)
        pipeline.assert_storage_available()

    def _character_image_history(self, target, channel):
        containers = self.pipeline._variant_channels(target, 'character', channel)[0][1]
        selected = next((
            value.id for container in containers for value in self.pipeline._image_variants(container)
            if value.id == self.pipeline._selected_image(container)
        ), None)
        # Preserve the modern selection and both histories before legacy reconciliation.
        previous = {}
        for container in containers:
            for value in self.pipeline._image_variants(container):
                if value.id not in previous:
                    previous[value.id] = value.model_copy(deep=True)
        return ImageAsset(selected_id=selected, variants=list(previous.values()))

    def _complete_member(self, original, execution, token):
        current, member = self._retained_member(original, execution)
        if member.execution.phase == 'completed':
            return
        expected_phase = 'observed' if member.execution.schema_version == 3 else 'submitting'
        if (member.execution.phase != expected_phase or self._file_state(member.execution) != 'ready'
                or getattr(self.pipeline, '_asset_image_runs', {}).get(self._owner_key(current)) is not token):
            return
        try:
            target = self._projection_target(current)
            original_task = self._active_task(current)
            if (isinstance(target, StoryboardFrame) or original_task and original_task.get('_character_all_context')
                    or member.execution.intent.get('intent_contract_version') in (*GPT_IMAGE2_EDIT_CONTRACT_VERSIONS, QWEN20_IMAGE_EDIT_CONTRACT_VERSION)):
                task = self._active_task(current)
                if task is not None:
                    self._require_original(current, task)
        except StorageConflictError:
            return
        variant = ImageVariant.model_validate(dict(
            id=member.variant_id, url=member.execution.output_ref,
            created_at=current.created_at, prompt_used=member.execution.intent['prompt']))
        if isinstance(target, StoryboardFrame):
            unit = target.rendered_image_asset or ImageAsset.model_validate({})
            existing = next((v for v in unit.variants if v.id == variant.id), None)
            if existing is not None and existing.model_dump() != variant.model_dump():
                raise StorageConflictError('studio_image_variant_conflict')
            if existing is None:
                unit.variants.append(variant)
            unit.selected_id = variant.id
            target.rendered_image_asset = unit
            target.image_url = target.rendered_image_url = variant.url
            target.image_prompt = member.execution.intent['prompt']
            target.updated_at = current.created_at
        elif isinstance(target, Character) and current.channel == 'full_body':
            unit = (self._character_image_history(target, 'full_body')
                    if member.execution.intent.get('intent_contract_version') == GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION
                    else target.full_body_asset or ImageAsset.model_validate({}))
            existing = next((v for v in unit.variants if v.id == variant.id), None)
            if existing is not None and existing.model_dump() != variant.model_dump():
                raise StorageConflictError('studio_image_variant_conflict')
            if existing is None:
                unit.variants.insert(0, variant)
            if all(m is member or m.execution.phase == 'completed' for m in current.members):
                if len(current.members) == 1 or not unit.selected_id:
                    unit.selected_id = current.members[0].variant_id
            target.full_body_asset = unit
            target.full_body = target.full_body or AssetUnit.model_validate({})
            target.full_body.image_prompt = current.base_prompt
            target.full_body_prompt = current.base_prompt
            target.full_body_updated_at = current.created_at
            target.is_consistent = False
            self.pipeline._reconcile_asset_image_result(target, target, 'character', 'full_body', failed=False)
        elif isinstance(target, Character) and current.channel in {'three_view', 'headshot'}:
            unit_field, legacy_field = (('three_views', 'three_view_asset') if current.channel == 'three_view'
                                        else ('head_shot', 'headshot_asset'))
            if current.channel == 'headshot':
                unit = self._character_image_history(target, 'headshot')
            else:
                unit = getattr(target, legacy_field) or ImageAsset.model_validate({})
            existing = next((v for v in unit.variants if v.id == variant.id), None)
            if existing is not None and existing.model_dump() != variant.model_dump():
                raise StorageConflictError('studio_image_variant_conflict')
            if existing is None:
                unit.variants.insert(0, variant)
            if all(m is member or m.execution.phase == 'completed' for m in current.members):
                if len(current.members) == 1 or not unit.selected_id:
                    unit.selected_id = current.members[0].variant_id
            setattr(target, legacy_field, unit)
            image_unit = getattr(target, unit_field) or AssetUnit.model_validate({})
            image_unit.image_prompt = current.base_prompt
            setattr(target, unit_field, image_unit)
            setattr(target, f'{current.channel}_prompt', current.base_prompt)
            setattr(target, f'{current.channel}_updated_at', current.created_at)
            # A member timestamp does not prove the currently selected derived images agree.
            # Do not change the shared flag or neighboring receipt projection guards.
            self.pipeline._reconcile_asset_image_result(target, target, 'character', current.channel, failed=False)
        elif isinstance(target, Character):
            unit = target.reference_sheet or AssetUnit.model_validate({})
            existing = next((v for v in unit.image_variants if v.id == variant.id), None)
            if existing is not None and existing.model_dump() != variant.model_dump():
                raise StorageConflictError('studio_image_variant_conflict')
            if existing is None:
                unit.image_variants.append(variant)
            if not unit.selected_image_id:
                unit.selected_image_id = variant.id
                target.image_url = variant.url
            target.reference_sheet = unit
        else:
            unit = target.image_asset or ImageAsset.model_validate({})
            existing = next((v for v in unit.variants if v.id == variant.id), None)
            if existing is not None and existing.model_dump() != variant.model_dump():
                raise StorageConflictError('studio_image_variant_conflict')
            if existing is None:
                unit.variants.append(variant)
            ids = [m.variant_id for m in current.members]
            generated = {v.id: v for v in unit.variants if v.id in ids}
            unit.variants = [generated[key] for key in reversed(ids) if key in generated] + [v for v in unit.variants if v.id not in ids]
            if all(m is member or m.execution.phase == 'completed' for m in current.members):
                if len(current.members) == 1 or not unit.selected_id:
                    unit.selected_id = current.members[0].variant_id
                    target.image_url = current.members[0].execution.output_ref
            target.image_asset = unit
        member.execution = record_with_changes(member.execution, phase='completed', artifact=member.execution.prepared_artifact)
        current.projection_guard = image_projection_guard(target, current.channel)
        target.status = (GenerationStatus.COMPLETED if all(m.execution.phase == 'completed' for m in current.members)
                         else GenerationStatus.PROCESSING)
        self.pipeline._commit_storage(_OWNER_FILES[current.owner_kind])
        task = self._active_task(current)
        if task and task.get('_character_all_context') is not None:
            task['_character_all_context']['expected'] = target.model_dump()

    def _finish_status(self, original):
        current = self._retained_request(original)
        if self._owner_key(current) in getattr(self.pipeline, '_asset_image_runs', {}):
            return
        try:
            target = self._projection_target(current)
            task = self.pipeline.asset_generation_tasks.get(current.id)
            if task and task.get('_character_all_context') is not None:
                self.pipeline._check_asset_image(task['_character_all_context'])
        except StorageConflictError:
            return
        state = self.request_state(current)
        status = (GenerationStatus.COMPLETED if state['status'] == 'completed'
                  and not self._incomplete_character_all(current) else 'needs_confirmation')
        if target.status != status:
            target.status = status
            self.pipeline._commit_storage(_OWNER_FILES[current.owner_kind])
            task = self.pipeline.asset_generation_tasks.get(current.id)
            if task and task.get('_character_all_context') is not None:
                task['_character_all_context']['expected'] = target.model_dump()

    def repair_request(self, root_kind, root_id, client_request_id) -> AssetImageAdmission:
        """Repair only a retained proof and final file; never resolve credentials."""
        pipeline = self.pipeline
        with pipeline._save_lock:
            original = self.read_request(root_kind, root_id, client_request_id).request
            token, key = object(), self._owner_key(original)
            pipeline.assert_storage_available()
            if not hasattr(pipeline, '_asset_image_runs'):
                pipeline._asset_image_runs = {}
            if key in pipeline._asset_image_runs:
                return self._ack(self._retained_request(original), replayed=True)
            if (original.asset_type == 'storyboard_frame'
                    and (root_id, original.asset_id) in getattr(pipeline, '_storyboard_image_runs', {})):
                return self._ack(self._retained_request(original), replayed=True)
            pipeline._asset_image_runs[key] = token
            try:
                contexts = {
                    initial.execution.attempt_id: self._async_context(original, initial.execution, token)
                    for initial in original.members if initial.execution.schema_version == 3
                }
            except BaseException:
                if pipeline._asset_image_runs.get(key) is token:
                    del pipeline._asset_image_runs[key]
                raise
        try:
            with pipeline._save_lock:
                for initial in original.members:
                    context = contexts.get(initial.execution.attempt_id)
                    if context is not None:
                        current, member = self._async_owned(context)
                        if member.execution.phase == 'observed' and self._file_state(member.execution) == 'ready':
                            self._complete_async_recovery(current, member.execution, token)
                            context['expected'] = member.execution.model_dump(mode='json')
                    else:
                        current, member = self._retained_member(original, initial.execution)
                        if member.execution.phase == 'submitting' and self._file_state(member.execution) == 'ready':
                            self._complete_member(current, member.execution, token)
                if contexts:
                    for context in contexts.values():
                        self._async_owned(context)
                    current = self._retained_request(original)
                    # Finish under the same lock, without transferring this claim.
                    del pipeline._asset_image_runs[key]
                    self._finish_status(current)
                    return self._ack(current, replayed=True)
        finally:
            with pipeline._save_lock:
                if pipeline._asset_image_runs.get(key) is token:
                    del pipeline._asset_image_runs[key]
        with pipeline._save_lock:
            current = self._retained_request(original)
            self._finish_status(current)
            return self._ack(current, replayed=True)

    def _prepare(self, root_kind, root_id, command):
        if command.asset_type == 'storyboard_frame':
            from .pipeline import ProjectContextError
            if root_kind != 'project':
                raise ValueError('studio_storyboard_project_required')
            view = self.pipeline.read_committed_project_view(root_id)
            root = view[0]
            if root is None:
                raise ProjectContextError('Script not found', not_found=True)
            if root.revision != command.expected_revision:
                raise StorageConflictError('Project changed; reload before generating the frame')
            frame_context = self.pipeline._prepare_storyboard_image(
                view, command.asset_id, command.composition_data, command.prompt, 1)
            if not self.pipeline._is_core_storyboard_image(frame_context):
                raise ValueError('studio_storyboard_image_inputs_not_supported')
            context = dict(frame_context, key=('script', root_id, 'storyboard_frame', command.asset_id),
                           root_kind=root_kind, root_id=root_id, kind='storyboard_frame', asset_id=command.asset_id,
                           target=frame_context['frame'], root_identity=frame_context['script_identity'],
                           target_identity=frame_context['frame_identity'], owner_identity=frame_context['script_identity'],
                           owner=('script', root_id), series_id=root.series_id,
                           request_root_scope_id=root.asset_image_request_scope_id, view=view,
                           expected_revision=root.revision, storyboard_context=frame_context, base_prompt=command.prompt,
                           reference_image_url=next(iter(frame_context['implicit_reference_urls']), None))
            intent, version = self._intent_from_context(context, command)
            return context, intent, version
        if command.asset_type == 'character' and (
                command.generation_type in {'three_view', 'headshot'}
                or (command.generation_type == 'full_body' and command.reference_variant_id is not None)):
            return self._prepare_character_edit(root_kind, root_id, command)
        # URL-only commands retain the pre-edit rejection contract.
        if command.reference_image_url is not None:
            raise ValueError('studio_image_reference_not_supported')
        if command.aspect_ratio is not None and command.aspect_ratio not in ASPECT_RATIO_TO_SIZE:
            raise ValueError('studio_image_aspect_ratio_invalid')
        params = command.model_dump(exclude={'client_request_id', 'asset_id', 'asset_type', 'seed', 'watermark', 'expected_revision', 'composition_data', 'reference_variant_id', 'reference_source'})
        view = self.pipeline._read_asset_image_view(root_kind, root_id)
        if view[0] is None:
            from .pipeline import ProjectContextError
            raise ProjectContextError('Asset request root not found', not_found=True)
        context = self.pipeline._prepare_asset_image(
            view, command.asset_id, command.asset_type, params, root_kind=root_kind,
        )
        context['request_root_scope_id'] = view[0].asset_image_request_scope_id
        intent, image_contract_version = self._intent_from_context(context, command)
        return context, intent, image_contract_version

    def _prepare_character_edit(self, root_kind, root_id, command):
        channel = command.generation_type
        full_body = channel == 'full_body'
        view = self.pipeline._read_asset_image_view(root_kind, root_id)
        root = view[0]
        if root is None:
            from .pipeline import ProjectContextError
            raise ProjectContextError('Asset request root not found', not_found=True)
        wan_edit = channel == 'three_view' and root.model_settings.i2i_model in STUDIO_WAN_IMAGE_MODELS
        qwen_edit = channel == 'three_view' and root.model_settings.i2i_model in STUDIO_QWEN_IMAGE_MODELS
        dashscope_edit = wan_edit or qwen_edit
        allowed_models = STUDIO_GPT_IMAGE_MODELS
        if dashscope_edit:
            family = 'qwen' if qwen_edit else 'wan'
            provider_model = root.model_settings.i2i_model.removeprefix(f'{family}/').removesuffix('#image')
            allowed_models = frozenset({provider_model, f'{family}/{provider_model}#image'})
        if ((dashscope_edit and root_kind != 'project')
                or root.model_settings.i2i_model not in allowed_models
                or (command.model_name is not None and command.model_name not in allowed_models)):
            raise ValueError(f'studio_{channel}_model_not_supported')
        forbidden = {'aspect_ratio'} if dashscope_edit else ({'seed', 'watermark'} if full_body else {'aspect_ratio', 'seed', 'watermark'})
        if dashscope_edit and 'seed' in command.model_fields_set and command.seed is None:
            raise ValueError('studio_three_view_parameter_not_supported')
        if command.model_fields_set & forbidden:
            raise ValueError(f'studio_{channel}_parameter_not_supported')
        if full_body and command.aspect_ratio is not None and command.aspect_ratio not in ASPECT_RATIO_TO_SIZE:
            raise ValueError('studio_image_aspect_ratio_invalid')
        if command.negative_prompt and not qwen_edit:
            raise ValueError('studio_image_negative_prompt_not_supported' if wan_edit else 'studio_gpt_image_negative_prompt_not_supported')
        params = command.model_dump(exclude={
            'client_request_id', 'asset_id', 'asset_type', 'seed', 'watermark',
            'expected_revision', 'composition_data', 'reference_variant_id', 'reference_source',
        })
        context = self.pipeline._prepare_asset_image(
            view, command.asset_id, command.asset_type, params, root_kind=root_kind,
        )
        target = context['target']
        if dashscope_edit and (target.base_character_id or command.reference_source is not None):
            raise ValueError('studio_three_view_selected_local_reference_required')
        if wan_edit and context['options']['negative_prompt']:
            raise ValueError('studio_image_negative_prompt_not_supported')
        parent_reference = None
        if full_body and target.base_character_id:
            if root_kind != 'project' or context['owner'] != ('script', root.id):
                raise ValueError('studio_full_body_base_character_not_supported')
            parent, parent_owner = self.pipeline._saved_asset_image_target(view, target.base_character_id, 'character')
            if not isinstance(parent, Character) or parent.id == target.id or parent_owner != context['owner']:
                raise ValueError('studio_full_body_base_character_not_supported')
            parent_reference = selected_three_view_reference(parent, reject_ambiguous=True)
            context['view'] = view
        if command.reference_source is not None:
            if full_body:
                raise ValueError(f'studio_{channel}_fallback_reference_not_supported')
            fallback = derived_image_fallback_reference(target, channel, reject_ambiguous=True)
            if (fallback is None or command.reference_source != fallback[0]
                    or command.reference_variant_id != fallback[1]
                    or command.reference_image_url != fallback[2]):
                raise ValueError(f'studio_{channel}_fallback_reference_changed')
            reference_url = fallback[2]
            if (not reference_url or reference_url.strip() != reference_url
                    or any(ord(value) < 32 or ord(value) == 127 for value in reference_url)
                    or classify_media_ref(reference_url, output_root=str(self.pipeline.data_dir)) != MEDIA_REF_LOCAL_PATH):
                raise ValueError(f'studio_{channel}_fallback_local_reference_required')
        else:
            try:
                if full_body:
                    if target.base_character_id:
                        reference = parent_reference
                    else:
                        source = uploaded_full_body_reference(target, reject_ambiguous=True)
                        reference = source[1] if source else None
                else:
                    reference = selected_three_view_reference(target, reject_ambiguous=True)
            except ValueError as exc:
                if channel == 'headshot':
                    raise ValueError('studio_headshot_selected_reference_ambiguous') from exc
                raise
            if (reference is None or not command.reference_variant_id or not command.reference_image_url
                    or command.reference_variant_id != reference.id or command.reference_image_url != reference.url
                    or classify_media_ref(reference.url, output_root=str(self.pipeline.data_dir)) != MEDIA_REF_LOCAL_PATH):
                raise ValueError(f'studio_{channel}_selected_local_reference_required')
            reference_url = reference.url
        if full_body and context['options']['negative_prompt']:
            raise ValueError('studio_gpt_image_negative_prompt_not_supported')
        size = context['options']['size'] if full_body else '1024x1024'
        if dashscope_edit:
            # Legacy three-view omits size, so the active image model config owns it.
            size = self.pipeline.asset_generator.model.params.get('size', '1280*1280')
        context['options'] = dict(context['options'], model_name=root.model_settings.i2i_model, size=size)
        context['request_root_scope_id'] = root.asset_image_request_scope_id
        context['reference_image_url'] = reference_url
        intent, image_contract_version = self._intent_from_context(context, command)
        return context, intent, image_contract_version

    @staticmethod
    def _validate_prepared_command(command, context):
        params = command.model_dump(exclude={'client_request_id', 'asset_id', 'asset_type', 'seed', 'watermark', 'expected_revision', 'composition_data', 'reference_variant_id', 'reference_source'})
        if (
            command.asset_id != context['asset_id'] or command.asset_type != context['kind']
            or params != context['params']
            or 'seed' in command.model_fields_set or command.watermark
            or context['key'] != (*context['owner'], context['kind'], context['asset_id'])
            or not _is_target(context['target'], context['kind'])
            or context['target'].model_dump() != context['expected']
        ):
            raise StorageConflictError('studio_image_prepared_context_mismatch')
        if command.asset_type == 'storyboard_frame' and (
                command.expected_revision != context['view'][0].revision
                or command.prompt != (context['options']['prompt'] or '')
                or command.composition_data != context['composition']):
            raise StorageConflictError('studio_image_prepared_context_mismatch')
        if command.reference_image_url is not None:
            raise ValueError('studio_image_reference_not_supported')
        if command.aspect_ratio is not None and command.aspect_ratio not in ASPECT_RATIO_TO_SIZE:
            raise ValueError('studio_image_aspect_ratio_invalid')

    @staticmethod
    def _intent_from_context(context, command):
        options, target = context['options'], context['target']
        if (isinstance(target, Character) and command.generation_type == 'full_body'
                and context.get('reference_image_url') is not None):
            from ...models.mulerouter import _normalize_gpt_image_size
            base_prompt, prompt, size = prepare_character_full_body(
                target.name, target.description, prompt=options['prompt'],
                positive_prompt=options['positive_prompt'], size=options['size'],
            )
            if not base_prompt.strip():
                raise ValueError('studio_image_base_prompt_required')
            context['base_prompt'] = base_prompt
            return normalize_generation_intent(
                {'model_id': options['model_name'], 'mode': 'i2i',
                 'prompt': prepare_full_body_edit_prompt(prompt), 'params': {'size': _normalize_gpt_image_size(size)}},
                allowed_modes={'i2i'}, allowed_parameters={'size'},
            ), GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION
        if isinstance(target, Character) and command.generation_type in {'three_view', 'headshot'}:
            prepare_prompt = prepare_character_three_view if command.generation_type == 'three_view' else prepare_character_headshot
            base_prompt, prompt = prepare_prompt(
                target.name, target.description, prompt=options['prompt'], positive_prompt=options['positive_prompt'],
            )
            if not base_prompt.strip():
                raise ValueError('studio_image_base_prompt_required')
            context['base_prompt'] = base_prompt
            if command.generation_type == 'three_view' and options['model_name'] in STUDIO_QWEN_IMAGE_MODELS:
                # Preserve the existing assets.py three-view negative suffix.
                negative = options['negative_prompt'] + THREE_VIEW_NEGATIVE_SUFFIX
                if len(negative) > 500:
                    raise ValueError('studio_image_negative_prompt_exceeds_500_characters')
                qwen_params: dict[str, str | bool | int] = {
                    'size': options['size'], 'watermark': command.watermark,
                    'prompt_extend': True, 'negative_prompt': negative,
                }
                if command.seed is not None:
                    qwen_params['seed'] = command.seed
                return normalize_generation_intent(
                    {'model_id': options['model_name'], 'mode': 'i2i', 'prompt': prompt, 'params': qwen_params},
                    allowed_modes={'i2i'}, allowed_parameters=set(qwen_params),
                ), QWEN20_IMAGE_EDIT_CONTRACT_VERSION
            if command.generation_type == 'three_view' and options['model_name'] in STUDIO_WAN_IMAGE_MODELS:
                wan_params: dict[str, str | bool | int] = {'size': options['size'], 'watermark': command.watermark}
                if command.seed is not None:
                    wan_params['seed'] = command.seed
                return normalize_generation_intent(
                    {'model_id': options['model_name'], 'mode': 'i2i', 'prompt': prompt, 'params': wan_params},
                    allowed_modes={'i2i'}, allowed_parameters=set(wan_params),
                ), WAN27_IMAGE_EDIT_CONTRACT_VERSION
            return normalize_generation_intent(
                {'model_id': options['model_name'], 'mode': 'i2i', 'prompt': prompt, 'params': {'size': '1024x1024'}},
                allowed_modes={'i2i'}, allowed_parameters={'size'},
            ), GPT_IMAGE2_EDIT_CONTRACT_VERSION
        if isinstance(target, StoryboardFrame):
            from .storyboard import prepare_storyboard_prompt
            prompt = prepare_storyboard_prompt(target, context['characters'], context['scene'], command.prompt)
            params = {'size': options['size'], 'watermark': False, 'prompt_extend': True}
            mode = 'i2i' if context['reference_image_url'] is not None else 't2i'
            return normalize_generation_intent(
                {'model_id': options['model_name'], 'mode': mode, 'prompt': prompt, 'params': params},
                allowed_modes={mode}, allowed_parameters=set(params),
            ), QWEN20_IMAGE_EDIT_CONTRACT_VERSION if mode == 'i2i' else QWEN20_IMAGE_CONTRACT_VERSION
        wan_names = WAN27_IMAGE_PROVIDER_MODELS | {f'wan/{name}#image' for name in WAN27_IMAGE_PROVIDER_MODELS}
        qwen_names = QWEN20_IMAGE_PROVIDER_MODELS | {f'qwen/{name}#image' for name in QWEN20_IMAGE_PROVIDER_MODELS}
        if options['model_name'] in wan_names:
            image_contract_version = WAN27_IMAGE_CONTRACT_VERSION
            if options['negative_prompt']:
                raise ValueError('studio_image_negative_prompt_not_supported')
        elif options['model_name'] in qwen_names:
            image_contract_version = QWEN20_IMAGE_CONTRACT_VERSION
            if len(options['negative_prompt']) > 500:
                raise ValueError('studio_image_negative_prompt_exceeds_500_characters')
        elif options['model_name'] in STUDIO_GPT_IMAGE_MODELS:
            if (command.asset_type, command.generation_type) not in {
                ('character', 'reference_sheet'), ('character', 'full_body'),
                ('scene', 'all'), ('prop', 'all'),
            }:
                raise ValueError('studio_gpt_image_channel_not_supported')
            if command.negative_prompt:
                raise ValueError('studio_gpt_image_negative_prompt_not_supported')
            if command.model_fields_set & {'seed', 'watermark'}:
                raise ValueError('studio_gpt_image_parameter_not_supported')
            image_contract_version = None
        else:
            raise ValueError('studio_image_model_not_supported')
        if isinstance(target, Character) and command.generation_type == 'full_body':
            if has_full_body_reference_input(target):
                raise ValueError('studio_image_reference_not_supported')
            base_prompt, prompt, size = prepare_character_full_body(
                target.name, target.description, prompt=options['prompt'],
                positive_prompt=options['positive_prompt'], size=options['size'],
            )
            if not base_prompt.strip():
                raise ValueError('studio_image_base_prompt_required')
            context['base_prompt'] = base_prompt
        elif command.asset_type == 'character':
            prompt, size = prepare_character_reference_sheet(
                target.name, target.description, prompt=options['prompt'],
                positive_prompt=options['positive_prompt'], size=options['size'],
            )
        else:
            prepare = prepare_scene_image if command.asset_type == 'scene' else prepare_prop_image
            prompt, size = prepare(target.name, target.description, prompt=options['prompt'],
                                   positive_prompt=options['positive_prompt'], size=options['size'])
        if image_contract_version is None:
            from ...models.mulerouter import _normalize_gpt_image_size
            params: dict[str, object] = {'size': _normalize_gpt_image_size(size)}
        else:
            params = {'size': size, 'watermark': command.watermark}
        if image_contract_version == QWEN20_IMAGE_CONTRACT_VERSION:
            params.update(prompt_extend=True, negative_prompt=options['negative_prompt'])
        if 'seed' in command.model_fields_set:
            params['seed'] = command.seed
        intent = normalize_generation_intent(
            {'model_id': options['model_name'], 'mode': 't2i', 'prompt': prompt, 'params': params},
            allowed_modes={'t2i'}, allowed_parameters=set(params),
        )
        return intent, image_contract_version

    def _acceptance_target(self, context):
        if context['kind'] == 'storyboard_frame':
            self.pipeline._check_storyboard_image(context['storyboard_context'])
            if (context['expected_revision'] is not None
                    and self.pipeline.read_committed_script(context['root_id']).revision != context['expected_revision']):
                raise StorageConflictError('Project changed; reload before generating the frame')
        root, target, owner_object, owner = self.pipeline._live_asset_image_target(
            context['root_kind'], context['root_id'], context['asset_id'], context['kind'],
        )
        if (
            root is None or root is not context['root_identity']
            or not _is_target(target, context['kind']) or target is not context['target_identity']
            or owner_object is None or owner_object is not context['owner_identity']
            or owner is None or owner != context['owner']
            or root.asset_image_request_scope_id != context['request_root_scope_id']
            or (isinstance(root, Script) and root.series_id != context['series_id'])
            or target.model_dump() != context['expected']
        ):
            raise StorageConflictError('studio_image_target_changed')
        return root, target, owner_object, owner

    @staticmethod
    def _validate_root(root_kind, root_id):
        from .pipeline import _validate_safe_id
        if root_kind not in {'project', 'series'}:
            raise ValueError('studio_image_request_root_invalid')
        _validate_safe_id(root_id, 'request root')

    def read_committed_requests(self) -> list[AssetImageRequestRecord]:
        """Detached receipts from each actual owner, never merged episode views."""
        with self.pipeline._save_lock:
            self.pipeline.assert_storage_available()
            records = list(self._iter_committed_requests())
            if len({record.id for record in records}) != len(records):
                raise StorageError('Duplicate retained Studio image request')
            return records

    def _iter_committed_requests(self):
        committed = self.pipeline._committed
        owners = [('script', key, value) for key, value in committed['projects.json'].items()]
        owners.extend(('series', key, value) for key, value in committed['series.json'].items())
        owners.append(('global', 'library', committed['library_assets.json']))
        for owner_kind, owner_id, aggregate in owners:
            for raw in aggregate.get('asset_image_requests', []):
                try:
                    record = AssetImageRequestRecord.model_validate(deepcopy(raw))
                    if (record.owner_kind, record.owner_id) != (owner_kind, owner_id):
                        raise ValueError('foreign owner')
                except (TypeError, ValueError) as exc:
                    raise StorageError('Invalid retained Studio image request') from exc
                yield record

    def _find_request(self, root_kind=None, root_id=None, client_request_id=None, *, request_id=None):
        found = [record for record in self._iter_committed_requests()
                 if (record.id == request_id if request_id is not None else
                     (record.request_root_kind, record.request_root_id, record.client_request_id)
                     == (root_kind, root_id, client_request_id))]
        if len(found) > 1:
            raise StorageError('Duplicate retained Studio image request')
        return found[0] if found else None

    def _ack(self, record, *, replayed):
        return AssetImageAdmission(record.model_copy(deep=True), replayed, self.association_reason(record))

    def association_reason(self, record: AssetImageRequestRecord) -> str | None:
        if any(member.execution.schema_version == 3 and not studio_image_material_complete(member.execution)
               for member in record.members):
            return 'studio_image_material_incomplete'
        view = self.pipeline._read_asset_image_view(record.request_root_kind, record.request_root_id)
        root = view[0]
        if root is None or root.asset_image_request_scope_id != record.request_root_scope_id:
            return 'studio_image_request_root_changed'
        if isinstance(root, Script) and root.series_id != record.request_root_series_id:
            return 'studio_image_request_root_changed'
        target, owner = self.pipeline._saved_asset_image_target(view, record.asset_id, record.asset_type, root_kind=record.request_root_kind)
        if target is None or owner != (record.owner_kind, record.owner_id):
            return 'studio_image_request_owner_changed'
        if getattr(target, _POINTER_FIELDS[(record.asset_type, record.channel)]) != record.id:
            return 'studio_image_request_target_changed'
        if self._input_guard(target, record.channel, view=view, base_prompt=record.base_prompt,
                             image_contract_version=record.members[0].execution.intent.get('intent_contract_version')) != record.input_guard:
            return 'studio_image_request_inputs_changed'
        if image_projection_guard(target, record.channel) != record.projection_guard:
            return 'studio_image_request_projection_changed'
        return None
