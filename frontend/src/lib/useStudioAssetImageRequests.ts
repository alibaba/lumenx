'use client';

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { readProjectOnce } from '@/lib/projectReadback';
import type { ImageVariant, Project } from '@/store/projectStore';
import type { StudioAssetImageRequest } from '@/lib/studioAssetImageContract';
import { listStudioAssetImageRequests, readStudioAssetImageRequestReceipt, resolveStudioAssetImageRequest,
    isStudioFullBodyEditRequest, isStudioGptImageModel, isStudioWanImageModel, isStudioQwenImageModel, storyboardImageRetentionProof, submitStudioAssetImageRequest, type StoredStudioAssetImageRequest, type StudioAssetImageReadResult, type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';

type AssetKind = 'character' | 'scene' | 'prop' | 'storyboard_frame';
type ImageChannel = 'reference_sheet' | 'full_body' | 'three_view' | 'headshot' | 'image';

type FullBodyCandidate = {
    base_character_id?: string | null;
    three_view_asset?: { variants?: { is_uploaded_source?: boolean }[] } | null;
    headshot_asset?: { variants?: { is_uploaded_source?: boolean }[] } | null;
    full_body_asset?: { variants?: { is_uploaded_source?: boolean }[] } | null;
};
/** Uploaded full-body artwork does not introduce a reference-generation dependency. */
export function isStudioFullBodyEligible(asset: FullBodyCandidate): boolean {
    return !asset.base_character_id && ![asset.three_view_asset, asset.headshot_asset]
        .some(unit => unit?.variants?.some(variant => variant.is_uploaded_source));
}

export function usesStudioQwenImageCore(kind: string | null, model: string, channel: string): boolean {
    return (kind === 'character' && (channel === 'reference_sheet' || channel === 'full_body' || channel === 'three_view')
        || (kind === 'scene' || kind === 'prop' || kind === 'storyboard_frame') && channel === 'image')
        && isStudioQwenImageModel(model);
}

export function usesStudioQwenReferenceSheet(kind: string | null, model: string, channel: string): boolean {
    return channel === 'reference_sheet' && usesStudioQwenImageCore(kind, model, channel);
}

export function usesStudioGptReferenceSheet(kind: string | null, model: string, channel: string): boolean {
    return kind === 'character' && channel === 'reference_sheet' && isStudioGptImageModel(model);
}

export function usesStudioGptThreeView(kind: string | null, model: string, channel: string): boolean {
    return kind === 'character' && channel === 'three_view' && isStudioGptImageModel(model);
}

export function usesStudioGptHeadshot(kind: string | null, model: string, channel: string): boolean {
    return kind === 'character' && channel === 'headshot' && isStudioGptImageModel(model);
}

export function usesStudioImageCore(kind: string | null, model: string, channel: string): boolean {
    const supported = kind === 'character' && (channel === 'reference_sheet' || channel === 'full_body')
        || (kind === 'scene' || kind === 'prop') && channel === 'image';
    return supported && (isStudioWanImageModel(model) || isStudioGptImageModel(model))
        || kind === 'character' && channel === 'three_view' && isStudioWanImageModel(model)
        || usesStudioQwenImageCore(kind, model, channel)
        || usesStudioGptThreeView(kind, model, channel) || usesStudioGptHeadshot(kind, model, channel);
}

type Options = { kind: AssetKind | null; channel?: ImageChannel; open: boolean; projectId?: string; assetId: string | null; source?: string; seriesId?: string | null;
    /** Library owns uncached projections; validate this predicate before applying any fetched snapshot. */
    readback?: (isCurrent: (snapshot: Project) => boolean) => Promise<void>;
    /** Derived-image draft edits must invalidate readback even if their values later return. */
    isDerivedImageReadbackCurrent?: (result: StudioAssetImageReadResult, snapshot?: Project) => boolean;
};
type View = { token: object; results: StudioAssetImageReadResult[]; reading: boolean; submitting: boolean; readFailed: boolean; readbackFailed: boolean; submitFailed: boolean };
const initial = (token: object): View => ({ token, results: [], reading: false, submitting: false, readFailed: false, readbackFailed: false, submitFailed: false });
const terminal = (result: StudioAssetImageReadResult) => result.status === 'confirmed' && (result.receipt?.request.members.every(m => m.status === 'completed' || m.status === 'cancelled') ?? false);
const pending = (result: StudioAssetImageReadResult) => result.status === 'confirmed' && (result.receipt?.request.members.some(m => m.status === 'pending' || m.status === 'processing') ?? false);
// Known recovery work remains unconfirmed in the UI while its original receipt is observed.
const shouldObserve = (result: StudioAssetImageReadResult) => pending(result) || result.status === 'confirmed'
    && !result.receipt?.reason && (result.receipt?.request.members.some(member => member.status === 'needs_confirmation'
        && ['provider_image_query_pending', 'provider_image_result_pending', 'provider_image_projection_pending']
            .includes(member.reason ?? '')) ?? false);
const unknown = (result: StudioAssetImageReadResult): StudioAssetImageReadResult => ({ status: 'unknown', journal: result.journal, reason: 'studio_image_receipt_unconfirmed' });

function rawImageValue(value: object | null | undefined, field = 'url'): unknown {
    if (!value) return undefined;
    const record = value as Record<string, unknown>, refs = record.media_refs;
    return refs && typeof refs === 'object' && Object.hasOwn(refs, field)
        ? (refs as Record<string, unknown>)[field] : record[field];
}

/** A receipt is durable before its public asset projection is necessarily visible. */
function hasAssetImageProjection(snapshot: Project, request: StudioAssetImageRequest, source?: string): boolean {
    if (request.request_root_kind !== 'project' || snapshot.id !== request.request_root_id
        || (snapshot.series_id ?? null) !== request.request_root_series_id) return false;
    const completed = request.members.filter(member => member.status === 'completed');
    let target: object, histories: ImageVariant[][], selected: string | null, alias: string;
    if (request.asset_type === 'character') {
        const targets = snapshot.characters.filter(item => item.id === request.asset_id);
        if (targets.length !== 1 || targets[0].source !== source) return false;
        const character = targets[0]; target = character;
        if (!completed.length) return true; // Cancelled work has no generated projection to prove.
        if (request.channel === 'full_body') {
            const legacy = character.full_body_asset, modern = character.full_body;
            if (character.full_body_request_id !== request.id || !legacy || !modern
                || legacy.selected_id !== modern.selected_image_id) return false;
            histories = [legacy.variants, modern.image_variants]; selected = legacy.selected_id;
            alias = 'full_body_image_url';
        } else if (request.channel === 'reference_sheet') {
            const unit = character.reference_sheet;
            if (!('reference_sheet_request_id' in character) || character.reference_sheet_request_id !== request.id || !unit) return false;
            histories = [unit.image_variants]; selected = unit.selected_image_id; alias = 'image_url';
        } else return false;
    } else if (request.asset_type === 'scene' || request.asset_type === 'prop') {
        const targets = (request.asset_type === 'scene' ? snapshot.scenes : snapshot.props).filter(item => item.id === request.asset_id);
        if (targets.length !== 1 || targets[0].source !== source) return false;
        const asset = targets[0]; target = asset;
        if (!completed.length) return true;
        if (!('image_request_id' in asset) || asset.image_request_id !== request.id || !asset.image_asset) return false;
        histories = [asset.image_asset.variants]; selected = asset.image_asset.selected_id; alias = 'image_url';
    } else return false;
    if (!histories.every(variants => completed.every(member => {
        const matches = variants.filter(variant => variant.id === member.variant_id);
        return matches.length === 1 && rawImageValue(matches[0]) === member.output_ref;
    }))) return false;
    // Recovery proves new history without choosing it over the author's selection.
    if (!selected) {
        const aliasValue = rawImageValue(target, alias);
        return selected === null && (aliasValue === undefined || aliasValue === null || aliasValue === '');
    }
    const selectedUrl = rawImageValue(target, alias);
    return typeof selectedUrl === 'string' && !!selectedUrl.trim() && histories.every(variants => {
        const matches = variants.filter(variant => variant.id === selected);
        return matches.length === 1 && rawImageValue(matches[0]) === selectedUrl;
    });
}

/** Transient receipt view only: journals and server receipts retain all durable authority. */
export function useStudioAssetImageRequests({ kind, channel = kind === 'character' ? 'reference_sheet' : 'image', open, projectId, assetId, source, seriesId, readback, isDerivedImageReadbackCurrent }: Options) {
    const environment = useSyncExternalStore(subscribeRuntimeEnvironment, getRuntimeEnvironment, () => null);
    const token = useMemo(() => ({ kind, channel, open, projectId, assetId, source, seriesId, environment }), [kind, channel, open, projectId, assetId, source, seriesId, environment]);
    const latest = useRef(token);
    latest.current = token;
    const derivedImageAuthorCurrent = useRef(isDerivedImageReadbackCurrent);
    derivedImageAuthorCurrent.current = isDerivedImageReadbackCurrent;
    const lifecycle = useRef<object | null>(null);
    const reading = useRef<object | null>(null);
    const submitting = useRef<object | null>(null);
    const exchanges = useRef({ next: 0, accepted: new Map<string, { lease: object; order: number }>() });
    const frameRecovery = useRef({ lease: null as object | null, blocked: new Set<string>(), kept: new Map<string, string>() });
    const [view, setView] = useState<View>(() => initial(token));
    const current = view.token === token ? view : initial(token);
    const visible = useRef(current.results);
    visible.current = current.results;
    const live = useCallback((lease: object | null) => open && !!environment && !!lease && lifecycle.current === lease
        && latest.current === token && isCurrentRuntimeEnvironment(environment.key, environment.generation), [open, environment, token]);
    const patch = useCallback((update: Partial<View>, lease: object) => {
        if (live(lease)) setView(previous => ({ ...(previous.token === token ? previous : initial(token)), ...update }));
    }, [live, token]);
    const frameProof = useCallback((result: StudioAssetImageReadResult) => {
        if (kind !== 'storyboard_frame' || !environment || !projectId || result.status !== 'confirmed'
            || result.journal.request.asset_id !== assetId) return null;
        return storyboardImageRetentionProof({ environment, rootKind: 'project', rootId: projectId,
            journal: result.journal, receipt: result.receipt });
    }, [kind, environment, projectId, assetId]);
    const keptResult = useCallback((result: StudioAssetImageReadResult) => {
        const recovery = frameRecovery.current, lease = lifecycle.current;
        return recovery.lease === lease && live(lease)
            && recovery.kept.get(result.journal.request.client_request_id) === frameProof(result)
            && recovery.kept.has(result.journal.request.client_request_id);
    }, [frameProof, live]);
    const blockedResult = useCallback((result: StudioAssetImageReadResult) => frameRecovery.current.lease === lifecycle.current
        && frameRecovery.current.blocked.has(result.journal.request.client_request_id) && !keptResult(result), [keptResult]);
    const consume = useCallback(async (received: StudioAssetImageReadResult[], lease: object, order: number, absent = new Set<string>(), keep?: { key: string; proof: string }) => {
        if (!live(lease) || !environment || !projectId) return;
        const accept = (id: string) => {
            const previous = exchanges.current.accepted.get(id);
            if (previous?.lease === lease && previous.order > order) return false;
            exchanges.current.accepted.set(id, { lease, order });
            return true;
        };
        const results = received.filter(result => accept(result.journal.request.client_request_id));
        const missing = new Set(Array.from(absent).filter(accept));
        const isLatest = (result: StudioAssetImageReadResult) => {
            const accepted = exchanges.current.accepted.get(result.journal.request.client_request_id);
            return live(lease) && accepted?.lease === lease && accepted.order === order;
        };
        if (!results.length && !missing.size) return;
        setView(previous => {
            const base = previous.token === token ? previous : initial(token);
            const merged = new Map(base.results.map(result => [result.journal.request.client_request_id,
                missing.has(result.journal.request.client_request_id) && shouldObserve(result) ? unknown(result) : result]));
            results.forEach(result => merged.set(result.journal.request.client_request_id, result));
            return { ...base, results: Array.from(merged.values()), readFailed: false };
        });
        if (kind === 'storyboard_frame') {
            const recovery = frameRecovery.current;
            for (const result of results) {
                if (!isLatest(result) || recovery.lease !== lease) continue;
                const key = result.journal.request.client_request_id;
                if (keptResult(result)) continue;
                if (recovery.kept.delete(key)) recovery.blocked.add(key);
                if (keep?.key === key) {
                    if (frameProof(result) === keep.proof) recovery.kept.set(key, keep.proof);
                    recovery.blocked.add(key);
                    continue; // Explicit retention is a receipt GET, never project readback/adoption.
                }
                if (!terminal(result) && !result.receipt?.request.members.some(member => member.status === 'completed')) continue;
                const request = result.receipt?.request;
                recovery.blocked.add(key);
                if (!request || result.receipt?.reason || request.members.some(member => member.status !== 'completed' || member.reason)
                    || request.owner_kind !== 'script' || request.owner_id !== projectId) continue;
                const isCurrent = (snapshot: Project) => isLatest(result) && snapshot.frames.some(frame => frame.id === assetId
                    && frame.image_request_id === request.id
                    && frame.rendered_image_asset?.selected_id === request.members[0].variant_id
                    && frame.rendered_image_asset?.variants?.some((variant: { id?: string }) => variant.id === request.members[0].variant_id));
                try {
                    if (readback) await readback(isCurrent);
                    else {
                        const snapshot = await readProjectOnce(projectId, undefined, { isCurrent });
                        if (!isCurrent(snapshot)) throw new Error('Frame receipt is no longer current');
                    }
                    if (isLatest(result) && result.receipt && resolveStudioAssetImageRequest({ environment, rootKind: 'project', rootId: projectId,
                        journal: result.journal, receipt: result.receipt })) {
                        recovery.blocked.delete(key);
                        exchanges.current.accepted.set(key, { lease, order: ++exchanges.current.next });
                    }
                } catch { /* The per-result blocker retains the original key and author draft. */ }
            }
            patch({}, lease);
            return;
        }
        const finished = results.filter(result => terminal(result) || result.receipt?.request.members.some(m => m.status === 'completed'));
        if (!finished.length) return;
        let readable = finished.filter(result => {
            const request = result.receipt?.request;
            return request && (source === 'global' ? request.owner_kind === 'global' && request.owner_id === 'library'
                : source === 'series' ? request.owner_kind === 'series' && request.owner_id === seriesId
                    : (source === undefined || source === 'episode') && request.owner_kind === 'script' && request.owner_id === projectId);
        });
        const ownerChanged = readable.length !== finished.length;
        if (!readable.length) { patch({ readbackFailed: true }, lease); return; }
        if (kind === 'character' && (channel === 'three_view' || channel === 'headshot'
            || channel === 'full_body' && readable.some(result => isStudioFullBodyEditRequest(result.journal.request)))) {
            let failed = ownerChanged;
            let checked = false;
            for (const result of readable) {
                // A full-body hook can retain historical T2I and new Edit journals together.
                const fullBodyEdit = isStudioFullBodyEditRequest(result.journal.request);
                if (channel === 'full_body' && !fullBodyEdit || !isLatest(result)) continue;
                const receipt = result.receipt, request = receipt?.request;
                const completed = request?.members.filter(member => member.status === 'completed') ?? [];
                const authorCurrent = (snapshot?: Project) => isLatest(result)
                    && (!derivedImageAuthorCurrent.current || derivedImageAuthorCurrent.current(result, snapshot));
                if (!receipt || !request || request.channel !== channel || receipt.reason
                    || !completed.length || completed.some(member => member.reason) || !authorCurrent()) {
                    failed = true; checked = true; continue;
                }
                const isCurrent = (snapshot: Project) => {
                    if (!authorCurrent(snapshot) || snapshot.id !== projectId
                        || (snapshot.series_id ?? null) !== request.request_root_series_id) return false;
                    const targets = snapshot.characters.filter(item => item.id === assetId && item.source === source);
                    if (targets.length !== 1) return false;
                    const target = targets[0];
                    if (channel === 'headshot' || fullBodyEdit) {
                        if ((fullBodyEdit ? target.full_body_request_id : target.headshot_request_id) !== request.id) return false;
                        const legacy = fullBodyEdit ? target.full_body_asset : target.headshot_asset;
                        const modern = fullBodyEdit ? target.full_body : target.head_shot;
                        if (!legacy || !modern || legacy.selected_id !== modern.selected_image_id) return false;
                        if (!completed.every(member => {
                            const old = legacy.variants.filter(value => value.id === member.variant_id);
                            const current = modern.image_variants.filter(value => value.id === member.variant_id);
                            return old.length === 1 && current.length === 1
                                && rawImageValue(old[0]) === member.output_ref && rawImageValue(current[0]) === member.output_ref;
                        })) return false;
                        if (!legacy.selected_id) {
                            const aliasValue = rawImageValue(target, fullBodyEdit ? 'full_body_image_url' : 'headshot_image_url');
                            // An unselected recovered headshot may retain an independent avatar.
                            return legacy.selected_id === null && (aliasValue === undefined || aliasValue === null || aliasValue === '');
                        }
                        if (legacy.selected_id) {
                            const old = legacy.variants.filter(value => value.id === legacy.selected_id);
                            const current = modern.image_variants.filter(value => value.id === legacy.selected_id);
                            const selectedUrl = rawImageValue(old[0]);
                            if (old.length !== 1 || current.length !== 1 || typeof selectedUrl !== 'string' || !selectedUrl.trim()
                                || selectedUrl !== rawImageValue(current[0])
                                || rawImageValue(target, fullBodyEdit ? 'full_body_image_url' : 'headshot_image_url') !== selectedUrl) return false;
                        }
                        return true;
                    }
                    if (!('three_view_request_id' in target) || target.three_view_request_id !== request.id) return false;
                    return completed.every(member => {
                        const variants = target.three_view_asset?.variants.filter(variant => variant.id === member.variant_id) ?? [];
                        if (variants.length !== 1 || member.status !== 'completed') return false;
                        const variant = variants[0], refs = 'media_refs' in variant ? variant.media_refs : null;
                        const raw = refs && typeof refs === 'object' && Object.hasOwn(refs, 'url') && 'url' in refs ? refs.url : variant.url;
                        return raw === member.output_ref;
                    });
                };
                try {
                    const snapshot = await readProjectOnce(projectId, undefined, { isCurrent });
                    // A newer store snapshot may supersede a valid HTTP response.
                    if (!isCurrent(snapshot)) throw new Error('Derived-image receipt is no longer current');
                    checked = true;
                    if (terminal(result) && resolveStudioAssetImageRequest({ environment, rootKind: 'project', rootId: projectId,
                        journal: result.journal, receipt })) {
                        exchanges.current.accepted.set(result.journal.request.client_request_id, { lease, order: ++exchanges.current.next });
                    }
                } catch { if (isLatest(result)) { failed = true; checked = true; } }
            }
            if (checked) patch({ readbackFailed: failed }, lease);
            if (channel !== 'full_body') return;
            // Rejected Edit proof must never reach generic T2I adoption through a mixed batch.
            if (failed || !checked) return;
            readable = readable.filter(result => !isStudioFullBodyEditRequest(result.journal.request));
            if (!readable.length) return;
        }
        let failed = ownerChanged;
        let checked = false;
        for (const result of readable) {
            if (!isLatest(result)) continue;
            const receipt = result.receipt, request = receipt?.request;
            if (!receipt || !request) continue;
            const completed = request.members.filter(member => member.status === 'completed');
            if (completed.length && (receipt.reason || completed.some(member => member.reason))) {
                failed = true; checked = true; continue;
            }
            const isCurrent = (snapshot: Project) => isLatest(result) && hasAssetImageProjection(snapshot, request, source);
            try {
                if (readback) await readback(isCurrent);
                else {
                    const snapshot = await readProjectOnce(projectId, undefined, { isCurrent });
                    // Recheck a newer store projection that may have superseded the fetched response.
                    if (!isCurrent(snapshot)) throw new Error('Asset image receipt is no longer current');
                }
                if (!isLatest(result)) continue;
                checked = true;
                if (terminal(result) && resolveStudioAssetImageRequest({ environment, rootKind: 'project', rootId: projectId,
                    journal: result.journal, receipt })) {
                    // Resolve only the receipt whose complete projection was just verified.
                    exchanges.current.accepted.set(result.journal.request.client_request_id, { lease, order: ++exchanges.current.next });
                }
            } catch { if (isLatest(result)) { failed = true; checked = true; } }
        }
        if (checked) patch({ readbackFailed: failed }, lease);
    }, [live, environment, projectId, token, source, seriesId, assetId, kind, channel, patch, readback, frameProof, keptResult]);
    const matchesTarget = useCallback((input: StudioAssetImageRequestInput) => input.asset_id === assetId
        && (kind === 'character' ? (input.asset_type ?? 'character') === 'character'
            && (input.generation_type ?? 'reference_sheet') === channel
            : channel === 'image' && (kind === 'scene' || kind === 'prop' || kind === 'storyboard_frame') && input.asset_type === kind && input.generation_type === 'all'), [assetId, kind, channel]);
    const refresh = useCallback(async (includeKept = true) => {
        const lease = lifecycle.current;
        if (!lease || !live(lease) || !environment || !projectId || !assetId || reading.current === lease) return;
        reading.current = lease;
        const order = ++exchanges.current.next;
        const known = new Map(visible.current.map(result => [result.journal.request.client_request_id, result.journal]));
        const observed = visible.current.filter(shouldObserve);
        patch({ reading: true, readFailed: false }, lease);
        try {
            const owner = { environment, rootKind: 'project' as const, rootId: projectId };
            const journals = listStudioAssetImageRequests(owner).filter(journal => matchesTarget(journal.request)).map(journal => {
                const expected = known.get(journal.request.client_request_id);
                return expected?.receiptIdentity ? expected : journal;
            });
            // A frame or derived-image panel with no journal has nothing to recover. Keep its existing legacy
            // generation button immediately available rather than adding an empty GET turn.
            if ((kind === 'storyboard_frame' || kind === 'character' && (channel === 'three_view' || channel === 'headshot' || channel === 'full_body'))
                && !journals.length && !observed.length) return;
            // Another tab can resolve a journal. Its absence proves no terminal result here.
            const retained = new Set(journals.map(journal => journal.request.client_request_id));
            const missing = new Set(observed.map(result => result.journal.request.client_request_id).filter(id => !retained.has(id)));
            const results = await Promise.all(journals.filter(journal => includeKept || !visible.current.some(result =>
                result.journal.request.client_request_id === journal.request.client_request_id && keptResult(result)))
                .map(journal => readStudioAssetImageRequestReceipt({ ...owner, journal })));
            await consume(results, lease, order, missing);
        } catch { await consume(observed.map(unknown), lease, order); patch({ readFailed: true }, lease); }
        finally { if (reading.current === lease) reading.current = null; patch({ reading: false }, lease); }
    }, [live, environment, projectId, assetId, kind, channel, patch, consume, matchesTarget, keptResult]);
    useEffect(() => {
        const lease = {};
        lifecycle.current = lease;
        frameRecovery.current = { lease, blocked: new Set(), kept: new Map() };
        void refresh(false);
        return () => { if (lifecycle.current === lease) lifecycle.current = null; };
    }, [refresh]);
    useEffect(() => {
        if (current.reading || current.submitting || !current.results.some(shouldObserve)) return;
        const timer = setTimeout(() => { void refresh(false); }, 2500);
        return () => clearTimeout(timer);
    }, [current.results, current.reading, current.submitting, refresh]);
    const canKeepResult = useCallback((result: StudioAssetImageReadResult) => blockedResult(result) && !!frameProof(result), [blockedResult, frameProof]);
    const keepResult = useCallback(async (result: StudioAssetImageReadResult) => {
        const lease = lifecycle.current;
        if (!lease || !live(lease) || !environment || !projectId || reading.current === lease || submitting.current === lease || !canKeepResult(result)) return;
        const proof = frameProof(result);
        if (!proof) return;
        reading.current = lease;
        const order = ++exchanges.current.next;
        patch({ reading: true }, lease);
        try {
            const received = await readStudioAssetImageRequestReceipt({ environment, rootKind: 'project', rootId: projectId, journal: result.journal });
            await consume([received], lease, order, new Set(), { key: result.journal.request.client_request_id, proof });
        } finally { if (reading.current === lease) reading.current = null; patch({ reading: false }, lease); }
    }, [live, environment, projectId, canKeepResult, frameProof, patch, consume]);
    const submit = useCallback(async (input: StudioAssetImageRequestInput, isCurrent?: () => boolean,
        onCreated?: (journal: StoredStudioAssetImageRequest) => void) => {
        const lease = lifecycle.current;
        if (!lease || !live(lease) || !environment || !projectId || submitting.current === lease) return;
        if (!matchesTarget(input)) { patch({ submitFailed: true }, lease); return; }
        if (kind === 'storyboard_frame' && visible.current.some(result => blockedResult(result)
            || result.status === 'unknown' || pending(result) || result.receipt?.request.members.some(member => member.status === 'needs_confirmation'))) {
            patch({}, lease); return;
        }
        submitting.current = lease;
        const order = ++exchanges.current.next;
        patch({ submitting: true, submitFailed: false }, lease);
        try { await consume([await submitStudioAssetImageRequest({ environment, rootKind: 'project', rootId: projectId, input,
            isCurrent: () => live(lease) && (!isCurrent || isCurrent()), onCreated })], lease, order); }
        catch { patch({ submitFailed: true }, lease); }
        finally { if (submitting.current === lease) submitting.current = null; patch({ submitting: false }, lease); }
    }, [live, environment, projectId, patch, consume, matchesTarget, kind, blockedResult]);
    return { ...current, refresh, submit, canKeepResult, keepResult, keptResult,
        readbackFailed: kind === 'storyboard_frame' ? current.results.some(blockedResult) : current.readbackFailed,
        busy: current.submitting || current.results.some(pending),
        unknown: current.results.some(result => result.status === 'unknown' || result.receipt?.request.members.some(m => m.status === 'needs_confirmation')) };
}
