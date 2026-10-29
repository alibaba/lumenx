"""Media drafts are editable intent; they never grant generation authority."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.apps.atelier.generation_draft import (
    AtelierGenerationDraftService, ImageGenerationDraftCommand, AudioGenerationDraftCommand,
)
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.apps.atelier.tts_commands import list_builtin_tts_voices, TTSCandidateCommand, normalize_tts_candidate_request
from src.utils.json_storage import StorageConflictError


def image(**changes):
    raw = dict(expected_revision=0, media_kind='image', prompt='', model_id='wan/wan2.7-image-pro#image',
        mode='i2i', params={'seed': 0, 'watermark': False}, media=[], batch_size=2)
    raw.update(changes)
    return ImageGenerationDraftCommand(**raw)


def audio(**changes):
    raw = dict(expected_revision=0, media_kind='audio', prompt='', voice='longanyang', params={'volume': 0})
    raw.update(changes)
    return AudioGenerationDraftCommand(**raw)


@pytest.mark.parametrize('kind,command', [('image', image), ('audio', audio)])
def test_media_draft_roundtrip_allows_incomplete_intent_without_execution(tmp_path, kind, command):
    store = AtelierStorage(tmp_path)
    try:
        store.create_project(AtelierProject(id='p', title='Draft', created_at=1, updated_at=1,
            nodes=[AtelierNode(id='n', project_id='p', type=kind, data={'custom_note': 'keep'})]))
        before = store.get_project('p')
        ack = AtelierGenerationDraftService(store, clock=lambda: 7).update_generation_draft('p', 'n', command())
        assert ack.project.nodes[0].prompt == ''
        assert ack.project.nodes[0].data['custom_note'] == 'keep'
        assert ack.project.nodes[0].data['generation_draft'] == command().draft_payload()
        assert ack.project.revision == 1
        assert ack.project.generation_attempts == before.generation_attempts == []
        assert ack.project.generation_request_receipts == before.generation_request_receipts == []
        assert ack.project.nodes[0].media_urls == []
        with pytest.raises(StorageConflictError):
            AtelierGenerationDraftService(store).update_generation_draft('p', 'n', command())
        assert store.get_project('p') == ack.project
    finally:
        store.close()
    reopened = AtelierStorage(tmp_path)
    try:
        assert reopened.get_project('p').nodes[0].data['generation_draft'] == command().draft_payload()
    finally:
        reopened.close()


def test_media_draft_rejects_cross_kind_write_without_changing_project(tmp_path):
    store = AtelierStorage(tmp_path)
    try:
        store.create_project(AtelierProject(id='p', title='Draft', created_at=1, updated_at=1,
            nodes=[AtelierNode(id='n', project_id='p', type='video')]))
        before = deepcopy(store.get_project('p'))
        with pytest.raises(ValueError):
            AtelierGenerationDraftService(store).update_generation_draft('p', 'n', image())
        assert store.get_project('p') == before
    finally:
        store.close()


@pytest.mark.parametrize('factory,changes', [
    (image, {'batch_size': 7}), (image, {'params': {'credential_ref': 'evil'}}),
    (image, {'model_id': 'unknown-image'}), (image, {'mode': 't2i', 'media': [{'role': 'reference_image', 'ordinal': 0, 'node_id': 'ref', 'media_url': 'uploads/ref.png'}]}),
    (image, {'media': [{'role': 'reference_image', 'ordinal': 1, 'node_id': 'ref', 'media_url': 'uploads/ref.png'}]}),
    (audio, {'voice': 'private-clone'}), (audio, {'params': {'volume': False}}),
    (audio, {'params': {'speech_rate': float('nan')}}), (audio, {'voice': 'Cherry', 'params': {'volume': 0}}),
])
def test_media_draft_rejects_invalid_parameters(factory, changes):
    with pytest.raises((ValueError, ValidationError)):
        factory(**changes)


def test_builtin_voice_projection_matches_generation_capabilities():
    voices = list_builtin_tts_voices()
    assert voices and len({voice['id'] for voice in voices}) == len(voices)
    assert all(set(voice) == {'id', 'name', 'family', 'supported_parameters'} for voice in voices)
    assert next(voice for voice in voices if voice['id'] == 'longanyang')['supported_parameters'] == ['instructions', 'pitch_rate', 'speech_rate', 'volume']
    for voice in voices:
        options = {'instructions': 'Calm narration', 'pitch_rate': 1, 'speech_rate': 1, 'volume': 50}
        supported = {name: options[name] for name in voice['supported_parameters']}
        prepared = normalize_tts_candidate_request(TTSCandidateCommand(expected_revision=0,
            client_request_id='voice-preview', voice=voice['id'], text='Hello', params=supported))
        assert prepared.family == voice['family'] and not prepared.unsupported
