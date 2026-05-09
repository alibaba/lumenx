# Atelier Agent Runtime Implementation Plan

Date: 2026-05-09

## Context

Atelier v1 already has a canvas-first workflow for creating video nodes, attaching reference images, generating multiple candidate videos, and selecting the best result. The next product layer is an in-canvas Agent that can translate user intent into proposed canvas actions, then execute those actions through bounded tools.

This plan references three current general-agent implementations:

- Hermes Agent: tool registry, toolset availability, gateway approvals, progressive Skills.
- OpenAI Codex: explicit approval modes, sandbox/permission profiles, thread and turn event streams.
- claw-code: permission enforcement, allowed tool filtering, deterministic mock parity harness.

Atelier should not copy a general coding agent runtime. The correct v1 is a narrow creative-canvas agent harness whose tools mutate Atelier projects only through existing Atelier Core APIs.

## Product Decision

Use a shared Agent Runtime inside Atelier Core, not a separate generic automation runtime.

Atelier Agent v1 should operate as:

```text
User intent
  -> Agent turn
  -> proposed tool calls
  -> policy/harness validation
  -> approval if required
  -> Atelier Core mutation
  -> visible canvas diff + transcript event
```

The Agent should never get direct filesystem, shell, arbitrary network, or provider credentials access in v1. Its only write surface is the Atelier canvas/project API.

## Reference Takeaways

### Hermes Agent

What to adopt:

- Central tool registry with schema, handler, toolset, availability check, and result budgets.
- Skills as progressive disclosure: list metadata first, load detailed instructions only when needed.
- Gateway-style approval/event model where a request can pause until user choice.

What not to adopt in v1:

- Multi-platform chat gateway.
- Autonomous long-running cron/delegation runtime.
- Broad OS/browser/file tools.

### Codex

What to adopt:

- Codex-like approval modes: `untrusted`, `on_failure`, `on_request`, `never`.
- Turn-based event stream: agent message, tool call started/completed/failed, approval requested, turn completed.
- Separate tool permission and execution sandbox concepts.
- Explicit action categories rather than one undifferentiated "tool call".

What not to adopt in v1:

- Full filesystem sandbox.
- MCP server lifecycle.
- Terminal/patch/network permissions.

### claw-code

What to adopt:

- Permission enforcer before tool execution.
- `allowedTools` filtering independent of global permission mode.
- Harness-style deterministic scenarios for approval, denial, multi-tool turns, and plugin/tool roundtrips.
- Tool registry rejects name conflicts.

What not to adopt in v1:

- Claude Code parity surface.
- Rust runtime split.
- Plugin execution surface.

## Core Architecture

### Backend Modules

```text
src/apps/comic_gen/
├── models.py
│   ├── AtelierAgentPolicy
│   ├── AtelierAgentTurn
│   ├── AtelierAgentToolCall
│   └── AtelierAgentToolResult
├── atelier_agent.py
│   ├── AtelierToolSpec
│   ├── AtelierToolRegistry
│   ├── AtelierPermissionEnforcer
│   └── AtelierAgentHarness
├── pipeline.py
│   └── existing Atelier project/node/candidate mutations
└── api.py
    └── /atelier/projects/{id}/agent/*
```

`atelier_agent.py` should be the boundary. It should know tool schemas, policy enforcement, and execution dispatch, but it should call `ComicGenPipeline` for actual mutations instead of modifying project state directly.

### Frontend Modules

```text
frontend/src/lib/api.ts
  Agent tool specs, turn APIs, approval APIs

frontend/src/store/atelierStore.ts
  agent turn state, pending approvals, transcript refresh

frontend/src/components/atelier/AtelierShell.tsx
  Agent panel transcript, permission selector, proposed actions, approval controls
```

The existing Agent Panel remains the interaction home. Do not add a separate hidden automation page.

## Tool Model

Each tool should have:

- `name`: stable namespaced ID, for example `canvas.createVideoNode`.
- `description`: model-facing behavior description.
- `input_schema`: JSON schema.
- `required_permission`: `read`, `canvas_write`, `generation`.
- `mutates_canvas`: boolean.
- `requires_user_asset`: boolean.
- `max_count_cost`: how it counts against `max_nodes_per_action`.
- `executor`: a bounded backend function.

Initial tool set:

| Tool | Purpose | Permission |
| --- | --- | --- |
| `canvas.createVideoNode` | Create an empty video generation node with prompt/model defaults | `canvas_write` |
| `canvas.updateNodePrompt` | Update prompt/model/params on an existing video node | `canvas_write` |
| `canvas.createReferenceImageNode` | Create a reference image node from an existing uploaded/generated media URL | `canvas_write` |
| `canvas.attachReferenceNode` | Link an image node to a video node | `canvas_write` |
| `generation.createVideoCandidates` | Start candidate generation for a video node | `generation` |
| `canvas.readProject` | Return compact canvas state for planning | `read` |

Do not expose `candidate.select` to the Agent in v1. Selection is taste judgment and should remain user-owned.

## Approval Policy

Reuse the existing `AtelierAgentPolicy` fields:

- `approval_mode`
- `allowed_tools`
- `max_nodes_per_action`

Semantics:

- `untrusted`: all mutating tool calls pause for user approval before execution.
- `on_failure`: safe canvas writes can execute; failed calls are surfaced and user can approve a retry/escalation. Generation calls should still require approval if they spend provider credits.
- `on_request`: only calls explicitly marked `requires_approval` or exceeding normal bounds pause.
- `never`: run without asking, still respecting `allowed_tools`, hard validation, max node count, and unsupported-tool denial.

Hard denials apply in every mode:

- unknown tool
- tool not in `allowed_tools` when the allowlist is non-empty
- project/node not found
- node-count cost exceeds `max_nodes_per_action`
- generation request missing required references
- model/reference input mismatch
- direct filesystem/network/shell request

## Harness

The v1 harness should support two phases:

1. `preview`: validate and return an executable plan without mutating state.
2. `execute`: enforce policy and either execute, return `approval_required`, or return structured denial/error.

Every tool call result should be persisted with:

- `turn_id`
- `call_id`
- `tool_name`
- `arguments`
- `status`
- `approval_required`
- `approval_granted`
- `created_at`
- `completed_at`
- `error`
- `result_snapshot`

This gives the Agent Panel a stable transcript and makes future LLM integration debuggable.

## Continuous Call Chain

Do not implement an unbounded autonomous loop.

Atelier Agent v1 should support:

- max 8 tool calls per turn by default
- max 1 generation tool call per turn by default
- deterministic stop when all proposed calls are executed, denied, or waiting for approval
- user interruption by starting a new prompt or canceling pending approvals
- project refresh after each mutating call

The LLM layer can later call tools in multiple rounds, but the backend harness must remain deterministic and bounded.

## Skills

Skills should be prompt/planning packages, not executable plugins.

Initial internal skills:

- `idea-to-canvas`: turn an idea into 2-4 proposed video nodes.
- `reference-expander`: suggest reference image nodes from existing media.
- `shot-variant-maker`: create prompt variants for one selected node.
- `candidate-brief`: prepare generation parameters for a video node.

Store skill metadata separately from tool specs. The Agent can load skill instructions into its prompt, but Skills should not bypass permission enforcement.

## Implementation Sequence

1. Backend foundation:
   - Add `atelier_agent.py`.
   - Add agent turn/tool-call models.
   - Add tool registry and permission enforcer.
   - Add preview/execute APIs.
   - Add unit tests for allow/deny/approval_required/execution paths.

2. Frontend API/store:
   - Add Agent tool/turn API types.
   - Add store state for `agentTurns`, `pendingApprovals`, and tool execution.

3. Agent Panel v1:
   - Show transcript.
   - Show available tools.
   - Show permission mode and allowlist.
   - Show proposed tool call diff.
   - Approve/deny pending tool calls.

4. LLM integration:
   - Add a narrow agent prompt that sees compact canvas state and tool schemas.
   - Ask LLM for structured tool-call plans first.
   - Execute through harness only; never directly mutate state from model text.

5. Skills:
   - Add local skill manifests and a loader.
   - Allow Agent prompt to opt into a skill by name.
   - Keep Skills read-only prompt assets in v1.

## Acceptance Criteria

- User can type an intent in the Agent Panel.
- Backend can create a validated proposed canvas action plan.
- In `untrusted`, mutating calls return `approval_required` and do not mutate state until approved.
- In `never`, valid canvas writes execute without approval.
- `allowed_tools` blocks disallowed tools even in `never`.
- Generation tool validates model/reference inputs before starting provider work.
- Agent transcript preserves every tool call and result.
- Tests cover multi-tool turn, approval required, approval granted, denial, max node limit, and generation input validation.

## Non-Goals For V1

- Arbitrary shell/file/network tools.
- External MCP server support.
- Plugin execution.
- Autonomous background scheduling.
- Multi-agent delegation.
- User taste decisions such as selecting the final candidate.
