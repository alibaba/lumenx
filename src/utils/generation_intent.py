"""Normalize the parameter portion of a generation intent without side effects.

This is not a frozen media snapshot or approval to submit a paid request. Its
fingerprint includes effective parameters, their provenance, and preset/rule
versions. Receipt identity and the complete media-bound intent need separate
contracts; this digest is not a provider idempotency key.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any

from .model_catalog import CatalogAccessor, get_catalog_accessor


RULES_VERSION = "generation-parameters-v1"

# Spelling and scalar types only; support, options, bounds and defaults belong
# to the existing catalog. Provider-specific fields retain separate semantics.
_PARAMETERS = {
    "duration": ("duration", int),
    "ratio": ("ratio", str),
    "aspect_ratio": ("aspectRatio", str),
    "resolution": ("resolution", str),
    "size": ("size", str),
    "quality": ("quality", str),
    "seed": ("seed", int),
    "negative_prompt": ("negativePrompt", str),
    "prompt_extend": ("promptExtend", bool),
    "watermark": ("watermark", bool),
    "audio_setting": ("audioSetting", str),
    "audio": ("audio", bool),
    "sound": ("sound", bool),
    "vidu_audio": ("viduAudio", bool),
    "cfg_scale": ("cfgScale", float),
    "mode": ("mode", str),
    "movement_amplitude": ("movementAmplitude", str),
    "shot_type": ("shotType", str),
}
_SPELLINGS = {spelling: name for name, (spelling, _) in _PARAMETERS.items()}
_SPELLINGS.update({name: name for name in _PARAMETERS})


class IntentValidationError(ValueError):
    """An entrance, request, preset, or catalog value cannot be accepted."""


@dataclass(frozen=True)
class GenerationIntent:
    model_id: str
    catalog_mode: str
    mode: str
    prompt: str
    params: Mapping[str, Any]
    sources: Mapping[str, str]
    request_fields: tuple[str, ...]
    preset_id: str | None = None
    preset_version: str | None = None
    rules_version: str = RULES_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Return a detached, JSON-compatible parameter snapshot."""
        return {
            "rules_version": self.rules_version,
            "scope": "parameters-only",
            "model_id": self.model_id,
            "catalog_mode": self.catalog_mode,
            "mode": self.mode,
            "prompt": self.prompt,
            "params": dict(self.params),
            "sources": dict(self.sources),
            "request_fields": list(self.request_fields),
            "preset_id": self.preset_id,
            "preset_version": self.preset_version,
        }

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.to_dict(), sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _mapping(value: Any, allowed: Collection[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntentValidationError(f"{label} must be an object")
    if any(not isinstance(key, str) or key not in allowed for key in value):
        raise IntentValidationError(f"{label} contains unknown fields")
    return dict(value)


def _utf8_text(value: str, label: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise IntentValidationError(f"{label} must be UTF-8 encodable") from exc
    return value


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntentValidationError(f"{label} must be a nonempty string")
    return _utf8_text(value, label)


def _model_id(value: Any, catalog: CatalogAccessor) -> str:
    name = _name(value, "model_id")
    canonical = name if catalog.get_mode_entry(name) else catalog.resolve_legacy_to_canonical(name)
    if canonical is None or catalog.get_mode_entry(canonical) is None:
        raise IntentValidationError(f"Unknown model: {name}")
    return canonical


def _parameters(value: Any, allowed: Collection[str], label: str) -> dict[str, Any]:
    supplied = _mapping(value, _SPELLINGS, label)
    result = {}
    for spelling, item in supplied.items():
        name = _SPELLINGS[spelling]
        if name not in allowed:
            raise IntentValidationError(f"Entrance does not support parameter: {name}")
        if name in result and (type(item) is not type(result[name]) or item != result[name]):
            raise IntentValidationError(f"Conflicting aliases for parameter: {name}")
        result[name] = item
    return result


def _validate_parameter(name: str, value: Any, spec: Any, *, mode: str) -> Any:
    if spec is False or spec is None:
        raise IntentValidationError(f"Model does not support parameter: {name}")
    if spec is not True and not isinstance(spec, Mapping):
        raise IntentValidationError(f"Unknown catalog parameter contract: {name}")
    expected = _PARAMETERS[name][1]
    if expected is float:
        if type(value) not in (int, float):
            raise IntentValidationError(f"{name} must be a finite number")
        try:
            value = float(value)
        except OverflowError as exc:
            raise IntentValidationError(f"{name} must be a finite number") from exc
        if not math.isfinite(value):
            raise IntentValidationError(f"{name} must be a finite number")
    elif type(value) is not expected:
        raise IntentValidationError(f"Invalid value type for {name}")
    if expected is str:
        _utf8_text(value, name)
    if name == "seed" and value < -1:
        raise IntentValidationError("seed must be -1 (random) or a nonnegative integer")
    if isinstance(spec, Mapping):
        if name == "duration" and value == -1:
            if spec.get("supports_auto") is True:
                return value
            raise IntentValidationError("duration auto sentinel is not supported by this model")
        custom_modes = spec.get("allow_custom_modes")
        custom_size = name == "size" and isinstance(custom_modes, (list, tuple)) and mode in custom_modes
        if "options" in spec and value not in spec["options"] and not custom_size:
            raise IntentValidationError(f"{name} is outside catalog options")
        if spec.get("type") == "fixed" and value != spec.get("value"):
            raise IntentValidationError(f"{name} differs from the catalog fixed value")
        if type(value) in (int, float):
            if "min" in spec and value < spec["min"]:
                raise IntentValidationError(f"{name} is below catalog minimum")
            if "max" in spec and value > spec["max"]:
                raise IntentValidationError(f"{name} is above catalog maximum")
            if "step" in spec:
                step = Decimal(str(spec["step"]))
                offset = Decimal(str(value)) - Decimal(str(spec.get("min", 0)))
                if step <= 0 or offset % step:
                    raise IntentValidationError(f"{name} does not match catalog step")
    return value


def normalize_generation_intent(
    request: Mapping[str, Any], *, allowed_modes: Collection[str],
    allowed_parameters: Collection[str], preset: Mapping[str, Any] | None = None,
    catalog: CatalogAccessor | None = None,
) -> GenerationIntent:
    """Resolve explicit request → explicitly selected preset → known catalog defaults.

    ``mode`` is the creative capability (e.g. t2i); ``model_id`` identifies the
    exact canonical catalog entry (which may end in #image). Neither is changed
    to rescue an incompatible request. Missing boolean support flags stay absent.
    Inputs and media roles are deliberately outside this parameter-only slice.
    """
    if isinstance(allowed_modes, str) or isinstance(allowed_parameters, str):
        raise IntentValidationError("Entrance capabilities must be collections of field names")
    if any(name not in _PARAMETERS for name in allowed_parameters):
        raise IntentValidationError("Entrance declares unknown parameters")
    catalog = catalog if catalog is not None else get_catalog_accessor()
    supplied = _mapping(request, {"mode", "prompt", "model_id", "model_name", "params"}, "request")
    mode = _name(supplied.get("mode"), "mode")
    if mode not in allowed_modes:
        raise IntentValidationError(f"Entrance does not support mode: {mode}")
    if "prompt" not in supplied or not isinstance(supplied["prompt"], str):
        raise IntentValidationError("prompt must be supplied as a string")
    _utf8_text(supplied["prompt"], "prompt")
    explicit = _parameters(supplied.get("params", {}), allowed_parameters, "request.params")

    selected = {}
    preset_params = {}
    preset_model = None
    if preset is not None:
        selected = _mapping(preset, {"id", "version", "mode", "model_id", "params"}, "preset")
        _name(selected.get("id"), "preset.id")
        _name(selected.get("version"), "preset.version")
        if _name(selected.get("mode"), "preset.mode") != mode:
            raise IntentValidationError("Preset mode does not match the request")
        preset_params = _parameters(selected.get("params", {}), allowed_parameters, "preset.params")
        if "model_id" in selected:
            preset_model = _model_id(selected["model_id"], catalog)

    model_ids = [_model_id(supplied[key], catalog) for key in ("model_id", "model_name") if key in supplied]
    if model_ids:
        if len(set(model_ids)) != 1:
            raise IntentValidationError("Conflicting model aliases")
        model_id, model_source = model_ids[0], "request"
    elif preset_model is not None:
        model_id, model_source = preset_model, "preset"
    else:
        default = catalog.canonical_defaults().get(f"{mode}_model")
        if default is None:
            raise IntentValidationError(f"Catalog has no known model default for {mode}")
        model_id, model_source = _model_id(default, catalog), "catalog"
    entry = catalog.get_mode_entry(model_id)
    if entry is None:
        raise IntentValidationError(f"Unknown model: {model_id}")
    if mode not in entry.get("capabilities", []):
        raise IntentValidationError(f"Model {model_id} does not support mode {mode}")

    params = {}
    sources = {"model_id": model_source, "mode": "request", "prompt": "request"}
    for name in sorted(allowed_parameters):
        spelling = _PARAMETERS[name][0]
        spec = entry.get("duration") if name == "duration" else entry.get("params", {}).get(spelling)
        if name in explicit:
            value, source = explicit[name], "request"
        elif name in preset_params:
            value, source = preset_params[name], "preset"
        elif isinstance(spec, Mapping) and "default" in spec:
            value, source = spec["default"], "catalog"
        elif name == "duration" and isinstance(spec, Mapping) and spec.get("type") == "fixed":
            value, source = spec["value"], "catalog"
        else:
            continue
        params[name] = _validate_parameter(name, value, spec, mode=mode)
        sources[f"params.{name}"] = source

    duration_spec = entry.get("duration")
    if "duration" in params and isinstance(duration_spec, Mapping) and "options_by_resolution" in duration_spec:
        by_resolution = duration_spec["options_by_resolution"]
        if "resolution" not in params:
            raise IntentValidationError("duration requires a resolved resolution")
        if not isinstance(by_resolution, Mapping):
            raise IntentValidationError("Unknown catalog duration options_by_resolution contract")
        options = by_resolution.get(params["resolution"])
        if not isinstance(options, (list, tuple)) or params["duration"] not in options:
            raise IntentValidationError("duration is outside catalog options for resolution")

    request_fields = {"mode", "prompt", *(f"params.{name}" for name in explicit)}
    if model_ids:
        request_fields.add("model_id")
    return GenerationIntent(
        model_id=model_id, catalog_mode=entry["mode"], mode=mode, prompt=supplied["prompt"],
        params=MappingProxyType(params), sources=MappingProxyType(sources),
        request_fields=tuple(sorted(request_fields)), preset_id=selected.get("id"),
        preset_version=selected.get("version"),
    )
