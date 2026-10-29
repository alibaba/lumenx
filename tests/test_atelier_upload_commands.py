from __future__ import annotations

import json

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.apps.atelier.api import router
from src.apps.atelier.media_upload import ManagedUpload
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.service import AtelierService
from src.apps.atelier.storage import AtelierStorage
from src.apps.atelier.upload_commands import (
    UPLOAD_TARGET_ACTIVE_CODE,
    AtelierUploadTargetActiveError,
    CreateMediaNodeUpload,
    UploadReferenceAttach,
    fingerprint_upload_request,
)
from src.utils.json_storage import StorageConflictError, StorageError


class Sequencer:
    def __init__(self):
        self.now = 100.0
        self.ids = 0

    def clock(self) -> float:
        self.now += 1.0
        return self.now

    def id_factory(self) -> str:
        self.ids += 1
        return f"id-{self.ids}"


class FakeUploader:
    def __init__(self, storage: AtelierStorage | None = None, project_id: str = "p1"):
        self.calls: list[tuple[list[bytes], str, str | None]] = []
        self.discarded: list[ManagedUpload] = []
        self.storage = storage
        self.project_id = project_id
        self.mutate_revision_during_upload = False
        self.serial = 0

    def __call__(self, chunks, *, filename: str, content_type: str | None) -> ManagedUpload:
        data = list(chunks)
        self.calls.append((data, filename, content_type))
        if self.mutate_revision_during_upload and self.storage is not None:
            current = self.storage.get_project(self.project_id)
            self.storage.apply_project_command(
                self.project_id,
                expected_revision=current.revision,
                command=lambda project: setattr(project, "title", "changed while uploading"),
            )
        suffix = filename.rsplit(".", 1)[-1]
        kind = "image" if suffix in {"png", "jpg", "jpeg", "webp", "gif"} else "video" if suffix in {"mp4", "mov", "webm", "mkv"} else "audio"
        body = b"".join(data)
        sha = "a" * 64 if body == b"same" else "b" * 64
        self.serial += 1
        return ManagedUpload(
            url=f"uploads/{sha[:8]}-{self.serial}.{suffix}",
            media_kind=kind,
            size_bytes=len(body),
            sha256=sha,
            width=12 if kind in {"image", "video"} else None,
            height=8 if kind in {"image", "video"} else None,
            duration=1.25 if kind in {"audio", "video"} else None,
            poster_url="uploads/poster.jpg" if kind == "video" else None,
        )

    def discard(self, managed: ManagedUpload) -> None:
        self.discarded.append(managed)


def make_service(tmp_path, *, nodes: list[AtelierNode] | None = None):
    seq = Sequencer()
    storage = AtelierStorage(tmp_path)
    storage.create_project(
        AtelierProject(
            id="p1",
            title="Canvas",
            created_at=1.0,
            updated_at=1.0,
            nodes=nodes or [],
        )
    )
    uploader = FakeUploader(storage)
    service = AtelierService(
        storage,
        clock=seq.clock,
        id_factory=seq.id_factory,
        media_uploader=uploader,
        media_discarder=uploader.discard,
    )
    return service, storage, uploader


def test_create_media_node_persists_upload_receipt_and_replays_after_node_deleted(tmp_path):
    service, storage, uploader = make_service(tmp_path)
    command = CreateMediaNodeUpload(
        expected_revision=0,
        client_request_id="req-1",
        filename="hero.png",
        node_type="image",
        title="Hero",
        x=5,
        y=6,
    )

    ack = service.create_media_node_upload("p1", command, [b"same"], content_type="image/png")

    assert ack.replayed is False
    assert ack.node_ids == ["id-2"]
    node = ack.project.nodes[0]
    assert node.type == "image"
    assert node.status == "completed"
    assert node.media_urls == ["uploads/aaaaaaaa-1.png"]
    assert node.title == "Hero"
    assert node.x == 5
    assert node.data == {
        "filename": "hero.png",
        "size_bytes": 4,
        "sha256": "a" * 64,
        "width": 12,
        "height": 8,
    }
    receipt = ack.project.node_upload_receipts[0]
    assert receipt.client_request_id == "req-1"
    assert receipt.node_ids == ["id-2"]
    assert receipt.base_revision == 0
    assert receipt.committed_revision == 1
    assert receipt.media.url == "uploads/aaaaaaaa-1.png"

    storage.apply_project_command("p1", expected_revision=1, command=lambda project: project.nodes.clear())
    replay = service.create_media_node_upload(
        "p1",
        command.model_copy(update={"expected_revision": 1}),
        [b"same"],
        content_type="image/png",
    )

    assert replay.replayed is True
    assert replay.node_ids == ["id-2"]
    assert replay.project.revision == 2
    assert replay.project.nodes == []
    assert len(uploader.calls) == 2
    assert [item.url for item in uploader.discarded] == ["uploads/aaaaaaaa-2.png"]


def test_duplicate_request_id_with_different_actual_bytes_conflicts_without_mutation(tmp_path):
    service, storage, uploader = make_service(tmp_path)
    command = CreateMediaNodeUpload(expected_revision=0, client_request_id="req-1", filename="hero.png", node_type="image")
    service.create_media_node_upload("p1", command, [b"same"], content_type="image/png")

    with pytest.raises(StorageConflictError, match="request id"):
        service.create_media_node_upload("p1", command.model_copy(update={"expected_revision": 1}), [b"different"], content_type="image/png")

    project = storage.get_project("p1")
    assert project.revision == 1
    assert len(project.nodes) == 1
    assert [item.url for item in uploader.discarded] == ["uploads/bbbbbbbb-2.png"]


def test_stale_revision_after_upload_discards_new_media_and_does_not_publish_receipt(tmp_path):
    service, storage, uploader = make_service(tmp_path)
    uploader.mutate_revision_during_upload = True

    with pytest.raises(StorageConflictError):
        service.create_media_node_upload(
            "p1",
            CreateMediaNodeUpload(expected_revision=0, client_request_id="req-late", filename="hero.png", node_type="image"),
            [b"same"],
            content_type="image/png",
        )

    project = storage.get_project("p1")
    assert project.title == "changed while uploading"
    assert project.nodes == []
    assert project.node_upload_receipts == []
    assert [item.url for item in uploader.discarded] == ["uploads/aaaaaaaa-1.png"]


def test_upload_reference_attach_creates_source_and_updates_target_atomically(tmp_path):
    target = AtelierNode(id="target", project_id="p1", type="video", title="Target")
    service, storage, _uploader = make_service(tmp_path, nodes=[target])

    ack = service.upload_reference_attach(
        "p1",
        "target",
        UploadReferenceAttach(expected_revision=0, client_request_id="ref-1", filename="ref.png", title="Reference", x=20, y=30),
        [b"same"],
        content_type="image/png",
    )

    assert ack.replayed is False
    assert ack.node_ids == ["target", "id-2"]
    project = storage.get_project("p1")
    target_node = next(node for node in project.nodes if node.id == "target")
    source_node = next(node for node in project.nodes if node.id == "id-2")
    assert source_node.type == "image"
    assert source_node.media_urls == ["uploads/aaaaaaaa-1.png"]
    assert source_node.data["parent_node_id"] == "target"
    assert source_node.data["reference_role"] == "video_reference_image"
    assert target_node.data["reference_node_ids"] == ["id-2"]
    assert target_node.data["reference_image_urls"] == ["uploads/aaaaaaaa-1.png"]
    receipt = project.node_upload_receipts[0]
    assert receipt.operation == "upload_reference_attach"
    assert receipt.target_node_id == "target"
    assert receipt.source_node_id == "id-2"



def test_upload_reference_active_target_is_definite_no_write_and_discards_media(tmp_path):
    target = AtelierNode(
        id="target",
        project_id="p1",
        type="video",
        title="Target",
        status="completed",
        data={"candidates": [{"id": "candidate-1", "status": "accepted"}]},
    )
    service, storage, uploader = make_service(tmp_path, nodes=[target])

    with pytest.raises(AtelierUploadTargetActiveError, match="active execution"):
        service.upload_reference_attach(
            "p1",
            "target",
            UploadReferenceAttach(expected_revision=0, client_request_id="ref-active", filename="ref.png"),
            [b"same"],
            content_type="image/png",
        )

    project = storage.get_project("p1")
    assert project.revision == 0
    assert project.node_upload_receipts == []
    assert len(project.nodes) == 1
    assert [item.url for item in uploader.discarded] == ["uploads/aaaaaaaa-1.png"]


def test_upload_reference_terminal_candidates_allow_upload(tmp_path):
    target = AtelierNode(
        id="target",
        project_id="p1",
        type="video",
        title="Target",
        status="completed",
        data={"candidates": [{"id": "candidate-1", "status": "completed", "video_url": "video/done.mp4"}]},
    )
    service, storage, _uploader = make_service(tmp_path, nodes=[target])

    ack = service.upload_reference_attach(
        "p1",
        "target",
        UploadReferenceAttach(expected_revision=0, client_request_id="ref-done", filename="ref.png"),
        [b"same"],
        content_type="image/png",
    )

    assert ack.node_ids == ["target", "id-2"]
    project = storage.get_project("p1")
    assert project.revision == 1
    assert project.node_upload_receipts[0].client_request_id == "ref-done"


def test_upload_reference_active_target_cleanup_failure_remains_uncertain(tmp_path):
    target = AtelierNode(
        id="target",
        project_id="p1",
        type="video",
        title="Target",
        status="completed",
        data={"candidates": [{"id": "candidate-1", "status": "unknown"}]},
    )
    service, storage, _uploader = make_service(tmp_path, nodes=[target])

    def fail_discard(_managed: ManagedUpload) -> None:
        raise ValueError("cleanup failed")

    service._media_discarder = fail_discard

    with pytest.raises(StorageError) as exc_info:
        service.upload_reference_attach(
            "p1",
            "target",
            UploadReferenceAttach(expected_revision=0, client_request_id="ref-active", filename="ref.png"),
            [b"same"],
            content_type="image/png",
        )

    assert exc_info.value.commit_uncertain is True
    assert storage.get_project("p1").node_upload_receipts == []


def test_upload_reference_active_target_http_uses_structured_definite_code(tmp_path):
    target = AtelierNode(
        id="target",
        project_id="p1",
        type="video",
        title="Target",
        status="completed",
        data={"candidates": [{"id": "candidate-1", "status": "running"}]},
    )
    service, storage, _uploader = make_service(tmp_path, nodes=[target])
    app = FastAPI()
    app.state.atelier_service = service
    app.include_router(router, prefix="/atelier")

    with TestClient(app) as client:
        response = client.post(
            "/atelier/projects/p1/uploads/references/target",
            params={"expected_revision": 0, "client_request_id": "ref-active", "filename": "ref.png"},
            content=b"same",
            headers={"content-type": "image/png"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": UPLOAD_TARGET_ACTIVE_CODE,
        "detail": "Target node has active execution; wait for it to finish before uploading a reference",
        "commit_uncertain": False,
    }
    assert storage.get_project("p1").node_upload_receipts == []

def test_persisted_upload_receipts_are_validated_fail_closed(tmp_path):
    service, storage, _uploader = make_service(tmp_path)
    command = CreateMediaNodeUpload(expected_revision=0, client_request_id="req-1", filename="hero.png", node_type="image")
    service.create_media_node_upload("p1", command, [b"same"], content_type="image/png")
    storage.close()

    db_path = tmp_path / "atelier_projects.json"
    payload = json.loads(db_path.read_text(encoding="utf-8"))
    duplicate = dict(payload["p1"]["node_upload_receipts"][0])
    duplicate["receipt_id"] = "other-receipt"
    payload["p1"]["node_upload_receipts"].append(duplicate)
    db_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Exception, match="Duplicate Atelier upload request id"):
        AtelierStorage(tmp_path)


def test_discard_failure_after_upload_conflict_is_commit_uncertain(tmp_path):
    service, _storage, uploader = make_service(tmp_path)
    command = CreateMediaNodeUpload(expected_revision=0, client_request_id="req-1", filename="hero.png", node_type="image")
    service.create_media_node_upload("p1", command, [b"same"], content_type="image/png")

    def fail_discard(_managed: ManagedUpload) -> None:
        raise ValueError("poster cleanup failed")

    service._media_discarder = fail_discard
    with pytest.raises(Exception) as exc_info:
        service.create_media_node_upload("p1", command.model_copy(update={"expected_revision": 1}), [b"different"], content_type="image/png")
    assert getattr(exc_info.value, "commit_uncertain", False) is True
    assert len(uploader.calls) == 2


def test_persisted_upload_receipt_contract_matrix_fails_closed(tmp_path):
    service, storage, _uploader = make_service(tmp_path)
    command = CreateMediaNodeUpload(expected_revision=0, client_request_id="req-1", filename="hero.png", node_type="image")
    service.create_media_node_upload("p1", command, [b"same"], content_type="image/png")
    storage.close()

    db_path = tmp_path / "atelier_projects.json"
    base = json.loads(db_path.read_text(encoding="utf-8"))

    def receipt_of(data):
        return data["p1"]["node_upload_receipts"][0]

    def media_of(data):
        return receipt_of(data)["media"]

    cases = [
        (lambda data: receipt_of(data).__setitem__("committed_revision", 3), "revision"),
        (lambda data: receipt_of(data).__setitem__("client_request_id", "bad space"), "client request"),
        (lambda data: receipt_of(data).__setitem__("node_ids", ["id-2", "id-2"]), "node id"),
        (lambda data: receipt_of(data).__setitem__("source_node_id", ""), "fields"),
        (lambda data: receipt_of(data).__setitem__("previous_media", media_of(data)), "replacement"),
        (lambda data: receipt_of(data).__setitem__("replaced_node_id", "old"), "replacement"),
        (lambda data: media_of(data).__setitem__("url", r"uploads/foo\bar.png"), "media reference"),
        (lambda data: media_of(data).__setitem__("url", "uploads/a b.png"), "media reference"),
        (lambda data: media_of(data).__setitem__("url", "uploads/not-image.mp4"), "media extension"),
        (lambda data: media_of(data).__setitem__("width", None), "image dimensions"),
        (lambda data: media_of(data).__setitem__("duration", 1.0), "image duration"),
        (lambda data: media_of(data).__setitem__("poster_url", "uploads/poster.jpg"), "poster proof"),
    ]
    audio_media = dict(media_of(base), url="uploads/audio.mp3", media_kind="audio", width=None, height=None, duration=1.25)
    video_media = dict(media_of(base), url="uploads/video.mp4", media_kind="video", width=12, height=8, duration=1.25)
    cases.extend(
        [
            (lambda data: receipt_of(data).__setitem__("media", dict(audio_media, width=1)), "audio dimensions"),
            (lambda data: receipt_of(data).__setitem__("media", dict(audio_media, duration=None)), "audio duration"),
            (lambda data: receipt_of(data).__setitem__("media", dict(video_media, width=None)), "video dimensions"),
            (lambda data: receipt_of(data).__setitem__("media", dict(video_media, duration=None)), "video duration"),
            (
                lambda data: receipt_of(data).__setitem__(
                    "media",
                    dict(video_media, url="uploads/video.jpg", poster_url="uploads/video.jpg", poster_size_bytes=1, poster_sha256="a" * 64),
                ),
                "poster proof",
            ),
            (
                lambda data: receipt_of(data).__setitem__(
                    "media",
                    dict(video_media, poster_url="uploads/poster.png", poster_size_bytes=1, poster_sha256="a" * 64),
                ),
                "poster proof",
            ),
        ]
    )
    for mutate, message in cases:
        payload = json.loads(json.dumps(base))
        mutate(payload)
        db_path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(StorageError, match=message):
            AtelierStorage(tmp_path)


def test_request_fingerprint_distinguishes_missing_and_empty_title():
    media = ManagedUpload(url="uploads/a.png", media_kind="image", size_bytes=1, sha256="a" * 64, width=1, height=1)
    missing = CreateMediaNodeUpload(expected_revision=0, client_request_id="req", filename="a.png", node_type="image")
    empty = CreateMediaNodeUpload(expected_revision=0, client_request_id="req", filename="a.png", node_type="image", title="")

    assert fingerprint_upload_request(missing, operation="create_media_node", media=media) != fingerprint_upload_request(
        empty,
        operation="create_media_node",
        media=media,
    )
