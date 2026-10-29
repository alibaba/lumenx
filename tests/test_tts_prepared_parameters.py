"""Prepared inputs must describe actual SDK calls; no real TTS or downloads."""
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import dashscope
import pytest
from dashscope.audio.tts_v2 import SpeechSynthesizer

from src.audio.tts import TTSProcessor, VOICES


@pytest.fixture
def transport(monkeypatch):
    calls = SimpleNamespace(cosy=[], qwen=[], downloads=[])
    monkeypatch.setattr(dashscope, 'api_key', 'synthetic-secret-key')

    def cosy(synthesizer, text):
        payload = json.loads(synthesizer.request.get_start_request())['payload']
        calls.cosy.append((text, payload))
        return b'offline cosy audio'

    def qwen(**kwargs):
        calls.qwen.append(kwargs)
        return SimpleNamespace(output=SimpleNamespace(audio=SimpleNamespace(
            url='https://offline.invalid/audio?signature=synthetic-url-secret',
        )), request_id='qwen-request')

    def download(url, **kwargs):
        calls.downloads.append((url, kwargs))
        return SimpleNamespace(content=b'offline qwen audio', raise_for_status=lambda: None)

    monkeypatch.setattr(SpeechSynthesizer, 'call', cosy)
    monkeypatch.setattr(dashscope.MultiModalConversation, 'call', qwen)
    monkeypatch.setattr('requests.get', download)
    return calls


@pytest.fixture
def tts(transport):
    return TTSProcessor(api_key='synthetic-secret-key')


def unsupported_fields(prepared):
    return {notice.field: notice.reason for notice in prepared.unsupported}


def test_cosy_prepared_matches_real_sdk_serialization(tts, transport, tmp_path):
    prepared = tts.prepare('你好', voice='longanyang', speech_rate=2.8, pitch_rate=0.2,
                           volume=0, instructions='温柔地说')
    assert (prepared.family, prepared.model, prepared.voice, prepared.text) == (
        'cosyvoice', 'cosyvoice-v3-flash', 'longanyang', '你好',
    )
    assert {notice.field: (notice.requested, notice.effective, notice.reason)
            for notice in prepared.adjustments} == {
        'speech_rate': (2.8, 2.0, 'clamped'), 'pitch_rate': (0.2, 0.5, 'clamped'),
    }
    assert not prepared.unsupported
    result = tts.execute(prepared, str(tmp_path / 'cosy.mp3'))
    text, payload = transport.cosy[0]
    kwargs = prepared.sdk_kwargs()
    assert payload['model'] == kwargs['model']
    assert payload['parameters']['voice'] == kwargs['voice']
    assert text == kwargs['text']
    for sdk_name, wire_name in [('speech_rate', 'rate'), ('pitch_rate', 'pitch'),
                               ('volume', 'volume'), ('instruction', 'instruction')]:
        assert payload['parameters'][wire_name] == kwargs[sdk_name]
    assert 'instructions' not in payload['parameters']
    assert Path(result[0]).read_bytes() == b'offline cosy audio'
    assert len(result) == 3


@pytest.mark.parametrize('value,expected', [(-1, 0), (0, 0), (100, 100), (101, 100)])
def test_cosy_volume_clamp_retains_zero(tts, value, expected):
    prepared = tts.prepare('hello', volume=value)
    assert prepared.sdk_kwargs()['volume'] == expected
    assert bool(prepared.adjustments) == (value != expected)


@pytest.mark.parametrize('voice,model,expected', [
    ('longanyang', 'cosyvoice-v2', False),
    ('longxiaochun_v2', 'cosyvoice-v3-flash', False),
    ('longanyang', 'cosyvoice-v3.5-plus', False),
    ('custom-clone', 'cosyvoice-v3.5-plus', True),
    ('custom-clone', 'cosyvoice-v3-plus', False),
])
def test_cosy_instruction_uses_selected_model_and_voice(tts, voice, model, expected):
    prepared = tts.prepare('hello', voice=voice, model_override=model, instructions='soft')
    assert prepared.model == model
    assert ('instruction' in prepared.sdk_kwargs()) is expected
    assert ('instructions' in unsupported_fields(prepared)) is not expected


@pytest.mark.parametrize('instructions', [None, ''])
def test_empty_instruction_is_not_an_unsupported_request(tts, instructions):
    prepared = tts.prepare('hello', voice='longxiaochun_v2', instructions=instructions)
    assert 'instruction' not in prepared.sdk_kwargs()
    assert not prepared.unsupported


def test_qwen_prepared_exactly_matches_nonsecret_call(tts, transport, tmp_path):
    prepared = tts.prepare('hello', voice='Cherry', instructions='soft', speech_rate=1.3,
                           pitch_rate=0.8, volume=0)
    assert prepared.model == 'qwen3-tts-instruct-flash'
    assert [(item.field, item.reason) for item in prepared.adjustments] == [('model', 'instruction_model')]
    assert unsupported_fields(prepared) == dict.fromkeys(
        ['speech_rate', 'pitch_rate', 'volume'], 'transport_not_supported',
    )
    assert {item.field: item.requested for item in prepared.unsupported} == {
        'speech_rate': 1.3, 'pitch_rate': 0.8, 'volume': 0,
    }
    output = tmp_path / 'qwen.mp3'
    result = tts.execute(prepared, str(output))
    assert result == (str(output), 0.0, 'qwen-request')
    sent = transport.qwen[0]
    assert sent.pop('api_key') == 'synthetic-secret-key'
    assert sent == prepared.sdk_kwargs() == {
        'model': 'qwen3-tts-instruct-flash', 'voice': 'Cherry', 'text': 'hello',
        'language_type': 'Chinese', 'stream': False,
        'instructions': 'soft', 'optimize_instructions': True,
    }
    assert output.read_bytes() == b'offline qwen audio'
    assert len(transport.downloads) == 1


@pytest.mark.parametrize('voice,override,expected_model,with_instruction', [
    ('Cherry', 'qwen3-tts-flash', 'qwen3-tts-flash', False),
    ('Cherry', 'qwen3-tts-instruct-flash', 'qwen3-tts-instruct-flash', True),
    ('Jennifer', None, 'qwen3-tts-flash', False),
    ('Jennifer', 'qwen3-tts-instruct-flash', 'qwen3-tts-instruct-flash', False),
    ('custom-qwen', 'qwen3-tts-instruct-flash', 'qwen3-tts-instruct-flash', False),
])
def test_qwen_explicit_model_and_voice_instruction_capability(
    tts, transport, tmp_path, voice, override, expected_model, with_instruction,
):
    prepared = tts.prepare('hello', voice=voice, model_override=override,
                           family_override='qwen3', instructions='soft')
    assert prepared.model == expected_model
    assert ('instructions' in prepared.sdk_kwargs()) is with_instruction
    assert ('instructions' in unsupported_fields(prepared)) is not with_instruction
    tts.execute(prepared, str(tmp_path / 'qwen.mp3'))
    assert transport.qwen[0]['model'] == expected_model
    assert ('instructions' in transport.qwen[0]) is with_instruction


@pytest.mark.parametrize('voice,language', [('Cherry', 'Chinese'), ('Ono Anna', 'Japanese'),
                                           ('Bodega', 'Spanish'), ('Emilien', 'French')])
def test_qwen_keeps_existing_language_hint(tts, voice, language):
    prepared = tts.prepare('hello', voice=voice)
    assert prepared.sdk_kwargs()['language_type'] == language


@pytest.mark.parametrize('voice', ['longanyang', 'Cherry'])
def test_prepared_detaches_execution_from_processor_and_registry(tts, transport, tmp_path, monkeypatch, voice):
    prepared = tts.prepare('original text', voice=voice, instructions='soft', volume=0)
    original = prepared.to_snapshot()
    tts.model = 'unrelated-model'
    tts.voice = 'unrelated-voice'
    monkeypatch.setitem(VOICES[voice], 'model', 'mutated-model')
    monkeypatch.setitem(VOICES[voice], 'family', 'mutated-family')
    monkeypatch.setitem(VOICES[voice], 'supports_instruction', False)
    monkeypatch.setitem(VOICES[voice], 'lang_primary', 'ja')
    monkeypatch.setattr(tts, '_voice_meta', lambda *_: pytest.fail('execute read live registry'))
    kwargs = prepared.sdk_kwargs()
    kwargs['model'] = 'modified-copy'
    snapshot = prepared.to_snapshot()
    snapshot['parameters']['voice'] = 'modified-copy'
    snapshot['unsupported'].clear()
    snapshot['adjustments'].clear()
    with pytest.raises(FrozenInstanceError):
        prepared.family = 'qwen3'
    with pytest.raises(TypeError):
        prepared.parameters[0] = ('voice', 'modified')
    tts.execute(prepared, str(tmp_path / 'audio.mp3'))
    assert prepared.to_snapshot() == original
    assert prepared.voice == voice
    if voice == 'Cherry':
        assert transport.qwen[0]['model'] == 'qwen3-tts-instruct-flash'
        assert transport.qwen[0]['language_type'] == 'Chinese'
    else:
        assert transport.cosy[0][1]['model'] == 'cosyvoice-v3-flash'
        assert transport.cosy[0][1]['parameters']['instruction'] == 'soft'
    stored = json.dumps(prepared.to_snapshot(), ensure_ascii=False, allow_nan=False)
    assert 'synthetic-secret-key' not in stored
    assert 'synthetic-url-secret' not in stored
    assert 'api_key' not in stored
    assert prepared.rules_version


@pytest.mark.parametrize('kwargs', [
    {'speech_rate': float('nan')}, {'pitch_rate': float('inf')}, {'volume': float('-inf')},
    {'speech_rate': True}, {'pitch_rate': '1'}, {'volume': None},
    {'text': '\ud800'}, {'voice': '\ud800'}, {'instructions': '\ud800'},
    {'model_override': '\ud800'}, {'family_override': '\ud800'},
    {'family_override': 'unknown'}, {'family_override': ''},
    {'model_override': ''}, {'voice': ''}, {'text': None}, {'instructions': []},
])
def test_invalid_inputs_reject_before_sdk_or_output(tts, transport, tmp_path, kwargs):
    arguments = {'text': 'hello', **kwargs}
    with pytest.raises(ValueError):
        tts.synthesize(output_path=str(tmp_path / 'must-not-exist.mp3'), **arguments)
    assert not transport.cosy and not transport.qwen and not transport.downloads
    assert list(tmp_path.iterdir()) == []


def test_legacy_synthesize_delegates_to_the_exact_prepared_value(tts, transport, tmp_path, monkeypatch):
    execute = tts.execute
    seen = []

    def capture(prepared, path):
        seen.append(prepared)
        return execute(prepared, path)

    monkeypatch.setattr(tts, 'execute', capture)
    output = tmp_path / 'legacy.mp3'
    result = tts.synthesize('hello', str(output), voice='Cherry', model_override='qwen3-tts-flash',
                            instructions='soft', volume=0)
    assert result[0] == str(output) and len(seen) == 1
    assert transport.qwen[0]['model'] == seen[0].model == 'qwen3-tts-flash'
    assert 'instructions' in unsupported_fields(seen[0])


def test_existing_synthesize_does_not_replace_an_explicit_qwen_model(tts, transport, tmp_path):
    tts.synthesize('hello', str(tmp_path / 'explicit.mp3'), voice='Cherry',
                   model_override='qwen3-tts-flash', instructions='soft')
    assert transport.qwen[0]['model'] == 'qwen3-tts-flash'
    assert 'instructions' not in transport.qwen[0]
