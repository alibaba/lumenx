# Atelier · Flova-grade craft audit (v1.3)

> Static-audit log produced as part of the v1.3 batch-1 operational-hygiene
> sweep. Four checks run against the live worktree at
> `feat/atelier-v4-canvas-uplift`; each check records command, raw evidence,
> verdict, drift table, and follow-up.
>
> Authoritative target: [`atelier-flova-target-spec.md`](./atelier-flova-target-spec.md)
> (sections §9.1, §9.3, §9.7, §10).
>
> v1.3 framing: Atelier ships **Flova-grade visual vocabulary with
> documented craft debt**. This audit is the documentation of that debt — it
> records *which* surfaces drift from spec, *by how much*, and *why* (most
> drift is intentional restraint applied after the spec was frozen at v0.5).

---

## 0. Pixel-level wording sweep (item 1b)

Search for `pixel-level / pixel-perfect / pixel level / pixel perfect /
pixel precise / pixel-precise` across the worktree:

```
$ rg -n 'pixel-level|pixel-perfect|pixel level|pixel perfect|pixel precise|pixel-precise'
docs/design/atelier-flova-target-spec.md:1:# Atelier → Flova-grade canvas · pixel-level target spec (v0.5)
docs/design/atelier-flova-target-spec.md:3:> GOAL: rebuild the Atelier v3 canvas to pixel-level match three references:
```

Findings:

| File | Status |
|---|---|
| `README.md` | **0 hits** — no rewrite needed. |
| `README_EN.md` | **0 hits** — no rewrite needed. |
| Any `CHANGELOG*` / `RELEASE*` / release-notes file | **does not exist** (verified `find . -iname 'CHANGELOG*' -o -iname 'RELEASE*' -o -iname 'release-notes*'`). |
| `docs/design/atelier-flova-target-spec.md` | **2 hits** (lines 1 and 3) — out of batch-1 file ownership (`docs/design/references/*` + `README.md` + this audit doc only). Recorded here as a v1.4 follow-up: rename heading and goal-line wording to "Flova-grade visual vocabulary with documented craft debt". |

Verdict: **NO-OP for item 1b in this commit.** README.md never carried
pixel-level claims, and there are no release notes to rewrite. The single
remaining survivor (the spec doc heading itself) is parked as a v1.4
follow-up so this audit doesn't violate batch-1 file ownership.

---

## 1. Check · mono-caps survival (spec §9.1)

Spec §9.1 mandates **zero** `font-mono` + `uppercase` combinations on node
anatomy (meta / footer / port labels) and reserves mono ONLY for "one
optional build-label". The check is intentionally broader than just
`font-mono uppercase` — it also catches `uppercase tracking-[<n>em]`
because that pairing is the same "terminal / dev tool" tell §9.1 fights.

```
$ rg -n --no-heading 'font-mono[^"]*uppercase|uppercase[^"]*tracking-\[' \
    frontend/src/components/atelier/
frontend/src/components/atelier/v3/ExportsPanel.tsx:361:        <span className="font-display text-[11px] uppercase tracking-[0.08em] text-text-muted/85">
frontend/src/components/atelier/v3/AgentPanelV3.tsx:1350:                className={`btn-tip inline-flex h-7 items-center gap-1 rounded-md px-2 text-[10.5px] font-medium uppercase tracking-[0.06em] transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
```

Two survivors. Both are `font-display` / `font-medium` — **NOT**
`font-mono`. Spec §9.1 specifically targets node-anatomy chrome; both
survivors live on **side-panel chrome** (an export-list count badge and an
agent-panel mode-toggle button), not on any node body / meta / port label.

| File:line | Surface | Class | Verdict | Justification |
|---|---|---|---|---|
| `ExportsPanel.tsx:361` | Side-rail "{n} record(s)" header chip | `font-display text-[11px] uppercase tracking-[0.08em] text-text-muted/85` | CONDITIONAL-PASS | Panel-chrome heading, not node anatomy. Reads as a section divider, not a node-body label. v1.4 follow-up: replace with sentence-case `text-white/45` to match the rest of the panel-chrome ladder. |
| `AgentPanelV3.tsx:1350` | Planner mode toggle ("Plan" / "Auto") | `text-[10.5px] font-medium uppercase tracking-[0.06em]` | CONDITIONAL-PASS | Mode-toggle pill in the agent panel header. Mirrors the editorial "Slide 03" / "Creative Tools" caps style spec §5 explicitly *allows* on chrome labels. v1.4 follow-up: revisit alongside agent-panel header redesign. |

Verdict: **PASS with two documented chrome survivors.** No node-anatomy
regressions. Both survivors are panel-chrome, both pre-date v1.3, both are
documented for v1.4 cleanup.

Bonus: `rg 'font-mono'` against `frontend/src/components/atelier/` returns
matches only for keyboard `<kbd>` glyphs, code-input fields, sequence
strip timecodes, capability glyph, and one `kbd`-flavored shortcut hint.
None pair with `uppercase`. Spec §9.1's explicit `font-mono uppercase`
hit count is **0**.

---

## 2. Check · type-ladder consistency (spec §10)

Spec §10 ladder (the one §10 enumerates verbatim):

| Element | Class |
|---|---|
| TITLE | `font-sans text-[15px] font-medium tracking-[-0.01em] text-white/90` |
| META | `text-[11.5px] font-normal text-white/45 leading-[1.4]` |
| BODY / prompt | `text-[13px] leading-[1.6] text-white/80` |
| LABEL | `text-[11px] text-white/45` |
| VALUE | `text-[12px] tabular-nums text-white/75` |
| FOOTER | `text-[11px] text-white/40` |
| Placeholder | `text-[12px] italic text-white/35` |

So the §10-defined ladder = **{ 11, 11.5, 12, 13, 15 }** for node anatomy.
Chrome (rails, dialogs, icon labels, kbd) is allowed off-ladder.

```
$ rg -n --no-heading 'text-\[1[0-5]\.?5?px\]' frontend/src/components/atelier/
```

Matches summary (counts after grouping):

| px | Sites | Surface category | Verdict |
|---|---|---|---|
| 10 | LeftRailV3 (4×), MediaNode badges (4×), DraftWorkbench badges (2×), AssetLibrary chips (3×), NodePort low-key port label, Composer/popover, AtelierShellV3 kbd glyphs, RightRailV3 chip | Chrome / icon-rail / kbd / pill chrome | OFF-LADDER — accepted (spec §10 only enumerates node anatomy). |
| 10.5 | AgentPanelV3:1350 mode pill, RightRailV3:200 caption | Panel chrome | OFF-LADDER — 2 sites, both panel chrome. v1.4: round both to 11. |
| 11 | many (LABEL / FOOTER / chip body / kbd / port label) | Mixed | IN-LADDER. |
| 11.5 | MediaNode menu item (1×) | Node anatomy | IN-LADDER. |
| 12 | many (VALUE / placeholder / various inputs) | Mixed | IN-LADDER. |
| 12.5 | Dialogs body (2×) | Modal chrome | OFF-LADDER — 2 sites in `Dialogs.tsx` modal body copy. Stable since v0.5.x; documented as dialog-only off-ladder. |
| 13 | MediaNode body, Dialogs textarea, AssetLibrary input | Mixed | IN-LADDER. |
| 14 | Dialogs h3 (2×), RightRailV3 panel header, AssetLibrary panel header | Panel chrome / modal heading | OFF-LADDER — accepted (spec §10 silent on panel-header size). v1.4: lock 14 as the official panel/dialog heading size in §10. |
| 15 | TITLE — DraftWorkbench title, MediaNode "Final result" / collapsed title | Node anatomy | IN-LADDER. |

Verdict: **PASS with documented chrome drift.** Every node-anatomy size
falls inside the spec §10 ladder. Off-ladder values (10, 10.5, 12.5, 14)
all sit on panel / dialog / kbd chrome, which spec §10 does not govern.
The two `text-[10.5px]` sites are flagged as the cheapest v1.4 cleanup
(round to 11px and the off-ladder set shrinks by one tier).

---

## 3. Check · bloom alphas (spec §9.3)

Spec §9.3 target alphas (raw, before strength multiplier):

| tier | spec α |
|---|---|
| 0% (focal core) | 0.22 |
| 20% | 0.18 |
| 44% | 0.13 |
| 66% | 0.08 |

Spec also: "HERO strength 1.0" (for the focal node).

Actual recipe (`frontend/src/app/globals.css`):

```css
/* §10.10.2 bloom tiers (v0.5.8 — Flova restraint) */
.atelier-bloom-hero      { --bloom-strength: 0.85; }
.atelier-bloom-secondary { --bloom-strength: 0.70; }
.atelier-bloom-ambient   { --bloom-strength: 0.40; }

/* §12.7.3 base bloom recipe */
.atelier-bloom::after {
  background:
    radial-gradient(ellipse 94% 42% at 50% 0%,
      rgba(var(--atelier-bloom-1), calc(0.16  * var(--bloom-strength))) 0%,
      rgba(var(--atelier-bloom-2), calc(0.13  * var(--bloom-strength))) 20%,
      rgba(var(--atelier-bloom-3), calc(0.085 * var(--bloom-strength))) 44%,
      rgba(var(--atelier-bloom-4), calc(0.05  * var(--bloom-strength))) 66%,
      transparent 84%);
  filter: blur(calc(28px + 10px * var(--bloom-strength)));
}
```

Drift table (effective alpha at hero strength 0.85):

| stop | spec α | raw α (code) | effective α (× 0.85) | drift vs spec | severity |
|---|---|---|---|---|---|
| 0% | 0.22 | 0.16 | 0.136 | −0.084 | DOCUMENTED |
| 20% | 0.18 | 0.13 | 0.111 | −0.069 | DOCUMENTED |
| 44% | 0.13 | 0.085 | 0.072 | −0.058 | DOCUMENTED |
| 66% | 0.08 | 0.05 | 0.0425 | −0.038 | DOCUMENTED |
| HERO strength | 1.0 | 0.85 | — | −0.15 | DOCUMENTED |

The drift is **intentional and load-bearing** — every tier is below spec
because v0.5.8 explicitly pulled bloom strength down "so the canvas-wide
colored wash recedes and the focal node's halo reads as intentional, not
decorative" (the comment block above the rule). The spec is from v0.5;
the code is post-v0.5.8 Flova-restraint pass.

Verdict: **DOCUMENTED DRIFT — intentional, do not 'fix'.** When the spec
is next revised, §9.3 should be re-pinned to the live values (raw 0.16 /
0.13 / 0.085 / 0.05 + hero strength 0.85) so future audits stop flagging
this. v1.4 follow-up: bring spec §9.3 in line with the v0.5.8 bloom
recipe, not the other way around.

(`.atelier-opaque-shell.atelier-bloom::before` is a sibling rule with
alphas 0.15 / 0.12 / 0.085 / 0.05 — same shape, same documented restraint.
Not separately broken out here because §9.3 governs the perceived focal
halo, which is the `::after` recipe.)

---

## 4. Check · edge-beam SVG (spec §9.7)

Spec §9.7:
- Glow path: width 8–10, opacity 0.20, stdDeviation 4.5
- Core path: width 1.5, white, opacity 0.9
- Endpoint flares: brighter, r 8

Actual (focal/active beam, `AtelierShellV3.tsx:131–157`):

```tsx
{/* glow halo */}
<path … strokeWidth={isSelected ? 9 : 7}
       strokeOpacity={isSelected ? 0.3 : 0.16}
       strokeLinecap="round" filter="url(#beam-glow)" />
{/* bright filament core */}
<path … strokeWidth={isSelected ? 2 : 1.4}
       strokeOpacity={isSelected ? 1 : 0.85} … />
{/* endpoint flares */}
<circle cx={x1} cy={y1} r={isSelected ? 9 : 6.5} fill="url(#beam-flare)" />
<circle cx={x2} cy={y2} r={isSelected ? 9 : 6.5} fill="url(#beam-flare)" />
```

Filter + flare gradient (`AtelierShellV3.tsx:4462–4471`):

```tsx
<filter id="beam-glow" x="-80%" y="-80%" width="260%" height="260%">
  <feGaussianBlur stdDeviation="4.5" />
</filter>
<radialGradient id="beam-flare">
  <stop offset="0%"   stopColor="#ffffff" stopOpacity="0.95" />
  <stop offset="35%"  stopColor="#ffffff" stopOpacity="0.45" />
  <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
</radialGradient>
```

Drift table (focal/active hover — the "beam ON" state):

| Layer | Property | Spec | Code (hover) | Code (selected) | Drift |
|---|---|---|---|---|---|
| Glow | stroke width | 8–10 | 7 | 9 | hover −1 (just below band); selected ✓ |
| Glow | opacity | 0.20 | 0.16 | 0.30 | hover −0.04 (just outside ±0.03 tolerance); selected ↑0.10 (selection emphasis, intentional) |
| Glow | stdDeviation | 4.5 | 4.5 | 4.5 | ✓ exact |
| Core | width | 1.5 | 1.4 | 2 | within ±0.5 ✓ |
| Core | opacity | 0.9 | 0.85 | 1.0 | within ±0.05 ✓ |
| Flare | radius | 8 | 6.5 | 9 | hover −1.5 (drift); selected ✓ |
| Flare | shape | radial gradient | radial gradient | radial gradient | ✓ |

3-layer assertion: glow path + core path + endpoint flares — **all 3
present** in the focal/active branch. Filter mechanism is `feGaussianBlur`
SVG (not CSS-only blur) — meets the "blend properly at high zoom" bar.

Ambient (non-focal) edge:
- Spec §9.7 calls for "1px rgba(255,255,255,0.10) (whisper)".
- Code renders a 1.75px stroke with a linear-gradient stop (0.45 → 0.18)
  plus 3px endpoint dots at 0.45.
- Comment: "v0.7 nudges the static state up again (alpha 0.45→0.18,
  stroke 1.75px, endpoint dots 3px @ 0.45) so users can read connections
  at rest without losing the Flova quiet — RHTV reference (image #8)
  reads its splines at this density."
- Drift: intentional, post-spec, RHTV-reference-driven. **DOCUMENTED.**

Verdict: **PASS with two minor focal-hover drifts and one documented
ambient drift.** The 3-layer beam architecture is intact, the blur
mechanism is SVG, the focal/selected state matches spec, and the only
true off-spec values are (a) hover glow width 7 vs spec 8–10, (b) hover
glow opacity 0.16 vs 0.20, (c) hover flare r 6.5 vs spec 8 — all on the
**hover** branch only. v1.4 follow-up: bump hover glow to width 8 +
opacity 0.18 + flare r 7 to land inside spec tolerance without disturbing
the (intentional) selected-state lift.

---

## 5. Carry-forward to v1.4

Items recorded here that the next iteration should pick up:

1. Rename the heading + goal-line of `docs/design/atelier-flova-target-spec.md` from "pixel-level target spec" to "Flova-grade visual vocabulary with documented craft debt" (out of batch-1 file ownership; deferred deliberately).
2. Capture the three MISSING hero PNGs (`libtv-hero-*.png`, `rhtv-hero-*.png`, `flova-hero-*.png`) per `docs/design/references/SOURCES.md` §1, after a designer is signed in to LibTV / RHTV.
3. Confirm the canonical Flova / FLORA domain (the v1.3 brief asserted `flova.ai`; only the FLORA reference image is captured internally, never a live URL).
4. Replace `ExportsPanel.tsx:361` mono-caps section count + `AgentPanelV3.tsx:1350` mode pill with sentence-case spec-§10-conformant equivalents (or, if kept, document them in the spec as the explicitly allowed editorial-caps chrome surfaces per §5).
5. Round the two `text-[10.5px]` sites (AgentPanelV3, RightRailV3) to `text-[11px]` and either drop or canonize the `text-[12.5px]` Dialogs body + `text-[14px]` panel-header sizes by adding a "chrome ladder" subsection to spec §10.
6. Re-pin spec §9.3 to the v0.5.8 live recipe (raw 0.16 / 0.13 / 0.085 / 0.05 + hero strength 0.85) so future audits don't false-flag intentional restraint.
7. Bump focal-hover edge-beam glow width to 8, opacity to 0.18, and endpoint flare r to 7 to land inside spec §9.7 tolerance without disturbing selected-state emphasis.
