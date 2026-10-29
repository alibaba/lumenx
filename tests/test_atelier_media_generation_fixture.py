"""Preserve historical public wire without re-admitting its old Wan2.7 request.

These sorted frontend goldens are wire examples, not restorable storage backups.
Real untagged storage restore/execute/replay uses the unchanged captured fixture
in test_atelier_wan27_t2i_contract; new strict wire is generated separately there.
"""
import json
from pathlib import Path

import pytest

from src.apps.atelier.generation_commands import ManualImageCandidateBatchCommand
from src.apps.atelier.media_generation_admission import MediaGenerationAdmissionAck
from src.apps.atelier.media_generation_execution import MediaGenerationAttemptAck
from src.apps.atelier.tts_commands import TTSCandidateCommand

FIXTURE_DIR = Path(__file__).resolve().parents[1] / 'frontend/src/__tests__/fixtures'


@pytest.mark.parametrize('name', ['image', 'audio', 'completed', 'unknown', 'image-replayed'])
def test_historical_media_generation_wire_still_roundtrips_backend_ack(name):
    payload = json.loads((FIXTURE_DIR / f'atelier-media-generation-{name}.json').read_text())
    ack_type = MediaGenerationAttemptAck if name in ('completed', 'unknown') else MediaGenerationAdmissionAck
    ack = ack_type.model_validate(payload)
    assert ack.model_dump(mode='json') == payload
    if name == 'completed':
        assert payload['attempt']['status'] == 'completed'
    if name == 'unknown':
        assert payload['attempt']['status'] == 'needs_confirmation'
    if name == 'image-replayed':
        assert payload['new_candidate_ids'] == []
    image_records = [attempt.provider_execution for attempt in ack.project.generation_attempts
                     if getattr(attempt, 'media_kind', None) == 'image']
    assert image_records
    assert all('intent_contract_version' not in record.intent for record in image_records)


def test_historical_media_request_wire_retains_original_explicit_parameters():
    requests = json.loads((FIXTURE_DIR / 'atelier-media-generation-requests.json').read_text())
    image = ManualImageCandidateBatchCommand.model_validate(requests['image'])
    audio = TTSCandidateCommand.model_validate(requests['audio'])
    assert image.model_dump(mode='json', exclude_unset=True) == requests['image']
    assert audio.model_dump(mode='json', exclude_unset=True) == requests['audio']
    assert image.params == {'seed': 0, 'prompt_extend': False, 'watermark': False}
