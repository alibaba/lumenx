"""Shared Atelier generation preview/preparation must be admission's single source of truth."""

import base64
from unittest.mock import Mock

import pytest

from src.apps.atelier.generation_admission import AtelierGenerationAdmissionService
from src.apps.atelier.generation_commands import CreateVideoCandidateCommand
from src.apps.atelier.generation_preview import build_generation_preview
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.utils.json_storage import StorageConflictError

PNG_A = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=")
PNG_B = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/58BAwMCAO8B9QQAAAAASUVORK5CYII=")
SCOPE_A = ProviderScope(provider="dashscope", backend="dashscope", gateway="dashscope", base_url="https://dashscope.aliyuncs.com", region="cn-beijing")
SCOPE_B = ProviderScope(provider="dashscope", backend="dashscope", gateway="dashscope", base_url="https://dashscope.aliyuncs.com", region="cn-beijing", workspace_id="workspace-a")


def command(**changes):
    return CreateVideoCandidateCommand(**{
        "expected_revision": 0,
        "client_request_id": "request-1",
        "model_id": "wan/wan2.7-video#i2v",
        "mode": "i2v",
        "prompt": "Slow sunrise",
        "first_frame": {"node_id": "image", "media_url": "uploads/frame.png"},
        **changes,
    })


@pytest.fixture
def preview_app(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(AtelierProject(id="p", title="Canvas", created_at=1.0, updated_at=1.0, nodes=[
        AtelierNode(id="image", project_id="p", type="image", status="completed", media_urls=["uploads/frame.png"]),
        AtelierNode(id="video", project_id="p", type="video", data={"reference_node_ids": ["image"], "reference_image_urls": ["uploads/frame.png"]}),
    ]))
    image = tmp_path / "uploads/frame.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG_A)
    yield store, image
    store.close()


def build(store, *, scope=SCOPE_A, credential_ref="bound-key", command_value=None, storage_identity="storage-a"):
    return build_generation_preview(
        store.get_project("p"),
        "video",
        command_value or command(),
        media_scope=store._media_write_scope(),
        credential=ResolvedCredential(scope, credential_ref, "test-only-secret"),
        server_storage_identity=storage_identity,
    )


def test_preview_public_record_is_stable_secret_free_and_attempt_independent(preview_app):
    store, _ = preview_app
    first = build(store)
    second = build(store)

    assert first.preview_fingerprint == second.preview_fingerprint
    assert first.public_record == second.public_record
    assert first.provider_model_id == "wan2.7-i2v"
    assert first.record.attempt_id == "preview-attempt"
    assert first.record.phase == "accepted"
    rendered = str(first.public_record) + first.preview_fingerprint + first.record.model_dump_json()
    assert "test-only-secret" not in rendered
    assert "preview-attempt" not in first.preview_fingerprint
    assert first.public_record["server_storage_identity"] == "storage-a"
    assert first.public_record["credential_ref"] == "bound-key"
    assert first.public_record["media"][0]["sha256"] == first.media_version.sha256
    assert store.get_project("p").generation_attempts == []


def test_preview_repr_does_not_leak_internal_sensitive_objects(preview_app):
    store, _ = preview_app
    preview = build(store)
    rendered = repr(preview)
    assert "test-only-secret" not in rendered
    assert "data:image" not in rendered
    assert "submission=" not in rendered
    assert "credential=" not in rendered


def test_preview_requires_server_storage_identity_not_browser_api_identity(preview_app):
    store, _ = preview_app
    with pytest.raises(ValueError, match="server storage identity"):
        build_generation_preview(
            store.get_project("p"),
            "video",
            command(),
            media_scope=store._media_write_scope(),
            credential=ResolvedCredential(SCOPE_A, "bound-key", "test-only-secret"),
            server_storage_identity="",
        )


def test_preview_hash_preserves_omission_false_zero_and_random_seed(preview_app):
    store, _ = preview_app
    omitted = build(store, command_value=command())
    explicit = build(store, command_value=command(params={"duration": 5, "resolution": "1080p", "seed": -1, "prompt_extend": False, "watermark": False}))
    explicit_zero = build(store, command_value=command(params={"duration": 5, "resolution": "1080p", "seed": 0, "prompt_extend": False, "watermark": False}))

    assert omitted.preview_fingerprint != explicit.preview_fingerprint
    assert explicit.preview_fingerprint != explicit_zero.preview_fingerprint
    assert explicit.public_record["intent"]["params"]["seed"] == -1
    assert explicit.public_record["intent"]["params"]["prompt_extend"] is False
    assert explicit.public_record["intent"]["params"]["watermark"] is False
    assert explicit.record.intent["params"]["seed"] == -1
    assert explicit_zero.public_record["intent"]["params"]["seed"] == 0
    assert explicit_zero.record.intent["params"]["seed"] == 0


def test_preview_hash_changes_for_same_url_changed_bytes_scope_or_storage(preview_app):
    store, image = preview_app
    first = build(store)
    image.write_bytes(PNG_B)
    changed_media = build(store)
    changed_scope = build(store, scope=SCOPE_B)
    changed_storage = build(store, storage_identity="storage-b")
    changed_credential = build(store, credential_ref="other-bound-key")

    assert first.public_record["first_frame"]["media_url"] == changed_media.public_record["first_frame"]["media_url"]
    assert first.public_record["media"][0]["sha256"] != changed_media.public_record["media"][0]["sha256"]
    assert len({
        first.preview_fingerprint,
        changed_media.preview_fingerprint,
        changed_scope.preview_fingerprint,
        changed_storage.preview_fingerprint,
        changed_credential.preview_fingerprint,
    }) == 5


def test_preview_does_not_create_generation_members_or_call_provider(preview_app, monkeypatch):
    store, _ = preview_app
    before_bytes = store.path.read_bytes()
    before_project = store.get_project("p")

    def provider_work_bomb(*_args, **_kwargs):
        raise AssertionError("preview must not perform provider work")

    monkeypatch.setattr("src.models.wanx.WanxModel.submit", provider_work_bomb)
    monkeypatch.setattr("src.models.wanx.WanxModel.query", provider_work_bomb)
    monkeypatch.setattr("src.models.wanx.WanxModel.fetch", provider_work_bomb)
    credential = ResolvedCredential(SCOPE_A, "bound-key", "test-only-secret")
    preview = build_generation_preview(
        store.get_project("p"),
        "video",
        command(),
        media_scope=store._media_write_scope(),
        credential=credential,
        server_storage_identity="storage-a",
    )
    after_project = store.get_project("p")

    assert preview.submission.credential_ref == "bound-key"
    assert store.path.read_bytes() == before_bytes
    assert after_project == before_project
    assert after_project.generation_request_receipts == []
    assert after_project.generation_attempts == []
    assert after_project.agent_turns == []
    assert next(n for n in after_project.nodes if n.id == "video").data.get("candidates") is None


def test_manual_admission_reuses_preview_builder_and_replay_skips_builder(preview_app, monkeypatch):
    store, _ = preview_app
    bind = Mock(return_value=ResolvedCredential(SCOPE_A, "bound-key", "test-only-secret"))
    calls = []
    original = build_generation_preview

    def tracking_builder(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr("src.apps.atelier.generation_admission.build_generation_preview", tracking_builder)
    service = AtelierGenerationAdmissionService(
        store,
        credential_provider=bind,
        clock=lambda: 100.0,
        server_storage_identity=lambda: "storage-a",
    )
    first = service.admit("p", "video", command())
    assert len(calls) == 1
    assert calls[0][1]["attempt_id"] == first.attempt.attempt_id
    assert calls[0][1]["server_storage_identity"] == "storage-a"
    assert first.attempt.provider_execution.attempt_id == first.attempt.attempt_id

    monkeypatch.setattr("src.apps.atelier.generation_admission.build_generation_preview", Mock(side_effect=AssertionError("replay must not build preview")))
    bind.side_effect = AssertionError("replay must not bind current credential")
    replay = service.admit("p", "video", command(expected_revision=999))
    assert replay.replayed and replay.new_candidate_ids == []


def test_admission_replay_and_readback_do_not_initialize_default_storage_identity(preview_app, monkeypatch):
    store, _ = preview_app
    bind = Mock(return_value=ResolvedCredential(SCOPE_A, "bound-key", "test-only-secret"))
    service = AtelierGenerationAdmissionService(
        store, credential_provider=bind, clock=lambda: 100.0, server_storage_identity="storage-a",
    )
    original = service.admit("p", "video", command())

    monkeypatch.setattr(
        "src.apps.atelier.generation_admission.initialize_storage_identity",
        Mock(side_effect=AssertionError("replay/readback must not initialize storage identity")),
    )
    replay_service = AtelierGenerationAdmissionService(store, credential_provider=Mock(side_effect=AssertionError("No binding")))
    assert replay_service.read_request("p", "request-1").attempt == original.attempt
    assert replay_service.admit("p", "video", command(expected_revision=99)).replayed


def test_explicit_empty_storage_identity_fails_without_defaulting(preview_app):
    store, _ = preview_app
    service = AtelierGenerationAdmissionService(
        store,
        credential_provider=Mock(return_value=ResolvedCredential(SCOPE_A, "bound-key", "test-only-secret")),
        server_storage_identity="",
    )
    with pytest.raises(ValueError, match="server storage identity"):
        service.admit("p", "video", command())


def test_stale_manual_admission_rejects_before_preview(preview_app, monkeypatch):
    store, _ = preview_app
    bind = Mock(return_value=ResolvedCredential(SCOPE_A, "bound-key", "test-only-secret"))
    monkeypatch.setattr("src.apps.atelier.generation_admission.build_generation_preview", Mock(side_effect=AssertionError("stale request must not build preview")))
    service = AtelierGenerationAdmissionService(store, credential_provider=bind, server_storage_identity=lambda: "storage-a")
    with pytest.raises(StorageConflictError):
        service.admit("p", "video", command(expected_revision=4))
    bind.assert_not_called()
