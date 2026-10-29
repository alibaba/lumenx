import type { Character, Project, Prop, Scene } from '@/store/projectStore';
import { sanitizeProjectMergeFreshness } from '@/lib/mergeFreshness';

export interface ReadVersions {
    project: number;
    // Missing means unknown in a composed client view; null means no parent.
    series?: { id: string; revision: number | null } | null;
    library?: number;
}
export interface SourceWatermark { library?: number; series: Record<string, number>; project?: Record<string, number> }
export type ReadWatermarks = Record<string, SourceWatermark>;
export interface SnapshotApplyResult {
    scope: 'read';
    status: 'applied' | 'superseded' | 'unconfirmed' | 'rejected';
    reason?: 'unknown_sources' | 'incomparable' | 'projection_mismatch';
    current: boolean;
    cached: boolean;
}
/** Confirms only fields owned by the project command, never shared freshness. */
export interface FrameCommandApplyResult {
    scope: 'project';
    status: 'applied' | 'superseded';
    current: boolean;
    cached: boolean;
}
const version = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;

export function validateReadVersions(raw: Pick<RawProject, 'revision' | 'series_id' | 'read_versions'>): ReadVersions | undefined {
    if (!Object.prototype.hasOwnProperty.call(raw, 'read_versions')) return undefined;
    const value = raw.read_versions;
    if (!value || typeof value !== 'object' || value.project !== raw.revision || !version(value.library)
        || !Object.prototype.hasOwnProperty.call(value, 'series')) throw new Error('Invalid project read versions');
    const series = value.series;
    if (raw.series_id != null && (typeof raw.series_id !== 'string' || !raw.series_id.trim())) throw new Error('Invalid project source identity');
    if (raw.series_id ? !series || typeof series.id !== 'string' || !series.id.trim() || series.id !== raw.series_id || !(series.revision === null || version(series.revision))
        : series !== null) throw new Error('Invalid project source identity');
    return { project: value.project, series: series ? { id: series.id, revision: series.revision } : null, library: value.library };
}

type RawProject = Omit<Project, 'originalText' | 'createdAt' | 'updatedAt'> & {
    original_text: string;
    created_at: number;
    updated_at: number;
};

function readRawProject(id: string, value: unknown): RawProject {
    const raw = value as Partial<RawProject> | null;
    if (!raw || raw.id !== id || typeof raw.revision !== 'number' || !Number.isSafeInteger(raw.revision) || raw.revision < 0
        || typeof raw.original_text !== 'string' || typeof raw.title !== 'string'
        || (raw.series_id != null && (typeof raw.series_id !== 'string' || !raw.series_id.trim()))
        || !Array.isArray(raw.frames) || !Array.isArray(raw.characters) || !Array.isArray(raw.scenes)
        || !Array.isArray(raw.props) || !Array.isArray(raw.video_tasks)
        || !raw.frames.every((frame: unknown) => typeof frame === 'object' && frame !== null && 'id' in frame && typeof frame.id === 'string')
        || new Set(raw.frames.map((frame: { id: string }) => frame.id)).size !== raw.frames.length
        || typeof raw.created_at !== 'number' || !Number.isFinite(raw.created_at)
        || typeof raw.updated_at !== 'number' || !Number.isFinite(raw.updated_at)) {
        throw new Error('Invalid committed project snapshot');
    }
    return raw as RawProject;
}

function normalize(raw: RawProject): Project {
    return sanitizeProjectMergeFreshness({ ...raw, originalText: raw.original_text,
        createdAt: new Date(raw.created_at * 1000).toISOString(),
        updatedAt: new Date(raw.updated_at * 1000).toISOString(), status: raw.status ?? 'draft' });
}

/** GET already carries authoritative asset sources; never relabel them. */
export function projectReadSnapshot(id: string, value: unknown, environment?: string): Project {
    const raw = readRawProject(id, value);
    const read_versions = validateReadVersions(raw);
    return { ...normalize(raw), read_versions, readEnvironment: environment,
        sharedAssetsNeedRefresh: !read_versions };
}

/** Frame commands return a raw Script, while GET includes shared assets. */
export function frameCommandSnapshot(id: string, value: unknown, previous?: Project, environment?: string): Project {
    const raw = readRawProject(id, value);
    previous = previous?.readEnvironment === environment ? previous : undefined;
    const sameSeries = (raw.series_id ?? null) === (previous?.series_id ?? null);
    const assets = <T extends Character | Scene | Prop>(localAssets: T[], previousAssets: T[] = []): T[] => {
        const local = localAssets.map(item => ({ ...item, source: 'episode' as const }));
        const ids = new Set(local.map(item => item.id));
        return [...local, ...previousAssets.filter(item => {
            if ((item.source !== 'global' && !(item.source === 'series' && sameSeries)) || ids.has(item.id)) return false;
            ids.add(item.id);
            return true;
        })];
    };
    const trusted = previous?.readEnvironment === environment && environment !== undefined;
    const previousVersions = trusted ? previous?.read_versions : undefined;
    const read_versions = previousVersions ? { project: raw.revision, library: previousVersions.library,
        ...(sameSeries ? { series: previousVersions.series } : !raw.series_id ? { series: null } : {}) } : undefined;
    const removedOverride = (local: { id: string }[], before: { id: string; source?: string }[] = []) =>
        before.some(item => item.source === 'episode' && !local.some(next => next.id === item.id));
    return { ...normalize(raw), read_versions, readEnvironment: environment,
        sharedAssetsNeedRefresh: !read_versions || !sameSeries || !!previous?.sharedAssetsNeedRefresh
            || removedOverride(raw.characters, previous?.characters) || removedOverride(raw.scenes, previous?.scenes)
            || removedOverride(raw.props, previous?.props),
        characters: assets(raw.characters, previous?.characters), scenes: assets(raw.scenes, previous?.scenes),
        props: assets(raw.props, previous?.props) };
}

export function acceptsProjectRevision(previous: Project | null | undefined, revision: number): boolean {
    return !previous || !Number.isSafeInteger(previous.revision) || previous.revision <= revision;
}

/** Only verified metadata from this environment may seed the independent floor. */
function cloneFloor(old?: SourceWatermark): SourceWatermark {
    const series: Record<string, number> = Object.create(null);
    for (const [id, revision] of Object.entries(old?.series ?? {})) {
        if (id.trim() && version(revision)) series[id] = revision;
    }
    const project: Record<string, number> = Object.create(null);
    for (const [id, revision] of Object.entries(old?.project ?? {})) {
        if (id.trim() && version(revision)) project[id] = revision;
    }
    return { library: version(old?.library) ? old.library : undefined, series, project };
}

export function observeProjectRevision(watermarks: ReadWatermarks, environment: string, projectId: string, revision: number): ReadWatermarks {
    if (!projectId.trim() || !version(revision)) throw new Error('Invalid project revision floor');
    const floor = cloneFloor(watermarks[environment]);
    floor.project![projectId] = Math.max(floor.project![projectId] ?? 0, revision);
    return { ...watermarks, [environment]: floor };
}

/** Only verified metadata from this environment may seed the independent floor. */
export function observeReadVersions(watermarks: ReadWatermarks, projects: Project[], environment: string): ReadWatermarks {
    const floor = cloneFloor(watermarks[environment]);
    for (const project of projects) {
        const versions = project.read_versions;
        if (project.readEnvironment !== environment || !versions || !version(project.revision) || versions.project !== project.revision) continue;
        if (version(versions.library)) floor.library = Math.max(floor.library ?? 0, versions.library);
        const series = versions.series;
        if (series && typeof series.id === 'string' && series.id.trim() && series.id === project.series_id && version(series.revision)) {
            floor.series[series.id] = Math.max(floor.series[series.id] ?? 0, series.revision);
        }
    }
    return { ...watermarks, [environment]: floor };
}

export function sourcesMeetFloor(project: Project, floor: SourceWatermark, environment: string): boolean {
    const projectFloor = floor.project?.[project.id];
    if (version(projectFloor) && project.revision < projectFloor) return false;
    const versions = project.readEnvironment === environment ? project.read_versions : undefined;
    if (!versions || versions.project !== project.revision || !version(versions.library)
        || versions.library < (floor.library ?? 0)) return false;
    if (!project.series_id) return versions.series === null;
    const series = versions.series;
    if (!series || series.id !== project.series_id) return false;
    const known = floor.series[series.id];
    return series.revision === null ? known === undefined : version(series.revision) && series.revision >= (known ?? 0);
}

export function markSharedReadStale(project: Project, floor: SourceWatermark, environment: string): Project {
    return !sourcesMeetFloor(project, floor, environment) && !project.sharedAssetsNeedRefresh
        ? { ...project, sharedAssetsNeedRefresh: true } : project;
}

export function canApplyRead(previous: Project | undefined | null, next: Project, floor: SourceWatermark, environment: string): boolean {
    previous = previous?.readEnvironment === environment ? previous : undefined;
    const projectFloor = floor.project?.[next.id];
    if (version(projectFloor) && next.revision < projectFloor) return false;
    if (!acceptsProjectRevision(previous, next.revision)) return false;
    if (previous?.revision === next.revision && (previous.series_id ?? null) !== (next.series_id ?? null)) return false;
    const priorSeries = previous?.read_versions?.series;
    const nextSeries = next.read_versions?.series;
    if (priorSeries && nextSeries && priorSeries.id === nextSeries.id
        && (priorSeries.revision === null) !== (nextSeries.revision === null)) return false;
    if (!next.read_versions) return floor.library === undefined && (!next.series_id || floor.series[next.series_id] === undefined);
    return sourcesMeetFloor(next, floor, environment);
}

export function isReadConfirmed(result: SnapshotApplyResult): boolean {
    return result.status === 'applied' || result.status === 'superseded';
}

export function readDominates(previous: Project, next: Project, floor: SourceWatermark, environment: string): boolean {
    if (previous.sharedAssetsNeedRefresh || !sourcesMeetFloor(previous, floor, environment) || !next.read_versions
        || previous.revision < next.revision || (previous.series_id ?? null) !== (next.series_id ?? null)) return false;
    const before = previous.read_versions!; const incoming = next.read_versions;
    if (!version(incoming.library) || before.library! < incoming.library) return false;
    if (!next.series_id) return before.series === null && incoming.series === null;
    const a = before.series; const b = incoming.series;
    return !!a && !!b && a.id === b.id && (a.revision === null || b.revision === null
        ? a.revision === b.revision : a.revision >= b.revision);
}

export function sameReadVersion(a: Project, b: Project): boolean {
    return a.readEnvironment === b.readEnvironment && a.revision === b.revision
        && (a.series_id ?? null) === (b.series_id ?? null)
        && a.read_versions?.library === b.read_versions?.library
        && a.read_versions?.series?.id === b.read_versions?.series?.id
        && a.read_versions?.series?.revision === b.read_versions?.series?.revision;
}
