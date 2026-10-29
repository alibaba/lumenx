from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierProjectNotFound, AtelierStorage
from src.utils.json_storage import DirectoryWriterLock, StorageConflictError, StorageError


def make_project(project_id: str = "atelier-1", *, title: str = "First") -> AtelierProject:
    return AtelierProject(id=project_id, title=title, created_at=1.0, updated_at=1.0)


def db_path(root: Path) -> Path:
    return root / "atelier_projects.json"


def load_raw(root: Path) -> dict:
    return json.loads(db_path(root).read_text(encoding="utf-8"))


def test_committed_reads_are_deep_copies_and_noop_commands_do_not_bump_revision(tmp_path):
    store = AtelierStorage(tmp_path)
    created = store.create_project(make_project())
    assert created.revision == 0

    leaked = store.get_project("atelier-1")
    leaked.title = "mutated outside storage"
    leaked.nodes.append(AtelierNode(id="local-only", project_id="atelier-1"))

    assert store.get_project("atelier-1").title == "First"
    assert store.get_project("atelier-1").nodes == []

    saved = store.apply_project_command("atelier-1", expected_revision=0, command=lambda project: None)
    assert saved.revision == 0
    assert store.get_project("atelier-1").revision == 0

    changed = store.apply_project_command(
        "atelier-1",
        expected_revision=0,
        command=lambda project: setattr(project, "title", "Changed"),
    )
    assert changed.revision == 1
    assert store.get_project("atelier-1").title == "Changed"

    with pytest.raises(StorageConflictError):
        store.apply_project_command("atelier-1", expected_revision=0, command=lambda project: setattr(project, "title", "late"))


def test_standalone_and_borrowed_writer_lease_lifecycle(tmp_path):
    first = AtelierStorage(tmp_path)
    with pytest.raises(StorageConflictError):
        AtelierStorage(tmp_path)
    first.close()

    parent = DirectoryWriterLock(tmp_path).acquire()
    borrowed = AtelierStorage(tmp_path, writer_lock=parent)
    borrowed.close()
    parent.assert_owned()
    parent.close()
    with pytest.raises(StorageConflictError):
        borrowed.list_projects()


def test_parent_closed_and_path_replaced_block_operations(tmp_path):
    parent = DirectoryWriterLock(tmp_path).acquire()
    store = AtelierStorage(tmp_path, writer_lock=parent)
    parent.close()
    with pytest.raises(StorageConflictError):
        store.create_project(make_project())

    original = tmp_path / "owned"
    replacement = tmp_path / "replacement"
    original.mkdir()
    replacement.mkdir()
    parent = DirectoryWriterLock(original).acquire()
    store = AtelierStorage(original, writer_lock=parent)
    original.rename(tmp_path / "old-owned")
    replacement.rename(original)
    with pytest.raises(StorageConflictError):
        store.list_projects()
    parent.close()


def test_uncertain_commit_seals_store_and_restart_reads_actual_file(tmp_path, monkeypatch):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())

    from src.apps.atelier import storage as storage_module

    original = storage_module.atomic_write_json

    def write_then_uncertain(path, value):
        original(path, value)
        raise StorageError("directory fsync failed", commit_uncertain=True)

    monkeypatch.setattr(storage_module, "atomic_write_json", write_then_uncertain)
    with pytest.raises(StorageError) as exc_info:
        store.apply_project_command(
            "atelier-1",
            expected_revision=0,
            command=lambda project: setattr(project, "title", "Maybe committed"),
        )
    assert exc_info.value.commit_uncertain is True
    with pytest.raises(StorageError, match="restart"):
        store.get_project("atelier-1")

    store.close()
    restarted = AtelierStorage(tmp_path)
    assert restarted.get_project("atelier-1").title == "Maybe committed"
    assert restarted.get_project("atelier-1").revision == 1


def test_unknown_future_fields_and_invalid_json_do_not_overwrite_existing_bytes(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    before = db_path(tmp_path).read_bytes()
    store.close()

    flat = load_raw(tmp_path)
    flat["future-field"] = {"must": "not disappear"}
    db_path(tmp_path).write_text(json.dumps(flat), encoding="utf-8")
    future_bytes = db_path(tmp_path).read_bytes()
    with pytest.raises(StorageError):
        AtelierStorage(tmp_path)
    assert db_path(tmp_path).read_bytes() == future_bytes

    db_path(tmp_path).write_bytes(before)
    bad_duplicate = '{"atelier-1":{"id":"atelier-1","title":"A","created_at":1,"updated_at":1},"atelier-1":{}}\n'
    db_path(tmp_path).write_text(bad_duplicate, encoding="utf-8")
    with pytest.raises(StorageError):
        AtelierStorage(tmp_path)
    assert db_path(tmp_path).read_text(encoding="utf-8") == bad_duplicate

    db_path(tmp_path).write_text('{"p":{"id":"p","title":"P","created_at":1,"updated_at":1,"revision":NaN}}\n')
    with pytest.raises(StorageError):
        AtelierStorage(tmp_path)


def test_legacy_flat_file_and_nested_typed_unknown_fields_rejected_but_node_data_remains_opaque(tmp_path):
    store = AtelierStorage(tmp_path)
    project = make_project()
    project.nodes.append(AtelierNode(id="node-1", project_id="atelier-1", data={"future_payload": {"kept": True}}))
    store.create_project(project)
    store.close()

    raw = load_raw(tmp_path)
    raw["atelier-1"]["nodes"][0]["unknown_typed_field"] = "reject"
    bad_bytes = json.dumps(raw).encode("utf-8")
    db_path(tmp_path).write_bytes(bad_bytes)
    with pytest.raises(StorageError):
        AtelierStorage(tmp_path)
    assert db_path(tmp_path).read_bytes() == bad_bytes

    del raw["atelier-1"]["nodes"][0]["unknown_typed_field"]
    raw["atelier-1"]["agent_turns"] = [{"id": "turn-1", "project_id": "atelier-1", "unknown_turn_field": True}]
    turn_bad_bytes = json.dumps(raw).encode("utf-8")
    db_path(tmp_path).write_bytes(turn_bad_bytes)
    with pytest.raises(StorageError):
        AtelierStorage(tmp_path)
    assert db_path(tmp_path).read_bytes() == turn_bad_bytes

    raw["atelier-1"]["agent_turns"] = [{"id": "turn-1", "project_id": "atelier-1"}]
    raw["atelier-1"]["nodes"][0]["data"]["future_payload"]["still"] = "opaque"
    db_path(tmp_path).write_text(json.dumps(raw), encoding="utf-8")
    restored = AtelierStorage(tmp_path)
    assert restored.get_project("atelier-1").nodes[0].data["future_payload"] == {"kept": True, "still": "opaque"}


def test_schema_owner_and_duplicate_node_validation_happens_before_write(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    before = db_path(tmp_path).read_bytes()

    with pytest.raises(StorageError):
        store.apply_project_command(
            "atelier-1",
            expected_revision=0,
            command=lambda project: project.nodes.append(AtelierNode(id="wrong-owner", project_id="other")),
        )
    assert db_path(tmp_path).read_bytes() == before
    assert store.get_project("atelier-1").nodes == []

    with pytest.raises(StorageError):
        store.apply_project_command(
            "atelier-1",
            expected_revision=0,
            command=lambda project: project.nodes.extend([
                AtelierNode(id="dup", project_id="atelier-1"),
                AtelierNode(id="dup", project_id="atelier-1"),
            ]),
        )
    assert db_path(tmp_path).read_bytes() == before
    assert store.get_project("atelier-1").nodes == []

    with pytest.raises(StorageError):
        store.create_project(make_project("other-id", title="Other").model_copy(update={"id": "atelier-1"}))


def test_concurrent_same_revision_commands_allow_one_success(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())

    def change(title: str):
        return store.apply_project_command(
            "atelier-1",
            expected_revision=0,
            command=lambda project: setattr(project, "title", title),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda title: _capture_result(lambda: change(title)), ["A", "B"]))

    successes = [result for ok, result in results if ok]
    failures = [result for ok, result in results if not ok]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], StorageConflictError)
    assert store.get_project("atelier-1").revision == 1
    assert store.get_project("atelier-1").title in {"A", "B"}


def _capture_result(call):
    try:
        return True, call()
    except Exception as exc:  # noqa: BLE001 - tests assert exact exception type in result
        return False, exc


def test_delete_and_late_write_do_not_resurrect_project(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    stale = store.get_project("atelier-1")
    store.delete_project("atelier-1", expected_revision=0)

    with pytest.raises(AtelierProjectNotFound):
        store.get_project("atelier-1")
    with pytest.raises(StorageConflictError):
        store.apply_project_command(
            stale.id,
            expected_revision=stale.revision,
            command=lambda project: setattr(project, "title", "resurrected"),
        )
    store.close()
    with pytest.raises(AtelierProjectNotFound):
        AtelierStorage(tmp_path).get_project("atelier-1")


def test_borrowed_writer_lock_must_match_store_data_dir_and_remains_open(tmp_path):
    parent_root = tmp_path / "parent"
    child_root = tmp_path / "child"
    parent = DirectoryWriterLock(parent_root).acquire()
    with pytest.raises(StorageConflictError):
        AtelierStorage(child_root, writer_lock=parent)
    parent.assert_owned()
    parent.close()


def test_reentrant_command_cannot_overwrite_nested_write_or_resurrect_delete(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())

    def nested_write(project):
        store.apply_project_command(
            "atelier-1",
            expected_revision=0,
            command=lambda nested: setattr(nested, "title", "Nested"),
        )
        project.title = "Outer"

    with pytest.raises(StorageConflictError):
        store.apply_project_command("atelier-1", expected_revision=0, command=nested_write)
    assert store.get_project("atelier-1").title == "First"
    assert store.get_project("atelier-1").revision == 0

    def nested_delete(project):
        store.delete_project("atelier-1", expected_revision=0)
        project.title = "Resurrected"

    with pytest.raises(StorageConflictError):
        store.apply_project_command("atelier-1", expected_revision=0, command=nested_delete)
    assert store.get_project("atelier-1").title == "First"


def test_uncertain_commit_latches_before_failing_mark_hook(tmp_path, monkeypatch):
    marked = []

    def mark(error):
        marked.append(error)
        raise RuntimeError("mark failed")

    store = AtelierStorage(tmp_path, mark_storage_failed=mark)
    store.create_project(make_project())
    from src.apps.atelier import storage as storage_module

    original = storage_module.atomic_write_json

    def write_then_uncertain(path, value):
        original(path, value)
        raise StorageError("directory fsync failed", commit_uncertain=True)

    monkeypatch.setattr(storage_module, "atomic_write_json", write_then_uncertain)
    with pytest.raises(StorageError) as exc_info:
        store.apply_project_command("atelier-1", expected_revision=0, command=lambda project: setattr(project, "title", "Maybe"))
    assert exc_info.value.commit_uncertain is True
    assert marked and marked[0].commit_uncertain is True
    with pytest.raises(StorageError, match="restart"):
        store.get_project("atelier-1")


def test_noop_command_validates_but_does_not_write(tmp_path, monkeypatch):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    before = db_path(tmp_path).read_bytes()
    from src.apps.atelier import storage as storage_module

    def fail_if_called(path, value):
        raise AssertionError("noop should not write")

    monkeypatch.setattr(storage_module, "atomic_write_json", fail_if_called)
    saved = store.apply_project_command("atelier-1", expected_revision=0, command=lambda project: None)
    assert saved.revision == 0
    assert db_path(tmp_path).read_bytes() == before


def test_close_during_callback_aborts_without_writing(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    before = db_path(tmp_path).read_bytes()

    def close_inside(project):
        project.title = "closed write"
        store.close()

    with pytest.raises(StorageConflictError):
        store.apply_project_command("atelier-1", expected_revision=0, command=close_inside)
    assert db_path(tmp_path).read_bytes() == before


def test_definite_write_failure_keeps_store_usable(tmp_path, monkeypatch):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())
    before = db_path(tmp_path).read_bytes()
    from src.apps.atelier import storage as storage_module

    def fail_before_replace(path, value):
        raise StorageError("disk full", commit_uncertain=False)

    monkeypatch.setattr(storage_module, "atomic_write_json", fail_before_replace)
    with pytest.raises(StorageError) as exc_info:
        store.apply_project_command("atelier-1", expected_revision=0, command=lambda project: setattr(project, "title", "Unsaved"))
    assert exc_info.value.commit_uncertain is False
    assert db_path(tmp_path).read_bytes() == before
    assert store.get_project("atelier-1").title == "First"


def test_runtime_check_failure_prevents_file_creation(tmp_path):
    def runtime_check():
        raise StorageError("runtime unavailable")

    store = AtelierStorage(tmp_path, runtime_check=runtime_check)
    with pytest.raises(StorageError, match="runtime unavailable"):
        store.create_project(make_project())
    assert not db_path(tmp_path).exists()


def test_legacy_flat_project_without_revision_loads_with_revision_zero(tmp_path):
    db_path(tmp_path).write_text(
        json.dumps({"atelier-1": {"id": "atelier-1", "title": "Legacy", "created_at": 1.0, "updated_at": 1.0}}),
        encoding="utf-8",
    )
    store = AtelierStorage(tmp_path)
    project = store.get_project("atelier-1")
    assert project.title == "Legacy"
    assert project.revision == 0


def test_unknown_project_top_level_field_rejected_without_rewrite(tmp_path):
    db_path(tmp_path).write_text(
        json.dumps({
            "atelier-1": {
                "id": "atelier-1",
                "title": "Future",
                "created_at": 1.0,
                "updated_at": 1.0,
                "unknown_project_field": "must reject",
            }
        }),
        encoding="utf-8",
    )
    before = db_path(tmp_path).read_bytes()
    with pytest.raises(StorageError):
        AtelierStorage(tmp_path)
    assert db_path(tmp_path).read_bytes() == before


def test_post_write_lease_failure_is_commit_uncertain_and_seals_store(tmp_path, monkeypatch):
    marked = []
    store = AtelierStorage(tmp_path, mark_storage_failed=marked.append)
    store.create_project(make_project())
    from src.apps.atelier import storage as storage_module

    original_write = storage_module.atomic_write_json

    def write_then_lose_lease(path, value):
        original_write(path, value)
        store._writer_lock.close()

    monkeypatch.setattr(storage_module, "atomic_write_json", write_then_lose_lease)
    with pytest.raises(StorageError) as exc_info:
        store.apply_project_command("atelier-1", expected_revision=0, command=lambda project: setattr(project, "title", "Visible"))
    assert exc_info.value.commit_uncertain is True
    assert marked and marked[0].commit_uncertain is True
    with pytest.raises(StorageError, match="restart"):
        store.get_project("atelier-1")
    store.close()
    assert AtelierStorage(tmp_path).get_project("atelier-1").title == "Visible"


def test_command_result_memory_and_restart_share_canonical_model_values(tmp_path):
    store = AtelierStorage(tmp_path)
    store.create_project(make_project())

    def assign_coercible_values(project):
        project.agent_policy.approval_mode = "never"
        project.agent_policy.max_nodes_per_action = "2"

    with pytest.warns(UserWarning, match="Pydantic serializer warnings"):
        ack = store.apply_project_command("atelier-1", expected_revision=0, command=assign_coercible_values)
    current = store.get_project("atelier-1")
    store.close()
    restarted = AtelierStorage(tmp_path).get_project("atelier-1")

    assert ack.agent_policy.approval_mode is current.agent_policy.approval_mode
    assert current.agent_policy.approval_mode is restarted.agent_policy.approval_mode
    assert ack.agent_policy.max_nodes_per_action == 2
    assert type(ack.agent_policy.max_nodes_per_action) is int
    assert type(current.agent_policy.max_nodes_per_action) is int
    assert type(restarted.agent_policy.max_nodes_per_action) is int


def test_opaque_node_data_uses_same_json_shape_for_ack_read_and_restart(tmp_path):
    store = AtelierStorage(tmp_path)
    project = make_project()
    project.nodes.append(AtelierNode(id="node-1", project_id="atelier-1", data={"points": (1, 2, 3)}))

    created = store.create_project(project)
    current = store.get_project("atelier-1")
    store.close()
    restarted = AtelierStorage(tmp_path).get_project("atelier-1")

    assert created.nodes[0].data["points"] == [1, 2, 3]
    assert current.nodes[0].data["points"] == [1, 2, 3]
    assert restarted.nodes[0].data["points"] == [1, 2, 3]
    assert type(created.nodes[0].data["points"]) is list
    assert type(current.nodes[0].data["points"]) is list
    assert type(restarted.nodes[0].data["points"]) is list


def test_create_precondition_runs_under_command_guard_before_publish(tmp_path):
    store = AtelierStorage(tmp_path)

    def nested_write_rejected() -> None:
        with pytest.raises(StorageConflictError):
            store.create_project(make_project("nested"))

    created = store.create_project(make_project(), before_commit=nested_write_rejected)
    assert created.id == "atelier-1"
    assert [project.id for project in store.list_projects()] == ["atelier-1"]


def test_create_precondition_error_releases_lock_and_publishes_nothing(tmp_path):
    store = AtelierStorage(tmp_path)

    def fail() -> None:
        raise ValueError("source disappeared")

    with pytest.raises(ValueError, match="source disappeared"):
        store.create_project(make_project(), before_commit=fail)

    assert store.list_projects() == []
    created = store.create_project(make_project())
    assert created.id == "atelier-1"


def test_create_precondition_closing_store_blocks_commit(tmp_path):
    store = AtelierStorage(tmp_path)

    with pytest.raises(StorageConflictError):
        store.create_project(make_project(), before_commit=store.close)

    with pytest.raises(StorageConflictError):
        store.list_projects()
    store.close()
    assert AtelierStorage(tmp_path).list_projects() == []
