# Atelier Canvas Competitive Research: LibTV And RunningHub RHTV

Date: 2026-05-19
Scope: Atelier infinite-canvas UX, function model, UI detail, and implementation implications
Commercial scope: excluded
Research target: core canvas experience, not landing pages
Primary URLs:

- LibTV: https://www.liblib.tv/
- LibTV authenticated canvas observed in Arc: `https://www.liblib.tv/canvas?projectId=e24b1278ab524420b0a817394b5c897c`
- RunningHub RHTV authenticated canvas observed in Arc: https://rhtv.runninghub.cn/projects/canvas/2056256964232486914

## 0. Correction Note

The earlier draft of this report over-weighted public pages and unauthenticated shells. That was not enough for the decision at hand. This version supersedes it and is based on the authenticated canvas views exposed in Arc, plus limited public-page context.

The useful comparison is not "who has an infinite canvas". The useful comparison is:

1. How does the canvas make creation legible?
2. How does a node become a complete generation workplace?
3. How are references, assets, model settings, generated variants, history, agent operations, and final editing connected?
4. Which interaction grammar should Atelier adopt, and which should it avoid?

## 1. Executive Conclusion

Atelier should not become a generic ComfyUI-style workflow surface. LibTV and RHTV both prove that the winning pattern for video creators is a **semantic creation canvas**:

- visual media nodes first;
- node-to-node references visible as lines;
- model settings and generation controls close to the media node;
- asset/history/workflow/template panels one click away;
- lightweight editing actions embedded on video nodes;
- agent or skill entry connected to selected nodes;
- quick-cut/composition available inside the same product loop.

The strongest product direction for Atelier v3+ is:

> Atelier is an infinite canvas where every media node can become a self-contained creative work cell, and where agents, presets, references, and quick-cut production all operate directly on visible canvas objects.

This means Atelier should keep the current open-source/shared-core direction, but the frontend should evolve from "React Flow graph with panels" into a **creator cockpit**:

- left rail for creation modes;
- compact node cards by default;
- expanded node workbench on selection;
- inline reference chips in prompts;
- attached references shown both as chips and canvas edges;
- workflow/preset library as insertable open-source templates;
- node-level operations for HD/upscale, frame capture, parse, audio split, clip, download, fullscreen preview, and add-to-agent;
- quick-cut lane for closing a short film without entering Studio.

## 2. Method And Confidence

High-confidence observations:

- LibTV authenticated canvas was opened in Arc and inspected visually plus through accessibility labels.
- RHTV authenticated project canvas was opened in Arc at the user-provided URL and inspected visually plus through accessibility labels.
- RHTV selected-video-node state exposed its internal controls, model config, reference slots, prompt field, and generation button.
- LibTV node creation, toolbox, material library, and viewport controls were opened or inspected.

Medium-confidence observations:

- LibTV `LibTV Skills` is likely the skill/agent layer, but I did not execute it.
- RHTV `导演台` and `剪辑` labels clearly exist, and video node actions show clip/agent/editing affordances, but I did not execute destructive or credit-consuming actions.

Out of scope:

- No generation was triggered.
- No private account data was extracted.
- No commercial pricing strategy is evaluated.

## 3. LibTV Canvas Findings

### 3.1 Overall Shape

LibTV's real canvas is a dark infinite workspace with many media nodes arranged into large visual regions. It is not just a blank graph editor. It supports:

- image/video node clusters;
- many curved reference edges;
- grouped work areas resembling production boards;
- creator-friendly media thumbnails as the main visual object;
- a quiet dark UI with minimal chrome;
- top-level project title editing;
- save/share/credit/profile controls;
- bottom-left minimap, zoom, grid, and auto-arrange controls.

The immediate lesson: LibTV makes the artifact visible first. The user sees images, videos, branches, and board-like regions before seeing configuration.

### 3.2 Top Bar

Observed controls:

- editable project title: `【AI短片】《格子》（Grid） - 副本`;
- `LibTV Skills`;
- `创建access key`;
- `保存画布`;
- share;
- notification;
- credits;
- membership entry;
- profile/avatar.

Atelier implication:

- Keep project identity visible, but do not crowd the creative area.
- `Agent` or `Skills` should be top-level discoverable, not hidden in a settings drawer.
- Save state and share/export should be explicit.

### 3.3 Left Rail

Observed persistent left rail:

- `添加节点`;
- `打开工具箱`;
- `我的素材`;
- `历史记录`;
- `教程`;
- `联系客服`.

The left rail is simple and stable. It does not expose every possible operation. It exposes the creator's recurring mental modes:

- add;
- use tools/templates;
- reuse assets;
- inspect history;
- learn/get help.

Atelier implication:

Atelier should use a fixed left rail with a small number of modes:

- Add;
- Assets;
- Workflows or Presets;
- History;
- Agent;
- Sequence or Quick Cut;
- Help.

Avoid making the left rail a raw node-type list. Node types belong inside the Add panel.

### 3.4 Add Node Panel

LibTV's Add Node panel is semantically organized.

Observed node types:

- `文本`: scripts, ads, brand copy;
- `图片`: posters, storyboards, character design;
- `视频`: creative ads, animation, film;
- `视频合成 Beta`: combine multiple video clips;
- `音频`: sound effects, voiceover, music;
- `脚本 Beta`: creative scripts, storyboards.

Observed resource entries:

- `上传`: upload image, video, audio;
- `从图库选择`: choose from historical generations.

Atelier implication:

Atelier should not lead with low-level "ImageNode / VideoNode / TextNode" labels. Use creator-facing labels:

- Text;
- Image;
- Video;
- Audio;
- Script or Storyboard;
- Video Compose;
- Upload;
- From Library.

Internally those can still map to typed node schemas.

### 3.5 Toolbox / Preset Layer

LibTV's toolbox is not an afterthought. It is a production shortcut layer.

Observed preset examples:

- left/right arc slide;
- ecommerce phone pop-up effect;
- coffee cup entrance;
- 360 rotation display;
- mechanical arm perspective;
- Live 2D;
- pupil zoom-in;
- bird disintegration;
- breaking out of a box;
- product dramatic entrance;
- inverted space;
- anti-gravity float;
- particle dissolve;
- travel transitions: zoom in, zoom out, rotate left/right, growth;
- hero perspective;
- AI model clothing dynamic display;
- master storyboard nine-grid, classic dark tone;
- AI interior decoration preview.

Atelier implication:

Atelier needs an open-source equivalent of this layer, but not as a commercial marketplace. Use a local/published preset registry:

- `workflow_templates`;
- `motion_presets`;
- `shot_templates`;
- `reference_recipes`;
- `sequence_templates`.

Each preset should instantiate a small group of nodes, references, prompts, and model defaults.

### 3.6 Material Library

Observed material categories:

- `全部`;
- `其它`;
- `人物`;
- `场景`;
- `物品`;
- `风格`;
- `音效`;
- `我的主体库`.

Atelier implication:

Use asset semantics that match creative production:

- Characters;
- Scenes;
- Props;
- Styles;
- Audio/SFX;
- Uploads;
- Generated takes;
- Favorites.

This should be shared with Core asset APIs so future Studio/Atelier separation remains clean.

### 3.7 Viewport Controls

Observed bottom-left controls:

- auto-arrange: `整理画布，Option+Shift+F`;
- minimap toggle;
- grid snap;
- zoom out;
- zoom option popup;
- zoom in.

Atelier implication:

These should be permanent canvas affordances, not hidden in menus:

- minimap;
- grid snap;
- reset view;
- fit selection or auto-arrange;
- zoom slider/value;
- keyboard shortcut hints.

## 4. RunningHub RHTV Canvas Findings

### 4.1 Overall Shape

The inspected RHTV project is titled:

- `赛博武侠风格的开放世界游戏GC`

The visible canvas uses a black dotted/grid workspace. The core layout is:

- source image/reference nodes on the left;
- output video/result nodes on the right;
- many long curved edges connecting references to outputs;
- a selected video node expands into a full generation workbench;
- bottom-left viewport controls;
- top-right account/credits/share controls;
- persistent left rail for major modes.

Unlike LibTV's board-like grouped composition, this RHTV project reads more like a production graph where multiple image references feed video outputs.

Atelier implication:

RHTV validates the exact Atelier user story: "upload reference images, connect them to a video generation node, configure model/parameters, generate multiple videos, preview/select/manage results."

### 4.2 Left Rail

Observed left rail:

- large `+` add button;
- `资产`;
- `工作流`;
- `历史`;
- `导演台`;
- `剪辑`.

This is a strong product taxonomy:

- Assets: what I have;
- Workflow: reusable recipes/templates;
- History: what happened;
- Director: intent-to-structure / orchestration;
- Clip: finish/edit the result.

Atelier implication:

Atelier should align to this grammar, but with open-source naming:

- Add;
- Assets;
- Workflows;
- History;
- Agent Director;
- Sequence or Quick Cut.

The important point is that "Agent" and "editing" are not external products. They are canvas modes.

### 4.3 Asset Nodes

Observed source nodes:

- uploaded images with filename titles such as `ChatGPT Image 2026年4月28日 ...`;
- each image node has a visible `替换` action;
- image nodes serve as references into video generation nodes;
- reference edges remain visible at canvas level.

Atelier implication:

Image/reference nodes need first-class replacement and version semantics:

- replace source;
- keep prior source in history;
- preserve downstream reference links;
- show whether downstream outputs are stale after replacement.

This matters more than building many exotic node types early.

### 4.4 Selected Video Node As Workbench

When a video node is selected, it exposes a dense but useful workbench inside the node. Observed controls:

- `高清` / HD upscale;
- `剪辑`;
- `捕捉帧`;
- `解析`;
- `音频分离`;
- download;
- fullscreen preview;
- `加入 Agent`;
- video preview with play, sound, fullscreen;
- reference mode tabs:
  - `全能参考`;
  - `图片参考`;
- `素材库`;
- reference slots:
  - `图片1`;
  - `图片2`;
  - `图片3`;
  - each removable;
- prompt editor with inline reference chips such as `@图片1`, `@图片2`, `@图片3`;
- model selector:
  - `Seedance2.0`;
- parameter summary:
  - `4k / 4s / 是 / 16:9`;
- generation count:
  - `1x`;
- cost estimate:
  - `7.32`;
- `开始生成` with Enter hint.

This is the most important RHTV observation.

Atelier should treat a selected video node as a **node-local production workbench**, not merely a card that pushes settings into a distant right inspector.

### 4.5 Inline Reference Chips

RHTV's prompt field uses inline references:

- `@图片3` for environment;
- `@图片1` for one character;
- `@图片2` for another character.

These chips correspond to visible reference slots and canvas edges.

Atelier implication:

Atelier needs typed reference tokens:

- `@image:<nodeId>`;
- `@video:<nodeId>`;
- `@audio:<nodeId>`;
- `@character:<assetId>`;
- `@scene:<assetId>`;

In the UI they should render as readable chips like `@图片1`, `@角色 Qingye`, or `@场景 Cyber Alley`. In the backend they should remain stable structured references, not plain prompt text.

This is how Atelier can preserve both:

- creator-friendly semantic prompts;
- model-specific structured input validation.

### 4.6 Node-Level Utility Actions

RHTV video nodes expose actions that happen after generation:

- HD/upscale;
- clip;
- frame capture;
- parse/analyze;
- audio separation;
- download;
- fullscreen preview;
- add to agent.

Atelier implication:

Atelier should add a compact post-generation action bar to media nodes. These actions are more useful than generic graph buttons:

- preview;
- choose result;
- upscale;
- extract frame;
- analyze;
- split audio;
- send to Agent;
- send to Sequence;
- download/delete.

### 4.7 Workflow / Inspiration Library

RHTV's workflow panel exposes:

- `灵感库`;
- `我的工作流`;
- category filters:
  - `全部`;
  - `行业定制`;
  - `数字虚拟`;
  - `文案策划`;
  - `平面设计`;
  - `视频内容`;
  - `电商视觉`;
- template/workflow examples:
  - `去看棒球比赛突然被镜头拍到了`;
  - `AI故事版生视频`;
  - `2D转3D渲染图`;
  - `产品功能宣传短片工作流`;
  - `口播带货短视频制作`;
  - `高颜值证件照｜DIY服饰发型背景`;
  - `AI-PPT 万能模板`;
  - `NARS模特产品展示`;
  - `自定义数字人・背景定制・智能对口型`;
  - `虚拟试穿演示视频`;
  - `产品开箱讲解视频`;
  - `好物种草短视频`;
  - `家居家装效果视频`;
  - `数字人播报天气预报`;
  - `探店文案生成`.

Atelier implication:

Atelier's open-source version should not imitate a marketplace, but it should include a curated template browser:

- Storyboard to video;
- Character reference video;
- Scene reference video;
- Product reveal;
- Dialogue / lip-sync;
- Motion study;
- Video compose;
- Frame extraction to continuation;
- Trailer / short-film sequence.

Each template should be a versioned JSON or YAML recipe that creates nodes and default model parameters.

### 4.8 Viewport Controls

Observed controls:

- open minimap;
- grid snap on/off;
- reset view;
- zoom slider;
- help;
- feedback.

Atelier implication:

This matches LibTV. These are now table stakes for infinite canvas products.

## 5. LibTV vs RHTV: What They Teach Differently

### 5.1 LibTV Is More Board-Like

LibTV feels like a visual production board:

- many visual regions;
- many small media nodes;
- node clusters arranged as story/process boards;
- strong preset/toolbox emphasis;
- `Skills` exposed at the top.

What Atelier should borrow:

- creator-facing add-node taxonomy;
- toolbox/preset library;
- material categories;
- canvas regions/groups;
- process-gallery/remix thinking;
- save/share/skills top-level affordance.

### 5.2 RHTV Is More Workbench-Like

RHTV feels like a direct generation cockpit:

- clear source-to-output reference lines;
- selected node expands into full generation controls;
- inline references in prompt;
- model/parameter/cost/generate controls inside the node;
- post-generation actions embedded on video nodes;
- workflow library plus director/clip modes.

What Atelier should borrow:

- selected media node as workbench;
- reference slots plus inline reference chips;
- node action bar;
- `加入 Agent` pattern;
- quick-cut/composition loop;
- model settings adjacent to preview and prompt.

### 5.3 The Difference That Matters For Atelier

LibTV teaches discoverability and template richness.

RHTV teaches node-local execution ergonomics.

Atelier should combine them:

- LibTV-style left rail + semantic add panel + presets;
- RHTV-style expanded node workbench + inline references + quick post-processing;
- Atelier's own differentiator: open-source local-first core plus agent harness and explicit permission model.

## 6. Product Principles For Atelier

### Principle 1: Node Is A Creative Object, Not A Step

A video node should contain:

- current chosen result;
- candidate results;
- references;
- prompt;
- model;
- parameters;
- generate button;
- status;
- post-generation actions.

The graph should describe creative dependency, not only execution order.

### Principle 2: References Must Be Visible Twice

Every reference should appear:

- as an edge on the canvas;
- as a chip/slot inside the node workbench.

Edges help spatial reasoning. Chips help prompt editing and model validation.

### Principle 3: Keep Low-Level Workflow Power, But Wrap It In Creation Semantics

Users who need power should still be able to inspect/modify execution details. But the default UI should speak in:

- images;
- videos;
- characters;
- scenes;
- prompts;
- candidates;
- sequences;
- agents.

Avoid exposing raw execution graph concepts as the first layer.

### Principle 4: Agent Should Operate On Selected Nodes

RHTV's `加入 Agent` is important. It implies a node can become conversational context.

Atelier should let users:

- select one or more nodes;
- add them to Agent context;
- ask Agent to generate variants, rewrite prompt, create follow-up nodes, or send results to sequence;
- review proposed changes before execution.

This matches Atelier's previously planned permission/harness direction.

### Principle 5: Quick Cut Belongs Inside Atelier

The canvas should close the creative loop for short pieces:

- collect chosen video nodes;
- arrange order;
- add basic transitions;
- place audio/music/SFX;
- export draft.

This does not require full Studio bridge in v1. Atelier can produce a self-contained canvas film.

## 7. Recommended Atelier Frontend Direction

### 7.1 Main Layout

Recommended structure:

- full-screen dark canvas;
- persistent left mode rail;
- top project bar;
- bottom-left viewport controls;
- floating/slide-in panels for assets, workflows, history, agent, sequence;
- node-local expanded workbench on selection;
- minimal right inspector only for advanced raw settings.

### 7.2 Left Rail

Suggested Atelier v3 rail:

- Add;
- Assets;
- Workflows;
- History;
- Agent;
- Sequence;
- Help.

Do not put every model/provider in the rail. Model/provider selection belongs inside generation nodes.

### 7.3 Add Panel

Suggested entries:

- Text;
- Image;
- Video;
- Audio;
- Script / Storyboard;
- Video Compose;
- Upload;
- From Library.

For v3, prioritize:

- Image;
- Video;
- Text;
- Upload;
- From Library.

### 7.4 Video Node States

Use three visual states:

1. Compact:
   - thumbnail;
   - title;
   - model badge;
   - status;
   - candidate count;
   - quick actions.

2. Selected:
   - preview;
   - candidate strip;
   - prompt;
   - reference chips/slots;
   - model/parameter controls;
   - generate button;
   - cost/status.

3. Expanded:
   - full workbench;
   - history;
   - raw request/response;
   - advanced model parameters;
   - validation warnings.

### 7.5 Reference UX

Reference handling should include:

- drag node onto another node to add as reference;
- `@` prompt mention menu;
- visible reference slots;
- removable chips;
- model-aware validation:
  - image-only models reject video references;
  - video-reference models accept video references;
  - character/scene slots can have semantic constraints;
- stale-reference warning when upstream node result changes.

This directly addresses the earlier R2V concern: mode is not enough; each model has its own input support.

### 7.6 Candidate UX

For the user's core story:

> create one video node, choose model/params, upload reference image(s), generate multiple candidate videos, preview and select best result, keep or delete alternatives.

Atelier should implement:

- `Generate 1x / 2x / 4x` control;
- candidate strip inside video node;
- one selected "primary result";
- candidate actions:
  - preview;
  - set as result;
  - send to sequence;
  - use as reference;
  - delete;
  - download.

### 7.7 Workflow Library

Implement an open-source workflow library with:

- local JSON/YAML templates;
- category filters;
- preview card;
- "Insert into canvas";
- "Remix from selection";
- "Save current selection as workflow".

Initial categories:

- Story Video;
- Character Reference;
- Scene Reference;
- Product Video;
- Motion Preset;
- Video Compose;
- Audio/SFX;
- Utility.

### 7.8 Agent Panel

Agent should be a real canvas operator, not only chat.

Minimum actions:

- create nodes;
- attach selected nodes as context;
- write/update prompts;
- propose reference wiring;
- propose model/parameter changes;
- generate after permission;
- explain why a node failed;
- summarize canvas history.

UI hooks:

- node action: `Add to Agent`;
- panel action: `Use selection`;
- permission level:
  - ask before action;
  - ask on failure;
  - ask on request;
  - never ask;
- action preview before write/generate.

## 8. Implementation Boundary For Atelier v3/v4

### v3 Must-Have

1. Left rail shell:
   - Add;
   - Assets;
   - Workflows;
   - History;
   - Agent;
   - Sequence.

2. Add panel:
   - Image, Video, Text, Upload, From Library.

3. Video node selected workbench:
   - preview;
   - prompt;
   - model selector;
   - parameter summary;
   - reference slots;
   - generate button;
   - candidate results.

4. Reference chips:
   - show `@image` references in prompt;
   - maintain structured refs in node payload.

5. Workflow/preset browser:
   - static local presets first;
   - insert nodes onto canvas.

6. Agent attach action:
   - add selected node(s) to Agent context;
   - no autonomous generation unless permission allows it.

7. Sequence/Quick Cut first pass:
   - collect selected video results;
   - reorder;
   - preview list;
   - simple export placeholder if backend export is not ready.

### v3 Should Avoid

- Full marketplace system.
- Full public community gallery.
- Full Studio bridge.
- Complex multi-user collaboration.
- Raw ComfyUI-style node catalog as the primary UX.
- A large right inspector that duplicates every node's workbench.

### v4 Candidate Work

- Process gallery / "view creation process";
- canvas group/frames;
- save current selection as reusable workflow;
- template sharing;
- node-level version timeline;
- richer quick-cut timeline;
- advanced raw execution graph view;
- provider-specific model cards and validation UI;
- deep Agent planning with tool-call audit trail.

## 9. Practical UI Checklist For Frontend Redesign

Use this checklist when redesigning Atelier:

- Does a new user immediately see how to create Image / Video / Upload nodes?
- Can the user understand source references from the canvas lines alone?
- Can the user understand references from inside the selected node alone?
- Can a video node be configured without leaving the node?
- Are model, parameters, prompt, references, candidate takes, and generate button visually adjacent?
- Can failed or old candidate takes be kept without polluting the chosen result?
- Can a node be sent to Agent with one click?
- Can a generated result be sent to Sequence/Quick Cut with one click?
- Is there a workflow/template browser that teaches use cases?
- Are minimap, grid snap, reset view, and zoom always discoverable?
- Does the design avoid generic admin/table/form feeling?

## 10. Final Recommendation

Atelier should benchmark against LibTV and RHTV at the interaction grammar level, not at the business-model level.

The most important changes are:

1. Make the selected video node a complete generation workbench.
2. Add structured reference chips and visible reference slots.
3. Add a LibTV/RHTV-style left rail with Add, Assets, Workflows, History, Agent, Sequence.
4. Build a local open-source workflow/preset library.
5. Let Agent operate on selected nodes with explicit permission and review.
6. Keep Quick Cut/Sequence inside Atelier so canvas creation can end in a draft film.

The differentiator should be:

> LibTV/RHTV-like creator ergonomics, but implemented as an open-source, model-catalog-aware, agent-harnessed canvas built on LumenX Core.

