# LumenX Unified Model Catalog Phase 1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade LumenX’s `model_catalog` to a mode-aware, future gateway-ready source schema while preserving all current frontend/backend consumer behavior and stored project compatibility.

**Architecture:** Phase 1 is a compatibility-preserving source-schema upgrade, not a consumer rewrite. The catalog builder should learn a richer internal model — family, model line, mode, backend, gateway-ready runtime metadata, alias mapping — while continuing to emit today’s flat compatibility artifact for existing frontend/backend consumers. This phase also reserves `runtime.<backend>.gateway` as a first-class extension point, but does not make gateway the active routing axis yet.

**Tech Stack:** YAML source catalog, generated JSON artifact, Python builder/validator utilities under `src/utils/`, existing backend routing layer, frontend compatibility consumer in `frontend/src/lib/modelCatalog.ts`, pytest, Vitest, TypeScript.

---

## Implementation boundaries for Phase 1

This implementation **must** preserve:

- existing flat default model IDs:
  - `wan2.6-t2i`
  - `wan2.6-image`
  - `wan2.6-i2v`
- existing frontend selectors and fallback behavior
- current hidden route model behavior for R2V
- current backend family routing through `src/utils/provider_registry.py`
- current saved project compatibility

This implementation **must not**:

- rewrite frontend consumers to canonical mode IDs
- migrate stored project JSON shape
- make gateway the primary routing axis
- rewrite all runtime adapters

---

### Task 1: Freeze the current compatibility contract with regression tests

**Files:**
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/tests/test_model_catalog.py`
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/__tests__/model-catalog.test.ts`

**Step 1: Add a backend test that locks the current generated compatibility shape**

Add assertions for the current generated artifact to confirm it still contains:

- `defaults.model_settings.t2i_model`
- `defaults.model_settings.i2i_model`
- `defaults.model_settings.i2v_model`
- flat `models` map keyed by legacy IDs
- current visible surfaces and current default model IDs

**Step 2: Add a backend test that defines the new phase-1 additive contract**

Add a failing test that expects the generated catalog to also expose new additive sections such as:

- `model_lines`
- `modes`
- `compat.legacy_model_ids`

The test must assert that:

- old flat IDs still exist in `models`
- a legacy ID resolves to a canonical mode-aware ID in `compat.legacy_model_ids`

**Step 3: Run the targeted backend test file to verify the new additive test fails first**

Run:

```bash
pytest tests/test_model_catalog.py -q
```

Expected:

- existing compatibility tests pass
- the new additive-contract test fails because `model_lines`, `modes`, or `compat` are not emitted yet

**Step 4: Add a frontend test that locks existing selector behavior**

Extend the current frontend test file to assert that:

- visible model lists still use legacy IDs
- `resolveModelSettings()` continues to normalize old or missing IDs to current visible defaults
- `R2V_SELECTION_MODEL_ID` and `R2V_ROUTE_MODEL_ID` still map to current legacy values

**Step 5: Run the targeted frontend compatibility tests**

Run:

```bash
cd /Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend
npm run test -- src/__tests__/model-catalog.test.ts
```

Expected:

- PASS

---

### Task 2: Introduce an internal mode-aware normalization layer in the Python builder

**Files:**
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/model_catalog.py`
- Test: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/tests/test_model_catalog.py`

**Step 1: Add internal helper structures for normalized family / model line / mode data**

Inside `src/utils/model_catalog.py`, add internal normalization helpers that can represent:

- family
- model line
- mode
- runtime backend block
- product block
- canonical mode ID
- legacy alias ID

Do not change the public generated artifact yet.

**Step 2: Teach the builder to accept both source forms**

Support two authoring forms during transition:

1. current flat model entry form
2. new model-line-with-`modes` form

The builder should normalize both forms into the same internal representation.

**Step 3: Add runtime gateway awareness to the internal representation**

Allow normalized runtime backend blocks to optionally carry:

- `gateway`

This field should be accepted and preserved in internal normalization even if no current consumer uses it yet.

**Step 4: Keep family-level transport and routing metadata unchanged**

Preserve current handling for:

- `routing_prefixes`
- `transport.image_input_mode`
- `transport.audio_input_mode`
- `transport.reference_video_input_mode`

These remain compatibility-supporting fields in phase 1.

**Step 5: Re-run backend tests**

Run:

```bash
pytest tests/test_model_catalog.py -q
```

Expected:

- the new additive-contract test should still fail if generated output is not emitted yet
- existing structural tests should still pass

---

### Task 3: Emit a dual-track generated artifact

**Files:**
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/model_catalog.py`
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/scripts/build_model_catalog.py`
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/scripts/validate_model_catalog.py`
- Test: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/tests/test_model_catalog.py`

**Step 1: Extend `build_catalog_dict()` to emit additive canonical sections**

Without removing the current compatibility output, extend the generated dict to also include:

- `model_lines`
- `modes`
- `compat.legacy_model_ids`
- optionally `canonical_defaults` if helpful internally

Required rule:

- the existing `models` map must remain keyed by legacy flat IDs in phase 1

**Step 2: Define canonical mode IDs**

Pick one canonical internal/generated mode ID shape and use it consistently, for example:

```text
<model_line_id>#<mode>
```

Examples:

- `wan/wan2.6-video#line-i2v` is **not** recommended because it embeds label noise
- prefer:
  - `wan/wan2.6-video#i2v`
  - `wan/wan2.6-video#r2v`

**Step 3: Emit explicit alias mapping**

Generate `compat.legacy_model_ids` so mappings become explicit instead of implicit.

Examples:

- `wan2.6-i2v -> wan/wan2.6-video#i2v`
- `wan2.6-r2v -> wan/wan2.6-video#r2v`
- `kling-v3 -> kling/kling-v3-video-generation#i2v`

**Step 4: Keep default model settings flat**

Ensure:

- `defaults.model_settings` still exposes flat compatibility IDs
- canonical defaults, if emitted, are additive and do not replace current keys

**Step 5: Update validation report logic**

Extend `build_catalog_validation_report()` so it additionally checks:

- every legacy alias resolves to a generated canonical mode ID
- canonical defaults, if emitted, point to real generated modes
- current product defaults still point to visible flat compatibility entries

**Step 6: Update the build script output text if needed**

If the generated output shape becomes richer, make sure `scripts/build_model_catalog.py` still communicates the correct next step:

- build
- then validate

**Step 7: Re-run the failing backend tests**

Run:

```bash
pytest tests/test_model_catalog.py -q
```

Expected:

- the previously failing additive-contract test now passes
- all existing compatibility tests still pass

---

### Task 4: Pilot the new source schema on the lowest-risk catalog entries

**Files:**
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/wan.yaml`
- Optionally modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/kling.yaml`
- Optionally modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/vidu.yaml`
- Optionally modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/pixverse.yaml`
- Test: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/tests/test_model_catalog.py`

**Step 1: Convert only the highest-value line first**

Pilot the mode-aware source shape on the strongest LumenX candidate first:

- Wan video line

Reason:

- it already expresses both `i2v` and `r2v`
- it already has visible selection vs hidden route behavior
- it is the clearest case for mode-first modeling

**Step 2: Keep flat IDs as aliases, not primary source truth**

For the Wan pilot:

- define a model line
- define `modes.i2v`
- define `modes.r2v`
- preserve flat compatibility IDs through generated aliases

Do not remove:

- `wan2.6-i2v`
- `wan2.6-r2v`

from generated compatibility outputs.

**Step 3: Add reserved runtime gateway metadata only where it helps**

For pilot source entries, allow:

```yaml
runtime:
  dashscope:
    gateway: dashscope
```

Do not force gateway on every existing entry yet.

**Step 4: Rebuild the catalog and verify generated output**

Run:

```bash
python /Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/scripts/build_model_catalog.py
python /Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/scripts/validate_model_catalog.py
```

Expected:

- PASS
- current frontend mirror stays in sync
- alias map contains the pilot mode mappings

**Step 5: Only convert additional families if Wan pilot stays stable**

If Wan mode-aware source conversion remains clean and low-risk, optionally convert:

- Kling
- Vidu

in the same source-shape style.

Do **not** convert additional families in the same pass if:

- it starts forcing frontend rewrites
- it complicates validation too early

---

### Task 5: Preserve current frontend consumption shape exactly

**Files:**
- Verify only: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/lib/modelCatalog.ts`
- Verify only: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/store/projectStore.ts`
- Verify only: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/common/ModelSettingsModal.tsx`
- Verify only: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/series/SeriesModelSettingsModal.tsx`
- Verify only: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/settings/SettingsPage.tsx`
- Verify only: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/VideoSidebar.tsx`
- Test: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/__tests__/model-catalog.test.ts`

**Step 1: Do not rewrite frontend lookups to canonical mode IDs**

Confirm the generated artifact still provides all fields the frontend currently consumes:

- `defaults.model_settings.*`
- `models[legacy_id]`
- `ui.selection_group`
- `ui.visible_in`
- `duration`
- `params`
- `inputs.reference_images.max`

**Step 2: Ensure R2V helper behavior still works**

Specifically verify:

- `R2V_SELECTION_MODEL_ID`
- `R2V_ROUTE_MODEL_ID`

still resolve to current legacy IDs in the frontend consumer.

**Step 3: Run the frontend targeted tests**

Run:

```bash
cd /Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend
npm run test -- src/__tests__/model-catalog.test.ts
npm run typecheck
```

Expected:

- PASS

---

### Task 6: Keep provider routing and transport stable while adding future-ready metadata

**Files:**
- Modify minimally if needed: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/provider_registry.py`
- Test: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/tests/test_model_catalog.py`

**Step 1: Preserve current provider registry behavior**

`src/utils/provider_registry.py` should continue to resolve families using:

- current routing prefixes
- current backend env key overrides

**Step 2: Do not switch routing to gateway-first**

Even if the normalized catalog now carries `runtime.<backend>.gateway`, do not make provider resolution depend on gateway in phase 1.

**Step 3: Add tests that prove gateway metadata is additive**

Add a backend test that confirms:

- `gateway` may appear in normalized runtime metadata
- provider family config derivation still works without consuming it

**Step 4: Re-run targeted backend tests**

Run:

```bash
pytest tests/test_model_catalog.py -q
```

Expected:

- PASS

---

### Task 7: Run full verification and document the phase-1 boundary

**Files:**
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/model-onboarding-implementation.md`
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/README.md`
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/README_EN.md`
- Modify if needed: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/CONTRIBUTING.md`

**Step 1: Update implementation docs after code is stable**

Explain clearly that phase 1 introduced:

- mode-aware internal structure
- canonical mode IDs
- explicit alias mapping
- reserved runtime gateway field

But did **not** introduce:

- gateway-first routing
- frontend canonical ID rewrite
- saved project format migration

**Step 2: Run backend verification**

Run:

```bash
cd /Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic
pytest -q
```

Expected:

- PASS

**Step 3: Run frontend verification**

Run:

```bash
cd /Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend
npm run typecheck
npm run test:all
npm run build
```

Expected:

- PASS

**Step 4: Run catalog verification**

Run:

```bash
cd /Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic
python scripts/build_model_catalog.py
python scripts/validate_model_catalog.py
```

Expected:

- PASS
- generated backend and frontend artifacts stay in sync
- validation report confirms defaults, surfaces, alias integrity, and documentation linkage

**Step 5: Commit after all verification passes**

Suggested commit slices:

1. tests and builder normalization
2. source schema pilot conversion
3. docs update

Do not squash unrelated work into this phase-1 branch if the goal is to keep reviewable boundaries.

