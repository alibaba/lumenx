"use client";
import { useLayoutEffect, useState } from "react";
import {
  Play,
  Settings,
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
} from "lucide-react";

// Per Codex doc §4.6 / §7.6 — completed-take nodes need a richer action
// vocabulary than draft/idea nodes. New entries are framed as "judgment
// after generation" affordances: save out / view full / hand to agent.
export type ActionKey =
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
  | "delete";

interface Props {
  kind: "image" | "video" | "audio" | "draft" | "idea" | "comment";
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

const ACTIONS: Record<ActionKey, ActionDef> = {
  play: { key: "play", label: "Play", Icon: Play },
  edit: { key: "edit", label: "Edit", Icon: Pencil },
  regenerate: { key: "regenerate", label: "Re-generate", Icon: Settings },
  useAsRef: { key: "useAsRef", label: "Use as reference", Icon: Link2 },
  branch: { key: "branch", label: "Branch", Icon: GitBranch },
  selectTake: { key: "selectTake", label: "Select as take", Icon: Check, variant: "select" },
  addToSequence: { key: "addToSequence", label: "Add to Sequence", Icon: Scissors },
  download: { key: "download", label: "Download", Icon: Download },
  fullscreen: { key: "fullscreen", label: "Fullscreen preview", Icon: Maximize2 },
  addToAgent: { key: "addToAgent", label: "Send to Agent", Icon: Bot },
  frameCapture: { key: "frameCapture", label: "Capture frame", Icon: Camera },
  replace: { key: "replace", label: "Replace media", Icon: RefreshCw },
  delete: { key: "delete", label: "Delete", Icon: Trash2, variant: "danger" },
};

type LayoutItem = ActionKey | "divider";

const LAYOUTS: Record<Props["kind"], LayoutItem[]> = {
  // Layout for completed takes (RHTV §4.6 reference): play / iterate /
  // attach / branch / select / sequence go first because they're flow
  // actions; download / fullscreen / agent are post-judgment actions
  // grouped after a divider; delete sits at the far right.
  video: [
    "play",
    "regenerate",
    "useAsRef",
    "branch",
    "selectTake",
    "addToSequence",
    "divider",
    "fullscreen",
    "download",
    "frameCapture",
    "addToAgent",
    "divider",
    "delete",
  ],
  audio: ["play", "useAsRef", "addToSequence", "divider", "download", "addToAgent", "divider", "delete"],
  // Branch is meaningless on a static image (you branch FROM a take, not
  // from a reference). Image action bar: attach + replace + post-judgment
  // (full / download / agent) + delete.
  image: ["useAsRef", "replace", "divider", "fullscreen", "download", "addToAgent", "divider", "delete"],
  // Drafts use the floating Composer as their editor — no "regenerate"
  // (would silently 400 without payload). Branch is meaningless on an
  // intent (you branch FROM a take). Just Delete.
  draft: ["delete"],
  idea: ["delete"],
  // Comments are sticky-note annotations — Delete only (body is edited
  // inline on the canvas).
  comment: ["delete"],
};

function buttonClass(variant: ActionDef["variant"]): string {
  const base = "btn-tip group/act inline-flex h-7 w-7 items-center justify-center rounded-full transition-all duration-150 active:scale-[0.92]";
  if (variant === "danger") {
    return `${base} text-text-secondary hover:scale-[1.08] hover:bg-red-400/15 hover:text-red-200`;
  }
  if (variant === "select") {
    return `${base} text-emerald-200/95 hover:scale-[1.08] hover:bg-emerald-400/12`;
  }
  return `${base} text-text-secondary hover:scale-[1.08] hover:bg-white/[0.06] hover:text-foreground`;
}

export function SelectionActionBar({ kind, nodeId, x, y, width, onAct }: Props) {
  const items = LAYOUTS[kind];

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

  return (
    <div
      role="toolbar"
      aria-label="Selection actions"
      className="absolute z-40 inline-flex -translate-x-1/2 items-center gap-0.5 rounded-full border border-white/8 bg-[#141416]/96 px-1 py-1 shadow-[0_14px_30px_-16px_rgba(0,0,0,0.7),0_2px_6px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-xl animate-atelier-popover-in motion-reduce:animate-none"
      style={{ left: pos.left, top: pos.top }}
    >
      {items.map((item, idx) => {
        if (item === "divider") {
          return (
            <span
              key={`divider-${idx}`}
              aria-hidden="true"
              className="mx-1 h-4 w-px bg-white/8"
            />
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
            className={buttonClass(def.variant)}
          >
            <Icon size={14} aria-hidden="true" />
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
