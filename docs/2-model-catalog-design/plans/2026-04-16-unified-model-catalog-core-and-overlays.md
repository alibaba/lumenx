# Unified Model Catalog Core And Overlay Design Draft

> Draft date: 2026-04-16  
> Scope: Design only, no implementation changes in this document  
> Purpose: Provide a concrete convergence draft for:
>
> 1. LumenX repo-native `model_catalog`
> 2. `video-generator` skill catalog under `~/.claude/skills/video-generator`

---

## 1. Why This Draft Exists

We now have two independently evolved catalog systems that solve almost the same class of problems:

- **LumenX** uses `model_catalog` to drive:
  - frontend model lists
  - backend defaults
  - provider-family routing
  - UI visibility, recommended badges, and fallback behavior
- **video-generator** uses its catalog to drive:
  - CLI execution
  - adapter selection
  - request profile selection
  - backend-specific payload construction
  - document traceability to vendor docs

They are not conflicting systems. They are two partial views of the same domain.

The problem today is not “which one is correct.”

The real problem is:

> We now have enough overlap that continuing to evolve them separately will create avoidable drift in model naming, backend rules, mode support, and documentation traceability.

This draft proposes a **shared core schema** plus **consumer-specific overlays**, so both systems can converge without forcing one project to adopt the other project’s runtime assumptions wholesale.

---

## 2. Inputs Reviewed For This Draft

### 2.1 LumenX inputs

- [config/model_catalog/catalog.meta.yaml](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/catalog.meta.yaml)
- [config/model_catalog/families/wan.yaml](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/wan.yaml)
- [config/model_catalog/families/kling.yaml](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/kling.yaml)
- [config/model_catalog/families/vidu.yaml](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/vidu.yaml)
- [config/model_catalog/families/pixverse.yaml](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/pixverse.yaml)
- [src/utils/model_catalog.py](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/model_catalog.py)
- [src/utils/provider_registry.py](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/provider_registry.py)
- [frontend/src/lib/modelCatalog.ts](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/lib/modelCatalog.ts)

### 2.2 video-generator inputs

- [~/.claude/skills/video-generator/catalog/catalog.meta.yaml](file:///Users/hoshinoren/.claude/skills/video-generator/catalog/catalog.meta.yaml)
- [~/.claude/skills/video-generator/catalog/families/wanx.yaml](file:///Users/hoshinoren/.claude/skills/video-generator/catalog/families/wanx.yaml)
- [~/.claude/skills/video-generator/catalog/families/kling.yaml](file:///Users/hoshinoren/.claude/skills/video-generator/catalog/families/kling.yaml)
- [~/.claude/skills/video-generator/catalog/families/vidu.yaml](file:///Users/hoshinoren/.claude/skills/video-generator/catalog/families/vidu.yaml)
- [~/.claude/skills/video-generator/catalog/families/pixverse.yaml](file:///Users/hoshinoren/.claude/skills/video-generator/catalog/families/pixverse.yaml)
- [~/.claude/skills/video-generator/scripts/runtime/catalog_loader.py](file:///Users/hoshinoren/.claude/skills/video-generator/scripts/runtime/catalog_loader.py)
- [~/.claude/skills/video-generator/scripts/runtime/request_builder.py](file:///Users/hoshinoren/.claude/skills/video-generator/scripts/runtime/request_builder.py)
- [~/.claude/skills/video-generator/references/model-catalog.md](file:///Users/hoshinoren/.claude/skills/video-generator/references/model-catalog.md)

---

## 3. High-Level Conclusion

The two systems should converge, but **not by direct replacement**.

The right target is:

1. A **shared core schema** that represents model facts
2. A **LumenX product overlay** for UI and product-specific behavior
3. A **video-generator runtime overlay** for CLI/runtime execution details

### Short version

- `video-generator` is stronger at **runtime execution modeling**
- LumenX is stronger at **product/UI consumption modeling**
- The next unified version should preserve both strengths

---

## 4. Where The Two Systems Already Agree

These are the parts that are already conceptually aligned.

### 4.1 Both treat catalog as executable source of truth

Both systems already reject the old pattern of:

- frontend hardcoded list
- backend hardcoded defaults
- runtime hardcoded provider branches

Instead, both are moving toward:

- YAML as authoring source
- generated JSON as execution artifact
- validation scripts to prevent drift

This is already the same philosophy.

### 4.2 Both organize support by family

Both systems use family as a first-class boundary:

- Wan / Wanx
- Kling
- Vidu
- PixVerse

This is the correct shared anchor. A unified schema should absolutely keep `family` as the top-level grouping concept.

### 4.3 Both already model backend routing

Both systems know that model execution is no longer single-provider:

- DashScope / Bailian first
- vendor direct as fallback or optional override

This means backend routing is already part of model metadata, not just runtime code.

### 4.4 Both already model capabilities, inputs, and params

Neither system treats “model support” as just a string anymore.

Both already store structured information such as:

- supported capabilities or modes
- input requirements
- duration or resolution defaults
- parameter support
- credential sources

So the convergence target is not speculative; it is already implicit in both implementations.

---

## 5. Where They Differ Today

This section is the most important. The two systems differ in **what they optimize for**, not in overall direction.

### 5.1 LumenX optimizes for product and UI consumption

LumenX catalog currently excels at fields like:

- `ui.selection_group`
- `ui.visible_in`
- `ui.recommended`
- `ui.order`
- `ui.badges`
- product-level defaults
- frontend fallback normalization
- hidden route model vs visible selection model split

These are all product-facing concerns.

They answer questions like:

- Which model should the user see in project settings?
- Which model should appear in video sidebar?
- Which model should be selectable?
- Which hidden model should be used under the hood for R2V?
- How should old saved settings be normalized when a model disappears?

This is not a weakness. It is a real product requirement.

### 5.2 video-generator optimizes for runtime execution

The video-generator catalog currently goes deeper on:

- `mode`
- `modes`
- backend-specific request profiles
- backend-specific adapter selection
- backend-specific `model_name`
- backend-specific `submit_url` / `poll_base_url`
- vendor doc path traceability

It answers questions like:

- For this exact mode, which adapter should build the request?
- Which official model name should be sent to Bailian?
- Which official model name should be sent to vendor direct?
- Which backend is allowed for this mode?
- Which raw vendor docs justify this model support?

This is closer to a full execution graph.

---

## 6. The Most Important Design Gap: Mode Is A First-Class Concept

This is the single most valuable lesson from `video-generator`.

### 6.1 LumenX today

LumenX mostly models video capability like this:

```yaml
id: kling-v3
capabilities: [t2v, i2v]
```

This is useful, but limited.

It tells us the model line can do both things, but it does **not** cleanly describe:

- whether `t2v` and `i2v` have different defaults
- whether they have different backend support
- whether they have different inputs
- whether they use different request profiles
- whether they use different vendor model names

### 6.2 video-generator today

`video-generator` can do this:

```yaml
id: kling/kling-v3-video-generation
modes:
  t2v: ...
  i2v: ...
  kf2v: ...
```

That is a better long-term abstraction.

### 6.3 Proposed principle

For the unified design:

> `mode` must become a first-class child of a model line, not just an implied capability tag.

This does **not** mean every catalog consumer must expose mode the same way.

It means the schema should support it, and each consumer can derive its own surface from it.

---

## 7. Proposed Unified Architecture

The convergence target should have **three layers**:

### Layer A — Shared Core Schema

This layer stores objective model facts:

- family
- model line
- mode
- backend support
- input support
- parameter support
- defaults
- docs traceability

This layer should be reusable by both LumenX and video-generator.

### Layer B — Product Overlay

This layer stores LumenX-only concerns:

- which surfaces expose this model
- UI grouping
- UI ordering
- badges
- recommended status
- fallback behavior
- route-model vs selection-model behavior

### Layer C — Runtime Overlay

This layer stores runtime-execution concerns:

- adapter name
- request profile
- backend-specific model name
- backend submit URL
- backend polling URL
- vendor mode mapping
- execution-specific credential lists

This is the part that `video-generator` already does well.

---

## 8. Proposed Unified Core Schema

This is the core design direction. The exact field names can still change, but the shape should be close to this.

```yaml
version: 2
defaults:
  backend_priority:
    - dashscope
    - vendor
  model_settings:
    t2i_model: wan/wan-2.6
    i2i_model: wan/wan-2.6-image
    i2v_model: wan/wan-2.6-video

families:
  wan:
    display_name: Wan
    provider: aliyun
    supported_backends: [dashscope]
    default_backend: dashscope
    credential_sources:
      dashscope: [DASHSCOPE_API_KEY]
    docs:
      official_snapshot_ids:
        - aliyun/wan/2026-04-16

model_lines:
  - id: kling/kling-v3-video-generation
    family: kling
    display_name: Kling 3.0 Video Generation
    status: active
    release_stage: stable
    description: Kling 3.0 shared model line for text/video generation workflows.
    docs:
      integration_doc_ids:
        - references/families/kling.md
      vendor_doc_paths:
        - vendor/kling/2026-04-09/model-guide.md
    modes:
      t2v:
        defaults:
          duration: 5
          aspect_ratio: "16:9"
        inputs:
          prompt: true
        params:
          negative_prompt: true
          sound: true
        supported_backends: [dashscope, vendor]
        default_backend: dashscope
        runtime:
          dashscope:
            adapter: bailian
            request_profile: bailian_kling_t2v
            model_name: kling/kling-v3-video-generation
          vendor:
            adapter: origin_kling
            vendor_mode: t2v
            model_name: kling-v3
        product:
          selection_group: i2v
          visible_in: [project_settings, series_settings, video_sidebar, global_settings]
          order: 60
          badges: [proxy-capable]
      i2v:
        ...
      kf2v:
        ...
```

### Key idea

In the unified schema:

- `family` stays at the family level
- `model line` becomes the stable identity
- `mode` becomes the stable execution unit
- `runtime` and `product` become overlay namespaces

That keeps the schema unified **without forcing every consumer to care about every field**.

---

## 9. What Belongs In Shared Core vs Overlay

This is the practical split.

### 9.1 Shared core fields

These should be shared by both systems:

- `version`
- `family`
- `display_name`
- `provider`
- `supported_backends`
- `default_backend`
- `credential_sources`
- `status`
- `release_stage`
- `description`
- `docs.integration_doc_ids`
- `docs.vendor_doc_paths`
- `docs.official_snapshot_ids`
- `modes`
- `defaults`
- `inputs`
- `params`

### 9.2 LumenX product overlay fields

These are legitimate LumenX-only fields:

- `product.selection_group`
- `product.visible_in`
- `product.recommended`
- `product.order`
- `product.badges`
- `product.selection_model_id`
- `product.route_model_id`
- fallback / normalization rules if we later formalize them

### 9.3 video-generator runtime overlay fields

These are runtime-only concerns:

- `runtime.<backend>.adapter`
- `runtime.<backend>.request_profile`
- `runtime.<backend>.model_name`
- `runtime.<backend>.vendor_mode`
- `runtime.<backend>.submit_url`
- `runtime.<backend>.poll_base_url`
- `runtime.<backend>.credentials`

---

## 10. Field Mapping Draft

This section gives the first practical conversion map.

### 10.1 LumenX → unified core

| LumenX field | Unified target | Notes |
|---|---|---|
| `family` | `family` | Keep |
| `provider` | `family.provider` | Keep |
| `routing_prefixes` | `family.routing_prefixes` | Keep temporarily; may later become derived from line IDs |
| `supported_backends` | `family.supported_backends` or `mode.supported_backends` | Keep both levels if needed |
| `default_backend` | `family.default_backend` or `mode.default_backend` | Move mode-specific defaults down where needed |
| `credential_sources` | `family.credential_sources` | Keep |
| `supported_modalities` | Replace with explicit `modes` | This becomes less important once mode is first-class |
| `transport.*_input_mode` | `family.transport.*` | Keep for now; can also become backend-mode runtime metadata later |
| `capabilities` | replace with explicit mode keys or derive from them | Prefer derive from `modes` |
| `duration` | `mode.defaults.duration` | Move under mode |
| `params` | `mode.params` | Move under mode |
| `inputs.reference_images.max` | `mode.inputs.reference_images.max` | Move under mode when relevant |
| `ui.*` | `product.*` | Keep as product overlay |

### 10.2 video-generator → unified core

| video-generator field | Unified target | Notes |
|---|---|---|
| `models[].id` | `model_line.id` | Keep |
| `models[].mode` | `modes.<mode>` | Convert single-mode lines into explicit `modes` |
| `models[].modes` | `modes` | Keep |
| `defaults.backend_priority` | root `defaults.backend_priority` | Strong candidate to adopt in LumenX too |
| `docs.integration_doc_ids` | `docs.integration_doc_ids` | Keep |
| `docs.vendor_doc_paths` | `docs.vendor_doc_paths` | Strongly worth adopting in LumenX |
| `backends.*.adapter` | `runtime.<backend>.adapter` | Keep |
| `backends.*.request_profile` | `runtime.<backend>.request_profile` | Keep |
| `backends.*.model_name` | `runtime.<backend>.model_name` | Keep |
| `backends.*.vendor_mode` | `runtime.<backend>.vendor_mode` | Keep |
| `defaults.*` | `mode.defaults.*` | Keep |
| `inputs.*` | `mode.inputs.*` | Keep |
| `params.*` | `mode.params.*` | Keep |

---

## 11. What LumenX Should Borrow First

If implementation starts incrementally, LumenX should borrow these parts first, in this order:

### Phase 1 — Add explicit mode config

This is the highest-value change.

Why first:

- It removes ambiguity around shared model lines
- It avoids future capability inflation
- It creates a stable bridge to video-generator

### Phase 2 — Add backend runtime profile structure

Add optional runtime sections such as:

```yaml
runtime:
  dashscope:
    adapter: ...
    request_profile: ...
    model_name: ...
  vendor:
    adapter: ...
    vendor_mode: ...
    model_name: ...
```

This lets LumenX gradually move toward catalog-driven runtime execution.

### Phase 3 — Add vendor doc path traceability

Add raw doc linkage to every active model/mode so onboarding and auditing become more durable.

---

## 12. What video-generator Should Borrow From LumenX

This should also be explicit, because convergence is not one-way.

### 12.1 Product-facing visibility concepts

If video-generator ever powers an interactive UI, LumenX’s current notions are already useful:

- `visible_in`
- `recommended`
- `badges`
- `order`

### 12.2 Hidden route model vs visible selection model

This is especially valuable for cases like:

- R2V route model differs from user-facing selection model
- one vendor mode is operationally hidden but still runtime-valid

### 12.3 Fallback normalization

LumenX already treats stale saved model IDs as a product reality.

This is worth preserving as a reusable pattern.

---

## 13. Migration Strategy

Do not try to unify both systems in one big rewrite.

### Step 1 — Define the shared conceptual schema

Deliverable:

- a shared spec doc
- example YAML fragments
- field mapping decisions

This draft is the first version of that step.

### Step 2 — Upgrade LumenX schema shape without changing behavior

Goal:

- add `modes`
- preserve current UI output
- preserve current frontend generated mirror
- preserve current defaults and fallback behavior

Important rule:

> Change schema shape first, keep behavior stable.

### Step 3 — Add runtime overlay fields to LumenX catalog

Goal:

- make runtime profile information catalog-readable
- do not immediately delete existing adapter code
- first consume it in validation or reporting

### Step 4 — Decide where shared code should live

There are three options:

1. **Spec-only sharing**
   - same schema idea, separate loaders
   - cheapest and lowest risk
2. **Shared Python library**
   - one loader/validator reused by both
   - stronger consistency
   - more coupling
3. **Generated artifact contract only**
   - both author separately, but validate against same generated schema
   - moderate coupling

### Recommendation

Start with **spec-only sharing**, then revisit shared loader code after the schema stabilizes.

That keeps velocity high and risk low.

---

## 14. Risks

### Risk 1 — Over-unifying too early

If we try to force identical runtime behavior into both projects too early, we may damage the product-specific strengths of LumenX or the CLI strengths of video-generator.

Mitigation:

- unify schema concepts first
- keep overlays separate

### Risk 2 — Losing UI semantics

If convergence focuses only on runtime execution, LumenX may lose valuable UI-specific metadata.

Mitigation:

- make `product` an explicit overlay namespace

### Risk 3 — Losing runtime fidelity

If convergence focuses only on frontend/UI needs, `video-generator` may lose request-profile fidelity.

Mitigation:

- make `runtime` an explicit overlay namespace

### Risk 4 — Migration churn

Introducing `modes` will affect loaders, validators, tests, and UI adapters.

Mitigation:

- add compatibility reading first
- preserve generated output shape during transition

---

## 15. Recommended Next Document

The next document after this draft should be much more operational:

**`Unified Model Catalog Spec v0.1`**

It should include:

1. final field names
2. canonical YAML examples
3. compatibility rules
4. generated JSON target shape
5. validation rules
6. phased implementation plan for LumenX
7. phased implementation plan for video-generator

---

## 16. Final Recommendation

The right next move is **not** to merge code immediately.

The right next move is:

1. accept `mode` as a first-class unit
2. separate shared core from overlays
3. preserve LumenX’s product metadata
4. preserve video-generator’s runtime metadata
5. converge schema before converging code

### In one sentence

> Build one shared catalog language, then let LumenX and video-generator speak different dialects of it through overlays.

