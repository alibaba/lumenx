---
name: lumenx-model-onboarding
description: LumenX model onboarding workflow for vendor doc capture, model catalog updates, adapter/UI scope checks, and end-to-end verification.
---

# LumenX Model Onboarding Workflow

Use this workflow when working in this repository and the user asks to:

- onboard a new model or model family into LumenX
- update model docs, versions, defaults, parameters, or UI exposure
- refresh catalog definitions for the Wan, Kling, Vidu, or PixVerse model families
- review whether a model change is catalog-only or requires runtime adapter work
- run `/lumenx-model-onboarding`

This workflow is the repo-native entrypoint for model support work. It keeps the process observable, testable, and reviewable.

## Supported Change Types

- **Doc refresh only**: update source links, release notes, or integration notes with no runtime change.
- **Catalog-only**: add or update model IDs, defaults, status, capabilities, durations, params, docs, or UI visibility entirely inside `config/model_catalog/`.
- **Catalog + frontend**: a catalog change also changes what the settings UI or video UI should expose.
- **Catalog + runtime adapter**: the model needs a new provider transport mode, request parameter, endpoint behavior, auth mode, or response parsing logic.
- **New provider family**: add a new family and wire routing, media transport, validation, and UI support end-to-end.

## Required Inputs

Before making changes, collect or infer these inputs:

- provider name
- model family
- model IDs involved
- change type from the list above
- source doc URLs
- whether the change affects:
  - default model selection
  - provider backend routing
  - input transport
  - request params
  - frontend visibility

If the request is underspecified, classify what is safe to do now and what needs user confirmation.

## Repo Responsibilities

This repository owns:

- executable model support via `config/model_catalog/`
- backend catalog loading and provider routing
- frontend model lists/defaults driven by generated catalog data
- repo-local workflow docs, verification scripts, and implementation docs

This repository does **not** own the canonical raw vendor-doc archive or the shared Context Hub package. Those should live in dedicated locations outside this repo.

## Documentation Modes

### Mode A: Full multi-repo flow

Use when the raw vendor-doc archive repo and the Context Hub source package are available in the workspace.

1. Capture or refresh the raw vendor docs in the archive repo.
2. Promote integration-critical facts into the Context Hub source docs.
3. Update `model_catalog` in this repo.

### Mode B: Repo-only flow

Use when only this repo is available.

1. Capture source evidence into `docs/1-api-reference/` as a repo-local staging mirror.
2. Update `model_catalog` here.
3. Explicitly note in the implementation doc or PR that the raw archive repo and Context Hub package still need promotion.

Mode B is acceptable for implementation work, but do not pretend it completed the cross-repo sync.

## Phase 1: Capture Documentation Evidence

Preferred tool:

- use the `url-to-markdown` or equivalent URL capture workflow for source pages

Preferred destination:

- canonical raw-doc archive repo outside this repository

Repo-local fallback:

- `docs/1-api-reference/<provider>-<topic>.md`

Minimum evidence requirements:

- original source URL
- capture date
- provider/family/model scope
- any release date or version note mentioned in the docs

## Phase 2: Decide Scope Before Coding

Classify the request into one of these buckets:

- **Catalog-only**
  - model ID changes
  - default changes
  - durations / params / badges / visibility / status
  - docs linkage
- **Needs frontend follow-up**
  - the model should appear or disappear in settings/video UI
  - a new parameter needs a control in the UI
- **Needs runtime follow-up**
  - auth mode changes
  - request payload changes
  - media transport mode changes
  - polling / response shape changes
  - a new provider family is introduced

Do not force a catalog-only change when the docs clearly imply runtime work.

## Phase 3: Update the Catalog

Primary files:

- `config/model_catalog/catalog.meta.yaml`
- `config/model_catalog/families/*.yaml`

Update at least these fields when relevant:

- `id`
- `display_name`
- `description`
- `status`
- `release_stage`
- `capabilities`
- `docs.official_snapshot_ids`
- `docs.context_hub_doc_ids`
- `ui.selection_group`
- `ui.visible_in`
- `ui.recommended`
- `ui.order`
- `duration`
- `params`
- `inputs`
- family-level `supported_backends`
- family-level `transport`
- family-level `credential_sources`

Rules:

- visible models must carry doc linkage
- defaults must point to real models
- planned/hidden models should not be exposed in UI by accident
- do not parse YAML from the frontend, always regenerate the frontend JSON mirror

## Phase 4: Extend Runtime or Frontend Only When Needed

If runtime behavior changes, inspect and update the appropriate areas:

- `src/utils/provider_registry.py`
- `src/utils/provider_media.py`
- `src/models/`
- `src/apps/comic_gen/models.py`
- request/response handling in provider-specific model wrappers

If UI behavior changes, inspect and update:

- `frontend/src/lib/modelCatalog.ts`
- `frontend/src/store/projectStore.ts`
- relevant settings or motion UI components

Do not touch these layers if the change is truly catalog-only.

## Phase 5: Regenerate Artifacts

Run:

```bash
python scripts/build_model_catalog.py
```

This must update:

- `config/model_catalog/generated/model_catalog.json`
- `frontend/src/generated/modelCatalog.json`
- `config/model_catalog/schema/model-catalog.schema.json`

## Phase 6: Validate the Catalog

Run:

```bash
python scripts/validate_model_catalog.py
```

The validation step must confirm:

- backend and frontend generated artifacts match
- defaults remain valid and visible on the right surfaces
- visible models retain doc linkage

## Phase 7: Run Verification

Run the repo checks required by the scope of your change.

Minimum checks for any catalog change:

```bash
pytest -q
cd frontend && npm run typecheck
cd frontend && npm run test:all
cd frontend && npm run build
```

If the change is narrower, you may run targeted checks first, but do not claim completion without an end-to-end verification pass.

## Stop And Ask The User When

- the docs imply a new provider family or auth contract
- a new transport mode is required
- a new UI control is required and the right UX is not obvious
- the raw archive repo or Context Hub package is unavailable and the user expects a full sync
- the docs are incomplete, contradictory, or behind authentication you cannot access

## Deliverables

A complete onboarding run should leave behind:

- updated docs or evidence capture
- updated catalog YAML
- regenerated backend and frontend artifacts
- updated runtime/frontend code if required
- passing verification output
- a clear note about anything intentionally deferred
