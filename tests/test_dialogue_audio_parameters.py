"""A2b commands use actual parameter preparation; only SDK I/O is replaced."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import dashscope
import pytest
from dashscope.audio.tts_v2 import SpeechSynthesizer
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Character, CustomVoice, DialogueStructured, Script, Series, StoryboardFrame
from src.utils.json_storage import CorruptStorageError, StorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ('AssetGenerator', 'StoryboardGenerator', 'VideoGenerator', 'ExportManager'):
        monkeypatch.setattr(module, name, Mock())
    monkeypatch.setattr(dashscope, 'api_key', 'ambient-sdk-key')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'offline-key')
    state = SimpleNamespace(calls=[], hook=None)

    def record(parameters):
        state.calls.append(parameters)
        if state.hook:
            state.hook()
        return f'offline-audio-{len(state.calls)}'.encode()

    def cosy(synthesizer, text):
        payload = json.loads(synthesizer.request.get_start_request())['payload']
        return record({'text': text, 'model': payload['model'], **payload['parameters']})

    def qwen(**kwargs):
        record(kwargs)
        return SimpleNamespace(output=SimpleNamespace(audio=SimpleNamespace(url='https://offline.invalid/audio')),
                               request_id='offline-request')

    monkeypatch.setattr(SpeechSynthesizer, 'call', cosy)
    monkeypatch.setattr(dashscope.MultiModalConversation, 'call', qwen)
    monkeypatch.setattr('requests.get', lambda *_args, **_kw: SimpleNamespace(
        content=b'offline-qwen-audio', raise_for_status=lambda: None,
    ))
    item = module.ComicGenPipeline(data_dir=tmp_path / 'data', script_processor=Mock())
    item.scripts['p'] = Script(id='p', title='project', original_text='source', series_id='s',
        created_at=1, updated_at=1,
        characters=[Character(id='c', name='Hero', description='character', voice_id='longanyang',
                              voice_speed=1.3, voice_pitch=0.8, voice_volume=37)],
        frames=[StoryboardFrame(id='f', scene_id='', character_ids=['c'], action_description='speaks', dialogue='hello')])
    item.series_store['s'] = Series(id='s', title='series', episode_ids=['p'], created_at=1, updated_at=1)
    item._commit_storage('projects.json', 'series.json', 'library_assets.json')
    with TestClient(api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)) as client:
        yield item, client, state
    item.close()


def line(runtime, options=None):
    item, client, _ = runtime
    response = client.post('/projects/p/frames/f/audio', json=options or {})
    assert response.status_code == 200, response.text
    return item.read_committed_script('p').frames[0]


def batch(runtime):
    response = runtime[1].post('/projects/p/dialogue_audio/batch')
    assert response.status_code == 200, response.text
    return response.json()


def move_speaker(item, source):
    if source != 'episode':
        speaker = item.scripts['p'].characters.pop()
        (item.series_store['s'] if source == 'series' else item.library_store).characters.append(speaker)
        item._commit_storage('projects.json', 'series.json', 'library_assets.json')


@pytest.mark.parametrize('source', ['episode', 'series', 'global'])
def test_http_empty_body_inherits_bound_numbers(runtime, source):
    item, _, state = runtime
    move_speaker(item, source)
    frame = line(runtime)
    assert (state.calls[-1]['rate'], state.calls[-1]['pitch'], state.calls[-1]['volume']) == (1.3, 0.8, 37)
    snapshot = frame.dialogue_audio_snapshot.model_dump()
    assert snapshot['prepared']['parameters']['speech_rate'] == 1.3
    assert snapshot['sources']['speed'] == 'character_binding'
    assert snapshot['audio_url'] == frame.audio_url


def test_overrides_persist_omission_preserves_and_null_resets(runtime):
    item, _, state = runtime
    first = line(runtime, {'speed': 1.7, 'volume': 0, 'instructions': ''})
    assert first.dialogue_audio_options.model_dump() == {'speed': 1.7, 'pitch': None, 'volume': 0, 'instructions': ''}
    line(runtime)
    assert (state.calls[-1]['rate'], state.calls[-1]['volume']) == (1.7, 0)
    reset = line(runtime, {'speed': None})
    assert reset.dialogue_audio_options.speed is None
    assert (state.calls[-1]['rate'], state.calls[-1]['volume']) == (1.3, 0)
    assert len(state.calls) == 3, 'explicit single regeneration remains intentional'


@pytest.mark.parametrize('field,value', [('speed', 1.6), ('pitch', 1.1), ('volume', 65)])
def test_bound_cosy_parameter_changes_regenerate(runtime, field, value):
    item, _, state = runtime
    assert batch(runtime)['_batch_stats']['generated'] == 1
    item.update_voice_params('p', 'c', **{'speed': 1.3, 'pitch': 0.8, 'volume': 37, field: value})
    assert batch(runtime)['_batch_stats']['generated'] == 1
    assert len(state.calls) == 2


@pytest.mark.parametrize('voice,override,before,after', [
    ('longanyang', 1.7, 1.3, 1.9), ('longanyang', None, 2.2, 2.8),
    ('Cherry', None, 1.3, 1.9),
])
def test_equal_actual_parameters_do_not_charge_again(runtime, voice, override, before, after):
    item, _, state = runtime
    item.scripts['p'].characters[0].voice_id = voice
    item.scripts['p'].characters[0].voice_speed = before
    item._save_data()
    line(runtime, {'speed': override} if override is not None else {})
    item.update_voice_params('p', 'c', speed=after, pitch=0.8, volume=37)
    result = batch(runtime)
    assert result['_batch_stats']['skipped'] == 1
    assert len(state.calls) == 1


@pytest.mark.parametrize('field', ['speed', 'pitch', 'volume'])
def test_explicit_unsupported_number_rejects_without_save_or_sdk(runtime, field):
    item, client, state = runtime
    item.scripts['p'].characters[0].voice_id = 'Cherry'
    item._save_data()
    before = item.read_committed_script('p').model_dump()
    response = client.post('/projects/p/frames/f/audio', json={field: 1})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'unsupported_audio_parameters'
    assert response.json()['detail']['fields'] == [field]
    assert not state.calls
    assert item.read_committed_script('p').model_dump() == before


def test_saved_override_becomes_unsupported_after_voice_change_without_blocking_other_frames(runtime):
    item, _, state = runtime
    first = line(runtime, {'speed': 1.7})
    item.scripts['p'].characters[0].voice_id = 'Cherry'
    item.scripts['p'].frames.append(StoryboardFrame(id='f2', scene_id='', character_ids=['c'], dialogue='second'))
    item._save_data()
    result = batch(runtime)
    assert result['_batch_stats'] == {'generated': 1, 'skipped': 0, 'failed': 1, 'no_voice': 0}
    assert result['_batch_errors'][0]['frame_id'] == 'f'
    assert result['_batch_errors'][0]['code'] == 'unsupported_audio_parameters'
    assert len(state.calls) == 2
    assert item.read_committed_script('p').frames[0].audio_url == first.audio_url
    line(runtime, {'speed': None})
    assert len(state.calls) == 3


@pytest.mark.parametrize('value', ['', 'angry'])
def test_real_refine_emotion_change_uses_current_structured_fields(runtime, value):
    item, _, state = runtime
    frame = item.scripts['p'].frames[0]
    frame.dialogue_structured = DialogueStructured(speaker='Hero', line='hello', emotion='calm', delivery='soft')
    item._save_data()
    first = line(runtime)
    item.script_processor.refine_frame_to_rich.return_value = {'dialogue_structured': {
        'speaker': 'Hero', 'line': 'hello', 'emotion': value, 'delivery': '',
    }}
    item.refine_frame('p', 'f')
    assert batch(runtime)['_batch_stats']['generated'] == 1
    assert state.calls[-1].get('instruction') == (f'情绪：{value}' if value else None)
    assert first.dialogue_audio_snapshot.prepared['parameters']['instruction'] == '情绪：calm；演绎：soft'


@pytest.mark.parametrize('instructions', ['', 'explicit'])
def test_explicit_instructions_mask_structured_until_reset(runtime, instructions):
    item, _, state = runtime
    frame = item.scripts['p'].frames[0]
    frame.dialogue_structured = DialogueStructured(speaker='Hero', line='hello', emotion='calm')
    item._save_data()
    line(runtime, {'instructions': instructions})
    frame.dialogue_structured.emotion = 'angry'
    item._save_data()
    assert batch(runtime)['_batch_stats']['skipped'] == 1
    line(runtime, {'instructions': None})
    assert state.calls[-1]['instruction'] == '情绪：angry'


def test_failure_saves_desired_options_but_preserves_last_success_snapshot(runtime):
    item, _, state = runtime
    first = line(runtime)
    old_bytes = (item.data_dir / first.audio_url).read_bytes()
    state.hook = lambda: (_ for _ in ()).throw(RuntimeError('offline failure'))
    failed = line(runtime, {'speed': 1.8})
    assert failed.audio_error
    assert failed.dialogue_audio_options.speed == 1.8
    assert failed.dialogue_audio_snapshot == first.dialogue_audio_snapshot
    assert failed.audio_url == first.audio_url
    assert (item.data_dir / first.audio_url).read_bytes() == old_bytes
    state.hook = None
    assert batch(runtime)['_batch_stats']['generated'] == 1
    assert state.calls[-1]['rate'] == 1.8


@pytest.mark.parametrize('kind', ['model', 'family', 'label'])
def test_custom_voice_metadata_uses_actual_identity(runtime, kind):
    item, _, state = runtime
    item.scripts['p'].characters[0].voice_id = 'custom-voice'
    voice = CustomVoice(id='custom-voice', label='custom', origin='clone',
                        target_model='cosyvoice-v3.5-plus', family='cosyvoice')
    item.series_store['s'].custom_voices.append(voice)
    item._commit_storage('projects.json', 'series.json')
    line(runtime)
    if kind == 'model':
        voice.target_model = 'cosyvoice-v3-flash'
    elif kind == 'family':
        voice.family = 'qwen3'
        voice.target_model = 'qwen3-tts-flash'
    else:
        voice.label = 'new label'
    item._save_series_data()
    result = batch(runtime)
    assert result['_batch_stats']['generated'] == (kind != 'label')
    assert len(state.calls) == (1 if kind == 'label' else 2)


def test_legacy_hash_and_instruction_are_unknown_not_desired(runtime):
    item, _, state = runtime
    frame = item.scripts['p'].frames[0]
    frame.audio_url = 'audio/legacy.mp3'
    frame.dialogue_instructions = 'ambiguous history'
    frame.dialogue_text_hash = 'old-hash'
    item._save_data()
    assert batch(runtime)['_batch_stats']['generated'] == 1
    saved = item.read_committed_script('p').frames[0]
    assert saved.dialogue_instructions == 'ambiguous history'
    assert state.calls[-1].get('instruction') is None
    assert saved.dialogue_audio_snapshot


@pytest.mark.parametrize('patch', [
    {'speed': True}, {'pitch': '1'}, {'volume': 2.5}, {'unknown': 1},
    {'speed': 'NaN'}, {'instructions': '\ud800'}, {'instructions': []},
])
def test_invalid_request_never_pays_or_changes_revision(runtime, patch):
    item, client, state = runtime
    before = item.read_committed_script('p').model_dump()
    response = client.post('/projects/p/frames/f/audio', content=json.dumps(patch),
                           headers={'Content-Type': 'application/json'})
    assert response.status_code == 422, response.text
    assert not state.calls
    assert item.read_committed_script('p').model_dump() == before


@pytest.mark.parametrize('change', ['closed', 'conflict', 'storage'])
def test_failed_commit_does_not_publish_new_options_or_snapshot(runtime, monkeypatch, change):
    item, _, state = runtime
    first = line(runtime)
    if change == 'closed':
        state.hook = item.close
    elif change == 'conflict':
        state.hook = lambda: item.update_frame('p', 'f', expected_revision=item.read_committed_script("p").revision, dialogue='edited')
    else:
        monkeypatch.setattr(item._storage, 'commit', Mock(side_effect=StorageError('offline disk failure')))
    with pytest.raises(StorageError):
        item.generate_dialogue_line('p', 'f', speed=1.8)
    disk = json.loads((item.data_dir / 'projects.json').read_text())['p']['frames'][0]
    assert disk['dialogue_audio_options'] == first.dialogue_audio_options.model_dump()
    assert disk['dialogue_audio_snapshot'] == first.dialogue_audio_snapshot.model_dump()


def test_closed_runtime_does_not_prepare_or_pay(runtime, monkeypatch):
    item, _, state = runtime
    item.close()
    monkeypatch.setattr(item.audio_generator.tts, 'prepare', Mock(side_effect=AssertionError('must not prepare')))
    with pytest.raises(StorageError):
        item.generate_dialogue_line('p', 'f')
    assert not state.calls


@pytest.mark.parametrize('change', ['schema_version', 'rules_version', 'audio_url'])
def test_unknown_or_unrelated_success_snapshot_is_stale(runtime, change):
    item, _, state = runtime
    line(runtime)
    snapshot = item.scripts['p'].frames[0].dialogue_audio_snapshot
    if change == 'schema_version':
        snapshot.schema_version = 20
    elif change == 'rules_version':
        snapshot.prepared['rules_version'] = 'future-rules'
    else:
        snapshot.audio_url = 'audio/unrelated.mp3'
    item._save_data()
    assert batch(runtime)['_batch_stats']['generated'] == 1
    assert len(state.calls) == 2


def test_audio_options_and_snapshot_survive_restart(runtime):
    item, _, state = runtime
    before = line(runtime, {'volume': 0, 'instructions': ''}).model_dump()
    item.close()
    restored = module.ComicGenPipeline(data_dir=item.data_dir, script_processor=Mock())
    try:
        assert restored.read_committed_script('p').frames[0].model_dump() == before
        _, stats, errors = restored.generate_dialogue_audio_batch('p')
        assert stats['skipped'] == 1 and not errors
        assert len(state.calls) == 1
    finally:
        restored.close()


@pytest.mark.parametrize('voice', ['longxiaochun_v2', 'Jennifer'])
def test_explicit_unsupported_instructions_reject_but_empty_can_disable(runtime, voice):
    item, client, state = runtime
    item.scripts['p'].characters[0].voice_id = voice
    item._save_data()
    before = item.read_committed_script('p').model_dump()
    response = client.post('/projects/p/frames/f/audio', json={'instructions': 'angry'})
    assert response.status_code == 422
    assert response.json()['detail']['fields'] == ['instructions']
    assert not state.calls and item.read_committed_script('p').model_dump() == before
    assert line(runtime, {'instructions': ''}).audio_url


def test_inherited_unsupported_instructions_change_does_not_pay_again(runtime):
    item, _, state = runtime
    item.scripts['p'].characters[0].voice_id = 'Jennifer'
    item.scripts['p'].frames[0].dialogue_structured = DialogueStructured(speaker='Hero', line='hello', emotion='calm')
    item._save_data()
    first = line(runtime)
    assert first.dialogue_audio_snapshot.prepared['unsupported']
    item.scripts['p'].frames[0].dialogue_structured.emotion = 'angry'
    item._save_data()
    assert batch(runtime)['_batch_stats']['skipped'] == 1
    assert len(state.calls) == 1


@pytest.mark.parametrize('voice,override,before,after', [
    ('longanyang', 1.7, 1.3, 1.9), ('longanyang', None, 2.2, 2.8),
    ('Cherry', None, 1.3, 1.9),
])
def test_actual_equal_shared_edit_during_tts_does_not_conflict(runtime, voice, override, before, after):
    item, _, state = runtime
    move_speaker(item, 'series')
    character = item.series_store['s'].characters[0]
    character.voice_id = voice
    character.voice_speed = before
    item._save_series_data()
    state.hook = lambda: item.update_voice_params('p', 'c', speed=after, pitch=0.8, volume=37)
    assert line(runtime, {'speed': override} if override is not None else {}).audio_url
    assert len(state.calls) == 1


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, '1.2'])
def test_invalid_internal_override_is_rejected_before_sdk(runtime, value):
    item, _, state = runtime
    before = item.read_committed_script('p').model_dump()
    with pytest.raises(module.ProjectContextError):
        item.generate_dialogue_line('p', 'f', speed=value)
    item.assert_storage_available()
    assert not state.calls and item.read_committed_script('p').model_dump() == before


def test_missing_tts_saves_desired_failure_without_replacing_success(runtime):
    item, _, state = runtime
    first = line(runtime)
    item.audio_generator.tts = None
    failed = line(runtime, {'speed': 1.6})
    assert failed.audio_error and failed.dialogue_audio_options.speed == 1.6
    assert failed.dialogue_audio_snapshot == first.dialogue_audio_snapshot
    assert failed.audio_url == first.audio_url and len(state.calls) == 1


def test_future_opaque_snapshot_reload_is_readable_unchanged_and_stale(runtime):
    item, _, state = runtime
    item.close()
    path = item.data_dir / 'projects.json'
    stored = json.loads(path.read_text())
    future = {'schema_version': 20, 'future_payload': {'voice_ref': ['future', 7], 'proof': False}}
    stored['p']['frames'][0]['audio_url'] = 'audio/previous.mp3'
    stored['p']['frames'][0]['dialogue_audio_snapshot'] = future
    raw = json.dumps(stored, indent=3)
    path.write_text(raw)
    restored = module.ComicGenPipeline(data_dir=item.data_dir, script_processor=Mock())
    try:
        frame = restored.read_committed_script('p').frames[0]
        assert frame.model_dump()['dialogue_audio_snapshot'] == future
        assert path.read_text() == raw, 'load must not rewrite or migrate unknown data'
        assert not state.calls
        restored.update_frame('p', 'f', expected_revision=restored.read_committed_script("p").revision, action_description='unrelated edit')
        assert json.loads(path.read_text())['p']['frames'][0]['dialogue_audio_snapshot'] == future
        _, stats, errors = restored.generate_dialogue_audio_batch('p')
        assert stats['generated'] == 1 and not errors and len(state.calls) == 1
        assert restored.read_committed_script('p').frames[0].dialogue_audio_snapshot.schema_version == 1
    finally:
        restored.close()


@pytest.mark.parametrize('version', [1, True, '20'])
def test_malformed_known_or_invalid_snapshot_version_still_rejects_reload(runtime, version):
    item, _, state = runtime
    item.close()
    path = item.data_dir / 'projects.json'
    stored = json.loads(path.read_text())
    stored['p']['frames'][0]['dialogue_audio_snapshot'] = {'schema_version': version, 'future_payload': {}}
    raw = json.dumps(stored)
    path.write_text(raw)
    with pytest.raises(CorruptStorageError):
        module.ComicGenPipeline(data_dir=item.data_dir, script_processor=Mock())
    assert path.read_text() == raw and not state.calls


def test_invalid_generator_success_envelope_is_safe_502_without_publishing(runtime, monkeypatch):
    item, client, state = runtime
    first = line(runtime)
    before = item.read_committed_script('p').model_dump()
    # Real AudioGenerator and real TTS preparation/SDK serialization still run.
    # Only the final generator output envelope is corrupted after file output.
    monkeypatch.setattr(item.audio_generator, '_rel_output_path', lambda _path: 17)
    response = client.post('/projects/p/frames/f/audio', json={'speed': 1.8})
    assert response.status_code == 502
    assert response.json()['detail'] == 'Audio generation returned invalid data'
    item.assert_storage_available()
    assert item.read_committed_script('p').model_dump() == before
    assert (item.data_dir / first.audio_url).exists()
    assert len(list((item.data_dir / 'audio' / 'dialogue').glob('*.mp3'))) == 2
    assert len(state.calls) == 2
