import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.apps.atelier.models import (
    AtelierAgentPolicy,
    AtelierApprovalMode,
    AtelierAudioTrack,
    AtelierNode,
    AtelierProject,
    AtelierSequenceEntry,
    AtelierSequenceTransition,
)


def _legacy_project_payload() -> dict:
    return {
        "id": "atelier-old-1",
        "title": "Neon alley exploration",
        "description": "legacy persisted canvas",
        "source_project_id": "studio-seed-1",
        "nodes": [
            {
                "id": "node-draft-1",
                "project_id": "atelier-old-1",
                "type": "draft",
                "title": "Rain chase draft",
                "prompt": "A quiet neon alley chase",
                "status": "completed",
                "x": 10.5,
                "y": -20.25,
                "width": 360.0,
                "height": 220.0,
                "source_project_id": "studio-seed-1",
                "frame_id": "frame-7",
                "asset_id": "asset-hero",
                "video_task_id": "task-v-1",
                "media_urls": ["video/atelier/take-a.mp4"],
                "data": {
                    "candidates": [
                        {
                            "id": "cand-a",
                            "url": "video/atelier/take-a.mp4",
                            "score": 0.82,
                            "metadata": {"provider": "wan3", "seed": 123},
                        }
                    ],
                    "generation": {
                        "model": "wan3-omni-video",
                        "params": {"ratio": "16:9", "duration": 5, "audio": True},
                    },
                    "selected_candidate_id": "cand-a",
                    "human_selected_candidate_id": "cand-a",
                    "human_rejected_candidate_ids": ["cand-b"],
                    "reference_image_urls": ["uploads/ref.png"],
                },
                "created_by": "agent",
                "created_at": 1000.0,
                "updated_at": 1001.0,
            }
        ],
        "agent_policy": {
            "approval_mode": "on_request",
            "allowed_tools": ["canvas.readProject", "generation.createVideoCandidates"],
            "max_nodes_per_action": 6,
            "max_tokens_per_turn": 4096,
            "enable_reviewer": True,
            "max_generation_cost_per_turn": 3.5,
            "updated_at": 1002.0,
        },
        "agent_turns": [
            {
                "id": "turn-1",
                "project_id": "atelier-old-1",
                "user_message": "make it more cinematic",
                "preview": False,
                "session_id": "session-a",
                "status": "completed",
                "tool_calls": [
                    {
                        "call_id": "call-1",
                        "tool_name": "generation.createVideoCandidates",
                        "arguments": {"node_id": "node-draft-1", "candidate_count": 2},
                        "status": "completed",
                        "approval_required": True,
                        "approval_granted": True,
                        "error": None,
                        "result_snapshot": {"created_candidate_ids": ["cand-a", "cand-b"]},
                        "created_at": 1003.0,
                        "completed_at": 1004.0,
                    }
                ],
                "node_reservations": {"generation.createVideoCandidates": 1},
                "completion_tokens_used": 380,
                "completion_tokens_reserved": 512,
                "generation_cost_quotes": {"call-1": 1.25},
                "created_at": 1003.0,
                "completed_at": 1005.0,
                "response": "Created two candidates.",
                "iteration_count": 1,
                "messages_history": [
                    {"role": "user", "content": "make it more cinematic"},
                    {"role": "assistant", "content": "I will create candidates."},
                ],
                "max_iterations": 4,
                "pending_tool_calls": [],
                "skill_name": "cinematic_variants",
                "iterations": [
                    {
                        "idx": 1,
                        "provider": "dashscope",
                        "model_id": "qwen-plus",
                        "prompt_tokens": 1240,
                        "completion_tokens": 380,
                        "total_tokens": 1620,
                        "latency_ms": 1400.5,
                        "tool_call_ids": ["call-1"],
                        "started_at": 1003.0,
                        "completed_at": 1004.0,
                        "error": None,
                    }
                ],
            }
        ],
        "sequence": [
            {
                "parentId": "node-draft-1",
                "candidateId": "cand-a",
                "id": "clip-1",
                "trimStart": 0.5,
                "trimEnd": 4.5,
                "gain": 0.8,
                "transition": {"kind": "fade", "duration": 0.4},
            }
        ],
        "audio_tracks": [
            {
                "id": "audio-track-1",
                "node_id": "audio-node-1",
                "start": 1.0,
                "trim_start": 0.25,
                "trim_end": 8.0,
                "gain": 1.2,
                "loop": True,
            }
        ],
        "exports": [
            {
                "id": "export-1",
                "project_id": "atelier-old-1",
                "created_at": 1006.0,
                "clip_count": 1,
                "size_mb": 12.5,
                "video_url": "videos/atelier_seq_1.mp4",
                "filename": "atelier_seq_1.mp4",
                "source_entries": [
                    {
                        "parentId": "node-draft-1",
                        "candidateId": "cand-a",
                        "id": "clip-1",
                        "trimStart": 0.5,
                        "trimEnd": 4.5,
                        "gain": 0.8,
                        "transition": {"kind": "fade", "duration": 0.4},
                    }
                ],
                "timeline_snapshot": {
                    "schema_version": "atelier.timeline.v1",
                    "clips": [{"url": "video/atelier/take-a.mp4", "duration": 4.0}],
                },
            }
        ],
        "agent_compaction_summary": "Earlier turns explored lighting and pacing.",
        "compacted_through_turn_id": "turn-0",
        "created_at": 999.0,
        "updated_at": 1007.0,
    }


def test_old_candidate_project_json_roundtrips_without_losing_creative_state():
    payload = _legacy_project_payload()

    project = AtelierProject.model_validate(payload)
    dumped = project.model_dump(mode="json")

    assert dumped["revision"] == 0
    expected_payload = {
        **payload,
        "exports": [
            {
                **export,
                "publication_proof": export.get("publication_proof"),
                "job_id": export.get("job_id"),
                "snapshot_digest": export.get("snapshot_digest"),
                "deletion": export.get("deletion"),
            }
            for export in payload["exports"]
        ],
        "sequence_export_jobs": payload.get("sequence_export_jobs", []),
    }
    for key, value in expected_payload.items():
        assert dumped[key] == value
    assert dumped["nodes"][0]["data"]["human_selected_candidate_id"] == "cand-a"
    assert dumped["nodes"][0]["data"]["human_rejected_candidate_ids"] == ["cand-b"]
    assert dumped["sequence"][0]["transition"] == {"kind": "fade", "duration": 0.4}
    assert dumped["audio_tracks"][0]["loop"] is True
    assert dumped["exports"][0]["timeline_snapshot"]["schema_version"] == "atelier.timeline.v1"
    assert dumped["agent_turns"][0]["iterations"][0]["tool_call_ids"] == ["call-1"]
    assert dumped["agent_compaction_summary"] == "Earlier turns explored lighting and pacing."


def test_frontend_project_contract_fixture_is_a_complete_backend_snapshot():
    fixture = (
        Path(__file__).resolve().parents[1]
        / "frontend/src/__tests__/fixtures/atelier-project.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    expected = {
        **payload,
        "exports": [
            {
                **export,
                "publication_proof": export.get("publication_proof"),
                "job_id": export.get("job_id"),
                "snapshot_digest": export.get("snapshot_digest"),
                "deletion": export.get("deletion"),
            }
            for export in payload.get("exports", [])
        ],
        "node_delete_receipts": payload.get("node_delete_receipts", []),
        "node_upload_receipts": payload.get("node_upload_receipts", []),
        "sequence_export_jobs": payload.get("sequence_export_jobs", []),
        "generation_attempts": payload.get("generation_attempts", []),
        "generation_request_receipts": payload.get("generation_request_receipts", []),
    }
    assert AtelierProject.model_validate(payload).model_dump(mode="json") == expected


def test_defaults_are_independent_and_default_policy_is_untrusted():
    first = AtelierProject(id="a", title="A", created_at=1.0, updated_at=1.0)
    second = AtelierProject(id="b", title="B", created_at=2.0, updated_at=2.0)

    assert first.revision == 0
    assert first.agent_policy.approval_mode is AtelierApprovalMode.UNTRUSTED
    first.nodes.append(AtelierNode(id="node-1", project_id="a"))
    first.sequence.append(AtelierSequenceEntry(parentId="node-1"))
    first.audio_tracks.append(AtelierAudioTrack(id="track-1", node_id="audio-1"))
    first.agent_policy.allowed_tools.append("canvas.readProject")

    assert [node.id for node in first.nodes] == ["node-1"]
    assert second.nodes == []
    assert second.sequence == []
    assert second.audio_tracks == []
    assert second.agent_policy.allowed_tools == []
    assert second.agent_policy.approval_mode is AtelierApprovalMode.UNTRUSTED


def test_sequence_audio_and_transition_reject_actual_invalid_bounds():
    with pytest.raises(ValidationError):
        AtelierSequenceEntry(parentId="node-1", trimStart=-0.1)
    with pytest.raises(ValidationError):
        AtelierSequenceEntry(parentId="node-1", trimEnd=0)
    with pytest.raises(ValidationError):
        AtelierSequenceEntry(parentId="node-1", gain=2.1)
    with pytest.raises(ValidationError):
        AtelierSequenceTransition(kind="wipe")
    with pytest.raises(ValidationError):
        AtelierSequenceTransition(kind="fade", duration=-0.1)
    with pytest.raises(ValidationError):
        AtelierSequenceTransition(kind="fade", duration=math.inf)
    with pytest.raises(ValidationError):
        AtelierAudioTrack(id="track", node_id="audio", start=-1)
    with pytest.raises(ValidationError):
        AtelierAudioTrack(id="track", node_id="audio", gain=2.5)


def test_revision_is_strict_nonnegative_storage_seam():
    assert AtelierProject(id="p", title="P", created_at=1.0, updated_at=1.0, revision=3).revision == 3
    with pytest.raises(ValidationError):
        AtelierProject(id="p", title="P", created_at=1.0, updated_at=1.0, revision=-1)
    with pytest.raises(ValidationError):
        AtelierProject(id="p", title="P", created_at=1.0, updated_at=1.0, revision=True)
    with pytest.raises(ValidationError):
        AtelierProject(id="p", title="P", created_at=1.0, updated_at=1.0, revision="1")


def test_policy_bounds_match_candidate_contract():
    assert AtelierAgentPolicy(max_nodes_per_action=1, max_tokens_per_turn=128).max_nodes_per_action == 1
    with pytest.raises(ValidationError):
        AtelierAgentPolicy(max_nodes_per_action=0)
    with pytest.raises(ValidationError):
        AtelierAgentPolicy(max_nodes_per_action=51)
    with pytest.raises(ValidationError):
        AtelierAgentPolicy(max_tokens_per_turn=127)
    with pytest.raises(ValidationError):
        AtelierAgentPolicy(max_tokens_per_turn=16385)
