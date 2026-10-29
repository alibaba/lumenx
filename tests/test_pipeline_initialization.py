"""Pipeline construction is path-injected and avoids implicit startup work."""

import importlib
import threading
from pathlib import Path
from src.apps.comic_gen.models import GlobalAssetLibrary


def _patch_pipeline_dependencies(monkeypatch, pipeline_mod, events):
    class StubScriptProcessor:
        def __init__(self, *args, **kwargs):
            events.append(("script_processor", args, kwargs))

    def _generator(name):
        class StubGenerator:
            def __init__(self, config=None, *, before_generation=None):
                events.append((name, config or {}))
                self.config = config or {}
                self.before_generation = before_generation

        return StubGenerator

    monkeypatch.setattr(pipeline_mod, "ScriptProcessor", StubScriptProcessor)
    monkeypatch.setattr(pipeline_mod, "AssetGenerator", _generator("asset_generator"))
    monkeypatch.setattr(pipeline_mod, "StoryboardGenerator", _generator("storyboard_generator"))
    monkeypatch.setattr(pipeline_mod, "VideoGenerator", _generator("video_generator"))
    monkeypatch.setattr(pipeline_mod, "AudioGenerator", _generator("audio_generator"))
    monkeypatch.setattr(pipeline_mod, "ExportManager", _generator("export_manager"))


def _patch_empty_stores(monkeypatch, pipeline_mod):
    monkeypatch.setattr(pipeline_mod.ComicGenPipeline, "_load_data", lambda self: {})
    monkeypatch.setattr(pipeline_mod.ComicGenPipeline, "_load_series_data", lambda self: {})
    monkeypatch.setattr(
        pipeline_mod.ComicGenPipeline,
        "_load_library_data",
        lambda self: GlobalAssetLibrary(),
    )


def test_pipeline_uses_explicit_data_dir_before_loading(monkeypatch, tmp_path):
    pipeline_mod = importlib.import_module("src.apps.comic_gen.pipeline")
    events = []
    loaded_paths = []
    _patch_pipeline_dependencies(monkeypatch, pipeline_mod, events)

    monkeypatch.setattr(
        pipeline_mod.ComicGenPipeline,
        "_load_data",
        lambda self: loaded_paths.append(Path(self.data_file)) or {},
    )
    monkeypatch.setattr(
        pipeline_mod.ComicGenPipeline,
        "_load_series_data",
        lambda self: loaded_paths.append(Path(self.series_data_file)) or {},
    )
    monkeypatch.setattr(
        pipeline_mod.ComicGenPipeline,
        "_load_library_data",
        lambda self: loaded_paths.append(Path(self.library_data_file)) or GlobalAssetLibrary(),
    )
    monkeypatch.setattr(
        pipeline_mod.ComicGenPipeline,
        "_repair_series_bindings",
        lambda self: None,
    )

    pipeline = pipeline_mod.ComicGenPipeline(
        data_dir=tmp_path / "owned-output",
        recover_orphan_tasks=False,
        warmup_demucs=False,
    )

    assert pipeline.data_file == str(tmp_path / "owned-output" / "projects.json")
    assert pipeline.series_data_file == str(tmp_path / "owned-output" / "series.json")
    assert pipeline.library_data_file == str(tmp_path / "owned-output" / "library_assets.json")
    assert loaded_paths == [
        tmp_path / "owned-output" / "projects.json",
        tmp_path / "owned-output" / "series.json",
        tmp_path / "owned-output" / "library_assets.json",
    ]


def test_pipeline_constructs_components_with_explicit_output_root(monkeypatch, tmp_path):
    pipeline_mod = importlib.import_module("src.apps.comic_gen.pipeline")
    events = []
    _patch_pipeline_dependencies(monkeypatch, pipeline_mod, events)
    _patch_empty_stores(monkeypatch, pipeline_mod)

    output_root = tmp_path / "output"
    pipeline = pipeline_mod.ComicGenPipeline(data_dir=output_root)

    assert pipeline.asset_generator.before_generation == pipeline.assert_storage_available
    for component in (pipeline.storyboard_generator, pipeline.video_generator, pipeline.audio_generator):
        assert component.before_generation == pipeline.assert_storage_available

    assert ("script_processor", (), {"output_root": str(output_root)}) in events
    generator_configs = {
        event[0]: event[1]
        for event in events
        if event[0].endswith("_generator") or event[0] == "export_manager"
    }
    assert generator_configs["asset_generator"]["output_dir"] == str(output_root / "assets")
    assert generator_configs["storyboard_generator"]["output_dir"] == str(output_root / "storyboard")
    assert generator_configs["video_generator"]["output_dir"] == str(output_root / "video")
    assert generator_configs["audio_generator"]["output_dir"] == str(output_root / "audio")
    assert generator_configs["export_manager"]["output_dir"] == str(output_root / "export")
    for config in generator_configs.values():
        assert config["output_root"] == str(output_root)
        assert config["model"]["output_root"] == str(output_root)


def test_pipeline_constructor_does_not_start_demucs_or_recover_unless_requested(
    monkeypatch, tmp_path
):
    pipeline_mod = importlib.import_module("src.apps.comic_gen.pipeline")
    events = []
    started_threads = []
    recovered = []
    repaired = []
    _patch_pipeline_dependencies(monkeypatch, pipeline_mod, events)

    monkeypatch.setattr(threading.Thread, "start", lambda self: started_threads.append(self))
    _patch_empty_stores(monkeypatch, pipeline_mod)
    monkeypatch.setattr(
        pipeline_mod.ComicGenPipeline,
        "_repair_series_bindings",
        lambda self: repaired.append(True),
    )
    monkeypatch.setattr(
        pipeline_mod.ComicGenPipeline,
        "_recover_orphan_tasks",
        lambda self: recovered.append(True),
    )

    pipeline_mod.ComicGenPipeline(
        data_dir=tmp_path / "output",
        recover_orphan_tasks=False,
        warmup_demucs=False,
    )

    assert started_threads == []
    assert recovered == []
    assert repaired == []


def test_pipeline_can_explicitly_request_startup_recovery_and_demucs_warmup(
    monkeypatch, tmp_path
):
    pipeline_mod = importlib.import_module("src.apps.comic_gen.pipeline")
    events = []
    started_threads = []
    recovered = []
    repaired = []
    _patch_pipeline_dependencies(monkeypatch, pipeline_mod, events)

    monkeypatch.setattr(threading.Thread, "start", lambda self: started_threads.append(self))
    _patch_empty_stores(monkeypatch, pipeline_mod)
    monkeypatch.setattr(
        pipeline_mod.ComicGenPipeline,
        "_repair_series_bindings",
        lambda self: repaired.append(True),
    )
    monkeypatch.setattr(
        pipeline_mod.ComicGenPipeline,
        "_recover_orphan_tasks",
        lambda self: recovered.append(True),
    )

    pipeline_mod.ComicGenPipeline(
        data_dir=tmp_path / "output",
        recover_orphan_tasks=True,
        warmup_demucs=True,
        repair_series_bindings=True,
    )

    assert recovered == [True]
    assert repaired == [True]
    assert len(started_threads) == 1
