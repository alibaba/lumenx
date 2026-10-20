# LumenX Unified Model Catalog Design Convergence

> Date: 2026-04-16  
> Status: Pre-implementation design convergence  
> Scope: LumenX-first decision document  
> Purpose: Resolve the remaining design questions before implementation, while explicitly minimizing migration risk for the current LumenX codebase.

---

## 1. Why This Document Exists

We already have two earlier documents:

- [Unified Model Catalog Core And Overlay Design Draft](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/plans/2026-04-16-unified-model-catalog-core-and-overlays.md)
- [Unified Model Catalog Spec v0.1](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/plans/2026-04-16-unified-model-catalog-spec-v0.1.md)
- [Unified Model Catalog Platform/Gateway Extension Note](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/plans/2026-04-16-unified-model-catalog-platform-gateway-extension.md)

Those two documents define:

- **why** the two catalog systems should converge
- **what** a shared schema could look like

But before implementation, LumenX still needs one more thing:

> a practical convergence decision memo that answers “what do we actually do first in LumenX, and how do we avoid a high-cost migration?”

This document is that memo.

---

## 2. The Key Clarification: “Migration” Does **Not** Mean Big-Bang Rewrite

This point must be explicit, because the word “migration” can sound much riskier than what we actually want.

For LumenX, **migration means internal schema evolution with compatibility preservation**, not:

- rewriting every adapter first
- rewriting the frontend to a new contract in one pass
- changing all stored project data format immediately
- renaming all model IDs everywhere at once
- forcing users to reopen or repair old projects

### What migration means in this plan

It means:

1. **We improve the source schema first**
2. **We keep the generated output backward-compatible**
3. **We preserve current frontend and backend consumption shape in phase 1**
4. **We add canonical IDs and alias mapping alongside existing IDs**
5. **We move runtime logic toward the new shape only after the data layer stabilizes**

In plain words:

> The first implementation phase should feel like “catalog got smarter,” not “the whole app got rebuilt.”

---

## 3. Final Position For LumenX

LumenX **can** adapt to the unified model-catalog direction, and it can do so with acceptable risk **if and only if** we keep these rules:

### Rule 1

**Do not change LumenX frontend consumer shape in phase 1.**

That means the frontend should still be able to ask for things like:

- default `t2i_model`
- default `i2i_model`
- default `i2v_model`
- visible model lists for `project_settings`, `series_settings`, `video_sidebar`, `global_settings`

### Rule 2

**Do not remove existing flat model IDs in phase 1.**

Instead:

- keep legacy IDs working
- add canonical mode-aware IDs in parallel
- emit alias maps

### Rule 3

**Do not force runtime adapter refactors before the source schema is stable.**

In phase 1, runtime overlay fields may exist in the source schema, but LumenX does not need to consume them everywhere yet.

### Rule 4

**Do not make old project data invalid.**

The migration must preserve:

- existing `model_settings` values
- current fallback normalization behavior
- hidden route model vs visible selection model behavior

---

## 4. Resolved Design Decisions

This section answers the major open questions from Spec v0.1 specifically for LumenX-first implementation.

---

### Decision A: Canonical backend naming in shared core

**Decision:** Use `dashscope` as the canonical backend name in LumenX’s source schema.

### Why

- LumenX already uses `dashscope` in:
  - provider routing
  - backend env keys
  - current catalog source
- The app domain is “Alibaba DashScope / 百炼 as product-facing backend”
- Renaming LumenX internals to `bailian` would create churn without real value

### Compatibility position

If the shared cross-project spec later wants to normalize `bailian` from video-generator:

- do that at import/export or normalization boundaries
- do **not** force LumenX to rename its current backend key now

### Implication

For LumenX implementation:

- keep `dashscope`
- treat `bailian` as an external synonym if needed later

---

### Decision B: Where `product` overlay should live

**Decision:** In LumenX, `product` overlay should live primarily at the **mode level**.

### Why

Because LumenX product behavior is mode-sensitive:

- `t2i` visibility is different from `i2v`
- `i2i` reference image rules are different
- `r2v` route mode may be hidden while `i2v` selection mode remains visible

If we place UI concerns only at model-line level, we lose the ability to express:

- same model line, different visible modes
- same model line, one mode hidden, another visible
- different UI groups for different modes

### Allowed exception

Model-line-level product fields may still exist for shared labels or future grouping helpers, but:

> the executable UI visibility contract should be mode-level.

---

### Decision C: Where runtime overlay should live

**Decision:** Runtime overlay also lives at the **mode level**.

### Why

Because runtime execution differs most strongly by mode:

- `t2v` vs `i2v` may use different payload structure
- `kf2v` needs end-frame handling
- `r2v` needs reference-media handling
- vendor direct and dashscope may map to different official model names by mode

This aligns with the strongest lesson from video-generator:

> mode is the actual execution unit

---

### Decision D: Should `routing_prefixes` remain

**Decision:** Keep `routing_prefixes` in LumenX phase 1 as a compatibility field.

### Why

Today LumenX provider resolution still benefits from family-prefix matching.

Even if long-term we may derive more routing information from mode-aware model line IDs, removing `routing_prefixes` now would create unnecessary change.

### Position

- keep in phase 1
- mark as “compatibility-supporting metadata”
- revisit only after mode-aware runtime resolution is stable

---

### Decision E: Should `transport` stay family-level

**Decision:** Keep `transport` family-level in phase 1.

### Why

In LumenX today, transport semantics are mostly provider-family concerns:

- dashscope image input mode
- vendor image input mode
- audio input mode
- reference video input mode

Even if some future models require per-mode overrides, the current system can still evolve by:

- keeping family defaults
- allowing mode-level runtime overrides later

### Position

For now:

- `family.transport.*` stays
- future `mode.runtime.<backend>` may override it if needed

This is lower risk than moving transport deeply into runtime overlay immediately.

---

### Decision E2: Should Platform/Gateway be part of this implementation generation?

**Decision:** Yes, but as a **reserved and documented runtime extension point**, not as a primary routing axis in phase 1.

### Why

Because future sponsored inference providers are likely to behave like:

- multi-family execution platforms
- unified auth and task frameworks
- unified media upload / polling systems

If we do not account for that now, future integration will still be possible, but the schema may need another structural revision.

### What this means in practice

For LumenX phase 1:

- backend remains the active routing key
- `runtime.<backend>.gateway` is allowed by design
- provider registry does not need to become gateway-first yet
- frontend does not need gateway UI yet

### Why this is still low risk

Because this decision changes the **design space**, not the current consumer contract.

It reserves flexibility without forcing big migration cost.

---

### Decision F: What should `defaults.model_settings` point to in LumenX

**Decision:** In phase 1, LumenX-generated artifacts should continue to expose **legacy-compatible flat IDs** in `defaults.model_settings`.

### This is the most important low-risk decision

Why:

- current frontend and backend already expect:
  - `t2i_model`
  - `i2i_model`
  - `i2v_model`
- these values are currently flat model IDs
- changing them to canonical mode IDs immediately would force:
  - frontend list logic rewrite
  - stored project compatibility rewrite
  - route-selection rewrite

### Therefore

Phase 1 should generate both:

1. **Canonical mode-aware IDs**
2. **Legacy-compatible default IDs**

Example:

```json
{
  "defaults": {
    "model_settings": {
      "t2i_model": "wan2.6-t2i",
      "i2i_model": "wan2.6-image",
      "i2v_model": "wan2.6-i2v"
    }
  },
  "canonical_defaults": {
    "t2i_mode_id": "wan/wan2.6-line#t2i",
    "i2i_mode_id": "wan/wan2.6-line#i2i",
    "i2v_mode_id": "wan/wan2.6-line#i2v"
  },
  "compat": {
    "legacy_model_ids": {
      "wan2.6-t2i": "wan/wan2.6-line#t2i",
      "wan2.6-image": "wan/wan2.6-line#i2i",
      "wan2.6-i2v": "wan/wan2.6-line#i2v",
      "wan2.6-r2v": "wan/wan2.6-line#r2v"
    }
  }
}
```

### Result

This lets LumenX:

- keep current behavior
- gain canonical IDs
- postpone risky consumer rewrites

---

### Decision G: Should `capabilities` survive?

**Decision:** Keep `capabilities` only as a **derived compatibility field**, not as the primary authoring abstraction.

### Why

Authoring should become mode-first:

- `modes.t2v`
- `modes.i2v`
- `modes.kf2v`
- `modes.r2v`

But generated compatibility artifacts may still expose:

```json
"capabilities": ["i2v", "r2v"]
```

This helps preserve existing logic during the transition.

### Position

- source schema: mode-first
- generated compatibility artifact: may still include `capabilities`

---

## 5. LumenX Phase-1 Success Criteria

This section defines what “safe enough to implement” means.

### Phase 1 should achieve all of these

1. LumenX source catalog can express `modes`
2. Existing generated frontend artifact remains consumable
3. Existing generated backend artifact remains consumable
4. `defaults.model_settings` still expose current flat IDs
5. legacy IDs are formally mapped to canonical mode IDs
6. hidden route model behavior still works
7. no existing project becomes invalid
8. current UI surfaces keep working:
   - `project_settings`
   - `series_settings`
   - `video_sidebar`
   - `global_settings`

### Phase 1 must **not** require

- rewriting all request builders
- changing stored project JSON shape
- removing current `resolveModelSettings()` behavior
- changing every frontend component to canonical mode IDs

---

## 6. What LumenX Should Implement First

This is the recommended first implementation slice.

### Step 1: Extend source schema shape

Allow family model entries to support:

- existing single-model entries
- new `modes` form

This lets us migrate family by family.

### Step 2: Normalize to internal mode-aware structure in builder

Inside `src/utils/model_catalog.py`, build an internal structure that understands:

- model line
- mode
- canonical mode ID
- legacy alias ID

This internal structure can be richer than the current generated JSON.

### Step 3: Keep generated compatibility artifact stable

Continue generating a frontend-friendly and backend-friendly artifact that still contains:

- current default fields
- visible lists
- flat IDs
- compatibility fields like `capabilities`

### Step 4: Add alias map to generated output

Add a formal alias map instead of leaving alias behavior implicit in code.

### Step 5: Add validation for alias and mode consistency

Validation should confirm:

- every legacy ID maps to exactly one canonical mode ID
- every default model resolves to a visible mode on required surfaces
- hidden route models are not accidentally exposed

---

## 7. What LumenX Should Explicitly Avoid In Phase 1

These are anti-goals for implementation.

### Avoid 1: rewriting frontend components around canonical IDs

Do not make components like:

- [frontend/src/components/common/ModelSettingsModal.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/common/ModelSettingsModal.tsx)
- [frontend/src/components/settings/SettingsPage.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/settings/SettingsPage.tsx)
- [frontend/src/components/modules/VideoSidebar.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/VideoSidebar.tsx)

consume canonical mode IDs directly yet.

### Avoid 2: moving all transport logic into runtime overlay

Keep current family transport metadata where it is until mode-aware runtime consumption is real.

### Avoid 3: deleting current provider registry fallback

Keep the current “catalog first, safe fallback second” posture in:

- [src/utils/provider_registry.py](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/provider_registry.py)

### Avoid 4: changing project persistence shape

Do not migrate saved `model_settings` payloads in the first pass.

Let alias resolution and normalization carry compatibility.

---

## 8. Cost/Risk Assessment

This section answers the concern directly: “Will this migration cost too much?”

### Short answer

**No, not if we implement the convergence in the order defined above.**

### Why the cost can stay controlled

Because the first implementation is not a rewrite of consumers. It is mainly:

1. a richer source schema
2. a smarter builder
3. a more expressive generated artifact
4. stronger validation

That means the bulk of the work is concentrated in:

- catalog YAML authoring shape
- [src/utils/model_catalog.py](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/model_catalog.py)
- catalog tests
- generated compatibility mapping

### Why the cost would become high

Cost only spikes if we try to do these too early:

- frontend canonical ID rewrite
- runtime overlay full consumption
- adapter-level auto-generation
- persistent data format change

This document explicitly recommends **not** doing those in phase 1.

---

## 9. Final Convergence Decision

Before implementation starts, this document resolves the design stance as follows:

### We will do

- mode-first source schema
- overlay separation
- `dashscope` remains canonical backend key in LumenX
- family-level transport stays for now
- Platform/Gateway is explicitly recognized as future runtime metadata
- legacy flat IDs stay exposed in generated defaults
- canonical mode IDs are added in parallel
- alias mapping becomes explicit

### We will not do yet

- big-bang runtime refactor
- big-bang frontend ID refactor
- project data migration
- removal of current compatibility consumers
- gateway-first routing rewrite

---

## 10. One-Sentence Implementation Guideline

> In LumenX, implement unified catalog convergence as a compatibility-preserving source-schema upgrade first, and postpone consumer rewrites until the generated contract has proven stable.
