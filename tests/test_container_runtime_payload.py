"""Exercise the actual catalog import from the backend image's COPY payload."""

from dataclasses import asdict
import importlib.util
from pathlib import Path
import shlex
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_backend_image_payload_can_resolve_its_model_defaults(tmp_path, monkeypatch):
    image_root = tmp_path / "app"
    image_root.mkdir()
    # Materialize only COPY inputs, never the checkout as an implicit fallback.
    for line in (ROOT / "Dockerfile.backend").read_text().splitlines():
        if not line.strip().startswith("COPY "):
            continue
        instruction, source, destination = shlex.split(line)
        assert instruction == "COPY"
        source_path = ROOT / source
        target = image_root / destination
        assert source_path.resolve().is_relative_to(ROOT)
        assert target.resolve().is_relative_to(image_root)
        if source_path.is_dir():
            shutil.copytree(
                source_path, target, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target)

    # Execute the copied loader itself so its __file__-relative paths cannot
    # accidentally resolve resources from the development checkout.
    name = "lumenx_container_payload_catalog"
    spec = importlib.util.spec_from_file_location(name, image_root / "src/utils/model_catalog.py")
    assert spec is not None and spec.loader is not None
    catalog = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, catalog)
    spec.loader.exec_module(catalog)
    assert catalog.REPO_ROOT == image_root
    defaults = asdict(catalog.get_default_model_settings())
    assert defaults
    assert all(isinstance(value, str) and value for value in defaults.values())
