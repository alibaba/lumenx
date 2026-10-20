import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from src.utils.model_catalog import (
    MODEL_CATALOG_ROOT,
    build_catalog_dict,
    build_catalog_validation_report,
    build_provider_family_configs,
    get_catalog_accessor,
    get_default_model_settings,
    write_frontend_generated_catalog,
    write_generated_catalog,
)


def _write_yaml(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


class TestModelCatalog:
    def test_repo_catalog_builds_with_phase1_compatibility_defaults_and_legacy_model_ids(self):
        catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

        assert catalog["version"] == 1
        # Defaults point at the wan2.7 image family plus the HappyHorse 1.1
        # video line (2026-07 catalog upgrade) and include the unified
        # image_model surface used by the Atelier/Studio image path.
        assert catalog["defaults"]["model_settings"] == {
            "t2i_model": "wan2.7-image-pro",
            "i2i_model": "wan2.7-image-pro",
            "image_model": "wan2.7-image-pro",
            "i2v_model": "happyhorse-1.1-i2v",
            "r2v_model": "happyhorse-1.1-r2v",
        }

        models = catalog["models"]
        # wan2.6 entries remain in the catalog (hidden / legacy-visible)
        # so existing project files keep round-tripping.
        assert "wan2.6-t2i" in models
        assert "wan2.6-image" in models
        assert "wan2.6-i2v" in models
        assert "wan2.6-r2v" in models
        # The kling / vidu legacy ids gained an explicit modality suffix
        # during Phase 2 to disambiguate i2v vs r2v entries.
        assert "kling-v3-i2v" in models
        assert "viduq3-pro-i2v" in models
        assert "pixverse-v4-i2v" in models

        # wan2.6 video entries are deprecated and no longer surface in
        # any UI, but keep round-tripping for existing project files.
        assert models["wan2.6-i2v"]["status"] == "deprecated"
        assert models["wan2.6-i2v"]["ui"]["visible_in"] == []
        assert models["wan2.6-r2v"]["status"] == "deprecated"
        assert models["wan2.6-r2v"]["ui"]["visible_in"] == []

    def test_repo_catalog_emits_additive_mode_aware_sections(self):
        catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

        assert "model_lines" in catalog
        assert "modes" in catalog
        assert "compat" in catalog
        assert "legacy_model_ids" in catalog["compat"]

        assert "wan2.6-i2v" in catalog["models"]
        assert "wan2.6-r2v" in catalog["models"]
        assert "wan/wan2.6-video" in catalog["model_lines"]
        assert "wan/wan2.6-video#i2v" in catalog["modes"]
        assert "wan/wan2.6-video#r2v" in catalog["modes"]
        assert catalog["compat"]["legacy_model_ids"]["wan2.6-i2v"] == "wan/wan2.6-video#i2v"
        assert catalog["compat"]["legacy_model_ids"]["wan2.6-r2v"] == "wan/wan2.6-video#r2v"

    def test_mode_runtime_gateway_metadata_is_additive_and_routing_stays_family_based(self):
        catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

        canonical_mode_id = catalog["compat"]["legacy_model_ids"]["wan2.6-r2v"]
        assert catalog["modes"][canonical_mode_id]["runtime"]["dashscope"]["gateway"] == "dashscope"

        family_configs = build_provider_family_configs(catalog)
        family_map = {config.model_family: config for config in family_configs}
        assert family_map["wan2.6-"].backend_default == "dashscope"

    def test_visible_models_must_link_to_context_hub_docs(self):
        catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

        for model_id, model in catalog["models"].items():
            visible_in = model["ui"].get("visible_in", [])
            if visible_in:
                assert model["docs"]["context_hub_doc_ids"], model_id

    def test_generated_catalog_is_deterministic(self, tmp_path):
        first = tmp_path / "catalog-a.json"
        second = tmp_path / "catalog-b.json"

        write_generated_catalog(first, catalog_root=MODEL_CATALOG_ROOT)
        write_generated_catalog(second, catalog_root=MODEL_CATALOG_ROOT)

        assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")

    def test_frontend_generated_catalog_matches_backend_catalog(self, tmp_path):
        frontend_catalog_path = tmp_path / "frontend" / "src" / "generated" / "modelCatalog.json"

        written_path = write_frontend_generated_catalog(
            frontend_catalog_path,
            catalog_root=MODEL_CATALOG_ROOT,
        )

        assert written_path == frontend_catalog_path
        assert frontend_catalog_path.exists()
        assert build_catalog_dict(MODEL_CATALOG_ROOT) == json.loads(
            frontend_catalog_path.read_text(encoding="utf-8")
        )

    def test_catalog_derives_provider_family_configs(self):
        catalog = build_catalog_dict(MODEL_CATALOG_ROOT)
        family_configs = build_provider_family_configs(catalog)
        family_map = {config.model_family: config for config in family_configs}

        assert "wan2.6-" in family_map
        assert "wan2.5-" in family_map
        assert "wan2.2-" in family_map
        assert "kling-" in family_map
        assert "vidu" in family_map
        assert "pixverse-" in family_map

        assert family_map["kling-"].backend_env_key == "KLING_PROVIDER_MODE"
        assert family_map["vidu"].backend_env_key == "VIDU_PROVIDER_MODE"
        # Pixverse has no vendor backend yet — all pixverse routing
        # currently goes through dashscope, so no PROVIDER_MODE env
        # switch is wired in the catalog. Document the current state.
        assert family_map["pixverse-"].backend_env_key is None

    def test_default_model_settings_come_from_catalog(self):
        defaults = get_default_model_settings(MODEL_CATALOG_ROOT)

        assert defaults.t2i_model == "wan2.7-image-pro"
        assert defaults.i2i_model == "wan2.7-image-pro"
        assert defaults.i2v_model == "happyhorse-1.1-i2v"
        assert defaults.r2v_model == "happyhorse-1.1-r2v"

    def test_validation_report_passes_for_repo_catalog(self):
        catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

        report = build_catalog_validation_report(catalog, deepcopy(catalog))

        assert report.ok is True
        assert report.errors == ()
        assert report.stats["defaults"]["t2i_model"] == "wan2.7-image-pro"
        assert report.stats["surface_summary"]["video_sidebar"]["i2v"]

    def test_validation_report_detects_frontend_catalog_drift(self):
        catalog = build_catalog_dict(MODEL_CATALOG_ROOT)
        frontend_catalog = deepcopy(catalog)
        frontend_catalog["defaults"]["model_settings"]["i2v_model"] = "wan2.5-i2v-preview"

        report = build_catalog_validation_report(catalog, frontend_catalog)

        assert report.ok is False
        assert any("Frontend generated catalog does not match" in error for error in report.errors)

    def test_validation_report_detects_default_visibility_regression(self):
        catalog = build_catalog_dict(MODEL_CATALOG_ROOT)
        broken_catalog = deepcopy(catalog)
        # Target the current default I2V model (happyhorse-1.1-i2v after
        # the HappyHorse 1.0→1.1 upgrade) so the validation actually
        # fires — older defaults are no longer authoritative.
        broken_catalog["models"]["happyhorse-1.1-i2v"]["ui"]["visible_in"] = [
            "project_settings",
            "series_settings",
            "global_settings",
        ]

        report = build_catalog_validation_report(broken_catalog, deepcopy(broken_catalog))

        assert report.ok is False
        assert any("video_sidebar" in error for error in report.errors)


class TestModelCatalogValidation:
    def test_duplicate_model_ids_fail_validation(self, tmp_path):
        _write_yaml(
            tmp_path / "catalog.meta.yaml",
            {
                "version": 1,
                "defaults": {
                    "model_settings": {
                        "t2i_model": "wan2.6-t2i",
                        "i2i_model": "wan2.6-image",
                        # image_model is required by the catalog validator
                        # (added during the Phase 2 unified image surface
                        # work) — synthetic test catalogs must include it.
                        "image_model": "wan2.6-t2i",
                        "i2v_model": "wan2.6-i2v",
                    }
                },
            },
        )
        _write_yaml(
            tmp_path / "families" / "wan.yaml",
            {
                "family": "wan",
                "provider": "aliyun",
                "routing_prefixes": ["wan2.6-"],
                "supported_backends": ["dashscope"],
                "default_backend": "dashscope",
                "credential_sources": {"dashscope": ["DASHSCOPE_API_KEY"]},
                "supported_modalities": ["t2i", "i2i", "i2v", "r2v"],
                "transport": {
                    "image_input_mode": {"dashscope": "dashscope_multimodal_message"},
                    "audio_input_mode": {"dashscope": "dashscope_temp_file_url"},
                    "reference_video_input_mode": {"dashscope": "dashscope_temp_file_url"},
                },
                "docs": {"official_snapshot_ids": ["aliyun/wan/2026-04-03"]},
                "models": [
                    {
                        "id": "wan2.6-t2i",
                        "display_name": "Wan 2.6 T2I",
                        "description": "Latest T2I model",
                        "status": "active",
                        "release_stage": "stable",
                        "capabilities": ["t2i"],
                        "docs": {"context_hub_doc_ids": ["aliyun/wan-t2i"]},
                        "ui": {"selection_group": "t2i", "visible_in": ["project_settings"]},
                    },
                    {
                        "id": "wan2.6-t2i",
                        "display_name": "Wan 2.6 T2I Duplicate",
                        "description": "Duplicate",
                        "status": "active",
                        "release_stage": "stable",
                        "capabilities": ["t2i"],
                        "docs": {"context_hub_doc_ids": ["aliyun/wan-t2i"]},
                        "ui": {"selection_group": "t2i", "visible_in": ["project_settings"]},
                    },
                ],
            },
        )

        with pytest.raises(ValueError, match="Duplicate model id"):
            build_catalog_dict(tmp_path)

    def test_unsupported_backend_name_fails_validation(self, tmp_path):
        _write_yaml(
            tmp_path / "catalog.meta.yaml",
            {
                "version": 1,
                "defaults": {
                    "model_settings": {
                        "t2i_model": "wan2.6-t2i",
                        "i2i_model": "wan2.6-image",
                        # image_model is required by the catalog validator
                        # (added during the Phase 2 unified image surface
                        # work) — synthetic test catalogs must include it.
                        "image_model": "wan2.6-t2i",
                        "i2v_model": "wan2.6-i2v",
                    }
                },
            },
        )
        _write_yaml(
            tmp_path / "families" / "broken.yaml",
            {
                "family": "broken",
                "provider": "example",
                "routing_prefixes": ["broken-"],
                "supported_backends": ["dashscope", "mystery"],
                "default_backend": "dashscope",
                "credential_sources": {"dashscope": ["DASHSCOPE_API_KEY"]},
                "supported_modalities": ["i2v"],
                "transport": {
                    "image_input_mode": {"dashscope": "dashscope_image_to_video"},
                    "audio_input_mode": {"dashscope": "dashscope_temp_file_url"},
                    "reference_video_input_mode": {"dashscope": "dashscope_temp_file_url"},
                },
                "docs": {"official_snapshot_ids": ["example/broken/2026-04-03"]},
                "models": [
                    {
                        "id": "broken-v1",
                        "display_name": "Broken v1",
                        "description": "Invalid backend example",
                        "status": "active",
                        "release_stage": "stable",
                        "capabilities": ["i2v"],
                        "docs": {"context_hub_doc_ids": ["example/broken"]},
                        "ui": {"selection_group": "i2v", "visible_in": ["video_sidebar"]},
                    }
                ],
            },
        )

        with pytest.raises(ValueError, match="Unsupported backend"):
            build_catalog_dict(tmp_path)


class TestPhase2CatalogContract:
    """Phase 2: Treat additive metadata as official generated contract."""

    def test_model_lines_emitted_for_all_families(self):
        catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

        model_lines = catalog["model_lines"]
        families_with_lines = {ml["family"] for ml in model_lines.values()}

        for family_name in catalog["families"]:
            assert family_name in families_with_lines, (
                f"Family '{family_name}' has no model_lines entries"
            )

    def test_every_legacy_model_has_canonical_mapping(self):
        catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

        for model_id in catalog["models"]:
            assert model_id in catalog["compat"]["legacy_model_ids"], (
                f"Legacy model '{model_id}' missing from compat.legacy_model_ids"
            )
            canonical_id = catalog["compat"]["legacy_model_ids"][model_id]
            assert canonical_id in catalog["modes"], (
                f"Canonical mode '{canonical_id}' for '{model_id}' missing from modes"
            )

    def test_canonical_defaults_resolve_to_valid_modes(self):
        catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

        canonical_defaults = catalog["defaults"]["canonical_model_settings"]
        assert canonical_defaults, "canonical_model_settings must be present"

        for key, canonical_id in canonical_defaults.items():
            assert canonical_id in catalog["modes"], (
                f"Canonical default '{key}' -> '{canonical_id}' not in modes"
            )
            mode_entry = catalog["modes"][canonical_id]
            assert mode_entry["legacy_model_id"] in catalog["models"], (
                f"Mode '{canonical_id}' legacy_model_id not in models"
            )

    def test_mode_entries_have_required_metadata(self):
        catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

        for mode_id, mode in catalog["modes"].items():
            assert "model_line_id" in mode, f"{mode_id} missing model_line_id"
            assert "legacy_model_id" in mode, f"{mode_id} missing legacy_model_id"
            assert "mode" in mode, f"{mode_id} missing mode name"
            assert "runtime" in mode, f"{mode_id} missing runtime"
            assert "family" in mode, f"{mode_id} missing family"
            assert "ui" in mode, f"{mode_id} missing ui"
            assert mode["model_line_id"] in catalog["model_lines"], (
                f"{mode_id} references nonexistent model_line '{mode['model_line_id']}'"
            )

    def test_runtime_gateway_present_where_defined(self):
        catalog = build_catalog_dict(MODEL_CATALOG_ROOT)

        wan_video_modes = [
            mid for mid in catalog["modes"]
            if mid.startswith("wan/wan2.6-video#")
        ]
        assert wan_video_modes, "Expected wan2.6-video modes to exist"

        for mode_id in wan_video_modes:
            mode = catalog["modes"][mode_id]
            dashscope_runtime = mode["runtime"].get("dashscope", {})
            assert "gateway" in dashscope_runtime, (
                f"{mode_id} missing runtime.dashscope.gateway"
            )


class TestCatalogAccessor:
    """Phase 2: Test the CatalogAccessor helper API."""

    def test_resolve_legacy_to_canonical(self):
        accessor = get_catalog_accessor(build_catalog_dict(MODEL_CATALOG_ROOT))

        assert accessor.resolve_legacy_to_canonical("wan2.6-i2v") == "wan/wan2.6-video#i2v"
        assert accessor.resolve_legacy_to_canonical("wan2.6-r2v") == "wan/wan2.6-video#r2v"
        assert accessor.resolve_legacy_to_canonical("nonexistent") is None

    def test_resolve_canonical_to_legacy(self):
        accessor = get_catalog_accessor(build_catalog_dict(MODEL_CATALOG_ROOT))

        assert accessor.resolve_canonical_to_legacy("wan/wan2.6-video#i2v") == "wan2.6-i2v"
        assert accessor.resolve_canonical_to_legacy("wan/wan2.6-video#r2v") == "wan2.6-r2v"
        assert accessor.resolve_canonical_to_legacy("nonexistent") is None

    def test_resolve_to_flat_accepts_both_id_forms(self):
        accessor = get_catalog_accessor(build_catalog_dict(MODEL_CATALOG_ROOT))

        assert accessor.resolve_to_flat("wan2.6-i2v") == "wan2.6-i2v"
        assert accessor.resolve_to_flat("wan/wan2.6-video#i2v") == "wan2.6-i2v"
        assert accessor.resolve_to_flat("unknown-id") == "unknown-id"

    def test_get_mode_entry_returns_full_metadata(self):
        accessor = get_catalog_accessor(build_catalog_dict(MODEL_CATALOG_ROOT))

        entry = accessor.get_mode_entry("wan/wan2.6-video#i2v")
        assert entry is not None
        assert entry["model_line_id"] == "wan/wan2.6-video"
        assert entry["legacy_model_id"] == "wan2.6-i2v"
        assert entry["mode"] == "i2v"
        assert entry["family"] == "wan"

    def test_get_mode_runtime(self):
        accessor = get_catalog_accessor(build_catalog_dict(MODEL_CATALOG_ROOT))

        runtime = accessor.get_mode_runtime("wan/wan2.6-video#r2v")
        assert runtime is not None
        assert "dashscope" in runtime

        assert accessor.get_mode_runtime("nonexistent") is None

    def test_get_mode_product(self):
        accessor = get_catalog_accessor(build_catalog_dict(MODEL_CATALOG_ROOT))

        ui = accessor.get_mode_product("wan/wan2.6-video#i2v")
        assert ui is not None
        assert ui["selection_group"] == "i2v"

        assert accessor.get_mode_product("nonexistent") is None

    def test_get_gateway(self):
        accessor = get_catalog_accessor(build_catalog_dict(MODEL_CATALOG_ROOT))

        assert accessor.get_gateway("wan/wan2.6-video#r2v") == "dashscope"
        assert accessor.get_gateway("wan/wan2.6-video#r2v", "vendor") is None
        assert accessor.get_gateway("nonexistent") is None

    def test_enumeration_helpers(self):
        accessor = get_catalog_accessor(build_catalog_dict(MODEL_CATALOG_ROOT))

        canonical_ids = accessor.all_canonical_mode_ids()
        legacy_ids = accessor.all_legacy_model_ids()
        line_ids = accessor.all_model_line_ids()

        assert len(canonical_ids) == len(legacy_ids)
        assert len(canonical_ids) > 0
        assert len(line_ids) > 0
        assert all("#" in cid for cid in canonical_ids)

    def test_canonical_defaults(self):
        accessor = get_catalog_accessor(build_catalog_dict(MODEL_CATALOG_ROOT))

        defaults = accessor.canonical_defaults()
        assert "t2i_model" in defaults
        assert "i2i_model" in defaults
        assert "i2v_model" in defaults
        assert all("#" in v for v in defaults.values())


class TestGetGatewayForModel:
    """Phase 2 Task 6: Tests for provider_registry.get_gateway_for_model()."""

    def test_gateway_lookup_with_flat_id(self):
        from src.utils.provider_registry import get_gateway_for_model

        result = get_gateway_for_model("wan2.6-i2v")
        assert result == "dashscope"

    def test_gateway_lookup_with_canonical_id(self):
        from src.utils.provider_registry import get_gateway_for_model

        result = get_gateway_for_model("wan/wan2.6-video#i2v")
        assert result == "dashscope"

    def test_gateway_lookup_vendor_backend(self):
        from src.utils.provider_registry import get_gateway_for_model

        # Phase 2 catalog migration split kling-v3 into mode-suffixed
        # legacy ids (kling-v3-i2v / kling-v3-r2v).
        result = get_gateway_for_model("kling-v3-i2v", backend="vendor")
        assert result == "kling"

    def test_gateway_lookup_dashscope_backend_for_dual_provider(self):
        from src.utils.provider_registry import get_gateway_for_model

        result = get_gateway_for_model("kling-v3-i2v", backend="dashscope")
        assert result == "dashscope"

    def test_gateway_lookup_vidu_vendor(self):
        from src.utils.provider_registry import get_gateway_for_model

        # viduq3-pro likewise split into viduq3-pro-i2v / -r2v.
        result = get_gateway_for_model("viduq3-pro-i2v", backend="vendor")
        assert result == "vidu"

    def test_gateway_lookup_returns_none_for_unknown_model(self):
        from src.utils.provider_registry import get_gateway_for_model

        result = get_gateway_for_model("nonexistent-model")
        assert result is None

    def test_gateway_defaults_to_dashscope_when_backend_omitted(self):
        from src.utils.provider_registry import get_gateway_for_model

        result_explicit = get_gateway_for_model("wan2.6-r2v", backend="dashscope")
        result_default = get_gateway_for_model("wan2.6-r2v")
        assert result_explicit == result_default == "dashscope"
