import axios from "axios";

import { API_URL } from "@/lib/apiUrl";
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, type RuntimeEnvironment } from "@/lib/runtimeEnvironment";

export const APP_ACCESS_API_URL = API_URL;
export const APP_ACCESS_SESSION_EXPIRED_EVENT = "lumenx:app-access-session-expired";
const EXPECTED_SERVICE_NAME = "lumenx-core";
const EXPECTED_PROTOCOL_VERSION = 1;
const SERVICE_IDENTITY_TIMEOUT_MS = 5_000;

export type AppAccessSession = {
    auth_required: boolean;
    authenticated: boolean;
};

export type AppServiceIdentity = {
    service: "lumenx-core";
    protocol_version: 1;
};

export class AppAccessError extends Error {
    constructor(message: string, public readonly code: string = "app_access_error") {
        super(message);
        this.name = "AppAccessError";
    }
}

const isRecord = (value: unknown): value is Record<string, unknown> => (
    typeof value === "object" && value !== null && !Array.isArray(value)
);

const parseServiceIdentity = (value: unknown): AppServiceIdentity => {
    if (!isRecord(value) || value.ok !== true || value.service !== EXPECTED_SERVICE_NAME
        || typeof value.protocol_version !== "number" || value.protocol_version !== EXPECTED_PROTOCOL_VERSION) {
        throw new AppAccessError("Invalid LumenX service identity", "invalid_service_identity");
    }
    return { service: EXPECTED_SERVICE_NAME, protocol_version: EXPECTED_PROTOCOL_VERSION };
};

const parseSession = (value: unknown): AppAccessSession => {
    if (!isRecord(value) || typeof value.auth_required !== "boolean" || typeof value.authenticated !== "boolean") {
        throw new AppAccessError("Invalid app access session response", "invalid_session");
    }
    return {
        auth_required: value.auth_required,
        authenticated: value.authenticated,
    };
};

const sessionUrl = () => `${APP_ACCESS_API_URL}/auth/session`;
const healthUrl = () => `${APP_ACCESS_API_URL}/health`;

const createTimedHealthSignal = (parentSignal?: AbortSignal): { signal: AbortSignal; cleanup: () => void } => {
    const controller = new AbortController();
    const abort = () => {
        if (!controller.signal.aborted) {
            controller.abort();
        }
    };
    const timeoutId = setTimeout(abort, SERVICE_IDENTITY_TIMEOUT_MS);
    if (parentSignal?.aborted) {
        abort();
    }
    parentSignal?.addEventListener("abort", abort, { once: true });
    return {
        signal: controller.signal,
        cleanup: () => {
            clearTimeout(timeoutId);
            parentSignal?.removeEventListener("abort", abort);
        },
    };
};

export async function readAppServiceIdentity(options: { signal?: AbortSignal } = {}): Promise<AppServiceIdentity> {
    const timedSignal = createTimedHealthSignal(options.signal);
    try {
        const response = await fetch(healthUrl(), {
            method: "GET",
            credentials: "include",
            headers: { "Accept": "application/json" },
            cache: "no-store",
            redirect: "error",
            signal: timedSignal.signal,
        });
        if (!response.ok) {
            throw new AppAccessError("Unable to read LumenX service identity", "service_identity_read_failed");
        }
        try {
            return parseServiceIdentity(await response.json());
        } catch (error) {
            if (error instanceof AppAccessError) throw error;
            throw new AppAccessError("Invalid LumenX service identity", "invalid_service_identity");
        }
    } finally {
        timedSignal.cleanup();
    }
}

export async function readAppAccessSession(options: { signal?: AbortSignal } = {}): Promise<AppAccessSession> {
    const response = await fetch(sessionUrl(), {
        method: "GET",
        credentials: "include",
        headers: { "Accept": "application/json" },
        cache: "no-store",
        redirect: "error",
        signal: options.signal,
    });
    if (!response.ok) {
        throw new AppAccessError("Unable to read app access session", "session_read_failed");
    }
    return parseSession(await response.json());
}

export async function loginAppAccess(token: string, options: { signal?: AbortSignal } = {}): Promise<AppAccessSession> {
    const response = await fetch(sessionUrl(), {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
        redirect: "error",
        signal: options.signal,
    });
    if (!response.ok) {
        throw new AppAccessError("Unable to unlock app access", "login_failed");
    }
    parseSession(await response.json());
    return readAppAccessSession({ signal: options.signal });
}

export function emitAppAccessSessionExpired(): void {
    if (typeof window === "undefined") return;
    window.dispatchEvent(new CustomEvent(APP_ACCESS_SESSION_EXPIRED_EVENT, { detail: { reason: "access_required" } }));
}

export function emitAppAccessEnvironmentChanged(): void {
    if (typeof window === "undefined") return;
    window.dispatchEvent(new CustomEvent(APP_ACCESS_SESSION_EXPIRED_EVENT, { detail: { reason: "environment_changed" } }));
}

const normalizeBasePath = (pathname: string): string => {
    const withoutTrailing = pathname.replace(/\/+$/, "");
    return withoutTrailing || "";
};

const isUrlUnderApiBase = (current: URL): boolean => {
    const base = new URL(APP_ACCESS_API_URL);
    if (current.origin !== base.origin) return false;
    const basePath = normalizeBasePath(base.pathname);
    if (!basePath) return true;
    return current.pathname === basePath || current.pathname.startsWith(`${basePath}/`);
};

const resolveAxiosLikeUrl = (href: string, baseUrl?: string): URL => {
    const base = new URL(APP_ACCESS_API_URL);
    if (/^[a-z][a-z0-9+.-]*:/i.test(href)) {
        return new URL(href);
    }
    if (!baseUrl) {
        return new URL(href, typeof window !== "undefined" ? window.location.href : base.href);
    }
    const resolvedBase = new URL(baseUrl, base.href);
    const joined = `${resolvedBase.href.replace(/\/+$/, "")}/${href.replace(/^\/+/, "")}`;
    return new URL(joined);
};

export function isAppAccessApiUrl(input: RequestInfo | URL, baseUrl?: string): boolean {
    try {
        const href = typeof input === "string" || input instanceof URL ? String(input) : input.url;
        if (baseUrl && !isUrlUnderApiBase(new URL(baseUrl, APP_ACCESS_API_URL))) return false;
        return isUrlUnderApiBase(resolveAxiosLikeUrl(href, baseUrl));
    } catch {
        return false;
    }
}

const isAccessRequiredBody = (body: unknown): boolean => (
    isRecord(body) && body.code === "access_required"
);

const isEnvironmentChangedBody = (body: unknown): boolean => (
    isRecord(body) && body.code === "environment_changed"
);

function isDiscoveryEndpoint(input: RequestInfo | URL, baseUrl?: string): boolean {
    try {
        const href = typeof input === "string" || input instanceof URL ? String(input) : input.url;
        const url = resolveAxiosLikeUrl(href, baseUrl);
        const base = new URL(APP_ACCESS_API_URL);
        const basePath = normalizeBasePath(base.pathname);
        const relative = basePath && url.pathname.startsWith(`${basePath}/`)
            ? url.pathname.slice(basePath.length)
            : url.pathname;
        return relative === "/health" || relative === "/auth/session" || relative === "/runtime/environment";
    } catch {
        return false;
    }
}

function withRuntimeEnvironmentHeader(input: RequestInfo | URL, init: RequestInit, environment: RuntimeEnvironment): RequestInit {
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init.headers).forEach((value, key) => headers.set(key, value));
    const existing = headers.get("X-LumenX-Environment");
    if (existing && existing !== environment.storageId) {
        throw new AppAccessError("Runtime environment changed before request dispatch", "environment_changed");
    }
    if (!existing) {
        headers.set("X-LumenX-Environment", environment.storageId);
    }
    return { ...init, headers };
}

const getAxiosEnvironmentHeader = (headers: unknown): string | null => {
    if (!headers) return null;
    if (headers instanceof Headers) return headers.get("X-LumenX-Environment");
    if (isRecord(headers)) {
        const getter = headers.get;
        if (typeof getter === "function") {
            const value = getter.call(headers, "X-LumenX-Environment");
            if (typeof value === "string") return value;
        }
        const direct = headers["X-LumenX-Environment"] ?? headers["x-lumenx-environment"];
        return typeof direct === "string" ? direct : null;
    }
    return null;
};

const withAxiosRuntimeEnvironmentHeader = (headers: unknown, environment: RuntimeEnvironment): Record<string, unknown> => {
    const existing = getAxiosEnvironmentHeader(headers);
    if (existing && existing !== environment.storageId) {
        throw new AppAccessError("Runtime environment changed before request dispatch", "environment_changed");
    }
    const next = isRecord(headers) ? { ...headers } : {};
    if (!existing) {
        next["X-LumenX-Environment"] = environment.storageId;
    }
    return next;
};

async function emitIfAccessRequired(response: Response): Promise<void> {
    if (response.status !== 401 && response.status !== 409) return;
    try {
        const body = await response.clone().json();
        if ((response.status === 401 && isAccessRequiredBody(body))
            || (response.status === 409 && isEnvironmentChangedBody(body))) {
            if (response.status === 409) emitAppAccessEnvironmentChanged();
            else emitAppAccessSessionExpired();
        }
    } catch {
        // Keep the original response untouched for callers; non-JSON 401s are not app access events.
    }
}

export async function appAccessFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
    const shouldAttach = isAppAccessApiUrl(input);
    const discovery = shouldAttach && isDiscoveryEndpoint(input);
    const environment = shouldAttach && !discovery ? getRuntimeEnvironment() : null;
    if (shouldAttach && !discovery && !environment) {
        throw new AppAccessError("Runtime environment is not active", "runtime_environment_unavailable");
    }
    const baseInit: RequestInit = shouldAttach ? { ...init, credentials: "include" as RequestCredentials } : init;
    if (shouldAttach && typeof window !== "undefined") {
        const headers = new Headers(baseInit.headers);
        const platformToken = window.localStorage.getItem("lumenx_platform_token");
        if (platformToken && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${platformToken}`);
        if (!headers.has("X-LumenX-Product")) headers.set("X-LumenX-Product", "studio");
        baseInit.headers = headers;
    }
    const response = await fetch(input, shouldAttach && !discovery && environment
        ? withRuntimeEnvironmentHeader(input, baseInit, environment)
        : baseInit);
    if (shouldAttach) {
        if (environment && !isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
            throw new AppAccessError("Runtime environment changed while request was in flight", "environment_changed");
        }
        await emitIfAccessRequired(response);
    }
    return response;
}

type MaybeInterceptorManager = {
    use?: (...handlers: Array<((value: unknown) => unknown) | null | undefined>) => number;
    eject?: (id: number) => void;
};

type MaybeAxios = {
    interceptors?: {
        request?: MaybeInterceptorManager;
        response?: MaybeInterceptorManager;
    };
};

export function installAppAccessAxiosInterceptors(axiosClient: MaybeAxios = axios as MaybeAxios): () => void {
    const requestManager = axiosClient.interceptors?.request;
    const responseManager = axiosClient.interceptors?.response;
    if (!requestManager?.use || !requestManager.eject || !responseManager?.use || !responseManager.eject) {
        return () => undefined;
    }

    const requestId = requestManager.use((config: unknown) => {
        if (!isRecord(config)) return config;
        const rawUrl = config.url;
        const rawBaseUrl = config.baseURL;
        if (typeof rawUrl !== "string") return config;
        const baseUrl = typeof rawBaseUrl === "string" ? rawBaseUrl : undefined;
        if (!isAppAccessApiUrl(rawUrl, baseUrl)) return config;
        const next: Record<string, unknown> = { ...config, withCredentials: true };
        if (!isDiscoveryEndpoint(rawUrl, baseUrl)) {
            const environment = getRuntimeEnvironment();
            if (!environment) {
                throw new AppAccessError("Runtime environment is not active", "runtime_environment_unavailable");
            }
            next.headers = withAxiosRuntimeEnvironmentHeader(next.headers, environment);
            next.__lumenxEnvironmentKey = environment.key;
            next.__lumenxEnvironmentGeneration = environment.generation;
        }
        return next;
    });
    const responseId = responseManager.use(
        response => {
            const config = isRecord(response) && isRecord(response.config) ? response.config : null;
            const key = config && typeof config.__lumenxEnvironmentKey === "string" ? config.__lumenxEnvironmentKey : null;
            const generation = config && typeof config.__lumenxEnvironmentGeneration === "number" ? config.__lumenxEnvironmentGeneration : undefined;
            if (key && !isCurrentRuntimeEnvironment(key, generation)) {
                throw new AppAccessError("Runtime environment changed while request was in flight", "environment_changed");
            }
            return response;
        },
        error => {
            const status = isRecord(error) && isRecord(error.response) ? error.response.status : undefined;
            const data = isRecord(error) && isRecord(error.response) ? error.response.data : undefined;
            const config = isRecord(error) && isRecord(error.config) ? error.config : null;
            const rawUrl = config && typeof config.url === "string" ? config.url : null;
            const rawBaseUrl = config && typeof config.baseURL === "string" ? config.baseURL : undefined;
            if (rawUrl && isAppAccessApiUrl(rawUrl, rawBaseUrl)
                && ((status === 401 && isAccessRequiredBody(data))
                    || (status === 409 && isEnvironmentChangedBody(data)))) {
                const key = config && typeof config.__lumenxEnvironmentKey === "string" ? config.__lumenxEnvironmentKey : null;
                const generation = config && typeof config.__lumenxEnvironmentGeneration === "number" ? config.__lumenxEnvironmentGeneration : undefined;
                if (!key || isCurrentRuntimeEnvironment(key, generation)) {
                    if (status === 409) emitAppAccessEnvironmentChanged();
                    else emitAppAccessSessionExpired();
                }
            }
            return Promise.reject(error);
        }
    );

    return () => {
        requestManager.eject?.(requestId);
        responseManager.eject?.(responseId);
    };
}
