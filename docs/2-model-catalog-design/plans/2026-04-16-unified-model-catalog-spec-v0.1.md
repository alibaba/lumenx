# Unified Model Catalog Spec v0.1

> Date: 2026-04-16  
> Status: Draft  
> Scope: Specification only  
> Purpose: Define a concrete shared catalog language that can be consumed by:
>
> 1. **LumenX** as a product/UI system
> 2. **video-generator** as a CLI/runtime execution system

Related addendum:

- [Unified Model Catalog Platform/Gateway Extension Note](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/plans/2026-04-16-unified-model-catalog-platform-gateway-extension.md)

---

## 1. Why This Spec Exists

We now have two mature-but-separate catalog systems:

- LumenX catalog is strong at:
  - frontend visibility
  - defaults
  - fallback normalization
  - selection-model vs route-model behavior
  - product-facing metadata
- video-generator catalog is strong at:
  - mode-specific modeling
  - backend-specific execution profiles
  - adapter mapping
  - vendor/Bailian runtime configuration
  - document traceability to raw vendor docs

The goal of this spec is **not** to force both systems to share one codebase immediately.

The goal is to define one common **data language** so both systems can describe model support using the same structure, while still consuming different subsets of that structure.

---

## 2. Non-Goals

This spec does **not** require:

- one shared Python loader immediately
- one shared TypeScript adapter immediately
- full runtime auto-generation from catalog on day one
- removal of existing LumenX consumer behavior in the same iteration
- removal of existing video-generator request builders in the same iteration

This spec is about **schema convergence first**, code convergence later.

---

## 3. Design Principles

### Principle 1: Mode is first-class

A model line may support multiple modes:

- `t2v`
- `i2v`
- `kf2v`
- `r2v`
- future modes

Each mode may differ in:

- inputs
- defaults
- supported backends
- runtime adapter
- official model name
- UI exposure

Therefore mode cannot be represented only by a flat `capabilities` array.

### Principle 2: Core facts and overlays must be separated

The shared core should describe **model facts**.

Consumer-specific needs should be expressed as overlays:

- `product` overlay for LumenX
- `runtime` overlay for video-generator and runtime execution

### Principle 3: The schema should support partial consumption

Consumers should be allowed to ignore fields they do not need.

Examples:

- LumenX may ignore `runtime.vendor.submit_url`
- video-generator may ignore `product.visible_in`

### Principle 4: Documentation traceability is mandatory for active support

Every active model line or active mode should be traceable to:

- integration-facing summarized docs
- raw vendor documentation snapshots

### Principle 5: Defaults must be explicit

Defaults must be declared at the correct level:

- root-level product defaults for LumenX model settings
- mode-level defaults for runtime execution
- family-level backend defaults where appropriate

### Principle 6: Backward compatibility must be preserved during migration

Schema evolution should not immediately break:

- existing saved LumenX projects
- existing frontend model selectors
- existing runtime adapters
- existing CLI calls in video-generator

---

## 4. Canonical Concept Model

The unified schema uses the following conceptual hierarchy:

```text
catalog
  ├── defaults
  ├── families
  └── model_lines
        └── modes
              ├── shared mode metadata
              ├── runtime overlay
              └── product overlay
```

### 4.1 Family

A family groups related model lines and shared provider-level behavior.

Examples:

- `wan`
- `kling`
- `vidu`
- `pixverse`

### 4.2 Model line

A model line is the stable logical product or official model identity.

Examples:

- `kling/kling-v3-video-generation`
- `vidu/viduq3-pro_img2video`
- `wan/wan2.6-video`

It is important that a model line is not required to map 1:1 to a single mode.

### 4.3 Mode

A mode is the executable behavior unit.

Examples:

- `t2v`
- `i2v`
- `kf2v`
- `r2v`

The same model line may expose multiple modes.

### 4.4 Backend

A backend is the concrete provider path used to execute a mode.

Current likely values:

- `dashscope`
- `bailian`
- `vendor`

This spec allows naming normalization in generated artifacts if projects prefer `dashscope` vs `bailian`, but the source schema should pick one canonical spelling per repository.

### 4.5 Platform / Gateway

This spec now explicitly reserves room for a future distinction between:

- **backend** = consumer-facing routing choice
- **gateway** = external inference platform or unified execution framework

Examples:

- backend = `dashscope`, gateway = `dashscope`
- backend = `vendor`, gateway = `vendor_direct`
- backend = `sponsor_platform`, gateway = `sponsor_platform`

For v0.1, backend remains the primary routing key. Gateway is treated as an extension point inside runtime metadata.

---

## 5. Canonical Source Schema

This section defines the YAML authoring shape.

### 5.1 Root document

```yaml
version: 2

defaults:
  backend_priority:
    - dashscope
    - vendor
  model_settings:
    t2i_model: wan/wan-2.6-image
    i2i_model: wan/wan-2.6-image-edit
    i2v_model: wan/wan-2.6-video

families:
  # optional inline family block or externalized per-family yaml

model_lines:
  # optional inline model line block or externalized per-family yaml
```

### 5.2 Required root keys

| Key | Required | Meaning |
|---|---|---|
| `version` | yes | Schema version |
| `defaults` | yes | Root defaults |
| `families` or externalized family files | yes | Family definitions |
| `model_lines` or externalized family files | yes | Model line definitions |

### 5.3 Root defaults

#### `defaults.backend_priority`

Purpose:

- Declare preference order when a consumer can choose from multiple supported backends.

Requirements:

- non-empty list
- values must be known backend names
- no duplicates

#### `defaults.model_settings`

Purpose:

- Product-level defaults for consumers like LumenX

Required keys:

- `t2i_model`
- `i2i_model`
- `i2v_model`

Notes:

- These may point either to model lines or to generated compatibility IDs during migration.
- Each referenced target must eventually resolve to a product-visible mode.

---

## 6. Family Schema

Each family defines provider-level shared data.

### 6.1 Family source shape

```yaml
family: kling
display_name: Kling
provider: kling
routing_prefixes:
  - kling-
supported_backends:
  - dashscope
  - vendor
default_backend: dashscope
backend_env_key: KLING_PROVIDER_MODE
credential_sources:
  dashscope:
    - DASHSCOPE_API_KEY
  vendor:
    - KLING_ACCESS_KEY
    - KLING_SECRET_KEY
docs:
  official_snapshot_ids:
    - aliyun/kling/2026-04-16
    - vendor/kling/2026-04-16
transport:
  image_input_mode:
    dashscope: dashscope_image_to_video
    vendor: kling_vendor_base64_image
  audio_input_mode:
    dashscope: dashscope_temp_file_url
    vendor: kling_vendor_audio_url
  reference_video_input_mode:
    dashscope: dashscope_temp_file_url
    vendor: kling_vendor_video_url
model_lines:
  - ...
```

### 6.2 Required family fields

| Field | Required | Meaning |
|---|---|---|
| `family` | yes | Stable family key |
| `display_name` | yes | Human-readable family name |
| `provider` | yes | Provider owner |
| `supported_backends` | yes | Allowed backends |
| `default_backend` | yes | Default backend at family level |
| `credential_sources` | yes | Family-level credential declarations |
| `docs.official_snapshot_ids` | yes | Official source snapshot identifiers |

### 6.3 Optional family fields

| Field | Optional | Meaning |
|---|---|---|
| `routing_prefixes` | yes | Legacy routing aid or family matching hint |
| `backend_env_key` | yes | Backend override env key |
| `transport.*` | yes | Media transport defaults per backend |

### 6.4 Family validation rules

1. `family` must be unique
2. `supported_backends` must be non-empty
3. `default_backend` must be included in `supported_backends`
4. Every backend in `credential_sources` must appear in `supported_backends`
5. `docs.official_snapshot_ids` must be non-empty

---

## 7. Model Line Schema

Model lines belong to families and contain mode definitions.

### 7.1 Model line source shape

```yaml
- id: kling/kling-v3-video-generation
  display_name: Kling 3.0 Video Generation
  status: active
  release_stage: stable
  description: Shared Kling 3.0 model line.
  aliases:
    - kling-v3
  docs:
    integration_doc_ids:
      - references/families/kling.md
    vendor_doc_paths:
      - vendor/kling/2026-04-16/model-guide.md
  modes:
    t2v:
      ...
    i2v:
      ...
    kf2v:
      ...
```

### 7.2 Required model line fields

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Stable model line identity |
| `display_name` | yes | Human-readable name |
| `status` | yes | `active`, `planned`, `deprecated`, or `hidden` |
| `release_stage` | yes | `stable`, `preview`, etc. |
| `description` | yes | Short description |
| `docs.integration_doc_ids` | yes for active | AI-friendly integration docs |
| `docs.vendor_doc_paths` | yes for active | Raw vendor doc traceability |
| `modes` | yes | Mode definitions |

### 7.3 Optional model line fields

| Field | Optional | Meaning |
|---|---|---|
| `aliases` | yes | Legacy IDs or migration aliases |
| `product` | yes | Product-level line overlay if needed |

### 7.4 Model line validation rules

1. `id` must be unique across all model lines
2. `modes` must be non-empty
3. active model lines must have both:
   - `docs.integration_doc_ids`
   - `docs.vendor_doc_paths`
4. `status` must be one of:
   - `active`
   - `planned`
   - `deprecated`
   - `hidden`

---

## 8. Mode Schema

Mode is the most important execution unit in the unified spec.

### 8.1 Mode source shape

```yaml
t2v:
  display_name: Kling 3.0 T2V
  defaults:
    duration: 5
    resolution: 720P
    aspect_ratio: "16:9"
    sound: false
  inputs:
    prompt: true
  params:
    negative_prompt: true
    sound: true
    multi_shot: true
  supported_backends:
    - dashscope
    - vendor
  default_backend: dashscope
  runtime:
    dashscope:
      adapter: bailian
      request_profile: bailian_kling_t2v
      model_name: kling/kling-v3-video-generation
      credentials:
        - DASHSCOPE_API_KEY
    vendor:
      adapter: origin_kling
      vendor_mode: t2v
      model_name: kling-v3
      credentials:
        - KLING_ACCESS_KEY
        - KLING_SECRET_KEY
  product:
    selection_group: i2v
    visible_in:
      - project_settings
      - series_settings
      - video_sidebar
      - global_settings
    recommended: true
    order: 60
    badges:
      - proxy-capable
```

### 8.2 Required mode fields

| Field | Required | Meaning |
|---|---|---|
| `display_name` | yes | Human-readable mode name |
| `inputs` | yes | Input contract |
| `supported_backends` | yes | Allowed backends for this mode |
| `default_backend` | yes | Mode-level default backend |

### 8.3 Optional mode fields

| Field | Optional | Meaning |
|---|---|---|
| `defaults` | yes | Execution defaults |
| `params` | yes | Supported parameter surface |
| `runtime` | yes | Runtime overlay |
| `product` | yes | Product overlay |

### 8.4 Mode validation rules

1. `supported_backends` must be non-empty
2. `default_backend` must be in `supported_backends`
3. If `runtime` is present:
   - every supported backend must have a runtime config
4. `inputs` must be a mapping of allowed inputs
5. `params` may be empty but must be a mapping when present

---

## 9. Input Schema

Inputs describe what the mode requires or accepts.

### 9.1 Basic input keys

Supported canonical keys in v0.1:

- `prompt`
- `image`
- `end_image`
- `images`
- `video_ref`
- `audio`
- `reference_images`
- `reference_videos`

### 9.2 Input value conventions

Simple input support:

```yaml
inputs:
  prompt: true
  image: true
```

Extended input metadata:

```yaml
inputs:
  reference_images:
    required: false
    min: 1
    max: 4
```

### 9.3 Rule

Consumers may read either:

- boolean support
- structured constraint block

The generated artifact should normalize these into an explicit structured representation if necessary.

---

## 10. Parameter Schema

Parameters describe optional execution controls.

### 10.1 Supported parameter shapes

Boolean parameter:

```yaml
params:
  negative_prompt: true
```

Enumerated parameter:

```yaml
params:
  resolution:
    options: [480P, 720P, 1080P]
    default: 720P
```

Numeric range parameter:

```yaml
params:
  cfg_scale:
    min: 0
    max: 1
    step: 0.1
    default: 0.5
```

### 10.2 Canonical parameter naming

v0.1 recommends snake_case in source schema:

- `negative_prompt`
- `prompt_extend`
- `shot_type`
- `cfg_scale`
- `movement_amplitude`

Consumers may transform names in generated compatibility layers.

### 10.3 Compatibility note

LumenX frontend currently uses camelCase-like keys in some places:

- `negativePrompt`
- `promptExtend`
- `shotType`
- `cfgScale`

This spec recommends:

> Canonical source schema uses `snake_case`; generated consumer artifacts may expose compatibility aliases.

---

## 11. Runtime Overlay Schema

Runtime overlay is for execution engines like video-generator and future LumenX runtime consumers.

### 11.1 Runtime backend block

```yaml
runtime:
  dashscope:
    adapter: bailian
    request_profile: bailian_wan_i2v
    model_name: wan2.7-i2v
    submit_url: https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis
    poll_base_url: https://dashscope.aliyuncs.com/api/v1/tasks
    credentials:
      - DASHSCOPE_API_KEY
  vendor:
    adapter: origin_vidu
    vendor_mode: i2v
    model_name: viduq3-pro
    credentials:
      - VIDU_API_KEY
```

### 11.2 Required runtime backend fields

| Field | Required | Meaning |
|---|---|---|
| `adapter` | yes | Runtime adapter name |
| `model_name` | yes | Backend-specific official model identifier |

### 11.3 Optional runtime backend fields

| Field | Optional | Meaning |
|---|---|---|
| `request_profile` | yes | Payload-building profile |
| `vendor_mode` | yes | Vendor-side mode name |
| `submit_url` | yes | Submission endpoint override |
| `poll_base_url` | yes | Polling endpoint override |
| `credentials` | yes | Backend-specific required credentials |
| `gateway` | yes | External platform or inference framework identifier |

### 11.4 Runtime overlay rules

1. Every backend in `supported_backends` should have a matching `runtime.<backend>` block if the consumer is runtime-aware
2. `credentials` may refine family credentials but must not introduce unknown backend names
3. If `gateway` is present, consumers must not assume `gateway == backend`

---

## 12. Product Overlay Schema

Product overlay is for UI and product-facing systems like LumenX.

### 12.1 Product block

```yaml
product:
  selection_group: i2v
  visible_in:
    - project_settings
    - series_settings
    - video_sidebar
    - global_settings
  recommended: true
  order: 100
  badges:
    - latest
  selection_model_id: wan/wan-2.6-video#i2v
  route_model_id: wan/wan-2.6-video#r2v
```

### 12.2 Required product fields

In v0.1, product overlay is optional overall.

If present, these rules apply:

| Field | Required | Meaning |
|---|---|---|
| `selection_group` | yes | UI grouping such as `t2i`, `i2i`, `i2v` |
| `visible_in` | yes | Surfaces where the mode should be shown |

### 12.3 Optional product fields

| Field | Optional | Meaning |
|---|---|---|
| `recommended` | yes | Highlight in UI |
| `order` | yes | Sort priority |
| `badges` | yes | UI badges |
| `selection_model_id` | yes | Visible selection identity |
| `route_model_id` | yes | Hidden runtime identity |

### 12.4 Product overlay rules

1. `visible_in` may be empty for hidden or planned routes
2. If a mode is active and visible, it must have document linkage at model-line level
3. `selection_group` values in v0.1 are:
   - `t2i`
   - `i2i`
   - `i2v`

---

## 13. Generated JSON Contract

Source YAML may be flexible, but generated JSON should be normalized.

### 13.1 Generated artifact goals

Generated JSON should be:

- deterministic
- directly consumable
- explicit rather than inferred
- compatible enough for migration consumers

### 13.2 Recommended normalized output shape

```json
{
  "version": 2,
  "defaults": {
    "backend_priority": ["dashscope", "vendor"],
    "model_settings": {
      "t2i_model": "wan/wan-2.6-image#t2i",
      "i2i_model": "wan/wan-2.6-image#i2i",
      "i2v_model": "wan/wan-2.6-video#i2v"
    }
  },
  "families": {
    "kling": {
      "family": "kling",
      "display_name": "Kling",
      "provider": "kling",
      "supported_backends": ["dashscope", "vendor"],
      "default_backend": "dashscope"
    }
  },
  "model_lines": {
    "kling/kling-v3-video-generation": {
      "id": "kling/kling-v3-video-generation",
      "family": "kling",
      "display_name": "Kling 3.0 Video Generation",
      "status": "active",
      "release_stage": "stable",
      "description": "..."
    }
  },
  "modes": {
    "kling/kling-v3-video-generation#t2v": {
      "id": "kling/kling-v3-video-generation#t2v",
      "model_line_id": "kling/kling-v3-video-generation",
      "mode": "t2v",
      "display_name": "Kling 3.0 T2V",
      "inputs": {...},
      "params": {...},
      "supported_backends": ["dashscope", "vendor"],
      "default_backend": "dashscope",
      "runtime": {...},
      "product": {...}
    }
  },
  "compat": {
    "legacy_model_ids": {
      "wan2.6-i2v": "wan/wan-2.6-video#i2v",
      "wan2.6-r2v": "wan/wan-2.6-video#r2v",
      "kling-v3": "kling/kling-v3-video-generation#i2v"
    }
  }
}
```

### 13.3 Why a separate `modes` map is recommended

This makes runtime and UI consumption easier because:

- every executable unit gets a stable ID
- lookup is direct
- compatibility aliases become cleaner

---

## 14. Validation Rules

These should become shared validation expectations regardless of consumer.

### 14.1 Structural validation

1. schema version must match
2. family names must be unique
3. model line IDs must be unique
4. mode IDs must be unique in generated artifact
5. supported backend lists must be non-empty
6. defaults must reference valid generated mode IDs or valid aliases

### 14.2 Documentation validation

For `active` model lines:

1. `docs.integration_doc_ids` must be non-empty
2. `docs.vendor_doc_paths` must be non-empty
3. `docs.official_snapshot_ids` must be non-empty at family level

### 14.3 Product validation

For product-visible modes:

1. `selection_group` must exist
2. `visible_in` must contain only known surfaces
3. product default models must resolve to visible modes on required surfaces

### 14.4 Runtime validation

For runtime-aware modes:

1. each supported backend must have a matching runtime config
2. each runtime config must define `adapter`
3. each runtime config must define `model_name`
4. runtime credential declarations must be valid

### 14.5 Compatibility validation

If compatibility aliases are emitted:

1. every alias target must resolve to a valid generated mode ID
2. no alias may point to a `planned` mode unless explicitly marked as migration-only

---

## 15. Compatibility Strategy

This section is crucial for phased rollout.

### 15.1 Source compatibility

During transition, authoring tools may allow:

- old single-mode entries
- old `capabilities` fields
- old product-only model entries

But the build step should normalize them into the new mode-aware generated contract.

### 15.2 LumenX compatibility

LumenX currently expects:

- frontend-friendly lists by selection group
- product defaults like `t2i_model`, `i2i_model`, `i2v_model`
- some legacy model IDs in persisted project state

Therefore migration must emit:

- consumer-friendly generated subsets
- alias maps from old IDs to new mode IDs
- route-model compatibility helpers

### 15.3 video-generator compatibility

video-generator currently expects:

- direct access to model-by-mode runtime configuration
- backend-specific request profiles
- one-step CLI lookup

Therefore migration must preserve:

- runtime backend blocks
- mode-specific defaults and inputs
- direct model+mode lookup behavior

---

## 16. Recommended Migration Plan

### Phase 0 — Accept the spec

Deliverables:

- this document
- agreement on:
  - mode-first modeling
  - core vs overlay split
  - generated compatibility artifacts

### Phase 1 — Upgrade LumenX authoring schema

Goal:

- allow `modes` under model lines
- keep current generated frontend and backend shapes stable
- avoid breaking product UI

### Phase 2 — Add alias and compatibility generation

Goal:

- generate stable mode IDs
- generate alias map for legacy IDs
- keep existing persisted projects valid

### Phase 3 — Add runtime overlay fields to LumenX

Goal:

- allow LumenX to gradually become runtime-profile aware
- do not immediately replace all adapter logic

### Phase 4 — Align video-generator source schema

Goal:

- rename fields to canonical names where needed
- keep runtime behavior unchanged
- adopt shared validation contract

### Phase 5 — Evaluate shared tooling

Only after both schemas are close enough, decide whether to share:

- loader code
- validator code
- schema generation code

---

## 17. Open Questions

These are still intentionally unresolved in v0.1:

1. Should canonical backend naming be `dashscope` or `bailian` in shared core?
2. Should `routing_prefixes` remain in canonical source or become derived metadata?
3. Should `model_settings` defaults point to:
   - model-line IDs
   - mode IDs
   - consumer aliases
4. Should `product` live at:
   - model-line level
   - mode level
   - both
5. Should `transport` remain family-level or be partially moved into runtime backend blocks?
6. At what implementation phase should `gateway` move from reserved runtime metadata to actively consumed routing metadata?

For v0.1, these are acceptable to leave open as long as the migration plan preserves compatibility.

---

## 18. Recommendation Summary

### Adopt immediately

1. `mode` becomes first-class
2. core facts and overlays are separated
3. source schema uses canonical `snake_case`
4. generated artifacts provide compatibility aliases
5. active support requires raw vendor doc traceability
6. runtime overlay may carry a future-facing `gateway` field without forcing gateway-first routing yet

### Do not do immediately

1. do not force shared code library yet
2. do not break LumenX frontend consumer contracts yet
3. do not replace video-generator runtime builders yet
4. do not redesign phase-1 routing around gateway-first logic

---

## 19. Final Position

This spec recommends a shared data language, not an immediate shared implementation.

That is the safest path because it:

- reduces drift
- preserves current project strengths
- supports gradual migration
- avoids a risky rewrite

### One-sentence summary

> Define one canonical model catalog grammar, then let LumenX and video-generator consume different normalized views of the same truth.
