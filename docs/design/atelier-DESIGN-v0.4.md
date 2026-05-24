# Atelier DESIGN v0.4 — Canvas Visual System

> Derived from the 2026-05-24 visual references batch (see
> [`references/2026-05-24-canvas-ux-inspiration/notes.md`](references/2026-05-24-canvas-ux-inspiration/notes.md))
> and a structured grilling session that locked four dimensions
> (frame chrome, connector lines, icons, take aggregation) under one
> design philosophy:
>
> **Linear-precision school dominant + Vision-material focal accent.**
>
> Chrome whispers, the user's generations shout. Every chrome element
> earns its weight by being either restrained (the default) or
> deliberately material (the focal moment).

## 0. Design philosophy

| Pillar | What it means |
|---|---|
| **Restraint as identity** | Fewer materials, fewer accents, fewer sizes — and each one chosen, not defaulted. Premium = "I only placed what was necessary." |
| **Mono-accent system** | One brand color (emerald) carries every primary signal. Semantic edge colors form a small adjacent vocabulary; they are not "more brand colors". |
| **Static flat, focal glass** | Static chrome stays flat; frosted glass appears only on the surface the user is actively working in. Material weight is a focus signal, not decoration. |
| **Content > chrome** | Generations are the loudest thing on screen. Chrome density and saturation drop so output reads first. |

## 1. Brand accent — emerald primary

Atelier's primary color is **emerald**. The legacy purple-blue primary is retired.

```
emerald-400  #34d399    base brand
emerald-300  #6ee7b7    active icons, hover-up lift
emerald-500  #10b981    pressed / focus ring
glass tint   rgba(52,211,153,0.55)
cta bg       rgba(52,211,153,0.20) → hover 0.30
edge ref     rgba(52,211,153, opacity-by-state)
```

Justification: ref #01 anchors the AI-creative-canvas archetype on
emerald; emerald has higher dark-bg legibility than purple-blue without
needing saturation; it co-locates with the existing region/sequence
six-color palette so the system stays self-consistent.

## 2. Frame chrome

Two regimes: **static** (the default for any non-focused node) and
**focal frosted glass** (the active edit surface).

### 2.1 Static node chrome

| Token | Value |
|---|---|
| `border-width` | `1px` |
| `border-color` | `rgba(255,255,255,0.10)` |
| `border-style` | `solid` (Region is the only `dashed` exception — see §2.3) |
| `background` | `rgba(20,20,22,0.40)` |
| `backdrop-filter` | none |
| `corner-radius` | `6px` for compact (idea/comment), `10px` for standard nodes, `14px` for workbench-size |
| `shadow` | `0 8px 20px -10px rgba(0,0,0,0.5)` (single soft layer) |
| `selection ring` | retained — `ring-2` of the emerald primary, for nodes that don't escalate to focal glass |

### 2.2 Focal frosted glass

Applied only to:

1. The **currently-selected DraftWorkbench** (the cockpit center).
2. The **always-on RightRail AgentPanel** (it is the dialog surface).

| Token | Value |
|---|---|
| `border-width` | `1px` |
| `border-color` | `rgba(255,255,255,0.16)` |
| `border-style` | `solid` |
| `background` | `rgba(20,20,22,0.55)` |
| `backdrop-filter` | `blur(14px) saturate(180%)` |
| `inner highlight` | `inset 0 1px 0 0 rgba(255,255,255,0.08)` (top-edge catch) |
| `corner-radius` | `14px` (focal nodes are always more rounded) |
| `shadow` | 3 layers: `0 24px 48px -22px rgba(0,0,0,0.8), 0 4px 14px -4px rgba(0,0,0,0.55), inset 0 1px 0 0 rgba(255,255,255,0.06)` |
| `padding` | static value `+8px` to give focal surface extra breathing room |
| `selection ring` | **dropped** — the glass treatment itself is the selection signal. No `ring-2` on top. |

### 2.3 Region — the structural exception

Region frames stay **flat + dashed** as a deliberate semantic signal
("I am a container, not an artifact"). Treatment unchanged from B-α
except for the color-tint adoption from the 6-color palette.

## 3. Connector lines — chromatic-semantic

Every edge carries a semantic color. Default opacity drops sharply so
edges read as "ambient infrastructure" until you ask about them.

### 3.1 Geometry

| Variable | Value |
|---|---|
| path | curved bezier, control-point offset `max(40, dx*0.3)` (unchanged) |
| stroke-width — default | `1.25px` |
| stroke-width — hover-related | `1.75px` |
| stroke-width — selected | `2px` |
| stroke-dasharray | `1 5` (sparser than the legacy `2 4` — reads as constellation thread) |

### 3.2 Color × state matrix

| Semantic | Default opacity 0.55 | Hover-related 0.85 | Selected 1.0 |
|---|---|---|---|
| `reference` | `rgba(52,211,153,…)` emerald | same | same |
| `branch` | `rgba(251,191,36,…)` amber | same | same |
| `sequence-order` | `rgba(167,139,250,…)` violet — **only rendered on hover/select**, hidden by default | — | — |
| `region-attach` | not rendered — Region's own border carries the relationship |
| `generic / fallback` | `rgba(156,163,175,…)` slate |

Dimmed-unrelated state (when another node is hovered): opacity `0.10`.

### 3.3 Endpoint dots

A `3×3 px` solid circle in the edge color appears at each endpoint
**only in hover-related or selected state**. Default (ambient) edges
have no endpoint dots — keep the canvas quiet at rest.

### 3.4 Mid-label chip

On hover-related state, mid-edge label is a small color dot + the
full semantic name ("reference", "branch", "sequence"). Replaces the
legacy generic "ref" text.

### 3.5 Glass intersection

When an edge passes over a frosted-glass surface (selected
DraftWorkbench, RightRail), keep `mix-blend-mode: normal`. Do not
let edges bleed through glass — they terminate at the glass border
or pass behind it cleanly.

## 4. Icons

Library stays at **lucide-react**. The visual upgrade is discipline
on stroke + size + state.

### 4.1 Stroke width

| Context | Stroke |
|---|---|
| Standard chrome (rail buttons, chips, action bar) | `1.25px` |
| In-canvas icons (status dots, take placeholders) | `1.5px` |
| Focal CTA (primary buttons like Export) | `2px` |

Every `<Icon />` invocation must explicitly pass `strokeWidth` — no
default reliance, no per-component drift.

### 4.2 Size scale (only 4 sizes allowed)

| Token | px | Use |
|---|---|---|
| `caption` | **11** | status dots, stamp adjacencies, build-label glyphs |
| `chrome` | **13** | LeftRail / BottomNavRail / panel header buttons |
| `action` | **15** | SelectionActionBar, in-card buttons |
| `focal` | **18** | primary CTAs, empty-state hero icons |

Any other size is a smell — surface in PR review.

### 4.3 Color × state

| State | Token |
|---|---|
| Rest | `text-text-muted` (white-alpha ~50%) |
| Hover | `text-foreground` (white) |
| Active / selected | `text-emerald-300` (one step brighter than `emerald-400` so it "jumps") |

### 4.4 Hit area

Icon-only buttons: minimum `28×28px` hit area regardless of glyph size.
Icon + label pairs: `gap: 8px` exactly — no per-context drift.

## 5. Take aggregation — polaroid stack

The canvas-side rendering of a draft's candidates collapses from
"N tiles spread on a grid" to a single **polaroid stack** with hover
fan-out.

### 5.1 Geometry

| Variable | Value |
|---|---|
| Visible layers | `min(takes.length, 3)` |
| Per-layer offset | `x +6px`, `y +4px`, `rotation +0.5deg` |
| Top-layer rendering | full video preview at the configured size |
| Below-top layers | flat color + 1px border only (no video render — performance) |
| Chrome | same as §2.1 static node chrome — **no glass** |
| Take-count badge | bottom-right `N/M` (current/total) in `caption` size, `font-mono` |

### 5.2 Status indicator

A `caption`-size status dot in the top-right of the top layer,
aggregating across all takes (matches Region §B-β logic):

| Condition | Dot color |
|---|---|
| Any take `failed` | red (`bg-red-400/85`) |
| Else any `processing` / `pending` | blue, pulsing (`bg-blue-400/85 animate-pulse`) |
| Else any `completed` | emerald (`bg-emerald-400/85`) |
| Else | quiet (`bg-white/35`) |

### 5.3 Interaction

| Gesture | Effect |
|---|---|
| `hover` | 200ms ease fan-out into a horizontal row (up to 5 takes); 200ms ease back on leave |
| `click` (stack body) | select the parent draft → DraftWorkbench expands |
| `right-click` (stack body) | parent's context menu |
| `right-click` (specific fanned-out layer) | that take's context menu (branch / delete / set-as-primary) |
| `drag` (stack body) | drag the current primary's `video_url` as a reference handle (same semantics as today's take-tile drag) |
| `drag` (a specific fanned-out layer) | drag that specific take |

### 5.4 Anchor

Right of the parent draft, `+32px` gap (the existing
`PARENT_TO_CAND_GAP` constant). Vertically top-aligned with the draft.

### 5.5 Strip-in-workbench (no change)

The horizontal take strip inside DraftWorkbench (the I commit) stays
exactly as-is. Stack handles canvas-level "this draft has takes";
strip handles workbench-level "pick / compare / manage".

## 6. Implementation order (when v0.4 starts landing)

The 4 dimensions don't all need to ship in one PR. Suggested order:

1. **Brand recolor — emerald primary** (`6e2d706` follow-up). One PR;
   replaces every reference to the legacy purple primary with the
   emerald token. Cheap and unblocks Q3 + Q4 visuals.
2. **Connector lines — chromatic-semantic** (one PR). New per-edge
   color logic + endpoint dot + mid-label chip + opacity drop. Visible
   immediately on any project with non-trivial graph.
3. **Icon discipline** (one PR). Audit every `<Icon />` invocation,
   apply the 4-step size scale + explicit stroke width.
4. **Focal frosted glass** (one PR). Add the glass treatment to
   selected DraftWorkbench + RightRail AgentPanel. Drop the redundant
   selection ring on those two surfaces.
5. **Polaroid stack** (one PR). Replaces `renderCandidatesAsMediaNodes`
   on the canvas; keeps the DraftWorkbench take strip untouched.
   This is the biggest change — saved for last so the visual system
   is already at v0.4 standard when it lands.

## 7. Out of scope for v0.4

Captured for v0.5 consideration:

- **Inline assistant card** (ref #02) — moving Agent responses out of
  the right rail and onto the canvas as connected cards.
- **Split workbench** (ref #04) — dedicated right pane for full-quality
  preview + timeline.
- **Multiplayer pins** (ref #03) — collaborator presence on nodes.
- **Artifact-on-desk framing** (ref #01) — ambient outer backdrop
  treating the canvas as an object placed on a surface.
- **Edge draw-on animation** — small craft signal, performance-sensitive.
- **Audio-role pill cycle** — D' shipped the read-only badge; the
  cycling UI is a follow-up.

## 8. Source of truth

This document supersedes the visual sections of
[`atelier-DESIGN.md`](atelier-DESIGN.md) for any new work. Until a
v0.5 lands, the older doc remains canonical for what's already
shipped; this doc is canonical for the v0.4 direction.

---

## 9. v0.4.1 update — editorial / print upgrade

After the first v0.4 mockup landed, user feedback (2026-05-24)
identified four shortfalls: color usage felt "tech SaaS" not
"premium creative tool"; typography read "developer tool" not
"creative studio"; the bottom-dock pattern universal to canvas tools
was missing. A second grilling session locked the v0.4.1 deltas
below. Earlier §0-§8 decisions are otherwise unchanged.

Mockup: [`prototypes/atelier-v0.4.1-mockup.html`](prototypes/atelier-v0.4.1-mockup.html).

### 9.1 Brand palette — emerald demoted, cobalt blue primary

The reference image that anchored the upgrade was a Chinese poster
of cobalt-blue liquid ink dispersed on cream paper. That print/print
aesthetic — depth, restraint, editorial — is the new direction.

| Role | v0.4 | v0.4.1 |
|---|---|---|
| Primary brand | emerald-400 `#34d399` | **cobalt `#3b6bff`** |
| Hover-up brand | emerald-300 `#6ee7b7` | **cobalt-300 `#6e8fff`** |
| Pressed brand | emerald-500 `#10b981` | **cobalt-500 `#2548d8`** |
| `reference` edge | emerald | **cobalt** |
| `branch` edge | amber | amber (unchanged) |
| `sequence` edge | violet | violet (unchanged) |
| Status — completed | (used the brand) | **emerald-400** ← demoted here |
| Status — processing | blue | blue (unchanged) |
| Status — failed | red | red (unchanged) |

The brand color and the "completed" status color are now distinct.
This is the Linear-Cron convention and is critical for users who scan
status by hue — emerald = "I'm done", cobalt = "this is Atelier".

### 9.2 Diffuse aura + grain — material upgrade for focal surfaces

Focal frosted glass (§2.2) gains two new layers:

**Diffuse aura.** A radial cobalt glow placed underneath the focal
element. Technically: `radial-gradient` in a positioned pseudo-element
plus `filter: blur(20-28px)`. Behavior: applies to the **three** focal
surfaces — selected DraftWorkbench (large outer aura), RightRail
AgentPanel (side-bleed aura leaking left into the canvas), polaroid
stack's primary take (small halo). Static nodes get no aura. Edges get
no aura except the selected edge (see §9.4 selected-edge halo).

**Grain (6% opacity SVG noise).** A subtle paper-grain overlay applied
to all frosted-glass surfaces (DraftWorkbench, AgentPanel, the new
bottom dock). Implementation: a single SVG `feTurbulence` data-URI
applied via `::after` with `mix-blend-mode: overlay; opacity: 0.06`.
Cost: one cached paint per surface. Removes the "plastic" feel of flat
glass; signals editorial / print provenance.

### 9.3 Typography — mono-caps purge + Space Grotesk display

The "developer tool" feel was traced to 147 occurrences of
`font-mono uppercase tracking-wide` scattered across 20 components.
v0.4.1 removes almost all of them.

| Use | Family | Size | Weight | Case | Notes |
|---|---|---|---|---|---|
| Project title (top bar) | Space Grotesk display | 18 | 500 | Sentence | new |
| Region title | Space Grotesk display | 13 | 500 | Sentence | was mono-caps |
| Draft intent heading | Space Grotesk display | 15 | 500 | Sentence | was mono-caps |
| AgentPanel section header | Inter | 13 | 600 | Sentence | was mono-caps |
| Body / Prompt | Inter | 13 | 400 | Sentence | unchanged |
| Chrome labels (chips, buttons, toasts) | Inter | 12 | 500 | Sentence | was mono-caps |
| Meta info (model · 1280×720) | Inter | 11 | 500 | Sentence | was mono-caps |
| Numeric counter (3/5, 100%) | JetBrains Mono | 11 | 500 | — | mono retained for alignment |
| Build label only | JetBrains Mono | 9 | 500 | UPPERCASE | the **single** sanctioned mono-caps use |

Type scale crunched to six values: **11 / 13 / 15 / 18 / 22 / 28**.
Any other size is a smell.

### 9.4 Bottom dock — quick-add + composer

A canvas-product universal pattern v0.4 omitted. v0.4.1 adds a
bottom-center dock as the "high-frequency intent" surface.

**Layout.** Bottom-center floating pill, 640px × 56px, 32px above
canvas bottom. Material: focal frosted glass + grain + bottom-edge
cobalt aura (third aura element in §9.2).

**Left half — quick-add chips.** Three sentence-case chips for the
highest-frequency node types: `+ Image (I)`, `+ Video (V)`,
`+ Idea (T)`. Single-click creation; bypasses the LeftRail Add panel
for these three (the Add panel still handles Comment / Script /
From Library / Region / etc.).

**Right half — global composer.** A text input ("Describe to create,
or ask the agent…"), a `Free | Director` segment toggle mirroring
AgentPanelV3's planner mode, and a cobalt-tinted submit button.
Keyboard `/` focuses this input from anywhere on the canvas.

**Always visible**, with two state nuances:
- When a draft is selected and DraftWorkbench is open, the dock
  remains visible but the placeholder text shifts to
  "Ask the agent about anything…" — signaling "the workbench composer
  iterates the current draft; the dock composer starts new things".
- Modal contexts (Cmd+P palette, full-screen preview) hide the dock
  briefly to avoid occluding the focus surface.

**Division of labor.** LeftRail = mode/panel switching (Assets,
Workflows, History, Director, Agent, Sequence). Dock = immediate
action (quick-add, agent input). They share no overlap; the dock is
the express lane for what users do most.

### 9.5 Selected-edge cobalt halo

A small craft addition: a selected chromatic edge gets
`filter: drop-shadow(0 0 4px rgba(59,107,255,0.65))` — a soft cobalt
glow tracing its path. Default and hover edges keep flat color.
Cost: trivial (one filter per selected edge, typically ≤1 at a time).
Effect: selection is felt visually without thickening the line.

### 9.6 Updated implementation order

The v0.4.1 deltas slot into the v0.4 implementation order (§6) like so:

1. **Brand recolor — cobalt blue primary** (replaces the v0.4 emerald
   step). Token rename plus emerald demotion to a status-only color.
2. **Typography purge** (Q3 of v0.4.1). The 147 mono-caps replacement
   + Space Grotesk display + type-scale enforcement. Mechanical but
   large.
3. **Bottom dock** (Q4 of v0.4.1). New `<AtelierDock />` component;
   wires quick-add to existing creator actions and composer to
   `planAgentTurn` / `runAgentTurn`. Replaces the v0.4 brand recolor
   slot for "first visible upgrade".
4. **Chromatic-semantic connector lines** (unchanged from v0.4 §6).
5. **Icon discipline** (unchanged from v0.4 §6).
6. **Focal frosted glass + diffuse aura + grain** (extends v0.4 §6
   step 4 — same PR, additional layers).
7. **Polaroid stack** (unchanged from v0.4 §6).

---

## 10. v0.4.2 update — Aerogel atmospheric upgrade

The v0.4.1 mockup made progress (cobalt + grain + bottom dock) but
user feedback flagged that **the cobalt was still too cold and
synthetic**, and pointed at a new reference (`05-arpan-karmakar.jpeg`
— see references batch) showing the desired direction: soft sky-blue
gradients with heavy film grain and editorial poster typography.

v0.4.2 keeps every v0.4.1 decision intact and **adds** an
atmospheric layer on top, code-named "Aerogel" after the lightest
known solid — sky-blue, half-translucent, almost ethereal.

Mockup: [`prototypes/atelier-v0.4.2-mockup.html`](prototypes/atelier-v0.4.2-mockup.html).

### 10.1 Palette extension — atmospheric tier

Cobalt is not retired. It still owns **structural** uses (CTA,
selected ring, ref edge — anywhere high-contrast is required). What's
new is a parallel **atmospheric tier** — soft sky-blue plus a peach
grace note — for backgrounds, ambient auras, and any place a
gradient lives.

| Role | Token | Value | Used for |
|---|---|---|---|
| Structural — primary | `brand-400` | `#3b6bff` cobalt | CTA, selected ring, ref edge (unchanged from v0.4.1) |
| Atmospheric — base | `sky-300` | `#9cc4e8` | aura center, focal-glass tint, ambient wash |
| Atmospheric — light | `sky-100` | `#dde9f4` | aura mid-stop, gradient horizon line |
| Atmospheric — warmth | `peach-200` | `#e8b89c` | aura edge "hidden bleed", ambient gradient corner accent |
| Atmospheric — paper | `cream-100` | `#f3efe6` | reserved for any cream-background context |

The cobalt-sky-peach triplet is the new **aura recipe**. See §10.3.

### 10.2 Pigment grain — bump from 6% to 10-12%

v0.4.1 placed 6% SVG noise on focal glass surfaces only. v0.4.2:

- **Glass surfaces**: 10-12% opacity (was 6%) — the grain should be
  visible up close, not invisible.
- **Canvas background**: add a layer at 6-8% opacity over the
  dot-grid. The whole canvas now has a subtle pigment quality;
  it never feels "screen-flat" again.
- Implementation: keep the SVG `feTurbulence` data URI from v0.4.1;
  just adjust opacity. Cost remains a cached single paint.

### 10.3 Aura — from solid radial to multi-stop atmospheric

v0.4.1 aura was a single-stop cobalt radial gradient at 30% alpha.
v0.4.2 makes the aura a **3-stop atmospheric gradient** to match the
Arpan aerogel quality:

```css
.aura-focal-v042::before {
  content: '';
  position: absolute;
  inset: -80px;
  background:
    radial-gradient(ellipse at 35% 40%,
      rgba(59,107,255,0.32) 0%,        /* cobalt — structural center */
      rgba(156,196,232,0.18) 35%,      /* sky-blue — atmospheric middle */
      rgba(232,184,156,0.06) 60%,      /* peach hint — hidden warmth */
      transparent 80%);
  filter: blur(28px);
  pointer-events: none;
  z-index: -1;
  border-radius: inherit;
}
```

The peach hint at 6% alpha is the **critical detail**. Mathematically
barely there, but transforms the aura from "tinted light" into
"atmosphere with depth". Don't omit it.

### 10.4 Canvas background — atmospheric wash overlay

v0.4.1 canvas was `bg #0c0c0e` + dot grid. v0.4.2 adds a **very wide,
low-opacity atmospheric gradient** underneath the grid, giving the
whole canvas a sense of place:

```css
.canvas {
  background:
    /* dot grid (top layer) */
    radial-gradient(circle, rgba(255,255,255,0.06) 1px, transparent 1px),
    /* atmospheric wash — peach corner */
    radial-gradient(ellipse 120% 80% at 70% 15%,
      rgba(232,184,156,0.06) 0%,
      transparent 40%),
    /* atmospheric wash — sky pool */
    radial-gradient(ellipse 100% 100% at 30% 70%,
      rgba(156,196,232,0.08) 0%,
      transparent 50%),
    /* base color */
    #0c0c0e;
  background-size: 28px 28px, auto, auto, auto;
  background-position: -14px -14px, 0 0, 0 0, 0 0;
}
```

Plus the new noise overlay layer (§10.2). Net effect: the dark canvas
no longer feels like a blank dark room — it feels like a slightly
foggy room with a hint of dawn light coming from somewhere.

### 10.5 Typography breathing — same fonts, more space

v0.4.1 typography decisions (Inter body + Space Grotesk display +
sentence case + 6-step scale) stand. v0.4.2 only adjusts **spacing
and weight contrast** to match Arpan's editorial poster feel:

- **Hero / project title**: scale up from 18px → 24-28px (top bar
  and "moments of arrival" only — not every heading).
- **Display weight bias**: 500 (Medium), not 600/700 (Bold). Heavy
  weight + dense layout reads "tech"; medium weight + generous space
  reads "editorial".
- **Letter-spacing on display**: -0.012em (slight tighten) for big
  headings 22px+.
- **Line-height on observation copy**: 1.65 (was 1.55) — breathing.

No new font loads. No type-scale additions.

### 10.6 Selected edge halo — extend the aura recipe

v0.4.1: `drop-shadow(0 0 4px rgba(59,107,255,0.65))` (single cobalt).

v0.4.2: 2-stop drop-shadow mirroring the aura:

```css
.edge-selected-v042 {
  filter:
    drop-shadow(0 0 4px rgba(59,107,255,0.55))    /* cobalt structural */
    drop-shadow(0 0 8px rgba(156,196,232,0.30));  /* sky atmospheric */
}
```

The sky-blue outer drop-shadow gives the line a "lit fog" quality
without thickening it.

### 10.7 What v0.4.2 explicitly does NOT change

- Chrome layout (left rail, right rail, bottom dock)
- Mode dispatch and behavior
- Connector line semantic colors (cobalt for `reference` stays — it
  owns the structural high-contrast role)
- Icon stroke / size / state colors
- Polaroid stack mechanics
- The 8 P0+P1+P2 code commits already on this branch

This is a **visual material upgrade**, not a layout or behavior
change. When implementing, all changes land in CSS tokens and 2-3
component files (aura, canvas, focal glass) — no React component
restructure.

### 10.8 Implementation order — slots into v0.4 §6

v0.4.2 deltas slot in between v0.4 step 1 (brand recolor) and v0.4
step 2 (connector lines). Updated order:

1. Brand recolor — cobalt blue primary (from v0.4 §6 step 1).
2. **(new)** Atmospheric palette tier: register `sky-300`, `sky-100`,
   `peach-200`, `cream-100` tokens.
3. **(new)** Canvas background — atmospheric wash overlay (§10.4).
4. **(new)** Aura recipe update — multi-stop atmospheric (§10.3).
5. **(new)** Pigment grain bump — 6% → 10-12% + add canvas layer (§10.2).
6. Connector lines — chromatic-semantic edges (from v0.4 §6 step 2).
7. **(updated)** Selected edge halo — 2-stop drop-shadow (§10.6).
8. Icon discipline (from v0.4 §6 step 3).
9. Typography breathing — spacing-only tweaks (§10.5).
10. Focal frosted glass (from v0.4 §6 step 4).
11. Polaroid stack (from v0.4 §6 step 5).

Steps 2-5, 7, 9 are pure CSS-token work, mostly trivial; the heavy
work remains the original v0.4 plan.

### 10.9 Iridescent rim glow — the missing focal signal

After v0.4.2 mockup landed, user feedback identified that I had
missed a technique present on `03-instagram.jpeg`: a multi-hue
spectrum bar bleeding from the top edge of focal nodes (the
"Image Generator" node and the screen-pinned composer pill). Where
the aerogel aura is a wide, soft, atmospheric halo BEHIND the
element, the **iridescent rim** is a thin, sharp(er), prismatic
spectrum ON the element's top edge — pink → violet → sky → mint all
at once.

Reference patterns: Apple Vision Pro selected rim, Arc Browser tab
bar active indicator, oil-slick water surface, holographic foil.

The aerogel aura (§10.3) and the iridescent rim solve different
problems:

- **Aerogel aura**: atmospheric weight around the focal element —
  "this element has space around it that is also alive"
- **Iridescent rim**: localised hyper-focal accent on the element
  itself — "this element is lit from a direction we can't see; it
  has its own subtle aliveness"

They compose — focal workbench gets **both**: aerogel aura behind +
iridescent rim on top edge.

#### Token

Add to the atmospheric tier (§10.1):

| Role | Token | Value | Used for |
|---|---|---|---|
| Spectrum rim | `--iridescent-band` | `linear-gradient(90deg, transparent 0%, rgba(255,140,200,0.55) 18%, rgba(180,140,255,0.70) 38%, rgba(140,200,255,0.55) 62%, rgba(180,255,220,0.45) 82%, transparent 100%)` | top-edge rim on focal surfaces |

#### Implementation

A `::before` pseudo-element with a thin horizontal spectrum bar,
positioned 1-2 px above the element's top border, with `filter:
blur(4px)` for the soft bleed.

```css
.iridescent-top-rim {
  position: relative;
}
.iridescent-top-rim::before {
  content: '';
  position: absolute;
  top: -2px;
  left: 8%;
  right: 8%;
  height: 3px;
  background: var(--iridescent-band);
  filter: blur(4px);
  border-radius: 999px;
  pointer-events: none;
}
```

#### Where it appears in v0.4.2 (post-§10.9 update)

Apply only on the **3 highest-attention focal surfaces** — these are
where the user is currently looking; iridescent rim says "this is
where you are." More than 3 simultaneous rims dilute the signal.

1. **Selected DraftWorkbench** — top-edge iridescent rim, ~80%
   width centered.
2. **Polaroid stack — primary take (top layer)** — top-edge rim,
   ~70% width centered.
3. **Agent panel "Awaiting approval" bubble** (when present) —
   top-edge rim, ~90% width centered.

Other glass surfaces (right rail base, bottom dock, take strip,
chips) **do not** get the rim. They stay quiet so the rim signal
remains rare and meaningful.

#### Discipline

- The rim is **always horizontal across the top edge** in v0.4.2.
  Full-perimeter conic-gradient rims (Arc Browser style) are v0.5
  candidates — they're more complex and risk visual noise.
- Opacity values in the gradient are tuned for a dark background.
  If light-mode lands in the future (§7 of original v0.4 spec
  flagged off-white cream backgrounds), the alphas need bumping
  ~1.5×.
- The rim must not be animated in v0.4.x. Static only. (A subtle
  ~6s drift could be a v0.5 polish but risks being noticeable in
  peripheral vision and tiring.)

#### Updated implementation order (replaces §10.8)

The iridescent rim slots in **alongside** the aura step:

1. Brand recolor — cobalt blue primary (from v0.4 §6 step 1).
2. Atmospheric palette tier — add `sky-300`, `sky-100`, `peach-200`,
   `cream-100`, `--iridescent-band` tokens.
3. Canvas background — atmospheric wash overlay (§10.4).
4. Aura recipe update — multi-stop atmospheric (§10.3).
5. **(also new in this step)** Iridescent rim on focal workbench,
   polaroid primary, approval bubble (§10.9).
6. Pigment grain bump — 6% → 10-12% + add canvas layer (§10.2).
7. Connector lines — chromatic-semantic edges (from v0.4 §6 step 2).
8. Selected edge halo — 2-stop drop-shadow (§10.6).
9. Icon discipline (from v0.4 §6 step 3).
10. Typography breathing — spacing-only tweaks (§10.5).
11. Focal frosted glass (from v0.4 §6 step 4).
12. Polaroid stack (from v0.4 §6 step 5).
