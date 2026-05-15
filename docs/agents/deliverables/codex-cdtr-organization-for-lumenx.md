# Codex CDTR Organization For LumenX

## Purpose

Use CDTR to keep Codex-created coordination artifacts organized without disturbing the executable repository layout.

This repository is a runnable product. Source code, tests, configuration, workflows, scripts, generated catalog artifacts, and runtime data must stay in their existing project locations.

## Directory Rules

- `docs/agents/context/` stores user-provided inputs, scraped references, requirements, screenshots, and source materials.
- `docs/agents/deliverables/` stores final user-facing outputs that are meant to be reused.
- `docs/agents/raw/` stores drafts, intermediate analysis, experiments, and scratch notes.
- `docs/agents/tools/` stores helper scripts created for agent workflow support.

## Boundary

CDTR applies only to new non-source artifacts created during Codex work.

Do not move, wrap, duplicate, or reorganize files from these executable project areas:

- `src/`
- `frontend/`
- `config/`
- `scripts/`
- `tests/`
- `.codex/workflows/`
- package, lock, environment, build, or runtime data files

If a file is part of the product implementation, keep it in the existing source tree.

## Promotion Rules

- A raw draft can be promoted to `deliverables/` only when it is the accepted final version.
- A helper in `docs/agents/tools/` can be promoted to `scripts/` only when it becomes part of the normal project maintenance workflow.
- External articles and scraped references should stay in `context/` or in the user's global notes area; project docs should link or summarize rather than duplicate large source materials.

## Codex Workflow

1. Put user-provided materials in `context/`.
2. Put working notes and exploratory analysis in `raw/`.
3. Put the final reusable artifact in `deliverables/`.
4. Put reusable agent helper scripts in `tools/`.
5. Keep product code and tests in the normal repository structure.

When unsure whether a new file is a source file or an agent artifact, ask before creating it.
