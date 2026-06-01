"use client";
//
// WorkflowsPanel — body for the LeftRailV3 Workflows mode (Sprint C).
// Browses local workflow templates, filters by category, fires `onInsert`
// when the user picks one. Insert handler in the shell does the actual
// node creation + reference wiring.
//
// Per Codex doc §4.7 / §7.7. Open-source local-first; no marketplace,
// no remote catalog, no auth. Defaults ship in workflowTemplates.ts.
// User templates (saved from canvas selection) live in localStorage —
// see useUserWorkflows below.
import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Sparkles, Trash2 } from "lucide-react";
import {
  TEMPLATE_CATEGORY_LABELS,
  WORKFLOW_TEMPLATES,
  type TemplateCategory,
  type WorkflowTemplate,
} from "./workflowTemplates";
import { TemplateThumbnail } from "./TemplateThumbnail";

/** Mime type the card emits on drag — shell-side drop handler reads
 *  this and inserts the template at the cursor position. Kept in this
 *  file (not workflowTemplates.ts) because it's a UI contract between
 *  this panel and the canvas, not a property of the template schema. */
export const WORKFLOW_TEMPLATE_DRAG_MIME = "application/x-atelier-template";

interface Props {
  onInsert: (template: WorkflowTemplate) => void;
  /** Optional escape hatch for the shell to receive an explicit drop
   *  anchor (cursor world coords). Track N exposes the prop so the
   *  shell owner can wire it later; the panel itself never calls this
   *  — the drag payload is a mime + id, the shell decides where to
   *  drop. Present here purely for API symmetry / future use. */
  onInsertAt?: (template: WorkflowTemplate, anchor: { x: number; y: number }) => void;
}

const ALL_CATEGORIES: Array<TemplateCategory | "all" | "mine"> = [
  "all",
  "mine",
  "story",
  "character",
  "scene",
  "product",
  "motion",
  "utility",
];

const USER_WORKFLOWS_KEY = "atelier-v3-user-workflows";

export function readUserWorkflows(): WorkflowTemplate[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(USER_WORKFLOWS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    // Cheap shape filter — drop anything that doesn't have the minimum
    // surface a template needs. Defensive against schema drift across
    // versions (the field is in localStorage so it survives upgrades).
    return parsed.filter(
      (t): t is WorkflowTemplate =>
        !!t &&
        typeof t === "object" &&
        typeof (t as WorkflowTemplate).id === "string" &&
        Array.isArray((t as WorkflowTemplate).nodes),
    );
  } catch {
    return [];
  }
}

function writeUserWorkflows(list: WorkflowTemplate[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(USER_WORKFLOWS_KEY, JSON.stringify(list));
    // Ping any other tab/component that listens. v1 has only one
    // consumer (this panel) but the helper is cheap.
    window.dispatchEvent(new CustomEvent("atelier-user-workflows-changed"));
  } catch {
    /* ignore quota / private mode */
  }
}

/** Helper exported so the shell's "Save selection as workflow" handler
 *  can append a new template without re-implementing the storage layer. */
export function appendUserWorkflow(t: WorkflowTemplate): void {
  const list = readUserWorkflows();
  list.unshift(t);
  writeUserWorkflows(list);
}

export function WorkflowsPanel({ onInsert, onInsertAt: _onInsertAt }: Props) {
  // `_onInsertAt` is intentionally unused inside the panel — it exists
  // so the shell can adopt a typed prop hook later without breaking the
  // public API. Drop-target ownership lives on the canvas root.
  void _onInsertAt;

  const [filter, setFilter] = useState<TemplateCategory | "all" | "mine">("all");
  const [userTemplates, setUserTemplates] = useState<WorkflowTemplate[]>(() => readUserWorkflows());
  // Which card is currently being dragged — drives the visual feedback
  // (opacity/scale) without needing a per-card useState.
  const [draggingId, setDraggingId] = useState<string | null>(null);

  // Hidden off-screen mount node that we render TemplateThumbnail into
  // and hand to setDragImage. Browsers screenshot the live DOM node at
  // dragstart, then we tear it down on dragend so it doesn't pile up.
  const dragImageHostRef = useRef<HTMLDivElement | null>(null);
  const dragImageRootRef = useRef<Root | null>(null);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const host = document.createElement("div");
    // Pin off-screen so the screenshot picks up real pixels (browsers
    // refuse `display:none`) but the user never sees it.
    host.style.position = "fixed";
    host.style.top = "-9999px";
    host.style.left = "-9999px";
    host.style.pointerEvents = "none";
    host.setAttribute("data-atelier-template-drag-image", "true");
    document.body.appendChild(host);
    dragImageHostRef.current = host;
    dragImageRootRef.current = createRoot(host);
    return () => {
      // Defer the unmount/remove a tick — React 18 strict-mode dev
      // doubles invoke this effect and unmount-during-render of a
      // sibling root warns otherwise.
      const root = dragImageRootRef.current;
      const node = dragImageHostRef.current;
      dragImageRootRef.current = null;
      dragImageHostRef.current = null;
      queueMicrotask(() => {
        try {
          root?.unmount();
        } catch {
          /* ignore */
        }
        if (node?.parentNode) node.parentNode.removeChild(node);
      });
    };
  }, []);

  // Re-read on append events. localStorage doesn't fire for the writer
  // tab, so we use a custom event from the writer.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onChange = () => setUserTemplates(readUserWorkflows());
    window.addEventListener("atelier-user-workflows-changed", onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener("atelier-user-workflows-changed", onChange);
      window.removeEventListener("storage", onChange);
    };
  }, []);

  const filtered = useMemo(() => {
    if (filter === "all") return [...userTemplates, ...WORKFLOW_TEMPLATES];
    if (filter === "mine") return userTemplates;
    return WORKFLOW_TEMPLATES.filter((t) => t.category === filter);
  }, [filter, userTemplates]);

  const removeUser = (id: string) => {
    const next = userTemplates.filter((t) => t.id !== id);
    setUserTemplates(next);
    writeUserWorkflows(next);
  };

  const labelFor = (cat: TemplateCategory | "all" | "mine") => {
    if (cat === "all") return "All";
    if (cat === "mine") return "Mine";
    return TEMPLATE_CATEGORY_LABELS[cat];
  };

  return (
    <div className="flex h-full flex-col">
      {/* Category filter row — same vocabulary as AssetLibrary's kind
          chips so the two panels feel like part of the same system. */}
      <div className="shrink-0 border-b border-white/6 px-3 py-2">
        <div role="tablist" aria-label="Template category" className="flex flex-wrap items-center gap-1">
          {ALL_CATEGORIES.map((cat) => {
            const isActive = filter === cat;
            return (
              <button
                key={cat}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => setFilter(cat)}
                className={`rounded-full border px-2 py-[3px] text-[10px] font-medium tracking-[0.01em] transition-colors ${
                  isActive
                    ? "border-atelier-brand-400/45 bg-atelier-brand-400/15 text-atelier-brand-400"
                    : "border-dashed border-white/15 text-text-muted/85 hover:border-white/25 hover:text-foreground"
                }`}
              >
                {labelFor(cat)}
                {cat === "mine" && userTemplates.length > 0 ? (
                  <span className="ml-1 rounded-full bg-atelier-brand-400/20 px-1 font-display text-[10px] tabular-nums tracking-tight text-atelier-brand-400/95">
                    {userTemplates.length}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>

      {/* Templates list — each card is the full payload preview. Click
          calls onInsert; the shell handles "where to drop" by computing
          a viewport-center offset. User templates render with a small
          trash affordance that wipes them from localStorage. */}
      <ul className="flex-1 space-y-2 overflow-y-auto p-2.5">
        {filtered.length === 0 ? (
          <li className="grid place-items-center px-3 py-8 text-center text-text-muted/85">
            <div className="text-[11px] text-white/45">
              {filter === "mine"
                ? "Nothing saved yet · select nodes & use Save as workflow"
                : "No templates in this category"}
            </div>
          </li>
        ) : (
          filtered.map((t) => {
            const isUser = userTemplates.some((u) => u.id === t.id);
            return (
              <li key={t.id} className="relative">
                <button
                  type="button"
                  draggable
                  data-dragging={draggingId === t.id || undefined}
                  onDragStart={(e) => {
                    // Payload mime is consumed by the canvas root drop
                    // handler (see AtelierShellV3 handleDrop). We only
                    // pass the template id — the shell looks it up in
                    // WORKFLOW_TEMPLATES + user templates. Keeps the
                    // payload small and avoids stuffing structured data
                    // through dataTransfer (which only stringifies).
                    e.dataTransfer.setData(WORKFLOW_TEMPLATE_DRAG_MIME, t.id);
                    e.dataTransfer.effectAllowed = "copy";

                    // Custom drag image: render a larger thumbnail into
                    // the off-screen host and hand it to the browser.
                    // If anything in this chain fails (no DOM, no
                    // root, setDragImage unsupported) we silently fall
                    // back to the browser's default card screenshot.
                    const root = dragImageRootRef.current;
                    const host = dragImageHostRef.current;
                    if (root && host) {
                      try {
                        root.render(<TemplateThumbnail template={t} size="lg" />);
                        // Browsers screenshot synchronously from
                        // dragstart; the SVG is small enough that the
                        // current frame's already-painted state is
                        // close to good. If the first drag of a card
                        // shows a blank image, the second works — an
                        // acceptable trade for not blocking dragstart.
                        e.dataTransfer.setDragImage(host, 64, 40);
                      } catch {
                        /* fall back to default drag image */
                      }
                    }
                    setDraggingId(t.id);
                  }}
                  onDragEnd={() => setDraggingId(null)}
                  onClick={() => onInsert(t)}
                  className="group flex w-full flex-col gap-1.5 rounded-md border border-white/8 bg-black/25 p-3 text-left transition-all hover:-translate-y-[1px] hover:border-atelier-brand-400/40 hover:bg-atelier-brand-400/[0.05] data-[dragging=true]:scale-[0.98] data-[dragging=true]:opacity-50"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-display text-[13px] font-medium tracking-[-0.005em] text-foreground/95">
                      {t.name}
                    </div>
                    <span
                      aria-hidden="true"
                      className="inline-flex shrink-0 items-center gap-1 rounded-[3px] border border-dashed border-white/22 bg-black/40 px-1.5 py-[1px] text-[10px] font-medium tracking-[0.01em] text-text-muted/85"
                    >
                      {isUser ? "Mine" : TEMPLATE_CATEGORY_LABELS[t.category]}
                    </span>
                  </div>
                  <p className="text-[11.5px] leading-[1.5] text-text-secondary/95">
                    {t.description}
                  </p>

                  {/* Hover-reveal thumbnail — inline expanding row so it
                      doesn't fight the rail's overflow-y scroll. The
                      grid-rows trick animates between 0fr and 1fr,
                      driving a smooth height interpolation without
                      hard-coding a max-height. `group-focus-within`
                      mirrors hover for keyboard parity. */}
                  <div className="grid grid-rows-[0fr] transition-[grid-template-rows] duration-200 ease-out group-hover:grid-rows-[1fr] group-focus-within:grid-rows-[1fr]">
                    <div className="overflow-hidden">
                      <div className="flex items-center gap-2 pt-1.5 pb-0.5">
                        <TemplateThumbnail template={t} size="sm" />
                        <span className="text-[10px] tabular-nums tracking-tight text-white/45">
                          {t.nodes.length} {t.nodes.length === 1 ? "node" : "nodes"}
                          {t.edges.length > 0
                            ? ` · ${t.edges.length} ${t.edges.length === 1 ? "ref" : "refs"}`
                            : ""}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    {t.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full border border-white/8 bg-white/[0.03] px-1.5 py-[1px] text-[10px] tracking-[0.01em] text-text-muted/85"
                      >
                        {tag}
                      </span>
                    ))}
                    <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-atelier-brand-400/12 px-2 py-[3px] text-[10px] font-medium tracking-[0.01em] text-atelier-brand-400/95 transition-colors group-hover:bg-atelier-brand-400/22">
                      <Sparkles size={9} aria-hidden="true" />
                      Insert
                    </span>
                  </div>
                </button>
                {isUser ? (
                  <button
                    type="button"
                    aria-label={`Delete saved workflow ${t.name}`}
                    data-tip="Delete"
                    onClick={(e) => {
                      e.stopPropagation();
                      removeUser(t.id);
                    }}
                    className="btn-tip absolute right-2 top-2 grid h-6 w-6 place-items-center rounded-full text-text-muted opacity-0 transition-opacity hover:bg-red-400/15 hover:text-red-200 group-hover:opacity-100"
                  >
                    <Trash2 size={11} aria-hidden="true" />
                  </button>
                ) : null}
              </li>
            );
          })
        )}
      </ul>
    </div>
  );
}
