"""Versioned SQLite migrations for the creator platform.

The runner is deliberately small and deterministic. It does not import the
application or create a database until the caller explicitly invokes it.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
import sqlite3
from typing import Iterator


MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class MigrationError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def backup_database(path: Path, backup_dir: Path | None = None) -> Path | None:
    """Create a versioned backup before migration; return None for a new DB."""

    if not path.exists():
        return None
    target_dir = backup_dir or path.parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{path.stem}.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.bak"
    shutil.copy2(path, target)
    return target


def migrate(path: Path) -> list[str]:
    """Apply pending migrations and return the applied version names."""

    applied: list[str] = []
    with connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        recorded = dict(connection.execute("SELECT version, checksum FROM schema_migrations"))
        for migration in _migration_files():
            version = migration.name
            checksum = _checksum(migration)
            if version in recorded:
                if recorded[version] != checksum:
                    raise MigrationError(f"migration checksum changed: {version}")
                continue
            connection.executescript(migration.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations(version, checksum, applied_at) VALUES (?, ?, ?)",
                (version, checksum, _utc_now()),
            )
            applied.append(version)
    return applied


def check_foreign_keys(path: Path) -> list[tuple]:
    with connect(path) as connection:
        return connection.execute("PRAGMA foreign_key_check").fetchall()

