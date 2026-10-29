export type MergeFreshnessState = 'current' | 'stale' | 'unknown';

export interface MergeVideoFreshness {
    state: MergeFreshnessState;
    reason: string;
    project_revision: number;
}

export interface MergeFreshnessProject {
    revision?: unknown;
    readEnvironment?: unknown;
    merged_video_url?: unknown;
    merged_video_freshness?: unknown;
}

export type MergeVideoDisplayState =
    | { kind: 'missing'; url: null; freshness: null }
    | { kind: 'current' | 'stale' | 'unknown'; url: string; freshness: MergeVideoFreshness | null };

function safeRevision(value: unknown): value is number {
    return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

export function normalizeMergeFreshness(value: unknown, fallbackRevision?: number): MergeVideoFreshness | undefined {
    if (value === undefined || value === null) return undefined;
    if (typeof value !== 'object') {
        return safeRevision(fallbackRevision) ? { state: 'unknown', reason: 'invalid_proof', project_revision: fallbackRevision } : undefined;
    }
    const raw = value as { state?: unknown; reason?: unknown; project_revision?: unknown };
    const state = raw.state;
    if (state !== 'current' && state !== 'stale' && state !== 'unknown') {
        return safeRevision(fallbackRevision) ? { state: 'unknown', reason: 'invalid_proof', project_revision: fallbackRevision } : undefined;
    }
    if (typeof raw.reason !== 'string' || !raw.reason.trim() || !safeRevision(raw.project_revision)) {
        return safeRevision(fallbackRevision) ? { state: 'unknown', reason: 'invalid_proof', project_revision: fallbackRevision } : undefined;
    }
    return { state, reason: raw.reason, project_revision: raw.project_revision };
}

export function sanitizeProjectMergeFreshness<T extends MergeFreshnessProject>(project: T): T {
    if (!Object.prototype.hasOwnProperty.call(project, 'merged_video_freshness')) return project;
    const normalized = normalizeMergeFreshness(project.merged_video_freshness, safeRevision(project.revision) ? project.revision : undefined);
    if (normalized === project.merged_video_freshness) return project;
    return { ...project, merged_video_freshness: normalized };
}

export function withoutMergeFreshness<T extends MergeFreshnessProject>(project: T): T {
    if (!Object.prototype.hasOwnProperty.call(project, 'merged_video_freshness')) return project;
    const rest = { ...project };
    delete rest.merged_video_freshness;
    return rest;
}

export function mergeVideoDisplayState(project: MergeFreshnessProject | null | undefined, expectedEnvironment: string): MergeVideoDisplayState {
    const url = typeof project?.merged_video_url === 'string' && project.merged_video_url.trim()
        ? project.merged_video_url
        : null;
    if (!url) return { kind: 'missing', url: null, freshness: null };

    const revision = project?.revision;
    const proof = normalizeMergeFreshness(project?.merged_video_freshness, safeRevision(revision) ? revision : undefined);
    if (project?.readEnvironment !== expectedEnvironment || !safeRevision(revision) || !proof || proof.project_revision !== revision) {
        return { kind: 'unknown', url, freshness: proof ?? null };
    }
    if (proof.state === 'current') return { kind: 'current', url, freshness: proof };
    if (proof.state === 'stale') return { kind: 'stale', url, freshness: proof };
    return { kind: 'unknown', url, freshness: proof };
}

export function isMergeVideoCurrent(project: MergeFreshnessProject | null | undefined, expectedEnvironment: string): boolean {
    return mergeVideoDisplayState(project, expectedEnvironment).kind === 'current';
}
