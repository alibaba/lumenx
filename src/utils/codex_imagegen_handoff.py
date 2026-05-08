from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.apps.comic_gen.models import (
    CodexImagegenPolicy,
    CodexImagegenRecommendationPolicy,
)

SAFE_REFS_ONLY_MODE = "safe_refs_only"
TWO_STAGE_HIGH_CONSISTENCY_MODE = "two_stage_high_consistency"
VALID_PACK_MODES = {SAFE_REFS_ONLY_MODE, TWO_STAGE_HIGH_CONSISTENCY_MODE}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _lower_key(value: Any) -> str:
    return _clean_text(value).lower()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else []


def _normalize_pack_mode(value: Any) -> str:
    normalized = _lower_key(value)
    if normalized in {"two_stage", "high_consistency"}:
        return TWO_STAGE_HIGH_CONSISTENCY_MODE
    if normalized in VALID_PACK_MODES:
        return normalized
    return SAFE_REFS_ONLY_MODE


def _selected_variant_url(asset: Any) -> Optional[str]:
    asset_map = _mapping(asset)
    variants = list(_sequence(asset_map.get("variants")))
    selected_id = _clean_text(asset_map.get("selected_id"))

    if selected_id:
        for variant in variants:
            variant_map = _mapping(variant)
            if _clean_text(variant_map.get("id")) == selected_id:
                url = _clean_text(variant_map.get("url"))
                if url:
                    return url

    for variant in variants:
        url = _clean_text(_mapping(variant).get("url"))
        if url:
            return url
    return None


def _selected_frame_reference(frame: Any) -> Optional[str]:
    frame_map = _mapping(frame)
    rendered_asset = _mapping(frame_map.get("rendered_image_asset"))
    url = _selected_variant_url(rendered_asset)
    if url:
        return url
    return _clean_text(frame_map.get("rendered_image_url")) or _clean_text(frame_map.get("image_url")) or None


def _previous_same_scene_frame(script: Any, frame: Any) -> Optional[Mapping[str, Any]]:
    script_map = _mapping(script)
    frame_map = _mapping(frame)
    frame_id = _clean_text(frame_map.get("id"))
    if not frame_id:
        return None

    frames = list(_sequence(script_map.get("frames")))
    index = next((i for i, item in enumerate(frames) if _clean_text(_mapping(item).get("id")) == frame_id), -1)
    if index <= 0:
        return None

    previous_frame = _mapping(frames[index - 1])
    if _clean_text(previous_frame.get("scene_id")) != _clean_text(frame_map.get("scene_id")):
        return None
    return previous_frame


def _style_reference_urls(style_config: Any) -> List[str]:
    style_map = _mapping(style_config)
    reference_images = style_map.get("reference_images")
    if not isinstance(reference_images, list):
        return []
    urls = [_clean_text(value) for value in reference_images if _clean_text(value)]
    return list(dict.fromkeys(urls))


def build_codex_reference_preview(
    script: Any,
    frame: Any,
    *,
    continuity_lock: Optional[bool] = None,
    include_style_references: bool = True,
) -> List[Dict[str, Any]]:
    script_map = _mapping(script)
    frame_map = _mapping(frame)
    composition = _mapping(frame_map.get("composition_data"))
    binding_version = composition.get("reference_binding_version")
    bindings = composition if binding_version else {}

    if continuity_lock is None:
        continuity_lock = bool(composition.get("continuity_lock", True))

    preview: List[Dict[str, Any]] = []
    previous_same_scene_frame = _previous_same_scene_frame(script_map, frame_map)
    continuity_url = _selected_frame_reference(previous_same_scene_frame) if continuity_lock else None
    if continuity_lock:
        continuity_bindings = _mapping(bindings.get("continuity"))
        preview.append(
            {
                "id": _clean_text(previous_same_scene_frame.get("id") if previous_same_scene_frame else "previous-frame")
                or "previous-frame",
                "name": (
                    f"上一帧 #{_clean_text(previous_same_scene_frame.get('id'))[:8]}"
                    if previous_same_scene_frame
                    else "上一帧连续参考"
                ),
                "type": "continuity",
                "url": continuity_url,
                "required": bool(continuity_bindings.get("prefer_previous_frame")),
                "locked": True,
                "status": "ready" if continuity_url else "missing",
                "source": "同场景连续镜头",
            }
        )

    scenes_by_id = {
        _clean_text(scene.get("id")): _mapping(scene)
        for scene in _sequence(script_map.get("scenes"))
        if _clean_text(_mapping(scene).get("id"))
    }
    scene_id = _clean_text(frame_map.get("scene_id"))
    if scene_id:
        scene = scenes_by_id.get(scene_id, {})
        scene_bindings = _mapping(bindings.get("scene"))
        preview.append(
            {
                "id": _clean_text(scene.get("id")) or scene_id,
                "name": _clean_text(scene.get("name")) or "未知场景",
                "type": "scene",
                "url": _selected_variant_url(scene.get("image_asset")) or _clean_text(scene.get("image_url")) or None,
                "required": scene_bindings.get("required", True) is not False,
                "locked": bool(scene.get("locked") or scene_bindings.get("lock")),
                "status": "ready"
                if (_selected_variant_url(scene.get("image_asset")) or _clean_text(scene.get("image_url")))
                else "missing",
                "source": "场景主参考",
            }
        )

    characters_by_id = {
        _clean_text(character.get("id")): _mapping(character)
        for character in _sequence(script_map.get("characters"))
        if _clean_text(_mapping(character).get("id"))
    }
    for character_id in _sequence(frame_map.get("character_ids")):
        character = characters_by_id.get(_clean_text(character_id), {})
        url = (
            _selected_variant_url(character.get("three_view_asset"))
            or _selected_variant_url(character.get("full_body_asset"))
            or _selected_variant_url(character.get("headshot_asset"))
            or _clean_text(character.get("three_view_image_url"))
            or _clean_text(character.get("full_body_image_url"))
            or _clean_text(character.get("headshot_image_url"))
            or _clean_text(character.get("avatar_url"))
            or _clean_text(character.get("image_url"))
            or None
        )
        preview.append(
            {
                "id": _clean_text(character.get("id")) or _clean_text(character_id),
                "name": _clean_text(character.get("name")) or "未知角色",
                "type": "character",
                "url": url,
                "required": True,
                "locked": bool(character.get("locked")),
                "status": "ready" if url else "missing",
                "source": "角色主参考",
            }
        )

    props_by_id = {
        _clean_text(prop.get("id")): _mapping(prop)
        for prop in _sequence(script_map.get("props"))
        if _clean_text(_mapping(prop).get("id"))
    }
    for prop_id in _sequence(frame_map.get("prop_ids")):
        prop = props_by_id.get(_clean_text(prop_id), {})
        url = _selected_variant_url(prop.get("image_asset")) or _clean_text(prop.get("image_url")) or None
        preview.append(
            {
                "id": _clean_text(prop.get("id")) or _clean_text(prop_id),
                "name": _clean_text(prop.get("name")) or "未知道具",
                "type": "prop",
                "url": url,
                "required": True,
                "locked": bool(prop.get("locked")),
                "status": "ready" if url else "missing",
                "source": "道具主参考",
            }
        )

    if include_style_references:
        art_direction = _mapping(script_map.get("art_direction"))
        style_config = _mapping(art_direction.get("style_config"))
        style_urls = _style_reference_urls(style_config)
        style_name = _clean_text(style_config.get("name")) or "风格参考"
        style_bindings = _mapping(bindings.get("style"))
        for index, url in enumerate(style_urls):
            preview.append(
                {
                    "id": f"style-{index}",
                    "name": style_name,
                    "type": "style",
                    "url": url,
                    "required": False,
                    "locked": bool(style_bindings.get("lock")),
                    "status": "ready" if url else "missing",
                    "source": "美术指导参考",
                }
            )

    return preview


def summarize_codex_reference_preview(preview_items: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    items = [_mapping(item) for item in preview_items]
    ready_items = [
        item for item in items if _clean_text(item.get("status")) == "ready" and bool(_clean_text(item.get("url")))
    ]
    required_items = [item for item in items if bool(item.get("required"))]
    ready_required_items = [
        item for item in required_items if _clean_text(item.get("status")) == "ready" and bool(_clean_text(item.get("url")))
    ]
    counts = Counter(_clean_text(item.get("type")) for item in ready_items)
    identity_count = counts.get("character", 0) + counts.get("prop", 0)
    environment_count = counts.get("continuity", 0) + counts.get("scene", 0) + counts.get("style", 0)
    locked_count = sum(1 for item in ready_items if bool(item.get("locked")))

    return {
        "ready_count": len(ready_items),
        "total_count": len(items),
        "required_ready_count": len(ready_required_items),
        "missing_required_count": max(0, len(required_items) - len(ready_required_items)),
        "continuity_count": counts.get("continuity", 0),
        "scene_count": counts.get("scene", 0),
        "character_count": counts.get("character", 0),
        "prop_count": counts.get("prop", 0),
        "style_count": counts.get("style", 0),
        "identity_count": identity_count,
        "environment_count": environment_count,
        "locked_count": locked_count,
    }


def summarize_codex_reference_items(reference_items: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    items = [_mapping(item) for item in reference_items]
    counts = Counter(_lower_key(item.get("role")) for item in items)
    ready_count = sum(1 for item in items if _clean_text(item.get("path")))
    identity_count = counts.get("character", 0) + counts.get("prop", 0) + counts.get("key_prop", 0)
    environment_count = counts.get("continuity", 0) + counts.get("scene", 0) + counts.get("style", 0)
    return {
        "ready_count": ready_count,
        "total_count": len(items),
        "required_ready_count": ready_count,
        "missing_required_count": 0,
        "continuity_count": counts.get("continuity", 0),
        "scene_count": counts.get("scene", 0),
        "character_count": counts.get("character", 0),
        "prop_count": counts.get("prop", 0) + counts.get("key_prop", 0),
        "style_count": counts.get("style", 0),
        "identity_count": identity_count,
        "environment_count": environment_count,
        "locked_count": sum(1 for item in items if bool(item.get("locked"))),
    }


def _policy_snapshot(policy: CodexImagegenRecommendationPolicy) -> Dict[str, Any]:
    return {
        "enabled": policy.enabled,
        "auto_apply": policy.auto_apply,
        "safe_direct_max_ready_refs": policy.safe_direct_max_ready_refs,
        "two_stage_min_ready_refs": policy.two_stage_min_ready_refs,
        "two_stage_min_identity_refs": policy.two_stage_min_identity_refs,
        "two_stage_min_character_refs": policy.two_stage_min_character_refs,
        "two_stage_min_prop_refs": policy.two_stage_min_prop_refs,
        "two_stage_min_scene_refs": policy.two_stage_min_scene_refs,
        "direct_when_required_refs_missing": policy.direct_when_required_refs_missing,
        "shot_type_overrides": policy.shot_type_overrides,
        "genre_overrides": policy.genre_overrides,
    }


def normalize_codex_recommendation_policy(
    value: Any,
) -> CodexImagegenRecommendationPolicy:
    if isinstance(value, CodexImagegenRecommendationPolicy):
        return value
    if isinstance(value, CodexImagegenPolicy):
        return value.recommendation
    if isinstance(value, Mapping):
        if "recommendation" in value and isinstance(value.get("recommendation"), Mapping):
            return CodexImagegenRecommendationPolicy.model_validate(value["recommendation"])
        return CodexImagegenRecommendationPolicy.model_validate(value)
    return CodexImagegenRecommendationPolicy()


def _resolve_threshold_overrides(
    policy: CodexImagegenRecommendationPolicy,
    *,
    shot_type: Optional[str] = None,
    genre: Optional[str] = None,
) -> Dict[str, int]:
    thresholds = {
        "safe_direct_max_ready_refs": policy.safe_direct_max_ready_refs,
        "two_stage_min_ready_refs": policy.two_stage_min_ready_refs,
        "two_stage_min_identity_refs": policy.two_stage_min_identity_refs,
        "two_stage_min_character_refs": policy.two_stage_min_character_refs,
        "two_stage_min_prop_refs": policy.two_stage_min_prop_refs,
        "two_stage_min_scene_refs": policy.two_stage_min_scene_refs,
    }
    for raw_key in (
        _lower_key(shot_type),
        _lower_key(genre),
    ):
        if not raw_key:
            continue
        overrides = policy.shot_type_overrides.get(raw_key) or policy.genre_overrides.get(raw_key)
        if not isinstance(overrides, Mapping):
            continue
        for key, value in overrides.items():
            if key not in thresholds:
                continue
            try:
                thresholds[key] = max(0, int(value))
            except (TypeError, ValueError):
                continue
    return thresholds


def recommend_codex_imagegen_mode(
    preview_items: Sequence[Mapping[str, Any]],
    policy: Any = None,
    *,
    shot_type: Optional[str] = None,
    genre: Optional[str] = None,
) -> Dict[str, Any]:
    policy_obj = normalize_codex_recommendation_policy(policy)
    summary = summarize_codex_reference_preview(preview_items)
    thresholds = _resolve_threshold_overrides(policy_obj, shot_type=shot_type, genre=genre)

    if not policy_obj.enabled:
        mode = SAFE_REFS_ONLY_MODE
        reason = "推荐开关已关闭，默认返回安全直连。"
        should_use_two_stage = False
    else:
        missing_required = summary["missing_required_count"]
        ready_count = summary["ready_count"]
        identity_count = summary["identity_count"]
        character_count = summary["character_count"]
        prop_count = summary["prop_count"]
        scene_count = summary["scene_count"]
        safe_direct_budget_ok = ready_count <= thresholds["safe_direct_max_ready_refs"]

        should_use_two_stage = (
            missing_required == 0
            and (
                (
                    not safe_direct_budget_ok
                    and ready_count >= thresholds["two_stage_min_ready_refs"]
                )
                or identity_count >= thresholds["two_stage_min_identity_refs"]
                or (character_count >= thresholds["two_stage_min_character_refs"] and prop_count >= 1)
                or (character_count >= thresholds["two_stage_min_character_refs"] and scene_count >= thresholds["two_stage_min_scene_refs"])
                or (prop_count >= thresholds["two_stage_min_prop_refs"] and character_count >= 1)
            )
        )

        if missing_required > 0 and policy_obj.direct_when_required_refs_missing:
            should_use_two_stage = False

        if should_use_two_stage:
            mode = TWO_STAGE_HIGH_CONSISTENCY_MODE
            if not safe_direct_budget_ok and ready_count >= thresholds["two_stage_min_ready_refs"]:
                reason = "参考数量已超过安全直连预算，建议先用两段式锁人物与关键道具，再细化场景和光影。"
            else:
                reason = "身份锚点分散且需要更强的一致性控制，建议先用两段式锁人物与关键道具，再细化场景和光影。"
        else:
            mode = SAFE_REFS_ONLY_MODE
            if missing_required > 0 and policy_obj.direct_when_required_refs_missing:
                reason = f"当前有 {missing_required} 个主参考缺失，先用安全直连稳住可用参考。"
            elif not safe_direct_budget_ok:
                reason = "虽然参考数量已超过安全直连预算，但当前身份锚点不足以强制切到两段式，先保持安全直连。"
            else:
                reason = "当前参考量较轻，安全直连已足够覆盖镜头一致性。"

    ready_count = summary["ready_count"]
    identity_count = summary["identity_count"]
    environment_count = summary["environment_count"]
    locked_count = summary["locked_count"]
    missing_required_count = summary["missing_required_count"]

    score = (
        ready_count * 11
        + identity_count * 9
        + environment_count * 7
        + locked_count * 3
        + (10 if summary["character_count"] >= 2 else 0)
        + (6 if summary["prop_count"] >= 2 else 0)
        + (8 if summary["scene_count"] >= 1 and identity_count >= 2 else 0)
        + (4 if summary["continuity_count"] >= 1 and summary["scene_count"] >= 1 else 0)
        - missing_required_count * 18
    )
    if mode == SAFE_REFS_ONLY_MODE:
        score = min(score, 59)
    else:
        score = max(score, 60)

    return {
        "mode": mode,
        "score": max(0, min(100, int(round(score)))),
        "reason": reason,
        "metrics": summary,
        "thresholds": thresholds,
        "policy": _policy_snapshot(policy_obj),
        "shot_type": _lower_key(shot_type) or None,
        "genre": _lower_key(genre) or None,
    }


def recommend_codex_imagegen_mode_from_reference_items(
    reference_items: Sequence[Mapping[str, Any]],
    policy: Any = None,
    *,
    shot_type: Optional[str] = None,
    genre: Optional[str] = None,
) -> Dict[str, Any]:
    policy_obj = normalize_codex_recommendation_policy(policy)
    summary = summarize_codex_reference_items(reference_items)
    thresholds = _resolve_threshold_overrides(policy_obj, shot_type=shot_type, genre=genre)

    if not policy_obj.enabled:
        mode = SAFE_REFS_ONLY_MODE
        reason = "推荐开关已关闭，默认返回安全直连。"
    else:
        missing_required = summary["missing_required_count"]
        ready_count = summary["ready_count"]
        identity_count = summary["identity_count"]
        character_count = summary["character_count"]
        prop_count = summary["prop_count"]
        scene_count = summary["scene_count"]
        safe_direct_budget_ok = ready_count <= thresholds["safe_direct_max_ready_refs"]
        two_stage = (
            missing_required == 0
            and (
                (
                    not safe_direct_budget_ok
                    and ready_count >= thresholds["two_stage_min_ready_refs"]
                )
                or identity_count >= thresholds["two_stage_min_identity_refs"]
                or (character_count >= thresholds["two_stage_min_character_refs"] and prop_count >= 1)
                or (character_count >= thresholds["two_stage_min_character_refs"] and scene_count >= thresholds["two_stage_min_scene_refs"])
                or (prop_count >= thresholds["two_stage_min_prop_refs"] and character_count >= 1)
            )
        )
        if missing_required > 0 and policy_obj.direct_when_required_refs_missing:
            two_stage = False

        if two_stage:
            mode = TWO_STAGE_HIGH_CONSISTENCY_MODE
            if not safe_direct_budget_ok and ready_count >= thresholds["two_stage_min_ready_refs"]:
                reason = "参考数量已超过安全直连预算，建议先用两段式锁人物与关键道具，再细化场景和光影。"
            else:
                reason = "身份锚点分散且需要更强的一致性控制，建议先用两段式锁人物与关键道具，再细化场景和光影。"
        else:
            mode = SAFE_REFS_ONLY_MODE
            if missing_required > 0 and policy_obj.direct_when_required_refs_missing:
                reason = f"当前有 {missing_required} 个主参考缺失，先用安全直连稳住可用参考。"
            elif not safe_direct_budget_ok:
                reason = "虽然参考数量已超过安全直连预算，但当前身份锚点不足以强制切到两段式，先保持安全直连。"
            else:
                reason = "当前参考量较轻，安全直连已足够覆盖镜头一致性。"

    score = (
        summary["ready_count"] * 11
        + summary["identity_count"] * 9
        + summary["environment_count"] * 7
        + summary["locked_count"] * 3
        + (10 if summary["character_count"] >= 2 else 0)
        + (6 if summary["prop_count"] >= 2 else 0)
        + (8 if summary["scene_count"] >= 1 and summary["identity_count"] >= 2 else 0)
        + (4 if summary["continuity_count"] >= 1 and summary["scene_count"] >= 1 else 0)
        - summary["missing_required_count"] * 18
    )
    if mode == SAFE_REFS_ONLY_MODE:
        score = min(score, 59)
    else:
        score = max(score, 60)

    return {
        "mode": mode,
        "score": max(0, min(100, int(round(score)))),
        "reason": reason,
        "metrics": summary,
        "thresholds": thresholds,
        "policy": _policy_snapshot(policy_obj),
        "shot_type": _lower_key(shot_type) or None,
        "genre": _lower_key(genre) or None,
    }


def build_codex_handoff_plan(
    preview_items: Sequence[Mapping[str, Any]],
    policy: Any = None,
    *,
    explicit_mode: Optional[str] = None,
    shot_type: Optional[str] = None,
    genre: Optional[str] = None,
) -> Dict[str, Any]:
    policy_obj = normalize_codex_recommendation_policy(policy)
    recommendation = recommend_codex_imagegen_mode(
        preview_items,
        policy_obj,
        shot_type=shot_type,
        genre=genre,
    )
    return select_codex_handoff_mode(
        policy,
        recommendation=recommendation,
        explicit_mode=explicit_mode,
    )


def select_codex_handoff_mode(
    policy: Any = None,
    *,
    recommendation: Optional[Mapping[str, Any]] = None,
    explicit_mode: Optional[str] = None,
) -> Dict[str, Any]:
    policy_obj = normalize_codex_recommendation_policy(policy)
    explicit_raw = _lower_key(explicit_mode)
    if explicit_raw == "auto" and recommendation:
        selected_mode = _normalize_pack_mode(recommendation.get("mode"))
        selection_source = "explicit_auto"
    elif explicit_mode not in {None, ""}:
        selected_mode = _normalize_pack_mode(explicit_mode)
        selection_source = "explicit_override"
    elif policy_obj.auto_apply and recommendation:
        selected_mode = _normalize_pack_mode(recommendation.get("mode"))
        selection_source = "recommendation_auto_apply"
    else:
        selected_mode = SAFE_REFS_ONLY_MODE
        selection_source = "manual_default"
        if isinstance(policy, CodexImagegenPolicy):
            selected_mode = _normalize_pack_mode(policy.mode)
            selection_source = "policy_mode"
        elif isinstance(policy, Mapping) and policy.get("mode") is not None:
            selected_mode = _normalize_pack_mode(policy.get("mode"))
            selection_source = "policy_mode"

    return {
        "mode": selected_mode,
        "selection_source": selection_source,
        "recommendation": dict(recommendation or {}),
    }


def build_codex_reference_recommendation_for_frame(
    script: Any,
    frame: Any,
    policy: Any = None,
    *,
    continuity_lock: Optional[bool] = None,
    include_style_references: bool = True,
) -> Dict[str, Any]:
    script_map = _mapping(script)
    frame_map = _mapping(frame)
    preview_items = build_codex_reference_preview(
        script_map,
        frame_map,
        continuity_lock=continuity_lock,
        include_style_references=include_style_references,
    )
    shot_type = _clean_text(frame_map.get("shot_size")) or _clean_text(frame_map.get("camera_angle"))
    if not shot_type:
        composition = _mapping(frame_map.get("composition_data"))
        shot_type = _clean_text(composition.get("shot_type")) or _clean_text(composition.get("camera_label"))
    genre = _clean_text(_mapping(script_map.get("generation_metadata")).get("genre"))
    if not genre:
        genre = _clean_text(script_map.get("fixture_project_type"))

    return {
        "preview": preview_items,
        "recommendation": recommend_codex_imagegen_mode(
            preview_items,
            policy,
            shot_type=shot_type or None,
            genre=genre or None,
        ),
        "handoff_plan": build_codex_handoff_plan(
            preview_items,
            policy,
            shot_type=shot_type or None,
            genre=genre or None,
        ),
    }


def enrich_project_payload_with_codex_imagegen_insights(
    payload: Any,
) -> Any:
    if isinstance(payload, list):
        return [enrich_project_payload_with_codex_imagegen_insights(item) for item in payload]
    if not isinstance(payload, Mapping):
        return payload

    enriched = dict(payload)
    policy = enriched.get("codex_imagegen_policy")
    frames = enriched.get("frames")
    if not isinstance(frames, list) or not policy:
        return enriched

    enriched_frames = list(frames)
    recommendation_map: Dict[str, Any] = {}
    for frame_index, frame in enumerate(enriched_frames):
        if not isinstance(frame, Mapping):
            continue
        frame_payload = dict(frame)
        frame_recommendation = build_codex_reference_recommendation_for_frame(
            enriched,
            frame_payload,
            policy,
            continuity_lock=frame_payload.get("composition_data", {}).get("continuity_lock")
            if isinstance(frame_payload.get("composition_data"), Mapping)
            else None,
            include_style_references=True,
        )
        composition = dict(frame_payload.get("composition_data") or {})
        composition.setdefault("codex_imagegen_reference_preview", frame_recommendation["preview"])
        composition["codex_imagegen_recommendation"] = frame_recommendation["recommendation"]
        composition["codex_imagegen_handoff_plan"] = frame_recommendation["handoff_plan"]
        frame_payload["composition_data"] = composition
        recommendation_map[_clean_text(frame_payload.get("id")) or f"frame-{len(recommendation_map)}"] = frame_recommendation["recommendation"]
        enriched_frames[frame_index] = frame_payload

    generation_metadata = dict(_mapping(enriched.get("generation_metadata")))
    generation_metadata["codex_imagegen_recommendations"] = recommendation_map
    enriched["generation_metadata"] = generation_metadata
    enriched["frames"] = enriched_frames
    return enriched
