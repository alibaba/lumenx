"""Bootstrap pytest after installing LumenX's isolation guard."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepend_existing(paths: list[str]) -> None:
    for raw in reversed(paths):
        if raw and Path(raw).exists() and raw not in sys.path:
            sys.path.insert(0, raw)


def main() -> int:
    repo_root = os.environ["LUMENX_REPO_ROOT"]
    support_dir = str(Path(__file__).resolve().parent)
    site_roots = os.environ.get("LUMENX_TEST_SITE_PACKAGES", "").split(os.pathsep)
    _prepend_existing([repo_root, support_dir, *site_roots])

    import isolation_guard

    isolation_guard.install()

    os.environ["LUMENX_TEST_PHASE"] = "collecting"
    import pytest

    return pytest.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
