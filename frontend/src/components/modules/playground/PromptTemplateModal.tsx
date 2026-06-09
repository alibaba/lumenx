"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, Plus, Copy, Trash2, Sparkles, BookmarkPlus, Star } from "lucide-react";
import { playgroundApi } from "@/lib/api";
import { usePlaygroundStore, type PlaygroundTemplate } from "./usePlaygroundStore";

const CATEGORIES = [
  { value: "image", label: "图像", color: "text-blue-400" },
  { value: "video", label: "视频", color: "text-purple-400" },
  { value: "general", label: "通用", color: "text-text-secondary" },
] as const;

type CategoryValue = (typeof CATEGORIES)[number]["value"];

function categoryMeta(cat: string) {
  return CATEGORIES.find((c) => c.value === cat) ?? CATEGORIES[2];
}

interface FormState {
  name: string;
  category: CategoryValue;
  prompt: string;
}

const EMPTY_FORM: FormState = { name: "", category: "general", prompt: "" };

export default function PromptTemplateModal() {
  const {
    templates,
    showTemplateModal,
    setShowTemplateModal,
    applyTemplate,
    removeTemplate,
    addTemplate,
    prompt: currentPrompt,
    mode: currentMode,
    modelId: currentModelId,
    parameters: currentParams,
    negativePrompt: currentNegativePrompt,
    toggleTemplateFavorite,
    isTemplateFavorited,
  } = usePlaygroundStore();

  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [filterCat, setFilterCat] = useState<CategoryValue | "all">("all");

  const dialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!showTemplateModal) return;
    const t = window.setTimeout(() => dialogRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, [showTemplateModal]);

  useEffect(() => {
    if (!showTemplateModal) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); setShowTemplateModal(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showTemplateModal, setShowTemplateModal]);

  // Auto-open form with pre-filled prompt when modal opens with currentPrompt set
  useEffect(() => {
    if (showTemplateModal && currentPrompt.trim() && !formOpen) {
      setForm({
        name: "",
        category: (currentMode === "t2i" || currentMode === "i2i") ? "image"
          : (currentMode === "t2v" || currentMode === "i2v" || currentMode === "r2v" || currentMode === "v2v") ? "video"
          : "general",
        prompt: currentPrompt,
      });
      setFormOpen(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showTemplateModal]);

  const handleApply = useCallback((tpl: PlaygroundTemplate) => {
    applyTemplate(tpl);
    setShowTemplateModal(false);
  }, [applyTemplate, setShowTemplateModal]);

  const handleDelete = useCallback(async (id: string) => {
    setDeletingId(id);
    try { await playgroundApi.deleteTemplate(id); removeTemplate(id); } catch { /* silent */ }
    finally { setDeletingId(null); }
  }, [removeTemplate]);

  const handleCreate = useCallback(async () => {
    if (!form.name.trim() || !form.prompt.trim()) return;
    setBusy(true);
    try {
      const created = await playgroundApi.createTemplate({
        name: form.name.trim(),
        category: form.category,
        prompt: form.prompt.trim(),
        negative_prompt: currentNegativePrompt || undefined,
        default_mode: currentMode,
        default_model_id: currentModelId || undefined,
        default_parameters: Object.keys(currentParams).length > 0 ? currentParams : undefined,
      });
      addTemplate(created as unknown as PlaygroundTemplate);
      setForm(EMPTY_FORM);
      setFormOpen(false);
    } catch { /* silent */ }
    finally { setBusy(false); }
  }, [form, addTemplate, currentMode, currentModelId, currentParams, currentNegativePrompt]);

  const handlePrefill = useCallback(() => {
    setForm({
      name: "",
      category: (currentMode === "t2i" || currentMode === "i2i") ? "image"
        : (currentMode === "t2v" || currentMode === "i2v" || currentMode === "r2v" || currentMode === "v2v") ? "video"
        : "general",
      prompt: currentPrompt,
    });
    setFormOpen(true);
  }, [currentPrompt, currentMode]);

  if (!showTemplateModal || typeof window === "undefined") return null;

  const filtered = (filterCat === "all"
    ? templates
    : templates.filter((t) => t.category === filterCat)
  ).sort((a, b) => {
    const aFav = isTemplateFavorited(a.id) ? 0 : 1;
    const bFav = isTemplateFavorited(b.id) ? 0 : 1;
    return aFav - bFav;
  });

  const modal = (
    <>
      <div
        aria-hidden="true"
        className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm"
        onClick={() => setShowTemplateModal(false)}
      />

      <div className="fixed inset-0 z-50 flex items-center justify-center pointer-events-none">
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          tabIndex={-1}
          className="pointer-events-auto w-[560px] max-h-[85vh] bg-elevated border border-glass-border rounded-2xl shadow-[0_24px_80px_rgba(0,0,0,0.28)] flex flex-col overflow-hidden outline-none"
        >
          {/* Header */}
          <div className="px-6 py-5 border-b border-glass-border flex items-center justify-between shrink-0">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-primary/15 flex items-center justify-center">
                <BookmarkPlus size={16} className="text-primary" />
              </div>
              <div>
                <h2 className="text-[15px] font-semibold text-foreground">Prompt 模板</h2>
                <p className="text-[10px] text-text-muted mt-0.5">保存常用提示词，一键套用</p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setShowTemplateModal(false)}
              className="grid h-8 w-8 place-items-center rounded-lg text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground"
            >
              <X size={16} />
            </button>
          </div>

          {/* Filter tabs */}
          <div className="px-6 pt-4 pb-2 flex gap-1.5 shrink-0">
            {[{ value: "all" as const, label: "全部" }, ...CATEGORIES].map((c) => (
              <button
                key={c.value}
                type="button"
                onClick={() => setFilterCat(c.value)}
                className={[
                  "px-3 py-1.5 rounded-md text-[11px] font-medium transition-all",
                  filterCat === c.value
                    ? "text-foreground bg-hover-bg border border-glass-border"
                    : "text-text-secondary hover:text-foreground hover:bg-hover-bg border border-transparent",
                ].join(" ")}
              >
                {c.label}
                {c.value !== "all" && (
                  <span className="ml-1.5 text-[9px] text-text-muted">
                    {templates.filter((t) => t.category === c.value).length}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Template list */}
          <div className="flex-1 overflow-y-auto px-6 py-3 min-h-0 space-y-2">
            {filtered.length === 0 && (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <Sparkles size={28} className="text-text-muted mb-3" />
                <p className="text-[13px] text-text-secondary mb-1">
                  {filterCat === "all" ? "暂无模板" : `暂无${categoryMeta(filterCat).label}模板`}
                </p>
                <p className="text-[11px] text-text-muted">点击下方「新建」创建你的第一个模板</p>
              </div>
            )}

            {filtered.map((tpl) => {
              const meta = categoryMeta(tpl.category);
              return (
                <div
                  key={tpl.id}
                  className="group p-4 rounded-xl bg-glass border border-glass-border hover:border-primary/30 hover:bg-hover-bg transition-all"
                >
                  <div className="flex items-start gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1.5">
                        <span className="text-[13px] font-medium text-foreground truncate">{tpl.name}</span>
                        <span className={`text-[9px] font-mono uppercase px-1.5 py-[2px] rounded bg-glass shrink-0 ${meta.color}`}>
                          {meta.label}
                        </span>
                        {tpl.default_mode && (
                          <span className="text-[9px] font-mono uppercase px-1.5 py-[2px] rounded bg-glass text-text-muted shrink-0">
                            {tpl.default_mode}
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-text-secondary line-clamp-2 leading-[1.6]">{tpl.prompt}</p>
                    </div>

                    {/* Actions — visible on hover */}
                    <div className="flex gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        type="button"
                        onClick={() => toggleTemplateFavorite(tpl.id)}
                        className={`h-7 w-7 rounded-md flex items-center justify-center transition-colors ${
                          isTemplateFavorited(tpl.id)
                            ? 'text-amber-400'
                            : 'text-text-muted hover:text-amber-400/70'
                        }`}
                        title={isTemplateFavorited(tpl.id) ? '取消收藏' : '收藏'}
                      >
                        <Star size={12} className={isTemplateFavorited(tpl.id) ? 'fill-amber-400' : ''} />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleApply(tpl)}
                        className="h-7 px-2.5 rounded-md text-[11px] font-medium text-primary bg-primary/10 hover:bg-primary/20 transition-colors flex items-center gap-1"
                      >
                        <Copy size={11} />
                        套用
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(tpl.id)}
                        disabled={deletingId === tpl.id}
                        className="h-7 w-7 rounded-md flex items-center justify-center text-text-muted hover:text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-30"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Footer: create form */}
          <div className="border-t border-glass-border shrink-0">
            {/* Toggle row */}
            <div className="px-6 py-3 flex items-center">
              {!formOpen ? (
                <>
                  <button
                    type="button"
                    onClick={() => setFormOpen(true)}
                    className="inline-flex items-center gap-1.5 text-xs font-medium text-text-secondary hover:text-foreground transition-colors"
                  >
                    <Plus size={14} />
                    新建模板
                  </button>
                  {currentPrompt.trim() && (
                    <button
                      type="button"
                      onClick={handlePrefill}
                      className="ml-auto inline-flex items-center gap-1.5 text-[11px] font-medium text-text-muted hover:text-text-secondary transition-colors"
                    >
                      <Copy size={11} />
                      从当前输入保存
                    </button>
                  )}
                </>
              ) : (
                <>
                  <span className="text-xs font-medium text-text-secondary">新建模板</span>
                  <button
                    type="button"
                    onClick={() => { setFormOpen(false); setForm(EMPTY_FORM); }}
                    className="ml-auto text-[11px] text-text-muted hover:text-text-secondary transition-colors"
                  >
                    收起
                  </button>
                </>
              )}
            </div>

            {/* Create form */}
            {formOpen && (
              <div className="px-6 pb-5 space-y-3">
                {/* Name + Category row */}
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={form.name}
                    onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                    placeholder="模板名称"
                    className="flex-1 h-9 px-3 text-[13px] bg-input-bg border border-glass-border rounded-lg text-foreground placeholder:text-text-muted outline-none focus:border-primary/40 transition-colors"
                    autoFocus
                  />
                </div>

                {/* Category pills */}
                <div className="flex gap-0 p-[2px] bg-glass rounded-lg border border-glass-border">
                  {CATEGORIES.map((c) => (
                    <button
                      key={c.value}
                      type="button"
                      onClick={() => setForm((f) => ({ ...f, category: c.value as CategoryValue }))}
                      className={[
                        "flex-1 py-[6px] rounded-md text-[11px] font-medium text-center cursor-pointer transition-all",
                        form.category === c.value
                          ? "text-white bg-primary shadow-[0_1px_4px_rgba(100,108,255,0.3)]"
                          : "text-text-secondary hover:text-foreground",
                      ].join(" ")}
                    >
                      {c.label}
                    </button>
                  ))}
                </div>

                {/* Prompt */}
                <textarea
                  value={form.prompt}
                  onChange={(e) => setForm((f) => ({ ...f, prompt: e.target.value }))}
                  placeholder="输入 Prompt 内容..."
                  rows={5}
                  className="w-full min-h-[120px] px-3 py-2.5 text-[13px] leading-relaxed bg-input-bg border border-glass-border rounded-lg text-foreground placeholder:text-text-muted outline-none focus:border-primary/40 transition-colors resize-y"
                />

                {/* Submit */}
                <button
                  type="button"
                  onClick={handleCreate}
                  disabled={busy || !form.name.trim() || !form.prompt.trim()}
                  className={[
                    "w-full h-9 rounded-lg text-[13px] font-medium transition-all",
                    form.name.trim() && form.prompt.trim()
                      ? "bg-primary text-white hover:bg-secondary shadow-[0_2px_12px_rgba(100,108,255,0.25)]"
                      : "bg-glass text-text-muted cursor-not-allowed",
                  ].join(" ")}
                >
                  {busy ? "保存中…" : "保存模板"}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );

  return createPortal(modal, document.body);
}
