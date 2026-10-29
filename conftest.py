"""Repository-level pytest entry guard."""

from __future__ import annotations

import os
import sys

import pytest


def pytest_configure(config):
    if os.environ.get("LUMENX_TEST_ISOLATED") != "1":
        pytest.exit(
            "Run backend tests through scripts/run_tests_isolated.py so collection "
            "cannot read real credentials or write user data.",
            returncode=4,
        )
    try:
        import isolation_guard
    except ModuleNotFoundError as exc:  # pragma: no cover - actionable bootstrap failure
        raise SystemExit(
            "LumenX isolation guard is missing. Use scripts/run_tests_isolated.py."
        ) from exc
    isolation_guard.install()
    os.environ["LUMENX_TEST_PHASE"] = "collecting"


def pytest_collection_finish(session):
    os.environ["LUMENX_TEST_PHASE"] = "running"


def pytest_sessionfinish(session, exitstatus):
    try:
        import isolation_guard
    except ModuleNotFoundError:
        return
    if isolation_guard.violations():
        for item in isolation_guard.violations():
            print(f"LumenX isolation violation: {item}", file=sys.stderr)
        session.exitstatus = 1
