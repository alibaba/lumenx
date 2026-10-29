const ABSOLUTE_OR_EMBEDDED_URL = /^(?:https?:|blob:|data:)/i;

function normalizeAppBasePath(value: string | undefined): string {
    const trimmed = value?.trim();
    if (!trimmed || trimmed === '/') return '';
    return `/${trimmed.replace(/^\/+|\/+$/g, '')}`;
}

/**
 * Prefixes shipped Next public assets with the app base path injected by next.config.
 * This is only for frontend-bundled public files; backend media URLs still use getAssetUrl.
 */
export function publicAssetPath(path: string): string {
    if (!path || ABSOLUTE_OR_EMBEDDED_URL.test(path)) return path;

    const assetPath = path.startsWith('/') ? path : `/${path}`;
    const appBasePath = normalizeAppBasePath(process.env.NEXT_PUBLIC_APP_BASE_PATH);
    if (!appBasePath) return assetPath;
    if (assetPath === appBasePath || assetPath.startsWith(`${appBasePath}/`)) return assetPath;
    return `${appBasePath}${assetPath}`;
}
