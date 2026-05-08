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


def test_browser_smoke_prompt_quality_gate_blocks_invalid_prompt(tmp_path) -> None:
    run_ci_dev_smoke(tmp_path, "prompt-quality")
