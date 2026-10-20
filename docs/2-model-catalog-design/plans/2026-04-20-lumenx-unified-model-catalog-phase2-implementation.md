# LumenX Unified Model Catalog Phase 2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move LumenX from a Phase 1 additive model-catalog foundation to a Phase 2 consumer-aware model system, where backend/frontend consumers begin to meaningfully use canonical mode-aware metadata while preserving flat compatibility IDs, existing UI behavior, and current saved project compatibility.

**Architecture:** Phase 2 is still a compatibility-first evolution, not a big-bang migration. The key shift is that the app should stop treating `model_lines`, `modes`, and `compat` as passive additive metadata and start treating them as a real internal source of truth for normalization, runtime metadata access, route-vs-selection behavior, and future platform/gateway growth. Flat IDs remain the persisted and consumer-facing compatibility layer unless and until a later dedicated migration phase is approved.

**Tech Stack:** YAML/JSON model catalog, Python builder/validator (`src/utils/model_catalog.py`), provider routing (`src/utils/provider_registry.py`), TypeScript frontend catalog adapter (`frontend/src/lib/modelCatalog.ts`), pytest, Vitest, TypeScript.

---

## Phase 2 implementation guardrails

This phase **must preserve**:

- existing flat default IDs:
  - `wan2.6-t2i`
  - `wan2.6-image`
  - `wan2.6-i2v`
- existing saved `model_settings` compatibility
- current route-model vs selection-model behavior
- current frontend visible selector behavior
- current no-OSS / provider-routing behavior

This phase **must not**:

- require project data migration
- switch frontend UI controls to canonical IDs
- make gateway the primary runtime routing axis
- replace existing provider registry with a full mode-runtime router
- remove compatibility maps

---

### Task 1: Strengthen the generated contract and freeze it with higher-order tests

**Files:**
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/tests/test_model_catalog.py`
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/__tests__/model-catalog.test.ts`

**Step 1: Add backend tests that treat additive metadata as part of the official generated contract**

Expand backend catalog tests to verify:

- `model_lines` is emitted for all converted or normalized model lines
- `modes` is emitted for all executable modes
- `compat.legacy_model_ids` is emitted and complete for visible and route models
- `defaults.canonical_model_settings` exists and resolves to valid canonical modes

**Step 2: Add backend tests for runtime gateway metadata**

Add tests confirming:

- `runtime.<backend>.gateway` may exist in generated canonical modes
- gateway presence is additive
- current family/provider routing still works without gateway-first logic

**Step 3: Add frontend tests that use canonical metadata as internal truth**

The frontend test file should verify that:

- canonical mode IDs can normalize back to flat compatibility IDs
- route-model and selection-model behavior is derived from the canonical mode data
- canonical mode metadata does not leak into visible flat model selectors unless intentionally surfaced

**Step 4: Run targeted tests**

Run:

```bash
pytest tests/test_model_catalog.py -q
cd /Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend
npm run test -- src/__tests__/model-catalog.test.ts
```

Expected:

- PASS

---

### Task 2: Promote canonical mode metadata to first-class internal helpers in Python

**Files:**
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/model_catalog.py`
- Test: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/tests/test_model_catalog.py`

**Step 1: Add explicit helper APIs for canonical mode lookups**

Add backend utility helpers such as:

- resolve legacy flat ID -> canonical mode ID
- resolve canonical mode ID -> compatibility flat ID
- resolve default flat IDs -> canonical default mode IDs
- read runtime metadata for a canonical mode
- read product metadata for a canonical mode

These should be stable internal helpers, not just ad-hoc dict indexing.

**Step 2: Keep compatibility outputs, but stop treating them as the only internal truth**

Inside Python helpers, use:

- canonical mode entries as internal model execution metadata
- compatibility flat IDs as external-facing compatibility layer

**Step 3: Expose gateway metadata via helpers without changing active routing**

Introduce helper-level access to:

- `runtime.<backend>.gateway`

But do **not** change `resolve_provider_backend()` to gateway-first logic in this phase.

**Step 4: Re-run targeted backend tests**

Run:

```bash
pytest tests/test_model_catalog.py -q
python scripts/build_model_catalog.py
python scripts/validate_model_catalog.py
```

Expected:

- PASS

---

### Task 3: Make frontend `modelCatalog.ts` consume canonical metadata internally

**Files:**
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/lib/modelCatalog.ts`
- Test: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/__tests__/model-catalog.test.ts`

**Step 1: Extend the frontend catalog type definitions**

Teach the frontend catalog adapter about:

- `model_lines`
- `modes`
- `compat`
- `canonical_model_settings`

without breaking current flat-ID selectors.

**Step 2: Move route/selection helper logic to canonical mode-aware internals**

Refactor helper logic so that:

- visible selectors still emit flat compatibility IDs
- canonical mode metadata is used for internal reasoning
- route-model vs selection-model logic can be expressed against canonical data, then mapped back to flat IDs

**Step 3: Preserve current public behavior**

The following exported values must remain compatible:

- `DEFAULT_MODEL_SETTINGS`
- `resolveModelSettings()`
- `resolveModelId()`
- `PROJECT_*_MODELS`
- `GLOBAL_*_MODELS`
- `VIDEO_I2V_MODELS`
- `R2V_SELECTION_MODEL_ID`
- `R2V_ROUTE_MODEL_ID`

**Step 4: Add a dedicated frontend test for canonical-internal / flat-external behavior**

Examples to verify:

- input canonical ID -> output flat compatibility ID
- hidden route mode still maps correctly to the visible compatibility route helper
- generated selectors do not start leaking canonical IDs into current UI model lists

**Step 5: Run targeted frontend checks**

Run:

```bash
cd /Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend
npm run typecheck
npm run test -- src/__tests__/model-catalog.test.ts
```

Expected:

- PASS

---

### Task 4: Improve product/runtime overlay separation in source YAML without widening scope too far

**Files:**
- Modify selectively: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/wan.yaml`
- Modify selectively: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/kling.yaml`
- Modify selectively: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/vidu.yaml`
- Optionally modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/pixverse.yaml`

**Step 1: Normalize the Wan pilot source shape**

Make sure the Wan pilot mode-aware source is clean and representative:

- canonical line ID
- mode blocks
- route-mode metadata
- gateway metadata where useful

**Step 2: Upgrade one or two additional families only if it reduces future ambiguity**

Recommended next candidates:

- Kling
- Vidu

but only if:

- the migration is small
- the source shape becomes clearer
- generated compatibility outputs remain stable

**Step 3: Do not over-convert just to be “consistent”**

It is acceptable for some families to still be in transitional form if:

- canonical generation remains stable
- validation stays strong

**Step 4: Rebuild and validate**

Run:

```bash
python scripts/build_model_catalog.py
python scripts/validate_model_catalog.py
```

Expected:

- PASS

---

### Task 5: Introduce consumer-visible inspection/debug affordances for canonical metadata

**Files:**
- Modify minimally if helpful: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/settings/SettingsPage.tsx`
- Modify minimally if helpful: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/model-onboarding-implementation.md`
- Optionally modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/README.md`

**Step 1: Add low-noise developer-facing visibility into canonical metadata**

This does **not** mean exposing canonical IDs to ordinary users everywhere.

It means optionally exposing:

- model line ID
- canonical mode ID
- backend/gateway metadata

in a controlled debug / advanced / implementation-facing surface or documentation.

**Step 2: Update implementation documentation**

Document clearly:

- what Phase 2 means
- what is still compatibility-facing
- what is now canonical-internal
- how to reason about flat IDs vs canonical mode IDs

**Step 3: Keep user-facing UI simple**

Do not flood normal settings pages with canonical IDs unless there is a concrete product need.

---

### Task 6: Keep provider routing stable, but prepare for future platform/gateway growth

**Files:**
- Modify minimally if needed: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/provider_registry.py`
- Optionally add tests: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/tests/test_provider_registry.py`

**Step 1: Preserve current runtime routing behavior**

Provider routing should continue to operate correctly for:

- DashScope-first
- vendor fallback
- current family routing prefixes

**Step 2: Add optional helper(s) to inspect gateway metadata**

Examples:

- get gateway for `(model line, mode, backend)`
- inspect backend metadata for diagnostics

**Step 3: Do not switch to gateway-first routing yet**

Gateway remains:

- explicit in metadata
- available for future sponsor platform work
- not the controlling routing axis in Phase 2

---

### Task 7: Run full verification and prepare the branch for the next product-facing wave

**Files:**
- Modify docs if needed:
  - `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/model-onboarding-implementation.md`
  - `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/README.md`
  - `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/README_EN.md`
  - `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/CONTRIBUTING.md`

**Step 1: Run backend verification**

Run:

```bash
cd /Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic
pytest -q
```

Expected:

- PASS

**Step 2: Run frontend verification**

Run:

```bash
cd /Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend
npm run typecheck
npm run test:all
npm run build
```

Expected:

- PASS

**Step 3: Run catalog verification**

Run:

```bash
cd /Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic
python scripts/build_model_catalog.py
python scripts/validate_model_catalog.py
```

Expected:

- PASS

**Step 4: Record what remains intentionally deferred**

Document that Phase 2 still does **not** do:

- gateway-first active routing
- project data migration
- canonical-ID-only frontend settings

**Step 5: Commit**

Recommended commit boundary:

- one semantic Phase 2 commit if diff remains coherent
- or split into:
  - backend/catalog contract evolution
  - frontend canonical-internal compatibility layer
  - docs update

