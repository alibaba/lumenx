import json
import os
import time

import pytest

from src.utils.json_store import (
    JsonStoreLockTimeout,
    load_json_object_with_backup,
    save_json_object_atomic,
)


def test_atomic_json_save_creates_backup(tmp_path):
    data_path = tmp_path / "projects.json"

    save_json_object_atomic(data_path, {"old": {"title": "before"}})
    save_json_object_atomic(data_path, {"new": {"title": "after"}})

    assert json.loads(data_path.read_text(encoding="utf-8")) == {"new": {"title": "after"}}
    assert json.loads((tmp_path / "projects.json.bak").read_text(encoding="utf-8")) == {
        "old": {"title": "before"}
    }
    assert not (tmp_path / "projects.json.lock").exists()


def test_load_json_uses_backup_when_primary_is_corrupt(tmp_path):
    data_path = tmp_path / "projects.json"
    backup_path = tmp_path / "projects.json.bak"
    data_path.write_text("{not-json", encoding="utf-8")
    backup_path.write_text(json.dumps({"safe": {"title": "backup"}}), encoding="utf-8")

    assert load_json_object_with_backup(data_path) == {"safe": {"title": "backup"}}


def test_atomic_json_save_preserves_backup_when_primary_is_corrupt(tmp_path):
    data_path = tmp_path / "projects.json"
    backup_path = tmp_path / "projects.json.bak"
    data_path.write_text("{not-json", encoding="utf-8")
    backup_path.write_text(json.dumps({"safe": {"title": "backup"}}), encoding="utf-8")

    save_json_object_atomic(data_path, {"recovered": {"title": "fresh"}})

    assert json.loads(data_path.read_text(encoding="utf-8")) == {
        "recovered": {"title": "fresh"}
    }
    assert json.loads(backup_path.read_text(encoding="utf-8")) == {
        "safe": {"title": "backup"}
    }


def test_atomic_json_save_respects_active_lock(monkeypatch, tmp_path):
    data_path = tmp_path / "projects.json"
    lock_path = tmp_path / "projects.json.lock"
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "created_at": time.time()}),
        encoding="utf-8",
    )
    monkeypatch.setattr("src.utils.json_store._process_is_alive", lambda pid: True)

    with pytest.raises(JsonStoreLockTimeout):
        save_json_object_atomic(
            data_path,
            {"blocked": True},
            lock_timeout_seconds=0,
        )
