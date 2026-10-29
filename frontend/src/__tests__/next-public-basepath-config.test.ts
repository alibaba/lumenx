import { afterEach, describe, expect, it, vi } from 'vitest';

type NextConfigShape = {
    basePath?: string;
    assetPrefix?: string;
    env?: Record<string, string>;
    distDir?: string;
};

const originalEnv = {
    NODE_ENV: process.env.NODE_ENV,
    DOCKER_BUILD: process.env.DOCKER_BUILD,
    TAURI_BUILD: process.env.TAURI_BUILD,
};

function restoreEnv() {
    for (const [key, value] of Object.entries(originalEnv)) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
    }
}

async function loadNextConfig(env: Record<string, string | undefined>): Promise<NextConfigShape> {
    restoreEnv();
    for (const [key, value] of Object.entries(env)) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
    }
    vi.resetModules();
    const mod = await import(/* @vite-ignore */ `../../next.config.mjs?case=${Date.now()}-${Math.random()}`);
    return mod.default as NextConfigShape;
}

afterEach(() => {
    restoreEnv();
    vi.resetModules();
});

describe('next public app base path config', () => {
    it('does not inject a base path in development', async () => {
        const config = await loadNextConfig({ NODE_ENV: 'development', DOCKER_BUILD: undefined, TAURI_BUILD: undefined });

        expect(config.basePath).toBeUndefined();
        expect(config.assetPrefix).toBeUndefined();
        expect(config.env?.NEXT_PUBLIC_APP_BASE_PATH).toBe('');
    });

    it('injects /static for the default production desktop export', async () => {
        const config = await loadNextConfig({ NODE_ENV: 'production', DOCKER_BUILD: undefined, TAURI_BUILD: undefined });

        expect(config.distDir).toBe('../static');
        expect(config.basePath).toBe('/static');
        expect(config.assetPrefix).toBe('/static');
        expect(config.env?.NEXT_PUBLIC_APP_BASE_PATH).toBe('/static');
    });

    it('keeps Docker and Tauri production exports rooted', async () => {
        const dockerConfig = await loadNextConfig({ NODE_ENV: 'production', DOCKER_BUILD: 'true', TAURI_BUILD: undefined });
        const tauriConfig = await loadNextConfig({ NODE_ENV: 'production', DOCKER_BUILD: undefined, TAURI_BUILD: 'true' });

        expect(dockerConfig.basePath).toBeUndefined();
        expect(dockerConfig.assetPrefix).toBeUndefined();
        expect(dockerConfig.env?.NEXT_PUBLIC_APP_BASE_PATH).toBe('');
        expect(tauriConfig.basePath).toBeUndefined();
        expect(tauriConfig.assetPrefix).toBeUndefined();
        expect(tauriConfig.env?.NEXT_PUBLIC_APP_BASE_PATH).toBe('');
    });
});
