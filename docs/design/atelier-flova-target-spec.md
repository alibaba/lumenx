# Atelier → Flova-grade canvas · pixel-level target spec (v0.5)

> GOAL: rebuild the Atelier v3 canvas to pixel-level match three references:
> ① RON "image generation v3" node board (NODE style) · ② the white glowing
> tapered beam (CONNECTION style) · ③ FLORA/Flova slide (overall AESTHETIC).
> The reference images live in the chat; this doc is the textual source of truth
> the implementation workflow + build phases use (subagents can't see images).
>
> DIRECTION CHANGE (explicit, supersedes prior decisions for these elements):
> - Action/Generate CTA → **spring green** (not cobalt). Cobalt stays for
>   node SELECTION ring only.
> - Node I/O ports get a **functional color code** (model=amber, positive=green,
>   negative=red, output=blue) — multi-color is allowed ON PORTS only.
> - Connections → **white glowing light-beam** (not grey dashed hairlines).
> - Focal node keeps an **iridescent bloom** (consistent with prior work).
> Dark canvas, dotted grid, editorial type, restraint elsewhere — unchanged.

---

## 1. Canvas / stage (refs ①③)

- Base: near-pure black `#08080a` (slightly warmer/darker than current `#0c0c0e`).
- Dotted grid: dots `rgba(255,255,255,0.05)`, **~30px** spacing, 1px dots. Very
  quiet — reads as graph paper, not a pattern.
- No atmospheric wash. The canvas is a black vitrine; nodes + glowing edges are
  the only light sources.

## 2. Node anatomy — frosted I/O card (ref ①, the "Image Generator")

The signature node. Anatomy top→bottom:

- **Shell**: rounded-rect, radius **16px**, `backdrop-filter: blur(20px)`, fill
  `rgba(22,22,28,0.66)` (translucent — the grid shows through faintly), 1px
  border `rgba(255,255,255,0.09)`, soft drop shadow `0 24px 60px -24px rgba(0,0,0,0.8)`.
- **Focal bloom**: a multi-hue iridescent glow bleeding from the TOP edge behind
  the shell — pink→violet→blue→cyan, blurred ~40px, `mix-blend:screen`, only on
  the selected/focal node (reuse the existing `atelier-bloom` ::before recipe,
  re-tuned to this 4-stop iridescent ramp).
- **Header row**: a tiny status dot (left) + node title (white, Inter/Space
  Grotesk 14px medium) + a right-aligned **output port**: label ("image") in
  muted grey + a **blue port dot** flush to the right edge.
- **Input ports** (left edge, stacked): each row = a colored dot flush to the
  left edge + a label. Color code: `model` = **amber `#e0b94e`**, `positive` =
  **green `#3ddc84`**, `negative` = **red `#f0616d`**. Dot ø 8px with a faint
  same-hue halo. Ports sit ON the node's left border (half outside) so edges can
  plug into them.
- **Settings rows**: label left (mono-ish, `rgba(255,255,255,0.5)`, ~12px) +
  value control right. Controls: text field ("12345"), dropdown ("Fixed",
  "dpm++ 2M" with ⌄), ◀ N ▶ stepper ("30"), plain value ("8.0"). Each control is
  a dark pill: fill `rgba(0,0,0,0.35)`, radius 6px, 1px `rgba(255,255,255,0.08)`,
  ~11px text. Row height ~30px, comfortable vertical rhythm.

### 2b. Prompt/input nodes (the two darker cards, ref ①)
- Same frosted shell, smaller. Body = the prompt text (muted). A status dot in
  the **top-right** corner (green = positive, red = negative). No port labels —
  just one output port dot on the right edge.

### 2c. Preview node (ref ①, right)
- Header "Preview Image" + dot; an inner "image" sub-card (blue dot).
- A **tall result card**: the media fills it; an **iridescent bloom** sits behind
  the media (the glow leaks past the card edges); a bottom **gradient scrim** with
  "Final Result" (medium) + a description line (smaller, `white/85`).
- Action row under it: icon buttons (expand / bookmark / copy / refresh) + `2x ⌄`
  + `PNG ⌄` + download — all small dark pills.

## 3. Connections — white glowing light-beam (ref ②, THE signature)

Each edge is a layered SVG `<g>`:
1. **Glow path**: the bezier, `stroke #ffffff`, width ~10px, `opacity 0.18`,
   `filter: blur(6px)` → the soft halo.
2. **Core path**: same bezier, `stroke #ffffff`, width ~1.5px, `opacity 0.95` →
   the bright filament.
3. **Endpoint flares**: at BOTH ends, a radial-gradient burst — white center
   fading to transparent, ~ø 26px, brightest where the beam meets the port; the
   beam visibly **tapers thin in the middle and flares wide+bright at the ports**
   (achieve via a 2nd core path with width-varying via two stacked strokes, or a
   radial-gradient `<circle>`/`<ellipse>` flare at each endpoint).
- Curve: smooth cubic S (ease in/out), horizontal control handles
  `dx = max(60, |Δx|*0.5)`.
- Default state = this white beam (no dashes). In-flight (generating) =
  marching-ants on the core only. Selected edge = warmer/brighter core.
- Flova variant (ref ③): for non-focal/ambient relations, a **thin grey** 1px
  low-opacity bezier (no glow) — hub-and-spoke calm. So: **focal/active edges =
  white beam; ambient = thin grey.**

## 4. Chrome (ref ①)

- Top-right toolbar: a dark pill cluster — `⋮  ▷ Queue ⌄  ⌃ ✕  🗑  ☰` + a white
  **Share** button + a dark **Make Public** button.
- Breadcrumb tab top-left: `‹  [Black bear ✕]  ›` (active doc as a dark pill).
- Tiny version label: "image generation v3" (mono, muted) top-left.
- Right-edge vertical control stack: square dark icon buttons `+ − ⤢ 👁 ⎙`
  (zoom in/out/fit/preview/capture), radius 8px, ~36px.
- **Generate** = a small **green pill** (`#3ddc84` fill, dark text) + sparkle
  icon, floating near the focal flow (the one saturated green in the UI).
- **Multiplayer cursors**: small rounded name pills following a cursor —
  "Kate" (blue), "Mario" (pink). Pinned in world coords.

## 5. Aesthetic / hub-and-spoke (ref ③)

- Media-thumbnail nodes: rounded 16px cards whose fill IS the asset (image/gradient)
  + a label/logo. App-icon nodes ~64px rounded squares.
- Hub-and-spoke: many nodes' thin grey connectors converge on a central node.
- Editorial wordmark vibe: a serif/pixel display lockup for brand moments.
- Labels: top-left "Creative Tools", top-right "Slide 03" style — small sans,
  the second word bold.

## 6. Color system (target)

| token | hex | use |
|---|---|---|
| canvas-base | `#08080a` | stage |
| grid-dot | `rgba(255,255,255,0.05)` | grid |
| node-fill | `rgba(22,22,28,0.66)` | frosted node |
| node-border | `rgba(255,255,255,0.09)` | node edge |
| port-model (amber) | `#e0b94e` | model input port |
| port-positive (green) | `#3ddc84` | positive port + Generate CTA |
| port-negative (red) | `#f0616d` | negative port |
| port-output (blue) | `#5b9dff` | output port |
| beam | `#ffffff` | connection core + glow |
| select-ring | `#3b6bff` (atelier-brand-400) | node SELECTION only |
| bloom | pink→violet→blue→cyan | focal/preview iridescent glow |

## 7. Mapping to current Atelier code (surfaces to rebuild)

- Canvas grid/bg: `globals.css .atelier-canvas-bg` + AtelierShellV3 root.
- Node shell + ports: DraftNode / MediaNode / DraftWorkbench / PlanNode + a NEW
  `NodePort` primitive + a NEW frosted-shell recipe.
- Edge renderer: AtelierShellV3 edge `<g>` builder (currently grey dashed bezier)
  → the layered white-beam renderer + endpoint flares (SVG defs: blur filter +
  radial gradient).
- Chrome: top toolbar, breadcrumb, right-edge zoom stack, Generate pill,
  multiplayer cursors (Composer / BottomNavRail / shell).
- Bloom: re-tune `.atelier-bloom` ::before/::after to the 4-stop iridescent ramp.

## 8. Fidelity loop

Build a surface → run dev → screenshot → compare against the reference image →
refine until pixel-close. Screenshots are the gate (subagents can't see; I do the
visual diff).

---

## 9. PREMIUM CRAFT deltas (v0.5.1) — why it currently reads "cheap", and the exact fixes

The elements are present but the CRAFT is amateur. The reference looks premium
because of restraint + glass depth + subtle light + refined type + air. Apply ALL:

### 9.1 KILL mono-caps (the #1 cheap tell)
- Node meta/footer/labels currently use `font-mono uppercase tracking-[0.2em]`
  ("WAN 2.7 · 1280×720", "AWAITING APPROVAL", "IDEA · NO 818"). This reads
  "terminal / dev tool", the OPPOSITE of premium.
- FIX: node body text → **Inter, sentence case**, normal tracking. Meta line:
  `text-[11px] text-white/45` sentence case ("Wan 2.7 · 1280×720 · 5s"). Footer
  status → a small sentence-case label, NOT mono-caps. Reserve mono ONLY for a
  single tiny build-label if any. Titles → Inter/Space-Grotesk 14–15px, weight
  500, sentence case (already mostly OK).

### 9.2 Frosted shell — real glass DEPTH (not a flat tint)
- Current `.atelier-node-shell` = flat rgba(22,22,28,0.66). Reads flat.
- FIX: add a subtle top→bottom gradient fill
  `linear-gradient(180deg, rgba(40,42,52,0.72) 0%, rgba(18,19,26,0.66) 100%)`,
  a brighter hairline top border (`inset 0 1px 0 rgba(255,255,255,0.10)`), and a
  deeper softer shadow `0 30px 80px -30px rgba(0,0,0,0.85), 0 8px 24px -12px rgba(0,0,0,0.6)`.
  Border `rgba(255,255,255,0.08)`. Radius 18. backdrop blur 24 + saturate(1.3).

### 9.3 Bloom — a WHISPER, not a rainbow
- Current alphas (0.42/0.36/0.28) at strength 1.5 = garish saturated smear.
- FIX: cut alphas ~half (0.22/0.18/0.13/0.08), widen blur (+8px), desaturate the
  ramp toward pastel (pink→violet→sky→mint but softer). It should read as faint
  ambient light bleeding from behind the focal node, barely perceptible — luxe,
  not a sticker. HERO strength 1.0 (was 1.5).

### 9.4 Ports — small, clean, subtle
- Dots 7px, halo softer (`0 0 0 2px rgba(hue,0.14), 0 0 5px rgba(hue,0.35)`).
  Labels Inter lowercase `text-[10px] text-white/45` (NOT mono-caps). On the
  collapsed node, show only the output dot (no label).

### 9.5 Air — generous spacing
- Node inner padding ↑ (collapsed `px-4 py-3.5`, workbench inner `p-5`).
- Line-height ↑ on body text (1.5). Row rhythm in settings ↑ to ~34px.
- Bigger gap between title and meta.

### 9.6 Restraint — fewer competing colors
- Default state nodes ≈ grayscale glass. Color appears ONLY as: the small port
  dots, the green Generate, the cobalt selection ring, and the focal bloom.
  Mute everything else (status footer → neutral grey text + a tiny status dot,
  not a colored tearline). The take-count/badge chrome → neutral.

### 9.7 Beam — gorgeous soft light
- Glow path width 8–10, opacity 0.20, blur stdDeviation 4.5 (softer/wider).
  Core 1.5 white 0.9. Endpoint flares brighter (r 8, the radial already exists).
  Ambient (non-focal) edges: 1px rgba(255,255,255,0.10) (whisper), not grey.

### 9.8 Composition (a) — bigger, fewer, airier
- Collapsed draft node width 244 → **280**; idea/comment 224 → **260**. More
  internal padding (9.5). Bigger title. This alone shifts density toward RON.

### 9.9 Surfaces to add (b)
- Preview result card (spec §2c). Top toolbar cluster (spec §4). Multiplayer
  cursor pills (Kate blue / Mario pink, world-pinned, subtle).
