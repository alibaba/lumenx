"""Exercise the public preview/confirm contract against actual Studio storage."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Script
from src.utils.json_storage import CorruptStorageError, StorageError


TEXT = "开场。第一集结束。次日。第二集结束。"
EPISODES = [
    {"episode_number": 1, "title": "开场", "summary": "初见", "start_marker": "开场。", "end_marker": "第一集结束。"},
    {"episode_number": 2, "title": "次日", "summary": "重逢", "start_marker": "次日。", "end_marker": "第二集结束。"},
]


@pytest.fixture
def make_runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    processor = Mock()
    processor.split_into_episodes.return_value = deepcopy(EPISODES)
    processor.create_draft_script.side_effect = lambda title, text: Script(
        id=str(uuid.uuid4()), title=title, original_text=text, created_at=1, updated_at=1,
    )
    runtimes = []

    def make():
        runtime = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=processor)
        runtimes.append(runtime)
        return runtime

    yield make
    for runtime in runtimes:
        runtime.close()


def client_for(runtime):
    return TestClient(api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime))


def preview(client):
    response = client.post("/series/import/preview?suggested_episodes=2", files={
        "file": ("story.txt", TEXT.encode(), "text/plain"),
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"filename", "text_length", "suggested_episodes", "episodes", "import_id"}
    assert body["episodes"] == EPISODES
    return {"title": "故事", "description": "独立系列", "import_id": body["import_id"], "episodes": body["episodes"]}


def test_preview_identifier_confirms_the_exact_text_without_resending_it(make_runtime):
    runtime = make_runtime()
    with client_for(runtime) as client:
        request = preview(client)
        response = client.post("/series/import/confirm", json=request)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"series", "episodes"}
        assert body["series"]["episode_ids"] == [episode["id"] for episode in body["episodes"]]
        assert [runtime.scripts[ep["id"]].original_text for ep in body["episodes"]] == [
            "开场。第一集结束。", "次日。第二集结束。",
        ]
        runtime.script_processor.split_into_episodes.assert_called_once_with(TEXT, 2)


def test_confirm_replay_returns_original_ids_without_creating_duplicates(make_runtime):
    runtime = make_runtime()
    with client_for(runtime) as client:
        request = preview(client)
        first = client.post("/series/import/confirm", json=request)
        second = client.post("/series/import/confirm", json=request)
        assert second.status_code == 200, second.text
        assert second.json() == first.json()
        assert len(runtime.series_store) == 1
        assert len(runtime.scripts) == 2
        assert runtime.script_processor.create_draft_script.call_count == 2


def test_confirm_replay_rejects_changed_intent(make_runtime):
    runtime = make_runtime()
    with client_for(runtime) as client:
        request = preview(client)
        assert client.post("/series/import/confirm", json=request).status_code == 200
        response = client.post("/series/import/confirm", json={**request, "title": "another title"})
        assert response.status_code == 409, response.text
        assert len(runtime.series_store) == 1


def test_saved_preview_can_confirm_after_runtime_restart_without_new_analysis(make_runtime):
    runtime = make_runtime()
    with client_for(runtime) as client:
        request = preview(client)
    runtime.close()
    restored = make_runtime()
    with client_for(restored) as client:
        response = client.post("/series/import/confirm", json=request)
        assert response.status_code == 200, response.text
        restored.script_processor.split_into_episodes.assert_called_once()


@pytest.mark.parametrize("commit_visible", [False, True])
def test_save_failure_restarts_to_one_recoverable_result(make_runtime, monkeypatch, commit_visible):
    runtime = make_runtime()
    with client_for(runtime) as client:
        request = preview(client)
        original_commit = runtime._storage.commit

        def fail(payloads):
            if commit_visible:
                original_commit(payloads)
            raise StorageError("injected save failure", commit_uncertain=commit_visible)

        monkeypatch.setattr(runtime._storage, "commit", fail)
        response = client.post("/series/import/confirm", json=request)
        assert response.status_code == 503, response.text
        assert runtime.scripts == {}
        assert runtime.series_store == {}
    runtime.close()
    restored = make_runtime()
    with client_for(restored) as client:
        first = client.post("/series/import/confirm", json=request)
        assert first.status_code == 200, first.text
        second = client.post("/series/import/confirm", json=request)
        assert second.status_code == 200, second.text
        assert second.json() == first.json()
        assert len(restored.series_store) == 1
        assert len(restored.scripts) == 2
        restored.script_processor.split_into_episodes.assert_called_once()


def test_confirm_replay_survives_restart(make_runtime):
    runtime = make_runtime()
    with client_for(runtime) as client:
        request = preview(client)
        first = client.post("/series/import/confirm", json=request)
        assert first.status_code == 200
    runtime.close()
    restored = make_runtime()
    with client_for(restored) as client:
        replay = client.post("/series/import/confirm", json=request)
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()
        assert restored.script_processor.create_draft_script.call_count == 2


def test_read_only_receipt_recovers_a_lost_confirmation_after_restart(make_runtime):
    runtime = make_runtime()
    with client_for(runtime) as client:
        request = preview(client)
        ready = client.get(f"/series/import/{request['import_id']}")
        assert ready.status_code == 200, ready.text
        assert ready.json()["status"] == "preview"
        first = client.post("/series/import/confirm", json=request)
        assert first.status_code == 200
    runtime.close()
    restored = make_runtime()
    before = {path.name: path.read_bytes() for path in restored.data_dir.glob("*.json")}
    with client_for(restored) as client:
        recovered = client.get(f"/series/import/{request['import_id']}")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == {"import_id": request["import_id"], "status": "completed", "result": first.json()}
    assert {path.name: path.read_bytes() for path in restored.data_dir.glob("*.json")} == before
    assert restored.script_processor.create_draft_script.call_count == 2
    restored.script_processor.split_into_episodes.assert_called_once()


def test_parallel_duplicate_confirmations_share_one_transaction(make_runtime, monkeypatch):
    runtime = make_runtime()
    with client_for(runtime) as client:
        request = preview(client)
        commits = []
        original = runtime._storage.commit

        def record(payloads):
            commits.append(set(payloads))
            original(payloads)

        monkeypatch.setattr(runtime._storage, "commit", record)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post("/series/import/confirm", json=request), range(2)))
        assert [response.status_code for response in responses] == [200, 200]
        assert responses[0].json() == responses[1].json()
        assert commits == [{"projects.json", "series.json", "import_receipts.json"}]


def test_expired_preview_fails_without_creating_or_reanalyzing(make_runtime, monkeypatch):
    runtime = make_runtime()
    with client_for(runtime) as client:
        request = preview(client)
        import time
        now = time.time()
        monkeypatch.setattr("time.time", lambda: now + 25 * 60 * 60)
        response = client.post("/series/import/confirm", json=request)
        assert response.status_code == 410, response.text
        assert client.get(f"/series/import/{request['import_id']}").status_code == 410
        assert runtime.series_store == {}
        runtime.script_processor.split_into_episodes.assert_called_once()


def test_unknown_preview_never_falls_back_to_request_text(make_runtime):
    runtime = make_runtime()
    with client_for(runtime) as client:
        response = client.post("/series/import/confirm", json={
            "title": "stale", "import_id": "missing", "episodes": EPISODES,
        })
        assert response.status_code == 404, response.text
        assert runtime.series_store == {}


@pytest.mark.parametrize("identity", [{}, {"import_id": ""}, {"import_id": "   "}])
def test_legacy_text_only_confirmation_requires_a_preview_identity(make_runtime, identity):
    runtime = make_runtime()
    with client_for(runtime) as client:
        response = client.post("/series/import/confirm", json={
            "title": "legacy", "text": TEXT, "episodes": EPISODES, **identity,
        })
        assert response.status_code == 422, response.text
        assert any(item["loc"] == ["body", "import_id"] for item in response.json()["detail"])
        assert runtime.series_store == {}
        runtime.script_processor.split_into_episodes.assert_not_called()
        runtime.script_processor.create_draft_script.assert_not_called()


def test_corrupt_import_receipt_storage_is_preserved_and_blocks_startup(make_runtime):
    runtime = make_runtime()
    root = runtime.data_dir
    runtime.close()
    path = root / "import_receipts.json"
    path.write_text('{"broken": {}}')
    before = path.read_bytes()
    with pytest.raises(CorruptStorageError):
        make_runtime()
    assert path.read_bytes() == before


def test_preview_empty_text_is_client_error(make_runtime):
    with client_for(make_runtime()) as client:
        response = client.post("/series/import/preview", files={"file": ("empty.txt", b"  ", "text/plain")})
        assert response.status_code == 400, response.text


@pytest.mark.parametrize("changes", [{"episodes": []}, {"title": "  "}, {"episodes": [{"episode_number": True, "title": "bad"}]}])
def test_invalid_confirmation_cannot_consume_a_preview(make_runtime, changes):
    runtime = make_runtime()
    with client_for(runtime) as client:
        request = preview(client)
        response = client.post("/series/import/confirm", json={**request, **changes})
        assert response.status_code == 422, response.text
        assert client.post("/series/import/confirm", json=request).status_code == 200


def test_preview_text_limit_is_checked_before_llm_analysis(make_runtime):
    runtime = make_runtime()
    with client_for(runtime) as client:
        response = client.post("/series/import/preview", files={
            "file": ("oversized.txt", b"a" * (2 * 1024 * 1024 + 1), "text/plain"),
        })
        assert response.status_code == 413, response.text
        runtime.script_processor.split_into_episodes.assert_not_called()


def test_actual_llm_json_parser_preview_confirm_and_reload(make_runtime, monkeypatch):
    from src.apps.comic_gen.llm import ScriptProcessor
    from src.apps.comic_gen import llm_adapter
    transport = Mock(is_configured=True)
    transport.chat.return_value = '```json\n' + json.dumps({"episodes": EPISODES}, ensure_ascii=False) + '\n```'
    monkeypatch.setattr(llm_adapter, "LLMAdapter", lambda **kwargs: transport)
    runtime = make_runtime()
    runtime.script_processor = ScriptProcessor(output_root=str(runtime.data_dir))
    with client_for(runtime) as client:
        request = preview(client)
        result = client.post("/series/import/confirm", json=request)
        assert result.status_code == 200, result.text
        series_id = result.json()["series"]["id"]
    runtime.close()
    restored = make_runtime()
    assert restored.series_store[series_id].episode_ids == [ep["id"] for ep in result.json()["episodes"]]
    assert [restored.scripts[ep["id"]].original_text for ep in result.json()["episodes"]] == ["开场。第一集结束。", "次日。第二集结束。"]
    transport.chat.assert_called_once()
    assert TEXT in transport.chat.call_args.kwargs["messages"][0]["content"]


def test_preview_capacity_is_checked_before_analysis_and_expiration_releases_it(make_runtime, monkeypatch):
    import time
    runtime = make_runtime()
    monkeypatch.setattr(module, "MAX_PENDING_IMPORTS", 1)
    with client_for(runtime) as client:
        first = preview(client)
        full = client.post("/series/import/preview", files={"file": ("story.txt", TEXT.encode(), "text/plain")})
        assert full.status_code == 429
        runtime.script_processor.split_into_episodes.assert_called_once()
        now = time.time()
        monkeypatch.setattr(time, "time", lambda: now + 25 * 60 * 60)
        replacement = preview(client)
        assert list(runtime.import_receipts) == [replacement["import_id"]]
        assert client.get(f"/series/import/{first['import_id']}").status_code == 404


def test_parallel_previews_reserve_capacity_before_starting_paid_analysis(make_runtime, monkeypatch):
    runtime = make_runtime()
    monkeypatch.setattr(module, "MAX_PENDING_IMPORTS", 1)
    entered, release = threading.Event(), threading.Event()

    def split(*args):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return deepcopy(EPISODES)

    runtime.script_processor.split_into_episodes.side_effect = split
    with client_for(runtime) as client, ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, "/series/import/preview", files={"file": ("first.txt", TEXT.encode(), "text/plain")})
        assert entered.wait(5)
        try:
            second = client.post("/series/import/preview", files={"file": ("second.txt", TEXT.encode(), "text/plain")})
        finally:
            release.set()
        first_response = first.result(timeout=5)
        assert second.status_code == 429, second.text
        assert first_response.status_code == 200, first_response.text
        runtime.script_processor.split_into_episodes.assert_called_once()


def test_failed_preview_releases_its_process_local_capacity(make_runtime, monkeypatch):
    runtime = make_runtime()
    monkeypatch.setattr(module, "MAX_PENDING_IMPORTS", 1)
    runtime.script_processor.split_into_episodes.side_effect = [RuntimeError("controlled upstream failure"), deepcopy(EPISODES)]
    with client_for(runtime) as client:
        failed = client.post("/series/import/preview", files={"file": ("first.txt", TEXT.encode(), "text/plain")})
        assert failed.status_code == 500
        preview(client)
        assert runtime.script_processor.split_into_episodes.call_count == 2


@pytest.mark.parametrize("last_written", ["projects.json", "series.json", "import_receipts.json"])
def test_interruption_before_transaction_decision_restores_preview_and_relationships(make_runtime, monkeypatch, last_written):
    from src.utils import json_transactions
    runtime = make_runtime()
    with client_for(runtime) as client:
        request = preview(client)
        write = json_transactions.atomic_write_json

        class InterruptedProcess(BaseException):
            pass

        def interrupt(path, value):
            write(path, value)
            if path.name == last_written:
                raise InterruptedProcess()

        with monkeypatch.context() as fault:
            fault.setattr(json_transactions, "atomic_write_json", interrupt)
            with pytest.raises(InterruptedProcess):
                runtime.create_series_from_import(request["title"], "", request["episodes"], request["description"], import_id=request["import_id"])
    runtime.close()
    restored = make_runtime()
    assert restored.scripts == {} and restored.series_store == {}
    with client_for(restored) as client:
        assert client.get(f"/series/import/{request['import_id']}").json()["status"] == "preview"
        assert client.post("/series/import/confirm", json=request).status_code == 200
    assert len(restored.scripts) == 2 and len(restored.series_store) == 1
