"""Document derivation sidecars must use app-owned storage and strict revisions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Script


def _doc(*texts: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
            for text in texts
        ],
    }


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item.scripts["p"] = Script(id="p", title="Project", original_text="stored", created_at=1, updated_at=1)
    item._commit_storage("projects.json")
    with TestClient(api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)) as client:
        yield item, client
    item.close()


def _save_document(client: TestClient, *texts: str) -> int:
    response = client.post("/projects/p/document", json={"content": _doc(*texts), "expected_revision": 0})
    assert response.status_code == 200, response.text
    return response.json()["revision"]


def test_sync_derivation_requires_revisions_and_uses_app_sidecar(runtime):
    item, client = runtime
    doc_revision = _save_document(client, "INT. LAB - DAY")

    missing = client.post("/projects/p/sync_derivation", json={"word_count": 10})
    assert missing.status_code == 422

    written = client.post(
        "/projects/p/sync_derivation",
        json={
            "expected_revision": 0,
            "source_document_revision": doc_revision,
            "scenes": [{"title": "Lab"}],
            "characters": [{"name": "ADA"}],
            "locations": ["Lab"],
            "estimated_duration": 60,
            "word_count": 10,
            "confidence_score": 0.9,
        },
    )

    assert written.status_code == 200, written.text
    body = written.json()
    assert body["status"] == "ok"
    assert body["revision"] == 1
    assert body["source_document_revision"] == doc_revision
    sidecar = item.data_dir / "documents" / "p" / "derivation.json"
    assert sidecar.is_file()
    assert not (item.data_dir.parent / ".tron" / "comic" / "projects" / "p" / "derivation.json").exists()

    readback = client.get("/projects/p/sync_derivation")
    assert readback.status_code == 200
    assert readback.json()["revision"] == 1
    assert readback.json()["data"]["word_count"] == 10


def test_sync_derivation_rejects_stale_source_revision(runtime):
    item, client = runtime
    _save_document(client, "INT. LAB - DAY")

    stale = client.post(
        "/projects/p/sync_derivation",
        json={"expected_revision": 0, "source_document_revision": 0, "word_count": 10},
    )

    assert stale.status_code == 409
    assert not (item.data_dir / "documents" / "p" / "derivation.json").exists()


def test_sync_derivation_rejects_non_finite_client_payload_without_runtime_poison(runtime):
    item, client = runtime
    doc_revision = _save_document(client, "INT. LAB - DAY")
    body = '{"expected_revision":0,"source_document_revision":1,"scenes":[{"bad":NaN}]}'

    response = client.post("/projects/p/sync_derivation", content=body, headers={"content-type": "application/json"})

    assert response.status_code == 422
    assert not (item.data_dir / "documents" / "p" / "derivation.json").exists()
    ok = client.post(
        "/projects/p/sync_derivation",
        json={"expected_revision": 0, "source_document_revision": doc_revision, "word_count": 10},
    )
    assert ok.status_code == 200, ok.text


def test_derivation_sidecar_requires_required_metadata(runtime):
    item, client = runtime
    path = item.data_dir / "documents" / "p" / "derivation.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"schema_version":1,"project_id":"p","revision":1,"data":{}}',
        encoding="utf-8",
    )

    response = client.get("/projects/p/sync_derivation")

    assert response.status_code == 503
    assert response.json()["code"] == "storage_unavailable"


def test_shot_blocks_sidecar_requires_required_metadata(runtime):
    item, client = runtime
    path = item.data_dir / "documents" / "p" / "shot_blocks.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"schema_version":1,"project_id":"p","revision":1,"updated_at":"2026-10-10T00:00:00Z","blocks":{}}',
        encoding="utf-8",
    )

    response = client.get("/projects/p/shot_blocks")

    assert response.status_code == 503
    assert response.json()["code"] == "storage_unavailable"


def test_confirm_shot_block_requires_revisions_and_uses_app_sidecar(runtime):
    item, client = runtime
    doc_revision = _save_document(client, "SHOT ONE")

    missing = client.post("/projects/p/shot_blocks/shot-a/confirm", json={"description": "wide"})
    assert missing.status_code == 422

    confirmed = client.post(
        "/projects/p/shot_blocks/shot-a/confirm",
        json={
            "expected_revision": 0,
            "source_document_revision": doc_revision,
            "shot_type": "wide",
            "camera_movement": "pan",
            "duration": 4,
            "description": "wide lab shot",
            "characters": ["ADA"],
        },
    )

    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["shot_id"] == "shot-a"
    assert body["status"] == "confirmed"
    assert body["revision"] == 1
    assert body["source_document_revision"] == doc_revision
    sidecar = item.data_dir / "documents" / "p" / "shot_blocks.json"
    assert sidecar.is_file()

    readback = client.get("/projects/p/shot_blocks")
    assert readback.status_code == 200
    assert readback.json()["revision"] == 1
    assert readback.json()["blocks"]["shot-a"]["description"] == "wide lab shot"


def test_confirm_shot_block_preserves_per_block_source_revision(runtime):
    _, client = runtime
    first_doc_revision = _save_document(client, "SHOT ONE")
    first = client.post(
        "/projects/p/shot_blocks/shot-a/confirm",
        json={"expected_revision": 0, "source_document_revision": first_doc_revision, "description": "old"},
    )
    assert first.status_code == 200, first.text
    second_doc = client.post("/projects/p/document", json={"content": _doc("SHOT TWO"), "expected_revision": 1})
    assert second_doc.status_code == 200, second_doc.text
    second_doc_revision = second_doc.json()["revision"]

    second = client.post(
        "/projects/p/shot_blocks/shot-b/confirm",
        json={"expected_revision": 1, "source_document_revision": second_doc_revision, "description": "new"},
    )

    assert second.status_code == 200, second.text
    blocks = client.get("/projects/p/shot_blocks").json()["blocks"]
    assert blocks["shot-a"]["source_document_revision"] == first_doc_revision
    assert blocks["shot-b"]["source_document_revision"] == second_doc_revision


def test_confirm_shot_block_rejects_unsafe_shot_id(runtime):
    item, client = runtime
    doc_revision = _save_document(client, "SHOT ONE")

    response = client.post(
        "/projects/p/shot_blocks/bad:shot/confirm",
        json={"expected_revision": 0, "source_document_revision": doc_revision},
    )

    assert response.status_code == 400
    assert not (item.data_dir / "documents" / "p" / "shot_blocks.json").exists()


def test_derive_gaps_reads_app_document_not_legacy_script(runtime, tmp_path, monkeypatch):
    item, client = runtime
    _save_document(client, "APP OWNED TEXT")
    legacy_dir = tmp_path / "legacy" / "comic" / "projects" / "p"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "script.json").write_text('{"scenes":[{"description":"LEGACY TEXT"}]}', encoding="utf-8")
    monkeypatch.setattr(api.Path, "home", lambda: tmp_path / "legacy")
    captured = {}

    def fake_call(text_blocks, gaps, already_extracted, *, env):
        assert env is client.app.state.runtime_environment.snapshot()
        captured["text_blocks"] = text_blocks
        return [api.DeriveGapEntity(type="prop", name="Scanner", confidence=0.8)]

    monkeypatch.setattr(api, "_call_llm_for_gaps", fake_call)
    response = client.post("/projects/p/derive_gaps", json={"gaps": ["props"]})

    assert response.status_code == 200, response.text
    assert captured["text_blocks"] == ["APP OWNED TEXT"]
    assert response.json()["entities"][0]["name"] == "Scanner"


def test_derive_gaps_explicit_text_requires_committed_project_without_legacy_dir(runtime, monkeypatch):
    _, client = runtime
    captured = {}

    def fake_call(text_blocks, gaps, already_extracted, *, env):
        assert env is client.app.state.runtime_environment.snapshot()
        captured["text_blocks"] = text_blocks
        return [api.DeriveGapEntity(type="location", name="Rooftop", confidence=0.7)]

    monkeypatch.setattr(api, "_call_llm_for_gaps", fake_call)
    response = client.post("/projects/p/derive_gaps", json={"raw_text_blocks": ["EXPLICIT TEXT"], "gaps": ["locations"]})
    missing = client.post("/projects/missing/derive_gaps", json={"raw_text_blocks": ["EXPLICIT TEXT"]})

    assert response.status_code == 200, response.text
    assert captured["text_blocks"] == ["EXPLICIT TEXT"]
    assert missing.status_code == 404


def test_derive_gaps_has_no_cross_app_global_cache(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    a = module.ComicGenPipeline(data_dir=tmp_path / "a", script_processor=Mock())
    b = module.ComicGenPipeline(data_dir=tmp_path / "b", script_processor=Mock())
    for item in (a, b):
        item.scripts["p"] = Script(id="p", title="Project", original_text="stored", created_at=1, updated_at=1)
        item._commit_storage("projects.json")
    calls = []

    def fake_call(text_blocks, gaps, already_extracted, *, env):
        expected = a if not calls else b
        assert env is expected.runtime_environment.snapshot()
        calls.append(text_blocks)
        return [api.DeriveGapEntity(type="prop", name=f"Result {len(calls)}", confidence=0.8)]

    monkeypatch.setattr(api, "_call_llm_for_gaps", fake_call)
    try:
        with TestClient(api.create_app(data_dir=a.data_dir, load_config=False, pipeline_instance=a)) as ca, TestClient(
            api.create_app(data_dir=b.data_dir, load_config=False, pipeline_instance=b)
        ) as cb:
            ra = ca.post("/projects/p/derive_gaps", json={"raw_text_blocks": ["same"], "gaps": ["props"]})
            rb = cb.post("/projects/p/derive_gaps", json={"raw_text_blocks": ["same"], "gaps": ["props"]})
        assert ra.status_code == 200
        assert rb.status_code == 200
        assert ra.json()["entities"][0]["name"] == "Result 1"
        assert rb.json()["entities"][0]["name"] == "Result 2"
        assert len(calls) == 2
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "{}",
        '{"entities":null}',
        '[{"type":"other","name":"A","confidence":0.5}]',
        '[{"type":"prop","name":"   ","confidence":0.5}]',
        '[{"type":"prop","name":"A","confidence":true}]',
        '[{"type":"prop","name":"A","confidence":"0.5"}]',
        '[{"type":"prop","name":"A","confidence":2}]',
        '[{"type":"prop","name":"A"}]',
        '[{"type":"prop","name":"A","confidence":0.5,"description":{}}]',
        '[{"type":"prop","name":"A","confidence":0.5,"scene_index":{}}]',
        '[{"type":"prop","name":"A","confidence":0.5,"scene_index":-1}]',
        '[{"type":"prop","name":"A","confidence":0.5,"scene_index":true}]',
        "[{\"name\":\"Missing type\",\"confidence\":0.5}]",
        "[{\"type\":\"prop\",\"name\":\"Bad confidence\",\"confidence\":NaN}]",
    ],
)
def test_derive_gaps_malformed_llm_response_is_not_fake_success(runtime, monkeypatch, payload):
    _, client = runtime
    from src.apps.comic_gen import llm_adapter

    class FakeAdapter:
        is_configured = True

        def __init__(self, *, env):
            assert env is client.app.state.runtime_environment.snapshot()

        def _get_default_model(self):
            return "fake-model"

        def chat(self, **_kwargs):
            return payload

    monkeypatch.setattr(llm_adapter, "LLMAdapter", FakeAdapter)

    response = client.post("/projects/p/derive_gaps", json={"raw_text_blocks": ["TEXT"], "gaps": ["props"]})

    assert response.status_code == 502
    assert "Malformed" in response.json()["detail"]


def test_derive_gaps_unconfigured_llm_is_not_fake_success(runtime, monkeypatch):
    _, client = runtime
    from src.apps.comic_gen import llm_adapter

    class FakeAdapter:
        is_configured = False

        def __init__(self, *, env):
            assert env is client.app.state.runtime_environment.snapshot()

    monkeypatch.setattr(llm_adapter, "LLMAdapter", FakeAdapter)

    response = client.post("/projects/p/derive_gaps", json={"raw_text_blocks": ["TEXT"], "gaps": ["props"]})

    assert response.status_code == 502
    assert "not configured" in response.json()["detail"]


def test_derive_gaps_forwards_request_timeout_and_disables_retries(runtime, monkeypatch):
    _, client = runtime
    from src.apps.comic_gen import llm_adapter
    calls = []

    class FakeAdapter:
        is_configured = True

        def __init__(self, *, env):
            assert env is client.app.state.runtime_environment.snapshot()

        def _get_default_model(self):
            return "fake-model"

        def chat(self, **kwargs):
            calls.append(kwargs)
            return '[{"type":"prop","name":"Scanner","confidence":0.5}]'

    monkeypatch.setattr(llm_adapter, "LLMAdapter", FakeAdapter)

    response = client.post("/projects/p/derive_gaps", json={"raw_text_blocks": ["TEXT"], "gaps": ["props"]})

    assert response.status_code == 200, response.text
    assert calls[0]["model"] == "fake-model"
    assert calls[0]["timeout"] == 2
    assert calls[0]["max_retries"] == 0


@pytest.mark.parametrize("payload", ['[]', '{"entities":[]}', '{"items":[]}'])
def test_derive_gaps_accepts_explicit_empty_results(runtime, monkeypatch, payload):
    _, client = runtime
    from src.apps.comic_gen import llm_adapter
    adapter = Mock(is_configured=True)
    adapter.chat.return_value = payload
    monkeypatch.setattr(llm_adapter, "LLMAdapter", lambda *, env: adapter)
    response = client.post("/projects/p/derive_gaps", json={"raw_text_blocks": ["TEXT"]})
    assert response.status_code == 200
    assert response.json()["entities"] == []


def test_malformed_document_tree_is_rejected_before_gap_provider(runtime, monkeypatch):
    _, client = runtime
    save = client.post("/projects/p/document", json={"content": {"type": "doc", "content": 7}, "expected_revision": 0})
    assert save.status_code == 200
    provider = Mock()
    monkeypatch.setattr(api, "_call_llm_for_gaps", provider)
    response = client.post("/projects/p/derive_gaps", json={})
    assert response.status_code == 400
    provider.assert_not_called()


@pytest.mark.parametrize("options", [{}, {"timeout": 2, "max_retries": 0}])
def test_llm_request_options_do_not_change_legacy_client_behavior(monkeypatch, options):
    from src.apps.comic_gen.llm_adapter import LLMAdapter
    adapter = LLMAdapter()
    client = Mock()
    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="done"))])
    client.chat.completions.create.return_value = reply
    client.with_options.return_value.chat.completions.create.return_value = reply
    monkeypatch.setattr(adapter, "_get_client", lambda: client)
    assert adapter.chat([{"role": "user", "content": "hello"}], model="fixture", **options) == "done"
    if options:
        client.with_options.assert_called_once_with(timeout=2, max_retries=0)
        client.chat.completions.create.assert_not_called()
    else:
        client.with_options.assert_not_called()
        client.chat.completions.create.assert_called_once()


@pytest.mark.parametrize("route,filename,body", [
    ("sync_derivation", "derivation.json", {"word_count": 2}),
    ("shot_blocks/shot-a/confirm", "shot_blocks.json", {"description": "new"}),
])
def test_failed_sidecar_commit_preserves_previous_file_and_blocks_writes(runtime, monkeypatch, route, filename, body):
    item, client = runtime
    from src.apps.comic_gen import documents
    from src.utils.json_storage import StorageError
    doc_revision = _save_document(client, "TEXT")
    response = client.post(f"/projects/p/{route}", json={"expected_revision": 0, "source_document_revision": doc_revision})
    assert response.status_code == 200
    path = item.data_dir / "documents" / "p" / filename
    before = path.read_bytes()
    monkeypatch.setattr(documents, "atomic_write_json", Mock(side_effect=StorageError("disk full")))
    failed = client.post(f"/projects/p/{route}", json={"expected_revision": response.json()["revision"], "source_document_revision": doc_revision, **body})
    assert failed.status_code == 503
    assert path.read_bytes() == before
    assert json.loads(before)["revision"] == response.json()["revision"]
    with pytest.raises(StorageError):
        item.assert_storage_available()
