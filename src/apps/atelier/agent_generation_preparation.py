"""Shared preparation of a generation proposal, before approval or admission.

Only the returned public binding is durable. This helper may freeze local
media and bind credential metadata, but cannot save a turn, admit paid work,
or schedule a provider. Call it outside the storage transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ...models.provider_tasks import ResolvedCredential
from .agent_authorization import TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, build_agent_generation_binding
from .generation_commands import CreateVideoCandidateCommand
from .generation_preview import build_generation_preview
from .models import AtelierAgentToolCall, AtelierAgentToolStatus, AtelierProject


def prepare_agent_generation_call(
    project: AtelierProject,
    proposal: Mapping[str, Any],
    *,
    turn_id: str,
    call_id: str,
    approval_id: str,
    client_request_id: str,
    expected_revision: int,
    media_scope: Any,
    credential: Callable[[], ResolvedCredential],
    server_storage_identity: str,
    clock: Callable[[], float],
) -> AtelierAgentToolCall:
    node_id = proposal["node_id"]
    request = CreateVideoCandidateCommand(
        expected_revision=expected_revision,
        client_request_id=client_request_id,
        **{key: value for key, value in proposal.items() if key != "node_id"},
    )
    preview = build_generation_preview(
        project, node_id, request,
        media_scope=media_scope,
        credential=credential,
        server_storage_identity=server_storage_identity,
    )
    binding = build_agent_generation_binding(
        preview, turn_id=turn_id, call_id=call_id, approval_id=approval_id,
        policy=project.agent_policy, created_at=clock(),
    )
    raw_request = preview.public_record["raw_request"]
    return AtelierAgentToolCall(
        call_id=call_id,
        tool_name=TOOL_GENERATION_CREATE_VIDEO_CANDIDATES,
        arguments={"node_id": node_id, **{key: value for key, value in raw_request.items() if key != "client_request_id"}},
        status=AtelierAgentToolStatus.APPROVAL_REQUIRED,
        approval_required=True,
        preview_binding=binding,
        created_at=binding.created_at,
    )
