// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { listStudioAssetImageRequests, type StudioAssetImageRequestOwner } from '@/lib/studioAssetImageRequest';

afterEach(() => clearRuntimeEnvironment());

it('normalizes a throwing owner storage getter before reading an image journal', () => {
    const options: StudioAssetImageRequestOwner = {
        environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' }),
        rootKind: 'project',
        rootId: 'storage-getter-contract',
        get storage(): Storage { throw new Error('fixture_storage_access_denied'); },
    };

    expect(() => listStudioAssetImageRequests(options)).toThrowError(/^studio_image_storage_unavailable$/);
});
