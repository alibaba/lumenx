"""Internal image/audio media generation admission uses the shared Atelier ledger."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from src.apps.atelier.generation_commands import ManualImageCandidateBatchCommand
from src.apps.atelier.media_generation_admission import AtelierMediaGenerationAdmissionService
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.apps.atelier.tts_commands import TTSCandidateCommand
from src.models.provider_binding import SOURCE_KEY
from src.models.provider_media_runtime import MediaRuntimeAuthority
from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.utils.json_storage import StorageConflictError, StorageError
from tests.frozen_image_fixtures import rgb_png_bytes

SECRET = "sk-test-" + "d" * 44
SCOPE = ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing")


def image_command(**changes):
    payload = {
        "expected_revision": 0,
        "client_request_id": "image-request",
        "model_id": "wan/wan2.7-image-pro#image",
        "mode": "i2i",
        "prompt": "A lunar garden",
        "media": [{"role": "reference_image", "ordinal": 0, "node_id": "ref", "media_url": "uploads/ref.png"}],
        "params": {"seed": 0, "prompt_extend": False, "watermark": False},
        "batch_size": 3,
    }
    payload.update(changes)
    return ManualImageCandidateBatchCommand(**payload)


def tts_command(**changes):
    payload = {
        "expected_revision": 0,
        "client_request_id": "tts-request",
        "voice": "longanyang",
        "text": "旁白从废墟中升起。",
        "params": {"volume": 0, "instructions": "低声"},
    }
    payload.update(changes)
    return TTSCandidateCommand(**payload)


class AuthorityProvider:
    def __init__(self):
        self.calls = []
        self.fail = None

    @contextmanager
    def __call__(self, *, media_kind, provider_model_id, preview):
        if self.fail is not None:
            raise self.fail
        self.calls.append((media_kind, provider_model_id, preview.preview_fingerprint))
        credential = ResolvedCredential(SCOPE, f"credential-{len(self.calls)}", SECRET)
        yield MediaRuntimeAuthority(
            media_kind=media_kind,
            provider_model_id=provider_model_id,
            scope=SCOPE,
            credential_ref=credential.credential_ref,
            source_key=SOURCE_KEY,
            credential=credential,
        )


@pytest.fixture
def app(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(AtelierProject(
        id="p",
        title="Canvas",
        created_at=1.0,
        updated_at=1.0,
        nodes=[
            AtelierNode(id="image-target", project_id="p", type="image", data={"reference_node_ids": ["ref"]}),
            AtelierNode(id="ref", project_id="p", type="image", media_urls=["uploads/ref.png"]),
            AtelierNode(id="audio-target", project_id="p", type="audio"),
        ],
    ))
    ref = tmp_path / "uploads" / "ref.png"
    ref.parent.mkdir(parents=True)
    ref.write_bytes(rgb_png_bytes("admission-ref"))
    ids = deque([
        "attempt-1", "candidate-1",
        "attempt-2", "candidate-2",
        "attempt-3", "candidate-3",
        "attempt-4", "candidate-4",
        "attempt-5", "candidate-5",
        "attempt-6", "candidate-6",
    ])
    provider = AuthorityProvider()
    service = AtelierMediaGenerationAdmissionService(
        store,
        authority_provider=provider,
        clock=lambda: 100.0,
        id_factory=ids.popleft,
        server_storage_identity="server-store",
    )
    yield service, store, provider, ref
    store.close()


def test_pending_reference_selected_for_image_admission_fails_before_authority(app):
    service, store, provider, _ref = app

    def add_pending(project: AtelierProject) -> None:
        target = next(node for node in project.nodes if node.id == "image-target")
        ref = next(node for node in project.nodes if node.id == "ref")
        ref.media_urls = []
        target.data = {
            "reference_node_ids": ["ref"],
            "pending_reference_nodes": [{"schema_version": 1, "source_node_id": "ref", "source_kind": "image", "status": "pending_media"}],
        }

    store.apply_project_command("p", expected_revision=0, command=add_pending)

    with pytest.raises(ValueError, match="atelier_reference_pending_media"):
        service.admit_image("p", "image-target", image_command(expected_revision=1))

    assert provider.calls == []
    assert store.get_project("p").revision == 1


def test_image_batch_admission_commits_media_attempts_candidates_and_one_receipt(app, monkeypatch):
    service, store, provider, _ = app
    freeze_calls = []
    from src.apps.atelier import media_generation_preparation as prep
    original_freeze = prep.freeze_media

    def counting_freeze(*args, **kwargs):
        freeze_calls.append((args, kwargs))
        return original_freeze(*args, **kwargs)

    monkeypatch.setattr(prep, "freeze_media", counting_freeze)

    ack = service.admit_image("p", "image-target", image_command())

    assert not ack.replayed
    assert len(ack.members) == 3
    assert ack.new_candidate_ids == ["candidate-1", "candidate-2", "candidate-3"]
    assert ack.receipt.schema_version == 2
    assert ack.receipt.entrypoint == "atelier.provider_image.v1"
    assert len(store.get_project("p").generation_attempts) == 3
    assert len(freeze_calls) == 1
    assert len(provider.calls) == 1
    attempts = [member.attempt for member in ack.members]
    assert {attempt.media_kind for attempt in attempts} == {"image"}
    assert [attempt.output_ref for attempt in attempts] == [None, None, None]
    assert [attempt.provider_execution.output_ref for attempt in attempts] == [
        "assets/atelier/candidate-1-attempt-1.png",
        "assets/atelier/candidate-2-attempt-2.png",
        "assets/atelier/candidate-3-attempt-3.png",
    ]
    assert [attempt.provider_execution.media for attempt in attempts] == [attempts[0].provider_execution.media] * 3
    assert attempts[0].provider_execution.intent["params"]["seed"] == 0
    assert attempts[0].provider_execution.intent["params"]["prompt_extend"] is False
    assert attempts[0].provider_execution.intent["params"]["watermark"] is False
    node = next(item for item in ack.project.nodes if item.id == "image-target")
    assert [item["id"] for item in node.data["candidates"]] == ack.new_candidate_ids
    assert all(item["execution_contract"] == "atelier-provider-media-v2" for item in node.data["candidates"])
    assert SECRET not in store.path.read_text()


def test_tts_admission_commits_audio_attempt_and_candidate_without_media_freeze(app, monkeypatch):
    service, store, provider, _ = app
    monkeypatch.setattr(
        "src.apps.atelier.media_generation_preparation.freeze_media",
        lambda *_args, **_kwargs: pytest.fail("TTS admission must not freeze source media"),
        raising=False,
    )

    ack = service.admit_tts("p", "audio-target", tts_command())

    assert not ack.replayed
    assert len(ack.members) == 1
    assert ack.receipt.schema_version == 1
    assert ack.receipt.entrypoint == "atelier.provider_audio.v1"
    attempt = ack.members[0].attempt
    assert attempt.media_kind == "audio"
    assert attempt.provider_execution.provider_model_id == "cosyvoice-v3-flash"
    assert attempt.provider_execution.media == []
    assert attempt.provider_execution.intent["request_fields"] == ["params.instructions", "params.volume", "text", "voice"]
    candidate = ack.members[0].candidate
    assert candidate["media_kind"] == "audio"
    assert candidate["status"] == "pending"
    assert len(provider.calls) == 1
    assert SECRET not in store.path.read_text()


def test_replay_and_readback_do_not_rebind_refreeze_or_read_changed_media(app, monkeypatch):
    service, _store, provider, ref = app
    original = service.admit_image("p", "image-target", image_command())
    ref.unlink()
    provider.fail = AssertionError("replay must not bind current credentials")
    monkeypatch.setattr(
        "src.apps.atelier.media_generation_preparation.freeze_media",
        lambda *_args, **_kwargs: pytest.fail("replay must not freeze media"),
        raising=False,
    )
    monkeypatch.setattr(
        "src.apps.atelier.generation_commands.get_catalog_accessor",
        lambda: pytest.fail("replay must not read catalog"),
    )
    monkeypatch.setattr(
        "src.apps.atelier.media_generation_preparation.get_catalog_accessor",
        lambda: pytest.fail("replay must not read catalog"),
    )

    replay = service.admit_image("p", "image-target", image_command(expected_revision=999))
    readback = service.read_request("p", "image-request")

    for ack in (replay, readback):
        assert ack.replayed
        assert ack.new_candidate_ids == []
        assert ack.receipt == original.receipt
        assert [member.attempt for member in ack.members] == [member.attempt for member in original.members]


def test_concurrent_same_media_request_commits_once_and_replays(app):
    service, store, provider, _ = app
    command = image_command(batch_size=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.admit_image("p", "image-target", command), range(2)))

    assert sorted(result.replayed for result in results) == [False, True]
    assert sum(len(result.new_candidate_ids) for result in results) == 1
    assert len(store.get_project("p").generation_attempts) == 1
    assert len(store.get_project("p").generation_request_receipts) == 1
    assert len(provider.calls) == 1


def test_same_request_id_with_different_image_or_tts_content_conflicts(app):
    service, store, _provider, _ = app
    service.admit_image("p", "image-target", image_command(batch_size=1))

    with pytest.raises(StorageConflictError):
        service.admit_image("p", "image-target", image_command(prompt="changed", batch_size=1))
    with pytest.raises(StorageConflictError):
        service.admit_tts("p", "audio-target", tts_command(client_request_id="image-request"))

    assert len(store.get_project("p").generation_attempts) == 1


def test_commit_failure_leaves_project_bytes_unchanged_and_no_durable_authority(app, monkeypatch):
    service, store, _provider, _ = app
    before = store.path.read_bytes()
    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", Mock(side_effect=StorageError("disk full")))

    with pytest.raises(StorageError):
        service.admit_image("p", "image-target", image_command(batch_size=1))

    assert store.path.read_bytes() == before
    project = store.get_project("p")
    assert project.generation_attempts == []
    assert project.generation_request_receipts == []
    assert not next(node for node in project.nodes if node.id == "image-target").data.get("candidates")


def test_stale_revision_rejects_before_authority_binding_or_freeze(app, monkeypatch):
    service, store, provider, _ = app
    monkeypatch.setattr(
        "src.apps.atelier.media_generation_preparation.freeze_media",
        lambda *_args, **_kwargs: pytest.fail("stale revision must reject before freeze"),
        raising=False,
    )

    with pytest.raises(StorageConflictError):
        service.admit_image("p", "image-target", image_command(expected_revision=4))

    assert store.get_project("p").generation_attempts == []
    assert provider.calls == []


def test_reopened_store_replays_media_receipt_without_credentials_or_catalog(app):
    service, store, _provider, ref = app
    original = service.admit_tts("p", "audio-target", tts_command())
    root = store.data_dir
    store.close()
    ref.unlink()
    reopened = AtelierStorage(root)
    provider = AuthorityProvider()
    provider.fail = AssertionError("readback must not bind credentials")
    try:
        restored = AtelierMediaGenerationAdmissionService(reopened, authority_provider=provider)
        replay = restored.read_request("p", "tts-request")
        assert replay.replayed
        assert replay.new_candidate_ids == []
        assert replay.receipt == original.receipt
        assert replay.members[0].attempt == original.members[0].attempt
    finally:
        reopened.close()
