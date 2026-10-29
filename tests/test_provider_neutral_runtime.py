"""Neutral provider runtime construction shared by product apps."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path


def test_neutral_provider_runtime_module_does_not_import_product_applications(tmp_path):
    probe = tmp_path / "neutral_runtime_import_probe.py"
    probe.write_text("""
import importlib
import sys

importlib.import_module("src.models.provider_runtime")
violations = [name for name in sys.modules if name == "src.apps" or name.startswith("src.apps.")]
if violations:
    raise SystemExit("\\n".join(violations))
""", encoding="utf-8")
    helper = Path(os.environ["LUMENX_REPO_ROOT"]) / "scripts/test_support/isolated_child.py"
    result = subprocess.run([sys.executable, "-S", str(helper), str(probe)], check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr or result.stdout


def test_studio_runtime_imports_reexport_neutral_contract_objects():
    neutral = importlib.import_module("src.models.provider_runtime")
    studio_runtime = importlib.import_module("src.apps.comic_gen.provider_runtime")
    studio_recovery = importlib.import_module("src.apps.comic_gen.provider_recovery")

    assert studio_recovery.StudioProviderRecoveryRuntime is neutral.ProviderRecoveryRuntime
    assert studio_runtime.StudioProviderRecoveryRuntime is neutral.ProviderRecoveryRuntime
    assert studio_runtime.StudioProviderRuntimeBuildResult is neutral.ProviderRuntimeBuildResult
    assert studio_runtime.build_studio_provider_recovery_runtime is neutral.build_provider_recovery_runtime


def test_neutral_builder_returns_neutral_result_and_runtime(tmp_path):
    neutral = importlib.import_module("src.models.provider_runtime")
    from src.utils.runtime_environment import RuntimeEnvironment

    result = neutral.build_provider_recovery_runtime(
        RuntimeEnvironment({"DASHSCOPE_API_KEY": "secret"}),
        output_root=tmp_path,
    )

    assert isinstance(result, neutral.ProviderRuntimeBuildResult)
    assert isinstance(result.runtime, neutral.ProviderRecoveryRuntime)
    assert result.reason is None
    assert result.runtime.resolve_secret() == "secret"
    assert Path(result.runtime.provider.output_root) == tmp_path
