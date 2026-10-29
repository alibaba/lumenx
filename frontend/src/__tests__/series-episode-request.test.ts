// @vitest-environment happy-dom
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import {
    beginSeriesEpisodeRequest, readSeriesEpisodeRequest, resolveSeriesEpisodeRequest,
    parseSeriesEpisodeReceipt, SeriesEpisodeJournalError,
} from '@/lib/seriesEpisodeRequest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';

const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';
const owner = (storageId = A, seriesId = 'series-1') => ({
    environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId }), seriesId,
});

describe('series episode request recovery journal', () => {
    beforeEach(() => { localStorage.clear(); clearRuntimeEnvironment(); });
    afterEach(() => { vi.restoreAllMocks(); clearRuntimeEnvironment(); });

    it('restores the same frozen payload while an earlier creation remains unresolved', () => {
        const first = beginSeriesEpisodeRequest(owner(), ' 第一集 ', 4);
        const restored = readSeriesEpisodeRequest(owner());
        expect(restored).toEqual(first);
        expect(Object.isFrozen(restored)).toBe(true);
        expect(first.title).toBe('第一集');
        expect(beginSeriesEpisodeRequest(owner(), '另一标题', 8)).toEqual(first);
    });

    it('keeps requests separate by series and runtime storage identity', () => {
        const original = beginSeriesEpisodeRequest(owner(), '第一集', 4);
        expect(readSeriesEpisodeRequest(owner(A, 'another-series'))).toBeNull();
        expect(readSeriesEpisodeRequest(owner(B))).toBeNull();
        expect(readSeriesEpisodeRequest(owner(A))).toEqual(original);
    });

    it('cannot clear another request when a late acknowledgement resolves', () => {
        const current = owner();
        const first = beginSeriesEpisodeRequest(current, '第一集', 4);
        resolveSeriesEpisodeRequest(current, 'different-request');
        expect(readSeriesEpisodeRequest(current)).toEqual(first);
        resolveSeriesEpisodeRequest(current, first.client_request_id);
        expect(readSeriesEpisodeRequest(current)).toBeNull();
    });

    it.each(['broken JSON', 'wrong owner', 'changed payload'])('preserves %s instead of starting a new identity', variant => {
        const current = owner();
        beginSeriesEpisodeRequest(current, '第一集', 4);
        const key = localStorage.key(0)!;
        const record = JSON.parse(localStorage.getItem(key)!);
        if (variant === 'wrong owner') record.series_id = 'another-series';
        if (variant === 'changed payload') record.request.expected_revision = true;
        const raw = variant === 'broken JSON' ? '{unfinished' : JSON.stringify(record);
        localStorage.setItem(key, raw);
        expect(() => beginSeriesEpisodeRequest(current, '第二集', 5)).toThrow(SeriesEpisodeJournalError);
        expect(localStorage.getItem(key)).toBe(raw);
    });

    it('refuses to acknowledge cleanup when browser storage ignores removal', () => {
        const current = owner();
        const request = beginSeriesEpisodeRequest(current, '第一集', 4);
        vi.spyOn(localStorage, 'removeItem').mockImplementation(() => {});
        expect(() => resolveSeriesEpisodeRequest(current, request.client_request_id)).toThrow(SeriesEpisodeJournalError);
        expect(readSeriesEpisodeRequest(current)).toEqual(request);
    });

    it.each([
        { series_id: 'another-series' }, { client_request_id: 'another-request' }, { request_fingerprint: 'invalid' },
        { episode_number: 0 }, { created_at: Infinity },
    ])('rejects a receipt outside the request contract: %o', changed => {
        const receipt = {
            schema_version: 1, series_id: 'series-1', client_request_id: 'request-1',
            request_fingerprint: 'a'.repeat(64), episode_id: 'episode-1', episode_number: 1,
            title: '第一集', created_at: 1, ...changed,
        };
        expect(() => parseSeriesEpisodeReceipt(receipt, 'series-1', 'request-1')).toThrow('Invalid series episode creation receipt');
    });
});
