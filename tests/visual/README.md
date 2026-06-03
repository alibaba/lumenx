# Visual regression harness

Captures Atelier canvas screenshots at fixed viewports and compares them against golden references.

## Usage

```bash
# First run (or update golden after intentional visual changes):
node tests/visual/run-visual-regression.mjs --update-golden

# Subsequent runs (compare against golden):
node tests/visual/run-visual-regression.mjs
```

## Prerequisites

- Dev server at `http://localhost:3009/#/atelier` (worktree)
- Backend at `http://localhost:17177`
- Playwright: `/opt/homebrew/lib/node_modules/playwright`
- An Atelier project with nodes (the script captures whatever is on the canvas)

## Configuration

| Env var | Default | Description |
|---|---|---|
| `ATELIER_URL` | `http://localhost:3009` | Dev server URL |
| `VISUAL_THRESHOLD` | `0.005` | Max byte-delta ratio (0.5%) before failing |

## Directory structure

```
tests/visual/
├── README.md
├── run-visual-regression.mjs   # Capture + compare script
├── golden/                     # Committed golden screenshots
│   ├── baseline.png
│   ├── selected-draft.png
│   └── agent-empty.png
├── captures/                   # Current run captures (gitignored)
└── fixtures/                   # Future: canned project JSON
```

## Not wired to CI

This is a local development tool. CI integration (GitHub Actions) will be added when the repo moves to a CI pipeline.
