import json
from pathlib import Path
from unittest.mock import patch

from src.apps.comic_gen.models import Script
from src.apps.comic_gen.pipeline import ComicGenPipeline


def _make_pipeline(tmp_path: Path) -> ComicGenPipeline:
    with patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
        patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
        patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
        patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
        patch("src.apps.comic_gen.pipeline.ExportManager"):
        pipeline = ComicGenPipeline()

    pipeline.data_file = str(tmp_path / "projects.json")
    pipeline.series_data_file = str(tmp_path / "series.json")
    pipeline.scripts = {}
    pipeline.series_store = {}
    return pipeline


def test_project_save_creates_backup_and_overwrites_atomically(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    pipeline.scripts["project-1"] = Script(
        id="project-1",
        title="First title",
        original_text="alpha",
        created_at=1.0,
        updated_at=1.0,
    )
    pipeline._save_data()

    pipeline.scripts["project-1"] = Script(
        id="project-1",
        title="Second title",
        original_text="alpha",
        created_at=1.0,
        updated_at=2.0,
    )
    pipeline._save_data()

    data_path = tmp_path / "projects.json"
    backup_path = tmp_path / "projects.json.bak"
    assert json.loads(data_path.read_text(encoding="utf-8"))["project-1"]["title"] == "Second title"
    assert json.loads(backup_path.read_text(encoding="utf-8"))["project-1"]["title"] == "First title"


def test_project_load_falls_back_to_backup_when_primary_is_corrupt(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    data_path = tmp_path / "projects.json"
    backup_path = tmp_path / "projects.json.bak"
    data_path.write_text("{bad json", encoding="utf-8")
    backup_path.write_text(
        json.dumps(
            {
                "project-1": {
                    "id": "project-1",
                    "title": "Recovered title",
                    "original_text": "alpha",
                    "created_at": 1.0,
                    "updated_at": 1.0,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = pipeline._load_data()

    assert loaded["project-1"].title == "Recovered title"
