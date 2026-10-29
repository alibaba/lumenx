"""Focused tests for the LumenX Core *global shared asset pool* foundation.

Covers the three "two-layer -> three-layer" resolver seams added for the
project-independent ``GlobalAssetLibrary`` (lowest priority layer:
Episode > Series > Global, dedup by id):

  (a) ``resolve_episode_assets`` folds the global library in at the
      correct priority.
  (b) ``_find_asset_with_source`` returns ``source="global"`` for a
      global-only asset, and ``_save_after_asset_mutation("global")``
      persists to ``library_assets.json`` (and *only* there).
  (c) Backward-compat: when the global library is empty every code path
      behaves bit-for-bit like the previous Episode/Series-only logic.

Plus an integration check that ``GET /projects/{id}`` tags global-only
assets with ``source="global"`` via FastAPI's ``TestClient`` (skipped
with a reason if the app can't be imported in this environment).

Design RFC: docs/plans/2026-06-18-lumenx-core-shared-asset-pool.md

These tests build a *bare* ``ComicGenPipeline`` via ``object.__new__`` and
populate only the attributes the resolver/save seams touch. That avoids
the real ``__init__`` side effects (Demucs warmup thread + orphan-task
recovery, which can write the real ``output/projects.json``) and keeps
the suite hermetic — no real ``output/*.json`` is read or written.
"""

import os
import sys
import json
import threading

# Make ``src`` importable as a top-level package when pytest runs from the
# worktree root (mirrors the bootstrap in test_pipeline.py).
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
# tiny builders (only required fields; everything else takes schema defaults)
# --------------------------------------------------------------------------
def _char(cid: str, name: str = None) -> Character:
    return Character(id=cid, name=name or cid, description=f"desc-{cid}")


def _scene(sid: str, name: str = None) -> Scene:
    return Scene(id=sid, name=name or sid, description=f"desc-{sid}")


def _prop(pid: str, name: str = None) -> Prop:
    return Prop(id=pid, name=name or pid, description=f"desc-{pid}")


def _script(sid="ep1", series_id=None, characters=None, scenes=None, props=None) -> Script:
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


def _series(sid="S", characters=None, scenes=None, props=None) -> Series:
    return Series(
        id=sid,
        title=f"series-{sid}",
        characters=list(characters or []),
        scenes=list(scenes or []),
        props=list(props or []),
        created_at=0.0,
        updated_at=0.0,
    )


def _bare_pipeline(library: GlobalAssetLibrary = None, series_store=None,
                   library_data_file="output/library_assets.json") -> ComicGenPipeline:
    """A ComicGenPipeline with *only* the attributes the resolver/save
    seams read — no heavy __init__, no real-file I/O."""
    p = object.__new__(ComicGenPipeline)
    p._closed = False
    p._storage_failed = None
    p.library_store = library if library is not None else GlobalAssetLibrary()
    p.series_store = series_store if series_store is not None else {}
    p._save_lock = threading.RLock()
    p.library_data_file = library_data_file
    # Routing targets for _save_after_asset_mutation's non-global branches;
    # set to sentinel paths so an accidental write to the wrong file is
    # observable in tests rather than silently hitting real output/.
    p.data_file = "output/__should_not_write_projects.json"
    p.series_data_file = "output/__should_not_write_series.json"
    p.scripts = {}
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
# (a) resolve_episode_assets folds global in at Episode > Series > Global
# --------------------------------------------------------------------------
def test_resolve_folds_global_at_lowest_priority():
    # Same id present in multiple layers -> the higher layer must win.
    episode = _script(
        sid="ep1",
        series_id="S",
        characters=[_char("c_ep", "ep-only"), _char("c_dup", "dup-EP")],
    )
    series = _series(
        sid="S",
        characters=[_char("c_ser", "ser-SER"), _char("c_dup", "dup-SER")],
    )
    library = GlobalAssetLibrary(
        characters=[
            _char("c_dup", "dup-GLOB"),   # shadowed by episode
            _char("c_ser", "ser-GLOB"),   # shadowed by series
            _char("c_glob", "glob-only"),  # only the global layer has this
        ],
        scenes=[_scene("s_glob", "scene-glob")],   # no episode/series scenes
        props=[_prop("p_glob", "prop-glob")],      # no episode/series props
    )
    p = _bare_pipeline(library=library, series_store={"S": series})

    resolved = p.resolve_episode_assets(episode, series)

    # Order: episode first, then series ids absent locally, then global ids
    # absent from both.
    assert [c.id for c in resolved["characters"]] == ["c_ep", "c_dup", "c_ser", "c_glob"]
    by_id = {c.id: c for c in resolved["characters"]}
    assert by_id["c_dup"].name == "dup-EP"     # episode wins over series & global
    assert by_id["c_ser"].name == "ser-SER"    # series wins over global
    assert by_id["c_glob"].name == "glob-only"  # global-only survives

    # Scenes/props had no episode/series entries -> come entirely from global.
    assert [s.id for s in resolved["scenes"]] == ["s_glob"]
    assert [pr.id for pr in resolved["props"]] == ["p_glob"]


def test_resolve_folds_global_without_parent_series():
    # Standalone episode (no series): episode-local sits on top of global.
    episode = _script(
        sid="ep1",
        series_id=None,
        characters=[_char("c_ep", "ep-only"), _char("c_dup", "dup-EP")],
    )
    library = GlobalAssetLibrary(
        characters=[_char("c_dup", "dup-GLOB"), _char("c_glob", "glob-only")],
        scenes=[_scene("s_glob")],
        props=[_prop("p_glob")],
    )
    p = _bare_pipeline(library=library)

    resolved = p.resolve_episode_assets(episode)  # no series passed, none exists

    assert [c.id for c in resolved["characters"]] == ["c_ep", "c_dup", "c_glob"]
    assert {c.id: c for c in resolved["characters"]}["c_dup"].name == "dup-EP"
    assert [s.id for s in resolved["scenes"]] == ["s_glob"]
    assert [pr.id for pr in resolved["props"]] == ["p_glob"]


# --------------------------------------------------------------------------
# (b) _find_asset_with_source -> "global" + _save_after_asset_mutation routing
# --------------------------------------------------------------------------
def test_find_asset_with_source_returns_global():
    library = GlobalAssetLibrary(
        characters=[_char("g_char", "glob-char")],
        scenes=[_scene("g_scene", "glob-scene")],
        props=[_prop("g_prop", "glob-prop")],
    )
    # Episode has a local char, and belongs to a series whose pool also
    # contains an id that the library duplicates — proves precedence order
    # script > series > global.
    series = _series(sid="S", characters=[_char("shared", "from-SERIES")])
    library.characters.append(_char("shared", "from-GLOBAL"))
    episode = _script(sid="ep1", series_id="S", characters=[_char("local1", "local")])
    p = _bare_pipeline(library=library, series_store={"S": series})

    # local id -> "script" (episode wins)
    asset, source = p._find_asset_with_source(episode, "local1", "character")
    assert source == "script" and asset is episode.characters[0]

    # id present in both series and global -> "series" (series wins over global)
    asset, source = p._find_asset_with_source(episode, "shared", "character")
    assert source == "series" and asset.name == "from-SERIES"

    # global-only ids across all three types -> "global"
    asset, source = p._find_asset_with_source(episode, "g_char", "character")
    assert source == "global" and asset.id == "g_char"
    asset, source = p._find_asset_with_source(episode, "g_scene", "scene")
    assert source == "global" and asset.id == "g_scene"
    asset, source = p._find_asset_with_source(episode, "g_prop", "prop")
    assert source == "global" and asset.id == "g_prop"

    # genuine miss -> (None, None)
    assert p._find_asset_with_source(episode, "nope", "character") == (None, None)


def test_save_after_asset_mutation_global_writes_library_file(tmp_path):
    lib_file = tmp_path / "library_assets.json"
    library = GlobalAssetLibrary(characters=[_char("g_char", "glob-char")])
    p = _bare_pipeline(library=library, library_data_file=str(lib_file))

    # Mutate the global asset, then persist via the "global" routing branch.
    p.library_store.characters[0].starred = True
    p._save_after_asset_mutation("global")

    assert lib_file.exists(), "global mutation must write library_assets.json"
    data = json.loads(lib_file.read_text())
    assert [c["id"] for c in data["characters"]] == ["g_char"]
    assert data["characters"][0]["starred"] is True

    # Routing isolation: the global branch must NOT touch projects/series files.
    assert not os.path.exists(p.data_file)
    assert not os.path.exists(p.series_data_file)


# --------------------------------------------------------------------------
# (c) backward compatibility: empty library == prior Episode/Series behavior
# --------------------------------------------------------------------------
def test_empty_library_is_byte_for_byte_noop():
    episode = _script(
        sid="ep1",
        series_id="S",
        characters=[_char("a"), _char("b")],
        scenes=[_scene("sa")],
        props=[_prop("pa")],
    )
    series = _series(
        sid="S",
        characters=[_char("c")],          # series-only character
        scenes=[_scene("sb")],            # series-only scene
        props=[],
    )

    # The exact two-layer (Episode > Series) merge the code produced *before*
    # the global pool existed — recomputed here as the oracle.
    ep_char_ids = {c.id for c in episode.characters}
    ep_scene_ids = {s.id for s in episode.scenes}
    ep_prop_ids = {pr.id for pr in episode.props}
    expected = {
        "characters": list(episode.characters) + [c for c in series.characters if c.id not in ep_char_ids],
        "scenes": list(episode.scenes) + [s for s in series.scenes if s.id not in ep_scene_ids],
        "props": list(episode.props) + [pr for pr in series.props if pr.id not in ep_prop_ids],
    }

    # With an *empty* global library the three-layer resolver must reproduce
    # the two-layer result exactly — same ids, same order, same instances.
    p = _bare_pipeline(library=GlobalAssetLibrary(), series_store={"S": series})
    resolved = p.resolve_episode_assets(episode, series)

    for key in ("characters", "scenes", "props"):
        assert [x.id for x in resolved[key]] == [x.id for x in expected[key]]
        # Same Python objects (no copies introduced) — proves byte-identity.
        assert all(a is b for a, b in zip(resolved[key], expected[key]))

    # And the no-series branch with an empty library == the episode's own lists.
    standalone = _script(sid="ep2", series_id=None,
                         characters=[_char("a"), _char("b")], scenes=[_scene("sa")])
    p2 = _bare_pipeline(library=GlobalAssetLibrary())
    resolved2 = p2.resolve_episode_assets(standalone)
    assert resolved2["characters"] == list(standalone.characters)
    assert resolved2["scenes"] == list(standalone.scenes)
    assert resolved2["props"] == list(standalone.props)
    assert all(a is b for a, b in zip(resolved2["characters"], standalone.characters))


# --------------------------------------------------------------------------
# integration: GET /projects/{id} tags global-only assets source="global"
# --------------------------------------------------------------------------
def test_get_project_merges_global_source(tmp_path):
    from fastapi.testclient import TestClient
    from src.apps.comic_gen import api

    pipeline = _bare_pipeline(library_data_file=str(tmp_path / "library_assets.json"))
    pipeline.data_dir = tmp_path
    import uuid
    sid = "ep-" + uuid.uuid4().hex[:8]
    script = _script(sid=sid, characters=[_char("epc", "ep-char")])

    # Prepare candidates, then commit them before exercising the public view.
    pipeline.scripts[sid] = script
    pipeline.library_store = GlobalAssetLibrary(
        characters=[_char("gc", "glob-char")],
        scenes=[_scene("gs", "glob-scene")],
        props=[_prop("gp", "glob-prop")],
    )
    test_app = api.create_app(
        data_dir=tmp_path,
        load_config=False,
        pipeline_instance=pipeline,
        recover_orphan_tasks=False,
        warmup_demucs=False,
    )
    with pipeline._writer_lock:
        pipeline._commit_storage("projects.json", "library_assets.json")
        saved_files = {
            name: (tmp_path / name).read_bytes()
            for name in ("projects.json", "library_assets.json")
        }
        with TestClient(test_app) as client:
            resp = client.get(f"/projects/{sid}")
        # Source labels are presentation-only and must not alter either file.
        for name, contents in saved_files.items():
            assert (tmp_path / name).read_bytes() == contents
    assert resp.status_code == 200, resp.text
    body = resp.json()

    chars = {c["id"]: c for c in body["characters"]}
    assert chars["epc"]["source"] == "episode"   # episode-local
    assert chars["gc"]["source"] == "global"      # folded from library
    scenes = {s["id"]: s for s in body["scenes"]}
    assert scenes["gs"]["source"] == "global"
    props = {pr["id"]: pr for pr in body["props"]}
    assert props["gp"]["source"] == "global"
