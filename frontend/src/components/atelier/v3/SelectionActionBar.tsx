"use client";
import { Play, Settings, Link2, GitBranch, Check, Scissors, Trash2 } from "lucide-react";

export type ActionKey =
  | "play"
  | "regenerate"
  | "useAsRef"
  | "branch"
  | "selectTake"
  | "addToSequence"
  | "delete";

interface Props {
  kind: "image" | "video" | "audio" | "draft" | "idea" | "comment";
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
  regenerate: { key: "regenerate", label: "Re-generate", Icon: Settings },
  useAsRef: { key: "useAsRef", label: "Use as reference", Icon: Link2 },
  branch: { key: "branch", label: "Branch", Icon: GitBranch },
  selectTake: { key: "selectTake", label: "Select as take", Icon: Check, variant: "select" },
  addToSequence: { key: "addToSequence", label: "Add to Sequence", Icon: Scissors },
  delete: { key: "delete", label: "Delete", Icon: Trash2, variant: "danger" },
};

type LayoutItem = ActionKey | "divider";

const LAYOUTS: Record<Props["kind"], LayoutItem[]> = {
  video: [
    "play",
    "regenerate",
    "useAsRef",
    "branch",
    "selectTake",
    "addToSequence",
    "divider",
    "delete",
  ],
  audio: ["play", "useAsRef", "addToSequence", "divider", "delete"],
  image: ["useAsRef", "branch", "divider", "delete"],
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
  const base = "btn-tip group/act inline-flex h-7 w-7 items-center justify-center rounded-full transition-colors";
  if (variant === "danger") {
    return `${base} text-text-secondary hover:bg-red-400/15 hover:text-red-200`;
  }
  if (variant === "select") {
    return `${base} text-emerald-200/95 hover:bg-emerald-400/12`;
  }
  return `${base} text-text-secondary hover:bg-white/[0.06] hover:text-foreground`;
}

export function SelectionActionBar({ kind, x, y, width, onAct }: Props) {
  const items = LAYOUTS[kind];

  return (
    <div
      className="absolute z-40 inline-flex -translate-x-1/2 items-center gap-0.5 rounded-full border border-white/8 bg-[#141416]/96 px-1 py-1 shadow-[0_14px_30px_-16px_rgba(0,0,0,0.7),0_2px_6px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-xl"
      style={{ left: x + width / 2, top: Math.max(8, y - 40) }}
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
    </div>
  );
}
