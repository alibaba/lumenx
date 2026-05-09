import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml


SUPPORTED_PROVIDER_BACKENDS = ("dashscope", "vendor")
SUPPORTED_MODEL_STATUSES = ("active", "planned", "deprecated", "hidden")
SUPPORTED_SELECTION_GROUPS = ("t2i", "i2i", "image", "i2v")
VISIBLE_MODEL_SURFACES = ("project_settings", "series_settings", "video_sidebar", "global_settings")
DEFAULT_MODEL_SURFACE_REQUIREMENTS = {
    "t2i_model": ("project_settings", "series_settings", "global_settings"),
    "i2i_model": ("project_settings", "series_settings", "global_settings"),
    "image_model": ("project_settings", "series_settings", "global_settings"),
    "i2v_model": ("project_settings", "series_settings", "video_sidebar", "global_settings"),
}

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_CATALOG_ROOT = REPO_ROOT / "config" / "model_catalog"
MODEL_CATALOG_META_PATH = MODEL_CATALOG_ROOT / "catalog.meta.yaml"
MODEL_CATALOG_FAMILIES_DIR = MODEL_CATALOG_ROOT / "families"
MODEL_CATALOG_SCHEMA_PATH = MODEL_CATALOG_ROOT / "schema" / "model-catalog.schema.json"
GENERATED_MODEL_CATALOG_PATH = MODEL_CATALOG_ROOT / "generated" / "model_catalog.json"
FRONTEND_GENERATED_MODEL_CATALOG_PATH = (
    REPO_ROOT / "frontend" / "src" / "generated" / "modelCatalog.json"
)


@dataclass(frozen=True)
class DefaultModelSettings:
    t2i_model: str
    i2i_model: str
    image_model: str
    i2v_model: str


@dataclass(frozen=True)
class R2VReferenceInputConfig:
    reference_type: str
    max_refs: int


@dataclass(frozen=True)
class CatalogValidationReport:
    ok: bool
    errors: Tuple[str, ...]
    warnings: Tuple[str, ...]
    stats: Mapping[str, Any]


def _read_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML document must be a mapping: {path}")
    return data


def _require_mapping(value: Any, *, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _require_list(value: Any, *, label: str) -> List[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _require_non_empty_str(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _normalize_string_list(value: Any, *, label: str) -> List[str]:
    items = _require_list(value, label=label)
    normalized = [_require_non_empty_str(item, label=f"{label} item") for item in items]
    return normalized


def _validate_backend_names(names: Iterable[str], *, label: str) -> List[str]:
    normalized = []
    for name in names:
        backend = _require_non_empty_str(name, label=f"{label} item").lower()
        if backend not in SUPPORTED_PROVIDER_BACKENDS:
            raise ValueError(f"Unsupported backend '{backend}' in {label}")
        normalized.append(backend)
    return normalized


def _sorted_unique(values: Sequence[str]) -> List[str]:
    return sorted(dict.fromkeys(values))


def _family_source_paths(catalog_root: Path) -> List[Path]:
    if not MODEL_CATALOG_FAMILIES_DIR.exists() and catalog_root == MODEL_CATALOG_ROOT:
        family_dir = MODEL_CATALOG_FAMILIES_DIR
    else:
        family_dir = catalog_root / "families"
    return sorted(path for path in family_dir.glob("*.yaml") if path.is_file())


def _build_schema_stub() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "LumenX Model Catalog",
        "type": "object",
        "required": ["version", "defaults", "families", "models"],
        "properties": {
            "version": {"type": "integer"},
            "defaults": {"type": "object"},
            "families": {"type": "object"},
            "models": {"type": "object"},
            "model_lines": {"type": "object"},
            "modes": {"type": "object"},
            "compat": {"type": "object"},
        },
    }


def _normalize_runtime_backends(value: Any, *, label: str) -> Dict[str, Dict[str, Any]]:
    runtime = _require_mapping(value or {}, label=label)
    normalized: Dict[str, Dict[str, Any]] = {}
    for backend, payload in runtime.items():
        normalized_backend = _require_non_empty_str(backend, label=f"{label} backend").lower()
        if normalized_backend not in SUPPORTED_PROVIDER_BACKENDS:
            raise ValueError(f"Unsupported backend '{normalized_backend}' in {label}")
        normalized[normalized_backend] = dict(
            _require_mapping(payload, label=f"{label}.{normalized_backend}")
        )
    return normalized


def _merge_runtime_backends(
    base: Mapping[str, Mapping[str, Any]],
    override: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    merged = {
        backend: dict(payload)
        for backend, payload in base.items()
    }
    for backend, payload in override.items():
        merged.setdefault(backend, {})
        merged[backend].update(dict(payload))
    return merged


def _normalize_mode_name(
    *,
    explicit_mode: Any,
    selection_group: str,
    capabilities: Sequence[str],
    label: str,
) -> str:
    if explicit_mode is not None:
        return _require_non_empty_str(explicit_mode, label=label)
    if selection_group:
        return selection_group
    if capabilities:
        return capabilities[0]
    raise ValueError(f"{label} could not be inferred")


def _build_legacy_model_payload(
    *,
    model_id: str,
    family_name: str,
    provider: str,
    status: str,
    release_stage: str,
    capabilities: Sequence[str],
    supported_backends: Sequence[str],
    default_backend: str,
    backend_env_key: Any,
    credential_sources: Mapping[str, Sequence[str]],
    routing_prefixes: Sequence[str],
    transport: Mapping[str, Any],
    official_snapshot_ids: Sequence[str],
    context_hub_doc_ids: Sequence[str],
    selection_group: str,
    visible_in: Sequence[str],
    recommended: bool,
    order: int,
    badges: Sequence[str],
    display_name: str,
    description: str,
    duration: Any,
    params: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "id": model_id,
        "display_name": display_name,
        "description": description,
        "family": family_name,
        "provider": provider,
        "status": status,
        "release_stage": release_stage,
        "capabilities": list(capabilities),
        "supported_backends": list(supported_backends),
        "default_backend": default_backend,
        "backend_env_key": backend_env_key,
        "credential_sources": {
            backend: list(keys)
            for backend, keys in credential_sources.items()
        },
        "routing_prefixes": list(routing_prefixes),
        "transport": {
            "image_input_mode": dict(transport["image_input_mode"]),
            "audio_input_mode": dict(transport["audio_input_mode"]),
            "reference_video_input_mode": dict(transport["reference_video_input_mode"]),
        },
        "docs": {
            "official_snapshot_ids": list(official_snapshot_ids),
            "context_hub_doc_ids": list(context_hub_doc_ids),
        },
        "ui": {
            "selection_group": selection_group,
            "visible_in": list(visible_in),
            "recommended": recommended,
            "order": order,
            "badges": list(badges),
        },
        "duration": duration,
        "params": dict(params),
        "inputs": dict(inputs),
    }


def _build_mode_payload(
    *,
    canonical_mode_id: str,
    model_line_id: str,
    legacy_model_id: str,
    mode_name: str,
    family_name: str,
    provider: str,
    status: str,
    release_stage: str,
    capabilities: Sequence[str],
    supported_backends: Sequence[str],
    default_backend: str,
    backend_env_key: Any,
    credential_sources: Mapping[str, Sequence[str]],
    routing_prefixes: Sequence[str],
    transport: Mapping[str, Any],
    runtime: Mapping[str, Mapping[str, Any]],
    official_snapshot_ids: Sequence[str],
    context_hub_doc_ids: Sequence[str],
    selection_group: str,
    visible_in: Sequence[str],
    recommended: bool,
    order: int,
    badges: Sequence[str],
    display_name: str,
    description: str,
    duration: Any,
    params: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "id": canonical_mode_id,
        "model_line_id": model_line_id,
        "legacy_model_id": legacy_model_id,
        "mode": mode_name,
        "family": family_name,
        "provider": provider,
        "status": status,
        "release_stage": release_stage,
        "display_name": display_name,
        "description": description,
        "capabilities": list(capabilities),
        "supported_backends": list(supported_backends),
        "default_backend": default_backend,
        "backend_env_key": backend_env_key,
        "credential_sources": {
            backend: list(keys)
            for backend, keys in credential_sources.items()
        },
        "routing_prefixes": list(routing_prefixes),
        "transport": {
            "image_input_mode": dict(transport["image_input_mode"]),
            "audio_input_mode": dict(transport["audio_input_mode"]),
            "reference_video_input_mode": dict(transport["reference_video_input_mode"]),
        },
        "runtime": {
            backend: dict(payload)
            for backend, payload in runtime.items()
        },
        "docs": {
            "official_snapshot_ids": list(official_snapshot_ids),
            "context_hub_doc_ids": list(context_hub_doc_ids),
        },
        "ui": {
            "selection_group": selection_group,
            "visible_in": list(visible_in),
            "recommended": recommended,
            "order": order,
            "badges": list(badges),
        },
        "duration": duration,
        "params": dict(params),
        "inputs": dict(inputs),
    }


def _ensure_model_line_payload(
    *,
    model_lines: Dict[str, Dict[str, Any]],
    model_line_id: str,
    line_payload: Mapping[str, Any],
    canonical_mode_id: str,
    legacy_model_id: str,
) -> None:
    existing = model_lines.get(model_line_id)
    if existing is None:
        existing = {
            key: (
                {
                    backend: dict(payload)
                    for backend, payload in value.items()
                }
                if key == "runtime"
                else {
                    "official_snapshot_ids": list(value["official_snapshot_ids"]),
                    "context_hub_doc_ids": list(value["context_hub_doc_ids"]),
                }
                if key == "docs"
                else list(value)
                if key in {"supported_backends", "routing_prefixes", "supported_modalities"}
                else {
                    backend: list(keys)
                    for backend, keys in value.items()
                }
                if key == "credential_sources"
                else {
                    "image_input_mode": dict(value["image_input_mode"]),
                    "audio_input_mode": dict(value["audio_input_mode"]),
                    "reference_video_input_mode": dict(value["reference_video_input_mode"]),
                }
                if key == "transport"
                else value
            )
            for key, value in line_payload.items()
        }
        existing["modes"] = []
        existing["legacy_model_ids"] = []
        model_lines[model_line_id] = existing

    existing["modes"].append(canonical_mode_id)
    existing["legacy_model_ids"].append(legacy_model_id)


def build_catalog_dict(catalog_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(catalog_root or MODEL_CATALOG_ROOT)
    meta = _read_yaml(root / "catalog.meta.yaml")
    version = meta.get("version")
    if version != 1:
        raise ValueError("catalog.meta.yaml version must be 1")

    defaults = _require_mapping(meta.get("defaults"), label="defaults")
    default_model_settings = _require_mapping(
        defaults.get("model_settings"),
        label="defaults.model_settings",
    )
    t2i_default = _require_non_empty_str(
        default_model_settings.get("t2i_model"),
        label="defaults.model_settings.t2i_model",
    )
    i2i_default = _require_non_empty_str(
        default_model_settings.get("i2i_model"),
        label="defaults.model_settings.i2i_model",
    )
    i2v_default = _require_non_empty_str(
        default_model_settings.get("i2v_model"),
        label="defaults.model_settings.i2v_model",
    )
    image_default = _require_non_empty_str(
        default_model_settings.get("image_model"),
        label="defaults.model_settings.image_model",
    )

    families: Dict[str, Dict[str, Any]] = {}
    models: Dict[str, Dict[str, Any]] = {}
    model_lines: Dict[str, Dict[str, Any]] = {}
    modes: Dict[str, Dict[str, Any]] = {}
    legacy_model_ids: Dict[str, str] = {}

    for family_path in _family_source_paths(root):
        raw_family = _read_yaml(family_path)
        family_name = _require_non_empty_str(raw_family.get("family"), label=f"{family_path}: family")
        provider = _require_non_empty_str(raw_family.get("provider"), label=f"{family_path}: provider")
        routing_prefixes = _sorted_unique(
            _normalize_string_list(raw_family.get("routing_prefixes"), label=f"{family_path}: routing_prefixes")
        )
        supported_backends = _validate_backend_names(
            _normalize_string_list(raw_family.get("supported_backends"), label=f"{family_path}: supported_backends"),
            label=f"{family_path}: supported_backends",
        )
        default_backend = _require_non_empty_str(
            raw_family.get("default_backend"),
            label=f"{family_path}: default_backend",
        ).lower()
        if default_backend not in supported_backends:
            raise ValueError(
                f"default_backend '{default_backend}' must exist in supported_backends for {family_path}"
            )

        credential_sources_raw = _require_mapping(
            raw_family.get("credential_sources"),
            label=f"{family_path}: credential_sources",
        )
        credential_sources: Dict[str, List[str]] = {}
        for backend, keys in credential_sources_raw.items():
            normalized_backend = _require_non_empty_str(
                backend, label=f"{family_path}: credential_sources backend"
            ).lower()
            if normalized_backend not in SUPPORTED_PROVIDER_BACKENDS:
                raise ValueError(f"Unsupported backend '{normalized_backend}' in {family_path}")
            credential_sources[normalized_backend] = _normalize_string_list(
                keys,
                label=f"{family_path}: credential_sources.{normalized_backend}",
            )

        supported_modalities = _normalize_string_list(
            raw_family.get("supported_modalities"),
            label=f"{family_path}: supported_modalities",
        )

        transport = _require_mapping(raw_family.get("transport"), label=f"{family_path}: transport")
        image_input_mode = _require_mapping(
            transport.get("image_input_mode"),
            label=f"{family_path}: transport.image_input_mode",
        )
        audio_input_mode = _require_mapping(
            transport.get("audio_input_mode"),
            label=f"{family_path}: transport.audio_input_mode",
        )
        reference_video_input_mode = _require_mapping(
            transport.get("reference_video_input_mode"),
            label=f"{family_path}: transport.reference_video_input_mode",
        )
        transport_payload = {
            "image_input_mode": image_input_mode,
            "audio_input_mode": audio_input_mode,
            "reference_video_input_mode": reference_video_input_mode,
        }

        family_docs = _require_mapping(raw_family.get("docs"), label=f"{family_path}: docs")
        official_snapshot_ids = _normalize_string_list(
            family_docs.get("official_snapshot_ids", []),
            label=f"{family_path}: docs.official_snapshot_ids",
        )

        family_payload: Dict[str, Any] = {
            "family": family_name,
            "provider": provider,
            "routing_prefixes": routing_prefixes,
            "supported_backends": supported_backends,
            "default_backend": default_backend,
            "backend_env_key": raw_family.get("backend_env_key"),
            "credential_sources": credential_sources,
            "supported_modalities": supported_modalities,
            "transport": transport_payload,
            "docs": {
                "official_snapshot_ids": official_snapshot_ids,
            },
            "models": [],
        }

        if family_name in families:
            raise ValueError(f"Duplicate family name '{family_name}'")
        families[family_name] = family_payload

        raw_models = _require_list(raw_family.get("models"), label=f"{family_path}: models")
        for raw_model in raw_models:
            model_mapping = _require_mapping(raw_model, label=f"{family_path}: model")
            if "modes" in model_mapping:
                model_line_id = _require_non_empty_str(
                    model_mapping.get("id"),
                    label=f"{family_path}: model_line.id",
                )
                line_display_name = _require_non_empty_str(
                    model_mapping.get("display_name"),
                    label=f"{family_path}: {model_line_id}.display_name",
                )
                line_description = _require_non_empty_str(
                    model_mapping.get("description"),
                    label=f"{family_path}: {model_line_id}.description",
                )
                line_status = _require_non_empty_str(
                    model_mapping.get("status"),
                    label=f"{family_path}: {model_line_id}.status",
                ).lower()
                if line_status not in SUPPORTED_MODEL_STATUSES:
                    raise ValueError(f"Unsupported model status '{line_status}' for {model_line_id}")
                line_release_stage = _require_non_empty_str(
                    model_mapping.get("release_stage"),
                    label=f"{family_path}: {model_line_id}.release_stage",
                )
                line_docs = _require_mapping(
                    model_mapping.get("docs"),
                    label=f"{family_path}: {model_line_id}.docs",
                )
                line_context_hub_doc_ids = _normalize_string_list(
                    line_docs.get("context_hub_doc_ids", []),
                    label=f"{family_path}: {model_line_id}.docs.context_hub_doc_ids",
                )
                line_runtime = _normalize_runtime_backends(
                    model_mapping.get("runtime", {}),
                    label=f"{family_path}: {model_line_id}.runtime",
                )
                raw_modes = _require_mapping(
                    model_mapping.get("modes"),
                    label=f"{family_path}: {model_line_id}.modes",
                )
                line_payload = {
                    "id": model_line_id,
                    "family": family_name,
                    "provider": provider,
                    "display_name": line_display_name,
                    "description": line_description,
                    "status": line_status,
                    "release_stage": line_release_stage,
                    "supported_backends": list(supported_backends),
                    "default_backend": default_backend,
                    "backend_env_key": raw_family.get("backend_env_key"),
                    "credential_sources": credential_sources,
                    "routing_prefixes": list(routing_prefixes),
                    "supported_modalities": list(supported_modalities),
                    "transport": transport_payload,
                    "runtime": line_runtime,
                    "docs": {
                        "official_snapshot_ids": official_snapshot_ids,
                        "context_hub_doc_ids": line_context_hub_doc_ids,
                    },
                }

                for mode_name, raw_mode in raw_modes.items():
                    mode_mapping = _require_mapping(
                        raw_mode,
                        label=f"{family_path}: {model_line_id}.modes.{mode_name}",
                    )
                    legacy_model_id = _require_non_empty_str(
                        mode_mapping.get("legacy_id"),
                        label=f"{family_path}: {model_line_id}.modes.{mode_name}.legacy_id",
                    )
                    if legacy_model_id in models:
                        raise ValueError(f"Duplicate model id '{legacy_model_id}'")

                    mode_status = _require_non_empty_str(
                        mode_mapping.get("status", line_status),
                        label=f"{family_path}: {legacy_model_id}.status",
                    ).lower()
                    if mode_status not in SUPPORTED_MODEL_STATUSES:
                        raise ValueError(f"Unsupported model status '{mode_status}' for {legacy_model_id}")

                    mode_release_stage = _require_non_empty_str(
                        mode_mapping.get("release_stage", line_release_stage),
                        label=f"{family_path}: {legacy_model_id}.release_stage",
                    )
                    model_docs = _require_mapping(
                        mode_mapping.get("docs", line_docs),
                        label=f"{family_path}: {legacy_model_id}.docs",
                    )
                    context_hub_doc_ids = _normalize_string_list(
                        model_docs.get("context_hub_doc_ids", line_context_hub_doc_ids),
                        label=f"{family_path}: {legacy_model_id}.docs.context_hub_doc_ids",
                    )

                    model_ui = _require_mapping(mode_mapping.get("ui"), label=f"{family_path}: {legacy_model_id}.ui")
                    selection_group = _require_non_empty_str(
                        model_ui.get("selection_group"),
                        label=f"{family_path}: {legacy_model_id}.ui.selection_group",
                    )
                    if selection_group not in SUPPORTED_SELECTION_GROUPS:
                        raise ValueError(
                            f"Unsupported selection_group '{selection_group}' for {legacy_model_id}"
                        )
                    visible_in = _normalize_string_list(
                        model_ui.get("visible_in", []),
                        label=f"{family_path}: {legacy_model_id}.ui.visible_in",
                    )
                    if visible_in and not context_hub_doc_ids:
                        raise ValueError(
                            f"Visible model '{legacy_model_id}' must define docs.context_hub_doc_ids"
                        )

                    capabilities = _normalize_string_list(
                        mode_mapping.get("capabilities", [mode_name]),
                        label=f"{family_path}: {legacy_model_id}.capabilities",
                    )
                    canonical_mode_id = f"{model_line_id}#{_require_non_empty_str(mode_name, label=f'{family_path}: mode name')}"
                    if canonical_mode_id in modes:
                        raise ValueError(f"Duplicate canonical mode id '{canonical_mode_id}'")

                    runtime = _merge_runtime_backends(
                        line_runtime,
                        _normalize_runtime_backends(
                            mode_mapping.get("runtime", {}),
                            label=f"{family_path}: {legacy_model_id}.runtime",
                        ),
                    )

                    legacy_model_payload = _build_legacy_model_payload(
                        model_id=legacy_model_id,
                        family_name=family_name,
                        provider=provider,
                        status=mode_status,
                        release_stage=mode_release_stage,
                        capabilities=capabilities,
                        supported_backends=supported_backends,
                        default_backend=default_backend,
                        backend_env_key=raw_family.get("backend_env_key"),
                        credential_sources=credential_sources,
                        routing_prefixes=routing_prefixes,
                        transport=transport_payload,
                        official_snapshot_ids=official_snapshot_ids,
                        context_hub_doc_ids=context_hub_doc_ids,
                        selection_group=selection_group,
                        visible_in=visible_in,
                        recommended=bool(model_ui.get("recommended", False)),
                        order=int(model_ui.get("order", 0)),
                        badges=_normalize_string_list(
                            model_ui.get("badges", []),
                            label=f"{family_path}: {legacy_model_id}.ui.badges",
                        ),
                        display_name=_require_non_empty_str(
                            mode_mapping.get("display_name"),
                            label=f"{family_path}: {legacy_model_id}.display_name",
                        ),
                        description=_require_non_empty_str(
                            mode_mapping.get("description"),
                            label=f"{family_path}: {legacy_model_id}.description",
                        ),
                        duration=mode_mapping.get("duration"),
                        params=_require_mapping(
                            mode_mapping.get("params", {}),
                            label=f"{family_path}: {legacy_model_id}.params",
                        ),
                        inputs=_require_mapping(
                            mode_mapping.get("inputs", {}),
                            label=f"{family_path}: {legacy_model_id}.inputs",
                        ),
                    )

                    mode_payload = _build_mode_payload(
                        canonical_mode_id=canonical_mode_id,
                        model_line_id=model_line_id,
                        legacy_model_id=legacy_model_id,
                        mode_name=_require_non_empty_str(mode_name, label=f"{family_path}: mode name"),
                        family_name=family_name,
                        provider=provider,
                        status=mode_status,
                        release_stage=mode_release_stage,
                        capabilities=capabilities,
                        supported_backends=supported_backends,
                        default_backend=default_backend,
                        backend_env_key=raw_family.get("backend_env_key"),
                        credential_sources=credential_sources,
                        routing_prefixes=routing_prefixes,
                        transport=transport_payload,
                        runtime=runtime,
                        official_snapshot_ids=official_snapshot_ids,
                        context_hub_doc_ids=context_hub_doc_ids,
                        selection_group=selection_group,
                        visible_in=visible_in,
                        recommended=bool(model_ui.get("recommended", False)),
                        order=int(model_ui.get("order", 0)),
                        badges=_normalize_string_list(
                            model_ui.get("badges", []),
                            label=f"{family_path}: {legacy_model_id}.ui.badges",
                        ),
                        display_name=legacy_model_payload["display_name"],
                        description=legacy_model_payload["description"],
                        duration=legacy_model_payload["duration"],
                        params=legacy_model_payload["params"],
                        inputs=legacy_model_payload["inputs"],
                    )

                    models[legacy_model_id] = legacy_model_payload
                    modes[canonical_mode_id] = mode_payload
                    legacy_model_ids[legacy_model_id] = canonical_mode_id
                    family_payload["models"].append(legacy_model_id)
                    _ensure_model_line_payload(
                        model_lines=model_lines,
                        model_line_id=model_line_id,
                        line_payload=line_payload,
                        canonical_mode_id=canonical_mode_id,
                        legacy_model_id=legacy_model_id,
                    )
                continue

            model_id = _require_non_empty_str(model_mapping.get("id"), label=f"{family_path}: model.id")
            if model_id in models:
                raise ValueError(f"Duplicate model id '{model_id}'")

            status = _require_non_empty_str(
                model_mapping.get("status"),
                label=f"{family_path}: {model_id}.status",
            ).lower()
            if status not in SUPPORTED_MODEL_STATUSES:
                raise ValueError(f"Unsupported model status '{status}' for {model_id}")

            model_docs = _require_mapping(
                model_mapping.get("docs"),
                label=f"{family_path}: {model_id}.docs",
            )
            context_hub_doc_ids = _normalize_string_list(
                model_docs.get("context_hub_doc_ids", []),
                label=f"{family_path}: {model_id}.docs.context_hub_doc_ids",
            )

            model_ui = _require_mapping(model_mapping.get("ui"), label=f"{family_path}: {model_id}.ui")
            selection_group = _require_non_empty_str(
                model_ui.get("selection_group"),
                label=f"{family_path}: {model_id}.ui.selection_group",
            )
            if selection_group not in SUPPORTED_SELECTION_GROUPS:
                raise ValueError(
                    f"Unsupported selection_group '{selection_group}' for {model_id}"
                )
            visible_in = _normalize_string_list(
                model_ui.get("visible_in", []),
                label=f"{family_path}: {model_id}.ui.visible_in",
            )
            if visible_in and not context_hub_doc_ids:
                raise ValueError(f"Visible model '{model_id}' must define docs.context_hub_doc_ids")

            capabilities = _normalize_string_list(
                model_mapping.get("capabilities"),
                label=f"{family_path}: {model_id}.capabilities",
            )
            runtime = _normalize_runtime_backends(
                model_mapping.get("runtime", {}),
                label=f"{family_path}: {model_id}.runtime",
            )
            model_line_id = _require_non_empty_str(
                model_mapping.get("model_line_id", f"{family_name}/{model_id}"),
                label=f"{family_path}: {model_id}.model_line_id",
            )
            mode_name = _normalize_mode_name(
                explicit_mode=model_mapping.get("mode"),
                selection_group=selection_group,
                capabilities=capabilities,
                label=f"{family_path}: {model_id}.mode",
            )
            canonical_mode_id = f"{model_line_id}#{mode_name}"
            if canonical_mode_id in modes:
                raise ValueError(f"Duplicate canonical mode id '{canonical_mode_id}'")

            legacy_model_payload = _build_legacy_model_payload(
                model_id=model_id,
                family_name=family_name,
                provider=provider,
                status=status,
                release_stage=_require_non_empty_str(
                    model_mapping.get("release_stage"),
                    label=f"{family_path}: {model_id}.release_stage",
                ),
                capabilities=capabilities,
                supported_backends=supported_backends,
                default_backend=default_backend,
                backend_env_key=raw_family.get("backend_env_key"),
                credential_sources=credential_sources,
                routing_prefixes=routing_prefixes,
                transport=transport_payload,
                official_snapshot_ids=official_snapshot_ids,
                context_hub_doc_ids=context_hub_doc_ids,
                selection_group=selection_group,
                visible_in=visible_in,
                recommended=bool(model_ui.get("recommended", False)),
                order=int(model_ui.get("order", 0)),
                badges=_normalize_string_list(
                    model_ui.get("badges", []),
                    label=f"{family_path}: {model_id}.ui.badges",
                ),
                display_name=_require_non_empty_str(
                    model_mapping.get("display_name"),
                    label=f"{family_path}: {model_id}.display_name",
                ),
                description=_require_non_empty_str(
                    model_mapping.get("description"),
                    label=f"{family_path}: {model_id}.description",
                ),
                duration=model_mapping.get("duration"),
                params=_require_mapping(
                    model_mapping.get("params", {}),
                    label=f"{family_path}: {model_id}.params",
                ),
                inputs=_require_mapping(
                    model_mapping.get("inputs", {}),
                    label=f"{family_path}: {model_id}.inputs",
                ),
            )

            line_payload = {
                "id": model_line_id,
                "family": family_name,
                "provider": provider,
                "display_name": legacy_model_payload["display_name"],
                "description": legacy_model_payload["description"],
                "status": status,
                "release_stage": legacy_model_payload["release_stage"],
                "supported_backends": list(supported_backends),
                "default_backend": default_backend,
                "backend_env_key": raw_family.get("backend_env_key"),
                "credential_sources": credential_sources,
                "routing_prefixes": list(routing_prefixes),
                "supported_modalities": list(supported_modalities),
                "transport": transport_payload,
                "runtime": runtime,
                "docs": {
                    "official_snapshot_ids": official_snapshot_ids,
                    "context_hub_doc_ids": context_hub_doc_ids,
                },
            }

            mode_payload = _build_mode_payload(
                canonical_mode_id=canonical_mode_id,
                model_line_id=model_line_id,
                legacy_model_id=model_id,
                mode_name=mode_name,
                family_name=family_name,
                provider=provider,
                status=status,
                release_stage=legacy_model_payload["release_stage"],
                capabilities=capabilities,
                supported_backends=supported_backends,
                default_backend=default_backend,
                backend_env_key=raw_family.get("backend_env_key"),
                credential_sources=credential_sources,
                routing_prefixes=routing_prefixes,
                transport=transport_payload,
                runtime=runtime,
                official_snapshot_ids=official_snapshot_ids,
                context_hub_doc_ids=context_hub_doc_ids,
                selection_group=selection_group,
                visible_in=visible_in,
                recommended=bool(model_ui.get("recommended", False)),
                order=int(model_ui.get("order", 0)),
                badges=_normalize_string_list(
                    model_ui.get("badges", []),
                    label=f"{family_path}: {model_id}.ui.badges",
                ),
                display_name=legacy_model_payload["display_name"],
                description=legacy_model_payload["description"],
                duration=legacy_model_payload["duration"],
                params=legacy_model_payload["params"],
                inputs=legacy_model_payload["inputs"],
            )

            models[model_id] = legacy_model_payload
            modes[canonical_mode_id] = mode_payload
            legacy_model_ids[model_id] = canonical_mode_id
            family_payload["models"].append(model_id)
            _ensure_model_line_payload(
                model_lines=model_lines,
                model_line_id=model_line_id,
                line_payload=line_payload,
                canonical_mode_id=canonical_mode_id,
                legacy_model_id=model_id,
            )

    for model_id in (t2i_default, i2i_default, image_default, i2v_default):
        if model_id not in models:
            raise ValueError(f"Default model '{model_id}' is missing from the catalog")

    for family in families.values():
        family["models"] = sorted(family["models"])
    for line_payload in model_lines.values():
        line_payload["modes"] = sorted(line_payload["modes"])
        line_payload["legacy_model_ids"] = sorted(line_payload["legacy_model_ids"])

    canonical_defaults = {
        "t2i_model": legacy_model_ids[t2i_default],
        "i2i_model": legacy_model_ids[i2i_default],
        "image_model": legacy_model_ids[image_default],
        "i2v_model": legacy_model_ids[i2v_default],
    }

    return {
        "version": version,
        "defaults": {
            "model_settings": {
                "t2i_model": t2i_default,
                "i2i_model": i2i_default,
                "image_model": image_default,
                "i2v_model": i2v_default,
            },
            "canonical_model_settings": canonical_defaults,
        },
        "families": {key: families[key] for key in sorted(families)},
        "model_lines": {key: model_lines[key] for key in sorted(model_lines)},
        "modes": {key: modes[key] for key in sorted(modes)},
        "compat": {
            "legacy_model_ids": {key: legacy_model_ids[key] for key in sorted(legacy_model_ids)},
        },
        "models": {key: models[key] for key in sorted(models)},
    }


def write_generated_catalog(
    output_path: Path = GENERATED_MODEL_CATALOG_PATH,
    *,
    catalog_root: Optional[Path] = None,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    catalog = build_catalog_dict(catalog_root or MODEL_CATALOG_ROOT)
    output.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def write_frontend_generated_catalog(
    output_path: Path = FRONTEND_GENERATED_MODEL_CATALOG_PATH,
    *,
    catalog_root: Optional[Path] = None,
) -> Path:
    return write_generated_catalog(output_path, catalog_root=catalog_root)


def write_catalog_schema(output_path: Path = MODEL_CATALOG_SCHEMA_PATH) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(_build_schema_stub(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def load_generated_model_catalog(path: Path = GENERATED_MODEL_CATALOG_PATH) -> Dict[str, Any]:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return build_catalog_dict(MODEL_CATALOG_ROOT)


def load_frontend_generated_model_catalog(
    path: Path = FRONTEND_GENERATED_MODEL_CATALOG_PATH,
) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Frontend generated catalog is missing: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Phase 2: Canonical mode helper API
# ---------------------------------------------------------------------------

class CatalogAccessor:
    """Stable helper API for canonical mode lookups on a loaded catalog."""

    def __init__(self, catalog: Mapping[str, Any]) -> None:
        self._catalog = catalog
        self._models: Mapping[str, Any] = catalog.get("models", {})
        self._model_lines: Mapping[str, Any] = catalog.get("model_lines", {})
        self._modes: Mapping[str, Any] = catalog.get("modes", {})
        self._legacy_to_canonical: Mapping[str, str] = (
            catalog.get("compat", {}).get("legacy_model_ids", {})
        )
        self._canonical_to_legacy: Dict[str, str] = {
            canonical: legacy
            for legacy, canonical in self._legacy_to_canonical.items()
        }

    # --- ID resolution helpers ---

    def resolve_legacy_to_canonical(self, flat_id: str) -> Optional[str]:
        """Resolve a legacy flat model ID to its canonical mode ID."""
        return self._legacy_to_canonical.get(flat_id)

    def resolve_canonical_to_legacy(self, canonical_mode_id: str) -> Optional[str]:
        """Resolve a canonical mode ID back to the legacy flat ID."""
        return self._canonical_to_legacy.get(canonical_mode_id)

    def resolve_to_flat(self, model_id: str) -> str:
        """Accept either a flat ID or canonical mode ID, always return a flat ID."""
        legacy = self.resolve_canonical_to_legacy(model_id)
        if legacy is not None:
            return legacy
        if model_id in self._models:
            return model_id
        return model_id

    # --- Metadata access helpers ---

    def get_mode_entry(self, canonical_mode_id: str) -> Optional[Dict[str, Any]]:
        """Return the full canonical mode entry."""
        entry = self._modes.get(canonical_mode_id)
        return dict(entry) if entry else None

    def get_mode_runtime(self, canonical_mode_id: str) -> Optional[Dict[str, Any]]:
        """Return the runtime backend metadata for a canonical mode."""
        entry = self._modes.get(canonical_mode_id)
        if entry is None:
            return None
        return dict(entry.get("runtime", {}))

    def get_mode_product(self, canonical_mode_id: str) -> Optional[Dict[str, Any]]:
        """Return the product/UI metadata for a canonical mode."""
        entry = self._modes.get(canonical_mode_id)
        if entry is None:
            return None
        return dict(entry.get("ui", {}))

    def get_model_line(self, model_line_id: str) -> Optional[Dict[str, Any]]:
        """Return the model line entry."""
        entry = self._model_lines.get(model_line_id)
        return dict(entry) if entry else None

    def get_gateway(
        self, canonical_mode_id: str, backend: str = "dashscope"
    ) -> Optional[str]:
        """Return the gateway value for a canonical mode on a specific backend."""
        runtime = self.get_mode_runtime(canonical_mode_id)
        if runtime is None:
            return None
        backend_meta = runtime.get(backend)
        if backend_meta is None:
            return None
        return backend_meta.get("gateway")

    # --- Enumeration helpers ---

    def all_canonical_mode_ids(self) -> List[str]:
        """Return all canonical mode IDs in sorted order."""
        return sorted(self._modes.keys())

    def all_legacy_model_ids(self) -> List[str]:
        """Return all legacy flat model IDs in sorted order."""
        return sorted(self._models.keys())

    def all_model_line_ids(self) -> List[str]:
        """Return all model line IDs in sorted order."""
        return sorted(self._model_lines.keys())

    def canonical_defaults(self) -> Dict[str, str]:
        """Return the canonical default model settings."""
        return dict(
            self._catalog.get("defaults", {}).get("canonical_model_settings", {})
        )


def get_catalog_accessor(
    catalog: Optional[Mapping[str, Any]] = None,
) -> CatalogAccessor:
    """Build a CatalogAccessor from a loaded or generated catalog."""
    active_catalog = catalog or load_generated_model_catalog()
    return CatalogAccessor(active_catalog)


def resolve_r2v_route_model_id(
    selected_model_id: str,
    catalog: Optional[Mapping[str, Any]] = None,
) -> str:
    """Resolve the concrete hidden R2V model for the selected video model."""
    active_catalog = catalog or load_generated_model_catalog()
    accessor = get_catalog_accessor(active_catalog)
    flat_model_id = accessor.resolve_to_flat(selected_model_id)
    canonical_mode_id = (
        accessor.resolve_legacy_to_canonical(flat_model_id)
        or (selected_model_id if selected_model_id in active_catalog.get("modes", {}) else None)
    )

    if canonical_mode_id:
        mode_entry = accessor.get_mode_entry(canonical_mode_id)
        model_line_id = mode_entry.get("model_line_id") if mode_entry else None
        model_line = accessor.get_model_line(model_line_id) if model_line_id else None
        candidate_mode_ids = model_line.get("modes", []) if model_line else []
        for candidate_mode_id in candidate_mode_ids:
            candidate_mode = accessor.get_mode_entry(candidate_mode_id)
            if candidate_mode and candidate_mode.get("mode") == "r2v":
                route_model_id = accessor.resolve_canonical_to_legacy(candidate_mode_id)
                if route_model_id:
                    return route_model_id

    fallback_routes = (
        ("happyhorse-1.0-", "happyhorse-1.0-r2v"),
        ("wan2.7-", "wan2.7-r2v"),
        ("wan2.6-", "wan2.6-r2v"),
        ("kling", "kling-v3-r2v"),
        ("pixverse", "pixverse-c1-r2v"),
        ("vidu", "viduq3-pro-r2v"),
    )
    for prefix, route_model_id in fallback_routes:
        if flat_model_id.startswith(prefix) and route_model_id in active_catalog.get("models", {}):
            return route_model_id

    if "wan2.7-r2v" in active_catalog.get("models", {}):
        return "wan2.7-r2v"
    return flat_model_id


def get_r2v_reference_input_config(
    model_id: str,
    catalog: Optional[Mapping[str, Any]] = None,
) -> R2VReferenceInputConfig:
    """Return the concrete reference input contract for an R2V model."""
    active_catalog = catalog or load_generated_model_catalog()
    accessor = get_catalog_accessor(active_catalog)
    flat_model_id = accessor.resolve_to_flat(model_id)
    model = active_catalog.get("models", {}).get(flat_model_id, {})
    reference_images = model.get("inputs", {}).get("reference_images", {})
    reference_type = reference_images.get("reference_type") or "image"
    if reference_type not in {"image", "video"}:
        reference_type = "image"

    default_max = 3 if reference_type == "video" else 9
    max_refs = reference_images.get("max", default_max)
    if not isinstance(max_refs, int) or max_refs <= 0:
        max_refs = default_max

    return R2VReferenceInputConfig(reference_type=reference_type, max_refs=max_refs)


def validate_r2v_reference_inputs(
    *,
    model_id: str,
    reference_video_urls: Sequence[str],
    reference_image_urls: Sequence[str],
    catalog: Optional[Mapping[str, Any]] = None,
) -> R2VReferenceInputConfig:
    """Validate R2V references against the concrete model contract."""
    config = get_r2v_reference_input_config(model_id, catalog)
    required_urls = (
        list(reference_video_urls)
        if config.reference_type == "video"
        else list(reference_image_urls)
    )

    if not required_urls:
        required_label = "reference_video_urls" if config.reference_type == "video" else "reference_image_urls"
        raise ValueError(f"Model '{model_id}' requires non-empty {required_label} for R2V")

    if len(required_urls) > config.max_refs:
        required_label = "reference_video_urls" if config.reference_type == "video" else "reference_image_urls"
        raise ValueError(
            f"Model '{model_id}' accepts at most {config.max_refs} {required_label}; got {len(required_urls)}"
        )

    return config


def get_default_model_settings(catalog_root: Optional[Path] = None) -> DefaultModelSettings:
    catalog = build_catalog_dict(catalog_root or MODEL_CATALOG_ROOT)
    defaults = catalog["defaults"]["model_settings"]
    return DefaultModelSettings(
        t2i_model=defaults["t2i_model"],
        i2i_model=defaults["i2i_model"],
        image_model=defaults["image_model"],
        i2v_model=defaults["i2v_model"],
    )


def build_provider_family_configs(
    catalog: Optional[Mapping[str, Any]] = None,
) -> Tuple["ProviderFamilyConfig", ...]:
    from .provider_registry import ProviderFamilyConfig

    active_catalog = catalog or load_generated_model_catalog()
    families: List[ProviderFamilyConfig] = []

    for family in active_catalog["families"].values():
        for prefix in family["routing_prefixes"]:
            families.append(
                ProviderFamilyConfig(
                    model_family=prefix,
                    backend_default=family["default_backend"],
                    backend_env_key=family.get("backend_env_key"),
                    credential_sources={
                        backend: tuple(keys)
                        for backend, keys in family["credential_sources"].items()
                    },
                    supported_modalities=tuple(family["supported_modalities"]),
                    image_input_mode=dict(family["transport"]["image_input_mode"]),
                    audio_input_mode=dict(family["transport"]["audio_input_mode"]),
                    reference_video_input_mode=dict(
                        family["transport"]["reference_video_input_mode"]
                    ),
                )
            )

    return tuple(families)


def _is_model_visible_on_surface(model: Mapping[str, Any], surface: str) -> bool:
    return (
        model.get("status") not in {"planned", "hidden"}
        and surface in model.get("ui", {}).get("visible_in", [])
    )


def build_catalog_validation_report(
    catalog: Optional[Mapping[str, Any]] = None,
    frontend_catalog: Optional[Mapping[str, Any]] = None,
) -> CatalogValidationReport:
    active_catalog = dict(catalog or load_generated_model_catalog())
    active_frontend_catalog = dict(frontend_catalog or load_frontend_generated_model_catalog())
    errors: List[str] = []
    warnings: List[str] = []

    if active_catalog != active_frontend_catalog:
        errors.append(
            "Frontend generated catalog does not match config/model_catalog/generated/model_catalog.json."
        )

    models = active_catalog.get("models", {})
    model_lines = active_catalog.get("model_lines", {})
    canonical_modes = active_catalog.get("modes", {})
    compat = active_catalog.get("compat", {})
    legacy_aliases = compat.get("legacy_model_ids", {})
    defaults = active_catalog.get("defaults", {}).get("model_settings", {})
    canonical_defaults = active_catalog.get("defaults", {}).get("canonical_model_settings", {})

    if not model_lines:
        errors.append("Generated catalog is missing additive model_lines data.")
    if not canonical_modes:
        errors.append("Generated catalog is missing additive modes data.")
    if not legacy_aliases:
        errors.append("Generated catalog is missing compat.legacy_model_ids mappings.")

    for legacy_model_id in sorted(models):
        canonical_mode_id = legacy_aliases.get(legacy_model_id)
        if not canonical_mode_id:
            errors.append(f"Legacy model '{legacy_model_id}' is missing compat.legacy_model_ids mapping.")
            continue
        if canonical_mode_id not in canonical_modes:
            errors.append(
                f"Legacy model '{legacy_model_id}' maps to missing canonical mode '{canonical_mode_id}'."
            )

    for default_key, canonical_mode_id in canonical_defaults.items():
        if canonical_mode_id not in canonical_modes:
            errors.append(
                f"Canonical default '{default_key}' points to missing mode '{canonical_mode_id}'."
            )

    visible_models = {
        model_id: model
        for model_id, model in models.items()
        if model.get("status") not in {"planned", "hidden"}
    }

    for default_key, surfaces in DEFAULT_MODEL_SURFACE_REQUIREMENTS.items():
        model_id = defaults.get(default_key)
        if not model_id:
            errors.append(f"Default '{default_key}' is missing from defaults.model_settings.")
            continue

        model = models.get(model_id)
        if not model:
            errors.append(f"Default '{default_key}' points to missing model '{model_id}'.")
            continue

        for surface in surfaces:
            if not _is_model_visible_on_surface(model, surface):
                errors.append(
                    f"Default '{default_key}' model '{model_id}' is not visible on '{surface}'."
                )

    for model_id, model in models.items():
        visible_in = tuple(model.get("ui", {}).get("visible_in", []))
        docs = model.get("docs", {})
        if visible_in and not docs.get("context_hub_doc_ids"):
            errors.append(f"Visible model '{model_id}' is missing docs.context_hub_doc_ids.")
        if visible_in and not docs.get("official_snapshot_ids"):
            errors.append(f"Visible model '{model_id}' is missing docs.official_snapshot_ids.")

    surface_summary: Dict[str, Dict[str, List[str]]] = {}
    for surface in VISIBLE_MODEL_SURFACES:
        group_summary: Dict[str, List[str]] = {}
        for selection_group in SUPPORTED_SELECTION_GROUPS:
            group_summary[selection_group] = sorted(
                model_id
                for model_id, model in visible_models.items()
                if model.get("ui", {}).get("selection_group") == selection_group
                and surface in model.get("ui", {}).get("visible_in", [])
            )
        surface_summary[surface] = group_summary

    planned_models = sorted(
        model_id for model_id, model in models.items() if model.get("status") == "planned"
    )
    hidden_models = sorted(
        model_id for model_id, model in models.items() if model.get("status") == "hidden"
    )

    if planned_models:
        warnings.append(
            "Planned models are present in the catalog and require runtime exposure work before activation: "
            + ", ".join(planned_models)
        )

    stats: Dict[str, Any] = {
        "families": len(active_catalog.get("families", {})),
        "models": len(models),
        "model_lines": len(model_lines),
        "canonical_modes": len(canonical_modes),
        "legacy_aliases": len(legacy_aliases),
        "visible_models": len(visible_models),
        "planned_models": planned_models,
        "hidden_models": hidden_models,
        "defaults": defaults,
        "canonical_defaults": canonical_defaults,
        "surface_summary": surface_summary,
    }

    return CatalogValidationReport(
        ok=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        stats=stats,
    )
