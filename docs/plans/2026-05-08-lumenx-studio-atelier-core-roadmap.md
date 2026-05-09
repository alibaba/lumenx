# LumenX Studio / Atelier / Core Roadmap

> This document supersedes the product-direction portions of
> `docs/plans/2026-04-19-lumenx-product-roadmap-and-implementation-plan.md`.
> Keep the older document as historical context. This plan reflects the R2V-first
> shift, the agent-operated infinite workspace direction, and the decision to
> prepare Studio and Atelier as separable product shells over a shared Core.

## 1. Thesis

LumenX should evolve as a product family built on one shared creation engine:

```text
LumenX Core
├── LumenX Studio
└── LumenX Atelier
```

The split is not mainly technical. It is a product-shape split:

- **LumenX Studio** remains a pipeline-first production product for workshop,
  team, series, and high-throughput motion-comic creation.
- **LumenX Atelier** becomes a graph-first personal creation product for
  open-ended AI video exploration, agent-operated generation, and final film
  assembly.
- **LumenX Core** owns model, media, generation, asset, job, provider, and
  export capabilities shared by both product shells.

Studio should not be redesigned into an infinite canvas. Atelier should not be a
minor mode inside Studio. They can share the Core while preserving independent
front-end information architecture, user mental model, and future extraction
paths.

## 2. Product Boundaries

### LumenX Core

Core is the shared backend and runtime layer. It should be designed as a stable
API surface, not as a Studio-specific implementation detail.

Core owns:

- `model_catalog` and generated model metadata.
- `provider_registry`, provider routing, provider media handling.
- Media storage, asset references, upload/download/signing behavior.
- Generation jobs and status transitions.
- Image, video, audio, and SFX generation capabilities.
- Export/merge primitives.
- Auth, settings, provider credentials, billing hooks where applicable.

Core must not depend on Studio or Atelier UI concepts. Product shells may
compose Core capabilities differently, but Core should expose typed creation
primitives rather than page-specific procedures.

Core has two API layers:

```text
Core primitives
- model/provider/media/job/export primitives shared by all shells

Product-domain APIs
- Studio pipeline/project APIs
- Atelier canvas/agent/sequence APIs
```

Atelier canvas state, sequence state, and agent runs may live in the same
backend service/repo for v1, but they are **Atelier-domain APIs**, not shared
Core primitives. This keeps Core reusable while still letting Atelier ship
without creating a separate backend service too early.

### LumenX Studio

Studio remains the pipeline production shell.

Primary use case:

```text
Project / Series
-> Script
-> Art direction
-> Assets
-> Storyboard / R2V storyboard
-> Generation
-> Assembly
-> Export
```

Studio target users:

- Workshops producing videos in volume.
- Teams managing series, episodes, reusable assets, and consistent outputs.
- Creators who already have a script or story and want guided production.

Studio near-term focus:

- Finish R2V-first workflow adaptation.
- Treat reference video and storyboard video as first-class production objects.
- Keep project/series structure, batch generation, consistency, and delivery
  throughput as primary values.
- Avoid pulling Studio into general-purpose infinite-canvas complexity.

### LumenX Atelier

Atelier is the graph-first personal creation shell.

Primary use case:

```text
Seed
-> Agent Plan
-> Draft Nodes
-> Approved Generation
-> Takes
-> Judgment
-> Branches
-> Sequence
-> MP4 Export
```

Atelier target users:

- Individual creators starting from an idea, prompt, image, reference video, or
  existing generated clip.
- Creators who want to explore multiple directions before locking a final video.
- Power users who care about prompt, reference, model, and execution trace
  control without starting from a raw ComfyUI-style workflow graph.

Atelier is not Studio's sidecar in v1. It has its own creation loop and can
produce final films without sending anything back to Studio.

## 3. Shared-Core Strategy

Studio and Atelier should share backend capability, not front-end state.

Shared:

- Model/provider capabilities.
- Media and asset references.
- Generation job runtime.
- Export primitives.
- Account/provider settings.
- Billing and cost accounting primitives.

Not shared in v1:

- Studio project state.
- Studio pipeline state.
- Atelier canvas state.
- Atelier sequence state.
- Cross-product project synchronization.

Studio/Atelier bridge is deliberately postponed. If added later, the bridge
should pass narrow artifacts such as `ExplorationBrief` and `ApprovedTake`, not
try to convert whole Studio projects into Atelier canvases or vice versa.

Recommended backend boundary:

```text
src/apps/comic_gen/       # existing Studio-oriented domain
src/apps/atelier/         # future Atelier-domain APIs
src/core/ or src/utils/   # shared primitives: catalog, provider, media, jobs
```

Do not put Atelier canvas persistence inside Studio project models. Do not make
Studio project state the parent of Atelier canvas state.

## 4. Atelier V1 Scope

Atelier v1 should prove the independent graph-first creation loop.

### Required Surfaces

```text
Center: Creative Graph Canvas
Right: Agent Panel
Bottom: Lightweight Sequence Strip
Inspector: Selected node / draft review / capsule controls
```

### Required Creation Modes

Atelier v1 supports:

- T2I
- I2I
- T2V
- I2V
- R2V
- V2V
- Audio
- SFX

UI naming:

- User-facing label: **Video Edit**
- Technical mode: `v2v`

V2V v1 means whole-clip video edit / transform using a prompt and optional
references. It does not imply frame-level masking, object tracking, keyframe
editing, or multi-layer compositing unless the underlying model/API later makes
those controls product-ready.

### Seed Inputs

Atelier starts from **Seed**, not from Story and not from Workflow.

V1 seed types:

- Text Seed
- Image Seed
- Video Seed
- Story Seed

Deferred:

- Workflow Seed
- Asset Seed

Rationale: some users arrive with a story, but many arrive with only a prompt,
image, video, or vague visual impulse. Atelier must let work begin before the
narrative structure is clear.

## 5. Creative Graph Model

Atelier is a **Creative Graph**, not only a Story Graph and not only an
Execution Graph.

Creative Graph contains:

- Execution Layer: how something is generated.
- Exploration Layer: how alternatives and branches are created.
- Narrative Layer: how generated material becomes a sequence or film.

The graph should let generated outputs grow into story, shot, reference, style,
sequence, or reusable judgment context.

### Base Node

All canvas nodes should share a base shape:

```ts
type BaseNodeStatus =
  | "draft"
  | "review"
  | "approved"
  | "running"
  | "completed"
  | "failed"
  | "archived";

interface BaseNode {
  id: string;
  canvasId: string;
  type: string;
  title: string;
  position: { x: number; y: number };
  size?: { width: number; height: number };
  status: BaseNodeStatus;
  owner: "user" | "agent" | "system";
  createdByRunId?: string;
  createdAt: string;
  updatedAt: string;
}
```

Each node type owns typed payload:

- `SeedPayload`
- `PlanPayload`
- `DraftGenerationPayload`
- `TakePayload`
- `ReferencePayload`
- `SequencePayload`
- `GroupPayload`

The base status enum is unified, but each node type defines its allowed
transitions. For example, `GenerationNode` may move from `draft` to `review` to
`approved` to `running`, while `TakeNode` normally starts at `completed`.

### Edge Types

Recommended v1 edge types:

```ts
type EdgeType =
  | "derived_from"
  | "uses_reference"
  | "generates"
  | "alternative_to"
  | "edited_from"
  | "belongs_to_plan"
  | "added_to_sequence";
```

Edges must carry creative meaning where possible. They are not only data pipes.

## 6. Agent-Operated Canvas

Atelier's differentiator is not that it has a canvas. Canvas is becoming table
stakes. The differentiator is that an agent can operate the creative graph with
bounded tools, visible plans, explicit permissions, and auditable runs.

### Core Interaction Loop

```text
User describes idea
-> Agent creates PlanNode
-> Agent creates draft generation nodes
-> User reviews intent, refs, cost, and prompt
-> User approves selected jobs
-> Core runs generation jobs
-> Results return as TakeNodes
-> User judges keep/reject/approve
-> Agent helps branch, sequence, or fill gaps
```

Agent is not only a chat assistant. It is a **Canvas Operator** and **Creative
Director Assistant**.

### Default Variant Behavior

When the user asks the agent to create generation options, the default is three
draft generation nodes:

1. Direct interpretation.
2. Cinematic/dramatic interpretation.
3. Wildcard/surprising branch.

Default behavior is same-type variants:

- User asks for video -> three `VideoGen` draft nodes.
- User asks for image -> three `ImageGen` draft nodes.

If input context is weak, the agent may create mixed planning nodes instead:

- Seed node.
- Image direction nodes.
- Video draft node.
- Reference request or prompt refinement nodes.

### Draft Review

Draft node review should focus on direction, not only prompt text.

Required card content:

- Creative intent label.
- Prompt.
- References.
- Recommended model and key params.
- Cost/time estimate.
- Approval action.

Advanced params live in the capsule inspector.

### Agent Panel

Atelier should use a dual-surface model:

```text
Canvas = durable work state
Agent Panel = conversation and control history
```

Canvas owns:

- Plan nodes.
- Draft nodes.
- Take nodes.
- Reference nodes.
- Edges.
- Status.
- Approval state.

Agent Panel owns:

- Messages.
- Tool-call timeline.
- Approval requests.
- Run summaries.
- Quick actions.

Clicking an agent message should locate related nodes. Clicking a node should
show relevant conversation/tool history in the panel.

Atelier supports:

- Canvas-level chat.
- Node-level focused thread.
- Selection-level agent operation.

## 7. Tools, Skills, and Harness

### Tools

Tools are typed, bounded actions over canvas, generation, and sequence state.

Examples:

- `create_node`
- `update_node`
- `connect_nodes`
- `group_nodes`
- `propose_generation_job`
- `submit_generation_job`
- `add_to_sequence`
- `export_sequence`

Agent must not write DB state directly. It must use tools that enforce
permissions, state transitions, cost controls, and audit logs.

### Skills

Skills are high-level reusable creative procedures built on tools.

Atelier v1 uses built-in Skills only:

- Explore 3 Directions
- Prompt Polish
- Build Sequence From Takes
- Fill Sequence Gaps
- Generate Reference Set
- Convert Idea To Shots
- Retry Failed Generation
- Compare Takes
- Add Sound Design

Deferred:

- User-authored skills.
- Skill marketplace.
- Arbitrary code skills.

### Harness

Agent Harness should be a persisted, auditable run system, not only a transient
chat model loop.

Recommended run shape:

```ts
interface AgentRun {
  id: string;
  canvasId: string;
  agentSessionId: string;
  skillName?: string;
  input: unknown;
  permissionMode: string;
  status: "running" | "awaiting_approval" | "completed" | "failed" | "cancelled";
  toolCalls: ToolCallRecord[];
  approvals: ApprovalRecord[];
  createdNodeIds: string[];
  createdJobIds: string[];
  summary?: string;
}
```

Harness must support:

- Replay/audit.
- Resume.
- Cancel.
- Retry failed tool calls.
- Roll back reversible draft artifacts.

Rollback may remove:

- Agent-created draft nodes.
- Agent-created edges.
- Agent-created groups.
- Unsent proposed jobs.
- Plan nodes from the same run.

Rollback must not automatically delete:

- Completed generation outputs.
- User-edited nodes.
- Approved jobs.
- Exported files.
- Shared links.

Those should be archived, hidden, detached, or explicitly deleted by the user.

## 8. Permission Model

Atelier needs an agent permission model similar in spirit to Codex, but adapted
to creative cost, queue, and media risks.

Permissions are:

```text
Account default + Canvas override
```

Recommended modes:

- **Cautious**: ask before every canvas action.
- **Balanced**: draft freely, ask before generation. Default.
- **Autopilot**: run approved tool classes within budget.
- **Unrestricted**: run without asking inside current workspace limits.

Tool risk classes:

```text
Safe
- create draft node
- connect nodes
- annotate
- group nodes
- fork node

Review
- modify user-created draft
- change references
- change prompt meaning
- create batch plan

Costly
- image generation
- video generation
- video edit
- upscale
- audio generation
- SFX generation

Destructive
- delete node
- overwrite user node
- remove media
- publish/share
```

Default Balanced behavior:

- Safe actions: auto.
- Review actions: ask when user-owned or meaning-changing.
- Costly actions: ask.
- Destructive actions: always ask.

Auto-run modes require:

- Budget cap.
- Max jobs.
- Model whitelist.
- Max duration.
- Max parallelism.
- Allowed tool classes.

## 9. Memory

Atelier memory exists to preserve creative continuity without letting the agent
quietly rewrite user taste or story canon.

V1 memory categories:

- Creative Preference.
- Canon.
- Technical Preference.
- Negative Memory.

Scopes:

- Account.
- Canvas.
- Node.

Write strategy:

- Node/local memory: may be inferred from direct judgment.
- Canvas memory: may be inferred from repeated or strong signals.
- Account memory: requires confirmation.
- Canon memory: requires confirmation before changing established facts.

Example:

- Rejecting a take with "too much like an ad" can create canvas-level Negative
  Memory.
- Repeatedly approving low-saturation slow push-in shots can propose an account
  Creative Preference, but should not silently save it globally.

## 10. Sequence and Export

Atelier must close the creation loop itself. It cannot only be an exploration
board.

V1 required surface:

```text
Lightweight Sequence Strip
```

Sequence Strip is not a full NLE. It is a minimal film assembly surface.

V1 sequence capabilities:

- Add generated video takes.
- Add gap placeholders.
- Order clips.
- Basic trim in/out.
- Add music/audio.
- Add SFX at timestamp.
- Basic volume controls.
- Transition markers: cut, fade, beat cut.
- Export merged MP4.

Deferred:

- Multi-track professional editing.
- Keyframes.
- Color grading.
- Full subtitle editor.
- Complex audio mixing.
- Frame-level V2V editing.

Recommended data shape:

```ts
interface Sequence {
  id: string;
  canvasId: string;
  clips: Clip[];
  audioItems: AudioItem[];
  transitionMarkers: TransitionMarker[];
  exportSettings: ExportSettings;
}

interface Clip {
  takeNodeId: string;
  mediaUrl: string;
  in: number;
  out: number;
  duration: number;
  transitionIn?: string;
  transitionOut?: string;
}

interface AudioItem {
  mediaUrl: string;
  start: number;
  in: number;
  out: number;
  volume: number;
  type: "music" | "voice" | "sfx";
}
```

Export v1:

- MP4 export.
- Saved canvas.
- Private read-only share link.
- Supported output formats: 9:16, 16:9, 1:1.

Not v1:

- Public gallery.
- Remix marketplace.
- Comments.
- User profiles.
- Studio handoff.

## 11. Execution Trace

Atelier should not expose editable Execution Graph in v1, but it should not be a
black box.

V1 should include:

- Capsule Inspector.
- Read-only Execution Trace.

Draft/Take inspector:

```text
Prompt
References
Model params
Cost/time
Execution Trace
├── prompt polish
├── media resolve
├── provider call
├── postprocess
└── export
```

Deferred:

- Editable Execution Graph.
- Workflow Seed.
- Workflow marketplace.
- Public reusable workflows.

This preserves trust and debuggability without turning Atelier v1 into a
general-purpose ComfyUI/RHTV-style workflow IDE.

## 12. Repo and Extraction Contract

Atelier v1 should start in the existing monorepo to move quickly while keeping
future product extraction possible.

Current-compatible layout:

```text
tron-comic/
├── frontend/             # LumenX Studio app
├── atelier/              # LumenX Atelier app
├── src/                  # LumenX Core backend
├── config/model_catalog/ # shared model truth
└── packages/             # optional shared TS packages
```

Potential future layout:

```text
tron-comic/
├── apps/studio/
├── apps/atelier/
├── services/core/
└── packages/
    ├── core-client/
    ├── shared-types/
    └── ui-tokens/
```

Extraction contract:

- Atelier is a product shell, not a Studio module.
- Studio and Atelier share Core through stable APIs.
- Cross-shell imports are forbidden.
- `atelier/` must not import from `frontend/`.
- `frontend/` must not import from `atelier/`.
- Core must not depend on either front-end shell.
- Shared TypeScript code must live in `packages/*`.
- Shared UI should be promoted deliberately, not copied by relative imports.
- App-specific commits should stay scoped by top-level directory where possible.
- API DTOs/client types should be generated or promoted to `packages/core-client`
  / `packages/shared-types` when both shells need them.
- Keep history splittable by top-level directory for future `git subtree split`
  or repo extraction.

Future extraction should be possible with commands conceptually similar to:

```bash
git subtree split --prefix=atelier
git subtree split --prefix=frontend
git subtree split --prefix=packages/core-client
```

This is a design constraint, not an immediate extraction requirement.

## 13. Phased Roadmap

### Phase 1: Studio R2V Stabilization

Goal: make Studio's pipeline fit the reference-video/storyboard-video paradigm.

Scope:

- Treat R2V and storyboard-video workflows as first-class.
- Keep old I2V projects compatible.
- Strengthen model catalog / provider metadata consumption.
- Improve generation failure diagnostics.
- Make reference video selection, generation, and reuse reliable.
- Keep Studio focused on guided production and export throughput.

Current checkpoint:

- Studio R2V now routes and validates inputs by concrete model capability, not
  by the broad `r2v` mode alone.
- HappyHorse R2V is treated as image-reference generation.
- Wan 2.7 / Wan 2.6 R2V are treated as video-reference generation.
- Storyboard R2V tasks now sync back into project state so Assembly can consume
  completed takes without requiring a reload.
- Video Queue surfaces R2V mode/model/reference metadata for debugging.

### Phase 2: Core Decoupling

Goal: prepare Core for multiple product shells without over-designing for
Atelier before its UX is validated.

Scope:

- Separate Core APIs from Studio page assumptions.
- Define generation job primitives for T2I/I2I/T2V/I2V/R2V/V2V/audio/SFX.
- Normalize media refs and lineage.
- Prepare cost/time estimate APIs.
- Prepare typed status/state transitions.
- Keep model catalog the source of truth.

Current checkpoint:

- Added `AtelierProject`, `AtelierNode`, and `AtelierAgentPolicy` as a minimal
  Atelier-domain layer over shared Core references.
- Atelier nodes can point at shared Studio/Core artifacts through
  `source_project_id`, `frame_id`, `asset_id`, `video_task_id`, and
  `media_urls`.
- Added `/atelier/projects` and `/atelier/projects/{id}/nodes` CRUD endpoints
  without embedding canvas state inside Studio `Script`.
- Added Codex-style approval modes:
  `untrusted`, `on_failure`, `on_request`, `never`.
- Added frontend API client types and methods for the future Atelier shell.
- Persistence lives in `output/atelier_projects.json` for now, keeping the
  monorepo path fast while making later product split straightforward.

### Phase 3: Atelier MVP

Goal: validate agent-operated creative graph and independent export loop.

Scope:

- `atelier/` app shell.
- Creative Graph Canvas.
- Agent Panel.
- PlanNode + DraftGenerationNode + TakeNode + ReferenceNode.
- Built-in Explore 3 Directions skill.
- Typed canvas/generation tools.
- Agent Harness with persisted runs.
- Balanced permission mode.
- Capsule Inspector.
- Read-only Execution Trace.
- Sequence Strip.
- MP4 export.
- Private read-only share link.

Suggested implementation slices:

```text
3A. Domain contracts
    Canvas, Node, Edge, AgentRun, Approval, Sequence, ShareLink DTOs.

3B. Tool harness
    Typed tools, permission checks, audit log, rollback for agent drafts.

3C. Canvas shell
    Atelier app, graph rendering, node inspector, Agent Panel shell.

3D. Generation loop
    PlanNode -> draft generation nodes -> approval -> job submit -> TakeNode.

3E. Sequence/export
    Sequence Strip, clip ordering, audio/SFX placement, MP4 export.

3F. Private sharing
    Read-only share token, expiry/revocation, media access checks.
```

### Phase 4: Atelier Expansion

Goal: deepen creative continuity and power-user control after v1 loop works.

Candidates:

- More built-in Skills.
- Better Memory and preference extraction.
- More model-specific controls.
- Editable Execution Graph.
- Workflow Seed.
- Workflow templates.
- Advanced V2V controls.
- More audio/sequence tooling.

### Phase 5: Studio / Atelier Bridge Decision

Goal: decide based on observed usage, not assumption.

Possible bridges:

- Studio shot -> Atelier exploration brief.
- Atelier approved take -> Studio shot slot.
- Shared asset library references.
- Shared style/canon package.

Decision should wait until both Studio R2V and Atelier v1 are stable enough to
show whether users actually need cross-product flow.

## 14. Explicit Non-Goals

Not in Atelier v1:

- Editable Execution Graph.
- Public workflow marketplace.
- Public remix community.
- Real-time collaboration.
- Full NLE editing.
- Multi-user approvals.
- Whole Studio project import/export.
- Studio handoff.
- User-authored arbitrary Skills.
- Cross-app component imports.

Not in Studio near-term:

- Rebuilding Studio as infinite canvas.
- Adopting Atelier canvas state as Studio project state.
- Adding open-ended graph exploration that harms pipeline clarity.

## 15. Verification Strategy

Execution should include tests at the boundary where mistakes are expensive:

```text
Backend / API
- Canvas node and edge CRUD.
- Allowed BaseNode status transitions per node type.
- Agent permission enforcement by risk class.
- AgentRun persistence, replay metadata, cancel, rollback.
- Approval token required before costly/destructive tool classes.
- Generation job creation from approved draft nodes.
- Take lineage: derived_from / edited_from / generates.
- Sequence export input validation.
- Private share token authorization, expiry, and revocation.

Frontend
- Canvas renders node types and status badges.
- Agent Panel links messages/tool calls to nodes.
- Draft review card shows intent, refs, model, cost/time, approval.
- Capsule Inspector shows params and read-only Execution Trace.
- Sequence Strip accepts only valid media/take/gap items.
- Permission mode UI changes approval behavior.

Integration / E2E
- Text Seed -> Agent Plan -> 3 draft nodes -> approve one -> job -> TakeNode.
- Video Seed -> Video Edit draft -> approval -> edited TakeNode.
- Multiple approved takes -> Sequence -> MP4 export.
- Private read-only share opens without edit controls.
```

If generated media calls are expensive or credential-gated, use deterministic
mock providers for CI and keep provider-backed smoke tests manual or explicitly
opt-in.

## 16. Risk Register

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| Core becomes Atelier-specific | Breaks Studio and future extraction | Keep shared primitives separate from Atelier-domain APIs |
| Atelier becomes a generic workflow IDE too early | Competes head-on with ComfyUI/RHTV before creative loop is proven | V1 uses Capsule Inspector + read-only Execution Trace |
| Agent burns cost without user intent | Video jobs are expensive and queue-polluting | Default Balanced permission; costly actions require approval |
| Canvas becomes unstructured chaos | Infinite workspaces lose narrative/sequence closure | PlanNode, typed edges, status, Sequence Strip |
| Memory corrupts canon or taste | Silent preference/canon changes break trust | Account/canon writes require confirmation |
| Private share leaks media | Generated assets may be sensitive or licensed | Tokenized read-only links, expiry/revocation, media access checks |
| Monorepo becomes inseparable | Future product split gets expensive | Top-level app boundary, no cross-shell imports, shared packages only |

## 17. Decision Summary

- Product family: **LumenX Core + LumenX Studio + LumenX Atelier**.
- Studio direction: R2V-first production pipeline for workshop/team use.
- Atelier direction: independent agent-operated creative graph for personal AI
  video creation.
- Core direction: shared backend/runtime/API capability, not a UI module.
- Atelier entry point: Seed.
- Atelier core loop: Seed -> Plan -> Draft Nodes -> Generation -> Takes ->
  Judgment -> Branches -> Sequence -> Export.
- Agent default: create three draft directions.
- Permission default: Balanced.
- Memory: scoped and confirmation-gated for account/canon changes.
- Sequence: lightweight film assembly, enough for MP4 export.
- Repo strategy: monorepo now, extractable later.
