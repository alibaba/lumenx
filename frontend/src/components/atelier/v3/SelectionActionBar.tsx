"use client";
import { useLayoutEffect, useRef, useState } from "react";
import {
  Play,
  Link2,
  GitBranch,
  Check,
  Scissors,
  Trash2,
  Pencil,
  Download,
  Maximize2,
  Bot,
  Camera,
  RefreshCw,
  ChevronDown,
  Sparkles,
  Wand2,
  Dice5,
  Bookmark,
  Lightbulb,
  Plus,
  Volume2,
  Film,
  LayoutGrid,
  Upload,
  Square,
  RotateCw,
  X,
} from "lucide-react";

// v0.7 contextual action toolbar — replaces the v0.5/v0.6 icon-only pill
// with a chip row (icon + sentence-case label) that mirrors the RHTV
// reference. Chips are borderless ghost buttons inside a frosted shell
// (no border per v0.6.2 chrome pivot — divider lives in the inset shadow
// ring). A trailing "More" chip opens an overflow menu where every kind's
// destructive Delete (and any low-priority actions) live.
//
// Anchoring + position math (getBoundingClientRect + ResizeObserver) is
// unchanged from v0.6 — see the useLayoutEffect block below.
export type ActionKey =
  // Legacy keys — preserved so existing handleActionBar handlers keep
  // dispatching without churn.
  | "play"
  | "edit"
  | "regenerate"
  | "useAsRef"
  | "branch"
  | "selectTake"
  | "addToSequence"
  | "download"
  | "fullscreen"
  | "addToAgent"
  | "frameCapture"
  | "replace"
  | "delete"
  // v0.7 new — image
  | "variations"
  | "editSubject"
  | "crop"
  | "upscale"
  | "styleTransfer"
  | "upload"
  // v0.7 new — draft video
  | "generate"
  | "editPrompt"
  | "rerollSeed"
  | "pickModel"
  | "aspect"
  | "duration"
  | "negativePrompt"
  | "convertToIdea"
  // v0.7 new — completed take
  | "compareTakes"
  // v0.7 new — idea / comment slip
  | "convertToDraft"
  | "pin"
  // v0.7 new — audio
  | "preview"
  | "replaceVoice"
  | "trim"
  | "useInSequence";

interface Props {
  kind: "image" | "video" | "audio" | "draft" | "idea" | "comment";
  // When kind === "image", determines which layout we render: media-bearing
  // images get the full v0.7 chip row; empty images collapse to the legacy
  // [Upload, useAsRef, More→Delete] row so the bar never promises actions
  // the card can't perform.
  hasMedia?: boolean;
  // nodeId is the canonical selected node id (real node id OR virtual
  // candidate key — both are mirrored as `data-atelier-node` attributes
  // on the rendered card). When provided, the bar reads that element's
  // live DOM rect so its position tracks pan/zoom/scroll without
  // relying on stale world-coord math. Optional so unit tests that
  // exercise the bar in isolation can keep using the x/y/width path.
  nodeId?: string;
  // Pan/zoom-derived fallback coords. Used as the initial paint and as
  // the position when nodeId is omitted or its DOM element can't be
  // found.
  x: number;
  y: number;
  width: number;
  onAct: (action: ActionKey) => void;
}

type IconComponent = typeof Play;

interface ActionDef {
  key: ActionKey;
  label: string;
  Icon: IconComponent;
  variant?: "default" | "danger" | "select";
}

// ── Action catalog ─────────────────────────────────────────────────────
// Labels are sentence-case Inter per v0.5.6/7/8 ladder. Variants:
//   default = neutral text-secondary
//   select  = emerald (used for the Generate CTA + Pick this take)
//   danger  = red on hover (Delete; always lives in overflow)
const ACTIONS: Record<ActionKey, ActionDef> = {
  // Legacy — labels updated to sentence case per v0.7 spec.
  play: { key: "play", label: "Play", Icon: Play },
  edit: { key: "edit", label: "Edit", Icon: Pencil },
  regenerate: { key: "regenerate", label: "Reroll", Icon: RotateCw },
  useAsRef: { key: "useAsRef", label: "Use as ref", Icon: Link2 },
  branch: { key: "branch", label: "Branch", Icon: GitBranch },
  selectTake: { key: "selectTake", label: "Pick this take", Icon: Check, variant: "select" },
  addToSequence: { key: "addToSequence", label: "Add to sequence", Icon: Scissors },
  download: { key: "download", label: "Download", Icon: Download },
  fullscreen: { key: "fullscreen", label: "Fullscreen", Icon: Maximize2 },
  addToAgent: { key: "addToAgent", label: "Send to agent", Icon: Bot },
  frameCapture: { key: "frameCapture", label: "Capture frame", Icon: Camera },
  replace: { key: "replace", label: "Replace media", Icon: RefreshCw },
  delete: { key: "delete", label: "Delete", Icon: Trash2, variant: "danger" },
  // Image — v0.7 new.
  variations: { key: "variations", label: "Variations", Icon: Dice5 },
  editSubject: { key: "editSubject", label: "Edit subject", Icon: Pencil },
  crop: { key: "crop", label: "Crop", Icon: Scissors },
  upscale: { key: "upscale", label: "Upscale", Icon: Maximize2 },
  styleTransfer: { key: "styleTransfer", label: "Style transfer", Icon: Wand2 },
  upload: { key: "upload", label: "Upload", Icon: Upload },
  // Draft — v0.7 new. Generate borrows the emerald CTA tone from v0.5.7.
  generate: { key: "generate", label: "Generate", Icon: Play, variant: "select" },
  editPrompt: { key: "editPrompt", label: "Edit prompt", Icon: Pencil },
  rerollSeed: { key: "rerollSeed", label: "Reroll seed", Icon: Dice5 },
  pickModel: { key: "pickModel", label: "Pick model", Icon: Sparkles },
  aspect: { key: "aspect", label: "Aspect", Icon: Square },
  duration: { key: "duration", label: "Duration", Icon: Film },
  negativePrompt: { key: "negativePrompt", label: "Negative prompt", Icon: X },
  convertToIdea: { key: "convertToIdea", label: "Convert to idea", Icon: Lightbulb },
  // Video take — v0.7 new.
  compareTakes: { key: "compareTakes", label: "Compare takes", Icon: LayoutGrid },
  // Idea / comment — v0.7 new.
  convertToDraft: { key: "convertToDraft", label: "Convert to draft", Icon: Wand2 },
  pin: { key: "pin", label: "Pin", Icon: Bookmark },
  // Audio — v0.7 new.
  preview: { key: "preview", label: "Preview", Icon: Play },
  replaceVoice: { key: "replaceVoice", label: "Replace voice", Icon: Volume2 },
  trim: { key: "trim", label: "Trim", Icon: Scissors },
  useInSequence: { key: "useInSequence", label: "Use in sequence", Icon: Plus },
};

// "__more__" is a sentinel; the overflow trigger isn't a real ActionKey
// because clicking it opens the popover instead of dispatching through
// onAct.
type LayoutMain = ReadonlyArray<ActionKey | "__more__">;
type LayoutOverflow = ReadonlyArray<ActionKey | "__divider__">;

interface KindLayout {
  main: LayoutMain;
  overflow: LayoutOverflow;
}

// Per-kind layout. Spec rules:
//   - Delete ALWAYS lives in the overflow menu (sole destructive action,
//     never promoted to the main row even for short layouts).
//   - Main rows stay around 5–6 chips + the trailing More chip so the
//     toolbar holds the ~420–460px width budget at 1× zoom.
const LAYOUTS: Record<Props["kind"] | "image-empty", KindLayout> = {
  // Completed take / candidate: take-judgment chips first, post-judgment
  // (fullscreen / send / capture / branch) in overflow.
  video: {
    main: [
      "selectTake",
      "regenerate",
      "addToSequence",
      "compareTakes",
      "useAsRef",
      "download",
      "__more__",
    ],
    overflow: [
      "fullscreen",
      "addToAgent",
      "frameCapture",
      "branch",
      "__divider__",
      "delete",
    ],
  },
  // Draft video: Generate is the emerald CTA, then prompt/seed/model
  // settings. Aspect/Duration/Pick-model are intended as chip-as-popover
  // triggers in Phase 2 — the chips render now, popovers wire later.
  draft: {
    main: [
      "generate",
      "editPrompt",
      "rerollSeed",
      "pickModel",
      "aspect",
      "duration",
      "__more__",
    ],
    overflow: ["negativePrompt", "convertToIdea", "__divider__", "delete"],
  },
  // Image with media: image-shaping chips first (variations / subject /
  // crop / upscale / style) then attach. Manage actions in overflow.
  image: {
    main: [
      "variations",
      "editSubject",
      "crop",
      "upscale",
      "styleTransfer",
      "useAsRef",
      "__more__",
    ],
    overflow: [
      "fullscreen",
      "download",
      "addToAgent",
      "replace",
      "__divider__",
      "delete",
    ],
  },
  // Empty image card collapse — the card has nothing to operate on, so
  // the bar offers Upload + (later) attach via "Use as reference", with
  // Delete tucked in the overflow.
  "image-empty": {
    main: ["upload", "useAsRef", "__more__"],
    overflow: ["delete"],
  },
  // Audio: playback / replace / trim / sequence. Send / download in
  // overflow alongside Delete.
  audio: {
    main: ["preview", "replaceVoice", "trim", "useInSequence", "__more__"],
    overflow: [
      "useAsRef",
      "download",
      "addToAgent",
      "__divider__",
      "delete",
    ],
  },
  // Idea / comment: convert / pin. Body still edits inline on the card.
  idea: {
    main: ["convertToDraft", "pin", "__more__"],
    overflow: ["delete"],
  },
  comment: {
    main: ["convertToDraft", "pin", "__more__"],
    overflow: ["delete"],
  },
};

function chipClass(variant: ActionDef["variant"]): string {
  // v0.7 chip primitive — borderless ghost button, sentence-case label.
  // Sticks to the v0.5.6/7/8 type ladder: 11px / font-medium / tight
  // tracking. Hover lifts only the chip plate (not the toolbar shell)
  // so the bar feels like a row of independent affordances.
  const base =
    "group/act inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full text-[11px] font-medium tracking-tight transition-colors duration-150 active:scale-[0.97]";
  if (variant === "select") {
    return `${base} text-emerald-200/95 hover:bg-emerald-400/12 hover:text-emerald-100`;
  }
  if (variant === "danger") {
    return `${base} text-text-secondary hover:bg-red-400/15 hover:text-red-200`;
  }
  return `${base} text-text-secondary hover:bg-white/[0.05] hover:text-foreground`;
}

function menuItemClass(variant: ActionDef["variant"]): string {
  const base =
    "flex items-center gap-2 w-full rounded px-2.5 py-[6px] text-[12px] transition-colors";
  if (variant === "danger") {
    return `${base} text-text-secondary hover:bg-red-400/15 hover:text-red-200`;
  }
  return `${base} text-text-secondary hover:bg-white/[0.05] hover:text-foreground`;
}

export function SelectionActionBar({ kind, hasMedia, nodeId, x, y, width, onAct }: Props) {
  // Resolve the active layout. Image kind branches on hasMedia so empty
  // image cards collapse to the lighter Upload row.
  const layoutKey: Props["kind"] | "image-empty" =
    kind === "image" && hasMedia === false ? "image-empty" : kind;
  const layout = LAYOUTS[layoutKey];

  // Anchor to the selected node's live DOM rect rather than reconstruct
  // its screen position from world coords. Bug v0.5.8: when a node sat
  // low in world coords AND the user had panned, the old math
  // (`Math.max(8, y - 36)`) underflowed and the bar pinned to the top of
  // the viewport, orphaned far above its node. Reading the rect after
  // layout keeps the bar 36px above the actual painted card across pan /
  // zoom / scroll / virtual-candidate cases.
  //
  // pinnedTop=true means the desired top would have cut off the bar at
  // the viewport edge; we clamp to 12px and emit a small downward arrow
  // so users see the bar still belongs to the node below.
  const [pos, setPos] = useState<{ left: number; top: number; pinnedTop: boolean }>(() => ({
    left: x + width / 2,
    top: Math.max(12, y - 36),
    pinnedTop: false,
  }));

  // Re-measure on selection change AND on any pan/zoom/scroll that
  // re-renders the parent (x/y/width recompute → effect re-fires).
  //
  // v0.6.2 anchor resolution order:
  //   1. `[data-atelier-workbench="${nodeId}"]` — the EXPANDED workbench
  //      root for a selected draft. This element is `position: absolute`
  //      + `transform: translate(x,y)` + a real width, so its rect is
  //      the true painted card. Without this preference, querySelector
  //      hits the outer AtelierShell wrapper (also tagged with
  //      `data-atelier-node` for selection wiring) which is a static,
  //      0×0 shell whose rect collapses to the world flow origin and
  //      causes the bar to land inside the workbench textarea.
  //   2. `[data-atelier-node="${nodeId}"]` — compact cards and virtual
  //      candidate tiles. For those, the data attribute already sits on
  //      a `position:absolute` card with real dimensions, so a single
  //      lookup is enough.
  //
  // ResizeObserver below also re-measures while the workbench plays its
  // 360ms `atelier-workbench-in` grow animation, keeping the bar's
  // horizontal center aligned through the expansion.
  useLayoutEffect(() => {
    if (typeof document === "undefined") return;
    if (!nodeId) return;
    const el =
      (document.querySelector(`[data-atelier-workbench="${nodeId}"]`) as HTMLElement | null) ??
      (document.querySelector(`[data-atelier-node="${nodeId}"]`) as HTMLElement | null);
    if (!el) return;
    const place = () => {
      const rect = el.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const desiredTop = rect.top - 36;
      if (desiredTop < 12) {
        setPos({ left: centerX, top: 12, pinnedTop: true });
      } else {
        setPos({ left: centerX, top: desiredTop, pinnedTop: false });
      }
    };
    place();
    // ResizeObserver covers the workbench grow-in animation (and any
    // future content-driven height/width changes) so the bar tracks the
    // top edge throughout the transition instead of pinning to the
    // pre-animation rect.
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => place());
    ro.observe(el);
    return () => ro.disconnect();
  }, [nodeId, x, y, width]);

  // Overflow popover state. Scrim closes on outside-click — same idiom
  // as the align menu at AtelierShellV3.tsx L4881.
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement | null>(null);

  return (
    <div
      role="toolbar"
      aria-label="Selection actions"
      // v0.6.2 chrome pivot: NO border. The 1px divider lives entirely in
      // the existing shadow stack (`inset 0 1px 0 rgba(255,255,255,0.06)`)
      // so the bar reads as a single quiet glass plate against the canvas.
      className="absolute z-40 inline-flex -translate-x-1/2 items-center gap-1 rounded-full bg-[#141416]/96 px-2 h-9 shadow-[0_14px_30px_-16px_rgba(0,0,0,0.7),0_2px_6px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-xl animate-atelier-popover-in motion-reduce:animate-none"
      style={{ left: pos.left, top: pos.top }}
    >
      {layout.main.map((item, idx) => {
        if (item === "__more__") {
          return (
            <div key={`more-${idx}`} ref={moreRef} className="relative">
              <button
                type="button"
                aria-label="More"
                aria-haspopup="menu"
                aria-expanded={moreOpen}
                data-tip="More actions"
                onClick={(event) => {
                  event.stopPropagation();
                  setMoreOpen((v) => !v);
                }}
                className={chipClass("default")}
              >
                <ChevronDown size={13} aria-hidden="true" />
                <span>More</span>
              </button>
              {moreOpen ? (
                <>
                  <div
                    aria-hidden="true"
                    className="fixed inset-0 z-[41]"
                    onClick={() => setMoreOpen(false)}
                  />
                  <ul
                    role="menu"
                    aria-label="More actions"
                    // No border — matches the v0.6.2 align-menu pattern.
                    className="absolute right-0 top-9 z-[42] min-w-[180px] origin-top-right rounded-md bg-[#141416]/96 p-1 shadow-[0_18px_36px_-20px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl animate-atelier-popover-in motion-reduce:animate-none"
                  >
                    {layout.overflow.map((entry, oIdx) => {
                      if (entry === "__divider__") {
                        return (
                          <li
                            key={`d-${oIdx}`}
                            role="none"
                            className="my-1 mx-2 h-px bg-white/6"
                          />
                        );
                      }
                      const def = ACTIONS[entry];
                      const Icon = def.Icon;
                      return (
                        <li key={def.key} role="none">
                          <button
                            type="button"
                            role="menuitem"
                            aria-label={def.label}
                            onClick={(event) => {
                              event.stopPropagation();
                              setMoreOpen(false);
                              onAct(def.key);
                            }}
                            className={menuItemClass(def.variant)}
                          >
                            <Icon size={13} aria-hidden="true" />
                            <span className="text-left">{def.label}</span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </>
              ) : null}
            </div>
          );
        }
        const def = ACTIONS[item];
        const Icon = def.Icon;
        return (
          <button
            key={def.key}
            type="button"
            aria-label={def.label}
            data-tip={def.label}
            onClick={(event) => {
              event.stopPropagation();
              onAct(def.key);
            }}
            className={chipClass(def.variant)}
          >
            <Icon size={13} aria-hidden="true" />
            <span>{def.label}</span>
          </button>
        );
      })}
      {/* When the bar would be cut off at the viewport top, we clamp it
          to 12px and surface a tiny downward chevron so the user can
          read the bar as still belonging to the (off-screen-above) node. */}
      {pos.pinnedTop ? (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-1/2 top-full -translate-x-1/2 translate-y-[2px] text-white/60"
        >
          <ChevronDown size={12} />
        </span>
      ) : null}
    </div>
  );
}
