"""Real Seedance catalog contract for bounded HTTP video migration."""
import pytest
from src.utils.model_catalog import MODEL_CATALOG_ROOT, build_catalog_dict

@pytest.mark.parametrize("mode", ["t2v", "i2v", "r2v"])
def test_seedance_http_catalog_identity_defaults_and_product_media_policy(mode):
    catalog = build_catalog_dict(MODEL_CATALOG_ROOT)
    flat = f"seedance-2.0-{mode}"
    canonical = f"seedance/seedance-2.0-video#{mode}"
    assert catalog["compat"]["legacy_model_ids"][flat] == canonical
    entry = catalog["modes"][canonical]
    assert entry["runtime"]["mulerouter"] == {"gateway": "mulerouter", "api_model_id": flat}
    params = entry["params"]
    assert params["resolution"] == {"options": ["720p", "1080p"], "default": "1080p"}
    assert params["aspectRatio"]["default"] == "16:9"
    assert params["audio"] == {"default": True}
    assert params["seed"] == {"min": -1, "max": 4294967295}
    assert params["watermark"] == ({"default": False} if mode == "t2v" else False)
    assert params["negativePrompt"] is False
    assert params["promptExtend"] is False
    assert entry["duration"] == {"type": "slider", "min": 4, "max": 15, "step": 1, "default": 5}
    if mode != "t2v":
        images = entry["inputs"]["reference_images"]
        assert images["max"] == (1 if mode == "i2v" else 9)
        assert images["mime_types"] == ["image/jpeg", "image/png", "image/webp"]
        assert images["max_bytes"] == 20 * 1024 * 1024


@pytest.mark.parametrize("mode", ["t2v", "i2v", "r2v"])
def test_every_catalog_seedance_option_passes_provider_contract(mode):
    from src.models.seedance_core_contract import validate_seedance_intent
    from src.utils.generation_intent import GenerationIntent

    canonical = f"seedance/seedance-2.0-video#{mode}"
    entry = build_catalog_dict(MODEL_CATALOG_ROOT)["modes"][canonical]
    defaults = {"duration": entry["duration"]["default"], "resolution": entry["params"]["resolution"]["default"],
                "aspect_ratio": entry["params"]["aspectRatio"]["default"], "audio": entry["params"]["audio"]["default"]}
    if mode == "t2v":
        defaults["watermark"] = entry["params"]["watermark"]["default"]
    options = {"resolution": entry["params"]["resolution"]["options"],
               "aspect_ratio": entry["params"]["aspectRatio"]["options"],
               "duration": range(entry["duration"]["min"], entry["duration"]["max"] + 1),
               "audio": [False, True], "seed": [-1, 0, 4294967295]}
    for name, values in options.items():
        for value in values:
            validate_seedance_intent(GenerationIntent(canonical, mode, mode, "catalog contract", {**defaults, name: value}, {}, ()))
