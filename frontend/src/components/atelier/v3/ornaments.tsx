"use client";
//
// Postcard / receipt ornament primitives. The user pointed at an editorial
// hotel-keycard mockup as the desired component vocabulary: numbered
// rubber-stamp badges, dashed perforations, mono-caps edge tags, italic
// display-font emphasis. Color palette stays dark (per user's explicit
// "色彩先不用参考"); we only borrow the typographic + structural moves.
//
// Each primitive is intentionally tiny so callers stay declarative:
//   <Perforation tone="muted" /> in place of an <hr/>.
//   <StampBadge label="DRAFT" number={3} /> as a corner tag.
//   <EdgeMark>POSTPOST · ATELIER · NO 204</EdgeMark> for a curved-arc feel.
//
// None of these introduce new colors; tones map onto our existing
// border-/text- color tokens (white/8, text-muted, primary, amber-300,
// emerald-300, blue-300, red-300).
import * as React from "react";

type OrnamentTone =
  | "muted"
  | "primary"
  | "amber"
  | "emerald"
  | "blue"
  | "red";

const TONE_TEXT: Record<OrnamentTone, string> = {
  muted: "text-text-muted/85",
  primary: "text-primary/95",
  amber: "text-amber-200/95",
  emerald: "text-emerald-200/95",
  blue: "text-blue-200/95",
  red: "text-red-200/95",
};

const TONE_BORDER: Record<OrnamentTone, string> = {
  muted: "border-white/8",
  primary: "border-primary/40",
  amber: "border-amber-300/35",
  emerald: "border-emerald-300/35",
  blue: "border-blue-300/35",
  red: "border-red-300/35",
};

const TONE_DASH_FROM: Record<OrnamentTone, string> = {
  muted: "from-white/12",
  primary: "from-primary/35",
  amber: "from-amber-300/35",
  emerald: "from-emerald-300/35",
  blue: "from-blue-300/35",
  red: "from-red-300/35",
};

// ── Perforation ─────────────────────────────────────────────────────────
//
// Dashed hairline that reads as a "tear here" line. Uses a CSS dotted/dashed
// border on a 1px tall element so the dot rhythm matches the receipt look
// of the reference. `via` adds a faded center stop so the line feels like
// it's hand-stamped, not CAD-precise.
export function Perforation({
  tone = "muted",
  className = "",
}: {
  tone?: OrnamentTone;
  className?: string;
}) {
  return (
    <div
      role="presentation"
      aria-hidden="true"
      className={`pointer-events-none h-px w-full bg-[length:6px_1px] bg-repeat-x ${className}`}
      style={{
        backgroundImage: `linear-gradient(to right, currentColor 50%, transparent 50%)`,
      }}
    >
      <div className={`h-px w-full ${TONE_TEXT[tone]} opacity-80`} />
    </div>
  );
}

// A more obvious "TEAR HERE" perforation: thicker dash + tiny scissor-style
// notches on each end. Used at the outer edge of a node where the user can
// imagine pulling the bottom strip off.
export function TearLine({
  tone = "muted",
  label,
  className = "",
}: {
  tone?: OrnamentTone;
  label?: string;
  className?: string;
}) {
  return (
    <div
      role="presentation"
      aria-hidden="true"
      className={`relative flex items-center gap-2 ${className}`}
    >
      <div
        className={`flex-1 border-t border-dashed ${TONE_BORDER[tone]}`}
      />
      {label ? (
        <span
          className={`shrink-0 font-mono text-[8.5px] font-medium uppercase tracking-[0.26em] ${TONE_TEXT[tone]}`}
        >
          {label}
        </span>
      ) : null}
      <div
        className={`flex-1 border-t border-dashed ${TONE_BORDER[tone]}`}
      />
    </div>
  );
}

// ── StampBadge ──────────────────────────────────────────────────────────
//
// A small rubber-stamp-feeling badge with mono caps + an optional number.
// Three shapes:
//   - "round" → fully rounded pill (works at 1-2 chars, also 3 caps + num)
//   - "rect"  → boxy rectangle
//   - "tri"   → triangular tab; uses a CSS clip-path so the dashed border
//                appears on the triangle outline. Reserved for "ROOM NO."
//                style accents that anchor the eye in the corner.
//
// All variants use a 1px dashed inset to read as "stamped" rather than
// "dropped in by Sketch".
export function StampBadge({
  label,
  number,
  shape = "rect",
  tone = "muted",
  className = "",
}: {
  label: string;
  number?: number | string;
  shape?: "round" | "rect" | "tri";
  tone?: OrnamentTone;
  className?: string;
}) {
  const text = TONE_TEXT[tone];
  const border = TONE_BORDER[tone];
  // Triangle uses a clip-path on a square; we paint a dashed ::before with
  // matching clip-path for the stamped outline. Tailwind can't express the
  // clip-path so we drop into inline style.
  if (shape === "tri") {
    return (
      <span
        className={`relative inline-grid place-items-center ${className}`}
        style={{
          width: 56,
          height: 56,
        }}
      >
        <span
          aria-hidden="true"
          className={`absolute inset-0 border border-dashed ${border}`}
          style={{
            clipPath: "polygon(50% 6%, 96% 92%, 4% 92%)",
          }}
        />
        <span
          className={`relative pt-3 text-center font-mono text-[8px] font-medium uppercase leading-[1.15] tracking-[0.18em] ${text}`}
        >
          <span className="block">{label}</span>
          {number != null ? (
            <span className="mt-[1px] block font-display text-[12px] font-semibold tracking-tight">
              {number}
            </span>
          ) : null}
        </span>
      </span>
    );
  }
  if (shape === "round") {
    return (
      <span
        className={`inline-grid h-[42px] w-[42px] place-items-center rounded-full border border-dashed ${border} text-center font-mono text-[7.5px] font-medium uppercase leading-[1.05] tracking-[0.18em] ${text} ${className}`}
      >
        <span>
          <span className="block">{label}</span>
          {number != null ? (
            <span className="mt-[1px] block font-display text-[11px] font-semibold tracking-tight">
              {number}
            </span>
          ) : null}
        </span>
      </span>
    );
  }
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-[3px] border border-dashed px-1.5 py-[3px] font-mono text-[8.5px] font-medium uppercase tracking-[0.22em] ${border} ${text} ${className}`}
    >
      <span>{label}</span>
      {number != null ? (
        <span className="font-display text-[10px] font-semibold tracking-tight">
          {number}
        </span>
      ) : null}
    </span>
  );
}

// ── EdgeMark ────────────────────────────────────────────────────────────
//
// A vertical mono-caps strip pinned to a node's edge — the equivalent of
// the tiny "TEL +43 (0) ..." printed sideways on the orange postcard in the
// reference. Defaults to right-edge / vertical-up reading. Use sparingly,
// only on nodes wide enough that the 14px strip doesn't crowd content.
export function EdgeMark({
  children,
  side = "right",
  tone = "muted",
  className = "",
}: {
  children: React.ReactNode;
  side?: "left" | "right";
  tone?: OrnamentTone;
  className?: string;
}) {
  const writingMode =
    side === "right"
      ? { writingMode: "vertical-rl" as const }
      : { writingMode: "vertical-rl" as const, transform: "rotate(180deg)" };
  const positionClass =
    side === "right" ? "right-0 top-2 bottom-2" : "left-0 top-2 bottom-2";
  return (
    <span
      aria-hidden="true"
      className={`pointer-events-none absolute flex items-center justify-center font-mono text-[8.5px] font-medium uppercase tracking-[0.32em] ${TONE_TEXT[tone]} ${positionClass} ${className}`}
      style={{
        ...writingMode,
        width: 14,
      }}
    >
      {children}
    </span>
  );
}

// ── DashRail ────────────────────────────────────────────────────────────
//
// Replacement for solid "status accent" rails on the left edge of a node:
// a top-anchored tiny dashed rail + faded gradient that reads as a hand-
// stamped color stripe rather than a CAD line. Lives at the same coords
// as the previous before:absolute before:inset-y-2 before:w-[2px] rail.
export function DashRail({
  tone = "primary",
  className = "",
}: {
  tone?: OrnamentTone;
  className?: string;
}) {
  return (
    <span
      role="presentation"
      aria-hidden="true"
      className={`pointer-events-none absolute left-0 top-2 bottom-2 w-[2px] bg-gradient-to-b ${TONE_DASH_FROM[tone]} via-transparent to-transparent ${className}`}
    />
  );
}

// ── EditorialTitle ──────────────────────────────────────────────────────
//
// Wrapping primitive for the "editorial display + italic accent" headline
// pattern. Splits the input on a `·` separator: anything after the bullet
// gets italic display-font emphasis, mimicking the "Post / Post" layout in
// the reference. If the title has no separator, renders plain.
//
// Example: "Cinematic · Rooftop chase" →
//   "Cinematic" [normal display]
//   "Rooftop chase" [italic display, slight lift]
export function EditorialTitle({
  children,
  className = "",
}: {
  children: string;
  className?: string;
}) {
  const text = children;
  const idx = text.indexOf("·");
  if (idx <= 0) {
    return (
      <span
        className={`font-display text-[13px] font-medium tracking-[-0.005em] ${className}`}
      >
        {text}
      </span>
    );
  }
  const head = text.slice(0, idx).trim();
  const tail = text.slice(idx + 1).trim();
  return (
    <span className={`flex flex-col leading-[1.05] ${className}`}>
      <span className="font-display text-[12px] font-medium tracking-tight text-foreground/95">
        {head}
      </span>
      <span className="font-display text-[14px] font-medium italic tracking-tight text-foreground">
        {tail}
      </span>
    </span>
  );
}
