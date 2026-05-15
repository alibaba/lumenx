# Atelier Frontend Redesign PRD

Status: Draft for frontend redesign
Date: 2026-05-15
Audience: Claude / design agent / frontend implementer
Owner context: LumenX Atelier, not LumenX Studio

## 1. Purpose

This document describes the product intent, user stories, interaction model, UI expectations, and acceptance criteria for the Atelier frontend redesign.

Atelier is the infinite-canvas creative shell of the LumenX product family. It is built for individual creators who want to explore ideas, references, prompts, generated images, generated videos, and rough sequencing in one spatial workspace. It shares the same backend/Core capabilities with LumenX Studio, but it should not look or behave like Studio's linear production pipeline.

The immediate redesign goal is not to create a generic node workflow editor. The goal is to make the first Atelier experience feel like a creator's exploratory video board:

1. Create a video node on the canvas.
2. Choose a model and edit its model-specific parameters.
3. Upload or attach reference images/videos where the selected model supports them.
4. Generate multiple candidate videos.
5. Preview candidates and select the best one as the node result.
6. Keep non-selected candidates available for comparison or delete them.
7. Continue branching, referencing earlier nodes, or ask the built-in Agent to create/edit nodes.
8. Place selected videos into a lightweight sequence strip to form a rough cut without entering Studio.

## 2. Existing References

These documents already exist in the repository and should be treated as background material:

- `docs/plans/2026-05-08-atelier-v1-implementation-boundary.md`
  - Defines Atelier v1 as a separate graph-first AI video creation shell over shared LumenX Core.
  - Establishes that Atelier is not a Studio tab, not a generic whiteboard, and not a raw ComfyUI clone.
- `docs/plans/2026-05-08-lumenx-studio-atelier-core-roadmap.md`
  - Defines the broader Studio + Atelier + shared Core direction.
  - Explains why Studio remains the workflow/pipeline shell and Atelier becomes the exploratory canvas shell.
- `docs/plans/2026-05-09-atelier-agent-runtime-implementation-plan.md`
  - Defines the Agent runtime direction, planning package, tool calls, policy/permission model, and Agent Panel.
- `docs/agents/raw/无限画布深度研究_豆包.md`
  - Raw infinite-canvas product research.
- `docs/agents/raw/无限画布调研_Mogu.md`
  - Raw infinite-canvas implementation/product research.

This PRD is the Claude-ready frontend design handoff. It consolidates the product need and interaction expectations so a design/front-end agent can redesign Atelier without reading every prior planning artifact.

## 3. Product Positioning

### 3.1 What Atelier Is

Atelier is an infinite-canvas creative workspace for AI video exploration.

It should feel like:

- A visual thinking space for video creators.
- A generation board where prompts, references, outputs, and branches stay visible.
- A lightweight editing surface where selected clips can become a rough sequence.
- A place where an Agent can act on the canvas through visible, reviewable operations.

### 3.2 What Atelier Is Not

Atelier should not become:

- A linear Studio workflow with a different layout.
- A generic low-level node editor where users must understand execution graphs first.
- A ComfyUI-like technical graph where every primitive operation is exposed as a node.
- A marketing landing page.
- A static gallery.
- A professional NLE replacement in v1.

The product can support lower-level workflow nodes later, but the default v1 experience should be business-semantic: idea, reference, image, video, candidate, selected take, sequence. The user should feel they are making video, not wiring infrastructure.

## 4. Relationship To Studio And Core

LumenX now has a shared-core, dual-shell direction:

- LumenX Studio: linear production workflow for workshops and teams.
- LumenX Atelier: exploratory infinite-canvas workflow for individual creators.
- Shared Core: model catalog, provider registry, media handling, asset CRUD, generation pipelines, task queue, state management.

Frontend redesign must preserve this separation:

- Atelier may use shared Core APIs and generated catalog metadata.
- Atelier should not depend on Studio-specific page structure, storyboard assumptions, or linear production state.
- Atelier should be able to evolve into a separately branded product later.
- The UI should avoid copy and structure that implies the user must enter Studio to finish a simple rough cut.

## 5. Target Users

### 5.1 Primary User: Individual AI Video Creator

This user has an idea, reference images, maybe a script fragment, and wants to quickly explore visual directions. They may not have a full story yet.

Needs:

- Start from a vague idea.
- Try several video models and prompts.
- Upload references quickly.
- Compare multiple generated videos.
- Branch from a good result.
- Keep the creative context visible.
- Avoid being forced into a rigid pipeline too early.

### 5.2 Secondary User: Advanced Workflow User

This user understands execution graphs or model workflows and may want lower-level control later.

Needs:

- Inspect model parameters.
- Understand what inputs each model supports.
- Reuse node outputs as inputs.
- Build more precise chains.
- Avoid losing flexibility behind business-semantics.

Design implication:

Atelier should expose semantic nodes first, but leave room for advanced node controls. A future "advanced graph" layer can coexist with the semantic canvas. The default v1 should not expose raw execution graph complexity as the first interaction.

### 5.3 Tertiary User: Agent-First Creator

This user talks to the built-in Agent, asks it to create nodes, and reviews the result.

Needs:

- One visible Agent Panel to review the conversation and actions.
- Preview proposed node/tool operations before execution.
- Approve, reject, or adjust generated plans.
- Set permission mode for how much the Agent can do automatically.
- See what changed on the canvas after Agent actions.

## 6. Core Jobs To Be Done

1. When I only have a vague idea, I want to describe it to the Agent so it can create a few candidate prompt/video nodes for me to review.
2. When I have a reference image, I want to upload it and use it as input for a video node without leaving the canvas.
3. When I choose a model, I want the UI to show only the input types and parameters that model actually supports.
4. When I generate videos, I want multiple candidates side by side so I can compare and choose the best one.
5. When a candidate is good, I want to promote it to the node's selected result while keeping or deleting the other candidates.
6. When a result inspires a new direction, I want to branch from that result into another node.
7. When I want a rough cut, I want to place selected videos into sequence order directly inside Atelier.
8. When generation fails, I want to understand what failed and retry without losing node context.
9. When the Agent wants to act, I want the plan and tool calls to be understandable before they change my canvas.

## 7. Product Principles

### 7.1 Canvas First

The canvas is the product. It must be the default surface, not a secondary preview beside forms. Controls should support the canvas, not dominate it.

### 7.2 Content Is The Visual Hero

Generated images, videos, prompt text, and references are more important than decorative UI. The interface should be quiet until the user needs action.

### 7.3 Semantic Nodes First

The user should see nodes such as "Idea", "Reference Image", "Video Generation", "Selected Take", and "Sequence", not low-level execution primitives by default.

### 7.4 Model-Aware Inputs

R2V is a mode, but actual model support differs:

- Some models may support reference images only.
- Some models may support reference videos.
- Some may support both.
- Some may support text-to-video only.
- Parameters can vary by model.

The UI must validate against model capabilities, not only generation mode labels.

### 7.5 Reviewable Agent Actions

Agent actions must be visible, auditable, and permission-bound. Raw tool JSON should not be the primary UI, but users should understand what the Agent intends to do.

### 7.6 Branching Without Losing The Main Thread

Exploration is core. Users should be able to create alternate branches around a scene or concept while still seeing which result is selected for the current rough cut.

### 7.7 Progressive Disclosure

Basic creation must be quick. Advanced settings, raw prompts, model parameters, and execution details should be accessible but not always expanded.

## 8. Current Implementation Snapshot

As of 2026-05-15, Atelier has these relevant implementation surfaces:

- Page shell: `frontend/src/components/atelier/AtelierShell.tsx`
- State store: `frontend/src/store/atelierStore.ts`
- API client: `frontend/src/lib/api.ts`
- Agent planning view model: `frontend/src/lib/atelierAgentPlanning.ts`
- Agent panel components under `frontend/src/components/atelier/`
- Tests under `frontend/src/__tests__/`

Current capabilities include:

- Atelier project bootstrap.
- Canvas-like full-screen workspace.
- Video node creation.
- Node dragging and absolute positioning.
- Reference image upload inside a video node.
- Candidate generation and candidate status display.
- Candidate preview, selection, deletion, and retry/regenerate actions.
- Lightweight sequence strip for selected video results.
- Agent Panel with planning readiness, conversation/history, approval controls, and policy mode.
- Agent permission modes inspired by Codex-like behavior.

Current UX gaps that the redesign should address:

- "New Video Node" needs to be visually unmistakable and operationally robust.
- Users need clear ways to add image/reference nodes and text/idea nodes, not only video nodes.
- Uploading a reference should feel like creating/attaching a canvas object, not a hidden form field.
- The toolbar needs clearer hierarchy and action labels/tooltips.
- Candidate comparison needs a stronger visual layout.
- The Agent Panel should feel like a unified conversation/action history, not a diagnostic side panel.
- Canvas controls such as pan, zoom, fit view, select, delete, and branch should be more explicit.
- Model selection must communicate capability constraints before generation.
- Empty state should invite creation without becoming a marketing hero.

## 9. Information Architecture

Atelier should use a focused five-zone layout.

### 9.1 Main Canvas

Purpose:

- Primary workspace for nodes, branches, references, generated media, and connections.

Expected behavior:

- Full-window, dark-first canvas.
- Supports pan and zoom.
- Supports node drag.
- Supports selecting nodes.
- Supports connecting reference/result outputs to generation inputs.
- Shows links/edges between related nodes.
- Avoids decorative clutter.

### 9.2 Top Or Floating Toolbar

Purpose:

- Creation and navigation controls.

Required controls:

- New Idea Node
- New Image Node / Upload Reference
- New Video Node
- Ask Agent
- Fit View
- Zoom Out
- Zoom In
- Undo
- Redo

Each icon button should have a tooltip. Text labels may be shown in the first iteration if icons alone are unclear, but controls should remain compact.

### 9.3 Node Layer

Purpose:

- Display semantic creative objects.

Node types in v1:

- Idea/Text node
- Image/Reference node
- Video generation node
- Candidate result inside a video node
- Selected result marker

Future node types:

- Audio node
- Sound effect node
- V2V edit node
- Sequence node
- Advanced workflow node

### 9.4 Agent Panel

Purpose:

- Unified Agent conversation, plan preview, tool approval, action history, and permission settings.

Expected behavior:

- Always available but collapsible.
- Familiar chat-like conversation structure.
- Shows proposed canvas operations in a readable summary.
- Shows approval controls when needed.
- Shows permission mode.
- Shows recent changes the Agent made to the canvas.
- Does not expose raw low-level tool payload as the main content.

### 9.5 Bottom Sequence Strip

Purpose:

- Allow users to arrange selected generated clips into a rough cut.

Expected behavior:

- Displays selected video results in order.
- Supports adding selected candidate to sequence.
- Supports reordering in a later iteration.
- Supports removing from sequence.
- Shows duration/thumbnail if available.
- Should not become a full editing timeline in v1.

## 10. Primary User Flows

### 10.1 Empty Canvas To First Video Node

1. User enters Atelier and sees an empty canvas with compact creation controls.
2. User clicks "New Video Node".
3. A video node appears near the current viewport center.
4. The new node is selected and ready for model/prompt/reference input.
5. If creation fails, an error appears near the toolbar and in the node area if applicable.

Acceptance:

- Button click must always produce either a visible node or a visible error.
- The action must not silently fail.
- The node must belong to the active Atelier project.
- The UI must not create duplicate default projects due to concurrent bootstrapping.

### 10.2 Add Reference Image

1. User clicks "New Image Node" or "Upload Reference".
2. User chooses an image file.
3. The image appears as a reference node on the canvas.
4. User can connect or attach it to a video node.
5. If the selected video model does not support image references, the UI explains the mismatch and suggests choosing a compatible model.

Alternative:

- User uploads directly inside a video node.
- The upload still creates a visible reference asset/card so the reference remains part of the canvas context.

Acceptance:

- Image upload should be discoverable from the toolbar and from video nodes.
- Uploaded media should remain visible as a reusable canvas object.
- Unsupported input types are blocked before submission.

### 10.3 Create Idea/Text Node

1. User clicks "New Idea Node".
2. A small text node appears.
3. User writes a premise, shot idea, prompt fragment, or visual note.
4. User can ask Agent to expand the idea into video nodes.
5. User can manually turn the idea into a video node.

Acceptance:

- Idea nodes are lightweight and fast.
- Idea nodes do not require model selection.
- They can become input context for Agent actions or prompt generation.

### 10.4 Configure Video Node

1. User selects a video node.
2. User chooses a model.
3. UI shows model-specific capabilities and required/optional inputs.
4. User edits prompt and supported parameters.
5. User attaches reference images/videos if supported.
6. User chooses candidate count.
7. User clicks "Generate".

Acceptance:

- Required fields are clear.
- Unsupported references are not accepted for the chosen model.
- Parameters should be grouped and collapsed by default when advanced.
- Model capability mismatch should be explained in user language.

### 10.5 Generate And Compare Candidates

1. User clicks "Generate".
2. Node enters processing state.
3. Candidate slots show pending/processing/completed/failed states.
4. Completed candidates show preview thumbnail/video.
5. User previews candidates.
6. User selects the best candidate as the node result.
7. Non-selected candidates remain as alternates unless deleted.

Acceptance:

- Multiple outputs are first-class, not hidden in logs.
- Selected candidate is visually obvious.
- Failed candidates show retry action.
- Deleting one candidate does not destroy the selected result unless it is the selected result and the user confirms.

### 10.6 Branch From A Result

1. User sees a good candidate or selected result.
2. User clicks "Branch" or drags from the result output handle.
3. A new video node appears connected to the source.
4. The new node inherits relevant context according to model support.
5. User edits prompt/reference and generates more candidates.

Acceptance:

- Branching is obvious and spatial.
- The source relationship is visible.
- The UI distinguishes "selected for sequence" from "used as branch reference".

### 10.7 Agent-Assisted Node Creation

1. User opens Agent Panel or clicks "Ask Agent".
2. User describes an idea, such as: "Create three options for a cyberpunk alley chase, one realistic, one anime, one handheld documentary."
3. Agent proposes a plan with canvas operations:
   - Create idea node.
   - Create three video nodes.
   - Fill prompts.
   - Select suggested models.
   - Attach references if provided.
4. Depending on permission mode, the Agent either asks for approval or executes allowed operations.
5. Created nodes appear on the canvas with a clear visual change.
6. Conversation and tool action summaries remain in Agent Panel history.

Acceptance:

- Agent-generated changes are visible on the canvas.
- The user can review before generation when policy requires.
- Tool calls are summarized as human-readable operations.
- The Agent cannot silently execute high-risk actions outside the configured permission mode.

### 10.8 Build Rough Cut In Atelier

1. User selects a candidate as node result.
2. User clicks "Add to Sequence".
3. Result appears in bottom Sequence Strip.
4. User repeats for multiple nodes.
5. User previews the rough order.

Acceptance:

- The flow can close inside Atelier for simple creator needs.
- Studio is not required for basic rough-cut assembly.
- Advanced export and professional editing can remain future scope.

## 11. Node Specifications

### 11.1 Idea/Text Node

Purpose:

- Capture an initial idea, prompt fragment, scene note, story beat, or instruction to the Agent.

Content:

- Title or short label.
- Body text.
- Optional tags.
- Optional "Ask Agent to expand" action.
- Optional "Turn into Video Node" action.

Primary actions:

- Edit text
- Duplicate
- Expand with Agent
- Convert to Video Node
- Delete

Visual treatment:

- Compact note-like card.
- Should not resemble a database form.
- Text should wrap cleanly and never overflow.

### 11.2 Image/Reference Node

Purpose:

- Hold uploaded or generated images that can be used as references.

Content:

- Image preview.
- Filename or generated label.
- Source metadata if available.
- Usage chips showing connected video nodes.

Primary actions:

- Upload/replace image
- Preview full image
- Connect to video node
- Use as first frame/reference
- Delete

Visual treatment:

- Media-first.
- Minimal chrome.
- Clear output handle for connections.

### 11.3 Video Generation Node

Purpose:

- Configure and generate one creative video attempt with multiple candidate outputs.

Content:

- Node title.
- Model selector.
- Capability indicator.
- Prompt field.
- Reference inputs.
- Model parameter summary.
- Candidate count.
- Generate action.
- Candidate grid/list.
- Selected result.

Primary actions:

- Choose model
- Edit prompt
- Attach reference
- Configure parameters
- Generate
- Regenerate all
- Retry failed candidate
- Preview candidate
- Select candidate
- Delete candidate
- Branch from candidate
- Add selected result to sequence
- Duplicate node
- Delete node

Visual treatment:

- Larger than idea/image nodes, but still compact.
- Media output area should dominate once candidates exist.
- Advanced settings should be collapsible.
- Status should be visible at node level and candidate level.

### 11.4 Candidate Result

Purpose:

- Represent one generated output from a video node.

Content:

- Thumbnail or video preview.
- Status.
- Duration if available.
- Model/job metadata in collapsed details.
- Error message if failed.

Primary actions:

- Preview
- Select as result
- Retry
- Delete
- Branch

Visual treatment:

- Candidate cards should be visually comparable.
- Selected candidate should have a strong but not flashy selected state.
- Failed candidate should be clear but not visually dominant.

## 12. Canvas Interaction Details

### 12.1 Selection

- Clicking a node selects it.
- Selected node has a visible outline/glow.
- Clicking empty canvas clears selection.
- Shift-click or multi-select can be future scope.

### 12.2 Dragging

- Dragging a node moves it.
- Buttons inside nodes must not accidentally trigger dragging.
- Interactive controls must stop propagation where needed.
- Drag handles can be the node header or the full card except controls.

### 12.3 Pan And Zoom

Required for redesign:

- Mouse wheel / trackpad pan behavior should feel natural.
- Zoom controls should be available as buttons.
- Fit View should center all nodes.
- Current zoom level can be shown subtly.

### 12.4 Connections

V1 can keep connections simple:

- Image node to video node reference input.
- Video result to new video node reference input.
- Idea node to Agent-created video nodes as contextual relationship.

Connections should be readable, but they do not need a full low-level port system in v1.

### 12.5 Keyboard Shortcuts

Useful but not required for initial redesign:

- Delete: delete selected node, with confirmation for nodes with outputs.
- Cmd/Ctrl+D: duplicate selected node.
- Cmd/Ctrl+Z: undo.
- Cmd/Ctrl+Shift+Z: redo.
- Space: pan mode.
- F: fit view.

Do not rely on shortcuts as the only way to perform an action.

## 13. Toolbar Button Requirements

### 13.1 New Idea Node

Icon suggestion:

- `StickyNote`, `Text`, or equivalent.

Function:

- Creates an editable idea/text node near viewport center.

States:

- Idle
- Creating
- Disabled during project bootstrap
- Error with retry

### 13.2 New Image Node / Upload Reference

Icon suggestion:

- `ImagePlus` or equivalent.

Function:

- Opens file picker and creates an image/reference node.

States:

- Idle
- Uploading
- Upload failed

### 13.3 New Video Node

Icon suggestion:

- `Film`, `Video`, or `Plus` + `Film`.

Function:

- Creates a video generation node near viewport center and selects it.

States:

- Idle
- Creating
- Disabled during project bootstrap
- Error with retry

Important:

- This button must always provide feedback.
- It must be above the canvas hit layer.
- It must not be swallowed by canvas pointer events.

### 13.4 Ask Agent

Icon suggestion:

- `Sparkles`, `Bot`, or equivalent.

Function:

- Opens/focuses Agent Panel and places cursor in Agent input.

States:

- Idle
- Agent running
- Waiting for approval
- Error

### 13.5 Fit View

Icon suggestion:

- `Maximize`, `Scan`, or equivalent.

Function:

- Centers and zooms the viewport to show all nodes.

### 13.6 Zoom Controls

Icon suggestion:

- `ZoomIn`, `ZoomOut`.

Function:

- Adjust canvas zoom.

### 13.7 Undo/Redo

Icon suggestion:

- `Undo2`, `Redo2`.

Function:

- Undo/redo canvas operations when supported.

If undo/redo is not implemented yet:

- Buttons may be hidden or disabled with tooltip "Coming later".
- Do not show active-looking controls that do nothing.

## 14. Agent Panel Requirements

The Agent Panel is a core part of Atelier, not an advanced debug drawer.

### 14.1 Structure

Recommended sections:

1. Header
   - Agent name/status.
   - Collapse/expand.
   - Permission mode.
2. Conversation
   - User messages.
   - Agent responses.
   - Compact action summaries.
3. Pending Plan
   - Human-readable proposed operations.
   - Approve / Reject / Edit prompt controls.
4. Tool Activity
   - Recent actions, summarized.
   - Status: planned, running, completed, failed.
5. Input Composer
   - Text input.
   - Send button.
   - Optional attach context/current selection.

### 14.2 Permission Modes

Inspired by Codex-like permission settings:

- Untrusted: always ask before taking action.
- On failure: ask only when an operation fails or needs escalation.
- On request: ask when escalation is requested.
- Never: run without asking for approval.

Recommended UI:

- A compact menu or segmented selector in the Agent Panel header.
- Plain language descriptions.
- Current selection clearly marked.

### 14.3 Tool Call Presentation

Do not show raw JSON as the default. Present operations like:

- Create 3 video nodes.
- Set model to Wan2.7 for Node A.
- Attach image "alley_reference.png" to Node B.
- Generate 4 candidates for Node C.

Advanced/raw details can be collapsible for debugging.

### 14.4 Approval UX

When approval is required:

- Show the pending operations.
- Provide Approve, Reject, and Edit Request.
- If only part of the plan is risky, identify the risky operation.
- The canvas should preview or highlight affected nodes when possible.

## 15. Model And Parameter UX

### 15.1 Model Selector

Model selector must be generation-task aware.

For each model, display:

- Model name.
- Provider.
- Supported input types.
- Supported output type.
- Important limitations.
- Whether it supports reference image.
- Whether it supports reference video.
- Whether it supports first-frame image.
- Whether candidate count is supported.

### 15.2 Capability Mismatch

Examples:

- User attaches a reference video but selected model supports only image reference.
- User selects an R2V mode but the model implementation accepts only image references.
- User wants first-frame behavior but the model does not support it.

Expected UI:

- Block invalid generation before API submission.
- Explain what is wrong.
- Suggest a compatible model or remove unsupported input.

### 15.3 Parameters

Parameters should be grouped:

- Basic: prompt, duration, aspect ratio, candidate count.
- Reference: image/video inputs.
- Advanced: seed, guidance, motion strength, style strength, provider-specific fields.

Do not overwhelm the initial node view. Collapsed advanced settings are acceptable.

## 16. Visual Direction

The existing LumenX design language should be respected but made more canvas-native.

### 16.1 Overall Feel

- Dark-first.
- Immersive creative cockpit.
- Media-forward.
- Quiet glass surfaces.
- Neon accents used sparingly.
- Spacious canvas with compact controls.
- Professional creator tool, not enterprise admin.

### 16.2 Avoid

- Landing-page hero layout.
- Big marketing cards.
- Decorative gradient blobs/orbs.
- Dense admin tables.
- Excessive purple gradients.
- Raw ComfyUI visual language as the default.
- Tiny unlabelled controls without tooltips.
- Text overflowing in nodes or buttons.
- Node cards inside larger decorative cards.

### 16.3 Node Styling

Nodes should feel tactile and spatial:

- Stable dimensions.
- Clear header/action area.
- Media preview area with fixed aspect ratio.
- Compact metadata.
- Strong selected state.
- Clear processing/failed/completed states.
- No layout shift when candidates load.

### 16.4 Motion

Motion should communicate state:

- Node creation appears smoothly.
- Candidate loading transitions into completed preview.
- Agent action highlights affected nodes.
- Panel collapse/expand is smooth.

Avoid decorative motion that distracts from content.

## 17. State Model For UI

### 17.1 Project State

- Loading project.
- Empty project.
- Loaded with nodes.
- Project load failed.

### 17.2 Node State

- Draft: user can edit.
- Ready: required inputs are satisfied.
- Processing: generation in progress.
- Completed: at least one candidate completed.
- Failed: generation failed or all candidates failed.

### 17.3 Candidate State

- Pending.
- Processing.
- Completed.
- Failed.
- Selected.

Selected is a role/state overlay. A selected candidate can still have completed metadata.

### 17.4 Agent State

- Idle.
- Thinking/planning.
- Waiting for approval.
- Running tool operations.
- Completed.
- Failed.

### 17.5 Permission State

- Untrusted.
- On failure.
- On request.
- Never.

## 18. Error Handling

Errors must be visible and contextual.

Examples:

- Node create failed.
- Upload failed.
- Selected model does not support attached reference.
- Generation request rejected.
- Candidate job failed.
- Agent tool operation failed.

UI requirements:

- Show short human-readable error text.
- Provide retry when possible.
- Preserve user input.
- Do not clear prompts/references after failure.
- Do not silently swallow click actions.
- Avoid raw backend tracebacks in primary UI.

## 19. Performance Requirements

Atelier is canvas-heavy, so performance matters.

V1 practical requirements:

- Avoid autoplaying too many videos at once.
- Use thumbnails/posters where possible.
- Keep node re-renders scoped.
- Avoid layout shift when candidates load.
- Use stable node dimensions.
- Keep Agent Panel updates from re-rendering every canvas node unnecessarily.
- Pan/zoom should remain responsive with dozens of nodes.

Future:

- Viewport virtualization.
- Media thumbnail caching.
- Large graph minimap.
- Canvas engine abstraction if DOM-node approach reaches limits.

## 20. Accessibility And Usability

Minimum expectations:

- Click targets should be at least 36-40px where practical.
- Icon buttons need tooltips.
- Buttons need disabled/loading states.
- Text must wrap and stay inside containers.
- Focus states should be visible.
- Important actions should not rely only on color.
- Keyboard navigation should not be broken by canvas layers.
- File upload must have an accessible trigger.

## 21. Implementation Boundaries

### 21.1 Should Reuse

- Existing Atelier API client methods in `frontend/src/lib/api.ts`.
- Existing generated model catalog utilities.
- Existing shared Core concepts.
- Existing Agent policy/planning view model where possible.
- Existing Zustand store patterns, unless redesign requires local component state for UI-only behavior.

### 21.2 Should Avoid

- Coupling Atelier UI to Studio workflow pages.
- Parsing model catalog YAML directly in frontend.
- Introducing new dependencies without a clear need.
- Rebuilding backend APIs for visual-only redesign.
- Showing raw Agent tool payloads as primary UI.
- Making the user enter Studio for the primary v1 story.

### 21.3 Files Likely In Scope

- `frontend/src/components/atelier/AtelierShell.tsx`
- `frontend/src/components/atelier/*`
- `frontend/src/store/atelierStore.ts`
- `frontend/src/lib/atelierAgentPlanning.ts`
- `frontend/src/lib/api.ts` only if API surface needs small additions
- `frontend/src/__tests__/atelier-*.test.ts`

## 22. Acceptance Criteria For Frontend Redesign

### 22.1 Creation

- User can create a video node from the toolbar.
- User can create an image/reference node or upload a reference from a visible control.
- User can create an idea/text node.
- Creation actions show loading and error feedback.

### 22.2 Model Configuration

- User can choose a model inside a video node.
- UI displays model-specific supported inputs and key parameters.
- Unsupported input/model combinations are blocked before generation.

### 22.3 Generation

- User can generate multiple candidate videos for a node.
- Candidate status and preview are visible.
- User can select one candidate as node result.
- User can delete or retry non-selected candidates.

### 22.4 Canvas

- User can drag nodes.
- User can pan/zoom and fit view.
- Relationships between references/results and video nodes are visible.
- Controls inside nodes do not accidentally drag the node or fail due to canvas event capture.

### 22.5 Agent

- User can open a unified Agent Panel.
- User can send a request.
- Agent plans/tool actions are summarized in human-readable form.
- Permission mode is visible and changeable.
- Approval-required actions are clearly reviewable.

### 22.6 Sequence

- User can add selected video result to a bottom Sequence Strip.
- Sequence Strip can show the rough order of selected clips.
- Removing from sequence does not delete the source node result.

### 22.7 Verification

- Frontend typecheck passes.
- Relevant Atelier store/component tests pass.
- Manual browser test confirms:
  - New Video Node works.
  - Image/reference node/upload works.
  - Idea node works.
  - Generate flow shows candidates or a clear mocked/real API state.
  - Agent Panel opens and shows permission controls.

## 23. Non-Goals For This Redesign

Do not spend v1 redesign scope on:

- Full professional nonlinear video editing.
- Complete Studio/Atelier bidirectional bridge.
- Full generic execution graph editor.
- ComfyUI-level custom graph authoring.
- Multi-user collaboration.
- Marketplace/template ecosystem.
- Mobile-first authoring.
- Full minimap/virtualization if current scale does not require it.
- Replacing the backend generation pipeline.

## 24. Open Product Questions

These can remain unresolved during initial redesign:

1. Should Atelier become a separate product name/brand later?
2. Should Studio and Atelier bridge at shot level, or only share Core?
3. When should advanced low-level workflow nodes appear?
4. Should sequence export remain inside Atelier or hand off to Studio/export pipeline?
5. How much of Agent reasoning should be visible versus summarized?
6. Should model presets be creator-facing names rather than provider model names?

## 25. Suggested Redesign Phases

### Phase A: Make The Canvas Creation Loop Obvious

- Redesign toolbar.
- Add explicit Idea/Image/Video creation.
- Improve empty state.
- Improve node selected/loading/error states.
- Ensure New Video Node cannot silently fail.

### Phase B: Improve Video Node And Candidate Comparison

- Redesign model selector.
- Add model capability display.
- Improve prompt/reference/parameter layout.
- Improve candidate grid and selected result treatment.
- Add clearer branch/add-to-sequence actions.

### Phase C: Make Agent Panel Feel Native

- Redesign Agent Panel as chat + action history.
- Improve permission selector.
- Improve pending plan approval UI.
- Highlight canvas changes caused by Agent actions.

### Phase D: Canvas Navigation And Sequencing

- Add pan/zoom/fit controls.
- Improve connection visuals.
- Improve bottom Sequence Strip.
- Add basic rough-cut preview affordance if backend/output supports it.

## 26. Short Prompt For A Design Agent

Use this if another agent needs a compact instruction:

> Redesign LumenX Atelier as a dark-first infinite-canvas AI video creation workspace for individual creators. The core story is: create idea/image/video nodes, choose a model with model-specific parameters, attach supported references, generate multiple candidate videos, preview and select the best result, branch from results, and assemble selected clips in a lightweight sequence strip. Keep the canvas as the primary surface, use semantic creative nodes instead of raw ComfyUI-style execution nodes, and make the Agent Panel a familiar chat/action-history surface with reviewable tool operations and Codex-like permission modes. The UI should feel like an immersive creator cockpit: media-forward, compact controls, quiet glass surfaces, clear loading/error states, tooltips on icon buttons, no marketing hero, no decorative blobs, no dense admin tables. Preserve Studio/Atelier separation and shared Core assumptions.

