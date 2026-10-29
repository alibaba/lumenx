"""The real text-edit route must change the text the real TTS processor receives."""
import json
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.models import DialogueStructured
from src.apps.comic_gen.prompt_assembly import enrich_prompt_with_dialogue
from src.utils.json_storage import StorageError
from tests.test_dialogue_audio_parameters import line, runtime as audio_runtime

runtime = audio_runtime


def structured_frame(runtime):
    item, _, _ = runtime
    frame = item.scripts['p'].frames[0]
    frame.dialogue = 'old words'
    frame.speaker = 'Hero'
    frame.dialogue_structured = DialogueStructured(
        speaker='Hero', line='old words', emotion='calm', delivery='soft',
    )
    frame.visual_description = 'Hero by the window'
    frame.assembled_prompt = 'outdated assembled prompt'
    frame.dialogue_instructions = 'historical audio instructions'
    item._save_data()
    return item.read_committed_script('p').frames[0]


def edit(runtime, patch):
    return runtime[1].post('/projects/p/frames/update', json={'frame_id': 'f', 'expected_revision': runtime[0].read_committed_script('p').revision, **patch})


def test_http_dialogue_edit_preserves_structured_metadata_and_changes_real_tts_input(runtime):
    item, _, state = runtime
    before = structured_frame(runtime)
    response = edit(runtime, {'dialogue': 'edited words'})
    assert response.status_code == 200, response.text
    saved = item.read_committed_script('p').frames[0]
    # SDK assertion comes first: UI ACK success alone does not prove changed audio.
    line(runtime)
    assert state.calls[-1]['text'] == 'edited words'
    assert saved.dialogue == 'edited words'
    assert saved.dialogue_structured.model_dump() == {
        **before.dialogue_structured.model_dump(), 'line': 'edited words',
    }
    assert saved.speaker == before.speaker
    assert saved.dialogue_instructions == before.dialogue_instructions
    assert saved.assembled_prompt == before.assembled_prompt
    assert 'edited words' in enrich_prompt_with_dialogue(saved.assembled_prompt, saved)
    allowed = {'dialogue', 'dialogue_structured'}
    assert {k: v for k, v in saved.model_dump().items() if k not in allowed} == {
        k: v for k, v in before.model_dump().items() if k not in allowed
    }
    assert response.json()['frames'][0] == saved.model_dump(mode='json')


def test_empty_http_dialogue_clears_both_texts_and_never_synthesizes(runtime):
    item, _, state = runtime
    before = structured_frame(runtime)
    response = edit(runtime, {'dialogue': ''})
    assert response.status_code == 200, response.text
    saved = item.read_committed_script('p').frames[0]
    after_edit = item.read_committed_script('p').model_dump()
    line(runtime)
    assert not state.calls
    assert saved.dialogue == '' and saved.dialogue_structured.line == ''
    assert saved.dialogue_structured.model_dump() == {**before.dialogue_structured.model_dump(), 'line': ''}
    assert saved.assembled_prompt == before.assembled_prompt
    assert enrich_prompt_with_dialogue(saved.assembled_prompt, saved) == saved.assembled_prompt
    assert item.read_committed_script('p').model_dump() == after_edit


def test_text_edit_does_not_rebuild_unrelated_visual_assembly(runtime, monkeypatch):
    item, _, state = runtime
    structured_frame(runtime)
    before = item.read_committed_script('p').frames[0]
    from src.apps.comic_gen import prompt_assembly
    monkeypatch.setattr(prompt_assembly, 'assemble_prompt', Mock(side_effect=RuntimeError('assembly failed')))
    response = edit(runtime, {'dialogue': 'new text'})
    assert response.status_code == 200
    saved = item.read_committed_script('p').frames[0]
    assert saved.dialogue == 'new text' and saved.dialogue_structured.line == 'new text'
    assert saved.assembled_prompt == before.assembled_prompt
    prompt_assembly.assemble_prompt.assert_not_called()
    item.assert_storage_available()
    assert not state.calls


def test_dialogue_save_failure_rolls_back_both_texts_and_restart(runtime, monkeypatch):
    item, _, state = runtime
    structured_frame(runtime)
    before = item.read_committed_script('p').model_dump(mode='json')
    disk = (item.data_dir / 'projects.json').read_bytes()
    monkeypatch.setattr(item._storage, 'commit', Mock(side_effect=StorageError('offline disk failure')))
    response = edit(runtime, {'dialogue': 'not committed'})
    assert response.status_code == 503
    assert item.scripts['p'].model_dump(mode='json') == before
    assert (item.data_dir / 'projects.json').read_bytes() == disk
    assert not state.calls
    item.close()
    restored = module.ComicGenPipeline(data_dir=item.data_dir, script_processor=Mock())
    try:
        assert restored.read_committed_script('p').model_dump(mode='json') == before
    finally:
        restored.close()


def test_dialogue_edit_survives_restart_without_changing_other_frame_fields(runtime):
    item, _, _ = runtime
    structured_frame(runtime)
    response = edit(runtime, {'dialogue': 'persisted words'})
    assert response.status_code == 200
    saved = item.read_committed_script('p').model_dump(mode='json')
    assert saved['frames'][0]['dialogue_structured']['line'] == 'persisted words'
    item.close()
    restored = module.ComicGenPipeline(data_dir=item.data_dir, script_processor=Mock())
    try:
        assert restored.read_committed_script('p').model_dump(mode='json') == saved
    finally:
        restored.close()


@pytest.mark.parametrize('patch', [{}, {'dialogue': None}, {'duration': 7}])
def test_omitted_or_null_dialogue_and_other_edits_preserve_text_contract(runtime, patch):
    item, _, state = runtime
    before = structured_frame(runtime)
    response = edit(runtime, patch)
    assert response.status_code == 200
    saved = item.read_committed_script('p').frames[0]
    assert saved.dialogue == before.dialogue
    assert saved.dialogue_structured == before.dialogue_structured
    assert saved.assembled_prompt == before.assembled_prompt
    assert saved.duration == (7 if 'duration' in patch else before.duration)
    assert not state.calls


@pytest.mark.parametrize('value', [[], {}])
def test_invalid_dialogue_request_is_rejected_without_changing_either_text(runtime, value):
    item, _, state = runtime
    structured_frame(runtime)
    before = (item.data_dir / 'projects.json').read_bytes()
    response = edit(runtime, {'dialogue': value, 'duration': 9})
    assert response.status_code == 422
    assert item.scripts['p'].model_dump(mode='json') == json.loads(before)['p']
    assert (item.data_dir / 'projects.json').read_bytes() == before
    assert not state.calls


def test_legacy_only_frame_edits_do_not_invent_structured_metadata(runtime):
    item, _, state = runtime
    response = edit(runtime, {'dialogue': 'legacy edit'})
    assert response.status_code == 200
    saved = item.read_committed_script('p').frames[0]
    assert saved.dialogue_structured is None
    line(runtime)
    assert state.calls[-1]['text'] == 'legacy edit'
