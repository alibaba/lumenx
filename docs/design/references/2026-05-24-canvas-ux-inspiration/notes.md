# 2026-05-24 — Atelier v4 Canvas UX Inspiration Batch

> Feeds the post-P0 visual evolution decision for Atelier v4. Goal:
> distill a shared vocabulary of visual / interaction patterns the user
> already loves, then judge fit against the infinite-canvas creator
> cockpit.

## Wave 1 — 4 assets

### `01-ai-dashboard.jpeg` — "Visual Mood Experiment"

- **Source**: unknown (looks like a Dribbble/Behance piece for an AI image+video gen dashboard)
- **Observation**:
  - Page-level: pale **sage-green ambient backdrop** wraps the dark canvas — the canvas is "an artifact placed on a desk", not the whole screen.
  - Canvas: near-black (#0c0c0e) with a **faint dot-grid**.
  - All connector lines + accent UI tokens are a single **electric emerald**, used sparingly (active state, edges, hub-node center bolt).
  - Three discrete node clusters: left (AI config), top (prompt), bottom-center (image settings) → all flow into a **central hub icon** → output card on the right.
  - Output is a **physically-stacked photo pile**: 3 cards offset by ~10px each, rounded, with a soft outer glow — reads as "multiple takes, one foregrounded".
  - Bottom floating icon toolbar: 6 round-ish icons; the active one wears the emerald accent + a tinted background pill.
  - Left rail: ~50px thin, icon-only.
- **Naming**:
  - **"Artifact-on-desk" framing** — the canvas is a tablet/window inside a larger ambient surface (the sage backdrop). Used to make the dark canvas feel like a focused workspace, not a void.
  - **Mono-accent system** — exactly one chromatic accent (emerald) carries every signal: active, edge, primary. Everything else is grayscale. Gives the UI a strong identity without color noise.
  - **Hub-and-spoke flow** — multiple input nodes converge on one center node (the bolt icon), which then fans out to results. Communicates "all inputs into one synthesis".
  - **Polaroid stack / take pile** — overlapping cards with slight offset to suggest "more like this exists, this is the one we're looking at".
- **Canvas fit**:
  - **Mono-accent**: Atelier currently splits status colors (blue/amber/emerald/red); a strong single-accent identity is appealing but would have to live alongside our semantic status palette. Could become: "primary brand color emerald, semantic status reserved for status only".
  - **Hub-and-spoke**: matches Director Mode's structured output pattern — the hub icon could be the auto-region center marker.
  - **Take pile**: directly transplantable to TakeTimeline. Today our take strip is a horizontal row of 36×36 tiles; the pile metaphor would compress 3+ tiles into one stacked-card visual when collapsed, expanding to the strip on hover.
  - **Artifact-on-desk**: would require a significant chrome change — currently Atelier is full-bleed dark. Probably better adopted only on **landing** / **project switcher** views, not the working canvas.

### `02-unknown.jpeg` — "Welcome to Flora"

- **Source**: Flora (florafauna.ai by guess of branding) — AI image/video tool with collaborative canvas
- **Observation**:
  - Pure black canvas, **no dot grid visible at this zoom**.
  - Two large, vivid **rendered images** dominate — chromatic gradient flowers (sky blue → orange → magenta → pink). Background is black so the imagery feels neon, projected.
  - Each image has a **black caption card** with a short paragraph beneath it.
  - **Person tags** ("Weber" black, "Alex" yellow, "Ethan" green) — colored pill capsules floating as if pinned to canvas regions. Looks like collaborator presence + comment anchors in one.
  - Mid-canvas: a **chat thread card** with model response containing a hex color list and a small image thumbnail — i.e., the AI assistant lives inline with the canvas, not in a side panel.
  - Curved connector lines between the chat card and the result images — bezier with very gentle curvature.
  - Left rail: ~40px ultra-thin, only essential icons.
  - Top-right: tiny avatar bubbles + share button + play (?) icon.
- **Naming**:
  - **Gallery-on-black** — pure black canvas treats the workspace as a **museum vitrine** rather than a desktop. Imagery is the only color source.
  - **Floating chromatic capsule** (for the person tags) — a 12×24px pill with a vivid fill and dark text, sitting in canvas world coordinates rather than in chrome.
  - **Inline assistant card** — chat bubble lives on the canvas, with edges that connect it to the artifact it's talking about. The opposite of a "side panel agent".
  - **Pinterest-density** — extreme negative space, content takes < 30% of pixels.
- **Canvas fit**:
  - **Gallery-on-black** + **chromatic-content-only**: Atelier today has gray-dotted background and quiet glass borders — we have the foundation but our content is not yet visually celebrated. The takeaway is "let the user's generations be the loudest thing on screen, fade chrome to whisper".
  - **Person tags as canvas-coord pills**: Atelier has no collaboration model — but the *idea* of "a small colored pill anchored to a node in world coordinates" is exactly what Atelier needs for **node-level metadata** (e.g., region color dot, agent-of-origin badge, character/style category). We can borrow the visual without adopting multiplayer yet.
  - **Inline assistant card**: today Atelier's Agent is in the right rail. Bringing planner responses into the canvas as a connected card would change the cockpit's center of gravity — interesting but high-impact. Likely a v5 idea, not v4.
  - **Pinterest-density**: directly applicable. Our DraftWorkbench (480px) and node cards could shed visual weight further.

### `03-instagram.jpeg` — "image generation v3"

- **Source**: appears to be a Dribbble/Instagram mockup of a node-based AI image generator, shown on a tilted monitor
- **Observation**:
  - Dark canvas with **visible dot grid** — denser than the others.
  - 5 nodes connected in a near-linear flow: Model → (Positive ∥ Negative) → Image Generator → Preview Image.
  - Each node has:
    - **status dot** in top-right (gray/yellow indicating state)
    - **collaborator pin** at a corner — a colored pill ("Paul" yellow, "Mona" pink, "Kate" blue) — clearly the Figma multiplayer pattern landed on a node graph
    - rounded card with 1px hairline + faint inner glow
  - Top chrome: **`Workflow | Edit | Help` text tabs** (flat, no underline at rest) on the left + central **breadcrumb pill** ("Black bear" with prev/next chevrons) + right-side **Share** button (white-on-dark high-contrast).
  - Top-center: a very small mono caption "image generation v3" — a build-label / version stamp aesthetic.
  - Bottom-right: utility action bar with scale/format toggle, eraser, copy, etc. — quiet icons.
  - Bottom-center floating: a **second composer panel** (prompt textarea + tiny icon row) — duplicate of the in-canvas Positive prompt, but as a floating "current intent" pinned to the screen. Possibly the active edit target.
  - Preview Image (right): tall card with a soft pastel cloud rendering + caption "Final Result · Minimalist illustration of a black bear...".
- **Naming**:
  - **Multiplayer pins** — colored name pills attached to the corner of a node, showing who is currently editing/looking at it. Mirrors Figma cursors but anchored to canvas objects, not pointer positions.
  - **Build-label caption** — a tiny mono mark like "v3" or "build #..." that grounds the entire UI in a "I am a tool with a version" identity. Adds craft.
  - **Breadcrumb pill** — central project-name component with prev/next chevrons. Suggests "this is one of a sequence; you can step".
  - **Screen-pinned composer** — a floating prompt bar at the bottom always visible, regardless of which node is selected. Acts as a "global current edit" surface.
- **Canvas fit**:
  - **Multiplayer pins**: same observation as #02 — visually transplantable as a node metadata pill, no multiplayer required.
  - **Build-label caption**: Atelier could earn a "v4 · canvas-uplift" mono stamp somewhere unobtrusive. Low cost, high craft signal. Lives well as a top-bar adjacent caption.
  - **Breadcrumb pill**: Atelier projects are independent canvases today; the breadcrumb would matter only if we ever introduce "scene sequence" navigation. For now: not yet.
  - **Screen-pinned composer**: this conflicts with our Sprint A decision (DraftWorkbench in-place workbench replaces floating Composer). But it raises a real question: when **no draft is selected**, should there still be an always-available intent input? Today the user has to click into a draft. A screen-pinned global composer could be the answer for the "agent-mode" flow without going through the right rail.

### `04-tranmautritam.jpeg` — "New Character" (Tran Mau Tri Tam)

- **Source**: Tran Mau Tri Tam (@tranmautritam on X) — well-known designer
- **Observation**:
  - **Split-pane**: left = node canvas with dot grid, right = full-size character preview + timeline.
  - Left pane nodes: small **"Ideas" / "References" / "Settings"** cards. The Settings card is **frosted glass** with visible blur, showing 5 rows (Mode / Trim / Think / Voice / Music), each row prefixed with a colored icon glyph (orange/blue/green/yellow micro-coins).
  - Connector lines: **bright orange + blue** — branded chromatic edges, not just gray.
  - Right pane: large rendered Pixar-style 3D character on transparent gradient bg. Below it, a **purple timeline track + waveform strip** — clearly an audio/video assembly view.
  - Top: **tab bar** `New Character | Framer Templates | Untitled +`, right side `Export` (bold purple primary).
  - Bottom-left: **inline code preview pane** — "Thoughts for 15s" with `import { CuteBoy } from "./components/3dcuteboy-animation"` — reveals the output type is **code** (likely React component).
  - Left rail: ~50px, ~10 icons stacked.
  - Right rail: ~40px, action icons (favorite, share, etc.).
- **Naming**:
  - **Split workbench** — canvas on one side, full-quality preview on the other, both visible at all times. Closer to a code-editor split than a single-surface canvas.
  - **Frosted glass settings node** — translucent panel with visible Gaussian blur. Heavier material than Atelier's current "1px hairline on dark" approach.
  - **Chromatic edges** — connector lines are not gray; they're branded colors. Orange for one signal type, blue for another. Edges become semantic, not just structural.
  - **Micro-coin row icon** — a small filled circle (10-12px) prefixing each row in a settings panel, color-coded per row type. Looks like RGB LEDs on hardware.
  - **Always-visible final preview** — output is not a node, it's a dedicated surface. The canvas works toward the preview rather than containing it.
- **Canvas fit**:
  - **Split workbench**: a significant departure from Atelier's "single unified canvas" thesis. But the right-side preview is essentially what a selected take could become if we let it: when a take is selected, dedicate the right 30-40% of the viewport to its high-quality view + timeline. Could be a Sequence-mode treatment.
  - **Frosted glass settings node**: stronger material than current Atelier. Worth experimenting on the DraftWorkbench panel — would give it more weight as the "thing you're working in" without adding chrome.
  - **Chromatic edges**: directly applicable. Today every Atelier ref edge is the same dashed gray; coloring edges by their semantic role (reference, branch, attachment-to-region, sequence-order) would make the graph instantly more legible.
  - **Micro-coin row icons**: a small but high-craft pattern. Easy to retrofit onto our settings rows in Composer / DraftWorkbench / RightRail.
  - **Always-visible final preview**: tension with our "everything's a node" approach. Could be a per-mode opt-in (e.g., Sequence mode dedicates the right pane to the playback preview).

## Wave 1 synthesis

**The 4 references converge on a recognizable design philosophy. Call it the "creative cockpit" school:**

1. **Dark canvas as gallery, content as color.** Chrome whispers, generations shout. All 4 use pure or near-pure black; vibrancy lives only in the user's output. Atelier already commits to this, but our current density and grayscale palette mean we're not yet *celebrating* the output enough.

2. **Curved connectors, never straight.** Every reference uses bezier flow lines. Three of the four use a **single accent color** for all edges (emerald, orange, multi-hue). None use schematic straight angled lines — the visual language is organic, breath-like, not electrical-diagram-like.

3. **Node cards on a dot-grid: quiet container, loud contents.** Universal pattern across all 4: rounded dark card + 1px hairline border + faint inner glow + dot grid behind. We have this. The differentiator is what's *inside* the card — and how much breathing room around it.

4. **Identity / state lives on small, sharply-colored pills.** Multiplayer pins (#03), person tags (#02), category tags, status dots — all are tiny saturated capsules attached to node corners. Maximum signal density per pixel. We have status dots; we don't yet have categorical or identity pills with this much character.

5. **The result deserves the most beautiful surface.** Polaroid stack (#01), oversize preview card (#03), full-pane preview (#04), neon image rendering (#02). All 4 references give the output **noticeably more visual weight** than the inputs. Atelier today treats input drafts (DraftWorkbench, 480px) and result takes (36×36 strip) inversely — the workbench is bigger than the result. This is backwards relative to all 4 refs.

## Wave 1 — terminology cheat sheet

When you want to say "I want this kind of thing", these are the words:

| You want… | Term | Reference |
|---|---|---|
| One brand color, everything else gray | **Mono-accent system** | #01 emerald |
| Pure black w/ user content as only color | **Gallery-on-black** | #02, #04 |
| Multiple takes shown as offset cards | **Polaroid stack / take pile** | #01 |
| Inputs converge to a central hub icon | **Hub-and-spoke flow** | #01 |
| Small saturated pill attached to a node | **Floating chromatic capsule** | #02, #03, #04 |
| Bezier line in branded color, not gray | **Chromatic edge** | #04 |
| Tiny mono "v3" version stamp | **Build-label caption** | #03 |
| Settings panel with visible blur | **Frosted glass node** | #04 |
| Row prefix dot, color-coded by row type | **Micro-coin row icon** | #04 |
| Canvas in the left, full preview on right | **Split workbench** | #04 |
| Inline AI response card on canvas | **Inline assistant card** | #02 |
| Always-visible bottom prompt input | **Screen-pinned composer** | #03 |
| Subtle "tool placed on a desk" framing | **Artifact-on-desk** | #01 |

## Wave 1.5 — `03-instagram` corrected (missed observation)

### Missed in initial analysis: **iridescent rim glow**

The user flagged that I missed a critical technique on `03-instagram.jpeg`:
**a multi-hue spectrum glow that bleeds along the top edge of certain
nodes** (specifically the "Image Generator" node header) and the
bottom "screen-pinned composer" pill. It reads as pink → violet → sky
→ mint **all at once**, soft and blurred — not a single color, not a
gradient between 2 colors, but a **spectrum band**.

This is different from the `hidden warm bleed` I noted on the Arpan
poster (single warm color hidden in a cool dominant gradient). The
iridescent rim is **the full prismatic spectrum laid along an edge**,
like:

- Apple Vision Pro selected-state rim
- Arc Browser tab bar active indicator
- Oil-slick on water surface
- Holographic foil on a credit card
- Aurora borealis (the visual quality, not the meteorological reality)

**Naming**: `iridescent rim glow` (industry term, with synonyms
`aurora bloom`, `holographic edge`, `spectral rim light`). Distinct
from `hidden warm bleed`.

**Why it matters for Atelier**: this is the missing piece on focal
elements. v0.4.2 added cobalt + sky + peach aerogel aura behind
focal surfaces — but the **top edge** of the focal workbench still
reads as flat. A 2-3 px iridescent rim on the top edge would give
the focal moment a "lit by something we can't see" feeling that pure
gradient aura doesn't achieve. v0.4.3 (or in-place v0.4.2 update)
adds this.

The card-03 entry in shelf.html has been updated to include this as
Reproduction 3 with a live HTML/CSS demo (a multi-hue blurred bar
along the top of a card).

## Wave 2 — Arpan addition (single ref)

### `05-arpan-karmakar.jpeg` — "Why Less Is More" newsletter cover

- **Source**: Arpan Karmakar (@thearpankarmakar) on Threads; design for
  KriateDesign Newsletter #025
- **Observation**:
  - Editorial poster format, portrait. Top 2/3 is pure atmospheric
    gradient sky; bottom 1/3 is content.
  - **Color**: a multi-stop gradient — sky blue (~#9cc4e8) dominant,
    fading to almost white near the horizon line at ~70% height, with
    a single hidden bleed of peach/coral (~#e8b89c) in the upper-right
    corner. The blue is **light, soft, atmospheric** — NOT cobalt /
    ocean / midnight. Reads as morning sky or watercolor wash.
  - **Heavy film grain** across the whole gradient — visually ~12-15%
    opacity. The grain is what makes the color feel like physical
    pigment on paper rather than digital fill. This is the editorial
    print quality.
  - **Typography**: hero "Why Less Is More" set huge, geometric heavy
    sans, full white, very tight to the bottom-left of the page; tiny
    mono labels at corners (NEWSLETTER / //025 / SUBSCRIBE) at ~9-10
    px, also white; body text small multi-column white; brand
    "kriatedesign" in italic display serif. **All text white**.
  - Massive **negative space** in the top 2/3 — the page is mostly
    sky. This is the editorial poster move.
- **Naming**:
  - **Aerogel gradient** — the soft, multi-stop, atmospheric wash that
    has no visible "stops" — it feels continuous like sky or fog. Not
    `linear-gradient(blue, white)` flat; more like radial + masked +
    multiple layers blended.
  - **Pigment grain** — heavy visible film grain (10-15% opacity)
    applied as a noise overlay. The signature of editorial / print /
    physical-media aesthetic.
  - **Hidden peach bleed** — a small off-key warm color (peach,
    coral, rust) intentionally placed in a corner of an otherwise
    cool gradient. Adds depth + suggests three-dimensional atmosphere
    rather than two-color flatness.
  - **Sky on poster** — a large editorial format where the top 2/3 is
    pure atmospheric color and the bottom 1/3 is dense content. The
    "weight at the bottom" reads as gravity; the sky reads as space.
- **Canvas fit (for Atelier)**:
  - Aerogel gradient: **directly transplantable to Atelier focal aura**
    — currently the aura is a single cobalt radial gradient. Upgrade
    to multi-stop: cobalt center → sky blue mid → peach hint edge →
    transparent. The peach hint is the critical detail (warmth on
    cool) that makes it feel "alive" not "tinted".
  - Pigment grain: **directly transplantable** — currently Atelier
    has 6% grain on glass surfaces; bump to 10-12% AND extend to the
    canvas background (not just glass).
  - Sky on poster: less directly applicable (Atelier is a working
    canvas not a poster), but suggests the **landing screen / project
    picker / hero state** of Atelier could use this format. v0.5 idea.
  - Soft sky-blue palette: **most important takeaway** — the cobalt
    we picked is too dark / too saturated. Need to introduce a
    softer "atmospheric blue" sibling color and use IT for
    aura / background tint, while keeping cobalt for structural
    high-contrast uses (CTA, edges).

## Cross-wave DNA (final)

> Wave 2+ deferred — the user locked her preferences off wave 1 alone
> via a 5-question grilling session. The motifs below are her **named
> preferences**, not just observations.

The 4 references converge on the **"creative cockpit" school**, and
the user's specific draws within it are these four:

1. **框体 (frame chrome)** — chrome whispers by default, becomes
   material (frosted glass) only on the active edit surface. The
   *contrast* between static-flat and focal-glass is the premium signal.
2. **连线 (connector lines)** — bezier curves with **chromatic-semantic
   coloring** (emerald=ref, amber=branch, violet=sequence). Default
   opacity drops sharply so edges read as ambient infrastructure until
   asked about. Endpoint dots appear only on hover/select.
3. **Takes 折叠 (polaroid stack)** — multiple takes collapse to a
   3-layer offset stack on the canvas (replacing today's spread-out
   tiles); hover fans them out into a horizontal strip; the
   inside-workbench take strip handles fine-grained navigation.
4. **简约图标 (icon restraint)** — lucide library kept, but stroke
   tightened to **1.25-1.5px**, sizes disciplined to 4 steps
   (11 / 13 / 15 / 18), active color → brand emerald.

Underlying these, one **brand-identity decision** fell out:
**emerald-400 (#34d399) replaces the legacy purple as Atelier's
primary**. This makes the four dimensions internally self-consistent
(ref-edge color = brand color = active-icon color = focal CTA color).

The full specification is captured at
[`docs/design/atelier-DESIGN-v0.4.md`](../../atelier-DESIGN-v0.4.md);
the first visual draft is at
[`docs/design/prototypes/atelier-v0.4-mockup.html`](../../prototypes/atelier-v0.4-mockup.html).

## Open questions / decisions deferred

- **Mono-accent vs semantic palette**: ref #01 has one accent color; Atelier needs status colors (failed/processing/completed). Can we have both — a brand accent that's distinct from status, or do we collapse them?
- **Result celebration**: should Atelier introduce a "primary take" focal surface (always-visible right pane, à la #04) when a take is selected? Or keep takes inline as today and just make the chrome thinner?
- **Inline assistant card vs side rail**: refs #02 + #03 move the assistant into the canvas. Our current AgentPanelV3 is firmly in the right rail. Is the move to canvas a v4 direction or a v5 question?
- **Chromatic edges semantic mapping**: if we go this way, what's the legend? E.g. emerald = reference, amber = branch, violet = sequence-order, gray = generic? Needs DESIGN.md anchor.
- **Density target**: refs all run at much lower density than Atelier. If we adopt this, the 480px DraftWorkbench probably grows / shrinks differently — and the candidate-tile spread on the canvas may need a "less is more" treatment.
