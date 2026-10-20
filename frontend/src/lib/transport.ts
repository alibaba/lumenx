/**
 * Transport layer for dual-mode API communication.
 * - In Tauri desktop mode: uses IPC invoke to proxy through Rust
 * - In Web mode: uses standard HTTP fetch/axios
 *
 * This abstraction allows the same frontend code to run in both environments.
 */

// Detect Tauri environment at runtime
export const isTauri = (): boolean => {
    return typeof window !== 'undefined' && '__TAURI__' in window;
};

interface ApiResponse<T = unknown> {
    data: T;
    status: number;
}

/**
 * Make an API request that works in both Tauri and Web environments.
 * In Tauri mode, requests are proxied through the Rust backend via IPC.
 * In Web mode, requests go directly via HTTP.
 */
export async function tauriApiRequest<T = unknown>(
    method: string,
    path: string,
    body?: unknown
): Promise<ApiResponse<T>> {
    if (isTauri()) {
        // Dynamic import to avoid bundling Tauri APIs in web builds
        const { invoke } = await import('@tauri-apps/api/core');

        const response = await invoke<{ status: number; body: string }>('api_proxy', {
            method: method.toUpperCase(),
            path,
            body: body ? JSON.stringify(body) : null,
        });

        return {
            status: response.status,
            data: JSON.parse(response.body) as T,
        };
    } else {
        // Web mode: use standard fetch
        const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:17177';
        const url = `${baseUrl}${path}`;

        const options: RequestInit = {
            method: method.toUpperCase(),
            headers: {
                'Content-Type': 'application/json',
            },
        };

        if (body && method.toUpperCase() !== 'GET') {
            options.body = JSON.stringify(body);
        }

        const response = await fetch(url, options);
        const data = await response.json();

        return {
            status: response.status,
            data: data as T,
        };
    }
}

/**
 * Check if the backend is ready (works in both modes)
 */
export async function checkBackendReady(): Promise<boolean> {
    if (isTauri()) {
        try {
            const { invoke } = await import('@tauri-apps/api/core');
            return await invoke<boolean>('check_backend_health');
        } catch {
            return false;
        }
    } else {
        try {
            const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:17177';
            const response = await fetch(`${baseUrl}/health`, { signal: AbortSignal.timeout(2000) });
            return response.ok;
        } catch {
            return false;
        }
    }
}
