"""Studio video-task resilience tests.

Covers the three regressions surfaced by the "stuck on 排队中..." user
report:

  1. Backend restart eats in-memory FastAPI BackgroundTasks. The persisted
     task on disk stays at status="pending" forever and the UI shows an
     eternal spinner. Fix: pipeline.__init__ runs _recover_orphan_tasks()
     which preserves pending/processing tasks as needs_confirmation without
     claiming a remote failure or enabling a failed-task retry.

  2. BG task wrapper in api.py used to silently log + drop exceptions
     that escaped pipeline.process_video_task's own try/except (e.g.
     get_script raising). The user saw nothing. Fix: a new
     pipeline.mark_video_task_failed helper writes status + error so the
     UI gets a definite failure.

  3. The user can end up with model="wan2.7-r2v" cached in
     localStorage but submit through the I2V flow without supplying ref
     images, which made wanx.py raise mid-generation. Fix:
     create_video_task validates model⇄refs consistency at submit time
     so the frontend gets a clean 400 instead of a permanently-failed
     task.
"""

import time
import uuid
from unittest.mock import patch

import pytest

from src.apps.comic_gen.models import Script, StoryboardFrame, VideoTask
from src.apps.comic_gen.pipeline import ComicGenPipeline


@pytest.fixture
def pipeline(tmp_path):
    """Pipeline with temp data files, real IO bypassed."""
    with patch("src.apps.comic_gen.pipeline.ScriptProcessor"), \
         patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        p = ComicGenPipeline(data_dir=tmp_path / "output")
    p.scripts = {}
    p.series_store = {}
    return p


def _video_task(status="pending", task_id=None) -> VideoTask:
    return VideoTask(
        id=task_id or str(uuid.uuid4()),
        project_id="p1",
        image_url="uploads/img.png",
        prompt="prompt",
        status=status,
        model="wan2.7-i2v",
    )


def _script_with_tasks(*tasks) -> Script:
    return Script(
        id="p1",
        title="Project",
        original_text="text",
        created_at=time.time(),
        updated_at=time.time(),
        video_tasks=list(tasks),
    )


# ---------------------------------------------------------------------------
# Orphan recovery
# ---------------------------------------------------------------------------


def test_orphan_recovery_marks_pending_and_processing_as_unconfirmed(pipeline):
    """Pending/processing tasks left over from a prior process die in
    mid-air when uvicorn restarts. Recovery retains uncertainty without
    authorizing another paid request."""
    pending = _video_task(status="pending", task_id="t-pending")
    processing = _video_task(status="processing", task_id="t-processing")
    completed = _video_task(status="completed", task_id="t-completed")
    failed = _video_task(status="failed", task_id="t-failed")
    pipeline.scripts = {
        "p1": _script_with_tasks(pending, processing, completed, failed),
    }

    pipeline._recover_orphan_tasks()

    by_id = {t.id: t for t in pipeline.scripts["p1"].video_tasks}
    assert by_id["t-pending"].status == "needs_confirmation"
    assert "Backend was restarted" in (by_id["t-pending"].error or "")
    assert by_id["t-processing"].status == "needs_confirmation"
    # Completed + failed are untouched.
    assert by_id["t-completed"].status == "completed"
    assert by_id["t-failed"].status == "failed"


def test_orphan_recovery_preserves_existing_error_message(pipeline):
    """If a stuck task already has an error message attached, the
    recovery sweep doesn't overwrite it (preserves diagnostic value)."""
    task = _video_task(status="pending", task_id="t1")
    task.error = "DashScope provider timed out"
    pipeline.scripts = {"p1": _script_with_tasks(task)}

    pipeline._recover_orphan_tasks()

    recovered = pipeline.scripts["p1"].video_tasks[0]
    assert recovered.status == "needs_confirmation"
    assert recovered.error == "DashScope provider timed out"


def test_orphan_recovery_is_noop_when_nothing_stuck(pipeline):
    pipeline.scripts = {
        "p1": _script_with_tasks(_video_task(status="completed")),
    }

    pipeline._recover_orphan_tasks()  # Should not raise

    # No save side-effect needed (recovered count was zero).
    assert pipeline.scripts["p1"].video_tasks[0].status == "completed"


# ---------------------------------------------------------------------------
# mark_video_task_failed (belt-and-suspenders writeback)
# ---------------------------------------------------------------------------


def test_mark_video_task_failed_writes_status_and_error(pipeline):
    task = _video_task(status="processing", task_id="t1")
    pipeline.scripts = {"p1": _script_with_tasks(task)}

    ok = pipeline.mark_video_task_failed("p1", "t1", "Background error: boom")

    assert ok is True
    after = pipeline.scripts["p1"].video_tasks[0]
    assert after.status == "failed"
    assert after.error == "Background error: boom"


def test_mark_video_task_failed_does_not_downgrade_completed(pipeline):
    """A spurious wrapper exception or a late cancel must not flip a
    successful task back to failed."""
    task = _video_task(status="completed", task_id="t1")
    pipeline.scripts = {"p1": _script_with_tasks(task)}

    ok = pipeline.mark_video_task_failed("p1", "t1", "spurious")

    assert ok is False
    assert pipeline.scripts["p1"].video_tasks[0].status == "completed"


def test_mark_video_task_failed_returns_false_for_unknown(pipeline):
    pipeline.scripts = {"p1": _script_with_tasks(_video_task(task_id="t1"))}

    assert pipeline.mark_video_task_failed("p1", "nope", "x") is False
    assert pipeline.mark_video_task_failed("nope", "t1", "x") is False


# ---------------------------------------------------------------------------
# create_video_task: model ⇄ ref consistency guard (Bug C)
# ---------------------------------------------------------------------------


def test_create_video_task_rejects_r2v_model_without_refs(pipeline):
    """The user reproduced this: stale localStorage carried wan2.7-r2v
    into an I2V flow that never supplies ref images. Without the guard
    wanx.py raises mid-flight and the user sees only a spinner."""
    pipeline.scripts = {"p1": _script_with_tasks()}

    with pytest.raises(ValueError, match="reference-to-video"):
        pipeline.create_video_task(
            script_id="p1",
            image_url="uploads/img.png",
            prompt="A scene",
            model="wan2.7-r2v",
            generation_mode="i2v",   # mismatched against the model
            reference_image_urls=[],
        )

    # Task was never persisted.
    assert pipeline.scripts["p1"].video_tasks == []


def test_create_video_task_rejects_wan26_r2v_without_video_refs(pipeline):
    pipeline.scripts = {"p1": _script_with_tasks()}

    with pytest.raises(ValueError, match="reference-to-video"):
        pipeline.create_video_task(
            script_id="p1",
            image_url="",
            prompt="A scene",
            model="wan2.6-r2v",
            generation_mode="r2v",
            reference_video_urls=[],
        )


def test_annotate_video_task_sets_star_and_label(pipeline):
    """User starts a take + attaches a free-text note via the new
    annotate endpoint. Both fields optional so the call can set either
    independently or together."""
    task = _video_task(status="completed", task_id="t1")
    pipeline.scripts = {"p1": _script_with_tasks(task)}

    # Star + label together.
    after = pipeline.annotate_video_task("p1", "t1", is_starred=True, label="best lighting")
    assert after is not None
    assert after.is_starred is True
    assert after.label == "best lighting"

    # Star only — label preserved.
    after = pipeline.annotate_video_task("p1", "t1", is_starred=False)
    assert after.is_starred is False
    assert after.label == "best lighting"

    # Label only — star preserved.
    after = pipeline.annotate_video_task("p1", "t1", label="action怪")
    assert after.is_starred is False
    assert after.label == "action怪"

    # clear_label removes label, ignores label payload value.
    after = pipeline.annotate_video_task("p1", "t1", clear_label=True)
    assert after.label is None


def test_annotate_video_task_truncates_label_to_max(pipeline):
    """Label is bounded server-side at 20 chars so a runaway client
    can't store a 10 KB note in a single field."""
    task = _video_task(status="completed", task_id="t1")
    pipeline.scripts = {"p1": _script_with_tasks(task)}

    long = "a" * 200
    after = pipeline.annotate_video_task("p1", "t1", label=long)
    assert after is not None
    assert len(after.label) == 20
    assert after.label == "a" * 20

    # Whitespace-only label clears.
    after = pipeline.annotate_video_task("p1", "t1", label="    ")
    assert after.label is None


def test_annotate_video_task_returns_none_for_unknown(pipeline):
    pipeline.scripts = {"p1": _script_with_tasks(_video_task(task_id="t1"))}
    assert pipeline.annotate_video_task("p1", "ghost", is_starred=True) is None
    assert pipeline.annotate_video_task("ghost", "t1", is_starred=True) is None


def test_model_settings_persists_r2v_model(pipeline):
    """B-plan: project-level model_settings.r2v_model is the default
    Storyboard's R2V tab seeds from. Verify it round-trips: writing
    via update_model_settings persists, get_script() sees it back."""
    from src.apps.comic_gen.models import Script
    script = Script(
        id="p1", title="P", original_text="t",
        created_at=time.time(), updated_at=time.time(),
    )
    pipeline.scripts = {"p1": script}

    # Default value is wan2.7-r2v per ModelSettings field default.
    assert script.model_settings.r2v_model == "wan2.7-r2v"

    # Exercise real temporary persistence: a mocked save cannot prove this
    # contract now that the response intentionally reads committed state.
    pipeline._save_data()
    updated = pipeline.update_model_settings("p1", r2v_model="kling-v3-r2v")
    assert updated.model_settings.r2v_model == "kling-v3-r2v"
    import json
    from pathlib import Path
    saved = Script.model_validate(json.loads(Path(pipeline.data_file).read_text())["p1"])
    assert saved.model_settings.r2v_model == "kling-v3-r2v"
    assert saved.revision == updated.revision == 1

    # Other fields untouched.
    assert updated.model_settings.i2v_model == script.model_settings.i2v_model


def test_create_video_task_accepts_r2v_with_refs(pipeline):
    pipeline.scripts = {"p1": _script_with_tasks()}
    # The snapshot copy logic also touches the filesystem; route a
    # bogus URL through the http branch so the function creates the task
    # while using real temp storage and committed return semantics.
    script, task_id = pipeline.create_video_task(
        script_id="p1",
        image_url="http://example.com/img.png",
        prompt="A scene",
        model="wan2.7-r2v",
        generation_mode="r2v",
        reference_image_urls=["http://example.com/ref1.png"],
    )

    assert task_id
    assert any(t.id == task_id for t in script.video_tasks)


# ---------------------------------------------------------------------------
# Storyboard R2V workbench persistence (P.1 + P.2 + P.3)
# ---------------------------------------------------------------------------


def _script_with_frame(frame: StoryboardFrame, *tasks: VideoTask) -> Script:
    return Script(
        id="p1",
        title="P",
        original_text="t",
        created_at=time.time(),
        updated_at=time.time(),
        frames=[frame],
        video_tasks=list(tasks),
    )


def test_storyboard_frame_workbench_fields_default_empty():
    """Old frames without workbench_* fields parse cleanly with defaults."""
    legacy_payload = {"id": "f1", "scene_id": "s1"}
    frame = StoryboardFrame.model_validate(legacy_payload)
    assert frame.workbench_tab_mode is None
    assert frame.t2i_image_urls == []
    assert frame.t2i_selected_index == 0
    assert frame.workbench_generate_count == 1


def test_storyboard_frame_workbench_fields_round_trip():
    """New workbench state survives Pydantic round-trip."""
    frame = StoryboardFrame(
        id="f1",
        scene_id="s1",
        workbench_tab_mode="t2i_i2v",
        t2i_image_urls=["http://a", "http://b", "http://c"],
        t2i_selected_index=2,
        workbench_generate_count=4,
    )
    revived = StoryboardFrame.model_validate(frame.model_dump())
    assert revived.workbench_tab_mode == "t2i_i2v"
    assert revived.t2i_image_urls == ["http://a", "http://b", "http://c"]
    assert revived.t2i_selected_index == 2
    assert revived.workbench_generate_count == 4


def test_video_task_workbench_tab_default_none():
    """Existing VideoTask records without workbench_tab parse fine."""
    legacy_payload = {"id": "v1", "project_id": "p1", "image_url": "x", "prompt": "p"}
    task = VideoTask.model_validate(legacy_payload)
    assert task.workbench_tab is None


def test_update_frame_workbench_partial_writes(pipeline):
    frame = StoryboardFrame(id="f1", scene_id="s1")
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    updated = pipeline.update_frame_workbench(
        "p1", "f1", workbench_tab_mode="direct_r2v",
    )
    assert updated is not None
    assert updated.workbench_tab_mode == "direct_r2v"
    # Other fields untouched.
    assert updated.t2i_image_urls == []
    assert updated.t2i_selected_index == 0
    assert updated.workbench_generate_count == 1


def test_update_frame_workbench_rejects_unknown_tab_mode(pipeline):
    frame = StoryboardFrame(id="f1", scene_id="s1")
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    with pytest.raises(ValueError, match="workbench_tab_mode"):
        pipeline.update_frame_workbench("p1", "f1", workbench_tab_mode="bogus_tab")


def test_update_frame_workbench_caps_t2i_history_at_10(pipeline):
    """Server-side defense in depth — the client also caps but the
    server must not accept unbounded list growth."""
    frame = StoryboardFrame(id="f1", scene_id="s1")
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    long_list = [f"http://img-{i}" for i in range(15)]
    updated = pipeline.update_frame_workbench(
        "p1", "f1", t2i_image_urls=long_list,
    )
    assert updated is not None
    # FIFO: oldest dropped, newest retained.
    assert updated.t2i_image_urls == [f"http://img-{i}" for i in range(5, 15)]
    assert len(updated.t2i_image_urls) == 10


def test_update_frame_workbench_clamps_selected_index_against_new_list(pipeline):
    frame = StoryboardFrame(id="f1", scene_id="s1")
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    updated = pipeline.update_frame_workbench(
        "p1", "f1",
        t2i_image_urls=["http://a", "http://b"],
        t2i_selected_index=99,  # out of range
    )
    assert updated is not None
    assert updated.t2i_selected_index == 1  # clamped to len-1


def test_update_frame_workbench_clamps_selected_index_to_zero_when_empty(pipeline):
    frame = StoryboardFrame(
        id="f1", scene_id="s1",
        t2i_image_urls=["http://a"],
        t2i_selected_index=0,
    )
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    updated = pipeline.update_frame_workbench(
        "p1", "f1", t2i_image_urls=[], t2i_selected_index=5,
    )
    assert updated is not None
    assert updated.t2i_image_urls == []
    assert updated.t2i_selected_index == 0


def test_update_frame_workbench_clamps_generate_count_to_range(pipeline):
    # Both updates use real temporary persistence and return independent
    # committed snapshots, so the first response remains unchanged.
    frame = StoryboardFrame(id="f1", scene_id="s1")
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    too_high = pipeline.update_frame_workbench(
        "p1", "f1", workbench_generate_count=99,
    )
    assert too_high is not None
    assert too_high.workbench_generate_count == 6  # clamped to upper bound
    too_low = pipeline.update_frame_workbench(
        "p1", "f1", workbench_generate_count=0,
    )
    assert too_low is not None
    assert too_low.workbench_generate_count == 1  # clamped to lower bound
    assert too_high.workbench_generate_count == 6


def test_update_frame_workbench_filters_blank_t2i_urls(pipeline):
    frame = StoryboardFrame(id="f1", scene_id="s1")
    pipeline.scripts = {"p1": _script_with_frame(frame)}
    updated = pipeline.update_frame_workbench(
        "p1", "f1",
        t2i_image_urls=["http://a", "", "  ", "http://b", None],  # type: ignore[list-item]
    )
    assert updated is not None
    assert updated.t2i_image_urls == ["http://a", "http://b"]


def test_update_frame_workbench_returns_none_for_unknown(pipeline):
    pipeline.scripts = {"p1": _script_with_frame(StoryboardFrame(id="f1", scene_id="s1"))}
    assert pipeline.update_frame_workbench("p1", "ghost", workbench_tab_mode="t2i_i2v") is None
    assert pipeline.update_frame_workbench("ghost", "f1", workbench_tab_mode="t2i_i2v") is None


def test_create_video_task_persists_workbench_tab(pipeline):
    pipeline.scripts = {"p1": _script_with_tasks()}
    script, task_id = pipeline.create_video_task(
        script_id="p1",
        image_url="http://example.com/img.png",
        prompt="A scene",
        model="wan2.7-i2v",
        generation_mode="i2v",
        workbench_tab="t2i_i2v",
    )
    task = next(t for t in script.video_tasks if t.id == task_id)
    assert task.workbench_tab == "t2i_i2v"


def test_create_video_task_workbench_tab_defaults_to_none(pipeline):
    """Pre-Phase-2 callers don't supply workbench_tab — must not break."""
    pipeline.scripts = {"p1": _script_with_tasks()}
    script, task_id = pipeline.create_video_task(
        script_id="p1",
        image_url="http://example.com/img.png",
        prompt="A scene",
        model="wan2.7-i2v",
        generation_mode="i2v",
    )
    task = next(t for t in script.video_tasks if t.id == task_id)
    assert task.workbench_tab is None
