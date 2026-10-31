import pytest

from src.apps.platform import db as platform_db
from src.apps.platform.db import MigrationError, backup_database, check_foreign_keys, connect, migrate


def test_migrate_creates_isolated_schema_with_sqlite_safety(tmp_path):
    db_path = tmp_path / "platform.db"
    assert migrate(db_path) == ["001_initial.sql", "002_business.sql", "003_token_usage.sql"]
    assert migrate(db_path) == []
    assert check_foreign_keys(db_path) == []
    with connect(db_path) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert connection.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall() == [("001_initial.sql",), ("002_business.sql",), ("003_token_usage.sql",)]
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"platform_users", "platform_sessions", "audit_events", "platform_token_usage"}.issubset(tables)


def test_migrate_rejects_changed_applied_migration(tmp_path, monkeypatch):
    db_path = tmp_path / "platform.db"
    migrate(db_path)
    monkeypatch.setattr(platform_db, "_checksum", lambda _path: "f" * 64)
    with pytest.raises(MigrationError, match="checksum changed"):
        migrate(db_path)


def test_backup_is_explicit_and_does_not_run_on_new_database(tmp_path):
    db_path = tmp_path / "platform.db"
    assert backup_database(db_path, tmp_path / "backups") is None
    db_path.write_bytes(b"sqlite placeholder")
    backup = backup_database(db_path, tmp_path / "backups")
    assert backup is not None
    assert backup.read_bytes() == b"sqlite placeholder"
