import { afterEach, describe, expect, it } from 'vitest';
import { publicAssetPath } from '@/lib/publicAsset';

const originalBasePath = process.env.NEXT_PUBLIC_APP_BASE_PATH;

afterEach(() => {
    if (originalBasePath === undefined) delete process.env.NEXT_PUBLIC_APP_BASE_PATH;
    else process.env.NEXT_PUBLIC_APP_BASE_PATH = originalBasePath;
});

describe('publicAssetPath', () => {
    it('keeps root public asset paths unchanged without an app base path', () => {
        delete process.env.NEXT_PUBLIC_APP_BASE_PATH;

        expect(publicAssetPath('/logo-dark.png')).toBe('/logo-dark.png');
        expect(publicAssetPath('logo-light.png')).toBe('/logo-light.png');
    });

    it('prefixes public assets with NEXT_PUBLIC_APP_BASE_PATH once', () => {
        process.env.NEXT_PUBLIC_APP_BASE_PATH = '/static';

        expect(publicAssetPath('/logo-dark.png')).toBe('/static/logo-dark.png');
        expect(publicAssetPath('logo-light.png')).toBe('/static/logo-light.png');
        expect(publicAssetPath('/static/logo-dark.png')).toBe('/static/logo-dark.png');
    });

    it('does not rewrite absolute or embedded media URLs', () => {
        process.env.NEXT_PUBLIC_APP_BASE_PATH = '/static';

        expect(publicAssetPath('https://cdn.example.com/logo.png')).toBe('https://cdn.example.com/logo.png');
        expect(publicAssetPath('http://localhost:17177/files/logo.png')).toBe('http://localhost:17177/files/logo.png');
        expect(publicAssetPath('blob:https://app.example.com/id')).toBe('blob:https://app.example.com/id');
        expect(publicAssetPath('data:image/png;base64,abc')).toBe('data:image/png;base64,abc');
    });
});
