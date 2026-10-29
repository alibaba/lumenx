"""Pure generation intent normalization: no product state or paid providers."""

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from src.utils.generation_intent import IntentValidationError, normalize_generation_intent
from src.utils.model_catalog import CatalogAccessor, load_generated_model_catalog


ALL_PARAMETERS = {
    "duration", "ratio", "aspect_ratio", "resolution", "size", "quality", "seed",
    "negative_prompt", "prompt_extend", "watermark", "audio", "sound", "vidu_audio",
    "cfg_scale", "mode", "movement_amplitude", "shot_type",
}
CATALOG_CASES = [
    (model_id, capability, entry["legacy_model_id"])
    for model_id, entry in load_generated_model_catalog()["modes"].items()
    for capability in entry["capabilities"]
]


@pytest.fixture
def catalog():
    return CatalogAccessor(load_generated_model_catalog())


def normalize(request, catalog, *, preset=None, parameters=None, modes=None):
    return normalize_generation_intent(
        request,
        catalog=catalog,
        allowed_modes=modes or {"i2v", "r2v", "t2i", "i2i"},
        allowed_parameters=parameters if parameters is not None else {
            "duration", "ratio", "resolution", "seed", "negative_prompt", "prompt_extend", "watermark",
        },
        preset=preset,
    )


def request(**params):
    return {"model_id": "wan2.7-i2v", "mode": "i2v", "prompt": "海边日出", "params": params}


@pytest.mark.parametrize("model_id,mode,legacy_id", CATALOG_CASES)
def test_real_catalog_modes_and_legacy_aliases_have_serializable_equivalent_intents(
    catalog, model_id, mode, legacy_id,
):
    req = {"model_id": model_id, "mode": mode, "prompt": "catalog probe"}
    canonical = normalize(req, catalog, parameters=ALL_PARAMETERS, modes={mode})
    legacy = normalize(
        {**req, "model_id": legacy_id}, catalog, parameters=ALL_PARAMETERS, modes={mode},
    )
    assert canonical.model_id == model_id
    assert canonical.to_dict() == legacy.to_dict()
    assert canonical.fingerprint == legacy.fingerprint
    assert json.loads(json.dumps(canonical.to_dict())) == canonical.to_dict()


@pytest.mark.parametrize("field", ["prompt", "negative_prompt", "preset_id", "preset_version"])
def test_unencodable_json_strings_are_rejected_at_admission(catalog, field):
    value = json.loads(r'"\ud800"')
    req = request()
    preset = None
    if field == "prompt":
        req["prompt"] = value
    elif field == "negative_prompt":
        req["params"]["negative_prompt"] = value
    else:
        preset = {"id": "p", "version": "1", "mode": "i2v"}
        preset[field.removeprefix("preset_")] = value
    with pytest.raises(IntentValidationError, match="UTF-8"):
        normalize(req, catalog, preset=preset)


def test_valid_unicode_keeps_its_existing_parameter_fingerprint():
    # A fixed historical catalog proves serialization stability independently
    # from corrections to defaults offered for newly admitted requests.
    data = load_generated_model_catalog()
    data["modes"]["wan/wan2.7-video#i2v"]["params"]["ratio"] = {
        "options": ["16:9", "9:16"], "default": "16:9",
    }
    catalog = CatalogAccessor(data)
    req = request(negative_prompt="模糊❌", seed=0)
    req["prompt"] = "海边日出🌅"
    preset = {"id": "海边", "version": "第一版", "mode": "i2v"}
    result = normalize(req, catalog, preset=preset)
    assert result.fingerprint == "1765faa98c099791dc0da6461851b13f1fa7bdcf0e9e957c6052ddc15793a586"


@pytest.mark.parametrize("source", ["preset", "catalog"])
def test_unencodable_default_text_is_rejected_before_creating_an_intent(source):
    value = json.loads(r'"\udfff"')
    data = deepcopy(load_generated_model_catalog())
    preset = None
    if source == "preset":
        preset = {"id": "p", "version": "1", "mode": "i2v", "params": {"negative_prompt": value}}
    else:
        data["modes"]["wan/wan2.7-video#i2v"]["params"]["negativePrompt"] = {"default": value}
    with pytest.raises(IntentValidationError, match="UTF-8"):
        normalize(request(), CatalogAccessor(data), preset=preset)


def test_wan3_auto_duration_and_official_boolean_defaults_are_catalog_opt_in(catalog):
    result = normalize(
        {
            "model_id": "wan3.0-video",
            "mode": "i2v",
            "prompt": "test",
            "params": {
                "duration": -1,
                "seed": 0,
                "prompt_extend": False,
                "watermark": False,
                "audio": False,
            },
        },
        catalog,
        parameters={"duration", "seed", "prompt_extend", "watermark", "audio"},
    )

    assert result.model_id == "wan/wan3.0-video#video"
    assert result.catalog_mode == "video"
    assert result.mode == "i2v"
    assert result.params == {
        "audio": False,
        "duration": -1,
        "prompt_extend": False,
        "seed": 0,
        "watermark": False,
    }
    assert result.sources["params.duration"] == "request"

    defaults = normalize(
        {"model_id": "wan3.0-video-prime", "mode": "t2v", "prompt": "test"},
        catalog,
        parameters={"duration", "prompt_extend", "watermark", "audio"},
        modes={"t2v"},
    )
    assert defaults.model_id == "wan/wan3.0-video-prime#video"
    assert defaults.params == {
        "audio": True,
        "duration": 5,
        "prompt_extend": True,
        "watermark": False,
    }
    assert defaults.sources["params.duration"] == "catalog"
    assert defaults.sources["params.prompt_extend"] == "catalog"

    rich_options = normalize(
        {
            "model_id": "wan3.0-video",
            "mode": "i2v",
            "prompt": "test",
            "params": {"duration": 30, "resolution": "480P", "ratio": "21:9"},
        },
        catalog,
        parameters={"duration", "resolution", "ratio"},
    )
    assert rich_options.params == {"duration": 30, "ratio": "21:9", "resolution": "480P"}


def test_auto_duration_sentinel_is_rejected_without_catalog_opt_in(catalog):
    with pytest.raises(IntentValidationError):
        normalize(request(duration=-1), catalog)
    with pytest.raises(IntentValidationError):
        normalize(
            {"model_id": "wan3.0-video", "mode": "i2v", "prompt": "test", "params": {"seed": -2}},
            catalog, parameters={"seed"},
        )


def test_real_catalog_supplies_only_known_defaults(catalog):
    result = normalize(request(), catalog)
    assert result.model_id == "wan/wan2.7-video#i2v"
    assert result.params == {"duration": 5, "resolution": "1080p"}
    assert result.sources["model_id"] == "request"
    assert result.sources["params.duration"] == "catalog"
    # Boolean catalog entries describe support, including seed and text fields.
    assert "watermark" not in result.params
    assert "seed" not in result.params
    assert "negative_prompt" not in result.params


def test_model_default_comes_from_catalog_for_requested_mode(catalog):
    result = normalize({"mode": "i2v", "prompt": "test"}, catalog)
    assert result.model_id == "happyhorse/happyhorse-1.1-video#i2v"
    assert result.sources["model_id"] == "catalog"


def test_current_happyhorse_t2v_catalog_uses_the_corrected_identity(catalog):
    result = normalize(
        {"mode": "t2v", "prompt": "test", "model_id": "happyhorse-1.1-t2v"},
        catalog, modes={"t2v"}, parameters={"duration"},
    )
    assert result.model_id == "happyhorse/happyhorse-1.1-t2v#t2v"
    assert result.catalog_mode == "t2v"
    assert result.mode == "t2v"


def test_historical_catalog_naming_debt_does_not_invent_a_new_model_identity():
    # The pure normalizer must follow its supplied catalog, not infer a new ID
    # from capability names. Product admission owns the old-request alias.
    historical_id = "happyhorse/happyhorse-1.1-t2v#i2v"
    historical_catalog = CatalogAccessor({
        "modes": {historical_id: {
            "id": historical_id, "mode": "i2v", "capabilities": ["t2v"], "params": {},
        }},
        "compat": {"legacy_model_ids": {"happyhorse-1.1-t2v": historical_id}},
    })
    result = normalize(
        {"mode": "t2v", "prompt": "test", "model_id": "happyhorse-1.1-t2v"},
        historical_catalog, modes={"t2v"}, parameters={"duration"},
    )
    assert result.model_id == historical_id
    assert result.catalog_mode == "i2v"
    assert result.mode == "t2v"


def test_image_mode_uses_catalog_capabilities_without_switching_model(catalog):
    result = normalize(
        {"mode": "t2i", "prompt": "test", "model_id": "wan2.7-image-pro"},
        catalog, parameters={"size", "seed"},
    )
    assert result.model_id == "wan/wan2.7-image-pro#image"
    assert result.params == {"size": "1280*1280"}


def test_explicit_input_overrides_selected_preset_then_catalog(catalog):
    preset = {"id": "portrait", "version": "2", "mode": "i2v", "model_id": "wan2.7-i2v",
              "params": {"duration": 10, "resolution": "720p", "watermark": True}}
    result = normalize({"mode": "i2v", "prompt": "test", "params": {"duration": 6, "watermark": False}},
                       catalog, preset=preset)
    assert result.params == {"duration": 6, "resolution": "720p", "watermark": False}
    assert result.sources["model_id"] == "preset"
    assert result.sources["params.duration"] == "request"
    assert result.sources["params.resolution"] == "preset"
    assert result.preset_id == "portrait"
    assert result.preset_version == "2"


@pytest.mark.parametrize("seed", [-1, 0, 123])
def test_explicit_false_zero_and_random_seed_are_preserved(catalog, seed):
    result = normalize(request(seed=seed, watermark=False, prompt_extend=False, negative_prompt=""), catalog)
    assert result.params["seed"] == seed
    assert result.params["watermark"] is False
    assert result.params["prompt_extend"] is False
    assert result.params["negative_prompt"] == ""
    assert result.sources["params.seed"] == "request"


def test_numeric_zero_and_catalog_numeric_default():
    # Test numeric normalization against a fixed historical capability. Current
    # workspace Kling deliberately does not advertise vendor-only cfg_scale.
    data = deepcopy(load_generated_model_catalog())
    data["modes"]["kling/kling-v3-video#i2v"]["params"]["cfgScale"] = {
        "min": 0, "max": 1, "default": 0.5,
    }
    catalog = CatalogAccessor(data)
    req = {"model_id": "kling-v3-i2v", "mode": "i2v", "prompt": "test", "params": {"cfg_scale": 0}}
    assert normalize(req, catalog, parameters={"cfg_scale"}).params == {"cfg_scale": 0}
    req.pop("params")
    result = normalize(req, catalog, parameters={"cfg_scale"})
    assert result.params == {"cfg_scale": 0.5}
    assert result.sources["params.cfg_scale"] == "catalog"


def test_current_workspace_kling_rejects_vendor_cfg_scale(catalog):
    req = {"model_id": "kling-v3-i2v", "mode": "i2v", "prompt": "test", "params": {"cfg_scale": 0}}
    with pytest.raises(IntentValidationError, match="does not support parameter: cfg_scale"):
        normalize(req, catalog, parameters={"cfg_scale"})


@pytest.mark.parametrize("params", [
    {"duration": None}, {"duration": 0}, {"duration": 2.5}, {"duration": True},
    {"duration": 99}, {"seed": -2}, {"seed": True}, {"seed": "0"},
    {"watermark": 0}, {"prompt_extend": "false"}, {"resolution": "bogus"},
    {"ratio": "auto"}, {"negative_prompt": []},
])
def test_invalid_explicit_values_are_never_replaced_by_defaults(catalog, params):
    with pytest.raises(IntentValidationError):
        normalize(request(**params), catalog)


@pytest.mark.parametrize("req", [
    {"mode": "i2v", "prompt": "test", "model_id": "unknown"},
    {"mode": "i2v", "prompt": "test", "model_id": None},
    {"mode": "i2v", "prompt": "test", "model_id": "wan2.7-r2v"},
    {"mode": "t2v", "prompt": "test"},
    {"prompt": "test", "model_id": "wan2.7-i2v"},
    {"mode": "i2v", "model_id": "wan2.7-i2v"},
])
def test_unknown_missing_or_incompatible_model_mode_fails(catalog, req):
    with pytest.raises(IntentValidationError):
        normalize(req, catalog, modes={"i2v", "r2v", "t2v"})


def test_entrance_mode_and_parameter_limits_are_enforced(catalog):
    with pytest.raises(IntentValidationError):
        normalize(request(), catalog, modes={"r2v"})
    with pytest.raises(IntentValidationError):
        normalize(request(seed=0), catalog, parameters={"duration"})
    assert normalize(request(), catalog, parameters={"duration"}).params == {"duration": 5}


@pytest.mark.parametrize("params", [{"seed": 0}, {"watermark": False}, {"prompt_extend": False}])
def test_explicit_unsupported_parameters_are_rejected_even_when_false(catalog, params):
    with pytest.raises(IntentValidationError):
        normalize({"model_id": "gpt-image-2", "mode": "t2i", "prompt": "test", "params": params}, catalog)


@pytest.mark.parametrize("req", [
    {"model_id": "wan2.7-i2v", "mode": "i2v", "prompt": "test", "api_key": "secret"},
    request(typo_seed=0),
    {"model_id": "wan2.7-i2v", "mode": "i2v", "prompt": "test", "params": None},
])
def test_unknown_fields_and_null_parameter_object_are_rejected(catalog, req):
    with pytest.raises(IntentValidationError):
        normalize(req, catalog)


def test_canonical_legacy_ids_and_parameter_spelling_have_same_intent(catalog):
    left = normalize(request(promptExtend=False), catalog)
    right_request = request(prompt_extend=False)
    right_request["model_id"] = "wan/wan2.7-video#i2v"
    right = normalize(right_request, catalog)
    assert left.to_dict() == right.to_dict()
    assert left.fingerprint == right.fingerprint


def test_alias_conflicts_are_rejected_but_equivalent_model_ids_are_accepted(catalog):
    with pytest.raises(IntentValidationError):
        normalize(request(promptExtend=True, prompt_extend=False), catalog)
    req = request()
    req["model_name"] = "happyhorse-1.1-i2v"
    with pytest.raises(IntentValidationError):
        normalize(req, catalog)
    req["model_name"] = "wan/wan2.7-video#i2v"
    assert normalize(req, catalog).model_id == "wan/wan2.7-video#i2v"


@pytest.mark.parametrize("preset", [
    {"id": "p", "mode": "i2v", "params": {}},
    {"id": "p", "version": "1", "mode": "r2v", "params": {}},
    {"id": "p", "version": "1", "mode": "i2v", "params": {"typo_seed": 1}},
])
def test_invalid_or_incompatible_preset_is_not_implicit_inheritance(catalog, preset):
    with pytest.raises(IntentValidationError):
        normalize(request(), catalog, preset=preset)


def test_explicit_model_does_not_make_an_unknown_preset_model_valid(catalog):
    preset = {"id": "p", "version": "1", "mode": "i2v", "model_id": "missing"}
    with pytest.raises(IntentValidationError):
        normalize(request(), catalog, preset=preset)


@pytest.mark.parametrize("value", [float("inf"), float("nan"), 10 ** 1000, 0.15],
                         ids=["infinity", "nan", "integer_overflow", "invalid_step"])
def test_invalid_numeric_parameter_is_a_validation_error(catalog, value):
    req = {"model_id": "kling-v3-i2v", "mode": "i2v", "prompt": "test", "params": {"cfg_scale": value}}
    with pytest.raises(IntentValidationError):
        normalize(req, catalog, parameters={"cfg_scale"})


def test_snapshot_and_fingerprint_survive_mutation_of_all_input_sources():
    data = load_generated_model_catalog()
    catalog = CatalogAccessor(data)
    req = request(seed=0)
    preset = {"id": "p", "version": "1", "mode": "i2v", "params": {"watermark": False}}
    result = normalize(req, catalog, preset=preset)
    before = result.to_dict()
    req["params"]["seed"] = 8
    preset["params"]["watermark"] = True
    data["modes"][result.model_id]["params"]["resolution"]["default"] = "720p"
    assert result.to_dict() == before
    copy = result.to_dict()
    copy["params"]["seed"] = 99
    assert result.params["seed"] == 0
    with pytest.raises(TypeError):
        result.params["seed"] = 9
    with pytest.raises(FrozenInstanceError):
        result.prompt = "new"


def test_hash_is_json_stable_and_changes_with_effective_intent(catalog):
    req = request(watermark=False, seed=0)
    result = normalize(req, catalog)
    reordered = json.loads(json.dumps(req, sort_keys=True, ensure_ascii=False))
    assert normalize(reordered, catalog).fingerprint == result.fingerprint
    assert len(result.fingerprint) == 64
    assert json.loads(json.dumps(result.to_dict(), ensure_ascii=False)) == result.to_dict()
    assert normalize(request(watermark=False, seed=1), catalog).fingerprint != result.fingerprint
    assert result.rules_version


def test_fingerprint_deliberately_includes_provenance_and_preset_version(catalog):
    implicit = normalize(request(), catalog)
    explicit = normalize(request(duration=5), catalog)
    assert implicit.params == explicit.params
    assert implicit.fingerprint != explicit.fingerprint
    assert "params.duration" not in implicit.request_fields
    assert "params.duration" in explicit.request_fields
    preset = {"id": "p", "version": "1", "mode": "i2v", "params": {"duration": 5}}
    first = normalize(request(), catalog, preset=preset)
    preset["version"] = "2"
    assert normalize(request(), catalog, preset=preset).fingerprint != first.fingerprint


def test_fixture_catalog_explicit_false_and_zero_defaults_have_catalog_origin():
    data = deepcopy(load_generated_model_catalog())
    entry = data["modes"]["wan/wan2.7-video#i2v"]
    entry["params"]["watermark"] = {"default": False}
    entry["params"]["seed"] = {"default": 0}
    result = normalize(request(), CatalogAccessor(data))
    assert result.params["watermark"] is False
    assert result.params["seed"] == 0
    assert result.sources["params.watermark"] == "catalog"
    assert result.sources["params.seed"] == "catalog"


@pytest.mark.parametrize("parameters", [
    {"duration": 10, "resolution": "1080P"},
    {"duration": 10},
    {"resolution": "1080P"},
    {},
])
def test_duration_resolution_rule_runs_after_request_preset_and_catalog_defaults(parameters):
    data = load_generated_model_catalog()
    entry = data["modes"]["pixverse/pixverse-v5.6-r2v#r2v"]
    entry["duration"] = {"options": [5, 8, 10], "default": 10, "options_by_resolution": {"720P": [5, 8, 10], "1080P": [5, 8]}}
    entry["params"]["resolution"] = {"options": ["720P", "1080P"], "default": "1080P"}
    req = {"model_id": "pixverse-v5.6-r2v", "mode": "r2v", "prompt": "test", "params": parameters}
    with pytest.raises(IntentValidationError, match="duration.*resolution"):
        normalize(req, CatalogAccessor(data), parameters={"duration", "resolution"})


def test_duration_resolution_rule_uses_resolved_preset_then_explicit_parameters():
    data = load_generated_model_catalog()
    data["modes"]["pixverse/pixverse-v5.6-r2v#r2v"]["duration"] = {"options": [5, 8, 10], "default": 5, "options_by_resolution": {"720P": [5, 8, 10], "1080P": [5, 8]}}
    data["modes"]["pixverse/pixverse-v5.6-r2v#r2v"]["params"]["resolution"] = {"options": ["720P", "1080P"], "default": "720P"}
    catalog = CatalogAccessor(data)
    req = {"model_id": "pixverse-v5.6-r2v", "mode": "r2v", "prompt": "test"}
    preset = {"id": "p", "version": "1", "mode": "r2v", "params": {"duration": 10, "resolution": "1080P"}}
    with pytest.raises(IntentValidationError, match="duration.*resolution"):
        normalize(req, catalog, preset=preset, parameters={"duration", "resolution"})
    result = normalize({**req, "params": {"resolution": "720P"}}, catalog, preset=preset, parameters={"duration", "resolution"})
    assert result.params == {"duration": 10, "resolution": "720P"}
    assert result.sources["params.duration"] == "preset"
    assert result.sources["params.resolution"] == "request"


def test_duration_resolution_rule_requires_frozen_resolution():
    data = load_generated_model_catalog()
    entry = data["modes"]["pixverse/pixverse-v5.6-r2v#r2v"]
    entry["duration"] = {"options": [5, 8, 10], "default": 5, "options_by_resolution": {"720P": [5, 8, 10], "1080P": [5, 8]}}
    with pytest.raises(IntentValidationError, match="resolution"):
        normalize({"model_id": "pixverse-v5.6-r2v", "mode": "r2v", "prompt": "test"}, CatalogAccessor(data), parameters={"duration"})


@pytest.mark.parametrize("resolution,duration", [("720P", 10), ("1080P", 5), ("1080P", 8)])
def test_real_pixverse_catalog_resolution_duration_combinations(catalog, resolution, duration):
    req = {"model_id": "pixverse-v5.6-r2v", "mode": "r2v", "prompt": "test", "params": {"resolution": resolution, "duration": duration}}
    result = normalize(req, catalog, parameters={"duration", "resolution"})
    assert result.params == req["params"]
    with pytest.raises(IntentValidationError, match="duration.*resolution"):
        normalize({**req, "params": {"duration": 10, "resolution": "1080P"}}, catalog, parameters={"duration", "resolution"})


@pytest.mark.parametrize("model_id", ["wan2.7-image", "wan2.7-image-pro", "qwen-image-2.0", "qwen-image-2.0-pro"])
def test_dashscope_image_custom_size_catalog_permission_is_i2i_only(catalog, model_id):
    payload = {"model_id": model_id, "mode": "i2i", "prompt": "Configured three views",
               "params": {"size": "1536*1024", "seed": 0, "watermark": False}}
    result = normalize(payload, catalog, parameters={"size", "seed", "watermark"}, modes={"i2i"})
    assert result.params == payload["params"]
    assert result.sources["params.size"] == "request" and "params.size" in result.request_fields
    with pytest.raises(IntentValidationError, match="size is outside catalog options"):
        normalize({**payload, "mode": "t2i"}, catalog, parameters={"size", "seed", "watermark"}, modes={"t2i"})


def test_custom_size_permission_keeps_types_other_fields_and_undeclared_models_strict():
    data = load_generated_model_catalog()
    # This flag must not become a generic parameter-enumeration bypass.
    data["modes"]["wan/wan2.7-image#image"]["params"]["quality"] = {
        "options": ["high"], "allow_custom_modes": ["i2i"],
    }
    catalog = CatalogAccessor(data)
    for params in ({"size": 1536}, {"size": "\ud800"}, {"quality": "low"}):
        with pytest.raises(IntentValidationError):
            normalize({"model_id": "wan2.7-image", "mode": "i2i", "prompt": "Test", "params": params},
                      catalog, parameters={"size", "quality"}, modes={"i2i"})
    with pytest.raises(IntentValidationError, match="size is outside catalog options"):
        normalize({"model_id": "gpt-image-2", "mode": "i2i", "prompt": "Test", "params": {"size": "1536*1024"}},
                  catalog, parameters={"size"}, modes={"i2i"})
