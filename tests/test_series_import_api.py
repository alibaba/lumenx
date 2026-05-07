import asyncio
import sys
import types

import pytest
from fastapi import HTTPException

dashscope_stub = types.ModuleType("dashscope")
dashscope_stub.VideoSynthesis = object
sys.modules.setdefault("dashscope", dashscope_stub)

from src.apps.comic_gen import api as api_module


class _FakePipeline:
    def __init__(self, text: str, result=None, error: Exception | None = None):
        self._import_cache = {"import-1": text}
        self._result = result if result is not None else {"series_id": "series-1"}
        self._error = error
        self.calls = []

    def create_series_from_import(self, title, text, episodes, description):
        self.calls.append(
            {
                "title": title,
                "text": text,
                "episodes": episodes,
                "description": description,
            }
        )
        if self._error is not None:
            raise self._error
        return self._result


def test_import_confirm_uses_import_id_and_clears_cache_after_success(monkeypatch):
    fake_pipeline = _FakePipeline("完整原文", result={"series_id": "series-ok"})
    monkeypatch.setattr(api_module, "pipeline", fake_pipeline)
    monkeypatch.setattr(api_module, "signed_response", lambda result: result)

    request = api_module.ConfirmImportRequest(
        title="测试系列",
        description="",
        import_id="import-1",
        episodes=[{"episode_number": 1, "title": "第一集"}],
    )

    result = asyncio.run(api_module.import_file_confirm(request))

    assert result == {"series_id": "series-ok"}
    assert fake_pipeline.calls[0]["text"] == "完整原文"
    assert "import-1" not in fake_pipeline._import_cache


def test_import_confirm_preserves_cache_when_creation_fails(monkeypatch):
    fake_pipeline = _FakePipeline("完整原文", error=RuntimeError("boom"))
    monkeypatch.setattr(api_module, "pipeline", fake_pipeline)
    monkeypatch.setattr(api_module, "signed_response", lambda result: result)

    request = api_module.ConfirmImportRequest(
        title="测试系列",
        description="",
        import_id="import-1",
        episodes=[{"episode_number": 1, "title": "第一集"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(api_module.import_file_confirm(request))

    assert exc_info.value.status_code == 500
    assert fake_pipeline._import_cache["import-1"] == "完整原文"
