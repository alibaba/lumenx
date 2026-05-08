# Quality Gates

This project uses a practical gate model: errors block delivery, warning debt is tracked explicitly, and high-signal tests run before build artifacts are trusted.

## Frontend Gate

Run from the repository root:

```bash
npm run quality:frontend
```

Or from `frontend/`:

```bash
npm run quality
```

The frontend gate includes:

- ESLint error gate: `npm run lint:errors`
- ESLint warning budget: `npm run lint:budget`
- TypeScript gate: `npm run typecheck`
- Unit/component tests: `npm run test`
- Production build: `npm run build`

The production build must keep lint and TypeScript checks enabled. Do not set `eslint.ignoreDuringBuilds` or `typescript.ignoreBuildErrors` in `frontend/next.config.mjs`.

## CI / PR Required Checks

Pull requests and pushes to `main` run `.github/workflows/ci.yml`.

The workflow exposes these quality-gate jobs that should be configured as required status checks in GitHub branch protection:

- `frontend-quality-gate`: runs `npm run quality:frontend`
- `backend-quality-gate`: runs `python -m pytest -q -m "not e2e"`
- `browser-e2e-smoke`: runs the split browser smoke pytest suite with `LUMENX_RUN_BROWSER_E2E=1`; each pytest case invokes `ci:dev-smoke`, so it still verifies port conflict handling, root `npm run dev`, and the real browser flow.

When `browser-e2e-smoke` fails, CI uploads the `browser-e2e-smoke-summary-screenshots` artifact. Open `browser-smoke-*.json` first: it includes `projectIds`, `backendUrl`, `frontendUrl`, `lastEndpoint`, dialog messages, and the screenshot path. The same artifact also includes `browser-e2e-smoke-failure.png` and the preserved `tmp/e2e-output-*` runtime directory.

Do not split these into weaker PR checks. The frontend job must keep lint, warning budget, TypeScript, Vitest, and production build in one required path.

## Warning Policy

The frontend warning budget is currently `0`. Warnings are not hidden: `npm run lint` prints any new backlog, and `npm run lint:budget` fails as soon as warning count exceeds `frontend/lint-warning-budget.json`.

Keep the budget at `0` unless a warning is explicitly reviewed as temporary debt. Prefer typed API/domain helpers in `src/lib` over local `any` patches, and use `NextImage` for image previews that are part of normal UI rendering.

## Backend Gate

Run from the repository root:

```bash
pytest -q
```

Backend tests cover provider routing, media references, config masking, project import, storyboard generation, audio/export behavior, security boundaries, and cross-phase flows. Security-boundary tests are part of the final backend gate; use targeted `-k` runs only for local diagnosis, not as the PR required command.

## Story Fixture Audit

Run from the repository root:

```bash
npm run audit:story-fixtures
```

This scans `tests/fixtures/story_projects` (excluding `_templates`) and cross-checks each fixture's `project_manifest.json`, `references/`, and `output/uploads/fixtures/` copies. It also enforces explicit `visual_gate` wording and file-completion wording where static export manifests are described.

## Smoke Regression Baselines

Fixture import, export transcoding, and series asset import changes must keep these smoke paths green as part of the full backend gate:

- `tests/test_e2e_smoke.py`
- `tests/test_prompt_quality_e2e.py`
- `tests/test_export_manager.py::test_smoke_render_project_transcodes_video_and_returns_subtitle`
- `tests/test_series.py::TestImportAssetsFromSeries::test_smoke_import_assets_from_series_deep_copies_selected_assets`

These tests are regression baselines, not optional subsets. Browser tests are skipped locally unless `LUMENX_RUN_BROWSER_E2E=1` is set; in CI they run in the dedicated `browser-e2e-smoke` job. Use targeted runs while diagnosing a failure, but finish PR validation with the relevant full gate.

## Generation Provenance Gate

AI-facing generation paths must keep provenance visible. Backend responses should preserve `generation_source`, `generation_degraded`, and `generation_reason` whenever a result comes from an LLM, mock, fallback, or heuristic draft path.

The frontend must surface degraded provenance on high-trust result screens: project cards, storyboard frames, video generation results, merged video preview, and export completion. A visible `Mock / 降级`, `Fallback / 降级`, or similar badge means the artifact is useful for local testing but should not be treated as final production output without regeneration.

## Suggested PR Checklist

- Frontend-only change: `npm run quality:frontend`
- Backend-only change: `pytest -q`
- Cross-stack change: `pytest -q` and `npm run quality:frontend`
- User-facing text change: `npm -C frontend run audit:copy`
- Story fixture or template change: `npm run audit:story-fixtures`

Strict copy audit is available with:

```bash
npm -C frontend run audit:copy:strict
```

It is intentionally conservative and may flag prompt dictionaries or domain vocabularies until the allowlist is fully curated.
