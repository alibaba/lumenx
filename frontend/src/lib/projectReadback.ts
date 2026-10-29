import { api } from '@/lib/api';
import { frameCommandSnapshot, isReadConfirmed, projectReadSnapshot } from '@/lib/frameCommandSnapshot';
import { isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore } from '@/store/projectStore';

export class ProjectReadbackError extends Error {}

interface ProjectReadbackOptions {
    /** Reject a fetched project before it can mutate the store when the owning UI session changed. */
    isCurrent?: (snapshot: ReturnType<typeof projectReadSnapshot>) => boolean;
}

/** One read attempt. Rejections never recurse or resubmit the preceding write. */
export async function readProjectOnce(projectId: string, minimumProjectRevision?: number, options: ProjectReadbackOptions = {}) {
    const environment = requireActiveRuntimeEnvironment();
    try {
        const response = await api.getProject(projectId);
        if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
            throw new ProjectReadbackError('Project read is no longer current');
        }
        const parsed = projectReadSnapshot(projectId, response, environment.key);
        if (minimumProjectRevision !== undefined && parsed.revision! < minimumProjectRevision) {
            throw new ProjectReadbackError('Project read predates the committed command');
        }
        if (options.isCurrent && !options.isCurrent(parsed)) {
            throw new ProjectReadbackError('Project read is no longer current');
        }
        const result = useProjectStore.getState().applyProjectReadSnapshot(projectId, response);
        if (!isReadConfirmed(result)) throw new ProjectReadbackError('Project read could not confirm all source versions');
        const store = useProjectStore.getState();
        const snapshot = store.currentProject?.id === projectId ? store.currentProject : store.projects.find(project => project.id === projectId);
        if (!snapshot) throw new ProjectReadbackError('Project is no longer present');
        return snapshot;
    } catch (error) {
        if (error instanceof ProjectReadbackError) throw error;
        throw new ProjectReadbackError('Project state could not be read');
    }
}

/** A raw Script proves the command only. Keep the visible projection until an enriched read confirms it. */
export async function confirmRawProjectAck(projectId: string, response: unknown, options?: ProjectReadbackOptions) {
    const parsed = frameCommandSnapshot(projectId, response);
    await readProjectOnce(projectId, parsed.revision, options);
}

/** A malformed initial acknowledgement is unconfirmed, not a successful write. */
export async function confirmProjectAck(projectId: string, response: unknown) {
    const environment = requireActiveRuntimeEnvironment();
    const parsed = projectReadSnapshot(projectId, response, environment.key);
    const result = useProjectStore.getState().applyProjectReadSnapshot(projectId, response);
    if (isReadConfirmed(result)) return;
    try { await readProjectOnce(projectId, parsed.revision); }
    catch { throw new ProjectReadbackError('Committed project state could not be read'); }
}
