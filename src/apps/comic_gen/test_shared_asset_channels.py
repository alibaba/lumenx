"""Tests for LumenX Core shared-asset-pool *Wave A* (feeding channels):

  - library CRUD: create_library_asset (character/scene/prop) + list +
    update + delete (persisted to library_assets.json).
  - promote_asset_to_library: deep-copy from a project/series into the
    global pool with a fresh id; source asset left intact; 404 paths.
  - create_project(series_id=...): binds the new project as the next
    episode (episode_number = max + 1); series_id=None stays standalone.

Hermetic, mirroring test_shared_asset_pool.py: a bare ComicGenPipeline via
object.__new__ with only the attributes the exercised methods touch — no
real output/*.json is read or written (temp paths + a fake processor).

Design RFC: docs/plans/2026-06-18-lumenx-core-shared-asset-pool.md
"""

import os
import sys
import json
import threading

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

import pytest

from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.comic_gen.models import (
    Character,
    Scene,
    Prop,
    Script,
    Series,
    GlobalAssetLibrary,
)


# --------------------------------------------------------------------------
# builders (only required fields; rest take schema defaults)
# --------------------------------------------------------------------------
def _char(cid, name=None):
    return Character(id=cid, name=name or cid, description=f"desc-{cid}")


def _scene(sid, name=None):
    return Scene(id=sid, name=name or sid, description=f"desc-{sid}")


def _prop(pid, name=None):
    return Prop(id=pid, name=name or pid, description=f"desc-{pid}")


def _script(sid="ep1", series_id=None, characters=None, scenes=None, props=None):
    return Script(
        id=sid,
        title=f"title-{sid}",
        original_text="once upon a time",
        series_id=series_id,
        characters=list(characters or []),
        scenes=list(scenes or []),
        props=list(props or []),
        created_at=0.0,
        updated_at=0.0,
    )


def _series(sid="S", characters=None, scenes=None, props=None):
    return Series(
        id=sid,
        title=f"series-{sid}",
        characters=list(characters or []),
        scenes=list(scenes or []),
        props=list(props or []),
        created_at=0.0,
        updated_at=0.0,
    )


class _FakeProcessor:
    """Stand-in for self.script_processor so create_project needs no LLM."""
    def __init__(self, sid="ep_new"):
        self._sid = sid

    def create_draft_script(self, title, text):
        return _script(sid=self._sid)

    def parse_novel(self, title, text):
        return _script(sid=self._sid)


def _bare_pipeline(tmp_path, library=None, series_store=None, scripts=None,
                   script_processor=None):
    """Bare ComicGenPipeline with temp persistence paths and only the
    attributes the exercised methods read/write."""
    p = object.__new__(ComicGenPipeline)
    p._closed = False
    p._storage_failed = None
    p.library_store = library if library is not None else GlobalAssetLibrary()
    p.series_store = series_store if series_store is not None else {}
    p.scripts = scripts if scripts is not None else {}
    p._save_lock = threading.RLock()
    p.library_data_file = str(tmp_path / "library_assets.json")
    p.data_file = str(tmp_path / "projects.json")
    p.series_data_file = str(tmp_path / "series.json")
    if script_processor is not None:
        p.script_processor = script_processor
    from pathlib import Path
    from src.utils.json_transactions import JsonFileTransaction
    from src.utils.json_storage import DirectoryWriterLock
    p._writer_lock = DirectoryWriterLock(Path(p.library_data_file).parent)
    p._storage = JsonFileTransaction(Path(p.library_data_file).parent, (
        "projects.json", "series.json", "library_assets.json",
    ))
    p._committed = p._storage_payloads(("projects.json", "series.json", "library_assets.json"))
    return p


# --------------------------------------------------------------------------
# library CRUD
# --------------------------------------------------------------------------
def test_create_library_asset_all_types_persists(tmp_path):
    p = _bare_pipeline(tmp_path)

    # character WITH an image_url — exercises the AssetUnit/ImageVariant path
    # (the runtime-risky field names in create_library_asset).
    ch = p.create_library_asset("character", {"name": "Hero", "description": "d", "image_url": "output/assets/characters/x.png"})
    assert ch.id.startswith("char_") and ch.name == "Hero"
    sc = p.create_library_asset("scene", {"name": "Alley", "image_url": "output/assets/scenes/y.png"})
    assert sc.id.startswith("scene_") and sc.image_url == "output/assets/scenes/y.png"
    pr = p.create_library_asset("prop", {"name": "Gun"})
    assert pr.id.startswith("prop_")

    lib = p.list_library_assets()
    assert [c.id for c in lib.characters] == [ch.id]
    assert [s.id for s in lib.scenes] == [sc.id]
    assert [x.id for x in lib.props] == [pr.id]

    # persisted to the temp library file
    data = json.loads(open(p.library_data_file).read())
    assert data["characters"][0]["id"] == ch.id
    assert data["scenes"][0]["id"] == sc.id
    assert data["props"][0]["id"] == pr.id

    # invalid type rejected
    with pytest.raises(ValueError):
        p.create_library_asset("video", {"name": "nope"})


def test_create_library_character_tolerates_partial_payload(tmp_path):
    # Playground录入 calls this directly with a minimal payload.
    p = _bare_pipeline(tmp_path)
    ch = p.create_library_asset("character", {})
    assert ch.name == "未命名"


def test_update_and_delete_library_asset(tmp_path):
    lib = GlobalAssetLibrary(characters=[_char("c1", "old")])
    p = _bare_pipeline(tmp_path, library=lib)

    updated = p.update_library_asset("character", "c1", {"name": "new", "starred": True, "id": "HACK", "status": "X"})
    assert updated.name == "new" and updated.starred is True
    assert updated.id == "c1"  # id is protected from patch

    # delete
    p.delete_library_asset("character", "c1")
    assert p.list_library_assets().characters == []
    data = json.loads(open(p.library_data_file).read())
    assert data["characters"] == []

    # delete absent -> ValueError
    with pytest.raises(ValueError):
        p.delete_library_asset("character", "ghost")


# --------------------------------------------------------------------------
# promote
# --------------------------------------------------------------------------
def test_promote_from_project_and_series(tmp_path):
    proj = _script(sid="p1", characters=[_char("pc", "proj-char")])
    ser = _series(sid="S", scenes=[_scene("ss", "ser-scene")])
    p = _bare_pipeline(tmp_path, scripts={"p1": proj}, series_store={"S": ser})

    promoted_c = p.promote_asset_to_library("project", "p1", "character", "pc")
    assert promoted_c.id != "pc"  # fresh id
    assert promoted_c.name == "proj-char"
    assert any(c.id == promoted_c.id for c in p.list_library_assets().characters)
    # original left intact
    assert proj.characters[0].id == "pc"

    promoted_s = p.promote_asset_to_library("series", "S", "scene", "ss")
    assert promoted_s.id != "ss" and promoted_s.name == "ser-scene"
    assert ser.scenes[0].id == "ss"  # original intact

    # 404-ish ValueErrors
    with pytest.raises(ValueError):
        p.promote_asset_to_library("project", "nope", "character", "pc")
    with pytest.raises(ValueError):
        p.promote_asset_to_library("project", "p1", "character", "ghost")
    with pytest.raises(ValueError):
        p.promote_asset_to_library("badkind", "p1", "character", "pc")


# --------------------------------------------------------------------------
# create_project(series_id) — episode binding + back-compat
# --------------------------------------------------------------------------
def test_create_project_binds_episode_when_series_id(tmp_path):
    ser = _series(sid="S")
    p = _bare_pipeline(tmp_path, series_store={"S": ser},
                       script_processor=_FakeProcessor(sid="ep_new"))

    script = p.create_project("New Ep", "text", skip_analysis=True, workflow_mode="r2v", series_id="S")
    assert script.series_id == "S"
    assert script.episode_number == 1  # first episode -> max(0)+1
    assert "ep_new" in p.scripts


def test_create_project_standalone_when_no_series_id(tmp_path):
    p = _bare_pipeline(tmp_path, script_processor=_FakeProcessor(sid="ep_solo"))
    script = p.create_project("Solo", "text", skip_analysis=True, workflow_mode="r2v")
    assert script.series_id is None
    assert "ep_solo" in p.scripts


def test_create_project_bad_series_raises(tmp_path):
    p = _bare_pipeline(tmp_path, script_processor=_FakeProcessor(sid="ep_bad"))
    with pytest.raises(ValueError):
        p.create_project("X", "text", skip_analysis=True, series_id="missing")
