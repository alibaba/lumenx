from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from src.apps.atelier.models import AtelierNode
from src.apps.atelier.references import (
    can_attach_atelier_reference,
    canonical_atelier_reference_data,
    reference_media_urls,
    reference_parent_data,
    reference_strings,
    unlink_atelier_reference_data,
)


def node(
    node_id: str,
    node_type: str,
    *,
    project_id: str = "p1",
    media_urls: list[str] | None = None,
    data: dict | None = None,
) -> AtelierNode:
    return AtelierNode(
        id=node_id,
        project_id=project_id,
        type=node_type,
        media_urls=list(media_urls or []),
        data=deepcopy(data or {}),
        created_at=1.0,
        updated_at=1.0,
    )


def test_reference_strings_and_media_urls_collect_completed_candidate_aliases_without_mutation():
    source = node(
        "src",
        "video",
        media_urls=["direct.mp4", "direct.mp4", ""],
        data={
            "candidates": [
                {"status": "completed", "media_url": "media.mp4", "video_url": "video.mp4"},
                {"status": "pending", "media_url": "pending.mp4", "video_url": "pending-video.mp4"},
                "opaque",
            ],
            "deleted_candidates": [
                {"status": "completed", "media_url": "deleted.mp4", "video_url": "deleted-video.mp4"},
                {"status": "failed", "media_url": "failed.mp4"},
            ],
        },
    )
    before = source.model_dump(mode="json")

    assert reference_strings(["a", "", "a", None, "b"]) == ["a", "b"]
    assert reference_strings("not-list") == []
    assert reference_media_urls(source) == [
        "direct.mp4",
        "media.mp4",
        "video.mp4",
        "deleted.mp4",
        "deleted-video.mp4",
    ]
    assert source.model_dump(mode="json") == before




def test_reference_media_urls_ignores_opaque_non_list_candidate_containers_without_mutation():
    for opaque in (7, True, {"status": "completed", "media_url": "hidden.png"}, "bad", None):
        source = node(
            "src",
            "image",
            media_urls=["direct.png"],
            data={"candidates": opaque, "deleted_candidates": opaque},
        )
        before = source.model_dump(mode="json")
        assert reference_media_urls(source) == ["direct.png"]
        assert source.model_dump(mode="json") == before

    source = node(
        "src",
        "image",
        media_urls=["direct.png"],
        data={
            "candidates": ["bad", 1, True, None, {"status": "completed", "media_url": "ok.png"}],
            "deleted_candidates": [{"status": "completed", "video_url": "old.mp4"}],
        },
    )
    before = source.model_dump(mode="json")
    assert reference_media_urls(source) == ["direct.png", "ok.png", "old.mp4"]
    assert source.model_dump(mode="json") == before


def test_can_attach_reference_rejects_cross_project_self_and_incompatible_kinds():
    video = node("video", "video")
    image = node("image", "image")
    audio = node("audio", "audio")
    other_project = node("other", "image", project_id="p2")

    assert can_attach_atelier_reference(video, image) is True
    assert can_attach_atelier_reference(video, audio) is True
    assert can_attach_atelier_reference(image, node("image2", "image")) is True
    assert can_attach_atelier_reference(image, audio) is False
    assert can_attach_atelier_reference(video, video) is False
    assert can_attach_atelier_reference(video, other_project) is False


def test_canonical_moves_misplaced_video_audio_urls_but_keeps_unknown_legacy_urls():
    target = node(
        "target",
        "video",
        data={
            "reference_node_ids": ["vid-src", "audio-src", "missing"],
            "reference_image_urls": ["unknown.png", "vid.mp4", "audio.wav"],
            "reference_video_urls": ["old.mp4"],
        },
    )
    vid = node("vid-src", "video", media_urls=["vid.mp4"])
    audio = node("audio-src", "audio", media_urls=["audio.wav"])
    unrelated = node("unrelated", "video", media_urls=["unknown.png"])
    nodes = [target, vid, audio, unrelated]
    before_nodes = [item.model_dump(mode="json") for item in nodes]

    refs = canonical_atelier_reference_data(target, nodes)

    assert refs["reference_node_ids"] == ["vid-src", "audio-src"]
    assert refs["reference_image_urls"] == ["unknown.png"]
    assert refs["reference_video_urls"] == ["old.mp4", "vid.mp4"]
    assert refs["reference_audio_urls"] == ["audio.wav"]
    assert [item.model_dump(mode="json") for item in nodes] == before_nodes


def test_unlink_multiple_sources_keeps_shared_url_only_when_remaining_target_source_proves_it():
    target = node(
        "target",
        "video",
        data={
            "reference_node_ids": ["a", "b"],
            "reference_video_urls": ["shared.mp4", "a-only.mp4", "unknown.mp4"],
        },
    )
    source_a = node("a", "video", media_urls=["shared.mp4", "a-only.mp4"])
    source_b = node("b", "video", media_urls=["shared.mp4"])
    unrelated = node("c", "video", media_urls=["a-only.mp4"])

    remove_a = unlink_atelier_reference_data(target, [target, source_a, source_b, unrelated], [source_a])
    assert remove_a["reference_node_ids"] == ["b"]
    assert remove_a["reference_video_urls"] == ["shared.mp4", "unknown.mp4"]

    remove_both = unlink_atelier_reference_data(target, [target, source_a, source_b, unrelated], [source_a, source_b])
    assert remove_both["reference_node_ids"] == []
    assert remove_both["reference_video_urls"] == ["unknown.mp4"]


def test_unlink_by_specific_url_and_parent_hint_select_remaining_compatible_target():
    source = node("src", "image", media_urls=["image.png"], data={"parent_node_id": "old-target"})
    old_target = node(
        "old-target",
        "video",
        data={"reference_node_ids": ["src"], "reference_image_urls": ["image.png"]},
    )
    new_target = node(
        "new-target",
        "video",
        data={"reference_node_ids": ["src"], "reference_image_urls": ["image.png"]},
    )
    image_target = node("image-target", "image", data={"reference_node_ids": ["src"]})

    unlinked = unlink_atelier_reference_data(old_target, [old_target, new_target, source], [source], url="image.png")
    assert unlinked["reference_node_ids"] == []
    assert unlinked["reference_image_urls"] == []

    hint = reference_parent_data(source, [old_target, new_target, image_target, source], removed_target_id="old-target")
    assert hint == {"parent_node_id": "new-target", "reference_role": "video_reference_image"}

    cleared = reference_parent_data(source, [old_target, source], removed_target_id="old-target")
    assert cleared == {"parent_node_id": None, "reference_role": None}


def test_parent_hint_requires_explicit_remaining_reference_not_just_media_or_parent_marker():
    source = node("src", "image", media_urls=["image.png"], data={"parent_node_id": "old-target"})
    old_target = node("old-target", "video", data={"reference_node_ids": ["src"]})
    media_only = node("media-only", "video", data={"reference_image_urls": ["image.png"]})
    parent_marker_only = node("parent-marker", "video")
    source_with_marker = source.model_copy(deep=True, update={"data": {"parent_node_id": "parent-marker"}})

    assert reference_parent_data(source, [old_target, media_only, source], removed_target_id="old-target") == {
        "parent_node_id": None,
        "reference_role": None,
    }
    assert reference_parent_data(source_with_marker, [old_target, parent_marker_only, source_with_marker], removed_target_id="old-target") == {
        "parent_node_id": None,
        "reference_role": None,
    }


def test_shared_frontend_reference_contract_fixture_matches_backend_helpers():
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "src"
        / "__tests__"
        / "fixtures"
        / "atelier-reference-contract.json"
    )
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert cases
    for case in cases:
        nodes = [
            node(
                item["id"],
                item["type"],
                project_id=item.get("project_id", "p1"),
                media_urls=item.get("media_urls") or [],
                data=item.get("data") or {},
            )
            for item in case["nodes"]
        ]
        before = [item.model_dump(mode="json") for item in nodes]
        target = next(item for item in nodes if item.id == case["target_id"])

        assert canonical_atelier_reference_data(target, nodes) == case["expected"]
        assert [item.model_dump(mode="json") for item in nodes] == before
