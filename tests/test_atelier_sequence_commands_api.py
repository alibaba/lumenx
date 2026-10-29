from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.apps.atelier.api import router
from src.apps.atelier.models import AtelierAudioTrack, AtelierNode, AtelierProject
from src.apps.atelier.service import AtelierService
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError, StorageError


def _tool(name: str) -> str:
    env_name = "LUMENX_TEST_FFMPEG" if name == "ffmpeg" else "LUMENX_TEST_FFPROBE"
    path = os.environ.get(env_name) or shutil.which(name)
    if not path:
        import pytest

        pytest.skip(f"{name} not available")
    return path


def _make_clip(
    path: Path, *, color: str = "red", duration: float = 0.7, frequency: int = 440
) -> None:
    ffmpeg = _tool("ffmpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=64x48:d={duration}:r=25",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}:sample_rate=44100",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def _make_audio(path: Path, *, duration: float = 0.7, frequency: int = 330) -> None:
    ffmpeg = _tool("ffmpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}:sample_rate=44100",
            "-c:a",
            "mp3",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def make_client(tmp_path: Path):
    from fastapi.testclient import TestClient

    storage = AtelierStorage(tmp_path)
    _make_clip(tmp_path / "uploads" / "clip.mp4")
    _make_audio(tmp_path / "uploads" / "bed.mp3")
    storage.create_project(
        AtelierProject(
            id="p1",
            title="Sequence",
            created_at=1.0,
            updated_at=1.0,
            nodes=[
                AtelierNode(
                    id="video-1",
                    project_id="p1",
                    type="video",
                    status="completed",
                    media_urls=["uploads/clip.mp4"],
                ),
                AtelierNode(
                    id="audio-1",
                    project_id="p1",
                    type="audio",
                    status="completed",
                    media_urls=["uploads/bed.mp3"],
                ),
            ],
            audio_tracks=[AtelierAudioTrack(id="bed", node_id="audio-1")],
        )
    )
    app = FastAPI()
    app.state.atelier_service = AtelierService(storage, clock=lambda: 20.0)

    @app.exception_handler(StorageError)
    async def storage_error_response(_request, exc: StorageError):
        conflict = isinstance(exc, StorageConflictError)
        return JSONResponse(
            {
                "code": "storage_conflict" if conflict else "storage_unavailable",
                "commit_uncertain": exc.commit_uncertain,
            },
            status_code=409 if conflict else 503,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_response(_request, _exc: RequestValidationError):
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    app.include_router(router, prefix="/atelier")
    return TestClient(app), storage


def test_replace_sequence_validates_freezes_and_preserves_or_clears_audio_tracks(tmp_path):
    client, storage = make_client(tmp_path)

    saved = client.put(
        "/atelier/projects/p1/sequence",
        json={
            "expected_revision": 0,
            "entries": [
                {
                    "id": "clip-1",
                    "parentId": "video-1",
                    "trimStart": 0,
                    "trimEnd": 0.4,
                    "transition": None,
                }
            ],
        },
    )

    assert saved.status_code == 200
    body = saved.json()
    assert body["revision"] == 1
    assert body["sequence"] == [
        {
            "parentId": "video-1",
            "candidateId": None,
            "id": "clip-1",
            "trimStart": 0.0,
            "trimEnd": 0.4,
            "gain": 1.0,
            "transition": {"kind": "cut", "duration": 0.0},
        }
    ]
    assert body["audio_tracks"] == [
        {
            "id": "bed",
            "node_id": "audio-1",
            "start": 0.0,
            "trim_start": None,
            "trim_end": None,
            "gain": 1.0,
            "loop": False,
        }
    ]

    cleared = client.put(
        "/atelier/projects/p1/sequence",
        json={"expected_revision": 1, "entries": [], "audio_tracks": []},
    )

    assert cleared.status_code == 200
    assert cleared.json()["revision"] == 2
    assert cleared.json()["sequence"] == []
    assert cleared.json()["audio_tracks"] == []
    assert storage.get_project("p1").sequence == []


def test_replace_sequence_rejects_null_audio_invalid_refs_and_stale_revision_without_changes(
    tmp_path,
):
    client, storage = make_client(tmp_path)
    original = storage.get_project("p1").model_dump(mode="json")

    null_audio = client.put(
        "/atelier/projects/p1/sequence",
        json={"expected_revision": 0, "entries": [], "audio_tracks": None},
    )
    assert null_audio.status_code == 422
    assert storage.get_project("p1").model_dump(mode="json") == original

    stale = client.put(
        "/atelier/projects/p1/sequence",
        json={"expected_revision": 9, "entries": []},
    )
    assert stale.status_code == 409
    assert storage.get_project("p1").model_dump(mode="json") == original

    invalid = client.put(
        "/atelier/projects/p1/sequence",
        json={
            "expected_revision": 0,
            "entries": [
                {"id": "missing", "parentId": "missing"},
                {"id": "bad-trim", "parentId": "video-1", "trimStart": 1, "trimEnd": 0.5},
            ],
        },
    )
    assert invalid.status_code == 422
    detail = invalid.json()["detail"]
    assert detail["code"] == "invalid_sequence"
    assert [issue["code"] for issue in detail["report"]["errors"]] == [
        "missing_node",
        "invalid_trim",
    ]
    assert storage.get_project("p1").model_dump(mode="json") == original


def test_sequence_preflight_reports_against_committed_project_without_changing_revision(tmp_path):
    client, storage = make_client(tmp_path)

    response = client.post(
        "/atelier/projects/p1/sequence/preflight",
        json={
            "entries": [
                {"id": "missing", "parentId": "missing"},
                {"id": "bad-trim", "parentId": "video-1", "trimStart": 1, "trimEnd": 0.5},
            ],
            "audio_tracks": [],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == "p1"
    assert body["revision"] == 0
    assert [issue["clip_id"] for issue in body["report"]["errors"]] == ["missing", "bad-trim"]
    assert [issue["code"] for issue in body["report"]["errors"]] == ["missing_node", "invalid_trim"]
    assert storage.get_project("p1").revision == 0

    missing = client.post("/atelier/projects/missing/sequence/preflight", json={"entries": []})
    assert missing.status_code == 404


def test_sequence_preflight_empty_saved_sequence_reports_export_readiness_error_without_revision_change(tmp_path):
    client, storage = make_client(tmp_path)

    response = client.post(
        "/atelier/projects/p1/sequence/preflight",
        json={"entries": []},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == "p1"
    assert body["revision"] == 0
    assert [issue["code"] for issue in body["report"]["errors"]] == ["empty_sequence"]
    assert storage.get_project("p1").revision == 0


def test_replace_sequence_maps_storage_failure_to_503_without_claiming_success(
    tmp_path, monkeypatch
):
    client, storage = make_client(tmp_path)
    original = storage.get_project("p1").model_dump(mode="json")

    def fail_commit(_next_projects):
        raise StorageError("disk unavailable")

    monkeypatch.setattr(storage, "_commit", fail_commit)
    response = client.put(
        "/atelier/projects/p1/sequence",
        json={
            "expected_revision": 0,
            "entries": [{"id": "clip-1", "parentId": "video-1", "trimStart": 0, "trimEnd": 0.4}],
            "audio_tracks": [],
        },
    )

    assert response.status_code == 503
    assert response.json()["code"] == "storage_unavailable"
    assert storage.get_project("p1").model_dump(mode="json") == original
