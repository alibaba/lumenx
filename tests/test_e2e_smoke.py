from __future__ import annotations

import os

import pytest

from tests.smoke_helpers import run_ci_dev_smoke

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("LUMENX_RUN_BROWSER_E2E") != "1",
        reason="browser e2e smoke is only enabled in startup verification jobs",
    ),
]


def test_browser_smoke_create_project(tmp_path) -> None:
    run_ci_dev_smoke(tmp_path, "create")


def test_browser_smoke_import_fixture(tmp_path) -> None:
    run_ci_dev_smoke(tmp_path, "import")


def test_browser_smoke_open_storyboard(tmp_path) -> None:
    run_ci_dev_smoke(tmp_path, "storyboard")


def test_browser_smoke_local_video_task(tmp_path) -> None:
    run_ci_dev_smoke(tmp_path, "video")
