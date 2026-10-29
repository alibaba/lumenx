import { isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment, type RuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { frameCommandSnapshot } from '@/lib/frameCommandSnapshot';
import { frameEditFailure } from '@/lib/useFrameCommand';

interface PendingEdit {
    payload: Record<string, unknown>;
    send: (payload: Record<string, unknown>) => Promise<unknown>;
    waiters: { resolve: (value: unknown) => void; reject: (cause: unknown) => void }[];
}
interface Lane { pending: Map<string, PendingEdit> }
const lanes = new Map<string, Lane>();

export interface FrameEditRevision {
    revision: number;
    projectId: string;
    environment: RuntimeEnvironment;
}
const drafts = new Set<FrameEditRevision>();
export function captureFrameEditRevision(projectId: string, revision: number): FrameEditRevision {
    const draft = { projectId, revision, environment: requireActiveRuntimeEnvironment() };
    drafts.add(draft);
    return draft;
}
export function releaseFrameEditRevision(draft: FrameEditRevision) { drafts.delete(draft); }

/** Only our validated CAS acknowledgements advance already-open drafts. A GET
 * or another session's store update cannot authorize overwriting an old draft. */
export async function sendFrameRevisionEdit(draft: FrameEditRevision, send: (revision: number) => Promise<unknown>) {
    const { environment, projectId, revision } = draft;
    if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) throw new Error('Frame edit environment changed');
    const response = await send(revision);
    const confirmed = frameCommandSnapshot(projectId, response);
    if (confirmed.revision !== revision && confirmed.revision !== revision + 1) throw new Error('Invalid frame edit revision acknowledgement');
    if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) throw new Error('Frame edit environment changed');
    for (const pending of Array.from(drafts)) {
        if (pending.projectId === projectId && pending.environment.key === environment.key
            && pending.environment.generation === environment.generation && pending.revision === revision) pending.revision = confirmed.revision;
    }
    return response;
}

/** Timers decide when to save. Ready writes share a project lane because CAS
 * versions belong to the project; pending writes still coalesce by editor. */
export function enqueueFrameEdit<T extends Record<string, unknown>>(
    projectId: string, laneId: string, payload: T, send: (patch: T) => Promise<unknown>,
): Promise<unknown> {
    const environment = requireActiveRuntimeEnvironment();
    const failure = frameEditFailure(projectId);
    if (failure) return Promise.reject(new Error(failure));
    const key = JSON.stringify([environment.key, environment.generation, projectId]);
    const existing = lanes.get(key);
    return new Promise((resolve, reject) => {
        const previous = existing?.pending.get(laneId);
        const pending: PendingEdit = {
            payload: { ...previous?.payload, ...payload },
            send: patch => send(patch as T),
            waiters: [...(previous?.waiters ?? []), { resolve, reject }],
        };
        if (existing) { existing.pending.set(laneId, pending); return; }
        const lane: Lane = { pending: new Map() };
        lanes.set(key, lane);
        const drain = async (first: PendingEdit) => {
            let current: PendingEdit | undefined = first;
            try {
                while (current) {
                    const response = await current.send(current.payload);
                    if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
                        throw new Error('Runtime environment changed during frame edit');
                    }
                    current.waiters.forEach(waiter => waiter.resolve(response));
                    const next = lane.pending.entries().next().value as [string, PendingEdit] | undefined;
                    current = next?.[1];
                    if (next) lane.pending.delete(next[0]);
                }
            } catch (cause) {
                current?.waiters.forEach(waiter => waiter.reject(cause));
                for (const entry of Array.from(lane.pending.values())) entry.waiters.forEach(waiter => waiter.reject(cause));
            } finally {
                lanes.delete(key);
            }
        };
        void drain(pending);
    });
}
