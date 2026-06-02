# Atelier Visual References

A library of visual references that inform Atelier's design evolution.
Each batch lives in a dated subdirectory and ships with a notes file
that records the analysis output (observation → terminology → canvas
fit) for every reference.

For the **canonical per-product URL + capture-state index** (LibTV,
RHTV, Flova), see [`SOURCES.md`](./SOURCES.md). That file is
product-scoped; the dated subdirectories below are theme-scoped.

## Layout

```
docs/design/references/
├── README.md                              ← this index
└── <YYYY-MM-DD-slug>/                     ← one batch per theme
    ├── notes.md                           ← per-image analysis + batch synthesis
    ├── 01-<short-slug>.{png,jpg,webp}     ← raw reference asset
    ├── 02-<short-slug>.{png,jpg,webp}
    └── ...
```

## Current batches

| Batch | Theme | Status |
|---|---|---|
| [2026-05-24-canvas-ux-inspiration](./2026-05-24-canvas-ux-inspiration/) | Atelier v4 canvas UX inspiration — feeding the post-P0 visual evolution decision | collecting |

## Conventions

- **File names**: `NN-source-or-topic.ext` — zero-padded ordinal first
  (so OS lex sort matches batch order), then a short slug. Slug is for
  human memory only; the analysis lives in `notes.md`. Examples:
  `01-libtv-canvas.png`, `04-figjam-frame.png`, `09-pentagram-poster.jpg`.
- **Formats**: PNG / JPG / WebP for stills. For motion references,
  drop in either a short MP4/WebM **or** 2-3 key frames extracted as
  stills + a paragraph in `notes.md` describing the trigger and curve.
- **Size**: anything is fine on disk — we treat references as design
  artifacts (matches the precedent set by `docs/design/prototypes/screens/`
  which also commits PNGs). If a batch ever balloons past a few hundred
  MB, we'll revisit gitignoring raw assets and keeping only the notes.
- **Sourcing**: if a reference came from a public site, note the URL in
  the relevant entry of `notes.md` so future readers can return to context.
