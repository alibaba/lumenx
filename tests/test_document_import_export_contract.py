from __future__ import annotations

import base64
import builtins
import sys
import types
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import Script


def _doc(text: str = "hello") -> dict:
    return {"type": "doc", "content": [{"type": "action", "content": [{"type": "text", "text": text}]}]}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=Mock())
    item.scripts["p"] = Script(id="p", title="Project", original_text="stored", created_at=1, updated_at=1)
    item._commit_storage("projects.json")
    with TestClient(api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item), raise_server_exceptions=False) as client:
        yield item, client
    item.close()


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def test_document_import_requires_existing_project(runtime):
    _, client = runtime
    response = client.post("/projects/missing/document/import", json={
        "filename": "story.txt", "content": _encoded("hello"), "file_type": "txt",
    })
    assert response.status_code == 404


def test_document_export_requires_existing_project(runtime):
    _, client = runtime
    response = client.post("/projects/missing/document/export", json={"content": _doc(), "format": "pdf"})
    assert response.status_code == 404


def test_document_import_rejects_malformed_base64(runtime):
    _, client = runtime
    response = client.post("/projects/p/document/import", json={
        "filename": "bad.txt", "content": "%%%", "file_type": "txt",
    })
    assert response.status_code == 400
    assert "decode" in response.json()["detail"].lower()


def test_document_import_valid_txt_still_returns_tiptap(runtime):
    _, client = runtime
    response = client.post("/projects/p/document/import", json={
        "filename": "story.txt", "content": _encoded("INT. ROOM\nHello"), "file_type": "txt",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "story.txt"
    assert body["file_type"] == "txt"
    assert body["content"]["content"][0]["content"][0]["text"] == "INT. ROOM"


def test_document_import_preserves_fdx_xml_error(runtime):
    _, client = runtime
    response = client.post("/projects/p/document/import", json={
        "filename": "bad.fdx", "content": _encoded("<FinalDraft><Content><Paragraph>"), "file_type": "fdx",
    })
    assert response.status_code == 400
    assert "Invalid FDX XML" in response.json()["detail"]


def test_document_import_rejects_fdx_entities(runtime):
    _, client = runtime
    xml = "<!DOCTYPE foo [<!ENTITY x 'y'>]><FinalDraft />"
    response = client.post("/projects/p/document/import", json={
        "filename": "bad.fdx", "content": _encoded(xml), "file_type": "fdx",
    })
    assert response.status_code == 400
    assert "DTD/entity" in response.json()["detail"]


@pytest.mark.parametrize("payload", [
    {"filename": "story.md", "content": _encoded("hello"), "file_type": "md"},
    {"filename": "story.txt", "content": _encoded("hello"), "file_type": True},
])
def test_document_import_strictly_rejects_unsupported_formats(runtime, payload):
    _, client = runtime
    response = client.post("/projects/p/document/import", json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize("content", [
    {"type": "doc", "content": [1]},
    {"type": "doc", "content": [{"content": [1]}]},
    {"type": "doc", "content": "not-list"},
    {"type": "doc", "content": None},
    {"type": "doc", "content": [{"type": "action", "content": None}]},
    {"type": "doc", "content": [{"type": "text", "text": None}]},
    {"type": "action", "content": []},
])
def test_document_export_malformed_nested_json_returns_controlled_4xx(runtime, content):
    _, client = runtime
    response = client.post("/projects/p/document/export", json={"content": content, "format": "pdf"})
    assert response.status_code == 422


@pytest.mark.parametrize("fmt", ["html", True])
def test_document_export_strictly_rejects_unsupported_formats(runtime, fmt):
    _, client = runtime
    response = client.post("/projects/p/document/export", json={"content": _doc(), "format": fmt})
    assert response.status_code == 422


def test_document_export_pdf_missing_library_is_explicit_503(runtime, monkeypatch):
    _, client = runtime
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "reportlab" or name.startswith("reportlab."):
            raise ImportError("blocked reportlab")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    response = client.post("/projects/p/document/export", json={"content": _doc(), "format": "pdf"})
    assert response.status_code == 503
    assert not response.headers.get("content-type", "").startswith("text/html")
    assert response.headers.get("x-export-fallback") is None


def test_document_export_docx_missing_library_is_explicit_503(runtime, monkeypatch):
    _, client = runtime
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "docx" or name.startswith("docx."):
            raise ImportError("blocked docx")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    response = client.post("/projects/p/document/export", json={"content": _doc(), "format": "docx"})
    assert response.status_code == 503
    assert not response.headers.get("content-type", "").startswith("text/plain")
    assert response.headers.get("x-export-fallback") is None


def test_document_export_pdf_success_preserves_pdf_headers_with_fake_library(runtime, monkeypatch):
    _, client = runtime

    reportlab = types.ModuleType("reportlab")
    lib = types.ModuleType("reportlab.lib")
    pagesizes = types.ModuleType("reportlab.lib.pagesizes")
    pagesizes.letter = (612, 792)
    units = types.ModuleType("reportlab.lib.units")
    units.inch = 72
    enums = types.ModuleType("reportlab.lib.enums")
    enums.TA_CENTER = 1
    enums.TA_RIGHT = 2

    styles_mod = types.ModuleType("reportlab.lib.styles")

    class FakeStyle:
        def __init__(self, name="style", **kwargs):
            self.name = name
            self.kwargs = kwargs

    class FakeStyles(dict):
        def add(self, style):
            self[style.name] = style

    def get_sample_style_sheet():
        return FakeStyles({"Heading3": FakeStyle("Heading3"), "Normal": FakeStyle("Normal")})

    styles_mod.getSampleStyleSheet = get_sample_style_sheet
    styles_mod.ParagraphStyle = FakeStyle

    platypus = types.ModuleType("reportlab.platypus")

    class FakeDoc:
        def __init__(self, buffer, **kwargs):
            self.buffer = buffer

        def build(self, story):
            self.buffer.write(b"%PDF-fake")

    class FakeParagraph:
        def __init__(self, text, style):
            self.text = text
            self.style = style

    platypus.SimpleDocTemplate = FakeDoc
    platypus.Paragraph = FakeParagraph
    platypus.Spacer = object

    modules = {
        "reportlab": reportlab,
        "reportlab.lib": lib,
        "reportlab.lib.pagesizes": pagesizes,
        "reportlab.lib.units": units,
        "reportlab.lib.enums": enums,
        "reportlab.lib.styles": styles_mod,
        "reportlab.platypus": platypus,
    }
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)

    response = client.post("/projects/p/document/export", json={"content": _doc(), "format": "pdf"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"] == 'attachment; filename="script.pdf"'
    assert response.content.startswith(b"%PDF")


def test_document_export_docx_success_preserves_docx_headers_with_fake_library(runtime, monkeypatch):
    _, client = runtime

    docx = types.ModuleType("docx")
    shared = types.ModuleType("docx.shared")
    enum_text = types.ModuleType("docx.enum.text")
    docx_enum = types.ModuleType("docx.enum")

    shared.Pt = lambda value: value
    shared.Inches = lambda value: value

    class Align:
        CENTER = 1
        RIGHT = 2

    enum_text.WD_ALIGN_PARAGRAPH = Align

    class FakeRun:
        bold = False

    class FakeParagraph:
        def __init__(self):
            self.style = None
            self.alignment = None
            self.paragraph_format = types.SimpleNamespace(left_indent=None, right_indent=None)

        def add_run(self, text):
            return FakeRun()

    class FakeDocument:
        def __init__(self):
            self.styles = {"Heading 3": object()}

        def add_paragraph(self, text=None):
            return FakeParagraph()

        def save(self, buffer):
            buffer.write(b"PKfake-docx")

    docx.Document = FakeDocument

    for name, mod in {
        "docx": docx,
        "docx.shared": shared,
        "docx.enum": docx_enum,
        "docx.enum.text": enum_text,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)

    response = client.post("/projects/p/document/export", json={"content": _doc(), "format": "docx"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert response.headers["content-disposition"] == 'attachment; filename="script.docx"'
    assert response.content.startswith(b"PK")
