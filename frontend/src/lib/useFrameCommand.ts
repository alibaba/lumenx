import { useCallback, useEffect, useRef } from 'react';
import { useProjectStore } from '@/store/projectStore';
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';

export type FrameCommandError = 'conflict' | 'editConflict' | 'unavailable' | 'unconfirmed' | 'pendingEdits' | 'failedEdits' | 'savedReadFailed' | 'submitUnconfirmed' | 'providerCancelUnconfirmed' | 'providerContinueUnavailable' | 'providerLockUnavailable' | 'providerRequestDeleted' | 'unsettled' | 'editorOpen';

export class FrameReadbackError extends Error {}
let editRequest = 0;
const activeEnvironmentKey = () => requireActiveRuntimeEnvironment().key;
const editPrefix = (projectId: string, environment = activeEnvironmentKey()) => `frame-edit:${encodeURIComponent(environment)}:${encodeURIComponent(projectId)}:`;
const editFailurePrefix = (projectId: string, environment = activeEnvironmentKey()) => `frame-edit-failed:${encodeURIComponent(environment)}:${encodeURIComponent(projectId)}:`;
export const frameRefiningKey = (projectId: string, environment = activeEnvironmentKey()) => `frame-refine:${encodeURIComponent(environment)}:${encodeURIComponent(projectId)}`;
const errorPrefix = (projectId: string, environment = activeEnvironmentKey()) => `frame-command-error:${encodeURIComponent(environment)}:${encodeURIComponent(projectId)}:`;

export function frameEditsPending(projectId: string): boolean {
    return Object.keys(useProjectStore.getState().runningOps).some(key => key.startsWith(editPrefix(projectId)));
}
export function frameEditFailure(projectId: string): 'savedReadFailed' | 'editConflict' | 'failedEdits' | null {
    const failures = Object.keys(useProjectStore.getState().runningOps).filter(key => key.startsWith(editFailurePrefix(projectId)));
    return failures.some(key => key.endsWith(':savedReadFailed')) ? 'savedReadFailed' : failures.some(key => key.endsWith(':editConflict')) ? 'editConflict' : failures.length ? 'failedEdits' : null;
}

/** Explicitly confirmed conflict recovery may clear only edit failure flags. */
export function clearFrameEditFailures(projectId: string) {
    const prefix = editFailurePrefix(projectId);
    useProjectStore.setState(state => ({ runningOps: Object.fromEntries(
        Object.entries(state.runningOps).filter(([key]) => !key.startsWith(prefix)),
    ) }));
}

/** Requests flushed by an old mount must still block the next mount's commands. */
export function trackFrameEdit<T>(projectId: string, request: Promise<T>): Promise<T> {
    const environment = requireActiveRuntimeEnvironment();
    const key = `${editPrefix(projectId, environment.key)}${++editRequest}`;
    const store = useProjectStore.getState();
    store.setRunningOp(key, true);
    return request.catch(cause => {
        const status = (cause as { response?: { status?: number } } | null)?.response?.status;
        const reason = cause instanceof FrameReadbackError ? 'savedReadFailed' : status === 409 ? 'editConflict' : 'failedEdits';
        if (isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
            store.setRunningOp(`${editFailurePrefix(projectId, environment.key)}${++editRequest}:${reason}`, true);
        }
        throw cause;
    }).finally(() => store.setRunningOp(key, false));
}

/** One structural write per project, including across step unmount/remount. */
export function useFrameCommand(projectId: string | undefined) {
    const environment = getRuntimeEnvironment();
    const environmentKey = environment?.key ?? '';
    const environmentGeneration = environment?.generation;
    const key = `frame-command:${encodeURIComponent(environmentKey)}:${projectId}`;
    const refineKey = projectId ? frameRefiningKey(projectId, environmentKey) : `frame-refine:${encodeURIComponent(environmentKey)}:`;
    const refining = useProjectStore(state => !!state.runningOps[refineKey]);
    const busy = useProjectStore(state => !!state.runningOps[key] || !!state.runningOps[refineKey]);
    const editsPending = useProjectStore(state => projectId
        ? Object.keys(state.runningOps).some(operation => operation.startsWith(editPrefix(projectId, environmentKey))) : false);
    const editsFailed = useProjectStore(state => projectId
        ? Object.keys(state.runningOps).some(operation => operation.startsWith(editFailurePrefix(projectId, environmentKey))) : false);
    const error = useProjectStore(state => {
        const prefix = projectId ? errorPrefix(projectId, environmentKey) : `frame-command-error:${encodeURIComponent(environmentKey)}:`;
        const entry = Object.keys(state.runningOps).find(operation => operation.startsWith(prefix));
        return entry?.slice(prefix.length) as FrameCommandError | undefined ?? null;
    });
    const setError = useCallback((value: FrameCommandError | null) => {
        if (!projectId || environmentGeneration === undefined
            || !isCurrentRuntimeEnvironment(environmentKey, environmentGeneration)) return;
        const prefix = errorPrefix(projectId, environmentKey);
        useProjectStore.setState(state => ({ runningOps: {
            ...Object.fromEntries(Object.entries(state.runningOps).filter(([operation]) => !operation.startsWith(prefix))),
            ...(value ? { [`${prefix}${value}`]: true } : {}),
        } }));
    }, [projectId, environmentKey, environmentGeneration]);
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);
    const isCurrent = () => mounted.current && useProjectStore.getState().currentProject?.id === projectId;
    const isBusy = useCallback(() => {
        const operations = useProjectStore.getState().runningOps;
        return !!operations[key] || !!operations[refineKey];
    }, [key, refineKey]);

    const run = async (command: () => Promise<unknown>) => {
        const operationEnvironment = getRuntimeEnvironment();
        if (!projectId || !operationEnvironment || operationEnvironment.key !== environmentKey
            || operationEnvironment.generation !== environmentGeneration || !isCurrent() || isBusy()) return null;
        const store = useProjectStore.getState();
        const failure = frameEditFailure(projectId);
        if (failure) { setError(failure); return null; }
        if (frameEditsPending(projectId)) { setError('pendingEdits'); return null; }
        store.setRunningOp(key, true);
        setError(null);
        try {
            const response = await command();
            if (!isCurrentRuntimeEnvironment(operationEnvironment.key, operationEnvironment.generation)) return null;
            store.applyFrameCommandSnapshot(projectId, response);
            return isCurrent() ? useProjectStore.getState().currentProject : null;
        } catch (cause: unknown) {
            if (!isCurrentRuntimeEnvironment(operationEnvironment.key, operationEnvironment.generation)) return null;
            const status = (cause as { response?: { status?: number } } | null)?.response?.status;
            // Outcome belongs to the project, not the component that sent it.
            setError(status === 409 ? 'conflict' : status === 503 ? 'unavailable' : 'unconfirmed');
            return null;
        } finally {
            // Ordinary remounts still share this operation; reconnects do not.
            if (isCurrentRuntimeEnvironment(operationEnvironment.key, operationEnvironment.generation)) {
                store.setRunningOp(key, false);
            }
        }
    };
    return { busy, refining, isBusy, editsPending, editsFailed, error, setError, run };
}
