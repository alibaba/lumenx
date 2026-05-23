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
