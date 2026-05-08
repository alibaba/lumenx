# LumenX Studio Frontend

This is the Next.js 14 frontend for LumenX Studio. It is normally run together with the FastAPI backend from the repository root.

## Daily Development

From the repository root:

```bash
npm run dev
```

This starts the backend, frontend, and browser helper together. For frontend-only work:

```bash
cd frontend
npm run dev
```

The frontend expects the backend at `http://127.0.0.1:18177` by default. Override it with `NEXT_PUBLIC_LUMENX_API_PORT` or `NEXT_PUBLIC_API_URL` when needed.

Dev startup writes ephemeral `tmp/lumenx-*.json` runtime manifests after port
conflict resolution. Treat them as launch hints only; see
[`docs/runtime-files.md`](../docs/runtime-files.md).

Smoke and dev launchers can also redirect runtime output with
`LUMENX_OUTPUT_DIR`. CI smoke jobs default to an isolated `tmp/e2e-output-*`
directory so failures do not collide with normal `output/` data.

## Quality Gates

Run this before sending frontend changes for review:

```bash
npm run quality
```

It performs:

- `npm run lint:errors`: fails only on ESLint errors.
- `npm run lint:budget`: fails if the warning count grows beyond `lint-warning-budget.json` (currently `0`).
- `npm run typecheck`: runs TypeScript with `noEmit`.
- `npm run test`: runs the Vitest unit/component suite.
- `npm run build`: runs the production Next build with lint and type checks enabled.

Use `npm run lint` during cleanup work to see any new warning backlog. New `any`, unused symbol, image, or Hook dependency warnings should be fixed before review instead of expanding the budget.

## Copy And I18n Audit

Run the copy scanner when touching user-facing text:

```bash
npm run audit:copy
```

`npm run audit:copy:strict` is intentionally stricter and may flag prompt dictionaries or domain vocabularies. Treat strict failures as review signals until the copy allowlist is fully curated.

## Frontend Structure

- `src/app`: Next app entry and global styles.
- `src/components`: feature modules, layout, settings, series, and shared UI.
- `src/lib`: API client, i18n, prompt helpers, and utility code.
- `src/store`: Zustand project state.
- `src/__tests__` and component `*.test.tsx`: Vitest coverage.

## Maintainability Policy

- Keep feature behavior changes covered by focused Vitest tests.
- Prefer typed API/domain helpers in `src/lib` over repeating ad hoc `any` handling inside components.
- Keep ESLint errors and warnings at zero.
- Do not raise `lint-warning-budget.json` without an explicit review note.
- Do not reintroduce `typescript.ignoreBuildErrors` or `eslint.ignoreDuringBuilds` in `next.config.mjs`.
