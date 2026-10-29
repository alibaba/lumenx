"""Studio can publish proved Core images without borrowing another product root."""

import hashlib

import pytest

from src.models.provider_media_intents import build_accepted_sync_artifact_record, sync_artifact_proof_from_record
from src.models.provider_recovery import ProviderRecoveryError, record_with_changes
from src.models.provider_tasks import ProviderScope
from src.utils.generation_intent import GenerationIntent
from src.utils import media_versions as media
from tests import test_sync_media_publication as publication_tests

owner = publication_tests.owner


OUTPUT = "assets/studio/attempt-studio.png"


def accepted(output=OUTPUT):
    return build_accepted_sync_artifact_record(
        intent=GenerationIntent(
            model_id="wan/wan2.7-image-pro#image", catalog_mode="image", mode="t2i",
            prompt="Frozen Studio image", params={"seed": 0, "watermark": False},
            sources={"params.seed": "request", "params.watermark": "request"},
            request_fields=("params.seed", "params.watermark"),
        ),
        provider_model_id="wan2.7-image-pro", media_kind="image", media=[],
        scope=ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing"),
        credential_ref="credential", attempt_id="attempt-studio", output_ref=output,
    )


def publish(owner, content=b"proved private bytes", output=OUTPUT, **kwargs):
    return media.store_media_stream(
        [content], output_ref=output, output_root=owner.root, writer_lock=owner.lease,
        storage_guard=owner.guard, max_bytes=1024, media_kind="image", **kwargs,
    )


def test_studio_record_prepared_proof_publish_verify_and_completion_share_one_identity(owner):
    record = record_with_changes(accepted(), phase="submitting")
    prepared = []

    def checkpoint(path, digest, size):
        assert not (owner.root / path).exists()
        proof = sync_artifact_proof_from_record(record, relative_path=path, sha256=digest, size_bytes=size)
        prepared.append(record_with_changes(record, prepared_artifact=proof))

    path, digest, size = publish(owner, on_prepared=checkpoint)
    complete = record_with_changes(prepared[0], phase="completed", artifact=prepared[0].prepared_artifact)
    assert complete.output_ref == path == OUTPUT
    assert complete.handle is None and complete.observation is None
    assert complete.prepared_artifact == complete.artifact
    assert complete.model_validate_json(complete.model_dump_json()) == complete
    assert media.verify_managed_media_file(
        path, output_root=owner.root, writer_lock=owner.lease, storage_guard=owner.guard,
        expected_sha256=digest, expected_size=size, max_bytes=1024, media_kind="image",
    ) == (path, hashlib.sha256(b"proved private bytes").hexdigest(), size)
    with pytest.raises(media.MediaVersionError, match="already exists"):
        publish(owner, b"later attempt")
    assert (owner.root / path).read_bytes() == b"proved private bytes"


@pytest.mark.parametrize("path", [
    "assets/out.png", "assets/studio/.png", "assets/studio/", "assets/studio/../out.png", "assets/studio/nested/out.png",
    "assets/studio//out.png", "assets/studio/./out.png", "assets/studio/.hidden.png",
    "assets/studio/out.jpg", "assets/studio/out.mp3", "assets/studio/out.PNG",
    "assets/studio/out.png?query=1", "assets/studio/out.png#fragment", "assets/studio/out%2fother.png",
    "assets/studio/out\\name.png", "assets/studio/out.png\n", "/assets/studio/out.png",
])
def test_studio_record_and_publication_reject_invalid_destinations_before_writes(owner, path):
    with pytest.raises(ValueError):
        accepted(path)
    with pytest.raises(media.MediaVersionError):
        publish(owner, output=path)
    assert not (owner.root / "assets").exists()


@pytest.mark.parametrize("level", ["assets", "studio", "file"])
def test_studio_publication_does_not_follow_symlinks(owner, tmp_path, level):
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = owner.root / OUTPUT
    link = {"assets": owner.root / "assets", "studio": destination.parent, "file": destination}[level]
    link.parent.mkdir(parents=True, exist_ok=True)
    target = outside / "retained.png" if level == "file" else outside
    if level == "file":
        target.write_bytes(b"retain")
    link.symlink_to(target, target_is_directory=level != "file")
    with pytest.raises(media.MediaVersionError):
        publish(owner)
    assert link.is_symlink()
    assert sorted(path.name for path in outside.iterdir()) == (["retained.png"] if level == "file" else [])
    if level == "file":
        assert target.read_bytes() == b"retain"


def test_studio_proof_cannot_move_to_another_attempt_path_or_bytes(owner):
    record = record_with_changes(accepted(), phase="submitting")
    proof = sync_artifact_proof_from_record(record, relative_path=OUTPUT, sha256="a" * 64, size_bytes=7)
    prepared = record_with_changes(record, prepared_artifact=proof)
    with pytest.raises(ValueError, match="attempt"):
        record_with_changes(record, prepared_artifact={**proof, "attempt_id": "different"})
    with pytest.raises(ValueError, match="output"):
        record_with_changes(record, prepared_artifact={**proof, "relative_path": "assets/studio/other.png", "output_ref": "assets/studio/other.png"})
    with pytest.raises(ProviderRecoveryError, match="changed"):
        sync_artifact_proof_from_record(prepared, relative_path=OUTPUT, sha256="b" * 64, size_bytes=7)
    publish(owner)
    with pytest.raises(media.MediaVersionError, match="content differs"):
        media.verify_managed_media_file(
            OUTPUT, output_root=owner.root, writer_lock=owner.lease, storage_guard=owner.guard,
            expected_sha256="b" * 64, expected_size=len(b"proved private bytes"), max_bytes=1024, media_kind="image",
        )


def test_studio_image_namespace_is_not_an_audio_or_legacy_video_destination(owner):
    with pytest.raises(ValueError, match="path"):
        record_with_changes(accepted(), media_kind="audio")
    for kind in ("audio", "video"):
        with pytest.raises(media.MediaVersionError):
            media.store_media_stream(
                [b"wrong media kind"], output_ref=OUTPUT, output_root=owner.root,
                writer_lock=owner.lease, storage_guard=owner.guard, max_bytes=1024, media_kind=kind,
            )
    assert not (owner.root / "assets").exists()
