"""Run a Python script after installing the LumenX isolation guard."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: isolated_child.py SCRIPT [ARGS...]", file=sys.stderr)
        return 2
    support_dir = Path(__file__).resolve().parent
    if str(support_dir) not in sys.path:
        sys.path.insert(0, str(support_dir))
    from isolated_pytest import _prepend_existing
    _prepend_existing([os.environ["LUMENX_REPO_ROOT"], *os.environ.get("LUMENX_TEST_SITE_PACKAGES", "").split(os.pathsep)])
    import isolation_guard

    isolation_guard.install()
    script = Path(sys.argv[1]).resolve()
    sys.argv = [str(script), *sys.argv[2:]]
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
