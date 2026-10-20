# LumenX Model Onboarding System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the current model catalog MVP into a repo-native, end-to-end model onboarding system that covers workflow entry, catalog regeneration, validation, and implementation documentation.

**Architecture:** Keep `model_catalog` as the executable source of truth, then add a repo-native onboarding workflow layer above it. The workflow should explain how to promote vendor docs into Context Hub notes and catalog changes, while local scripts provide deterministic regeneration and validation so onboarding work is observable and repeatable.

**Tech Stack:** Markdown workflow docs, Python validation scripts, YAML/JSON model catalog, FastAPI/Next.js repo docs, pytest, Vitest, TypeScript.

---

### Task 1: Add repo-native model onboarding workflow entry

**Files:**
- Create: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/.codex/workflows/lumenx-model-onboarding.md`
- Create: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/.claude/commands/lumenx-model-onboarding.md`
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/AGENTS.md`

**Step 1: Write the workflow docs**

Document:

- when `/lumenx-model-onboarding` should be used
- supported job types: new model, parameter update, default switch, doc refresh, UI exposure change
- required inputs
- the exact execution phases
- stop-and-ask conditions
- required verification steps

**Step 2: Mirror workflow behavior across Codex and Claude docs**

Ensure the Codex workflow and Claude command carry the same process and repo rules.

**Step 3: Add AGENTS routing**

Teach the repo that requests like “接入新模型”, “更新模型文档”, “运行模型接入工作流”, and `/lumenx-model-onboarding` should load the new workflow.

### Task 2: Add deterministic validation and reporting utilities

**Files:**
- Create: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/scripts/validate_model_catalog.py`
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/scripts/build_model_catalog.py`
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/tests/test_model_catalog.py`

**Step 1: Add a validation/report script**

The script should:

- load the canonical generated catalog
- load the frontend generated mirror
- verify they are identical
- verify default models exist and are visible on intended surfaces
- verify visible models carry doc linkage
- print a human-readable summary
- exit non-zero on validation failure

**Step 2: Keep build and validate responsibilities separate**

`build_model_catalog.py` should only build artifacts. Validation belongs in the new script.

**Step 3: Add tests**

Cover the new validation expectations at the Python level where practical.

### Task 3: Add implementation-facing documentation

**Files:**
- Create: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/model-onboarding-implementation.md`
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/plans/2026-04-03-model-docs-and-catalog-architecture.md`
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/README.md`
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/README_EN.md`
- Modify: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/CONTRIBUTING.md`

**Step 1: Write implementation doc**

The implementation doc should explain, in plain engineering language:

- what each onboarding-related file does
- what changed in the MVP
- what remains intentionally manual
- how to add or update a model safely
- which changes are catalog-only vs adapter-level

**Step 2: Sync design doc to reality**

Update the design doc to reflect:

- repo-native onboarding workflow exists
- frontend uses a generated local mirror
- validation/reporting now has a concrete script entrypoint

**Step 3: Update top-level docs**

Add short, clear developer-facing pointers in README and CONTRIBUTING so future contributors know where to start.

### Task 4: Verify the full onboarding system path

**Files:**
- Test: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/tests/test_model_catalog.py`
- Test: `/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/__tests__/model-catalog.test.ts`

**Step 1: Run backend tests**

Run: `pytest -q`
Expected: PASS

**Step 2: Run frontend checks**

Run: `cd frontend && npm run typecheck`
Expected: PASS

Run: `cd frontend && npm run test:all`
Expected: PASS

Run: `cd frontend && npm run build`
Expected: PASS

**Step 3: Run catalog build + validate commands**

Run: `python scripts/build_model_catalog.py`
Expected: PASS with backend and frontend artifact paths printed.

Run: `python scripts/validate_model_catalog.py`
Expected: PASS with a readable validation summary.
