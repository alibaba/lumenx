import { environmentLocalStorageKey, type RuntimeEnvironment } from '@/lib/runtimeEnvironment';

export interface CreateSeriesEpisodeRequest {
    client_request_id: string;
    title: string;
    expected_revision: number;
}

export interface SeriesEpisodeCreationReceipt {
    schema_version: 1;
    series_id: string;
    client_request_id: string;
    request_fingerprint: string;
    episode_id: string;
    episode_number: number;
    title: string;
    created_at: number;
}

export interface SeriesEpisodeRequestOwner {
    environment: RuntimeEnvironment;
    seriesId: string;
}

const ID = /^[A-Za-z0-9._:-]{1,128}$/;
const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);

export class SeriesEpisodeJournalError extends Error {
    constructor(public readonly code: 'unavailable' | 'corrupt') {
        super(`series_episode_journal_${code}`);
    }
}

function storage(): Storage {
    try { return window.localStorage; } catch { throw new SeriesEpisodeJournalError('unavailable'); }
}

function key(owner: SeriesEpisodeRequestOwner): string {
    return environmentLocalStorageKey(`lumenx.seriesEpisodeRequest.v1:${encodeURIComponent(owner.seriesId)}`, owner.environment.key);
}

function parseRequest(value: unknown): Readonly<CreateSeriesEpisodeRequest> {
    if (!isObject(value) || Object.keys(value).sort().join(',') !== 'client_request_id,expected_revision,title'
        || typeof value.client_request_id !== 'string' || !ID.test(value.client_request_id)
        || typeof value.title !== 'string' || !value.title.trim() || value.title !== value.title.trim()
        || typeof value.expected_revision !== 'number' || !Number.isSafeInteger(value.expected_revision) || value.expected_revision < 0) {
        throw new SeriesEpisodeJournalError('corrupt');
    }
    return Object.freeze({ client_request_id: value.client_request_id, title: value.title, expected_revision: value.expected_revision });
}

export function readSeriesEpisodeRequest(owner: SeriesEpisodeRequestOwner): Readonly<CreateSeriesEpisodeRequest> | null {
    let raw: string | null;
    try { raw = storage().getItem(key(owner)); } catch { throw new SeriesEpisodeJournalError('unavailable'); }
    if (raw === null) return null;
    let record: unknown;
    try { record = JSON.parse(raw); } catch { throw new SeriesEpisodeJournalError('corrupt'); }
    if (!isObject(record) || record.schema_version !== 1 || record.environment_key !== owner.environment.key
        || record.series_id !== owner.seriesId
        || Object.keys(record).sort().join(',') !== 'environment_key,request,schema_version,series_id') {
        throw new SeriesEpisodeJournalError('corrupt');
    }
    return parseRequest(record.request);
}

export function beginSeriesEpisodeRequest(owner: SeriesEpisodeRequestOwner, title: string, expectedRevision: number): Readonly<CreateSeriesEpisodeRequest> {
    const pending = readSeriesEpisodeRequest(owner);
    if (pending) return pending;
    const suffix = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    const request = parseRequest({ client_request_id: `episode-${suffix}`, title: title.trim(), expected_revision: expectedRevision });
    const serialized = JSON.stringify({ schema_version: 1, environment_key: owner.environment.key, series_id: owner.seriesId, request });
    try {
        storage().setItem(key(owner), serialized);
        if (storage().getItem(key(owner)) !== serialized) throw new Error('Journal write was not retained');
    } catch { throw new SeriesEpisodeJournalError('unavailable'); }
    return request;
}

export function resolveSeriesEpisodeRequest(owner: SeriesEpisodeRequestOwner, requestId: string): void {
    const pending = readSeriesEpisodeRequest(owner);
    if (!pending || pending.client_request_id !== requestId) return;
    try {
        storage().removeItem(key(owner));
        if (storage().getItem(key(owner)) !== null) throw new Error('Journal removal was not retained');
    } catch { throw new SeriesEpisodeJournalError('unavailable'); }
}

export function parseSeriesEpisodeReceipt(value: unknown, seriesId: string, requestId: string): SeriesEpisodeCreationReceipt {
    if (!isObject(value) || value.schema_version !== 1 || value.series_id !== seriesId || value.client_request_id !== requestId
        || typeof value.request_fingerprint !== 'string' || !/^[0-9a-f]{64}$/.test(value.request_fingerprint)
        || typeof value.episode_id !== 'string' || !value.episode_id
        || typeof value.episode_number !== 'number' || !Number.isSafeInteger(value.episode_number) || value.episode_number < 1
        || typeof value.title !== 'string' || !value.title.trim()
        || typeof value.created_at !== 'number' || !Number.isFinite(value.created_at) || value.created_at < 0) {
        throw new Error('Invalid series episode creation receipt');
    }
    return {
        schema_version: 1, series_id: seriesId, client_request_id: requestId,
        request_fingerprint: value.request_fingerprint, episode_id: value.episode_id,
        episode_number: value.episode_number, title: value.title, created_at: value.created_at,
    };
}
