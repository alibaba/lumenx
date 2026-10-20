"""Cross-phase integration tests (Phase 1 + 2 + 3).

Verifies that Series architecture (Phase 3) correctly integrates with
core pipeline (Phase 1) and interactive polish (Phase 2):
- PromptConfig 3-level fallback across polish workflows
- Asset resolution merging Series + Episode assets
- ModelSettings inheritance for Series
- Text splitting robustness
"""

import time
import uuid
import pytest
from unittest.mock import patch, MagicMock

from src.apps.comic_gen.models import (
    Series, Script, Character, Scene, Prop, PromptConfig, ModelSettings,
)
from src.apps.comic_gen.pipeline import ComicGenPipeline


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline(tmp_path):
    """Create a pipeline with temp data files, bypassing real IO."""
    with patch("src.apps.comic_gen.pipeline.ScriptProcessor") as MockSP, \
         patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        p = ComicGenPipeline()
    p.data_file = str(tmp_path / "projects.json")
    p.series_data_file = str(tmp_path / "series.json")
    p.scripts = {}
    p.series_store = {}
    return p


def _ts():
    return time.time()


def _make_script(**kw) -> Script:
    now = _ts()
    defaults = dict(
        id=str(uuid.uuid4()), title="Episode", original_text="text",
        created_at=now, updated_at=now,
    )
    defaults.update(kw)
    return Script(**defaults)


def _make_series(**kw) -> Series:
    now = _ts()
    defaults = dict(id=str(uuid.uuid4()), title="Series", created_at=now, updated_at=now)
    defaults.update(kw)
    return Series(**defaults)


def _make_character(name="Hero", **kw) -> Character:
    return Character(id=kw.pop("id", str(uuid.uuid4())), name=name,
                     description=kw.pop("description", "A brave hero"), **kw)


def _make_scene(name="Forest", **kw) -> Scene:
    return Scene(id=kw.pop("id", str(uuid.uuid4())), name=name,
                 description=kw.pop("description", "A dark forest"), **kw)


def _make_prop(name="Sword", **kw) -> Prop:
    return Prop(id=kw.pop("id", str(uuid.uuid4())), name=name,
                description=kw.pop("description", "A magic sword"), **kw)


# ===================================================================
# 1. PromptConfig 3-level fallback integration
# ===================================================================

class TestPromptConfigIntegration:
    """Tests that PromptConfig fallback works correctly across all polish types."""

    def test_all_valid_prompt_types_accepted(self, pipeline):
        """All three prompt types should be accepted."""
        ep = _make_script()
        for ptype in ("storyboard_polish", "video_polish", "r2v_polish"):
            result = pipeline.get_effective_prompt(ptype, ep)
            assert len(result.strip()) > 0, f"{ptype} should return non-empty default"

    def test_episode_overrides_series_for_all_types(self, pipeline):
        """Episode-level config should override Series-level for all types."""
        now = _ts()
        series = Series(
            id="s1", title="S", created_at=now, updated_at=now,
            prompt_config=PromptConfig(
                storyboard_polish="series storyboard",
                video_polish="series video",
                r2v_polish="series r2v",
            ),
        )
        ep = _make_script()
        ep.prompt_config = PromptConfig(
            storyboard_polish="ep storyboard",
            video_polish="ep video",
            r2v_polish="ep r2v",
        )

        for ptype, expected in [
            ("storyboard_polish", "ep storyboard"),
            ("video_polish", "ep video"),
            ("r2v_polish", "ep r2v"),
        ]:
            result = pipeline.get_effective_prompt(ptype, ep, series)
            assert result == expected

    def test_series_overrides_default_for_all_types(self, pipeline):
        """Series-level config should override system default."""
        now = _ts()
        series = Series(
            id="s1", title="S", created_at=now, updated_at=now,
            prompt_config=PromptConfig(
                storyboard_polish="series storyboard",
                video_polish="series video",
                r2v_polish="series r2v",
            ),
        )
        ep = _make_script()
        ep.prompt_config = PromptConfig()  # empty → fallback

        for ptype, expected in [
            ("storyboard_polish", "series storyboard"),
            ("video_polish", "series video"),
            ("r2v_polish", "series r2v"),
        ]:
            result = pipeline.get_effective_prompt(ptype, ep, series)
            assert result == expected

    def test_whitespace_only_episode_config_falls_through(self, pipeline):
        """Whitespace-only episode config should fall through to Series."""
        now = _ts()
        series = Series(
            id="s1", title="S", created_at=now, updated_at=now,
            prompt_config=PromptConfig(storyboard_polish="series prompt"),
        )
        ep = _make_script()
        ep.prompt_config = PromptConfig(storyboard_polish="   ")

        result = pipeline.get_effective_prompt("storyboard_polish", ep, series)
        assert result == "series prompt"

    def test_no_series_falls_to_default(self, pipeline):
        """Without series, empty episode config should fall to system default."""
        ep = _make_script()
        ep.prompt_config = PromptConfig()

        result = pipeline.get_effective_prompt("storyboard_polish", ep, series=None)
        from src.apps.comic_gen.llm import DEFAULT_STORYBOARD_POLISH_PROMPT
        assert result == DEFAULT_STORYBOARD_POLISH_PROMPT

    def test_episode_in_series_auto_resolves_prompt(self, pipeline):
        """Episode with series_id should auto-resolve series prompt config."""
        s = pipeline.create_series("S")
        s.prompt_config = PromptConfig(video_polish="auto-resolved series prompt")

        ep = _make_script(series_id=s.id)
        ep.prompt_config = PromptConfig()  # empty
        pipeline.scripts[ep.id] = ep
        pipeline.add_episode_to_series(s.id, ep.id)

        # When series is explicitly passed
        result = pipeline.get_effective_prompt("video_polish", ep, s)
        assert result == "auto-resolved series prompt"

    def test_invalid_prompt_type_raises(self, pipeline):
        ep = _make_script()
        with pytest.raises(ValueError, match="Invalid prompt_type"):
            pipeline.get_effective_prompt("nonexistent", ep)


# ===================================================================
# 2. Asset resolution integration (Phase 1 + 3)
# ===================================================================

class TestAssetResolutionIntegration:
    """Tests asset merging between Series and Episode."""

    def test_series_assets_supplement_episode(self, pipeline):
        """Series assets should be added when episode has no conflicting IDs."""
        s = pipeline.create_series("S")
        series_char = _make_character(name="Series Char")
        series_scene = _make_scene(name="Series Scene")
        series_prop = _make_prop(name="Series Prop")
        s.characters = [series_char]
        s.scenes = [series_scene]
        s.props = [series_prop]

        ep_char = _make_character(name="Ep Char")
        ep = _make_script(series_id=s.id, characters=[ep_char])
        pipeline.scripts[ep.id] = ep

        result = pipeline.resolve_episode_assets(ep)
        assert len(result["characters"]) == 2
        assert len(result["scenes"]) == 1
        assert len(result["props"]) == 1
        char_names = {c.name for c in result["characters"]}
        assert "Ep Char" in char_names
        assert "Series Char" in char_names

    def test_episode_asset_overrides_series_by_id(self, pipeline):
        """When episode and series have same asset ID, episode wins."""
        shared_id = "shared-id-123"
        s = pipeline.create_series("S")
        s.characters = [_make_character(name="Series Version", id=shared_id)]

        ep = _make_script(
            series_id=s.id,
            characters=[_make_character(name="Episode Version", id=shared_id)],
        )
        pipeline.scripts[ep.id] = ep

        result = pipeline.resolve_episode_assets(ep)
        assert len(result["characters"]) == 1
        assert result["characters"][0].name == "Episode Version"

    def test_no_series_returns_episode_only(self, pipeline):
        """Episode without series returns only its own assets."""
        ep_char = _make_character(name="Solo")
        ep = _make_script(characters=[ep_char])

        result = pipeline.resolve_episode_assets(ep)
        assert len(result["characters"]) == 1
        assert result["characters"][0].name == "Solo"
        assert result["scenes"] == []
        assert result["props"] == []

    def test_empty_series_returns_episode_only(self, pipeline):
        """Episode in a series with no shared assets returns only episode assets."""
        s = pipeline.create_series("Empty Series")
        ep = _make_script(series_id=s.id, characters=[_make_character()])
        pipeline.scripts[ep.id] = ep

        result = pipeline.resolve_episode_assets(ep)
        assert len(result["characters"]) == 1
        assert result["scenes"] == []
        assert result["props"] == []

    def test_multiple_asset_types_merge_independently(self, pipeline):
        """Each asset type (char/scene/prop) merges independently."""
        shared_scene_id = "scene-shared"
        s = pipeline.create_series("S")
        s.characters = [_make_character(name="S-Char")]
        s.scenes = [_make_scene(name="S-Scene", id=shared_scene_id)]
        s.props = [_make_prop(name="S-Prop")]

        ep = _make_script(
            series_id=s.id,
            scenes=[_make_scene(name="Ep-Scene", id=shared_scene_id)],  # override scene
        )
        pipeline.scripts[ep.id] = ep

        result = pipeline.resolve_episode_assets(ep)
        # Characters: 0 ep + 1 series = 1
        assert len(result["characters"]) == 1
        assert result["characters"][0].name == "S-Char"
        # Scenes: 1 ep (overrides series by ID) = 1
        assert len(result["scenes"]) == 1
        assert result["scenes"][0].name == "Ep-Scene"
        # Props: 0 ep + 1 series = 1
        assert len(result["props"]) == 1
        assert result["props"][0].name == "S-Prop"


# ===================================================================
# 3. ModelSettings inheritance integration
# ===================================================================

class TestModelSettingsIntegration:
    """Tests ModelSettings on Series model."""

    def test_series_has_default_model_settings(self):
        now = _ts()
        s = Series(id="s1", title="S", created_at=now, updated_at=now)
        assert isinstance(s.model_settings, ModelSettings)
        # Defaults follow the catalog (HappyHorse 1.0→1.1 upgrade: i2v
        # default moved to happyhorse-1.1-i2v, i2i default unified with
        # t2i on wan2.7-image-pro).
        assert s.model_settings.t2i_model == "wan2.7-image-pro"
        assert s.model_settings.i2v_model == "happyhorse-1.1-i2v"

    def test_update_series_model_settings_via_pipeline(self, pipeline):
        """Pipeline update_series should accept model_settings changes."""
        s = pipeline.create_series("S")
        new_ms = ModelSettings(t2i_model="custom-t2i", i2v_model="kling-1.6")
        updated = pipeline.update_series(s.id, {"model_settings": new_ms})
        assert updated.model_settings.t2i_model == "custom-t2i"
        assert updated.model_settings.i2v_model == "kling-1.6"
        # Other fields keep catalog defaults.
        assert updated.model_settings.i2i_model == "wan2.7-image-pro"

    def test_update_series_model_settings_partial_via_copy(self, pipeline):
        """Partial update via model_copy should preserve other fields."""
        s = pipeline.create_series("S")
        current_ms = s.model_settings
        updated_ms = current_ms.model_copy(update={"t2i_model": "new-model"})
        updated = pipeline.update_series(s.id, {"model_settings": updated_ms})
        assert updated.model_settings.t2i_model == "new-model"
        assert updated.model_settings.i2i_model == "wan2.7-image-pro"  # preserved
        assert updated.model_settings.storyboard_aspect_ratio == "16:9"  # preserved

    def test_model_settings_not_overwritten_by_id_or_created_at(self, pipeline):
        """update_series should not allow overwriting protected fields."""
        s = pipeline.create_series("S")
        original_id = s.id
        pipeline.update_series(s.id, {"id": "hacked", "created_at": 0})
        assert s.id == original_id
        assert s.created_at > 0


# ===================================================================
# 4. Episode lifecycle in Series (Phase 1 + 3)
# ===================================================================

class TestEpisodeLifecycleIntegration:
    """Tests the full episode lifecycle within a series."""

    def test_episode_reassignment_preserves_data(self, pipeline):
        """Moving episode between series preserves episode data."""
        s1 = pipeline.create_series("S1")
        s2 = pipeline.create_series("S2")
        ep = _make_script(title="My Episode", original_text="important text")
        pipeline.scripts[ep.id] = ep
        pipeline.add_episode_to_series(s1.id, ep.id)

        assert ep.series_id == s1.id
        assert ep.title == "My Episode"

        pipeline.add_episode_to_series(s2.id, ep.id)
        assert ep.series_id == s2.id
        assert ep.title == "My Episode"  # data preserved
        assert ep.original_text == "important text"

    def test_delete_series_orphans_episodes(self, pipeline):
        """Deleting a series should orphan episodes, not delete them."""
        s = pipeline.create_series("S")
        ep1 = _make_script(title="Ep1")
        ep2 = _make_script(title="Ep2")
        pipeline.scripts[ep1.id] = ep1
        pipeline.scripts[ep2.id] = ep2
        pipeline.add_episode_to_series(s.id, ep1.id)
        pipeline.add_episode_to_series(s.id, ep2.id)

        pipeline.delete_series(s.id)

        # Episodes should still exist but be orphaned
        assert ep1.id in pipeline.scripts
        assert ep2.id in pipeline.scripts
        assert ep1.series_id is None
        assert ep2.series_id is None
        assert ep1.episode_number is None

    def test_episode_numbering_sequential(self, pipeline):
        """Episodes added sequentially should get correct numbers."""
        s = pipeline.create_series("S")
        eps = []
        for i in range(3):
            ep = _make_script(title=f"Ep{i+1}")
            pipeline.scripts[ep.id] = ep
            pipeline.add_episode_to_series(s.id, ep.id, episode_number=i+1)
            eps.append(ep)

        episodes = pipeline.get_series_episodes(s.id)
        assert len(episodes) == 3
        for i, ep in enumerate(episodes):
            assert ep.episode_number == i + 1

    def test_remove_episode_renumber_not_automatic(self, pipeline):
        """Removing an episode should not auto-renumber remaining ones."""
        s = pipeline.create_series("S")
        ep1 = _make_script(title="Ep1")
        ep2 = _make_script(title="Ep2")
        ep3 = _make_script(title="Ep3")
        for ep in [ep1, ep2, ep3]:
            pipeline.scripts[ep.id] = ep
        pipeline.add_episode_to_series(s.id, ep1.id, episode_number=1)
        pipeline.add_episode_to_series(s.id, ep2.id, episode_number=2)
        pipeline.add_episode_to_series(s.id, ep3.id, episode_number=3)

        pipeline.remove_episode_from_series(s.id, ep2.id)

        # ep3 should keep its number
        assert ep3.episode_number == 3
        assert ep2.series_id is None


# ===================================================================
# 5. Text splitting robustness
# ===================================================================

class TestTextSplittingRobustness:
    """Additional edge cases for _split_text_by_markers."""

    def test_single_episode_gets_full_text(self, pipeline):
        text = "The complete story from beginning to end."
        episodes_data = [{"start_marker": "The", "end_marker": "end."}]
        chunks = pipeline._split_text_by_markers(text, episodes_data)
        assert len(chunks) == 1
        assert "complete story" in chunks[0]

    def test_overlapping_markers_handled_sequentially(self, pipeline):
        text = "AAABBBAAACCC"
        episodes_data = [
            {"start_marker": "AAA", "end_marker": "BBB"},
            {"start_marker": "AAA", "end_marker": "CCC"},
        ]
        chunks = pipeline._split_text_by_markers(text, episodes_data)
        assert len(chunks) == 2
        # First chunk starts at first AAA, second finds AAA after BBB
        assert "BBB" in chunks[0] or "AAA" in chunks[0]

    def test_empty_text_equal_split(self, pipeline):
        text = ""
        episodes_data = [
            {"start_marker": "X", "end_marker": "Y"},
            {"start_marker": "Z", "end_marker": "W"},
        ]
        chunks = pipeline._split_text_by_markers(text, episodes_data)
        assert len(chunks) == 2

    def test_unicode_markers(self, pipeline):
        text = "序章开始故事发展第一章正式开始主线剧情第二章高潮来临结局"
        episodes_data = [
            {"start_marker": "第一章", "end_marker": "主线剧情"},
            {"start_marker": "第二章", "end_marker": "结局"},
        ]
        chunks = pipeline._split_text_by_markers(text, episodes_data)
        assert len(chunks) == 2
        assert "第一章" in chunks[0]
        assert "第二章" in chunks[1]

    def test_many_episodes_split(self, pipeline):
        """Splitting into many episodes should produce correct count."""
        text = "A" * 100
        episodes_data = [{"start_marker": "", "end_marker": ""} for _ in range(10)]
        chunks = pipeline._split_text_by_markers(text, episodes_data)
        assert len(chunks) == 10
        total_len = sum(len(c) for c in chunks)
        assert total_len == 100


# ===================================================================
# 6. Cross-series import integration
# ===================================================================

class TestCrossSeriesImportIntegration:
    """Tests cross-series asset import with full lifecycle."""

    def test_imported_assets_independent_of_source(self, pipeline):
        """Imported assets should be deep copies, independent of source."""
        source = pipeline.create_series("Source")
        char = _make_character(name="Original Hero")
        source.characters = [char]

        target = pipeline.create_series("Target")
        result, imported, skipped = pipeline.import_assets_from_series(
            target.id, source.id, [char.id]
        )

        # Modify source asset
        source.characters[0].name = "Modified Hero"

        # Target should retain original name
        assert result.characters[0].name == "Original Hero"
        assert result.characters[0].id != char.id  # new ID

    def test_import_preserves_descriptions(self, pipeline):
        """Import should preserve all asset metadata."""
        source = pipeline.create_series("Source")
        char = _make_character(name="Hero", description="Wears red cape")
        scene = _make_scene(name="Castle", description="Ancient stone castle")
        source.characters = [char]
        source.scenes = [scene]

        target = pipeline.create_series("Target")
        result, imported, skipped = pipeline.import_assets_from_series(
            target.id, source.id, [char.id, scene.id]
        )

        imported_char = result.characters[0]
        imported_scene = result.scenes[0]
        assert imported_char.description == "Wears red cape"
        assert imported_scene.description == "Ancient stone castle"

    def test_import_empty_list_no_change(self, pipeline):
        """Import with empty list should not modify target."""
        source = pipeline.create_series("Source")
        source.characters = [_make_character()]
        target = pipeline.create_series("Target")

        result, imported, skipped = pipeline.import_assets_from_series(
            target.id, source.id, []
        )
        assert len(result.characters) == 0
        assert len(imported) == 0
        assert len(skipped) == 0


# ===================================================================
# 7. Series CRUD + persistence integration
# ===================================================================

class TestSeriesPersistenceIntegration:
    """Tests that Series CRUD correctly persists through save/load cycle."""

    def test_create_and_retrieve_series(self, pipeline):
        s = pipeline.create_series("Test Series", "A test description")
        retrieved = pipeline.get_series(s.id)
        assert retrieved is not None
        assert retrieved.title == "Test Series"
        assert retrieved.description == "A test description"

    def test_update_series_updates_timestamp(self, pipeline):
        s = pipeline.create_series("Old Title")
        old_ts = s.updated_at
        import time
        time.sleep(0.01)
        updated = pipeline.update_series(s.id, {"title": "New Title"})
        assert updated.updated_at > old_ts

    def test_list_series_returns_all(self, pipeline):
        pipeline.create_series("A")
        pipeline.create_series("B")
        pipeline.create_series("C")
        assert len(pipeline.list_series()) == 3

    def test_delete_series_removes_from_store(self, pipeline):
        s = pipeline.create_series("ToDelete")
        pipeline.delete_series(s.id)
        assert pipeline.get_series(s.id) is None
        assert s.id not in pipeline.series_store
