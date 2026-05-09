# Atelier V1 Implementation Boundary And Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build Atelier v1 as an independent graph-first AI video creation shell over LumenX Core, without coupling its canvas state or UI flow to Studio.

**Architecture:** Atelier owns its own frontend shell, canvas store, node renderer, Agent panel, permission UI, and sequence surface. It consumes shared Core APIs for model metadata, media references, video tasks, and future generation submission, while its own canvas state stays under Atelier-domain APIs and storage. Studio can link to shared artifacts later, but Studio project state must not become the parent of Atelier canvas state.

**Tech Stack:** Next.js 14, React 18, TypeScript, Zustand, Tailwind CSS, existing LumenX Core APIs, new Atelier-domain API client methods, and a DOM-node canvas engine based on React Flow / xyflow-style interaction patterns.

---

## 1. Final Implementation Boundary

### 1.1 Product Boundary

Atelier v1 is not a Studio tab and not a generic whiteboard.

It is a separate creation shell for:

- idea-to-node exploration;
- Agent-assisted node creation;
- media generation drafts;
- prompt/reference review;
- take selection;
- lightweight sequence assembly.

Studio remains pipeline-first:

- project;
- script;
- storyboard;
- assets;
- R2V generation;
- assembly;
- export.

Atelier may share Core capabilities with Studio, but v1 must not require a Studio project to exist.

### 1.2 Code Boundary

Use this file layout:

```text
frontend/src/app/page.tsx                         # temporary route switch only
frontend/src/components/atelier/                  # Atelier product shell
frontend/src/components/atelier/canvas/           # canvas viewport and nodes
frontend/src/components/atelier/agent/            # Agent panel and approval UI
frontend/src/components/atelier/inspector/        # selected node controls
frontend/src/components/atelier/sequence/         # sequence strip
frontend/src/store/atelierStore.ts                # Atelier-only frontend state
frontend/src/lib/api.ts                           # existing Atelier Core API client
src/apps/comic_gen/models.py                      # existing Atelier DTOs for now
src/apps/comic_gen/api.py                         # existing Atelier endpoints for now
src/apps/comic_gen/pipeline.py                    # existing persistence for now
tests/test_atelier_core.py                        # backend contract tests
frontend/src/__tests__/atelier-*.test.ts          # frontend unit tests
```

Later split target:

```text
src/apps/atelier/                                 # future backend extraction
frontend/src/app/atelier/                         # future dedicated route shell
packages/lumenx-core-client/                      # future shared API client
```

Do not import Studio module components into Atelier. Shared visual primitives should be copied into neutral common components only after they prove reusable.

### 1.3 Canvas Technology Boundary

Use a DOM-node canvas architecture for v1.

Primary reason:

- video nodes need native `<video>`, poster images, forms, menus, progress, prompt text, model controls, and approval cards;
- DOM nodes let Atelier maintain high frontend polish without fighting Canvas 2D text/input/video limitations;
- React Flow / xyflow-style transform, selection, edge, and node patterns match the product better than whiteboard-first or pure Canvas routes.

Do not start with:

- tldraw as the main product substrate;
- ComfyUI/litegraph-style raw Canvas 2D rendering;
- a full ECS/WebGL editor clone;
- editable low-level execution graph as the default UX.

Borrow later:

- viewport culling / spatial index ideas from ComfyUI-style implementations;
- chunking / LOD concepts from WebGL infinite-canvas research;
- custom shape ideas from tldraw only if Atelier needs freeform annotation after the graph loop works.

### 1.4 Data Boundary

Atelier v1 state is:

```text
AtelierProject
├── id
├── title
├── source_project_id?     # optional Studio seed only
├── agent_policy
└── nodes[]
```

AtelierNode may reference shared Core artifacts:

```text
source_project_id?
frame_id?
asset_id?
video_task_id?
media_urls[]
```

AtelierNode must not embed full Studio frame, asset, or project snapshots. Use IDs and media URLs.

### 1.5 Agent Boundary

Agent v1 can:

- create draft nodes;
- update draft prompts;
- attach references;
- suggest generation actions;
- explain a plan in the Agent panel;
- request approval for costly actions.

Agent v1 cannot:

- run arbitrary code;
- call unregistered tools;
- mutate Studio projects directly;
- silently submit expensive generation jobs under default permissions;
- bypass the canvas API/persistence layer.

Default approval mode should be `untrusted` until the UI makes the tradeoffs obvious.

---

## 2. V1 User Flow

### 2.1 First Screen

The first screen is the actual Atelier workspace:

```text
Left / center: infinite canvas
Right: Agent panel
Bottom: sequence strip
Floating: node inspector when a node is selected
Top-left: project switch / title / save status
Top-right: permission mode / model status / run controls
```

No marketing landing page.

### 2.2 Core Loop

```text
User enters seed idea
-> Agent proposes 3 draft nodes
-> User reviews nodes on canvas
-> User approves one or edits manually
-> Generation task is submitted
-> Completed take appears as a video node
-> User branches or adds to sequence
-> User exports rough cut
```

### 2.3 Required Node Types

V1 implements only these node types:

- `seed`: initial text/image/video/story seed;
- `plan`: Agent-created intent breakdown;
- `draft`: generation proposal awaiting review;
- `image`: image result or image reference;
- `video`: video result or video reference;
- `audio`: audio/music/SFX reference;
- `sequence`: lightweight assembly pointer.

Do not implement arbitrary node plugins in v1.

---

## 3. Implementation Tasks

### Task 1: Add Atelier Route Shell

**Files:**

- Modify: `frontend/src/app/page.tsx`
- Create: `frontend/src/components/atelier/AtelierShell.tsx`
- Create: `frontend/src/components/atelier/AtelierShell.test.tsx`

**Steps:**

1. Add a route/hash entry for Atelier, for example `#/atelier`.
2. Create `AtelierShell` with full-viewport layout:
   - canvas region;
   - right Agent panel placeholder;
   - bottom sequence strip placeholder;
   - top project/status bar.
3. Keep Studio navigation intact.
4. Test that `AtelierShell` renders the four regions.

**Verification:**

```bash
cd frontend
npm run test -- src/components/atelier/AtelierShell.test.tsx
npm run typecheck
```

**Acceptance:**

- Opening `#/atelier` shows Atelier shell without entering a Studio project.
- No Studio module component is imported into `components/atelier`.

### Task 2: Add Atelier Store

**Files:**

- Create: `frontend/src/store/atelierStore.ts`
- Create: `frontend/src/store/atelierStore.test.ts`

**Steps:**

1. Define frontend types matching Core DTOs:
   - `AtelierProject`;
   - `AtelierNode`;
   - `AtelierAgentPolicy`;
   - `AtelierSelectionState`;
   - `AtelierViewportState`.
2. Add actions:
   - `loadProjects`;
   - `loadProject`;
   - `createProject`;
   - `createNode`;
   - `updateNode`;
   - `selectNode`;
   - `setViewport`;
   - `updateAgentPolicy`.
3. Use existing `api.createAtelierProject`, `api.createAtelierNode`, and related methods.
4. Keep transient canvas UI state separate from backend-persisted node state.

**Verification:**

```bash
cd frontend
npm run test -- src/store/atelierStore.test.ts
npm run typecheck
```

**Acceptance:**

- Store can create/load an Atelier project without a Studio project.
- Store persists node changes through API methods, not direct local-only mutation.

### Task 3: Implement DOM Canvas Viewport

**Files:**

- Create: `frontend/src/components/atelier/canvas/AtelierCanvas.tsx`
- Create: `frontend/src/components/atelier/canvas/AtelierNodeRenderer.tsx`
- Create: `frontend/src/components/atelier/canvas/atelierCanvasMath.ts`
- Create: `frontend/src/components/atelier/canvas/atelierCanvasMath.test.ts`

**Steps:**

1. Implement viewport state:
   - pan `x/y`;
   - zoom;
   - selected node id.
2. Render nodes as absolutely positioned DOM cards inside a transformed world layer.
3. Add pointer drag for canvas pan.
4. Add wheel zoom centered around cursor.
5. Add node selection.
6. Keep edges deferred unless needed for draft node flow.

**Verification:**

```bash
cd frontend
npm run test -- src/components/atelier/canvas/atelierCanvasMath.test.ts
npm run typecheck
```

**Acceptance:**

- Panning and zooming are smooth enough for a first local demo.
- Nodes remain crisp DOM elements with normal text, image, video, and control rendering.
- No dependency on Studio project state.

### Task 4: Implement V1 Node Cards

**Files:**

- Create: `frontend/src/components/atelier/canvas/nodes/SeedNode.tsx`
- Create: `frontend/src/components/atelier/canvas/nodes/PlanNode.tsx`
- Create: `frontend/src/components/atelier/canvas/nodes/DraftNode.tsx`
- Create: `frontend/src/components/atelier/canvas/nodes/MediaNode.tsx`
- Create: `frontend/src/components/atelier/canvas/nodes/NodeChrome.tsx`
- Create: `frontend/src/components/atelier/canvas/nodes/nodeTypes.test.tsx`

**Steps:**

1. Create a shared `NodeChrome` wrapper for selection/status/model badges.
2. Render seed prompt and source media.
3. Render Agent plan bullet list.
4. Render draft prompt, model, references, and approval status.
5. Render image/video/audio media previews with lazy loading.
6. Keep controls compact and domain-specific; do not expose raw execution graph internals.

**Verification:**

```bash
cd frontend
npm run test -- src/components/atelier/canvas/nodes/nodeTypes.test.tsx
npm run typecheck
```

**Acceptance:**

- Video nodes use native `<video>` preview.
- Prompt text does not overflow cards.
- Status is visually scannable: draft, pending, processing, completed, failed.

### Task 5: Add Agent Panel V1

**Files:**

- Create: `frontend/src/components/atelier/agent/AgentPanel.tsx`
- Create: `frontend/src/components/atelier/agent/PermissionModeMenu.tsx`
- Create: `frontend/src/components/atelier/agent/AgentDraftReview.tsx`
- Create: `frontend/src/components/atelier/agent/AgentPanel.test.tsx`

**Steps:**

1. Render persistent conversation/history panel.
2. Add seed input.
3. Add local deterministic "Explore 3 Directions" mock action.
4. Create three `draft` nodes through the Atelier store.
5. Add permission mode control:
   - Untrusted;
   - On failure;
   - On request;
   - Never.
6. Persist policy through `api.updateAtelierAgentPolicy`.

**Verification:**

```bash
cd frontend
npm run test -- src/components/atelier/agent/AgentPanel.test.tsx
npm run typecheck
```

**Acceptance:**

- User can type an idea and get three draft nodes.
- Agent actions go through store/API methods.
- Permission mode is visible and persisted.

### Task 6: Add Inspector

**Files:**

- Create: `frontend/src/components/atelier/inspector/NodeInspector.tsx`
- Create: `frontend/src/components/atelier/inspector/NodeInspector.test.tsx`

**Steps:**

1. Show selected node details.
2. Allow editing title and prompt for `seed` / `draft` nodes.
3. Show read-only shared references:
   - `source_project_id`;
   - `frame_id`;
   - `asset_id`;
   - `video_task_id`;
   - `media_urls`.
4. Persist edits through `api.updateAtelierNode`.

**Verification:**

```bash
cd frontend
npm run test -- src/components/atelier/inspector/NodeInspector.test.tsx
npm run typecheck
```

**Acceptance:**

- Inspector never mutates Studio resources directly.
- Node edits remain scoped to Atelier node state.

### Task 7: Add Sequence Strip V1

**Files:**

- Create: `frontend/src/components/atelier/sequence/SequenceStrip.tsx`
- Create: `frontend/src/components/atelier/sequence/SequenceStrip.test.tsx`

**Steps:**

1. Show a horizontal strip of selected completed video/audio nodes.
2. Allow adding a completed `video` node to sequence.
3. Keep sequence local to Atelier project for now, stored in node `data.sequence`.
4. Defer MP4 export until generation loop is wired.

**Verification:**

```bash
cd frontend
npm run test -- src/components/atelier/sequence/SequenceStrip.test.tsx
npm run typecheck
```

**Acceptance:**

- Completed media nodes can be arranged into a rough order.
- No Studio `VideoAssembly` component is imported.

### Task 8: Add Performance Guardrails

**Files:**

- Create: `frontend/src/components/atelier/canvas/visibleNodes.ts`
- Create: `frontend/src/components/atelier/canvas/visibleNodes.test.ts`
- Modify: `frontend/src/components/atelier/canvas/AtelierCanvas.tsx`

**Steps:**

1. Add viewport bounds calculation.
2. Render only nodes inside viewport plus overscan.
3. Lazy-load media previews for offscreen nodes.
4. Add a node-count debug stat in development only.

**Verification:**

```bash
cd frontend
npm run test -- src/components/atelier/canvas/visibleNodes.test.ts
npm run typecheck
```

**Acceptance:**

- 200 mock nodes remain interactive on local preview.
- Offscreen videos are not mounted.

### Task 9: Visual QA And Route Smoke

**Files:**

- Create: `frontend/src/__tests__/atelier-route.test.tsx`
- Optionally create: `frontend/src/components/atelier/__tests__/atelier-smoke.test.tsx`

**Steps:**

1. Test route render.
2. Test create-project happy path with mocked API.
3. Test Agent action creates draft nodes.
4. Run local browser preview.

**Verification:**

```bash
cd frontend
npm run test -- src/__tests__/atelier-route.test.tsx src/components/atelier/**/*.test.tsx
npm run typecheck
npx next lint --file src/components/atelier/AtelierShell.tsx
```

Manual preview:

```bash
npm run dev
```

Open:

```text
http://localhost:3008/#/atelier
```

**Acceptance:**

- Atelier opens independently from Studio.
- Canvas/Agent/Inspector/Sequence layout is visually coherent on desktop.
- No obvious text overflow or panel overlap.

---

## 4. Non-Goals For V1

Do not implement these yet:

- full Agent harness with real model tool-calling;
- arbitrary user-authored skills;
- editable execution graph;
- Studio project import/export;
- cross-product shot bridge;
- multi-user collaboration;
- public sharing;
- full NLE editing;
- timeline-grade audio mixing;
- WebGL/ECS rewrite;
- tldraw whiteboard embedding;
- ComfyUI-style raw graph execution.

---

## 5. Commit Slices

Use small commits:

1. `Add independent Atelier shell route`
2. `Add Atelier store and API-backed project state`
3. `Add DOM canvas viewport and node renderer`
4. `Add Atelier node cards and inspector`
5. `Add Agent panel draft node flow`
6. `Add sequence strip and viewport culling`
7. `Add Atelier route smoke tests`

Each commit should include focused tests.

---

## 6. Open Decisions Before Real Generation

These should be decided after the shell is clickable:

1. Whether the first real generation node submits T2I or R2V.
2. Whether Agent draft nodes should call the backend immediately or stay local until approval.
3. Whether sequence state gets its own backend DTO or remains node `data` for v1.
4. Whether to add `@xyflow/react` as dependency or keep a minimal local DOM transform engine for the first proof.

Default recommendation:

- Start with a minimal local DOM transform engine for one iteration.
- If custom edge handling, selection, minimap, keyboard support, or nested graph behavior grows quickly, switch to `@xyflow/react` before building more UI on top.
