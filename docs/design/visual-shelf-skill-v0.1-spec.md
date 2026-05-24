# Visual Shelf — Skill v0.1 Spec

> A self-contained spec for a new Claude Code skill. Hand this file to
> `/skill-creator` to scaffold the actual `~/.claude/skills/visual-shelf/`
> directory. Suggested name `visual-shelf`; alternatives at the bottom.
>
> Derived from a 5-question grilling session (2026-05-24). All decisions
> are recorded inline as design rationale so future re-grills can re-open
> any branch.

---

## 0. Why this skill exists

The user collects visual reference assets (images today; possibly video
later) as a daily habit. She is **not a frontend / design professional**
and is missing the vocabulary that would let her describe what she
likes. She wants a tool that, given a folder of images:

1. Shows her each image alongside a **live HTML reproduction of its
   core visual moves** so she can see "this effect can be reused".
2. Names the techniques in industry-standard terms with
   **plain-language explanations** so she internalizes vocabulary.
3. As her collection grows, **extracts cross-image DNA** (recurring
   colors / techniques / components / typography rules) so she
   discovers her own taste empirically.
4. On request, applies that DNA to a new website project as a
   **design-system page + mockup**.

The skill is intentionally one skill with **progressive disclosure**:
the same `/visual-shelf` invocation handles all three modes; Claude
infers which mode from the user's prompt.

---

## 1. SKILL.md frontmatter draft

```yaml
---
name: visual-shelf
description: |
  Turn a folder of visual reference images into a self-contained
  shelf.html page that displays each image, reproduces its core
  visual effects live in HTML/CSS, extracts color palettes, and
  teaches the design terminology behind every move. As the folder
  grows, extracts cross-image visual DNA. On request, applies that
  DNA to a target project as a design-system page plus mockup.
  Use this skill whenever the user drops a folder of reference
  images and wants to (1) understand what they're looking at, (2)
  learn the design vocabulary for it, (3) discover the common taste
  across a collection, or (4) apply that taste to a new website
  project. Trigger on phrases like "看一下这批参考"、"提取一下风格"、
  "把这个风格用到项目"、"visual shelf"、"design DNA from these
  images"、"reproduce this style". Especially useful for users who
  are not professional designers and want plain-language explanations.
---
```

The description is intentionally pushy (per skill-creator guidance)
because the skill needs to be triggered confidently across both
Chinese and English prompts.

---

## 2. Mode dispatch (natural language, no flags)

The skill body MUST contain a dispatch table Claude reads at
invocation time:

| Prompt signals | Mode | Action |
|---|---|---|
| Just a folder path; no other intent ("看一下"、"分析一下"、"learn from this batch") | **Shelf mode** (default) | Step 1 only — display + reproduce + teach |
| User mentions DNA / 共性 / 共同 / extract / 提取 — but no target project path | **DNA mode** | Step 1 + enhance the cross-image DNA section in notes.md |
| User mentions apply / 应用到 / 套用到 / use this style on / make a mockup for + a target path | **Apply mode** | Step 1 + Step 2 (write design-system.html + mockup.html + dna-source.md to target) |

Edge cases:
- Apply mentioned but no target path supplied → ask once for the path; don't guess.
- Folder has < 3 images and user asks for DNA / Apply → tell them
  cross-image DNA needs at least 3 distinct references; run Shelf
  mode only and suggest adding more.
- Folder is empty → fail with "drop at least one image and re-run".

End of Shelf-mode output should include a short footer like:

> Want to extract the common style DNA from this batch? Just say
> "提取 DNA". To apply this style to a project, say "把这个风格用到
> `<your project path>`".

---

## 3. Input contract

- **Type**: a folder path. Either passed positionally
  (`/visual-shelf ./refs/`) or referenced inside the prompt
  (`/visual-shelf 看一下 ./refs/ 这批图`).
- **Folder contents**: one or more raster images. Accepted extensions:
  `.jpg .jpeg .png .webp .heic .gif` (gif treated as still frame).
  SVG **rejected** with a friendly message (vector logos aren't
  representative of "style").
- **Per-batch size**: 1-20 ideal; > 20 prints a soft warning
  recommending split into themed sub-batches, but proceeds.
- **Filenames**: no convention required. Skill uses original
  filenames. If multiple files have unwieldy names (long, with
  spaces / emoji), skill may **suggest** renaming to
  `NN-slug.ext` for future tidiness but never auto-renames.

---

## 4. Output contract

### 4.1 Files written

| File | Mode | Location |
|---|---|---|
| `shelf.html` | every mode | input folder |
| `notes.md` | every mode | input folder |
| `design-system.html` | Apply | target path |
| `mockup.html` | Apply | target path |
| `visual-dna-source.md` | Apply | target path |

### 4.2 `shelf.html` form

- Single self-contained HTML file with **reference-based** image
  loading (`<img src="NN.jpg">` reading from the same folder).
  Do **not** base64-embed images — keep file under ~500 KB even with
  20 images.
- Embedded CSS in `<style>` block; no external CSS files; no JS
  unless a specific reproduction demands it (see §6.2).
- Google Fonts loaded via `<link>` if any non-system font is used in
  reproductions — call out which font in the loaded set.
- Top of page: hero with batch slug (derived from folder name),
  creation date, asset count.
- Body: one "analysis card" per image (full spec in §6).
- Tail: cross-image DNA section (rendered only when ≥ 3 images),
  then global glossary, then a small "regenerated YYYY-MM-DD
  HH:MM" build label.

### 4.3 `notes.md` form

Three required sections, separated by `## ` headings:

```markdown
# <batch slug>

> Visual shelf analysis — last updated YYYY-MM-DD HH:MM.

## Per-image analysis

### `01-foo.jpg`
- **Tags**: tech, comp, color
- **Observation**: 2-3 sentence summary.
- **Reproductions**:
  1. **diffuse aura** (`tech`) — short technical name + 1 line of plain explanation.
  2. ...
- **Palette**: list of hex colors with assigned poetic names.
- **Source**: URL if known, else "unknown".
- (optional) `<!-- edited by user -->` marker — present means skill
  must NOT overwrite this entry on rerun.

### `02-bar.jpg`
...

## Cross-image DNA

(populated when ≥ 3 images and DNA mode triggered, or on every Apply.)
- **Recurring [tech]**: ... (seen in 4/5 images)
- **Recurring [color]**: ... (cobalt-family colors across 3/5)
- **Recurring [comp]**: ...
- **Recurring [type]**: ...

## Glossary

(populated cumulatively from every analysis.)
- **diffuse aura** — 通俗解释 …
- **frosted glass** — …
- ...
```

### 4.4 Rerun behavior — incremental idempotent

- Skill reads existing `notes.md` if present.
- For each image in the folder:
  - If an entry exists AND is marked `<!-- edited by user -->`,
    **skip** (preserve user edits verbatim).
  - If an entry exists without the marker, **regenerate**.
  - If no entry exists, **create** new analysis.
- For images in `notes.md` that no longer exist in the folder,
  mark `(removed)` instead of deleting their notes.md entry.
- Regenerate `shelf.html` in full each run (cheap).
- DNA section: recompute on every run that touches ≥ 1 image entry,
  unless the user explicitly says "keep DNA as is".

---

## 5. Layout & tone of `shelf.html`

### 5.1 Visual language

The shelf.html itself is a designed artifact — it should embody the
"editorial / brand-book" aesthetic, not look like a developer tool.
Specifically:

- Dark canvas (`#0c0c0e`) OR light editorial cream (`#f3efe6`) —
  pick whichever the **majority of source images** match. Detect by
  averaging extracted-palette luminance.
- Display font: **Space Grotesk** for headings (16-32 px).
- Body font: **Inter** sans-serif for all chrome / explanations.
- Mono font: **JetBrains Mono** only for hex codes and the build
  label. No `mono-caps uppercase tracking-wider` chrome labels —
  sentence case throughout.
- Generous padding; each analysis card has ≥ 48 px breathing room.

### 5.2 Color-system presentation (per-image palette)

Inspired by the user's references (a brand-book "Color System" page
treatment). Per-image palette is rendered as a **brand-book card
strip**, not a row of small dots.

Each palette card:

- Size: 160 × 200 px (or proportional)
- Fill: the extracted color
- Header (mono): "FFFFFF" style hex pill, top-left, on a
  contrast-aware background chip
- Body text on card (Inter):
  - Line 1 (Space Grotesk, 18 px): a **poetic name** Claude assigns
    based on the color's hue + the source image's mood
    (e.g. "Ink in Water", "Spring Fresh", "Linen White")
  - Line 2-3 (Inter 11px): a 1-line "what this feels like" description
- Footer: `RGB nnn,nnn,nnn` + `HEX #xxxxxx` in mono, 10 px
- Cards laid out in a horizontal strip OR a 2-row grid (whichever
  fits 5-7 colors better).

Optional below the strip: **2-tone capsule samples** showing 3
recommended color pairings (e.g. brand+background, brand+accent,
two-status combo) — matches the user's reference for "Sample"
capsules.

### 5.3 Analysis card layout (per image)

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│   [Hero image — full width of card, max 720px wide]          │
│                                                              │
│   <Image filename in Space Grotesk medium 22px>              │
│   <Tag chips: tech / comp / color / type>                    │
│                                                              │
│   What I see                                                 │
│   2-3 sentence observation in Inter 14px                     │
│                                                              │
│   ─── Reproduction 1 · [Effect name] · tag ────────────────  │
│   ┌──────────────────────────────────────────────┐           │
│   │ <LIVE HTML/CSS render of the effect>         │           │
│   └──────────────────────────────────────────────┘           │
│   **Industry term**: diffuse aura                            │
│   **Plain explanation**: A soft cloud of light around an    │
│   element, like it's quietly glowing.                        │
│   [▾ View source]   ← collapsed by default                   │
│                                                              │
│   ─── Reproduction 2 · ...  ───                              │
│   ...                                                         │
│                                                              │
│   ─── Palette ────────────────────────────────────────────   │
│   [brand-book card strip — see §5.2]                         │
│                                                              │
│   Terms used in this card                                    │
│   • diffuse aura — ...                                       │
│   • frosted glass — ...                                      │
└──────────────────────────────────────────────────────────────┘
```

### 5.4 Cross-image DNA section (tail)

Only renders when ≥ 3 images present. Format:

```
## Visual DNA across this batch

The patterns that recur across 3+ images.

[tech] Recurring techniques
1. Frosted glass + diffuse aura — seen in 4/5
2. Polaroid stack offset — seen in 2/5

[color] Recurring palette tendency
- 6 of the 7 most-used colors fall in the cobalt / deep-blue family
- Cream / off-white appears as paper background in 3/5
[brand-book card strip rendering the union palette]

[comp] Recurring components
- Centered text on dark — 3/5
- Vertical sideways UPPERCASE caption — 2/5 (editorial print device)

[type] Recurring typography rules
- Sentence-case sans for body — 5/5
- Display + body separation, mono used sparingly — 4/5
```

### 5.5 Glossary section (footer)

Alphabetized list of every term used across every analysis card,
each with 1-line plain explanation + a tiny anchored "see card #N"
link back to the card(s) that introduced it.

---

## 6. Reproductions — the heart of the skill

### 6.1 How Claude picks "core" effects per image

- Examine each image. Identify the **2-3 most distinctive visual
  moves** — the things that make this image stand out and feel like
  the user might want to reuse.
- Tag each pick with one of four categories:
  - **tech** — a visual technique (effect / treatment)
  - **comp** — a component pattern (a card, a button, a layout)
  - **color** — a color motif (a hue combination, a gradient)
  - **type** — a typography rule (size scale, case discipline, vertical setting)
- If fewer than 2 distinctive moves exist (very plain image), do
  whatever is honest — even 1 is fine, do not pad.
- Skip the **palette** category from the per-image "reproductions"
  count (it gets its own dedicated card strip — §5.2). Reproductions
  count covers tech / comp / type.

### 6.2 Reproduction implementation

- **Form**: a live HTML/CSS demo — a real `<div>` that exhibits the
  effect, not a screenshot annotation.
- **Sandbox**: render inside a 280 × 180 (or proportional) container
  card with neutral background contrast so the effect reads cleanly.
- **JS**: avoid unless the effect genuinely requires it (e.g. a
  hover-to-reveal motion). If JS is unavoidable, keep it vanilla,
  no external libraries.
- **Source disclosure**: every reproduction has a `[▾ View source]`
  toggle. Collapsed by default; expanded shows the literal HTML+CSS
  used to produce the demo, in a `<pre><code>` block.
- **Honesty rule**: if Claude cannot reproduce an effect faithfully
  in HTML/CSS (e.g. complex WebGL, real video processing, advanced
  print filter), the reproduction card MUST instead say:
  > "This effect can't be faithfully reproduced in HTML/CSS — it
  > would require [X]. Here's a partial approximation that captures
  > the [Y] aspect:" followed by the partial demo.
  No pretend-reproductions.

### 6.3 Terminology — the teaching component

Every reproduction includes two language layers:

- **Industry term** (English + Chinese gloss if helpful):
  "diffuse aura（弥散光晕）". One name per concept; consistent
  across the whole shelf.html.
- **Plain explanation** (Chinese, 1-2 sentences): no other jargon
  unless that jargon is also explained inline. Imagine a friend
  who doesn't design for a living.

The same term explanation appears in three places:
1. Inline at the reproduction (full).
2. In the per-card "Terms used in this card" list (1 line).
3. In the global Glossary at the page foot (alphabetized, full).

This deliberate repetition is the teaching device — the user sees
each new term in three contexts and remembers it.

---

## 7. Apply mode (Step 2) details

### 7.1 Triggered by

User prompt mentions one of:
- apply / 应用 / 套用 / use this style on
- design system for / mockup for
- AND supplies a target path (existing directory). If path missing,
  ask once.

### 7.2 Files written to target

```
<target>/
├── design-system.html
├── mockup.html
└── visual-dna-source.md
```

**Never** overwrite an existing file with the same name. If
`mockup.html` exists, prompt the user: rename / overwrite / abort.

### 7.3 `design-system.html` form

A brand-book style standalone page. Three sections, each rendered
with the editorial layout from §5:

1. **Color System** — full brand-book card grid using the cross-image
   palette (the same card style as §5.2 but at larger scale, like
   the user's reference image #7 layout).
2. **Typography System** — display + body + mono fonts with size
   scale showcase (e.g. "Geist 64 / 48 / 32 / 24 / 20 / 18" style
   demonstration).
3. **Components System** — the recurring components from the cross-
   image DNA (per §5.4 [comp] list), each rendered as a working
   HTML/CSS sample with its industry term + plain explanation.

This page is **independently useful** — a design spec deliverable
the user could share with a developer or designer.

### 7.4 `mockup.html` form

A full single-page mockup of a website **landing page** template by
default (one hero, one features section, one CTA section, one
footer — minimum). Built using the design-system.html tokens.

If the user's apply prompt specifies a different template
("apply for a blog homepage" / "for a SaaS pricing page" / "for a
portfolio"), generate that template instead. Always one page,
always self-contained HTML.

### 7.5 `visual-dna-source.md` form

A short markdown trace of provenance:

```markdown
# Design source

This page's visual design was extracted from the
[<batch-slug>](<path-to-shelf>) visual shelf, distilled on
YYYY-MM-DD.

## Key DNA threads carried over

- [color] Cobalt + cream + off-white anchor — from images 01, 03, 05
- [tech] Diffuse aura + 6% grain on focal surfaces — from 01, 04, 05
- [type] Display Space Grotesk for headings, Inter sentence-case body — from 03, 04
- [comp] Brand-book color cards with poetic names — from 04, 06, 07

## How to refresh

Re-run `/visual-shelf <batch-path> 把风格应用到 <this-target>` after
adding or removing shelf images.
```

### 7.6 Iteration

User feedback on the apply output flows through normal conversation
(no in-skill version manager). User says "make the mockup more
editorial" / "tighten the spacing" / "the cobalt is still too cold" →
Claude updates the relevant file in place (or all three if needed).
Skill body must instruct Claude to **always read the existing
file first** before regenerating, so user-applied tweaks aren't
clobbered.

---

## 8. Tone & vocabulary discipline (across all output)

The skill produces user-facing HTML/markdown. Style rules:

- **Plain Chinese first**, technical terms second with their
  English in parentheses. Example: "弥散光晕（diffuse aura）".
- **No mono-caps chrome labels** in any output. The single
  sanctioned use of UPPERCASE mono is the build label on shelf.html
  foot ("REGENERATED 2026-05-24 14:30").
- **Sentence case** everywhere else.
- **Never gatekeep**: if a term has no perfect translation, explain
  by analogy ("像印刷在纸上的感觉"). Don't say "this is too
  technical to explain".

---

## 9. Edge cases

| Situation | Behavior |
|---|---|
| Folder doesn't exist | Error: "folder not found: `<path>`" |
| Folder empty | Error: "drop at least one image and re-run" |
| Folder has only 1 image | Run Shelf mode; suppress cross-image DNA section. |
| User edited a `notes.md` entry and added `<!-- edited by user -->` | Preserve verbatim on rerun. |
| Image format unsupported (.svg, .pdf, .heic) | SVG/PDF: warn + skip. HEIC: convert via macOS `sips` if available, else warn + skip. |
| Image deeply corrupt / can't be parsed | Warn, mark `(unreadable)` in notes.md, skip. |
| Target path for Apply doesn't exist | Ask user to create or supply different. Do not auto-create deep paths. |
| Target has existing `mockup.html` | Ask: rename / overwrite / abort. |
| Network fonts blocked | Fall back to system fonts; note in build label. |
| `shelf.html` size approaching 1 MB | Warn that the batch is too large; suggest splitting. |

---

## 10. Out of scope for v0.1

- Video reference support (mp4 / webm / mov). Designed for; not yet
  implemented. v0.2 candidate.
- Image-embedding–based clustering for cross-batch DNA. v0.1 relies
  on Claude's visual judgment.
- Persistent cross-batch DNA tracking ("what is my taste across all
  my shelves combined"). v0.2 candidate; needs a shelf-of-shelves
  data structure.
- Interactive knob editing of reproductions ("now show me the same
  aura with twice the blur"). Future direction.
- Automatic skill triggering on file-system events (drop image →
  shelf updates). Out of skill's scope; that would be a hook.

---

## 11. Test prompts for `/skill-creator` eval pass

Hand these to skill-creator's eval runner once SKILL.md is drafted:

1. `/visual-shelf ./refs/cobalt-batch/` (Shelf-only, well-formed)
2. `/visual-shelf ./refs/cobalt-batch/ 帮我提取一下 DNA`
   (DNA-mode trigger)
3. `/visual-shelf ./refs/cobalt-batch/ 把这风格应用到 ~/projects/blog-mockup`
   (Apply mode, full)
4. `/visual-shelf ./refs/cobalt-batch/ 应用到我的项目`
   (Apply mode, missing path — should ask)
5. `/visual-shelf ./refs/empty-folder/`
   (Edge: empty folder — should error politely)
6. `/visual-shelf ./refs/single-image/ 提取 DNA`
   (Edge: < 3 images for DNA — should run Shelf only with explanation)
7. `/visual-shelf ./refs/cobalt-batch/` after manually editing one
   entry with `<!-- edited by user -->` (Rerun: verify preservation)

---

## 12. Suggested skill names — pick one

- `visual-shelf` ← **default recommendation**; matches the "shelf"
  metaphor the user used naturally during grilling
- `visual-curator` — emphasizes the human curation step
- `ref-shelf` — shorter, more obvious purpose
- `design-shelf` — closest to existing `design-*` skill family
- `style-decoder` — emphasizes the teaching aspect
- `mood-extractor` — emphasizes the DNA aspect

The default below assumes `visual-shelf` is chosen.

---

## 13. Implementation handoff

Steps the user would take to bring this to life:

1. Run `/skill-creator` and reference this spec file:
   ```
   /skill-creator 用这份 spec 创建一个新 skill：
   /Users/.../docs/design/visual-shelf-skill-v0.1-spec.md
   ```
2. skill-creator will:
   - Scaffold `~/.claude/skills/visual-shelf/` with manifest, evolution,
     and SKILL.md.
   - Translate this spec into Claude-targeted instructions in SKILL.md.
   - Generate the test prompts from §11.
   - Run an initial eval pass and show results.
3. User iterates on output samples; tweak SKILL.md until happy.
4. Use the skill on `docs/design/references/2026-05-24-canvas-ux-inspiration/`
   as the first real batch — converts our existing manual analysis
   into the v0.1 shelf format.

---

## 14. Rationale archive — what was decided + why

| Decision | Choice | Rationale |
|---|---|---|
| Scope | 1 skill, 2 modes via progressive disclosure | Shared core logic (extract + reproduce + explain); Step 2 strictly depends on Step 1; same skill ⇒ progressive disclosure works naturally. |
| Dispatch | Natural-language prompt detection, no flags | Matches existing skill ecosystem (`/grill-me`, `/dev-workflow`); user shouldn't memorize syntax. |
| I/O | Same folder; `shelf.html` + `notes.md`; reference-based images; incremental idempotent | Cohesive batch folder; no path juggling; preserves user edits; cheap reruns. |
| Reproduction count | 2-3 per image, live HTML/CSS render | Enough coverage without padding; live render is the "I can reuse this" payoff. |
| Color presentation | Brand-book card strip (per user references) with poetic names + hex + RGB + 1-line feel | Editorial aesthetic baked in; teaches color naming alongside hex values. |
| Apply outputs | 3 files: `design-system.html`, `mockup.html`, `visual-dna-source.md` | Design-system is independently useful; mockup grounds it visually; source records provenance. |
| Default mockup template | Landing page | Most generic; user can override via prompt. |
| Target project posture | Write-only, never read existing code | Safe v0.1; avoids DNA pollution from existing styles. |
| Iteration | Conversation-driven; skill always reads file before regenerating | Matches Claude Code's normal feedback loop; no in-skill version mgmt complexity. |
| Tone | Plain Chinese first, English term in parens; sentence case; no mono-caps chrome | Aligned with v0.4.1 typography decisions for Atelier; matches user's "not a designer" reality. |
