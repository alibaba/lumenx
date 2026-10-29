"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import { X, Plus, Copy, Trash2, Sparkles, Star, ChevronLeft } from "lucide-react";
import { playgroundApi } from "@/lib/api";
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { usePlaygroundStore, type PlaygroundTemplate } from "./usePlaygroundStore";

const CATEGORIES = [
  { value: "image", labelKey: "template.catImage", color: "text-primary" },
  { value: "video", labelKey: "template.catVideo", color: "text-accent" },
  { value: "general", labelKey: "template.catGeneral", color: "text-text-muted" },
] as const;

type CategoryValue = (typeof CATEGORIES)[number]["value"];

function categoryMeta(cat: string) {
  return CATEGORIES.find((c) => c.value === cat) ?? CATEGORIES[2];
}

function modeToCategory(mode: string): CategoryValue {
  if (mode === "t2i" || mode === "i2i") return "image";
  if (mode === "t2v" || mode === "i2v" || mode === "r2v" || mode === "v2v") return "video";
  return "general";
}

interface FormState {
  name: string;
  category: CategoryValue;
  prompt: string;
}

const EMPTY_FORM: FormState = { name: "", category: "general", prompt: "" };

/**
 * Prompt-template surface — a right-side drawer (mirrors PromptHistoryDrawer) with
 * two views: BROWSE (filter + cards) and CREATE (full-height form). Leaving the
 * create view via outside-click / Esc / X / back returns to browse and preserves
 * the draft (no data loss, no confirm); a full close only happens from browse.
 */
export default function PromptTemplateModal() {
  const t = useTranslations("playground");
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
    negativePromptProvided: currentNegativePromptProvided,
    toggleTemplateFavorite,
    isTemplateFavorited,
  } = usePlaygroundStore();

  const [view, setView] = useState<"browse" | "create">("browse");
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [filterCat, setFilterCat] = useState<CategoryValue | "all">("all");
  const [visible, setVisible] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const nameInputRef = useRef<HTMLInputElement | null>(null);
  const activationRef = useRef(0);

  useEffect(() => {
    activationRef.current += 1;
    const activation = activationRef.current;
    const unsubscribe = subscribeRuntimeEnvironment(() => {
      activationRef.current += 1;
      setBusy(false);
      setDeletingId(null);
      setActionError(null);

      const state = usePlaygroundStore.getState();
      if (!state.showTemplateModal) return;
      if (state.prompt.trim()) {
        setForm({ name: "", category: modeToCategory(state.mode), prompt: state.prompt });
        setView("create");
      } else {
        setForm(EMPTY_FORM);
        setView("browse");
      }
    });
    return () => {
      unsubscribe();
      if (activationRef.current === activation) activationRef.current += 1;
    };
  }, []);

  // Animate in after mount; open straight into CREATE (prefilled) when there is a
  // current prompt to capture, otherwise BROWSE.
  useEffect(() => {
    if (showTemplateModal) {
      const raf = requestAnimationFrame(() => setVisible(true));
      setActionError(null);
      if (currentPrompt.trim()) {
        setForm({ name: "", category: modeToCategory(currentMode), prompt: currentPrompt });
        setView("create");
      } else {
        setView("browse");
      }
      return () => cancelAnimationFrame(raf);
    }
    setVisible(false);
  }, [showTemplateModal]); // eslint-disable-line react-hooks/exhaustive-deps

  // Autofocus the name field on entering CREATE
  useEffect(() => {
    if (showTemplateModal && view === "create") {
      const id = window.setTimeout(() => nameInputRef.current?.focus(), 60);
      return () => window.clearTimeout(id);
    }
  }, [showTemplateModal, view]);

  // Animated close — resets to browse + clears the form after the slide-out.
  const close = useCallback(() => {
    setVisible(false);
    window.setTimeout(() => {
      setShowTemplateModal(false);
      setView("browse");
      setForm(EMPTY_FORM);
    }, 250);
  }, [setShowTemplateModal]);

  // Outside-click / Esc / X: from CREATE, retreat to BROWSE (keep the draft); from
  // BROWSE, close the drawer. Never silently discards an unsaved template.
  const dismiss = useCallback(() => {
    if (view === "create") {
      setView("browse");
    } else {
      close();
    }
  }, [view, close]);

  // Esc to dismiss
  useEffect(() => {
    if (!showTemplateModal) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        dismiss();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showTemplateModal, dismiss]);

  const handleApply = useCallback(
    (tpl: PlaygroundTemplate) => {
      applyTemplate(tpl);
      close();
    },
    [applyTemplate, close],
  );

  const handleDelete = useCallback(
    async (id: string) => {
      const capturedRuntime = getRuntimeEnvironment();
      const activation = activationRef.current;
      setDeletingId(id);
      setActionError(null);
      try {
        await playgroundApi.deleteTemplate(id);
        if (activationRef.current !== activation || (capturedRuntime && !isCurrentRuntimeEnvironment(capturedRuntime.key, capturedRuntime.generation))) return;
        removeTemplate(id);
      } catch {
        if (activationRef.current === activation && (!capturedRuntime || isCurrentRuntimeEnvironment(capturedRuntime.key, capturedRuntime.generation))) {
          setActionError(t("template.deleteFailed"));
        }
      } finally {
        if (activationRef.current === activation && (!capturedRuntime || isCurrentRuntimeEnvironment(capturedRuntime.key, capturedRuntime.generation))) {
          setDeletingId(null);
        }
      }
    },
    [removeTemplate, t],
  );

  const handleCreate = useCallback(async () => {
    if (!form.name.trim() || !form.prompt.trim()) return;
    const capturedRuntime = getRuntimeEnvironment();
    const activation = activationRef.current;
    setBusy(true);
    setActionError(null);
    try {
      const created = await playgroundApi.createTemplate({
        name: form.name.trim(),
        category: form.category,
        prompt: form.prompt.trim(),
        negative_prompt: currentNegativePromptProvided || currentNegativePrompt !== '' ? currentNegativePrompt : undefined,
        default_mode: currentMode,
        default_model_id: currentModelId,
        default_parameters: currentParams,
      });
      if (activationRef.current !== activation || (capturedRuntime && !isCurrentRuntimeEnvironment(capturedRuntime.key, capturedRuntime.generation))) return;
      addTemplate(created as unknown as PlaygroundTemplate);
      setForm(EMPTY_FORM);
      setView("browse");
    } catch {
      if (activationRef.current === activation && (!capturedRuntime || isCurrentRuntimeEnvironment(capturedRuntime.key, capturedRuntime.generation))) {
        setActionError(t("template.createFailed"));
      }
    } finally {
      if (activationRef.current === activation && (!capturedRuntime || isCurrentRuntimeEnvironment(capturedRuntime.key, capturedRuntime.generation))) {
        setBusy(false);
      }
    }
  }, [form, addTemplate, currentMode, currentModelId, currentParams, currentNegativePrompt, currentNegativePromptProvided, t]);

  // Enter CREATE — resume an in-progress draft if any, else prefill from current state.
  const openCreate = useCallback(() => {
    setForm((f) =>
      f.name.trim() || f.prompt.trim()
        ? f
        : { name: "", category: modeToCategory(currentMode), prompt: currentPrompt },
    );
    setView("create");
  }, [currentMode, currentPrompt]);

  // "Save from current" — always (re)prefill from the current compose state.
  const saveFromCurrent = useCallback(() => {
    setForm({ name: "", category: modeToCategory(currentMode), prompt: currentPrompt });
    setView("create");
  }, [currentMode, currentPrompt]);

  if (!showTemplateModal || typeof window === "undefined") return null;

  const filtered = (
    filterCat === "all" ? templates : templates.filter((x) => x.category === filterCat)
  ).sort((a, b) => {
    const aFav = isTemplateFavorited(a.id) ? 0 : 1;
    const bFav = isTemplateFavorited(b.id) ? 0 : 1;
    return aFav - bFav;
  });

  const drawer = (
    // Transparent click-catcher — no dark scrim, workspace stays visible (side panel).
    <div className="fixed inset-0 z-50" onClick={dismiss}>
      <div
        className="fixed right-0 top-0 h-full w-[420px] bg-elevated border-l border-glass-border shadow-2xl flex flex-col transition-transform duration-250 ease-out"
        style={{ transform: visible ? "translateX(0)" : "translateX(100%)" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── Header ── */}
        <div className="px-6 py-5 border-b border-border-subtle flex items-center gap-2 shrink-0">
          {view === "create" && (
            <button
              type="button"
              onClick={() => setView("browse")}
              className="w-8 h-8 -ml-1.5 rounded-lg flex items-center justify-center text-text-muted hover:text-foreground hover:bg-hover-bg transition-colors cursor-pointer"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
          )}
          <h2 className="font-display atelier-display text-[1.375rem] font-semibold tracking-tight text-foreground">
            {view === "create" ? t("template.newTemplate") : t("template.title")}
          </h2>
          <button
            type="button"
            onClick={dismiss}
            className="ml-auto w-8 h-8 rounded-lg flex items-center justify-center text-text-muted hover:text-foreground hover:bg-hover-bg transition-colors cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {actionError && (
          <div className="mx-6 mt-3 rounded-[12px] border border-status-failed-border bg-status-failed-bg px-3 py-2 text-[0.6875rem] text-status-failed-fg">
            {actionError}
          </div>
        )}

        {view === "browse" ? (
          <>
            {/* ── Filter pills ── */}
            <div className="px-6 pt-4 pb-2 shrink-0">
              <div className="flex gap-[2px] p-[3px] bg-surface-inset rounded-full atelier-pill-tabs">
                {[{ value: "all" as const, labelKey: "template.filterAll" as const }, ...CATEGORIES].map((c) => (
                  <button
                    key={c.value}
                    type="button"
                    onClick={() => setFilterCat(c.value)}
                    className={[
                      "flex-1 rounded-full px-2 py-1.5 text-[0.6875rem] font-medium text-center cursor-pointer transition-all",
                      filterCat === c.value
                        ? "bg-surface text-foreground atelier-pill-tab-active"
                        : "text-text-muted hover:text-foreground hover:bg-hover-bg",
                    ].join(" ")}
                  >
                    {t(c.labelKey)}
                    {c.value !== "all" && (
                      <span className="ml-1 text-[0.5625rem] opacity-60">
                        {templates.filter((x) => x.category === c.value).length}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </div>

            {/* ── Template list ── */}
            <div className="flex-1 overflow-y-auto px-6 py-2 min-h-0 space-y-2">
              {filtered.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full text-center px-6">
                  <Sparkles className="w-7 h-7 text-text-muted/60 mb-3" />
                  <p className="font-display italic text-[0.9375rem] text-text-secondary mb-1.5 leading-relaxed">
                    {filterCat === "all"
                      ? t("template.emptyAll")
                      : t("template.emptyFiltered", { category: t(categoryMeta(filterCat).labelKey) })}
                  </p>
                  <p className="text-[0.6875rem] text-text-muted">{t("template.emptyHint")}</p>
                </div>
              ) : (
                filtered.map((tpl) => {
                  const meta = categoryMeta(tpl.category);
                  return (
                    <div
                      key={tpl.id}
                      className="group p-4 bg-glass atelier-asset-card border border-glass-border hover:border-foreground/30 transition-all"
                    >
                      <div className="flex items-start gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1.5">
                            <span className="text-[0.8125rem] font-medium text-foreground truncate">{tpl.name}</span>
                            <span className={`text-[0.5625rem] font-mono uppercase px-1.5 py-[2px] rounded bg-elevated shrink-0 ${meta.color}`}>
                              {t(meta.labelKey)}
                            </span>
                            {tpl.default_mode && (
                              <span className="text-[0.5625rem] font-mono uppercase px-1.5 py-[2px] rounded bg-glass text-text-muted shrink-0">
                                {tpl.default_mode}
                              </span>
                            )}
                          </div>
                          <p className="font-display italic text-[0.8125rem] text-text-secondary line-clamp-2 leading-[1.6]">
                            {tpl.prompt}
                          </p>
                        </div>

                        {/* Actions — visible on hover */}
                        <div className="flex gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            type="button"
                            onClick={() => toggleTemplateFavorite(tpl.id)}
                            className={`h-7 w-7 rounded-md flex items-center justify-center transition-colors ${
                              isTemplateFavorited(tpl.id)
                                ? "text-status-starred-solid"
                                : "text-text-muted hover:text-status-starred-solid/70"
                            }`}
                            title={isTemplateFavorited(tpl.id) ? t("template.unfavorite") : t("template.favorite")}
                          >
                            <Star size={12} className={isTemplateFavorited(tpl.id) ? "fill-status-starred-solid" : ""} />
                          </button>
                          <button
                            type="button"
                            onClick={() => handleApply(tpl)}
                            className="h-7 px-2.5 rounded-md text-[0.6875rem] font-medium text-primary bg-primary/10 hover:bg-primary/20 transition-colors flex items-center gap-1"
                          >
                            <Copy size={11} />
                            {t("template.apply")}
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDelete(tpl.id)}
                            disabled={deletingId === tpl.id}
                            title={t("template.delete")}
                            className="h-7 w-7 rounded-md flex items-center justify-center text-text-muted hover:text-status-failed-fg hover:bg-status-failed-bg transition-colors disabled:opacity-30"
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            {/* ── Footer: new template ── */}
            <div className="border-t border-border-subtle shrink-0 px-6 py-3 flex items-center">
              <button
                type="button"
                onClick={openCreate}
                className="inline-flex items-center gap-1.5 text-xs font-medium text-text-muted hover:text-foreground transition-colors"
              >
                <Plus size={14} />
                {t("template.newTemplate")}
              </button>
              {currentPrompt.trim() && (
                <button
                  type="button"
                  onClick={saveFromCurrent}
                  className="ml-auto inline-flex items-center gap-1.5 text-[0.6875rem] font-medium text-text-muted hover:text-foreground transition-colors"
                >
                  <Copy size={11} />
                  {t("template.saveFromCurrent")}
                </button>
              )}
            </div>
          </>
        ) : (
          /* ── Create view (full drawer height) ── */
          <div className="flex-1 flex flex-col min-h-0">
            <div className="flex-1 overflow-y-auto px-6 py-4 flex flex-col gap-3 min-h-0">
              {/* Name */}
              <input
                ref={nameInputRef}
                type="text"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder={t("template.namePlaceholder")}
                className="w-full h-9 px-3 text-[0.8125rem] bg-surface-inset border border-glass-border rounded-[14px] text-foreground placeholder:text-text-muted outline-none focus:border-primary transition-colors shrink-0"
              />

              {/* Category pills */}
              <div className="flex gap-[2px] p-[3px] bg-surface-inset rounded-full atelier-pill-tabs shrink-0">
                {CATEGORIES.map((c) => (
                  <button
                    key={c.value}
                    type="button"
                    onClick={() => setForm((f) => ({ ...f, category: c.value as CategoryValue }))}
                    className={[
                      "flex-1 py-[6px] rounded-full text-[0.6875rem] font-medium text-center cursor-pointer transition-all",
                      form.category === c.value
                        ? "bg-primary text-on-accent"
                        : "text-text-muted hover:text-foreground hover:bg-hover-bg",
                    ].join(" ")}
                  >
                    {t(c.labelKey)}
                  </button>
                ))}
              </div>

              {/* Prompt */}
              <textarea
                value={form.prompt}
                onChange={(e) => setForm((f) => ({ ...f, prompt: e.target.value }))}
                placeholder={t("template.promptPlaceholder")}
                className="w-full flex-1 min-h-[180px] px-3 py-2.5 text-[0.8125rem] leading-relaxed bg-surface-inset border border-glass-border rounded-[14px] text-foreground placeholder:text-text-muted outline-none focus:border-primary transition-colors resize-none"
              />
            </div>

            {/* Footer: save */}
            <div className="border-t border-border-subtle shrink-0 px-6 py-4">
              <button
                type="button"
                onClick={handleCreate}
                disabled={busy || !form.name.trim() || !form.prompt.trim()}
                className={[
                  "w-full h-9 rounded-full text-[0.8125rem] font-medium transition-all",
                  form.name.trim() && form.prompt.trim()
                    ? "bg-primary text-on-accent hover:bg-primary-hover shadow-[var(--glow-primary)]"
                    : "bg-surface-inset text-text-muted cursor-not-allowed",
                ].join(" ")}
              >
                {busy ? t("template.saving") : t("template.save")}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );

  return createPortal(drawer, document.body);
}
