"""Regression tests for variant selection on R2V reference sheets.

Two user-visible bugs are locked down here:

1. ``select_asset_variant`` silently no-op'd (HTTP 200, nothing changed) for
   the Cast / R2V-v2 flow. Its variants live in ``Character.reference_sheet``,
   an ``AssetUnit`` holding ``image_variants`` + ``selected_image_id``, while
   the fallback path only searched the legacy ``ImageAsset`` containers
   (``variants`` + ``selected_id``). Picking a different reference image in
   the Cast workbench therefore had no effect.

2. ``POST /projects/{id}/assets/variant/select`` (and favorite / voice bind)
   returned an episode-only project payload. The frontend replaces its store
   with that response, so series-shared cast/scene/prop entries vanished from
   the UI until a manual reload. The payload builder is now shared with
   ``GET /projects/{id}``.

The pipeline tests build a *bare* ``ComicGenPipeline`` via ``object.__new__``
(same approach as ``test_shared_asset_pool.py``) so no real ``output/*.json``
is read or written.
"""

import os
import sys
import threading

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

import pytest

from src.apps.comic_gen.models import (
    AssetUnit,
    Character,
    ImageVariant,
    Script,
    Series,
)
from src.apps.comic_gen.pipeline import ComicGenPipeline


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------
def _variant(vid: str, url: str) -> ImageVariant:
    return ImageVariant(id=vid, url=url, created_at=0.0)


def _sheet(first_id: str = "v1", second_id: str = "v2") -> AssetUnit:
    """A reference sheet pre-populated with two candidate images."""
    return AssetUnit(
        selected_image_id=first_id,
        image_variants=[
            _variant(first_id, f"assets/characters/{first_id}.png"),
            _variant(second_id, f"assets/characters/{second_id}.png"),
        ],
    )


def _character(cid: str = "c1", sheet: AssetUnit = None) -> Character:
    char = Character(id=cid, name=f"char-{cid}", description=f"desc-{cid}")
    if sheet is not None:
        char.reference_sheet = sheet
    return char


def _script(sid: str = "ep1", series_id: str = None) -> Script:
    return Script(
        id=sid,
        title=f"title-{sid}",
        original_text="once upon a time",
        series_id=series_id,
        created_at=0.0,
        updated_at=0.0,
    )


def _series(sid: str = "S1", characters=None) -> Series:
    return Series(
        id=sid,
        title=f"series-{sid}",
        characters=list(characters or []),
        created_at=0.0,
        updated_at=0.0,
    )


def _bare_pipeline(scripts=None, series_store=None) -> ComicGenPipeline:
    p = object.__new__(ComicGenPipeline)
    p.scripts = scripts if scripts is not None else {}
    p.series_store = series_store if series_store is not None else {}
    p._save_lock = threading.RLock()
    # Sentinel paths: any accidental disk write is obvious in the tests.
    p.data_file = "output/__should_not_write_projects.json"
    p.series_data_file = "output/__should_not_write_series.json"
    return p


# --------------------------------------------------------------------------
# 1. reference-sheet selection without an explicit generation_type
# --------------------------------------------------------------------------
def test_select_reference_sheet_variant_without_generation_type(monkeypatch):
    char = _character("c_series", _sheet("v1", "v2"))
    episode = _script("ep1", series_id="S1")
    series = _series("S1", characters=[char])
    p = _bare_pipeline(scripts={episode.id: episode}, series_store={"S1": series})

    saves = []
    monkeypatch.setattr(p, "_save_data", lambda: saves.append("projects"))
    monkeypatch.setattr(p, "_save_series_data", lambda: saves.append("series"))

    returned = p.select_asset_variant(episode.id, char.id, "character", "v2")

    # The pick must land on the reference sheet itself...
    assert char.reference_sheet.selected_image_id == "v2"
    # ...and on the legacy image_url the rest of the app reads.
    assert char.image_url == "assets/characters/v2.png"
    # Series-level mutations persist to the series store as well.
    assert saves == ["projects", "series"]
    assert returned.id == episode.id


def test_select_reference_sheet_variant_with_explicit_generation_type(monkeypatch):
    """The explicit `generation_type="reference_sheet"` path must not crash.

    It used to call the ImageAsset helper, which reads `.variants` — an
    AttributeError on an AssetUnit (500 response).
    """
    char = _character("c_series", _sheet("v1", "v2"))
    episode = _script("ep1", series_id="S1")
    p = _bare_pipeline(
        scripts={episode.id: episode},
        series_store={"S1": _series("S1", characters=[char])},
    )
    monkeypatch.setattr(p, "_save_data", lambda: None)
    monkeypatch.setattr(p, "_save_series_data", lambda: None)

    p.select_asset_variant(episode.id, char.id, "character", "v2", "reference_sheet")

    assert char.reference_sheet.selected_image_id == "v2"
    assert char.image_url == "assets/characters/v2.png"


def test_unknown_variant_id_leaves_sheet_untouched(monkeypatch):
    char = _character("c_series", _sheet("v1", "v2"))
    episode = _script("ep1", series_id="S1")
    p = _bare_pipeline(
        scripts={episode.id: episode},
        series_store={"S1": _series("S1", characters=[char])},
    )
    monkeypatch.setattr(p, "_save_data", lambda: None)
    monkeypatch.setattr(p, "_save_series_data", lambda: None)

    p.select_asset_variant(episode.id, char.id, "character", "does-not-exist")

    assert char.reference_sheet.selected_image_id == "v1"


# --------------------------------------------------------------------------
# 2. voice binding on series-shared characters (same root cause as (1):
#    the Cast workbench edits assets that live on the parent series)
# --------------------------------------------------------------------------
def test_bind_voice_on_series_shared_character(monkeypatch):
    char = _character("c_series", _sheet("v1", "v2"))
    episode = _script("ep1", series_id="S1")
    p = _bare_pipeline(
        scripts={episode.id: episode},
        series_store={"S1": _series("S1", characters=[char])},
    )
    saves = []
    monkeypatch.setattr(p, "_save_data", lambda: saves.append("projects"))
    monkeypatch.setattr(p, "_save_series_data", lambda: saves.append("series"))

    p.bind_voice(episode.id, char.id, "Mochi", "Mochi · 童声男")

    assert char.voice_id == "Mochi"
    assert char.voice_name == "Mochi · 童声男"
    # A shared character must persist to the series store, not the episode.
    assert saves == ["series"]


def test_bind_voice_on_episode_local_character(monkeypatch):
    char = Character(id="c_local", name="local", description="d")
    episode = _script("ep1")
    episode.characters = [char]
    p = _bare_pipeline(scripts={episode.id: episode})
    saves = []
    monkeypatch.setattr(p, "_save_data", lambda: saves.append("projects"))
    monkeypatch.setattr(p, "_save_series_data", lambda: saves.append("series"))

    p.bind_voice(episode.id, char.id, "Eldric Sage", "沉稳老者")

    assert char.voice_id == "Eldric Sage"
    assert saves == ["projects"]


# --------------------------------------------------------------------------
# 3. legacy containers keep working (backward compatibility)
# --------------------------------------------------------------------------
def test_legacy_full_body_variant_selection_still_works(monkeypatch):
    from src.apps.comic_gen.models import ImageAsset

    char = Character(id="c_local", name="local", description="d")
    char.full_body_asset = ImageAsset(
        selected_id="fb1",
        variants=[
            _variant("fb1", "assets/characters/fb1.png"),
            _variant("fb2", "assets/characters/fb2.png"),
        ],
    )
    # Standalone episode: the character lives on the episode itself.
    episode = _script("ep1")
    episode.characters = [char]
    p = _bare_pipeline(scripts={episode.id: episode})
    monkeypatch.setattr(p, "_save_data", lambda: None)

    p.select_asset_variant(episode.id, char.id, "character", "fb2", "full_body")

    assert char.full_body_asset.selected_id == "fb2"
    assert char.full_body_image_url == "assets/characters/fb2.png"


def test_standalone_character_without_sheet_is_not_saved_as_series(monkeypatch):
    """A no-op selection on a standalone episode must not touch the series store."""
    char = Character(id="c_local", name="local", description="d")
    episode = _script("ep1")
    p = _bare_pipeline(scripts={episode.id: episode})
    saves = []
    monkeypatch.setattr(p, "_save_data", lambda: saves.append("projects"))
    monkeypatch.setattr(p, "_save_series_data", lambda: saves.append("series"))

    p.select_asset_variant(episode.id, char.id, "character", "v2")

    assert saves == ["projects"]


# --------------------------------------------------------------------------
# 3. mutation responses carry the same merged payload as the read endpoint
# --------------------------------------------------------------------------
def test_merged_project_payload_includes_series_assets():
    try:
        from src.apps.comic_gen import api
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"cannot import api in this env: {exc!r}")

    char = _character("c_series", _sheet("v1", "v2"))
    episode = _script("ep-merge", series_id="S-merge")

    saved_series = api.pipeline.series_store
    api.pipeline.series_store = {"S-merge": _series("S-merge", characters=[char])}
    try:
        payload = api._merged_project_payload(episode)
    finally:
        api.pipeline.series_store = saved_series

    ids = [c["id"] for c in payload["characters"]]
    assert "c_series" in ids
    merged = next(c for c in payload["characters"] if c["id"] == "c_series")
    # Read-time provenance tag the frontend relies on.
    assert merged["source"] == "series"
