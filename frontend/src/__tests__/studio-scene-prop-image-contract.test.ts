import { describe, expect, it } from 'vitest';
import { validateStudioAssetImageReceipt } from '@/lib/studioAssetImageContract';

// Synthetic public-contract cases; actual backend wire parity belongs to L2c.
const wire = (kind: 'scene' | 'prop') => ({
    schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: true,
    request: {
        id: '11111111-1111-4111-8111-111111111111', entrypoint: 'studio.asset.scene_prop.v1',
        request_contract_version: 'request-json-v1', client_request_id: 'image-key', request_fingerprint: 'a'.repeat(64),
        request_root_kind: 'project', request_root_id: 'project', request_root_scope_id: '22222222-2222-4222-8222-222222222222',
        request_root_series_id: null as string | null, owner_kind: 'script', owner_id: 'project',
        asset_type: kind, asset_id: 'shared/id', channel: 'image',
        members: [
            { variant_id: 'first', attempt_id: 'first', status: 'completed', output_ref: 'assets/studio/first.png' },
            { variant_id: 'second', attempt_id: 'second', status: 'needs_confirmation', reason: 'unknown' },
            { variant_id: 'third', attempt_id: 'third', status: 'cancelled' },
        ],
    },
});

describe.each(['scene', 'prop'] as const)('closed %s image receipts', kind => {
    it.each([
        ['project', 'project', null, 'script', 'project'],
        ['project', 'project', 'series', 'series', 'series'],
        ['project', 'project', null, 'global', 'library'],
        ['series', 'series', null, 'series', 'series'],
    ])('preserves the original %s root and %s owner relationship', (rootKind, rootId, seriesId, ownerKind, ownerId) => {
        const value = wire(kind);
        Object.assign(value.request, { request_root_kind: rootKind, request_root_id: rootId,
            request_root_series_id: seriesId, owner_kind: ownerKind, owner_id: ownerId });
        const parsed = validateStudioAssetImageReceipt(value);
        expect(parsed).toEqual(value);
        expect(parsed.request.members.map(m => m.status)).toEqual(['completed', 'needs_confirmation', 'cancelled']);
        expect(parsed.request.members[1]).not.toHaveProperty('output_ref');
        value.request.members[0].status = 'pending';
        expect(parsed.request.members[0].status).toBe('completed');
    });

    it.each([
        { entrypoint: 'studio.asset.reference_sheet.v1' },
        { channel: 'reference_sheet' }, { asset_type: 'character' },
        { entrypoint: 'studio.asset.future.v1' }, { asset_type: 'frame' }, { channel: 'all' },
        { owner_kind: 'global', owner_id: 'not-library' },
        { owner_kind: 'series', owner_id: 'series', request_root_series_id: 'another' },
    ])('rejects a broadened tuple or foreign owner %j', changes => {
        const value = wire(kind); Object.assign(value.request, changes);
        expect(() => validateStudioAssetImageReceipt(value)).toThrow('studio_image_receipt_invalid');
    });

    it.each(['top', 'request', 'member'] as const)('rejects execution authority at %s', level => {
        const value = wire(kind);
        const target = level === 'top' ? value : level === 'request' ? value.request : value.request.members[0];
        Object.assign(target, { credential_ref: 'not-public' });
        expect(() => validateStudioAssetImageReceipt(value)).toThrow('studio_image_receipt_invalid');
    });

    it.each(['output', 'unknown-output', 'duplicate'] as const)('keeps strict member validation for %s', damage => {
        const value = wire(kind);
        if (damage === 'output') value.request.members[0].output_ref = 'assets/studio/another.png';
        if (damage === 'unknown-output') Object.assign(value.request.members[1], { output_ref: 'assets/studio/second.png' });
        if (damage === 'duplicate') value.request.members.push({ ...value.request.members[0] });
        expect(() => validateStudioAssetImageReceipt(value)).toThrow('studio_image_receipt_invalid');
    });
});
