# Canonical references — Flova-grade visual vocabulary

> Authoritative URL + capture index for the products whose canvas/board UI
> Atelier is studying. This file is the source of truth for "where do
> reference hero images come from, at what viewport, in what state."
>
> The dated `<YYYY-MM-DD-…/>` subdirectories under `docs/design/references/`
> are *theme-scoped* (e.g. "canvas UX inspiration"). This `SOURCES.md` is
> *product-scoped* — one entry per canonical product whose UI we treat as a
> craft target.
>
> Status of v1.3: hero PNGs are MISSING. Both LibTV and RHTV gate the canvas
> behind authentication, so no automated `WebFetch` can grab a usable hero
> image. Run the capture protocol below and replace each `MISSING.png`
> placeholder when the on-call designer is signed in.

---

## 1. Capture protocol (viewport / browser / state)

Anyone refreshing a hero PNG MUST follow this protocol so successive
captures stay comparable across diff'd batches.

### Browser
- **Real Chrome (NOT headless)**, latest stable, default extensions OFF.
- Zoom **100%** (Cmd-0). Color profile: sRGB (system default).
- OS theme: dark — matches Atelier's dark-first palette so the references
  read at the brightness Atelier ships at.

### Viewport
- Logical size: **1920 × 1200 px**.
- DPR: **2x** (retina). Physical capture: **3840 × 2400** PNG.
- Recipe in DevTools: `Cmd-Shift-I → device toolbar → custom size 1920×1200 →
  set DPR to 2 → "Capture full size screenshot"`.
  Alternative: full-window screenshot on a 16-inch retina at native scale,
  then crop to 1920×1200 logical.

### Output format
- PNG, sRGB, no JPEG re-encode.
- One PNG per product per capture date.

### Filename convention
```
docs/design/references/<product>-hero-YYYY-MM-DD.png
```
Examples:
- `libtv-hero-2026-06-02.png`
- `rhtv-hero-2026-06-02.png`
- `flova-hero-2026-06-02.png`

If a current capture is missing, leave a `MISSING.png` placeholder (or a
text stub `<product>-hero-MISSING.txt`) and update the per-product entry
below to point at the missing-state.

### Capture state (what the page must look like at capture time)
- **Signed in** (anonymous landing pages don't show node anatomy / edge
  beams / agent surfaces).
- **One project open**, with **multiple nodes visible** so node anatomy
  AND edge beams are studyable in a single frame.
- Mix node types where the product supports it (image + video + reference).
- Avoid modal/popover overlays — close any onboarding tour first so the
  canvas reads at rest.

---

## 2. Per-product entries

### 2.1 LibTV (LiblibAI canvas)

| Field | Value |
|---|---|
| Product | LibTV (LiblibAI's canvas-based creation surface) |
| Tagline (observed) | Visual generation canvas with node-to-node references and per-node model controls |
| Marketing URL | https://www.liblib.tv/ |
| Canvas URL (auth-gated) | https://www.liblib.tv/canvas?projectId=e24b1278ab524420b0a817394b5c897c |
| Domain verification | DOC-VERIFIED — URL appears in `docs/agents/deliverables/atelier-competitive-research-libtv-rhtv-2026-05-19.md` (line 9-10). |
| Canonical viewport | 1920×1200 logical @ 2× DPR (3840×2400 physical) |
| Capture state | Signed-in canvas with one board open, multiple media nodes (mix image + video) visible, no modal overlays |
| Hero image | `libtv-hero-MISSING.png` — capture pending. Use protocol §1 above. |
| Last verified | 2026-06-02 |

### 2.2 RHTV (RunningHub RHTV)

| Field | Value |
|---|---|
| Product | RHTV — RunningHub's canvas / video-gen workspace |
| Tagline (observed) | Semantic creation canvas — media nodes + reference edges + per-node generation controls |
| Marketing URL | https://rhtv.runninghub.cn/ |
| Canvas URL (auth-gated) | https://rhtv.runninghub.cn/projects/canvas/2056256964232486914 |
| Domain verification | DOC-VERIFIED — URL appears in `docs/agents/deliverables/atelier-competitive-research-libtv-rhtv-2026-05-19.md` (line 11). The parent v1.3 spec called this `rht.video`; the in-doc evidence uses `rhtv.runninghub.cn` — that is the authoritative domain. |
| Canonical viewport | 1920×1200 logical @ 2× DPR (3840×2400 physical) |
| Capture state | Signed-in video-gen project with nodes wired (parent video → take spokes), beam glow visible on focal node |
| Hero image | `rhtv-hero-MISSING.png` — capture pending. Use protocol §1 above. |
| Last verified | 2026-06-02 |

### 2.3 Flova / FLORA

| Field | Value |
|---|---|
| Product | Flova (also branded "FLORA") — overall aesthetic reference for editorial restraint, glass shells, and iridescent bloom |
| Tagline (observed) | n/a — used as overall aesthetic target, not as a feature-by-feature competitor |
| Marketing URL (parent-asserted) | https://flova.ai/ |
| Canvas URL | n/a (no public canvas captured for this product to date) |
| Domain verification | **PARENT-ASSERTED, UNVERIFIED.** The v1.3 brief asserted `flova.ai`. The internal target spec (`docs/design/atelier-flova-target-spec.md` line 5) only references "FLORA/Flova slide" as a reference image, not a live URL. **Owner action: the next on-call designer must confirm the canonical domain before recording a hero capture.** |
| Canonical viewport | 1920×1200 logical @ 2× DPR (when the canonical surface is confirmed) |
| Capture state | Whichever surface is the reference for "overall aesthetic" — typically a marketing canvas slide showing iridescent bloom, dark vitrine, minimalist editorial type |
| Hero image | `flova-hero-MISSING.png` — capture pending domain confirmation. |
| Last verified | 2026-06-02 |

---

## 3. License / use note

These references are **observational design study only**. They are not
redistributed assets, not training data, and not part of LumenX's product
output.

- Hero PNGs may stay in-repo because we treat them as in-house craft
  alignment artifacts (precedent: `docs/design/prototypes/screens/` also
  commits PNGs).
- If any product owner asks us to remove a hero PNG, replace the entry
  with a link-only record (delete the PNG, keep the row in this file
  pointing at the public URL).
- Per-image analysis (observation → terminology → canvas fit) belongs in
  the dated batch's `notes.md`, not in this file. This file is the URL +
  capture-state index, not the analysis log.

---

## 4. Why no automated WebFetch?

Both LibTV and RHTV gate the canvas behind authentication. Anonymous
`WebFetch` against the canvas URLs returns the marketing landing page or
a login wall, not the canvas study target. Capture remains a **manual,
signed-in browser-screenshot task**, not an automatable pipeline. This is
intentional — we don't want a captures pipeline that silently degrades to
landing-page screenshots and quietly invalidates downstream craft work.
