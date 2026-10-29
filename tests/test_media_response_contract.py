"""Response-only media refs keep display signing out of provenance snapshots."""

import io
import json
import zipfile
from types import SimpleNamespace

from fastapi import UploadFile
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.apps.comic_gen.models import DialogueAudioOptions, DialogueAudioSnapshot, DubSource, Script, StoryboardFrame
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.comic_gen.project_package import build_package
from tests.test_dub_source_provenance import preview, runtime as dub_runtime
from tests.test_project_package import _resolved_assets

runtime = dub_runtime


def _install_signer(monkeypatch):
    signed = []

    def sign(value):
        signed.append(value)
        return f"https://signed.invalid/{value}?edge=1"

    monkeypatch.setattr(
        api,
        "OSSImageUploader",
        lambda *, env=None, output_root=None: SimpleNamespace(is_configured=True, sign_url_for_display=sign),
    )
    return signed


def _client(item):
    return TestClient(api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item))


def test_get_project_signs_display_media_and_keeps_source_snapshots_raw(runtime, monkeypatch):
    item, _ = runtime
    frame = item.scripts["p"].frames[0]
    frame.dialogue = "lumenx/spoken-line.mp3"
    frame.dialogue_instructions = "lumenx/historical-instruction.mp3"
    frame.audio_url = "lumenx/audio/current.mp3"
    frame.video_url = "lumenx/video/current.mp4"
    frame.dubbed_video_url = "lumenx/video/dubbed.mp4"
    frame.dub_applied_source = DubSource(
        schema_version=1,
        task_id="A",
        video_url="lumenx/video/source-a.mp4",
        audio_url="lumenx/audio/source-a.mp3",
        offset_ms=25,
    )
    frame.dialogue_audio_snapshot = DialogueAudioSnapshot(
        schema_version=1,
        audio_url="lumenx/audio/snapshot.mp3",
        options=DialogueAudioOptions(instructions="lumenx/not-media-instruction.mp3"),
        sources={"speed": "episode"},
        resolved_request={"voice_id": "lumenx/not-media-voice-id.mp3"},
        prepared={
            "text": "lumenx/not-media-text.mp3",
            "parameters": {"instruction": "lumenx/not-media-prepared.mp3"},
        },
    )
    item.scripts["p"].video_tasks[0].image_url = "lumenx/images/source.png"
    item.scripts["p"].video_tasks[0].video_url = "lumenx/video/task-a.mp4"
    item.scripts["p"].video_tasks[0].audio_url = "lumenx/audio/task-a.mp3"
    item.scripts["p"].video_tasks[0].reference_video_urls = ["lumenx/video/ref-a.mp4"]
    item.scripts["p"].video_tasks[0].reference_image_urls = ["lumenx/images/ref-a.png"]
    item._save_data()
    before = (item.data_dir / "projects.json").read_bytes()
    signed = _install_signer(monkeypatch)

    with _client(item) as client:
        response = client.get("/projects/p")

    assert response.status_code == 200, response.text
    data = response.json()
    frame_payload = data["frames"][0]
    task_payload = data["video_tasks"][0]
    assert (item.data_dir / "projects.json").read_bytes() == before

    assert frame_payload["audio_url"] == "https://signed.invalid/lumenx/audio/current.mp3?edge=1"
    assert frame_payload["video_url"] == "https://signed.invalid/lumenx/video/current.mp4?edge=1"
    assert frame_payload["dubbed_video_url"] == "https://signed.invalid/lumenx/video/dubbed.mp4?edge=1"
    assert frame_payload["media_refs"] == {
        "audio_url": "lumenx/audio/current.mp3",
        "video_url": "lumenx/video/current.mp4",
        "dubbed_video_url": "lumenx/video/dubbed.mp4",
    }

    assert task_payload["image_url"] == "https://signed.invalid/lumenx/images/source.png?edge=1"
    assert task_payload["video_url"] == "https://signed.invalid/lumenx/video/task-a.mp4?edge=1"
    assert task_payload["audio_url"] == "https://signed.invalid/lumenx/audio/task-a.mp3?edge=1"
    assert task_payload["reference_video_urls"] == ["https://signed.invalid/lumenx/video/ref-a.mp4?edge=1"]
    assert task_payload["reference_image_urls"] == ["https://signed.invalid/lumenx/images/ref-a.png?edge=1"]
    assert task_payload["media_refs"] == {
        "image_url": "lumenx/images/source.png",
        "video_url": "lumenx/video/task-a.mp4",
        "audio_url": "lumenx/audio/task-a.mp3",
        "reference_video_urls": ["lumenx/video/ref-a.mp4"],
        "reference_image_urls": ["lumenx/images/ref-a.png"],
    }

    assert frame_payload["dub_applied_source"] == {
        "schema_version": 1,
        "task_id": "A",
        "video_url": "lumenx/video/source-a.mp4",
        "audio_url": "lumenx/audio/source-a.mp3",
        "offset_ms": 25,
    }
    assert frame_payload["dialogue_audio_snapshot"]["audio_url"] == "lumenx/audio/snapshot.mp3"
    assert frame_payload["dialogue_audio_snapshot"]["options"]["instructions"] == "lumenx/not-media-instruction.mp3"
    assert frame_payload["dialogue_audio_snapshot"]["resolved_request"]["voice_id"] == "lumenx/not-media-voice-id.mp3"
    assert frame_payload["dialogue_audio_snapshot"]["prepared"]["text"] == "lumenx/not-media-text.mp3"
    assert frame_payload["dialogue"] == "lumenx/spoken-line.mp3"
    assert frame_payload["dialogue_instructions"] == "lumenx/historical-instruction.mp3"
    assert "lumenx/audio/snapshot.mp3" not in signed
    assert "lumenx/not-media-text.mp3" not in signed


def test_dub_apply_raw_ack_preserves_source_identity_and_adds_display_refs(runtime, monkeypatch):
    item, _ = runtime
    raw_video = item.data_dir / "lumenx/video/A.mp4"
    raw_audio = item.data_dir / "lumenx/audio/line.mp3"
    raw_video.parent.mkdir(parents=True, exist_ok=True)
    raw_audio.parent.mkdir(parents=True, exist_ok=True)
    raw_video.write_bytes(b"TAKE-A")
    raw_audio.write_bytes(b"AUDIO-OLD" * 200)
    frame = item.scripts["p"].frames[0]
    frame.video_url = "lumenx/video/A.mp4"
    frame.audio_url = "lumenx/audio/line.mp3"
    item.scripts["p"].video_tasks[0].video_url = "lumenx/video/A.mp4"
    item._save_data()
    signed = _install_signer(monkeypatch)
    preview(runtime, "A")

    with _client(item) as client:
        response = client.post("/projects/p/frames/f/dub/apply")

    assert response.status_code == 200, response.text
    data = response.json()
    frame_payload = data["frames"][0]
    source = frame_payload["dub_applied_source"]
    assert source["video_url"] == "lumenx/video/A.mp4"
    assert source["audio_url"] == "lumenx/audio/line.mp3"
    assert frame_payload["video_url"] == "https://signed.invalid/lumenx/video/A.mp4?edge=1"
    assert frame_payload["audio_url"] == "https://signed.invalid/lumenx/audio/line.mp3?edge=1"
    assert frame_payload["media_refs"]["video_url"] == "lumenx/video/A.mp4"
    assert frame_payload["media_refs"]["audio_url"] == "lumenx/audio/line.mp3"
    assert frame_payload["dubbed_video_url"].startswith("video/preview_f_")
    assert "lumenx/video/A.mp4" in signed
    assert "lumenx/audio/line.mp3" in signed


def test_audio_state_signs_only_display_audio_and_returns_media_ref(runtime, monkeypatch):
    item, _ = runtime
    frame = item.scripts["p"].frames[0]
    frame.dialogue = "lumenx/spoken-line.mp3"
    frame.dialogue_instructions = "lumenx/historical-instruction.mp3"
    frame.audio_url = "lumenx/audio/current.mp3"
    item._save_data()
    signed = _install_signer(monkeypatch)

    with _client(item) as client:
        response = client.get("/projects/p/frames/f/audio/state")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["audio_url"] == "https://signed.invalid/lumenx/audio/current.mp3?edge=1"
    assert data["media_refs"] == {"audio_url": "lumenx/audio/current.mp3"}
    assert data["dialogue"] == "lumenx/spoken-line.mp3"
    assert data["historical_instructions"] == "lumenx/historical-instruction.mp3"
    assert signed == ["lumenx/audio/current.mp3"]


def test_signed_response_ignores_incoming_sidecar_and_never_treats_ids_or_text_as_media(monkeypatch):
    signed = _install_signer(monkeypatch)
    response = api.signed_response({
        "id": "lumenx/id-is-text.mp3",
        "text": "lumenx/text-is-not-media.mp3",
        "video_url": "lumenx/video/display.mp4",
        "reference_video_urls": ["lumenx/video/ref.mp4", None, "plain-id"],
        "media_refs": {"video_url": "https://signed.invalid/stale.mp4?edge=old"},
        "dub_preview_source": {
            "schema_version": 1,
            "task_id": "A",
            "video_url": "lumenx/video/source.mp4",
            "audio_url": "lumenx/audio/source.mp3",
            "offset_ms": 0,
        },
    })

    data = json.loads(response.body)
    assert data["video_url"] == "https://signed.invalid/lumenx/video/display.mp4?edge=1"
    assert data["media_refs"] == {
        "video_url": "lumenx/video/display.mp4",
        "reference_video_urls": ["lumenx/video/ref.mp4", None, None],
    }
    assert data["id"] == "lumenx/id-is-text.mp3"
    assert data["text"] == "lumenx/text-is-not-media.mp3"
    assert data["dub_preview_source"]["video_url"] == "lumenx/video/source.mp4"
    assert data["dub_preview_source"]["audio_url"] == "lumenx/audio/source.mp3"
    assert signed == ["lumenx/video/display.mp4", "lumenx/video/ref.mp4"]


def test_upload_uses_explicit_url_media_field_without_global_url_signing(tmp_path, monkeypatch):
    signed = []

    class FakeUploader:
        is_configured = True

        def upload_image(self, _path):
            return "lumenx/video/uploaded.mp4"

        def sign_url_for_display(self, value):
            signed.append(value)
            return f"https://signed.invalid/{value}?edge=1"

    monkeypatch.setattr(api, "OSSImageUploader", FakeUploader)
    pipeline = SimpleNamespace(data_dir=tmp_path, assert_storage_available=lambda: None)
    response = api.upload_file(
        UploadFile(filename="clip.mp4", file=io.BytesIO(b"video")),
        active_pipeline=pipeline,
    )

    payload = json.loads(response.body)
    assert payload["url"] == "https://signed.invalid/lumenx/video/uploaded.mp4?edge=1"
    assert payload["media_refs"] == {"url": "lumenx/video/uploaded.mp4"}
    assert payload["local_url"].startswith("uploads/")
    assert payload["local_url"].endswith(".mp4")
    assert (tmp_path / payload["local_url"]).read_bytes() == b"video"
    assert signed == ["lumenx/video/uploaded.mp4"]


def test_export_existing_merge_delegates_to_merge_before_signing(tmp_path, monkeypatch):
    signed = []
    calls = []

    def sign(value):
        signed.append(value)
        return f"https://signed.invalid/{value}?edge=1"

    def merge_videos(script_id):
        calls.append(script_id)
        return SimpleNamespace(merged_video_url="lumenx/video/new-merge.mp4")

    monkeypatch.setattr(
        api,
        "OSSImageUploader",
        lambda: SimpleNamespace(is_configured=True, sign_url_for_display=sign),
    )
    pipeline = SimpleNamespace(
        data_dir=tmp_path,
        assert_storage_available=lambda: None,
        get_script=lambda _script_id: SimpleNamespace(merged_video_url="lumenx/video/old-merge.mp4"),
        merge_videos=merge_videos,
    )
    response = api.export_project("p", api.ExportRequest(), pipeline=pipeline)

    assert calls == ["p"]
    assert json.loads(response.body) == {
        "url": "https://signed.invalid/lumenx/video/new-merge.mp4?edge=1",
        "media_refs": {"url": "lumenx/video/new-merge.mp4"},
    }
    assert signed == ["lumenx/video/new-merge.mp4"]


def test_unconfigured_uploader_strips_incoming_media_refs_without_local_sidecar(monkeypatch):
    monkeypatch.setattr(api, "OSSImageUploader", lambda: SimpleNamespace(is_configured=False))

    response = api.signed_response({
        "video_url": "video/local.mp4",
        "child": {"video_url": "video/local2.mp4", "media_refs": {"video_url": "stale"}},
        "media_refs": {"video_url": "stale-or-client-controlled.mp4"},
    })

    assert json.loads(response.body) == {
        "video_url": "video/local.mp4",
        "child": {"video_url": "video/local2.mp4"},
    }


def test_metadata_subtrees_with_media_field_names_are_not_signed(monkeypatch):
    signed = _install_signer(monkeypatch)

    response = api.signed_response({
        "video_url": "lumenx/video/display.mp4",
        "settings": {"video_url": "lumenx/settings-looks-like-media.mp4"},
        "sources": {"audio_url": "lumenx/source-label.mp3"},
        "options": {"image_url": "lumenx/option-text.png"},
        "prepared": {"reference_video_urls": ["lumenx/prepared-text.mp4"]},
        "resolved_request": {"audio_url": "lumenx/request-text.mp3"},
    })

    data = json.loads(response.body)
    assert data["video_url"] == "https://signed.invalid/lumenx/video/display.mp4?edge=1"
    assert data["media_refs"] == {"video_url": "lumenx/video/display.mp4"}
    assert data["settings"]["video_url"] == "lumenx/settings-looks-like-media.mp4"
    assert data["sources"]["audio_url"] == "lumenx/source-label.mp3"
    assert data["options"]["image_url"] == "lumenx/option-text.png"
    assert data["prepared"]["reference_video_urls"] == ["lumenx/prepared-text.mp4"]
    assert data["resolved_request"]["audio_url"] == "lumenx/request-text.mp3"
    assert signed == ["lumenx/video/display.mp4"]


def test_opaque_snapshot_and_parameter_data_preserves_reserved_looking_keys(monkeypatch):
    _install_signer(monkeypatch)
    opaque = {
        "schema_version": 77,
        "video_url": "lumenx/video/original.mp4",
        "media_refs": {"literal": "future schema payload"},
        "nested": [{"media_refs": {"audio_url": "literal input"}}],
    }
    response = api.signed_response({
        "video_url": "lumenx/video/display.mp4",
        "dub_applied_source": opaque,
        "dialogue_audio_snapshot": opaque,
        "prepared": opaque,
    })
    payload = json.loads(response.body)
    assert payload["dub_applied_source"] == opaque
    assert payload["dialogue_audio_snapshot"] == opaque
    assert payload["prepared"] == opaque



class _UploadContractFakeUploader:
    def __init__(self, *, remote_object_key: str | None):
        self.remote_object_key = remote_object_key
        self.is_configured = remote_object_key is not None
        self.uploaded_paths: list[str] = []
        self.signed: list[str] = []

    def upload_image(self, path: str) -> str | None:
        self.uploaded_paths.append(path)
        return self.remote_object_key

    def sign_url_for_display(self, value: str) -> str:
        self.signed.append(value)
        return f"https://signed.invalid/{value}?edge=1"


def _upload_contract_runtime(tmp_path):
    runtime = ComicGenPipeline(data_dir=tmp_path / "data", recover_orphan_tasks=False, warmup_demucs=False)
    frame = StoryboardFrame(id="frame-1", scene_id="series-scene", action_description="shot")
    script = Script(id="project-1", title="Project", original_text="text", frames=[frame], created_at=1, updated_at=2)
    runtime.scripts[script.id] = script
    runtime._commit_storage("projects.json")
    return runtime


def test_http_upload_returns_local_url_for_oss_and_workbench_package_use(tmp_path, monkeypatch):
    runtime = _upload_contract_runtime(tmp_path)
    uploader = _UploadContractFakeUploader(remote_object_key="lumenx/uploads/remote-closing.png")
    monkeypatch.setattr(api, "_runtime_uploader", lambda _runtime: uploader)
    payload = b"closing-frame-bytes"

    with TestClient(api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime, warmup_demucs=False)) as client:
        upload = client.post("/upload", files={"file": ("closing.png", payload, "image/png")})
        assert upload.status_code == 200, upload.text
        data = upload.json()

        assert data["url"] == "https://signed.invalid/lumenx/uploads/remote-closing.png?edge=1"
        assert data["media_refs"] == {"url": "lumenx/uploads/remote-closing.png"}
        local_url = data["local_url"]
        assert local_url.startswith("uploads/")
        assert local_url.endswith(".png")
        assert (runtime.data_dir / local_url).read_bytes() == payload
        assert uploader.signed == ["lumenx/uploads/remote-closing.png"]
        assert uploader.uploaded_paths == [str(runtime.data_dir / local_url)]

        patch = client.patch(
            "/projects/project-1/frames/frame-1/workbench",
            json={"last_frame_url": local_url},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["last_frame_url"] == local_url

        (runtime.data_dir / "assets/characters").mkdir(parents=True, exist_ok=True)
        (runtime.data_dir / "assets/characters/hero.png").write_bytes(b"hero")
        (runtime.data_dir / "assets/scenes").mkdir(parents=True, exist_ok=True)
        (runtime.data_dir / "assets/scenes/room.png").write_bytes(b"room")
        script = runtime.read_committed_script("project-1")
        artifact = build_package(
            script,
            resolved_assets=_resolved_assets(),
            data_dir=runtime.data_dir,
            source_storage_id="source-storage",
            writer_lock=runtime._writer_lock,
        )
        with zipfile.ZipFile(io.BytesIO(artifact.data)) as archive:
            assert f"media/{local_url}" in set(archive.namelist())


def test_http_upload_returns_same_local_url_when_oss_disabled(tmp_path, monkeypatch):
    runtime = _upload_contract_runtime(tmp_path)
    uploader = _UploadContractFakeUploader(remote_object_key=None)
    monkeypatch.setattr(api, "_runtime_uploader", lambda _runtime: uploader)
    payload = b"local-closing-frame"

    with TestClient(api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime, warmup_demucs=False)) as client:
        upload = client.post("/upload", files={"file": ("closing.webp", payload, "image/webp")})
        assert upload.status_code == 200, upload.text
        data = upload.json()
        assert data["url"] == data["local_url"]
        assert data["local_url"].startswith("uploads/")
        assert data["local_url"].endswith(".webp")
        assert "media_refs" not in data
        assert (runtime.data_dir / data["local_url"]).read_bytes() == payload
        assert uploader.signed == []

        patch = client.patch(
            "/projects/project-1/frames/frame-1/workbench",
            json={"last_frame_url": data["local_url"]},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["last_frame_url"] == data["local_url"]
