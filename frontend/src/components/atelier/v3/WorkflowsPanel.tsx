"use client";

import { useMemo, useState } from "react";
import { TemplateThumbnail } from "./TemplateThumbnail";
import { BUILT_IN_WORKFLOW_TEMPLATES, WORKFLOW_TEMPLATE_MIME, type WorkflowTemplate, type WorkflowTemplateCategory } from "./workflowTemplates";

type Labels = { title: string; all: string; insert: string; drag: string };
const categories: Array<WorkflowTemplateCategory | "all"> = ["all", "story", "image", "video", "audio"];
const categoryLabel = (value: WorkflowTemplateCategory | "all", all: string): string =>
  value === "all" ? all : `${value.slice(0, 1).toUpperCase()}${value.slice(1)}`;

export function WorkflowsPanel({ labels, disabled, onInsert }: { labels: Labels; disabled?: boolean; onInsert: (template: WorkflowTemplate) => void }) {
  const [category, setCategory] = useState<WorkflowTemplateCategory | "all">("all");
  const templates = useMemo(() => category === "all" ? BUILT_IN_WORKFLOW_TEMPLATES : BUILT_IN_WORKFLOW_TEMPLATES.filter(template => template.category === category), [category]);
  return <section aria-label={labels.title} className="w-[300px] space-y-3 rounded-2xl border border-white/[0.08] bg-black/45 p-3 shadow-[0_24px_80px_rgba(0,0,0,0.35)] backdrop-blur-xl">
    <div className="flex items-center justify-between gap-3">
      <h2 className="font-display text-sm font-medium text-white/80">{labels.title}</h2>
      <span className="text-[11px] text-white/35">{labels.drag}</span>
    </div>
    <div className="flex flex-wrap gap-1.5" role="tablist" aria-label={labels.title}>
      {categories.map(item => <button key={item} type="button" role="tab" aria-selected={category === item} onClick={() => setCategory(item)}
        className={`rounded-full border px-2.5 py-1 text-[11px] ${category === item ? "border-white/25 bg-white/14 text-white" : "border-white/[0.08] bg-white/[0.04] text-white/45 hover:text-white/75"}`}>{categoryLabel(item, labels.all)}</button>)}
    </div>
    <div className="max-h-[54dvh] space-y-2 overflow-y-auto pr-1">
      {templates.map(template => <article key={template.id} data-testid="workflow-template-card" draggable={!disabled}
        onDragStart={event => { event.dataTransfer.effectAllowed = "copy"; event.dataTransfer.setData(WORKFLOW_TEMPLATE_MIME, template.id); }}
        className="rounded-2xl border border-white/[0.07] bg-white/[0.035] p-2.5">
        <TemplateThumbnail template={template} />
        <div className="mt-2 flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-medium text-white/78">{template.title}</h3>
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-white/42">{template.summary}</p>
          </div>
          <button type="button" disabled={disabled} onClick={() => { if (!disabled) onInsert(template); }}
            className="shrink-0 rounded-lg border border-white/[0.1] bg-white/[0.06] px-2.5 py-1.5 text-xs text-white/72 hover:bg-white/[0.1] disabled:cursor-not-allowed disabled:opacity-40">{labels.insert}</button>
        </div>
      </article>)}
    </div>
  </section>;
}
