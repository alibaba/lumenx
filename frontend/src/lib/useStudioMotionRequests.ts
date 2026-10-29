'use client';

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { readProjectOnce } from '@/lib/projectReadback';
import type { Project } from '@/store/projectStore';
import { listStudioMotionRequests, readStudioMotionRequestReceipt, submitStudioMotionRequest, runStudioMotionRecovery, studioMotionRecoveryAction,
    studioMotionCompletionProof, studioMotionDefiniteFailure, studioMotionJournalCurrent, studioMotionKnownRejection,
    studioMotionProjectionMatches, studioMotionRetentionProof, resolveStudioMotionRequest,
    type PrepareStudioMotionRequest, type StoredStudioMotionRequest, type StudioMotionReadResult, type StudioMotionRequestOwner } from '@/lib/studioMotionRequest';

type Options = { open: boolean; projectId?: string; assetId: string; assetKind?: StudioMotionRequestOwner['assetKind']; source?: string; seriesId?: string | null;
    onCreated?: (journal: StoredStudioMotionRequest) => void;
    readback?: (result: StudioMotionReadResult, isCurrent: (snapshot: Project) => boolean) => Promise<Project>;
};
type View = { token: object; results: StudioMotionReadResult[]; reading: boolean; submitting: boolean; acting: boolean; readFailed: boolean; submitFailed: boolean; actionFailed: boolean };
const initial = (token: object): View => ({ token, results: [], reading: false, submitting: false, acting: false, readFailed: false, submitFailed: false, actionFailed: false });
const active = (result: StudioMotionReadResult) => result.status === 'confirmed' && !result.receipt?.reason
    && ['pending', 'processing'].includes(result.receipt?.task?.status ?? '');
const missing = (result: StudioMotionReadResult): StudioMotionReadResult => ({ status: 'unknown', journal: result.journal, reason: 'studio_motion_journal_changed' });

/** Transient view of original request journals; no second persisted task state. */
export function useStudioMotionRequests({ open, projectId, assetId, assetKind = 'character', source, seriesId, onCreated, readback }: Options) {
    const environment = useSyncExternalStore(subscribeRuntimeEnvironment, getRuntimeEnvironment, () => null);
    const token = useMemo(() => ({ open, projectId, assetId, assetKind, source, seriesId, environment }), [open, projectId, assetId, assetKind, source, seriesId, environment]);
    const owner = useMemo<StudioMotionRequestOwner | null>(() => environment && projectId && (source !== 'series' || seriesId) ? {
        environment, projectId, assetId, assetKind, assetOwner: source === 'global' ? { kind: 'global', id: 'library' }
            : source === 'series' ? { kind: 'series', id: seriesId! } : { kind: 'script', id: projectId },
    } : null, [environment, projectId, assetId, assetKind, source, seriesId]);
    const latest = useRef(token); latest.current = token;
    const callbacks = useRef({ onCreated, readback }); callbacks.current = { onCreated, readback };
    const lifecycle = useRef<object | null>(null);
    const reading = useRef<object | null>(null), submitting = useRef<object | null>(null);
    const acting = useRef<{ lease: object; operation: object } | null>(null);
    const exchanges = useRef({ next: 0, accepted: new Map<string, { lease: object; order: number }>() });
    const decisions = useRef({ lease: null as object | null, kept: new Map<string, string>(), resolved: new Set<string>() });
    const [view, setView] = useState<View>(() => initial(token));
    const current = view.token === token ? view : initial(token);
    const visible = useRef(current.results); visible.current = current.results;
    const live = useCallback((lease: object | null) => open && !!owner && !!lease && lifecycle.current === lease && latest.current === token
        && isCurrentRuntimeEnvironment(owner.environment.key, owner.environment.generation), [open, owner, token]);
    const patch = useCallback((change: Partial<View>, lease: object) => {
        if (live(lease)) setView(previous => ({ ...(previous.token === token ? previous : initial(token)), ...change }));
    }, [live, token]);
    const currentExchange = useCallback((key: string, order: number, lease: object) => {
        const accepted = exchanges.current.accepted.get(key);
        return live(lease) && (!accepted || accepted.lease !== lease || accepted.order <= order);
    }, [live]);
    const proof = useCallback((result: StudioMotionReadResult) => owner ? studioMotionRetentionProof(owner, result) : null, [owner]);
    const keptResult = useCallback((result: StudioMotionReadResult) => {
        const value = decisions.current, lease = lifecycle.current, key = result.journal.request.client_request_id;
        const expected = value.kept.get(key);
        return value.lease === lease && live(lease) && !!expected && expected === proof(result);
    }, [live, proof]);
    const resolvedResult = useCallback((result: StudioMotionReadResult) => decisions.current.lease === lifecycle.current
        && live(lifecycle.current) && decisions.current.resolved.has(result.journal.request.client_request_id), [live]);
    const releasedResult = useCallback((result: StudioMotionReadResult) => resolvedResult(result) || keptResult(result)
        || !!owner && studioMotionKnownRejection(owner, result), [resolvedResult, keptResult, owner]);
    const consume = useCallback(async (results: StudioMotionReadResult[], order: number, lease: object, keep?: { key: string; proof: string }) => {
        if (!live(lease) || !owner) return;
        const merged = new Map(visible.current.map(result => [result.journal.request.client_request_id, result]));
        const accepted: StudioMotionReadResult[] = [];
        for (const result of results) {
            const key = result.journal.request.client_request_id;
            if (!currentExchange(key, order, lease)) continue;
            exchanges.current.accepted.set(key, { lease, order });
            decisions.current.resolved.delete(key); merged.set(key, result); accepted.push(result);
        }
        visible.current = Array.from(merged.values()); patch({ results: visible.current }, lease);
        for (const result of accepted) {
            const key = result.journal.request.client_request_id;
            const isLatest = () => live(lease) && decisions.current.lease === lease
                && exchanges.current.accepted.get(key)?.lease === lease && exchanges.current.accepted.get(key)?.order === order;
            if (!isLatest()) continue;
            if (keptResult(result)) continue;
            decisions.current.kept.delete(key);
            if (keep?.key === key) {
                if (proof(result) === keep.proof) decisions.current.kept.set(key, keep.proof);
                continue; // An explicit history decision never refreshes/adopts a project.
            }
            if (studioMotionDefiniteFailure(result)) {
                if (resolveStudioMotionRequest({ ...owner, journal: result.journal, result, isCurrent: isLatest })) {
                    decisions.current.resolved.add(key);
                    exchanges.current.accepted.set(key, { lease, order: ++exchanges.current.next });
                }
                continue;
            }
            if (!studioMotionCompletionProof(result) || result.receipt?.reason) continue;
            const isCurrent = (snapshot: Project) => isLatest() && studioMotionJournalCurrent(owner, result.journal)
                && studioMotionProjectionMatches(result, snapshot);
            try {
                const snapshot = callbacks.current.readback
                    ? await callbacks.current.readback(result, isCurrent)
                    : await readProjectOnce(owner.projectId, undefined, { isCurrent });
                // A superseded GET can return a newer store snapshot than its response.
                if (isCurrent(snapshot) && resolveStudioMotionRequest({ ...owner, journal: result.journal, result,
                    project: snapshot, isCurrent: isLatest })) {
                    decisions.current.resolved.add(key);
                    exchanges.current.accepted.set(key, { lease, order: ++exchanges.current.next });
                }
            } catch { /* This exact result stays blocking, with draft and journal intact. */ }
        }
        patch({}, lease);
    }, [live, owner, currentExchange, patch, keptResult, proof]);
    const refresh = useCallback(async (includeKept = true) => {
        const lease = lifecycle.current;
        if (!lease || !owner || !live(lease) || reading.current === lease || acting.current?.lease === lease) return;
        reading.current = lease; patch({ reading: true, readFailed: false }, lease);
        const order = ++exchanges.current.next;
        try {
            const known = new Map(visible.current.map(result => [result.journal.request.client_request_id, result]));
            const journals = listStudioMotionRequests(owner);
            const ids = new Set(journals.map(journal => journal.request.client_request_id));
            const absent = visible.current.filter(result => !ids.has(result.journal.request.client_request_id) && !resolvedResult(result)).map(missing);
            const results = await Promise.all(journals.filter(journal => includeKept
                || !known.has(journal.request.client_request_id) || !keptResult(known.get(journal.request.client_request_id)!))
                .map(journal => readStudioMotionRequestReceipt({ ...owner, journal: known.get(journal.request.client_request_id)?.journal ?? journal,
                    isCurrent: () => currentExchange(journal.request.client_request_id, order, lease) })));
            await consume([...results, ...absent], order, lease);
        } catch { patch({ readFailed: true }, lease); }
        finally { if (reading.current === lease) reading.current = null; patch({ reading: false }, lease); }
    }, [owner, live, patch, currentExchange, consume, resolvedResult, keptResult]);
    useEffect(() => {
        const lease = {}; lifecycle.current = lease;
        decisions.current = { lease, kept: new Map(), resolved: new Set() };
        void refresh(false);
        return () => { if (lifecycle.current === lease) lifecycle.current = null; };
    }, [refresh]);
    useEffect(() => {
        if (current.reading || current.submitting || current.acting || !current.results.some(active)) return;
        const timer = setTimeout(() => { void refresh(false); }, 2500);
        return () => clearTimeout(timer);
    }, [current.results, current.reading, current.submitting, current.acting, refresh]);
    const recover = useCallback(async (result: StudioMotionReadResult) => {
        const lease = lifecycle.current, action = studioMotionRecoveryAction(result);
        if (!lease || !owner || !live(lease) || !action || releasedResult(result) || acting.current?.lease === lease
            || reading.current === lease || submitting.current === lease) return;
        const operation = {}; acting.current = { lease, operation };
        patch({ acting: true, actionFailed: false }, lease);
        const order = ++exchanges.current.next, key = result.journal.request.client_request_id;
        try {
            const received = await runStudioMotionRecovery({ ...owner, journal: result.journal, action,
                isCurrent: () => currentExchange(key, order, lease) });
            await consume([received], order, lease);
        } catch { patch({ actionFailed: true }, lease); }
        finally {
            if (acting.current?.operation === operation) {
                acting.current = null; patch({ acting: false }, lease);
            }
        }
    }, [owner, live, currentExchange, patch, consume, releasedResult]);
    const canKeepResult = useCallback((result: StudioMotionReadResult) => live(lifecycle.current)
        && !releasedResult(result) && !!proof(result), [live, releasedResult, proof]);
    const keepResult = useCallback(async (result: StudioMotionReadResult) => {
        const lease = lifecycle.current, expected = proof(result), key = result.journal.request.client_request_id;
        if (!lease || !owner || !live(lease) || !expected || !canKeepResult(result)
            || acting.current?.lease === lease || reading.current === lease || submitting.current === lease) return;
        const operation = {}; acting.current = { lease, operation };
        const order = ++exchanges.current.next; patch({ acting: true, actionFailed: false }, lease);
        try {
            const received = await readStudioMotionRequestReceipt({ ...owner, journal: result.journal,
                isCurrent: () => currentExchange(key, order, lease) });
            await consume([received], order, lease, { key, proof: expected });
        } catch { patch({ actionFailed: true }, lease); }
        finally {
            if (acting.current?.operation === operation) { acting.current = null; patch({ acting: false }, lease); }
        }
    }, [owner, live, proof, canKeepResult, currentExchange, patch, consume]);
    const submit = useCallback(async (prepare: PrepareStudioMotionRequest, isCurrent: () => boolean, onLegacy?: () => void) => {
        const lease = lifecycle.current;
        if (!lease || !owner || !live(lease) || submitting.current === lease || acting.current?.lease === lease
            || reading.current === lease || visible.current.some(result => !releasedResult(result))) return;
        submitting.current = lease; patch({ submitting: true, submitFailed: false }, lease);
        const order = ++exchanges.current.next;
        try {
            const result = await submitStudioMotionRequest({ ...owner, prepare, onLegacy, isCurrent: () => live(lease) && isCurrent(),
                onCreated: journal => { if (live(lease)) callbacks.current.onCreated?.(journal); },
                maySkipJournal: journal => {
                    const result = visible.current.find(item => item.journal.request.client_request_id === journal.request.client_request_id);
                    return live(lease) && !!result && studioMotionJournalCurrent(owner, result.journal)
                        && (keptResult(result) || studioMotionKnownRejection(owner, result));
                } });
            if (result) await consume([result], order, lease);
        } catch { patch({ submitFailed: true }, lease); }
        finally { if (submitting.current === lease) submitting.current = null; patch({ submitting: false }, lease); }
    }, [owner, live, patch, consume, releasedResult, keptResult]);
    return { ...current, owner, refresh, submit, recover, canKeepResult, keepResult, keptResult, releasedResult,
        blocked: current.readFailed || current.reading || current.results.some(result => !releasedResult(result)),
        busy: current.submitting || current.acting || current.results.some(active) };
}
