"use client";
//
// WorkflowsPanel — body for the LeftRailV3 Workflows mode (Sprint C).
// Browses local workflow templates, filters by category, fires `onInsert`
// when the user picks one. Insert handler in the shell does the actual
// node creation + reference wiring.
//
// Per Codex doc §4.7 / §7.7. Open-source local-first; no marketplace,
// no remote catalog, no auth. Templates ship in workflowTemplates.ts.
import { useMemo, useState } from "react";
import { Sparkles } from "lucide-react";
import {
  TEMPLATE_CATEGORY_LABELS,
  WORKFLOW_TEMPLATES,
  type TemplateCategory,
  type WorkflowTemplate,
} from "./workflowTemplates";

interface Props {
  onInsert: (template: WorkflowTemplate) => void;
}

const ALL_CATEGORIES: Array<TemplateCategory | "all"> = [
  "all",
  "story",
  "character",
  "scene",
  "product",
  "motion",
  "utility",
];

export function WorkflowsPanel({ onInsert }: Props) {
  const [filter, setFilter] = useState<TemplateCategory | "all">("all");

  const filtered = useMemo(() => {
    if (filter === "all") return WORKFLOW_TEMPLATES;
    return WORKFLOW_TEMPLATES.filter((t) => t.category === filter);
  }, [filter]);

  return (
    <div className="flex h-full flex-col">
      {/* Category filter row — same vocabulary as AssetLibrary's kind
          chips so the two panels feel like part of the same system. */}
      <div className="shrink-0 border-b border-white/6 px-3 py-2">
        <div role="tablist" aria-label="Template category" className="flex flex-wrap items-center gap-1">
          {ALL_CATEGORIES.map((cat) => {
            const isActive = filter === cat;
            const label = cat === "all" ? "All" : TEMPLATE_CATEGORY_LABELS[cat];
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
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Templates list — each card is the full payload preview. Click
          calls onInsert; the shell handles "where to drop" by computing
          a viewport-center offset. */}
      <ul className="flex-1 space-y-2 overflow-y-auto p-2.5">
        {filtered.length === 0 ? (
          <li className="grid place-items-center px-3 py-8 text-center text-text-muted/85">
            <div className="font-mono text-[9.5px] uppercase tracking-[0.28em]">
              No templates in this category
            </div>
          </li>
        ) : (
          filtered.map((t) => (
            <li key={t.id}>
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
                    {TEMPLATE_CATEGORY_LABELS[t.category]}
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
            </li>
          ))
        )}
      </ul>
    </div>
  );
}
