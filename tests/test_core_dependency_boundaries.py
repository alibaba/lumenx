"""Keep shared backend primitives independent of product-owned application state."""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SHARED_DIRECTORIES = ("src/models", "src/utils", "src/core")


def _assert_shared_backend_imports(root: Path, directory: str) -> None:
    violations = []
    sources = sorted((root / directory).rglob("*.py"))
    assert sources, f"Shared source directory is absent: {directory}"
    for source in sources:
        relative = source.relative_to(root)
        package = ".".join(relative.parent.parts)
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"), filename=str(relative))):
            if isinstance(node, ast.Import):
                imports = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = resolve_name("." * node.level + module, package)
                imports = [module, *(f"{module}.{item.name}" for item in node.names)]
            else:
                continue
            for module in imports:
                if module == "src.apps" or module.startswith("src.apps."):
                    violations.append(f"{relative}:{node.lineno} imports {module}")
    assert not violations, "Shared code must not import product applications:\n" + "\n".join(violations)


@pytest.mark.parametrize("directory", SHARED_DIRECTORIES)
def test_shared_backend_imports_do_not_depend_on_product_applications(directory: str):
    _assert_shared_backend_imports(ROOT, directory)


@pytest.fixture
def shared_source_root(tmp_path: Path) -> Path:
    # These are the required layers, independently of the guard's configured scope.
    for directory in ("src/core", "src/models", "src/utils"):
        package = tmp_path / directory / "nested"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("directory", ["src/core", "src/models", "src/utils"])
@pytest.mark.parametrize(
    ("source", "module", "line"),
    [
        ("import src.apps\n", "src.apps", 1),
        ("import src.apps.comic_gen.pipeline as product\n", "src.apps.comic_gen.pipeline", 1),
        ("from src import apps as product\n", "src.apps", 1),
        ("from src.apps import atelier\n", "src.apps.atelier", 1),
        ("from ...apps.atelier import models\n", "src.apps.atelier", 1),
        ("from ... import apps\n", "src.apps", 1),
        ("def load():\n    import src.apps.comic_gen.pipeline\n", "src.apps.comic_gen.pipeline", 2),
    ],
    ids=["absolute-package", "absolute-module-alias", "from-alias", "from-module", "relative-module", "relative-package", "function-local"],
)
def test_configured_guard_rejects_product_imports_in_every_shared_layer(
    shared_source_root: Path, directory: str, source: str, module: str, line: int,
):
    relative = f"{directory}/nested/probe.py"
    (shared_source_root / relative).write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError, match="Shared code must not import product applications") as error:
        for configured_directory in SHARED_DIRECTORIES:
            _assert_shared_backend_imports(shared_source_root, configured_directory)
    assert f"{relative}:{line} imports {module}" in str(error.value)


def test_configured_guard_allows_neutral_imports(shared_source_root: Path):
    for directory in ("src/core", "src/models", "src/utils"):
        (shared_source_root / directory / "nested/probe.py").write_text(
            "import pathlib\nfrom src.utils import json_storage\n"
            "from ...utils import media_versions\nfrom src.appshell import shared\n",
            encoding="utf-8",
        )
    for directory in SHARED_DIRECTORIES:
        _assert_shared_backend_imports(shared_source_root, directory)


@pytest.mark.parametrize("directory", ["src/core", "src/models", "src/utils"])
@pytest.mark.parametrize("empty_directory", [False, True], ids=["absent", "empty"])
def test_shared_backend_guard_requires_python_sources(tmp_path: Path, directory: str, empty_directory: bool):
    if empty_directory:
        (tmp_path / directory).mkdir(parents=True)
    with pytest.raises(AssertionError, match=f"Shared source directory is absent: {directory}"):
        _assert_shared_backend_imports(tmp_path, directory)
