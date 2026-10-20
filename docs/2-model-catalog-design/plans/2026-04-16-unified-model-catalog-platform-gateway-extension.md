# Unified Model Catalog Platform/Gateway Extension Note

> Date: 2026-04-16  
> Status: Design addendum  
> Scope: Shared schema extension note before LumenX implementation  
> Purpose: Explicitly account for future inference-platform / sponsored gateway integration in the unified model catalog design.

---

## 1. Why This Addendum Exists

The current unified direction already models:

- family
- model line
- mode
- backend
- product overlay
- runtime overlay

That is already good enough for:

- DashScope-first routing
- vendor-direct fallback
- family-level transport rules

But a new future scenario is now considered likely:

> A sponsored inference API provider may offer a unified gateway that can execute multiple model families through one platform framework.

Examples of what that platform may do:

- proxy multiple upstream vendors
- normalize task submission and polling
- normalize auth
- normalize media upload
- expose one unified execution API across families
- host multiple model families under one contract

This is slightly different from an ordinary backend.

So before implementation begins, we should make this distinction explicit in the design.

---

## 2. The Core Distinction

The terms below must not be collapsed into one concept.

### 2.1 Family

The capability family or model domain.

Examples:

- `wan`
- `kling`
- `vidu`
- `pixverse`

This answers:

> “What kind of model capability is this?”

### 2.2 Mode

The executable operation shape.

Examples:

- `t2v`
- `i2v`
- `kf2v`
- `r2v`

This answers:

> “What operation are we performing?”

### 2.3 Backend

The concrete execution route for a mode.

Examples:

- `dashscope`
- `vendor`

This answers:

> “Which route is actually used to execute this mode?”

### 2.4 Platform / Gateway

The execution platform abstraction that may host one or more backends or present a unified inference contract across many families.

Examples:

- `dashscope`
- `vendor_direct`
- future `sponsor_platform`

This answers:

> “Which external platform or inference framework are we talking to?”

### Why backend and gateway are not always the same

For simple systems they may collapse into the same value.

Example:

- gateway = `dashscope`
- backend = `dashscope`

But once a sponsored multi-family inference platform appears, the distinction becomes useful:

- gateway = `sponsor_platform`
- backend = `sponsor_platform`
- family = `kling`
- mode = `i2v`
- runtime profile = `sponsor_platform_kling_i2v`

Or eventually:

- gateway = `sponsor_platform`
- backend = `managed_vendor_proxy`

This note does **not** require immediate implementation of separate backend/gateway values everywhere.  
It recommends preserving the conceptual space for it now.

---

## 3. Decision For This Implementation Generation

### Final decision

For the next LumenX implementation generation:

> We should **design with Platform/Gateway explicitly in mind**, but **not force a full split from backend in phase 1**.

This gives us flexibility without increasing migration cost too early.

### What this means practically

Phase 1 may still keep existing backend behavior like:

- `dashscope`
- `vendor`

But the schema and runtime overlay should now be documented such that:

- a future `gateway` field can be added without conceptual conflict
- a future sponsored platform can be represented without inventing a new schema from scratch

In plain words:

> We should reserve the abstraction now, but only operationalize it where it gives immediate value.

---

## 4. Recommended Schema Position

The correct place for Platform/Gateway in the long-term model is:

```yaml
mode:
  supported_backends:
    - dashscope
    - vendor
    - sponsor_platform
  default_backend: dashscope
  runtime:
    dashscope:
      gateway: dashscope
      adapter: bailian
      request_profile: dashscope_kling_i2v
      model_name: ...
    vendor:
      gateway: vendor_direct
      adapter: origin_kling
      request_profile: vendor_kling_i2v
      model_name: ...
    sponsor_platform:
      gateway: sponsor_platform
      adapter: sponsor_gateway
      request_profile: sponsor_platform_kling_i2v
      model_name: ...
```

### Interpretation

- `supported_backends` remains the routing contract
- `runtime.<backend>.gateway` describes the actual platform abstraction
- `runtime.<backend>.adapter` describes which local code path executes it

This keeps the model coherent:

- routing still happens by backend
- platform identity becomes visible in runtime metadata
- consumers that do not care can ignore `gateway`

---

## 5. Why Not Split Backend And Gateway Fully In Phase 1

Because doing so immediately would create migration cost that is not yet justified.

### It would force changes to:

- current provider registry logic
- environment variable naming strategy
- validation contracts
- existing YAML family definitions
- current frontend assumptions

### And it would solve a future problem before it is operationally needed

That is not good first-phase engineering.

### Therefore the right strategy is:

1. keep `backend` as current routing key
2. add `gateway` as runtime metadata shape in the design
3. only start consuming it when a true multi-family sponsored platform is introduced

---

## 6. LumenX-Specific Recommendation

For LumenX implementation, we should treat Platform/Gateway as:

- **designed-in**
- **schema-reserved**
- **runtime-optional in phase 1**

### Concretely

LumenX phase 1 does **not** need:

- frontend UI for gateway selection
- persistent project-level gateway state
- provider registry rewrite to use gateway as primary axis

But LumenX phase 1 **should**:

- allow runtime overlay to grow a `gateway` field later
- avoid hardcoding assumptions that `backend == provider == family owner`
- keep family/provider/backend relationships loose enough for a future sponsored platform

---

## 7. Example Future Scenarios

### Scenario A — Simple sponsored backend

A sponsor exposes:

- one API key
- one task submission contract
- one polling contract
- support for Kling and Vidu

Then we can model:

```yaml
runtime:
  sponsor_platform:
    gateway: sponsor_platform
    adapter: sponsor_gateway
    request_profile: sponsor_vidu_i2v
    model_name: sponsor/viduq3-pro
```

This requires no schema redesign.

### Scenario B — Same gateway, different upstream paths

A sponsor platform can execute:

- hosted models
- upstream vendor proxies

Then we can model:

```yaml
runtime:
  sponsor_platform:
    gateway: sponsor_platform
    adapter: sponsor_gateway
    upstream_route: hosted
    request_profile: sponsor_kling_i2v
```

or:

```yaml
runtime:
  sponsor_vendor_proxy:
    gateway: sponsor_platform
    adapter: sponsor_gateway
    upstream_route: vendor_proxy
    request_profile: sponsor_vendor_kling_i2v
```

Again, this extends runtime metadata without needing a new top-level abstraction.

---

## 8. Validation Implications

Once `gateway` becomes an actual runtime field, validation should add these rules:

1. if `runtime.<backend>.gateway` is present, it must be a non-empty string
2. the same gateway may appear under multiple backends
3. consumers may not assume `gateway == backend`
4. runtime-aware tooling may group backends by gateway for diagnostics or docs

Phase 1 does not need to enforce these rules yet in code, but the design should allow them cleanly.

---

## 9. Final Recommendation

### What to do now

- Keep current backend routing model for LumenX phase 1
- Explicitly reserve `gateway` as part of runtime overlay design
- Avoid schema decisions that would make `backend == platform` a permanent assumption

### What not to do now

- Do not redesign all current YAML around gateway-first routing
- Do not add product-level gateway configuration UI
- Do not rewrite provider registry around gateway-first logic yet

### One-sentence summary

> In this implementation generation, treat Platform/Gateway as a deliberate runtime extension point, not as a mandatory first-phase routing axis.

