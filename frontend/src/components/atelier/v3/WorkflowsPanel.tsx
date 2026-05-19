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
import { useEffect, useMemo, useState } from "react";
import { Sparkles, Trash2 } from "lucide-react";
import {
  TEMPLATE_CATEGORY_LABELS,
  WORKFLOW_TEMPLATES,
  type TemplateCategory,
  type WorkflowTemplate,
} from "./workflowTemplates";

interface Props {
  onInsert: (template: WorkflowTemplate) => void;
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

function readUserWorkflows(): WorkflowTemplate[] {
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

export function WorkflowsPanel({ onInsert }: Props) {
  const [filter, setFilter] = useState<TemplateCategory | "all" | "mine">("all");
  const [userTemplates, setUserTemplates] = useState<WorkflowTemplate[]>(() => readUserWorkflows());

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
                className={`rounded-full border px-2 py-[3px] font-mono text-[8.5px] font-medium uppercase tracking-[0.24em] transition-colors ${
                  isActive
                    ? "border-primary/45 bg-primary/15 text-primary"
                    : "border-dashed border-white/15 text-text-muted/85 hover:border-white/25 hover:text-foreground"
                }`}
              >
                {labelFor(cat)}
                {cat === "mine" && userTemplates.length > 0 ? (
                  <span className="ml-1 rounded-full bg-primary/20 px-1 font-display text-[8.5px] text-primary/95">
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
            <div className="font-mono text-[9.5px] uppercase tracking-[0.28em]">
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
                  onClick={() => onInsert(t)}
                  className="group flex w-full flex-col gap-1.5 rounded-md border border-white/8 bg-black/25 p-3 text-left transition-all hover:-translate-y-[1px] hover:border-primary/40 hover:bg-primary/[0.05]"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-display text-[13px] font-medium tracking-[-0.005em] text-foreground/95">
                      {t.name}
                    </div>
                    <span
                      aria-hidden="true"
                      className="inline-flex shrink-0 items-center gap-1 rounded-[3px] border border-dashed border-white/22 bg-black/40 px-1.5 py-[1px] font-mono text-[8.5px] font-medium uppercase tracking-[0.22em] text-text-muted/85"
                    >
                      {isUser ? "Mine" : TEMPLATE_CATEGORY_LABELS[t.category]}
                    </span>
                  </div>
                  <p className="text-[11.5px] leading-[1.5] text-text-secondary/95">
                    {t.description}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    {t.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full border border-white/8 bg-white/[0.03] px-1.5 py-[1px] font-mono text-[9px] uppercase tracking-[0.18em] text-text-muted/85"
                      >
                        {tag}
                      </span>
                    ))}
                    <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-primary/12 px-2 py-[3px] font-mono text-[9px] font-medium uppercase tracking-[0.2em] text-primary/95 transition-colors group-hover:bg-primary/22">
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
