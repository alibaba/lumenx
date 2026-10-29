"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from "react";
import { createPortal } from "react-dom";
import { ArrowUp, ChevronDown, Loader2, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import { composerBooleanDefault, isOrderedComposerModel, resolveOrderedMediaSelections, type AtelierComposerMode, type AtelierComposerModelOption, type AtelierComposerSource, type AtelierComposerValues } from "@/lib/atelierGenParams";

const chipClass = "inline-flex h-6 shrink-0 items-center gap-[5px] whitespace-nowrap rounded-full border border-white/[0.09] bg-black/30 px-2.5 text-[11px] text-white/55 transition-[border-color,color] hover:border-white/20 hover:text-white/85 disabled:cursor-not-allowed disabled:opacity-35";
const textInputClass = "h-6 min-w-[150px] flex-1 rounded-full border border-white/[0.09] bg-black/30 px-2.5 text-[11px] text-white/75 outline-none placeholder:text-white/25 focus:border-white/20";

export type PinnedComposerTargetRect = { left: number; top: number; width: number; height: number };
type DockSide = "screen" | "bottom" | "top" | "right" | "left";
type DockPlacement = { left: number; top: number; width: number; maxHeight: number; side: DockSide };

export function pinnedComposerPlacement(
    anchor: PinnedComposerTargetRect | null,
    viewport: { width: number; height: number },
    options: { height?: number; availableRect?: PinnedComposerTargetRect; obstacles?: PinnedComposerTargetRect[] } = {},
): DockPlacement {
    const gutter = 12;
    const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(value, Math.max(min, max)));
    const area = options.availableRect;
    const leftEdge = clamp(area?.left ?? gutter, gutter, viewport.width - gutter - 1);
    const topEdge = clamp(area?.top ?? gutter, gutter, viewport.height - gutter - 1);
    const right = Math.max(leftEdge + 1, Math.min(viewport.width - gutter, area ? area.left + area.width : viewport.width - gutter));
    const bottom = Math.max(topEdge + 1, Math.min(viewport.height - gutter, area ? area.top + area.height : viewport.height - gutter));
    const preferredWidth = Math.min(700, Math.max(560, viewport.width * 0.38), right - leftEdge);
    const height = options.height ?? 150;
    const obstacles = options.obstacles ?? [];
    const bottomFor = (left: number, width: number, limit: number) => obstacles.reduce((edge, obstacle) => (
        left < obstacle.left + obstacle.width + gutter && left + width > obstacle.left - gutter ? Math.min(edge, obstacle.top - gutter) : edge
    ), limit);

    if (!anchor) {
        const width = preferredWidth;
        const left = leftEdge + (right - leftEdge - width) / 2;
        const lowerEdge = Math.max(topEdge + 1, bottomFor(left, width, bottom - 8));
        const maxHeight = lowerEdge - topEdge;
        return { left, top: lowerEdge - Math.min(height, maxHeight), width, maxHeight, side: "screen" };
    }

    const centreX = anchor.left + anchor.width / 2;
    const centreY = anchor.top + anchor.height / 2;
    const horizontalLeft = clamp(centreX - preferredWidth / 2, leftEdge, right - preferredWidth);
    const lanes = [
        { side: "bottom" as const, left: leftEdge, top: Math.max(topEdge, anchor.top + anchor.height + gutter), right, bottom },
        { side: "top" as const, left: leftEdge, top: topEdge, right, bottom: Math.min(bottom, anchor.top - gutter) },
        { side: "right" as const, left: Math.max(leftEdge, anchor.left + anchor.width + gutter), top: topEdge, right, bottom },
        { side: "left" as const, left: leftEdge, top: topEdge, right: Math.min(right, anchor.left - gutter), bottom },
    ];
    const candidates: DockPlacement[] = [];
    for (const lane of lanes) {
        const width = Math.min(preferredWidth, lane.right - lane.left);
        if (width < Math.min(320, preferredWidth)) continue;
        const vertical = lane.side === "bottom" || lane.side === "top";
        const left = vertical ? horizontalLeft : lane.side === "right" ? lane.left : lane.right - width;
        const lowerEdge = bottomFor(left, width, lane.bottom);
        const maxHeight = lowerEdge - lane.top;
        if (maxHeight < Math.min(120, height)) continue;
        const shownHeight = Math.min(height, maxHeight);
        const top = lane.side === "bottom" ? lane.top : lane.side === "top" ? lowerEdge - shownHeight : clamp(centreY - shownHeight / 2, lane.top, lowerEdge - shownHeight);
        candidates.push({ left, top, width, maxHeight: lowerEdge - top, side: lane.side });
    }
    const full = candidates.find(item => item.width >= preferredWidth && item.maxHeight >= height);
    if (full) return full;
    if (candidates.length) return candidates.sort((a, b) => b.width * Math.min(height, b.maxHeight) - a.width * Math.min(height, a.maxHeight))[0];
    const lowerEdge = Math.max(topEdge + 1, bottomFor(horizontalLeft, preferredWidth, bottom));
    const maxHeight = lowerEdge - topEdge;
    const top = clamp(anchor.top + anchor.height + gutter, topEdge, lowerEdge - Math.min(height, maxHeight));
    return { left: horizontalLeft, top, width: preferredWidth, maxHeight: lowerEdge - top, side: "bottom" };
}

function useViewportMetrics(contentRef: RefObject<HTMLDivElement>) {
    const [metrics, setMetrics] = useState({ width: 1440, height: 900, contentHeight: 150 });
    useLayoutEffect(() => {
        const measure = () => setMetrics({
            width: window.innerWidth,
            height: window.innerHeight,
            contentHeight: Math.ceil(contentRef.current?.getBoundingClientRect().height || 148) + 2,
        });
        measure();
        const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
        if (contentRef.current) observer?.observe(contentRef.current);
        window.addEventListener("resize", measure);
        return () => { observer?.disconnect(); window.removeEventListener("resize", measure); };
    }, [contentRef]);
    return metrics;
}

type Copy = {
    prompt: string;
    firstFrame: string;
    duration: string;
    resolution: string;
    advanced: string;
    seed: string;
    negativePrompt: string;
    promptExtend: string;
    watermark: string;
    saveDraft: string;
    restoreDraft: string;
    create: string;
    working: string;
    missingSelectedFrameOption: string;
};

type ChipProps = {
    label: string;
    value: string;
    options: Array<{ value: string; label: string; disabled?: boolean }>;
    disabled?: boolean;
    onChange: (value: string) => void;
};

function useChipPopover(preferredWidth: number) {
    const [open, setOpen] = useState(false);
    const triggerRef = useRef<HTMLButtonElement>(null);
    const panelRef = useRef<HTMLDivElement>(null);
    const [style, setStyle] = useState<CSSProperties>({ position: "fixed", visibility: "hidden" });
    const close = useCallback((restoreFocus = false) => {
        setOpen(false);
        if (restoreFocus) triggerRef.current?.focus();
    }, []);
    useLayoutEffect(() => {
        if (!open) return;
        const position = () => {
            const anchor = triggerRef.current?.getBoundingClientRect();
            const panel = panelRef.current;
            if (!anchor || !panel) return;
            const width = Math.min(preferredWidth, window.innerWidth - 24);
            const height = Math.min(panel.scrollHeight || 240, 320);
            const below = Math.max(0, window.innerHeight - anchor.bottom - 12);
            const above = Math.max(0, anchor.top - 12);
            const upwards = above >= height || above >= below;
            const maxHeight = Math.max(80, Math.min(320, upwards ? above : below, window.innerHeight - 24));
            const shownHeight = Math.min(height, maxHeight);
            setStyle({
                position: "fixed",
                width,
                left: Math.max(12, Math.min(anchor.left, window.innerWidth - width - 12)),
                top: upwards ? Math.max(12, anchor.top - shownHeight - 6) : Math.min(anchor.bottom + 6, window.innerHeight - shownHeight - 12),
                maxHeight,
                overflowY: "auto",
                overscrollBehavior: "contain",
                zIndex: 80,
            });
        };
        position();
        const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(position);
        if (panelRef.current) observer?.observe(panelRef.current);
        window.addEventListener("resize", position);
        window.addEventListener("scroll", position, true);
        return () => { observer?.disconnect(); window.removeEventListener("resize", position); window.removeEventListener("scroll", position, true); };
    }, [open, preferredWidth]);
    useEffect(() => {
        if (!open) return;
        const outside = (event: Event) => {
            const node = event.target as Node;
            if (!triggerRef.current?.contains(node) && !panelRef.current?.contains(node)) close();
        };
        const key = (event: KeyboardEvent) => {
            if (event.key === "Escape" && !event.isComposing) { event.preventDefault(); event.stopPropagation(); close(true); }
        };
        window.addEventListener("pointerdown", outside);
        window.addEventListener("focusin", outside);
        window.addEventListener("keydown", key, true);
        return () => { window.removeEventListener("pointerdown", outside); window.removeEventListener("focusin", outside); window.removeEventListener("keydown", key, true); };
    }, [open, close]);
    return { open, setOpen, close, triggerRef, panelRef, style };
}

function UpChip({ label, value, options, disabled, onChange }: ChipProps) {
    const popover = useChipPopover(240);
    const { open, close } = popover;
    const selected = options.find(option => option.value === value);
    useEffect(() => { if (disabled && open) close(); }, [disabled, open, close]);
    const select = (next: string) => { onChange(next); popover.close(true); };
    return <span className="relative inline-block shrink-0">
        <button ref={popover.triggerRef} type="button" aria-label={label} aria-haspopup="listbox" aria-expanded={popover.open} value={value}
            disabled={disabled || !options.length} className={chipClass} onClick={() => popover.setOpen(open => !open)}
            onKeyDown={event => {
                if (!["Enter", " ", "ArrowDown", "ArrowUp"].includes(event.key)) return;
                event.preventDefault();
                popover.setOpen(true);
            }}>
            {selected?.label ?? value}<ChevronDown size={11} aria-hidden="true" className={popover.open ? "rotate-180" : ""} />
        </button>
        {popover.open ? createPortal(<div ref={popover.panelRef} role="listbox" aria-label={label} className="z-[80] rounded-xl border border-white/[0.1] bg-[#07070b]/95 p-1 shadow-2xl backdrop-blur-xl" style={popover.style}
            onKeyDown={event => event.stopPropagation()}>
            {options.map(option => <button key={option.value} type="button" role="option" aria-selected={option.value === value} disabled={option.disabled}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[11px] text-white/65 hover:bg-white/[0.08] disabled:cursor-not-allowed disabled:opacity-35"
                onClick={() => select(option.value)}>
                <span aria-hidden="true" className={option.value === value ? "opacity-100" : "opacity-0"}>✓</span>{option.label}
            </button>)}
        </div>, document.body) : null}
    </span>;
}


function SliderChip({ label, value, min, max, step, suffix, disabled, autoLabel, autoOffValue, onChange }: {
    label: string;
    value: number;
    min: number;
    max: number;
    step: number;
    suffix?: string;
    disabled?: boolean;
    autoLabel?: string;
    autoOffValue?: number;
    onChange: (value: number) => void;
}) {
    const popover = useChipPopover(196);
    const { open, close } = popover;
    useEffect(() => { if (disabled && open) close(); }, [disabled, open, close]);
    const isAuto = autoLabel !== undefined && value === -1;
    const sliderValue = isAuto ? min : value;
    useLayoutEffect(() => {
        if (!popover.open || popover.style.visibility === "hidden") return;
        popover.panelRef.current?.querySelector<HTMLElement>("button, input:not(:disabled)")?.focus({ preventScroll: true });
    }, [popover.open, popover.panelRef, popover.style.visibility]);
    return <span className="relative inline-block shrink-0">
        <button ref={popover.triggerRef} type="button" aria-label={label} aria-haspopup="dialog" aria-expanded={popover.open} disabled={disabled}
            className={chipClass} onClick={() => popover.setOpen(open => !open)}>
            {isAuto ? <span className="text-emerald-200">{autoLabel}</span> : <>{value}{suffix}</>}
        </button>
        {popover.open ? createPortal(<div ref={popover.panelRef} role="dialog" aria-label={label} className="z-[80] rounded-xl border border-white/[0.1] bg-[#07070b]/95 p-2.5 shadow-2xl backdrop-blur-xl" style={popover.style}
            onKeyDown={event => event.stopPropagation()}>
            {autoLabel !== undefined ? <button type="button" onClick={() => onChange(isAuto ? (autoOffValue ?? min) : -1)}
                className={`mb-1.5 flex w-full items-center justify-between rounded-md border px-2 py-1 text-[11px] ${isAuto ? "border-emerald-300/35 bg-emerald-400/10 text-emerald-100" : "border-white/[0.09] bg-black/30 text-white/55"}`}>
                <span>{autoLabel}</span><span className="font-mono">{isAuto ? "ON" : "OFF"}</span>
            </button> : null}
            <div className="mb-1.5 flex items-center justify-between text-[11px] text-white/55"><span>{label}</span><span className="font-mono text-white/80">{isAuto ? autoLabel : `${value}${suffix ?? ""}`}</span></div>
            <input type="range" aria-label={label} min={min} max={max} step={step} value={sliderValue} disabled={isAuto} onChange={event => onChange(Number(event.target.value))}
                className={`w-full accent-emerald-300 ${isAuto ? "pointer-events-none opacity-40" : ""}`} />
            <div className="mt-1 flex justify-between text-[10px] text-white/30"><span>{min}{suffix}</span><span>{max}{suffix}</span></div>
        </div>, document.body) : null}
    </span>;
}

function ToggleChip({ label, value, disabled, onChange }: { label: string; value: boolean; disabled?: boolean; onChange: (value: boolean) => void }) {
    return <button type="button" aria-label={label} aria-pressed={value} disabled={disabled} onClick={() => onChange(!value)}
        className={`${chipClass} ${value ? "border-emerald-300/35 bg-emerald-400/10 text-emerald-100" : ""}`}>{label}</button>;
}


function StaticChip({ label, value }: { label: string; value: string }) {
    return <span aria-label={label} className="inline-flex h-6 shrink-0 cursor-default items-center gap-[5px] whitespace-nowrap rounded-full border border-dashed border-white/[0.09] bg-black/20 px-2.5 text-[11px] text-white/40">{value}</span>;
}

function mediaLabel(source: AtelierComposerSource): string {
    return `${source.title} · ${source.mediaUrl}`;
}

export function PinnedComposer({
    values,
    models,
    sources,
    disabled,
    busy,
    missingSelectedFrame,
    missingLastFrame,
    canCreate,
    canSave,
    targetRect,
    targetAvailableRect,
    targetLabel,
    copy,
    onChange,
    onCreate,
    onSaveDraft,
    onRestoreDraft,
}: {
    values: AtelierComposerValues;
    models: AtelierComposerModelOption[];
    sources: AtelierComposerSource[];
    disabled: boolean;
    busy: string | null;
    missingSelectedFrame: boolean;
    missingLastFrame: boolean;
    canCreate: boolean;
    canSave: boolean;
    targetRect?: PinnedComposerTargetRect | null;
    targetAvailableRect?: PinnedComposerTargetRect | null;
    targetLabel: string;
    copy: Copy;
    onChange: (patch: Partial<AtelierComposerValues>) => void;
    onCreate: () => void;
    onSaveDraft: () => void;
    onRestoreDraft: () => void;
}) {
    const t = useTranslations("atelier.pinned");
    const [advancedOpen, setAdvancedOpen] = useState(false);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const contentRef = useRef<HTMLDivElement>(null);
    const metrics = useViewportMetrics(contentRef);
    const composingRef = useRef(false);
    const selectedModel = models.find(model => model.id === values.modelId) ?? models[0];
    const ordered = isOrderedComposerModel(values.modelId);
    const imageSources = sources.filter(source => source.kind === "image");
    const resolutionOptions = selectedModel?.params?.resolution?.options ?? [values.resolution].filter(Boolean);
    const ratioOptions = selectedModel?.params?.ratio?.options ?? [values.ratio].filter(Boolean);
    const durationSpec = selectedModel?.duration;
    const durationMin = durationSpec?.min ?? Math.min(1, values.duration === -1 ? durationSpec?.default ?? 5 : values.duration);
    const durationMax = durationSpec?.max ?? Math.max(10, durationSpec?.default ?? 5, values.duration === -1 ? 10 : values.duration);
    const durationStep = durationSpec?.step ?? 1;
    const referenceMedia = resolveOrderedMediaSelections(values.referenceMedia, sources);
    const actionLabel = busy === "create" ? copy.working : ordered ? t("generate") : copy.create;

    useEffect(() => { setAdvancedOpen(false); }, [values.modelId, values.mode]);

    const placement = targetRect ? pinnedComposerPlacement(targetRect, metrics, { height: metrics.contentHeight, availableRect: targetAvailableRect ?? undefined }) : null;
    const shellClass = targetRect
        ? "group/comp fixed z-[55] space-y-2 overflow-visible rounded-[18px] border border-white/[0.09] bg-black/70 p-3 shadow-[0_24px_80px_rgba(0,0,0,0.45)] backdrop-blur-xl transition-[box-shadow,transform,left,top] focus-within:shadow-[inset_0_1px_0_rgba(255,255,255,0.1),0_24px_80px_rgba(0,0,0,0.50),0_0_0_1px_rgba(99,102,241,0.28)]"
        : "group/comp relative space-y-2 overflow-visible rounded-[18px] border border-white/[0.09] bg-black/25 p-3 shadow-[0_24px_80px_rgba(0,0,0,0.24)] focus-within:shadow-[inset_0_1px_0_rgba(255,255,255,0.1),0_24px_80px_rgba(0,0,0,0.36),0_0_0_1px_rgba(99,102,241,0.28)]";
    const shellStyle: CSSProperties | undefined = placement ? { left: placement.left, top: placement.top, width: placement.width, maxHeight: placement.maxHeight, overflowY: "auto", overscrollBehavior: "contain" } : undefined;

    const body = <div role="search" aria-label="Shot parameters" data-composer-dock={targetRect ? "node" : "inspector"} data-composer-side={placement?.side ?? "inspector"} className={shellClass} style={shellStyle}>
        <div ref={contentRef}>
        <div aria-hidden="true" className="pointer-events-none absolute left-4 right-4 top-0 h-px bg-gradient-to-r from-transparent via-indigo-300/55 to-transparent opacity-0 transition-opacity group-focus-within/comp:opacity-90" />
        <div className="flex items-start gap-2.5">
            <Sparkles size={15} aria-hidden="true" className="mt-2 shrink-0 text-white/40 transition-colors group-focus-within/comp:text-emerald-300" />
            <textarea ref={inputRef} aria-label={copy.prompt} aria-keyshortcuts="Meta+Enter Control+Enter" rows={2} value={values.prompt}
                onCompositionStart={() => { composingRef.current = true; }} onCompositionEnd={() => { composingRef.current = false; }}
                onChange={event => onChange({ prompt: event.target.value })}
                onKeyDown={event => {
                    event.stopPropagation();
                    if (composingRef.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
                    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                        event.preventDefault();
                        if (!disabled && busy === null && canCreate) onCreate();
                    }
                }}
                placeholder={t("placeholder")} className="min-w-0 flex-1 resize-none border-none bg-transparent text-[13.5px] text-white/[0.88] outline-none placeholder:text-white/[0.32]" />
            <span className="mt-1 shrink-0 rounded-md border border-white/[0.08] bg-white/[0.04] px-2 py-1 text-[10px] text-white/35">{targetLabel}</span>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
            <UpChip label={t("model")} value={values.modelId} disabled={disabled || models.length < 2}
                options={models.map(model => ({ value: model.id, label: `${model.name}${model.status === "planned" ? " · planned" : ""}`, disabled: model.disabled }))}
                onChange={value => {
                    const model = models.find(item => item.id === value) ?? models[0];
                    const nextOrdered = isOrderedComposerModel(model.id);
                    onChange({ modelId: model.id, mode: nextOrdered ? "t2v" : "i2v", resolution: model.params?.resolution?.default ?? values.resolution,
                        ratio: model.params?.ratio?.default ?? values.ratio, duration: model.duration?.default ?? model.duration?.value ?? values.duration,
                        promptExtend: composerBooleanDefault(model, "promptExtend"),
                        watermark: composerBooleanDefault(model, "watermark"),
                        audio: composerBooleanDefault(model, "audio") });
                }} />
            {ordered ? <UpChip label={t("mode")} value={values.mode} disabled={disabled}
                options={[{ value: "t2v", label: "T2V" }, { value: "firstlast", label: "I2V" }, { value: "r2v", label: "R2V" }]}
                onChange={value => onChange({ mode: value as AtelierComposerMode })} /> : null}
            {!ordered || values.mode === "firstlast" ? <UpChip label={copy.firstFrame} value={values.firstSourceKey} disabled={disabled || !imageSources.length}
                options={[...(missingSelectedFrame ? [{ value: values.firstSourceKey, label: copy.missingSelectedFrameOption, disabled: true }] : []), ...imageSources.map(source => ({ value: `${source.nodeId}:${source.mediaUrl}`, label: mediaLabel(source) }))]}
                onChange={value => onChange({ firstSourceKey: value })} /> : null}
            {ordered && values.mode === "firstlast" ? <UpChip label={t("mediaRoles.last_frame")} value={values.lastSourceKey} disabled={disabled || !imageSources.length}
                options={[{ value: "", label: "—" }, ...(missingLastFrame ? [{ value: values.lastSourceKey, label: copy.missingSelectedFrameOption, disabled: true }] : []), ...imageSources.map(source => ({ value: `${source.nodeId}:${source.mediaUrl}`, label: mediaLabel(source) }))]}
                onChange={value => onChange({ lastSourceKey: value })} /> : null}
            {durationSpec?.type === "fixed" ? <StaticChip label={copy.duration} value={`${durationSpec.value ?? durationSpec.default ?? values.duration}s`} />
                : durationSpec?.type === "buttons" && durationSpec.options?.length ? <UpChip label={copy.duration} value={String(values.duration)} disabled={disabled}
                    options={durationSpec.options.map(option => ({ value: String(option), label: `${option}s` }))} onChange={value => onChange({ duration: Number(value) })} />
                : <SliderChip label={copy.duration} value={values.duration} min={durationMin} max={durationMax} step={durationStep} suffix="s" disabled={disabled}
                    autoLabel={durationSpec?.supports_auto ? t("smartDuration") : undefined} autoOffValue={durationSpec?.default}
                    onChange={value => onChange({ duration: value })} />}
            <UpChip label={copy.resolution} value={values.resolution} options={resolutionOptions.map(option => ({ value: option, label: option }))} onChange={value => onChange({ resolution: value })} />
            {ordered ? <UpChip label={t("aspect")} value={values.ratio} options={ratioOptions.map(option => ({ value: option, label: option }))} onChange={value => onChange({ ratio: value })} /> : null}
            <UpChip label={t("candidates")} value={String(values.batchSize)} disabled={!ordered} options={[1, 2, 3, 4, 5, 6].map(value => ({ value: String(value), label: String(value) }))} onChange={value => onChange({ batchSize: Number(value) })} />
            <button type="button" aria-label={copy.advanced} aria-expanded={advancedOpen} onClick={() => setAdvancedOpen(open => !open)} className={chipClass}>{copy.advanced}<ChevronDown size={11} aria-hidden="true" className={advancedOpen ? "rotate-180" : ""} /></button>
            <button type="button" disabled={disabled || busy !== null || !canSave} onClick={onSaveDraft} className={chipClass}>{busy === "save-draft" ? copy.working : copy.saveDraft}</button>
            <button type="button" disabled={disabled || busy !== null} onClick={onRestoreDraft} className={chipClass}>{busy === "restore-draft" ? copy.working : copy.restoreDraft}</button>
            <button type="button" disabled={disabled || busy !== null || !canCreate} onClick={onCreate}
                className={`ml-auto inline-flex min-h-[30px] items-center gap-1.5 rounded-[10px] border px-2.5 py-1 text-[11px] transition active:scale-95 disabled:cursor-not-allowed disabled:opacity-35 ${canCreate ? "border-transparent bg-emerald-300 text-[#06120b]" : "border-white/[0.12] bg-white/[0.08] text-white/60"}`}>
                {busy === "create" ? <Loader2 size={14} aria-hidden="true" className="animate-spin" /> : <ArrowUp size={14} aria-hidden="true" />}
                {actionLabel}
            </button>
        </div>

        {ordered && values.mode === "r2v" && referenceMedia.length ? <div className="flex flex-wrap gap-1.5 border-t border-white/[0.06] pt-2">
            {referenceMedia.map(item => <span key={`${item.role}:${item.ordinal}:${item.node_id}:${item.media_url}`} className={`rounded-full border px-2 py-1 text-[10px] ${item.available ? "border-white/[0.09] bg-black/30 text-white/45" : "border-amber-300/25 bg-amber-500/10 text-amber-100"}`}>
                {t(`mediaRoles.${item.role}`)} · {item.title ?? item.node_id} · {item.media_url}
            </span>)}
        </div> : null}

        {advancedOpen ? <div className="flex flex-wrap items-center gap-1.5 border-t border-white/[0.06] pt-2" onKeyDown={event => { if (event.key === "Escape") setAdvancedOpen(false); }}>
            <input aria-label={copy.seed} value={values.seed} placeholder={copy.seed} onChange={event => onChange({ seed: event.target.value })} className={textInputClass} />
            {!ordered ? <input aria-label={copy.negativePrompt} value={values.negativePrompt} placeholder={copy.negativePrompt} onChange={event => onChange({ negativePrompt: event.target.value })} className={textInputClass} /> : null}
            <ToggleChip label={copy.promptExtend} value={values.promptExtend} onChange={value => onChange({ promptExtend: value })} />
            <ToggleChip label={copy.watermark} value={values.watermark} onChange={value => onChange({ watermark: value })} />
            {ordered ? <ToggleChip label={t("audio")} value={values.audio} onChange={value => onChange({ audio: value })} /> : null}
        </div> : null}
        </div>
    </div>;

    return targetRect && typeof document !== "undefined" ? createPortal(body, document.body) : body;
}
