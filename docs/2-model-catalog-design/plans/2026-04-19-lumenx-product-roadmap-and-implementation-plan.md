# LumenX Product Roadmap And Implementation Plan

> **For Claude/Codex:** This is a planning artifact for roadmap alignment and future implementation sequencing. It is intentionally product-facing first, but each initiative includes concrete codebase touchpoints so it can be turned into execution plans later.

**Goal:** Provide a prioritized product roadmap for LumenX after the recent model-catalog, onboarding, no-OSS, provider-routing, and dev-runtime work, while incorporating the newly proposed feature directions:

1. Chinese / English language switch
2. Day / night theme switch (night remains the default)
3. Remove the currently non-functional Scripts-page memo / quick-notes text box
4. Add an infinite-canvas creation mode
5. Add a sidebar `video_agent` capability inspired by Claude / OpenAI agent-sdk workflows

**Architecture framing:** LumenX should continue evolving along three interacting axes:

- **Platform axis** — model catalog, onboarding, provider/gateway routing, local-first media, runtime reliability
- **Creation axis** — script → asset → storyboard → motion → export workflow plus new infinite-canvas mode
- **Product experience axis** — internationalization, theming, interaction polish, assistant workflows, failure recovery, observability

**Tech Stack:** Next.js 14 + React + TypeScript frontend, FastAPI backend, Zustand state, repo-native `model_catalog`, DashScope / vendor routing, local-first media outputs, pywebview desktop mode.

---

## 1. Strategic framing

The next stage of LumenX should not be “add random features.”  
It should be a deliberate shift from:

- a capable but still evolving AI comic generation tool

into:

- a **model-extensible**, **local-first**, **workflow-aware**, **assistant-augmented** production environment

That means roadmap choices should favor:

1. **features that widen usable audience**
2. **features that reduce future implementation cost**
3. **features that improve creative control without making the product feel like enterprise admin software**

---

## 2. Planning assumptions

This roadmap assumes the following are true:

1. `model_catalog` remains the long-term model truth source
2. DashScope-first plus vendor-fallback remains the core provider strategy
3. OSS remains optional, not mandatory
4. The app should remain usable for local-first creators
5. The product can evolve from “guided pipeline” into “multiple creation modes”
6. Future inference platforms / sponsored gateways may need to be integrated

---

## 3. Current codebase touchpoints that matter for roadmap decisions

These are the current frontend/backend areas most relevant to the roadmap.

### 3.1 App shell and global product chrome

- [frontend/src/app/layout.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/app/layout.tsx)
- [frontend/src/components/layout/AppShell.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/layout/AppShell.tsx)
- [frontend/src/components/layout/GlobalSidebar.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/layout/GlobalSidebar.tsx)
- [frontend/src/components/layout/PipelineSidebar.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/layout/PipelineSidebar.tsx)

These files are likely touchpoints for:

- language switch
- theme switch
- future `video_agent` sidebar entry
- multi-mode navigation

### 3.2 Current guided project workflow

- [frontend/src/components/project/ProjectClient.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/project/ProjectClient.tsx)
- [frontend/src/components/modules/ScriptProcessor.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/ScriptProcessor.tsx)
- [frontend/src/components/modules/ConsistencyVault.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/ConsistencyVault.tsx)
- [frontend/src/components/modules/StoryboardComposer.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/StoryboardComposer.tsx)
- [frontend/src/components/modules/VideoGenerator.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/VideoGenerator.tsx)
- [frontend/src/components/modules/VideoCreator.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/VideoCreator.tsx)
- [frontend/src/components/modules/VideoSidebar.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/VideoSidebar.tsx)
- [frontend/src/components/modules/PropertiesPanel.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/PropertiesPanel.tsx)

These files are likely touchpoints for:

- Scripts-page cleanup
- infinite-canvas mode insertion
- video-agent sidebar entry
- creation workflow branching

### 3.3 Theme and rendering surface

- [frontend/src/app/globals.css](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/app/globals.css)
- [frontend/tailwind.config.ts](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/tailwind.config.ts)
- [frontend/src/components/canvas/CreativeCanvas.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/canvas/CreativeCanvas.tsx)

These files are likely touchpoints for:

- day/night theme system
- infinite-canvas visuals
- design-token centralization

### 3.4 Model, provider, and backend evolution

- [config/model_catalog/catalog.meta.yaml](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/catalog.meta.yaml)
- [config/model_catalog/families/*.yaml](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families)
- [src/utils/model_catalog.py](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/model_catalog.py)
- [src/utils/provider_registry.py](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/provider_registry.py)
- [frontend/src/lib/modelCatalog.ts](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/lib/modelCatalog.ts)
- [frontend/src/lib/api.ts](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/lib/api.ts)

These files are likely touchpoints for:

- model onboarding phase 2
- provider/gateway/platform routing
- future sponsor platform access

---

## 4. Product roadmap priorities (P0 / P1 / P2)

This is the roadmap ordering I recommend.

---

## P0 — Highest priority / next major iteration

These are the changes with the best ratio of value, leverage, and future impact.

### P0.1 — Continue Unified Model Catalog implementation (Phase 2)

**Why now**

This is the single highest-leverage platform investment.  
It directly affects:

- model onboarding speed
- provider routing evolution
- future sponsor gateway integration
- parameter surface generation
- frontend model configurability

**Current foundation already exists**

- additive `model_lines`
- additive `modes`
- additive `compat`
- route-model vs selection-model logic

**Next target**

- let more consumers read canonical mode-aware metadata
- keep flat compatibility IDs working
- make runtime overlay progressively more useful

**Reason it stays P0**

Without this, future model/platform integration cost stays too high.

---

### P0.2 — Productize the local-first / no-OSS path

**Why now**

This is directly user-impacting and removes a common adoption blocker.

**Current foundation already exists**

- design direction is clear
- local-first media strategy is agreed
- provider media fallback logic has been explored

**Next target**

- make no-OSS mode more explicit in product UI
- unify media reference handling
- test the no-OSS flow end-to-end as a supported mode, not a fallback accident

**Reason it stays P0**

This broadens real usability immediately.

---

### P0.3 — Introduce i18n foundation (Chinese / English switch)

**Why now**

LumenX already contains mixed Chinese / English copy in:

- layout
- settings
- project workflow
- action buttons
- prompt panels

This becomes increasingly expensive to retrofit later.

**Current likely touchpoints**

- [frontend/src/app/layout.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/app/layout.tsx)
- [frontend/src/components/layout/GlobalSidebar.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/layout/PipelineSidebar.tsx)
- [frontend/src/components/layout/AppShell.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/layout/AppShell.tsx)
- [frontend/src/components/settings/SettingsPage.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/settings/SettingsPage.tsx)
- [frontend/src/components/project/ProjectClient.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/project/ProjectClient.tsx)

**Recommended scope**

Phase 1 for i18n should **not** attempt perfect translation of all prompts and generated AI content.

It should do:

- app-shell-level locale switch
- UI copy dictionary extraction
- locale persistence
- Chinese / English UI rendering

**Reason it stays P0**

This is foundational UI infrastructure. If delayed too long, every new feature doubles copy debt.

---

### P0.4 — Introduce theme foundation (day / night switch, default night)

**Why now**

Current design system is strongly dark-first.  
That has worked so far, but user-requested light mode means:

- theme tokens need to be formalized
- dark assumptions need to be surfaced
- UI cannot keep hardcoding black / white / gray assumptions everywhere

**Current likely touchpoints**

- [frontend/src/app/globals.css](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/app/globals.css)
- [frontend/tailwind.config.ts](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/tailwind.config.ts)
- [frontend/src/app/layout.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/app/layout.tsx)
- shared layout components under `frontend/src/components/layout/`

**Recommended scope**

Phase 1 should do:

- theme state
- tokenized surface/background/text colors
- default = night
- day mode support in app shell and core panels

Do **not** try to visually perfect every screen in the same iteration.

**Reason it stays P0**

Like i18n, this gets more expensive the longer it is postponed.

---

### P0.5 — Remove the non-functional Scripts-page memo / Quick Notes box

**Why now**

This is small, but it is a good cleanup target because:

- it is visible
- it implies capability that does not exist
- it adds confusion

**Likely current location**

- [frontend/src/components/modules/PropertiesPanel.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/PropertiesPanel.tsx)
  - `ScriptInspector`
  - `Quick Notes` block

**Recommended treatment**

Default recommendation:

- remove it entirely in the next UI cleanup pass

Alternative if product value is identified later:

- replace with actual persisted script notes feature

**Reason it stays P0**

Low effort, immediate UX honesty improvement.

---

### P0.6 — Failure diagnostics and recovery guidance

**Why now**

Recent work already proved that:

- failures can come from model/provider mismatch
- no-OSS and provider routing increase execution branching
- users need better explanations than “generation failed”

**Recommended scope**

- classify errors
- show actionable remediation
- recommend fallback backend when possible
- distinguish config / input / provider / media-reference failures

**Reason it stays P0**

This is required if the platform keeps getting more flexible.

---

## P1 — Differentiating product capabilities

These are high-value product moves after the foundations above are stable.

### P1.1 — Add infinite-canvas mode (new creation mode)

**Why this matters**

You already identified an important trend:

> Video generation is drifting toward multi-reference / compositional creation, not only first-frame generation.

That means the current linear pipeline may stop being the only valid creation paradigm.

**Current likely touchpoints**

- [frontend/src/components/canvas/CreativeCanvas.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/canvas/CreativeCanvas.tsx)
- [frontend/src/components/project/ProjectClient.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/project/ProjectClient.tsx)
- [frontend/src/components/modules/VideoGenerator.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/VideoGenerator.tsx)
- [frontend/src/components/modules/ConsistencyVault.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/ConsistencyVault.tsx)

**Recommended product framing**

Do **not** treat infinite canvas as a small extension to the current motion page.  
Treat it as a **new creation mode**.

Two modes can coexist:

1. **Pipeline mode**
   - script → assets → storyboard → motion
2. **Infinite canvas mode**
   - assemble references, characters, scenes, prompts, and generation nodes freely

**Reason it is P1, not P0**

It is strategically important, but it is a larger interaction model shift and should not block the platform foundations above.

---

### P1.2 — Add sidebar `video_agent` support

**Why this matters**

This can become a strong differentiator if done well.

Potential uses:

- suggest prompts
- inspect scene context
- recommend models
- propose shot breakdowns
- assist with retries / fallbacks
- orchestrate reference-based generation flows

**Current likely touchpoints**

- [frontend/src/components/layout/GlobalSidebar.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/layout/GlobalSidebar.tsx)
- [frontend/src/components/layout/AppShell.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/layout/AppShell.tsx)
- [frontend/src/components/project/ProjectClient.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/project/ProjectClient.tsx)
- [frontend/src/components/modules/VideoSidebar.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/VideoSidebar.tsx)

**Recommended scope**

Phase 1 should be small:

- agent sidebar shell
- context injection from current project / current frame / current assets
- no autonomous execution yet
- assistant-first, not agentic-first

Then later:

- deeper tool integration
- provider-aware recommendations
- workflow suggestions

**Reason it is P1**

It is strategically exciting, but if done too early, it risks becoming a flashy layer on top of still-shifting platform foundations.

---

### P1.3 — Dynamic model parameter surfaces

**Why this matters**

As `model_catalog` becomes richer, manual parameter rendering becomes technical debt.

**Current likely touchpoints**

- [frontend/src/lib/modelCatalog.ts](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/lib/modelCatalog.ts)
- [frontend/src/components/modules/VideoSidebar.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/VideoSidebar.tsx)
- [frontend/src/components/common/ModelSettingsModal.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/common/ModelSettingsModal.tsx)
- [frontend/src/components/settings/SettingsPage.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/settings/SettingsPage.tsx)

**Reason it is P1**

It builds naturally on Phase 2 model catalog evolution.

---

### P1.4 — Asset / storyboard / video lineage and dependency tracing

**Why this matters**

The more reference-driven the product becomes, the more users need to understand:

- what derived from what
- which asset version a video used
- which storyboard frame is stale

**Reason it is P1**

Very valuable, but best built once model/platform/media foundations are more settled.

---

## P2 — Scale, maturity, and ecosystem expansion

These are important, but can follow once the above is stable.

### P2.1 — Series-level inheritance and long-form creation UX

Current series support already exists, but later phases can deepen:

- character inheritance
- style inheritance
- per-series model presets
- continuity tooling

### P2.2 — Templates / presets / workflow bundles

This is where LumenX can become more production-efficient for repeated output types.

### P2.3 — Gateway/platform active routing support

The design should already allow gateway metadata in runtime overlays, but active gateway-first execution can wait until:

- a real sponsor platform or unified inference framework is ready to integrate

### P2.4 — Task center / observability center

This includes:

- richer task states
- per-provider diagnostics
- retry / resume UI
- audit trails

### P2.5 — Desktop and export maturity

This includes:

- better packaging ergonomics
- export bundles
- project portability
- diagnostics tooling

---

## 5. Roadmap summary by priority

### P0 — Do next

1. Unified Model Catalog Phase 2
2. Local-first / no-OSS productization
3. Chinese / English UI switch foundation
4. Day / night theme foundation (night default)
5. Remove Scripts-page non-functional Quick Notes / memo box
6. Failure diagnostics and recovery guidance

### P1 — Do after foundations stabilize

7. Infinite-canvas mode
8. Sidebar `video_agent` MVP
9. Dynamic model parameter surfaces
10. Asset / storyboard / video lineage

### P2 — Do after core workflow and platform layers mature

11. Series-level inheritance enhancements
12. Templates / presets / workflow bundles
13. Active gateway/platform routing support
14. Task center / observability
15. Desktop / export maturity

---

## 6. Recommended implementation order

This is the order I recommend, not just the priority grouping.

### Stage A — Platform + UX foundations

Do together or back-to-back:

1. Unified Model Catalog Phase 2
2. i18n foundation
3. theme foundation
4. Quick Notes removal

Why:

- these all touch central app shape
- better to absorb shell / config / token changes in one coordinated cycle

### Stage B — Runtime usability

Then do:

5. no-OSS productization
6. failure diagnostics
7. dynamic parameter surfaces

Why:

- these improve day-to-day usability dramatically
- they build on stronger model/provider metadata

### Stage C — Mode expansion

Then do:

8. infinite-canvas mode MVP
9. `video_agent` sidebar MVP

Why:

- these are bigger interaction model changes
- they benefit from the earlier platform work being settled

### Stage D — Long-term productivity

Finally:

10. lineage
11. series inheritance
12. templates
13. observability center
14. export / desktop maturity

---

## 7. Recommended first implementation bundle

If we want to choose the next concrete build wave, I recommend this bundle:

### Bundle: “Foundation UX + platform”

Includes:

- Unified Model Catalog Phase 2
- Chinese / English switch
- Day / night mode switch
- Remove Scripts-page Quick Notes box

### Why this bundle

It creates the best balance of:

- visible user value
- long-term platform leverage
- controlled implementation surface

### Files likely touched

- [frontend/src/app/layout.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/app/layout.tsx)
- [frontend/src/components/layout/AppShell.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/layout/AppShell.tsx)
- [frontend/src/components/layout/GlobalSidebar.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/layout/GlobalSidebar.tsx)
- [frontend/src/components/settings/SettingsPage.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/settings/SettingsPage.tsx)
- [frontend/src/components/modules/PropertiesPanel.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/PropertiesPanel.tsx)
- [frontend/src/components/project/ProjectClient.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/project/ProjectClient.tsx)
- [frontend/src/app/globals.css](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/app/globals.css)
- [frontend/tailwind.config.ts](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/tailwind.config.ts)
- [frontend/src/lib/modelCatalog.ts](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/lib/modelCatalog.ts)
- [src/utils/model_catalog.py](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/model_catalog.py)

---

## 8. Risks and mitigation notes

### Risk 1 — i18n and theme work spread too widely

Mitigation:

- first extract app-shell-level state and core token system
- do not attempt perfect translation or full light-theme polish in one pass

### Risk 2 — Infinite canvas becomes a giant rewrite

Mitigation:

- treat it as a new mode, not a refactor of the current motion page
- start with MVP scope

### Risk 3 — `video_agent` becomes gimmicky before it is useful

Mitigation:

- ship assistant-first MVP before agentic execution
- keep it context-aware and workflow-aware

### Risk 4 — model/platform work and UI work drift apart

Mitigation:

- continue to make `model_catalog` the model truth source
- avoid adding new hardcoded model decisions in UI components

---

## 9. Verification standard for future roadmap implementation

Any roadmap item that enters execution should include:

- explicit UI acceptance criteria
- file touchpoints
- failure states
- regression test targets
- whether it changes:
  - persisted project data
  - model/provider routing
  - no-OSS behavior
  - desktop runtime behavior

---

## 10. Final recommendation

If only one roadmap message should be kept, it is this:

> The next best iteration is not “more features everywhere.” It is a focused foundation wave: complete the model-catalog platform layer, add i18n and theming foundations, remove clearly non-functional UI, and only then branch into new creation modes like infinite canvas and assistant workflows like video_agent.

