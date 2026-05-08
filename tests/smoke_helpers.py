from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_ci_dev_smoke(tmp_path: Path, scenario: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if not node or not npm:
        pytest.skip("node/npm are required for the browser smoke")

    output_dir = tmp_path / f"browser-e2e-output-{scenario}"
    summary_path = output_dir / f"browser-smoke-{scenario}.json"

    env = os.environ.copy()
    env["PYTHON"] = sys.executable
    env["CI"] = "1"
    env.setdefault("LUMENX_SKIP_DEV_SETUP", "1")
    env.setdefault("LUMENX_SKIP_BROWSER_OPEN", "1")
    env.setdefault("LUMENX_E2E_HEADLESS", "1")
    env.setdefault("LUMENX_SKIP_BROWSER_E2E", "0")
    env["LUMENX_BROWSER_SMOKE_SCENARIO"] = scenario
    env["LUMENX_E2E_OUTPUT_DIR"] = str(output_dir)
    env["LUMENX_E2E_SUMMARY_PATH"] = str(summary_path)

    result = subprocess.run(
        [npm, "run", "ci:dev-smoke"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )

    if result.returncode != 0:
        summary_text = ""
        if summary_path.exists():
            summary_text = summary_path.read_text(encoding="utf-8", errors="replace").strip()
        raise AssertionError(
            "\n".join(
                [
                    f"Browser smoke scenario '{scenario}' failed.",
                    f"Summary path: {summary_path}",
                    "--- summary ---",
                    summary_text or "(missing)",
                    "--- stdout ---",
                    (result.stdout or "").strip(),
                    "--- stderr ---",
                    (result.stderr or "").strip(),
                ]
            )
        )

    return result
