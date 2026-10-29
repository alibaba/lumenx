"use client";

import { useId, useState } from "react";
import { Link2, Unlink } from "lucide-react";
import {
    ATELIER_REFERENCE_KINDS, canAttachAtelierReference, getAtelierCanvasNodeData,
    getAtelierPendingReferenceNodes, getAtelierReferenceMediaUrls, getAtelierReferenceNodeIds, resolveAtelierReferenceMedia,
} from "@/lib/atelierCanvas";
import type { AtelierProject, AttachAtelierPendingReferenceInput, AttachAtelierReferenceInput, DetachAtelierReferenceInput } from "@/lib/atelierTypes";

interface ReferenceControlsProps {
    project: AtelierProject;
    targetId: string;
    ownerKey: string;
    disabled?: boolean;
    labels: { title: string; select: string; empty: string; attach: string; detach: string; pending?: string; pendingReady?: string; attachPending?: string; confirmPending?: string };
    onAttach: (input: Omit<AttachAtelierReferenceInput, "expectedRevision">) => void;
    onAttachPending?: (input: Omit<AttachAtelierPendingReferenceInput, "expectedRevision">) => void;
    onDetach: (input: Omit<DetachAtelierReferenceInput, "expectedRevision">) => void;
}

export function ReferenceControls({ project, targetId, ownerKey, disabled, labels, onAttach, onAttachPending, onDetach }: ReferenceControlsProps) {
    const selectId = useId();
    const owner = JSON.stringify([ownerKey, project.id, targetId]);
    const [choice, setChoice] = useState({ owner, sourceId: "" });
    const target = project.nodes.find(node => node.id === targetId);
    if (!target || (target.type !== "image" && target.type !== "video")) return null;

    const data = getAtelierCanvasNodeData(target);
    const ids = getAtelierReferenceNodeIds(target);
    const compatible = project.nodes.filter(node => canAttachAtelierReference(target, node));
    const pending = getAtelierPendingReferenceNodes(target);
    const pendingIds = new Set(pending.map(reference => reference.source_node_id));
    const attached = compatible.filter(node => ids.includes(node.id) || node.data.parent_node_id === targetId);
    const actualSources = compatible.filter(node => !attached.includes(node) && resolveAtelierReferenceMedia(node));
    const pendingSources = compatible.filter(node => !attached.includes(node) && !resolveAtelierReferenceMedia(node) && getAtelierReferenceMediaUrls(node).length === 0);
    const sources = [...actualSources, ...pendingSources];
    const chosen = choice.owner === owner ? sources.find(node => node.id === choice.sourceId) : undefined;
    const chosenHasMedia = chosen ? Boolean(resolveAtelierReferenceMedia(chosen)) : false;
    const urls = Array.from(new Set(ATELIER_REFERENCE_KINDS.flatMap(kind => data[`reference_${kind}_urls`] ?? [])));
    const ownedUrls = new Set(attached.flatMap(getAtelierReferenceMediaUrls));
    const rows = attached.filter(node => !pendingIds.has(node.id)).map(node => ({ key: node.id, title: node.title || node.id, sourceNodeId: node.id }));
    const pendingRows = pending.map(reference => {
        const source = project.nodes.find(node => node.id === reference.source_node_id);
        return {
            key: reference.source_node_id,
            title: source?.title || reference.source_node_id,
            sourceNodeId: reference.source_node_id,
            hasCurrentMedia: Boolean(source && source.type === reference.source_kind
                && canAttachAtelierReference(target, source) && resolveAtelierReferenceMedia(source)),
        };
    });
    const looseUrls = urls.filter(url => !ownedUrls.has(url));
    const buttonClass = "inline-flex items-center justify-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-2 text-xs text-white/80 hover:bg-white/5 disabled:opacity-40 disabled:cursor-not-allowed";

    return <section aria-label={labels.title} className="space-y-3 border-t border-white/10 pt-4">
        <h3 className="text-xs font-medium text-white/45">{labels.title}</h3>
        {(rows.length > 0 || pendingRows.length > 0 || looseUrls.length > 0) && <ul className="space-y-2">
            {pendingRows.map(row => <li key={`pending:${row.key}`} className="flex items-center justify-between gap-2 text-xs text-white/80">
                <span className="min-w-0 truncate">
                    <span className="mr-2 rounded-full border border-amber-300/20 bg-amber-300/10 px-1.5 py-0.5 text-[10px] uppercase tracking-[0.16em] text-amber-100/70">{row.hasCurrentMedia ? labels.pendingReady ?? "Ready to confirm" : labels.pending ?? "Pending media"}</span>
                    {row.title}
                </span>
                <span className="flex shrink-0 items-center gap-1.5">
                    {row.hasCurrentMedia ? <button type="button" className={buttonClass} disabled={disabled}
                        aria-label={`${labels.confirmPending ?? labels.attach} ${row.title}`}
                        onClick={() => onAttach({ sourceNodeId: row.sourceNodeId })}>
                        <Link2 size={13} aria-hidden="true" />{labels.confirmPending ?? labels.attach}
                    </button> : null}
                    <button type="button" className={buttonClass} disabled={disabled}
                        aria-label={`${labels.detach} ${row.title}`}
                        onClick={() => onDetach({ sourceNodeId: row.sourceNodeId })}>
                        <Unlink size={13} aria-hidden="true" />{labels.detach}
                    </button>
                </span>
            </li>)}
            {rows.map(row => <li key={row.key} className="flex items-center justify-between gap-2 text-xs text-white/80">
                <span className="truncate">{row.title}</span>
                <button type="button" className={buttonClass} disabled={disabled}
                    aria-label={`${labels.detach} ${row.title}`}
                    onClick={() => onDetach({ sourceNodeId: row.sourceNodeId })}>
                    <Unlink size={13} aria-hidden="true" />{labels.detach}
                </button>
            </li>)}
            {looseUrls.map(url => <li key={url} className="flex items-center justify-between gap-2 text-xs text-white/80">
                <span className="truncate" title={url}>{url}</span>
                <button type="button" className={buttonClass} disabled={disabled} aria-label={`${labels.detach} ${url}`}
                    onClick={() => onDetach({ referenceUrl: url })}><Unlink size={13} aria-hidden="true" />{labels.detach}</button>
            </li>)}
        </ul>}
        {sources.length ? <div className="space-y-2">
            <label htmlFor={selectId} className="sr-only">{labels.select}</label>
            <select id={selectId} value={chosen?.id ?? ""} disabled={disabled}
                onChange={event => setChoice({ owner, sourceId: event.target.value })}
                className="w-full rounded-lg border border-white/10 bg-[#17171c] px-2.5 py-2 text-xs text-white/80">
                <option value="">{labels.select}</option>
                {sources.map(node => <option key={node.id} value={node.id}>{node.title || node.id}</option>)}
            </select>
            <button type="button" className={buttonClass} disabled={disabled || !chosen || (!chosenHasMedia && !onAttachPending)}
                onClick={() => {
                    if (!chosen) return;
                    if (chosenHasMedia) onAttach({ sourceNodeId: chosen.id });
                    else onAttachPending?.({ sourceNodeId: chosen.id });
                }}>
                <Link2 size={13} aria-hidden="true" />{chosen && !chosenHasMedia ? labels.attachPending ?? labels.attach : labels.attach}
            </button>
        </div> : <p className="text-xs leading-relaxed text-white/45">{labels.empty}</p>}
    </section>;
}
