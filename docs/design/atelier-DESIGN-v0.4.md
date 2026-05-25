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
- For the single most important focal surface (selected
  DraftWorkbench), **rim alone is insufficient** — user feedback
  showed the reference image (`03-instagram`) had full-perimeter
  bloom in addition to the rim. v0.4.2 adds a **`.iridescent-bloom`**
  variant (4 colored radial gradients positioned at the 4 outer
  quadrants, heavy blur, `mix-blend-mode: screen`, z-index below
  the aura) that **composes with** the rim. Apply both on the
  focal workbench; apply rim only on polaroid primary and approval
  bubble.
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

---

### 10.10 v0.4.3 bloom deployment roster — where + when iridescent bloom appears

**Problem this section fixes.** §10.9 introduced iridescent bloom (4-stop
radial gradient with `mix-blend-mode: screen`) but only deployed it on
the focal DraftWorkbench. Reviewing 03-instagram.jpeg (whole-node bloom
on important nodes) and 04-tranmautritam.jpeg (edge-endpoint bloom at
signal junctions) revealed two distinct deployment patterns that bloom
should cover. This section makes the deployment rules normative.

Reference mockup: `docs/design/prototypes/atelier-v0.4.3-mockup.html`.

#### 10.10.1 Two patterns

- **Pattern A — whole-node bloom**: bloom wraps the full perimeter of a
  surface. Signals "this surface matters right now". Inspired by
  03-instagram's halo around the Image Generator node.
- **Pattern B — edge endpoint bloom**: bloom sits at the connection
  point between an edge and a node. Signals "signal is converging /
  relationship is live". Inspired by 04-tranmautritam's chromatic
  endpoints on signal-junction edges.

Pattern A and Pattern B can co-occur — a selected workbench wears
Pattern A, and the edges flowing into it wear Pattern B endpoints in
the workbench's edge-color.

#### 10.10.2 Single recipe + strength multiplier

There is **one** bloom CSS recipe (the four-gradient `.bloom::after`
from §10.9). It scales via a CSS custom property:

```css
:root { --bloom-strength: 1; }

.bloom::after {
  inset: calc(-40px * var(--bloom-strength));
  /* 4 radial gradients, each alpha multiplied by var(--bloom-strength) */
  filter: blur(calc(28px + 8px * var(--bloom-strength)));
}

.bloom-hero      { --bloom-strength: 1.5; }  /* selected focal surface */
.bloom-secondary { --bloom-strength: 1.0; }  /* live-state surfaces   */
.bloom-ambient   { --bloom-strength: 0.6; }  /* low-key acknowledgers */
```

Three tiers, never more. The tier is a deployment classification, not a
prop the user toggles per component.

#### 10.10.3 Pattern A deployment roster

| Tier | Strength | Surface(s) | Trigger |
|---|---|---|---|
| **HERO** | 1.5× | Selected DraftWorkbench (only one at a time) | `selected` state |
| **SECONDARY** | 1.0× | Polaroid stack current take · Approval bubble · Processing node | take is current / approval pending / task in flight |
| **AMBIENT** | 0.6× | LeftRail active mode · Dock when composer focused · Connect port on hover · Selected or hover-related edge endpoints (Pattern B) | active / focused / hovered / edge selected |

Components **not** in this roster (region containers, static media
nodes, take strip non-current takes, chips, top-bar project name, region
title bars) do not wear bloom. Adding bloom there clutters the canvas
without a real meaning.

#### 10.10.4 Breathing — when bloom pulses

Bloom is static by default. Two animation rates add liveness when the
surface is doing something:

| Animation | Rate | Easing | When |
|---|---|---|---|
| `breath-generating` | 2.4s | ease-in-out | Task in flight (processing node, generating polaroid). The pulse is visible — it says "I'm working." |
| `breath-attending` | 4.5s | ease-in-out | Selected hero + polaroid current take + connected edge endpoints. The pulse is barely-there — it says "you are here." |

Opacity range is intentionally narrow (`0.85→1.0` for generating,
`0.92→1.0` for attending) — the breath should be perceived as alive,
not as a strobe.

**Critical rule — selected-node breath propagates to connected edges.**
This is the user's key insight: when a node is selected, its connected
edges' endpoints also wear `breath-attending` (Pattern B inherits from
Pattern A). This makes "what is this node related to" visible at a
glance without adding chrome — the breath itself is the answer.

#### 10.10.5 Pattern B — endpoint bloom recipe

Endpoint bloom is **edge-color-inherited**: it picks up the chromatic
color of the edge it sits on (cobalt for ref edges, amber for hover-
related branches, violet for special, etc.). Implementation is a stack
of three drop-shadows on the endpoint circle:

```css
.endpoint-cobalt {
  filter:
    drop-shadow(0 0 3px  rgba(59,107,255, 0.65))   /* core */
    drop-shadow(0 0 6px  rgba(156,196,232, 0.35))  /* mid */
    drop-shadow(0 0 10px rgba(180,140,255, 0.18)); /* iridescent outer */
}
```

`.endpoint-amber`, `.endpoint-violet`, `.endpoint-slate` follow the same
3-stop pattern with edge-color in the core stop and progressively cooler
tones outward. The outermost stop is always a hint of iridescence — it
visually ties the endpoint family to the same atmospheric palette as
Pattern A.

Endpoint bloom only appears at the endpoints of edges that are
**selected, hover-related, or connected to a selected node**. Idle
edges stay endpoint-bloom-free — otherwise every junction lights up
and the discipline collapses.

#### 10.10.6 Discipline cap — the rules of arbitration

At any moment the canvas must show **at most**:

- **≤1 HERO** bloom (selected focal surface)
- **≤3 SECONDARY** bloom
- **≤4 AMBIENT** bloom
- **Total ≤8** simultaneously visible bloom instances

When more candidates qualify than the cap allows, drop in this order:

| Tier | Priority (keep) | Drop |
|---|---|---|
| HERO | Most recent selection | older selections lose hero, downgrade to secondary if approval/polaroid roles apply, else no bloom |
| SECONDARY | (1) approval pending  (2) polaroid current take  (3) processing nodes by `created_at` ascending | excess processing nodes lose bloom but keep status dot |
| AMBIENT | (1) LeftRail active mode  (2) Dock when focused  (3) Hover ports  (4) selected/hover edge endpoints | excess endpoints stay color-tinted but lose drop-shadow stack |

The cap exists because the v0.4.2 review surfaced "everything wears
bloom = nothing reads as focal". Bloom is a scarcity-driven signal.

#### 10.10.7 Layer composition order

Bloom interacts with aura, rim, and the element body. To keep the
layering legible across surfaces, use this z-index discipline on every
bloom-bearing surface:

| Layer | z-index | What |
|---|---|---|
| Bloom (`::after`) | -3 | Outermost atmospheric halo |
| Aura (`::before`) | -1 | Mid-distance atmospheric wash (from §10.3) |
| Element body | 0 (default) | The actual card / button / glass surface |
| Iridescent rim (`::before` of inner wrapper) | 1 | Crisp top-edge spectrum bar (from §10.9) |

The aura sits *between* bloom and the element body — aura adds local
warmth, bloom adds field-radius color. The rim sits *above* the body —
it's the only sharp light in the stack.

**Do not** stack bloom on a surface that already has a hard drop-shadow
in the same color family — the shadow + bloom muddy each other. Either
remove the drop-shadow or downgrade bloom to ambient.

#### 10.10.8 What v0.4.3 explicitly does NOT change

- Bloom recipe (4 radial gradients + screen blend) — unchanged from §10.9.
- Aerogel palette tokens — unchanged from §10.1.
- Pigment grain percentages — unchanged from §10.2.
- Typography breathing — unchanged from §10.5.
- Bloom is still **never** applied to region containers, static media
  nodes, chips, the top bar, or take strip non-current takes.

#### 10.10.9 Updated implementation order (replaces §10.9's order)

The bloom roster slots in after the rim step:

1. Brand recolor — cobalt blue primary (from v0.4 §6 step 1).
2. Atmospheric palette tier (§10.1).
3. Canvas background — atmospheric wash overlay (§10.4).
4. Aura recipe update — multi-stop atmospheric (§10.3).
5. Iridescent rim on focal workbench + polaroid primary + approval
   bubble (§10.9).
6. **(new)** Bloom variable system — `--bloom-strength` + the three
   tier classes (§10.10.2).
7. **(new)** Pattern A roster — HERO/SECONDARY/AMBIENT deployment per
   §10.10.3, with discipline cap (§10.10.6).
8. **(new)** Breathing animations — `breath-generating` + `breath-
   attending`, including the selected-node-to-edge-endpoint
   inheritance rule (§10.10.4).
9. **(new)** Pattern B endpoint bloom — per-color 3-stop drop-shadows
   on selected / hover-related / connected edges (§10.10.5).
10. Pigment grain bump — 6% → 10-12% + add canvas layer (§10.2).
11. Connector lines — chromatic-semantic edges (from v0.4 §6 step 2).
12. Selected edge halo — 2-stop drop-shadow (§10.6).
13. Icon discipline (from v0.4 §6 step 3).
14. Typography breathing — spacing-only tweaks (§10.5).
15. Focal frosted glass (from v0.4 §6 step 4).
16. Polaroid stack (from v0.4 §6 step 5).

---

## 11. v0.4.3 → v0.4.4 patch — boundary discipline

### 11.1 The bug v0.4.4 fixes

Review of v0.4.3 mockup against the user's reference 03-instagram.jpeg
surfaced a structural problem: bloom on a translucent glass frame
dissolves the boundary between surface and canvas. The Moonlit chase
workbench in v0.4.3 reads as "a region of the canvas that happens to
have brighter color in it," not as "a card sitting on the canvas."

The reference shows the correct mental model: **a clearly-bounded
opaque card** with bloom radiating *outside* it. Bloom is the card's
halo, not its substance. Three things must be true for that to work:

1. The card's fill must be opaque enough that the canvas does not
   visually leak through it.
2. The card must have a visible edge (border + lift shadow).
3. The bloom must be physically prevented from painting on the card
   interior.

v0.4.3 violated all three. v0.4.4 restores all three for the
"content-container" tiers (HERO + SECONDARY). AMBIENT bloom-bearing
surfaces (rail mode, dock focus, hover ports, edge endpoints) are
exempt — they're chrome on structural rails, not content surfaces, and
the boundary problem does not apply.

Reference mockup: `docs/design/prototypes/atelier-v0.4.4-mockup.html`.

### 11.2 Opaque-base recipe

Add to `:root`:

```css
--opaque-fill:   rgba(20, 24, 42, 0.94);
--opaque-border: rgba(156, 196, 232, 0.18);
```

Add the `.opaque-base` class:

```css
.opaque-base {
  background: var(--opaque-fill) !important;
  backdrop-filter: blur(8px) !important;
  border: 1px solid var(--opaque-border) !important;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.04),   /* inner top highlight */
    0 12px 24px -8px rgba(0, 0, 0, 0.6)        /* lift shadow */
    !important;
}
```

Why `!important`: surfaces that adopt `.opaque-base` (workbench,
polaroid l1, approval bubble, processing node) already carry their own
background / border / shadow. The policy must win over those defaults
deterministically.

Three layers do the work:

| Layer | What | Why |
|---|---|---|
| Fill `rgba(20,24,42,0.94)` | 94% opaque, slight cobalt tint | Reads as solid card; tint keeps it inside the Aerogel palette |
| Border `rgba(156,196,232,0.18)` | 1px sky-tinted hairline | Carves the surface out of the canvas |
| `inset 0 1px 0 rgba(255,255,255,0.04)` | barely-there top highlight | Subliminal "surface has form" — like printed paper catching light |
| `0 12px 24px -8px rgba(0,0,0,0.6)` | lift drop-shadow | Says "this card floats above the canvas" |

The 8px backdrop-blur is retained to keep a hint of glass material —
the card is "almost solid", not "perfectly solid". This is the
difference between Aerogel and flat material design.

### 11.3 Bloom isolation — masking the interior

Bloom remains on `.bloom::after`. When `.opaque-base` is also present,
the `::after` gets a mask that subtracts the frame interior:

```css
.opaque-base.bloom::after {
  padding: calc(40px * var(--bloom-strength));
  box-sizing: border-box;
  -webkit-mask:
    linear-gradient(#000 0 0) padding-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
}
```

How the mask works: the `::after` has `inset: calc(-40px * strength)`
(it extends past the frame). With `padding: calc(40px * strength)` +
`box-sizing: border-box`, the content-box of `::after` coincides
exactly with the frame body, while the padding-box equals the full
`::after`. Compositing the two mask layers with XOR (`mask-composite:
exclude`) yields **only the padding ring** — bloom is visible only
outside the frame.

Browser support: `-webkit-mask-composite: xor` is supported in
Safari/Chromium since 2023. Firefox uses `mask-composite: exclude`
(both prefixed forms are listed).

### 11.4 Aura removal on opaque-base surfaces

Aura (§10.3) was designed to atmospherically render *inside*
translucent glass. With 94% opaque fill, aura is 94% blocked — invisible
in practice. Worse, the "outer glow" role aura partially fulfilled in
v0.4.2 is now fully owned by bloom (Pattern A), so aura on a bloom-
bearing surface is redundant.

**Rule.** Any surface that adopts `.opaque-base` must not also adopt
`.aura-focal` (or any other aura variant). In HTML class lists:

```diff
- <div class="workbench aura-focal grain bloom bloom-hero ...">
+ <div class="workbench opaque-base bloom bloom-hero ...">
```

Aura is **not deprecated** — it remains the correct treatment for
translucent ambient surfaces (selected sandbox, hover-state media
nodes, region containers) that do not carry bloom. Aura's role shifts
from "atmospheric wash for everything important" to "translucent-glass
descriptor for the still-glassy minority."

Grain (the `.grain` class) is also dropped from opaque-base surfaces —
SVG noise overlay at 7-11% on an opaque fill is invisible in practice.
Grain continues to live on the canvas + any remaining translucent glass
surfaces.

### 11.5 Applicability roster

The policy applies to **HERO + SECONDARY content containers only**:

| Surface | v0.4.3 | v0.4.4 |
|---|---|---|
| Selected DraftWorkbench (HERO) | `aura-focal grain bloom bloom-hero` | `opaque-base bloom bloom-hero` |
| Polaroid current take (SECONDARY) | `bloom bloom-secondary` | `opaque-base bloom bloom-secondary` |
| Approval bubble (SECONDARY) | `bloom bloom-secondary` | `opaque-base bloom bloom-secondary` |
| Processing node (SECONDARY) | `bloom bloom-secondary` | `opaque-base bloom bloom-secondary` |

The policy does **not** apply to AMBIENT bloom-bearing surfaces:

| Surface | Tier | Why exempt |
|---|---|---|
| LeftRail active mode button | AMBIENT | Already opaque chrome (rail background is solid) |
| Dock when composer focused | AMBIENT | Dock chrome itself is opaque-rendered |
| Hover ports | AMBIENT | Too small for boundary to matter; bloom reads as "lit endpoint" |
| Edge endpoints (Pattern B) | AMBIENT | SVG circles, no fill semantic; endpoint drop-shadows already painted outside |

### 11.6 What v0.4.4 does NOT change

- Bloom recipe (4 radial gradients + screen blend) — unchanged from
  §10.9 / §10.10.
- `--bloom-strength` tier multipliers — unchanged.
- Breathing animations — unchanged.
- Pattern B endpoint bloom — unchanged.
- Discipline cap — unchanged.
- Aerogel palette tokens — unchanged.
- Layer composition order (§10.10.7) is **refined**: on opaque-base
  surfaces, the aura layer (z:-1) is empty; the stack is now
  `bloom (-3) → element body (0) → rim (1)`. The aura layer is
  retained for translucent-glass surfaces.

### 11.7 Updated implementation order (replaces §10.10.9)

The boundary-discipline steps slot in **before** the bloom roster — an
opaque base is a precondition for bloom that reads cleanly:

1. Brand recolor — cobalt blue primary (from v0.4 §6 step 1).
2. Atmospheric palette tier (§10.1).
3. Canvas background — atmospheric wash overlay (§10.4).
4. Aura recipe update — multi-stop atmospheric (§10.3). (Still used
   on translucent-glass surfaces.)
5. Iridescent rim on focal workbench + polaroid primary + approval
   bubble (§10.9).
6. **(new)** `--opaque-fill` + `--opaque-border` tokens (§11.2).
7. **(new)** `.opaque-base` class (§11.2). Apply to HERO + SECONDARY
   content containers; remove `.aura-focal` / `.grain` from the same
   surfaces (§11.4).
8. Bloom variable system — `--bloom-strength` + tier classes (§10.10.2).
9. **(new)** `.opaque-base.bloom::after` mask override (§11.3).
10. Pattern A roster — HERO/SECONDARY/AMBIENT deployment per §10.10.3,
    with discipline cap (§10.10.6).
11. Breathing animations including edge-endpoint inheritance (§10.10.4).
12. Pattern B endpoint bloom (§10.10.5).
13. Pigment grain bump — 6% → 10-12% on canvas only (§10.2). (Removed
    from opaque-base surfaces per §11.4.)
14. Connector lines — chromatic-semantic edges (from v0.4 §6 step 2).
15. Selected edge halo — 2-stop drop-shadow (§10.6).
16. Icon discipline (from v0.4 §6 step 3).
17. Typography breathing — spacing-only tweaks (§10.5).
18. Focal frosted glass (from v0.4 §6 step 4). (Subsumed by opaque-base
    on the focal workbench.)
19. Polaroid stack (from v0.4 §6 step 5).

### 11.8 v0.4.4 round-2 — canvas + chrome cleanup

Review of the first v0.4.4 cut found that fixing per-card boundaries
was necessary but not sufficient. Two further sources of "background
bloom" still competed with node bloom, keeping the canvas reading as
foggy rather than as a neutral ground:

1. **Canvas atmospheric wash** (§10.4 — peach + sky radial gradients
   on `.stage`). Originally added to give the canvas a sense of
   atmosphere, but in practice it functions as a canvas-wide bloom
   that nullifies the contrast bloom-bearing nodes need to read as
   "this surface is lit."
2. **Dock `aura-bottom`** and right-rail `aura-side-left`. These were
   atmospheric chrome glow ringing the canvas perimeter — same
   problem at smaller scale.

Plus a structural issue with the right rail itself: it was translucent
glass, and the dialog bubbles inside it were also translucent. Two
translucent layers stacked have no hierarchy — they read as one foggy
zone. The fix is to invert the translucency: make the rail opaque so
the bubbles' translucency provides the hierarchy.

#### Changes

| Surface | Before | After |
|---|---|---|
| `.stage` background | `canvas-base` + dots + peach radial wash + sky radial wash | `canvas-base` + dots only |
| `.right-rail` | `rgba(18,18,22,0.52)` glass + `aura-side-left` + `grain` | `rgba(14,16,26,0.92)` opaque + 10px blur, no aura/grain |
| `.agent-bubble` (inside opaque rail) | `rgba(0,0,0,0.30)` | `rgba(255,255,255,0.04)` — lifted card on dark panel |
| `.dock` | glass + `aura-bottom` + `grain` | `rgba(14,16,26,0.92)` opaque pill, no aura/grain |

#### Rule extraction

This is the same principle applied at two scales:

- **Bloom belongs to important nodes, not to the canvas.** Atmospheric
  wash on the ground level competes with bloom on the figure level.
  Pick one — and for Atelier, the figure (node bloom) wins because
  it carries semantic meaning ("this is focused / generating / live").
- **Translucent surfaces only stack one layer deep.** When a panel and
  its children are both translucent, hierarchy collapses. The pattern
  to enforce: chrome panels are opaque; content layers on top of them
  are translucent. Translucency is reserved for the topmost interactive
  layer.

§10.4 (canvas atmospheric wash) is therefore **deprecated**. The
canvas should be near-uniform dark with just the dotted grid and
canvas-level grain. Atmospheric expression on Atelier lives in node
bloom + iridescent rim, not in the canvas.

Aura tokens (§10.3) remain valid only for translucent-glass surfaces
that are themselves *not* chrome panels — the residual use cases are
hover-state media nodes and selected sandbox. Both narrow.

#### Updated implementation order (replaces §11.7)

The canvas + chrome cleanup is the **first** step now — without it,
none of the bloom work reads correctly:

1. **(new)** Canvas: strip atmospheric wash, keep dots + grain only
   (§11.8).
2. **(new)** Chrome panels: right rail + dock + (later) top bar all
   go opaque (rgba 0.92). Strip `aura-bottom` / `aura-side-left` /
   `grain` from their HTML class lists (§11.8).
3. Brand recolor — cobalt blue primary (from v0.4 §6 step 1).
4. Atmospheric palette tier (§10.1).
5. Aura recipe — retained only for the narrow residual surfaces noted
   above (§10.3).
6. Iridescent rim (§10.9).
7. `--opaque-fill` + `--opaque-border` tokens (§11.2).
8. `.opaque-base` class for HERO + SECONDARY content containers (§11.2).
9. Bloom variable system + tier classes (§10.10.2).
10. `.opaque-base.bloom::after` mask override (§11.3).
11. Pattern A roster + discipline cap (§10.10.3 / §10.10.6).
12. Breathing animations + edge-endpoint inheritance (§10.10.4).
13. Pattern B endpoint bloom (§10.10.5).
14. Canvas grain only — bump 6% → 10-12% on canvas, removed from
    opaque-base surfaces (§10.2 + §11.4).
15. Connector lines — chromatic-semantic edges (v0.4 §6 step 2).
16. Selected edge halo — 2-stop drop-shadow (§10.6).
17. Icon discipline (v0.4 §6 step 3).
18. Typography breathing (§10.5).
19. Focal frosted glass (subsumed by opaque-base, v0.4 §6 step 4).
20. Polaroid stack (v0.4 §6 step 5).

---

## 12. v0.4.5 — Double frame + editorial buttons

### 12.1 What v0.4.5 fixes

v0.4.4 made every bloom-bearing card uniformly opaque with bloom
outside. That cleared the boundary problem, but two new issues
surfaced on review:

1. **Single-rectangle cards look uniformly "lit"**, not "framed." The
   user's reference (Image Generator card from the canvas-UX batch)
   has a visibly *thicker* outer atmospheric band carrying bloom + rim,
   wrapping a distinctly *darker* inner operating area. Two rings, one
   gap. v0.4.4's single opaque-base card couldn't express this — bloom
   was right at the card edge, so the whole card read as "the lit
   surface" instead of "the lit frame around a stable inner area."
2. **Buttons still read as dev-tool chrome.** Cobalt pill toggles,
   icon-only circular send buttons, compact pill controls — the
   semantics are correct but the *register* is "IDE / SaaS settings
   panel," not "magazine page." The user explicitly called this out:
   wants more whitespace, more stylistic typography, more "creative
   design" feel.

v0.4.5 introduces two distinct patterns to address these:

- **Double-frame shells** (§12.2) for the most important content
  containers — HERO workbench + critical SECONDARY (approval bubble).
  Polaroid current take + processing nodes keep v0.4.4's single
  opaque-base — they're too small for double frame to read cleanly.
- **Editorial button system** (§12.3) replaces all cobalt-pill /
  icon-only / compact-pill chrome with Space Grotesk italic verbs +
  cobalt underline indicators + generous padding.

Reference mockup: `docs/design/prototypes/atelier-v0.4.5-mockup.html`.

### 12.2 Double-frame shells

The structural change: HERO + critical SECONDARY surfaces become two
nested rectangles instead of one.

```css
.opaque-shell {
  background: rgba(28, 32, 52, 0.94);   /* slightly lighter cool tint */
  backdrop-filter: blur(8px) !important;
  border: 1px solid rgba(156, 196, 232, 0.22);
  border-radius: 16px;
  padding: 14px;                        /* the visible gap between shells */
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.06),
    0 16px 32px -10px rgba(0, 0, 0, 0.7);
}

.opaque-shell.bloom::after {
  /* same mask as opaque-base — bloom only outside shell */
  padding: calc(40px * var(--bloom-strength));
  box-sizing: border-box;
  -webkit-mask:
    linear-gradient(#000 0 0) padding-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
}

.opaque-inner {
  background: rgba(10, 12, 22, 0.96);   /* deeper than shell */
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 9px;                   /* tighter inner corner */
  padding: 20px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
}
```

HTML pattern:

```html
<div class="workbench opaque-shell bloom bloom-hero iridescent-rim">
  <div class="opaque-inner workbench-inner">
    <!-- title, prompt, refs, controls, take strip -->
  </div>
</div>
```

Why two shades of dark:

- **Outer shell** is rgba(28,32,52,0.94) — slightly *lighter* than the
  canvas and slightly *cooler* than neutral. This positions it as
  "atmospheric material," distinct from the canvas, ready to hold
  bloom + rim.
- **Inner card** is rgba(10,12,22,0.96) — *darker* than canvas, almost
  black. This positions it as "the recessed workspace" — the eye
  recognizes it as somewhere quiet for content.

The visible gap (14px shell padding) is what reads as "this card has
a frame." Bloom and rim live on the outer shell — they decorate the
frame. The inner card is untouched by atmosphere.

#### Roster

| Surface | v0.4.4 | v0.4.5 |
|---|---|---|
| Workbench (HERO) | single `opaque-base` | double frame: `opaque-shell` outer + `opaque-inner` inner |
| Approval bubble (critical SECONDARY) | single `opaque-base` | double frame |
| Polaroid current take (SECONDARY) | single `opaque-base` | unchanged — too small for double frame to read |
| Processing node (SECONDARY) | single `opaque-base` | unchanged — too small |
| AMBIENT bloom surfaces | unchanged | unchanged |

The 14px shell padding eats screen real estate, which is why
SECONDARY-but-not-critical surfaces stay single-frame. The double
frame is rationed for what *really* matters.

### 12.3 Editorial button system

Three new component classes replace the cobalt-pill / icon-only /
compact-pill chrome.

#### `.btn-editorial`

Replaces workbench Generate button, dock send button.

```css
.btn-editorial {
  font-family: 'Space Grotesk', 'Inter', sans-serif;
  font-weight: 500;
  font-size: 13.5px;
  letter-spacing: -0.005em;
  padding: 11px 22px;             /* was 8 16, now generously bigger */
  background: transparent;        /* no pill bg by default */
  border: none;
  color: var(--text-fg);
  border-radius: 999px;
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
}

.btn-editorial.primary {
  color: var(--brand-300);
  font-style: italic;             /* the editorial signal */
}

.btn-editorial.primary::after {
  content: '→';                   /* replaces icon-only chrome */
  font-style: normal;
  transition: transform 0.15s ease;
}

.btn-editorial.primary:hover::after {
  transform: translateX(3px);
}

.btn-editorial:hover {
  background: rgba(255, 255, 255, 0.04);  /* subtle, not chunky */
}
```

The italic + arrow is the entire affordance. No background pill, no
icon-only circle. "Generate →" or "Send →" reads as a magazine "Read
more →" link, not as a SaaS submit button.

#### `.editorial-toggle`

Replaces Free/Director seg-toggle in both right rail and dock.

```css
.editorial-toggle {
  display: inline-flex;
  align-items: baseline;
  gap: 28px;                      /* generous breathing room */
}

.editorial-toggle .opt {
  position: relative;
  font-family: 'Space Grotesk', sans-serif;
  font-size: 13.5px;
  font-weight: 500;
  color: var(--text-mute);
  padding: 8px 0;
  cursor: pointer;
}

.editorial-toggle .opt.on {
  color: var(--text-fg);
  font-style: italic;             /* active option is italic */
}

.editorial-toggle .opt.on::after {
  content: '';
  position: absolute;
  left: 0; right: 0;
  bottom: 2px;
  height: 1.5px;
  background: var(--brand-400);
  border-radius: 999px;
  box-shadow: 0 0 8px rgba(110, 143, 255, 0.45);  /* subtle cobalt glow */
}
```

This reads as magazine tab-nav: option labels with a cobalt underline
on the active one. No pill background, no `seg on` color block, no
"radio button" affordance suggestion. The italic + underline is the
state, full stop.

#### `.pill-editorial`

Replaces small pill-controls (16:9 / 5s / 4×).

```css
.pill-editorial {
  font-family: 'Space Grotesk', sans-serif;
  font-weight: 500;
  font-size: 12px;
  padding: 7px 13px 7px 14px;
  border: 1px solid rgba(255, 255, 255, 0.10);   /* hairline, not filled */
  border-radius: 999px;
  background: transparent;
  color: var(--text-mute);
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
}

.pill-editorial:hover {
  border-color: rgba(156, 196, 232, 0.35);
  color: var(--text-fg);
}

.pill-editorial .caret {
  font-size: 9px;
  opacity: 0.55;
}
```

Ghost-border pill instead of cobalt-fill pill. The dropdown indicator
becomes a small subtle caret rather than a tinted arrow.

### 12.4 Tone rules

These are normative across the editorial button system:

- **Italic = action.** Active toggle option, primary verb, in-flight
  state indicators ("Awaiting approval"). Static labels stay upright.
- **Cobalt underline (not cobalt fill) = active.** A 1.5px brand-color
  underline with a faint glow replaces the v0.4.4 cobalt-pill active
  state. The pill background is reserved for *standalone* CTAs (none
  in v0.4.5, but the door is left open).
- **Arrow (→), not icon, for "send / submit / generate" verbs.** Icon-
  only circular buttons are dev-tool register; arrow + verb is
  editorial register.
- **Padding floor: 11 vertical, 22 horizontal** on text actions; the
  former 8/16 reads as too tight against the new register.

### 12.5 What v0.4.5 does NOT change

- Bloom recipe, tier multipliers, breathing, Pattern B endpoints —
  unchanged from v0.4.3 / v0.4.4.
- `.opaque-base` (single-frame) is **not deprecated** — it remains the
  correct treatment for SECONDARY content containers that aren't large
  enough to support double frame (polaroid current, processing node).
- Right rail, dock, canvas chrome — unchanged from v0.4.4 round-2.
- AMBIENT bloom surfaces — unchanged.

### 12.6 Updated implementation order (replaces §11.8)

The double-frame + editorial steps slot in after the v0.4.4 work:

1. Canvas: strip atmospheric wash, dots + grain only (§11.8).
2. Chrome panels (right rail + dock + top bar): opaque rgba 0.92 (§11.8).
3. Brand recolor — cobalt primary (v0.4 §6 step 1).
4. Atmospheric palette tier (§10.1).
5. Aura — narrow residual use only (§10.3).
6. Iridescent rim (§10.9).
7. `--opaque-fill` + `--opaque-border` tokens (§11.2).
8. `.opaque-base` for SECONDARY content containers that don't go
   double-frame: polaroid current, processing node (§11.2).
9. **(new)** `.opaque-shell` + `.opaque-inner` for HERO + critical
   SECONDARY: workbench + approval bubble (§12.2). HTML restructure:
   wrap each in `.opaque-inner` child.
10. Bloom variable system + `.opaque-base.bloom::after` mask +
    `.opaque-shell.bloom::after` mask (§10.10.2 / §11.3 / §12.2).
11. Pattern A roster + cap (§10.10.3 / §10.10.6).
12. Breathing + endpoint inheritance (§10.10.4).
13. Pattern B endpoint bloom (§10.10.5).
14. Canvas grain only (§10.2 / §11.4).
15. Connector lines (v0.4 §6 step 2).
16. Selected edge halo (§10.6).
17. Icon discipline (v0.4 §6 step 3).
18. Typography breathing (§10.5).
19. **(new)** Editorial button system — `.btn-editorial` /
    `.editorial-toggle` / `.pill-editorial` (§12.3). Sweep all
    interactive surfaces: Free/Director toggles (right rail + dock),
    Send button, workbench Generate button, pill controls. Tone rules
    per §12.4.
20. Polaroid stack (v0.4 §6 step 5).

