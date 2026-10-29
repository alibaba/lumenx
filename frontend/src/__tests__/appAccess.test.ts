// @vitest-environment happy-dom
import axios from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_URL } from "@/lib/api";
import {
    APP_ACCESS_SESSION_EXPIRED_EVENT,
    appAccessFetch,
    installAppAccessAxiosInterceptors,
    loginAppAccess,
    readAppAccessSession,
    readAppServiceIdentity,
} from "@/lib/appAccess";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
    return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
        ...init,
    });
}

describe("app access client", () => {
    beforeEach(() => {
        vi.restoreAllMocks();
        clearRuntimeEnvironment();
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.useRealTimers();
    });


    it("validates the backend service identity from health", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true, service: "lumenx-core", protocol_version: 1 }));
        vi.stubGlobal("fetch", fetchMock);

        await expect(readAppServiceIdentity()).resolves.toEqual({ service: "lumenx-core", protocol_version: 1 });
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/health`, expect.objectContaining({
            method: "GET",
            credentials: "include",
            cache: "no-store",
            redirect: "error",
        }));
    });

    it.each([
        { ok: true, service: "wrong-service", protocol_version: 1 },
        { ok: true, service: "lumenx-core", protocol_version: true },
        { ok: false, service: "lumenx-core", protocol_version: 1 },
    ])("rejects malformed backend service identity %j", async (body) => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(body)));

        await expect(readAppServiceIdentity()).rejects.toMatchObject({ code: "invalid_service_identity" });
    });

    it("rejects non-JSON and non-2xx health responses", async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(new Response("<html>not lumenx</html>", { status: 200, headers: { "Content-Type": "text/html" } }))
            .mockResolvedValueOnce(jsonResponse({ code: "nope" }, { status: 500 }));
        vi.stubGlobal("fetch", fetchMock);

        await expect(readAppServiceIdentity()).rejects.toMatchObject({ code: "invalid_service_identity" });
        await expect(readAppServiceIdentity()).rejects.toMatchObject({ code: "service_identity_read_failed" });
    });


    it("times out hung service identity reads without falling back", async () => {
        vi.useFakeTimers();
        const fetchMock = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => reject(new Error("health aborted")), { once: true });
        }));
        vi.stubGlobal("fetch", fetchMock);

        const read = readAppServiceIdentity();
        const assertion = expect(read).rejects.toThrow("health aborted");
        await vi.advanceTimersByTimeAsync(5_000);

        await assertion;
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("reads and validates the session shape", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ auth_required: false, authenticated: false }));
        vi.stubGlobal("fetch", fetchMock);

        await expect(readAppAccessSession()).resolves.toEqual({ auth_required: false, authenticated: false });
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/auth/session`, expect.objectContaining({
            method: "GET",
            credentials: "include",
        }));

        fetchMock.mockResolvedValueOnce(jsonResponse({ auth_required: "no", authenticated: false }));
        await expect(readAppAccessSession()).rejects.toThrow(/session/i);
    });

    it("posts token in JSON body then confirms the cookie with a session GET", async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: true }))
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: true }));
        vi.stubGlobal("fetch", fetchMock);

        await loginAppAccess("secret-token");

        expect(fetchMock).toHaveBeenNthCalledWith(1, `${API_URL}/auth/session`, expect.objectContaining({
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token: "secret-token" }),
            redirect: "error",
        }));
        expect(fetchMock).toHaveBeenNthCalledWith(2, `${API_URL}/auth/session`, expect.objectContaining({
            method: "GET",
            credentials: "include",
        }));
        expect(fetchMock.mock.calls[0][0]).not.toContain("secret-token");
    });


    it("rejects redirected login token posts without following the token body", async () => {
        const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("redirect blocked"));
        vi.stubGlobal("fetch", fetchMock);

        await expect(loginAppAccess("secret-token")).rejects.toThrow("redirect blocked");

        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/auth/session`, expect.objectContaining({
            method: "POST",
            body: JSON.stringify({ token: "secret-token" }),
            redirect: "error",
        }));
    });

    it("adds credentials to backend fetches and emits access expiration without retry", async () => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ code: "access_required" }, { status: 401 }));
        vi.stubGlobal("fetch", fetchMock);
        const listener = vi.fn();
        window.addEventListener(APP_ACCESS_SESSION_EXPIRED_EVENT, listener);

        const response = await appAccessFetch(`${API_URL}/projects`, { method: "POST" });

        expect(response.status).toBe(401);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/projects`, expect.objectContaining({
            method: "POST",
            credentials: "include",
            headers: expect.any(Headers),
        }));
        expect((fetchMock.mock.calls[0][1].headers as Headers).get("X-LumenX-Environment")).toBe("11111111-1111-4111-8111-111111111111");
        expect(listener).toHaveBeenCalledTimes(1);
        window.removeEventListener(APP_ACCESS_SESSION_EXPIRED_EVENT, listener);
    });

    it("omits the storage identity header for discovery endpoints", async () => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
        vi.stubGlobal("fetch", fetchMock);

        await appAccessFetch(`${API_URL}/runtime/environment`);

        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/runtime/environment`, expect.objectContaining({
            credentials: "include",
        }));
        expect(fetchMock.mock.calls[0][1].headers).toBeUndefined();
    });


    it("fails closed for product fetches without an active runtime environment", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
        vi.stubGlobal("fetch", fetchMock);

        await expect(appAccessFetch(`${API_URL}/projects`, { method: "GET" })).rejects.toMatchObject({
            code: "runtime_environment_unavailable",
        });
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it("rejects explicit stale environment headers instead of replacing them", async () => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
        vi.stubGlobal("fetch", fetchMock);

        await expect(appAccessFetch(`${API_URL}/projects/p`, {
            method: "PUT",
            headers: { "X-LumenX-Environment": "11111111-1111-4111-8111-111111111111" },
        })).rejects.toMatchObject({ code: "environment_changed" });
        expect(fetchMock).not.toHaveBeenCalled();
    });


    it("rejects stale environment headers carried by Request objects", async () => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
        vi.stubGlobal("fetch", fetchMock);

        const request = new Request(`${API_URL}/projects/p`, {
            method: "PUT",
            headers: { "X-LumenX-Environment": "11111111-1111-4111-8111-111111111111" },
        });
        await expect(appAccessFetch(request)).rejects.toMatchObject({ code: "environment_changed" });
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it("does not emit access expiration from stale fetch responses after an environment switch", async () => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
        let resolveResponse!: (value: Response) => void;
        const fetchMock = vi.fn(() => new Promise<Response>(resolve => { resolveResponse = resolve; }));
        vi.stubGlobal("fetch", fetchMock);
        const listener = vi.fn();
        window.addEventListener(APP_ACCESS_SESSION_EXPIRED_EVENT, listener);

        const request = appAccessFetch(`${API_URL}/projects/p`, { method: "PUT" });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
        resolveResponse(jsonResponse({ code: "access_required" }, { status: 401 }));

        await expect(request).rejects.toMatchObject({ code: "environment_changed" });
        expect(listener).not.toHaveBeenCalled();
        window.removeEventListener(APP_ACCESS_SESSION_EXPIRED_EVENT, listener);
    });

    it("emits expiration on environment mismatch without replaying the request", async () => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ code: "environment_changed" }, { status: 409 }));
        vi.stubGlobal("fetch", fetchMock);
        const listener = vi.fn();
        window.addEventListener(APP_ACCESS_SESSION_EXPIRED_EVENT, listener);

        const response = await appAccessFetch(`${API_URL}/projects/p`, { method: "PUT" });

        expect(response.status).toBe(409);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(listener).toHaveBeenCalledTimes(1);
        window.removeEventListener(APP_ACCESS_SESSION_EXPIRED_EVENT, listener);
    });

    it("does not alter third-party fetch credentials", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
        vi.stubGlobal("fetch", fetchMock);

        await appAccessFetch("https://example.test/resource", { method: "POST" });

        expect(fetchMock).toHaveBeenCalledWith("https://example.test/resource", { method: "POST" });
    });

    it("returns the original readable response body for streams and text responses", async () => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
        const fetchMock = vi.fn().mockResolvedValue(new Response("event: done\n\n", {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
        }));
        vi.stubGlobal("fetch", fetchMock);

        const response = await appAccessFetch(`${API_URL}/stream`);

        await expect(response.text()).resolves.toBe("event: done\n\n");
    });


    it("limits axios session expiration to configured API requests", async () => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
        const listener = vi.fn();
        window.addEventListener(APP_ACCESS_SESSION_EXPIRED_EVENT, listener);
        const requestUse = vi.fn((...handlers: Array<((value: unknown) => unknown) | null | undefined>) => {
            const fulfilled = handlers[0];
            if (!fulfilled) throw new Error("missing request fulfilled handler");
            expect(fulfilled({ url: "/projects", baseURL: API_URL })).toMatchObject({ url: "/projects", baseURL: API_URL, withCredentials: true });
            expect(fulfilled({ url: "/projects", baseURL: `${API_URL}/api` })).toMatchObject({ url: "/projects", baseURL: `${API_URL}/api`, withCredentials: true });
            expect(fulfilled({ url: "/projects", baseURL: "https://example.test" })).toEqual({ url: "/projects", baseURL: "https://example.test" });
            expect(fulfilled({ url: "https://example.test/projects", baseURL: API_URL })).toEqual({ url: "https://example.test/projects", baseURL: API_URL });
            return 11;
        });
        let rejectHandler!: (error: unknown) => Promise<unknown>;
        const responseUse = vi.fn((...handlers: Array<((value: unknown) => unknown) | null | undefined>) => {
            const rejected = handlers[1];
            if (!rejected) throw new Error("missing response rejected handler");
            rejectHandler = rejected as (error: unknown) => Promise<unknown>;
            return 12;
        });
        const requestEject = vi.fn();
        const responseEject = vi.fn();

        const cleanup = installAppAccessAxiosInterceptors({
            interceptors: {
                request: { use: requestUse, eject: requestEject },
                response: { use: responseUse, eject: responseEject },
            },
        });

        await expect(rejectHandler({
            config: { url: "https://example.test/projects" },
            response: { status: 401, data: { code: "access_required" } },
        })).rejects.toBeTruthy();
        expect(listener).not.toHaveBeenCalled();

        await expect(rejectHandler({
            config: { url: "/projects", baseURL: API_URL },
            response: { status: 401, data: { code: "access_required" } },
        })).rejects.toBeTruthy();
        expect(listener).toHaveBeenCalledTimes(1);
        cleanup();
        expect(requestEject).toHaveBeenCalledWith(11);
        expect(responseEject).toHaveBeenCalledWith(12);
        window.removeEventListener(APP_ACCESS_SESSION_EXPIRED_EVENT, listener);
    });


    it("does not emit axios environment events from stale responses after an environment switch", async () => {
        const first = activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
        const listener = vi.fn();
        window.addEventListener(APP_ACCESS_SESSION_EXPIRED_EVENT, listener);
        let rejectHandler!: (error: unknown) => Promise<unknown>;
        const responseUse = vi.fn((...handlers: Array<((value: unknown) => unknown) | null | undefined>) => {
            const rejected = handlers[1];
            if (!rejected) throw new Error("missing response rejected handler");
            rejectHandler = rejected as (error: unknown) => Promise<unknown>;
            return 51;
        });

        installAppAccessAxiosInterceptors({
            interceptors: {
                request: { use: vi.fn(() => 50), eject: vi.fn() },
                response: { use: responseUse, eject: vi.fn() },
            },
        });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });

        await expect(rejectHandler({
            config: {
                url: "/projects/p",
                baseURL: API_URL,
                __lumenxEnvironmentKey: first.key,
                __lumenxEnvironmentGeneration: first.generation,
            },
            response: { status: 409, data: { code: "environment_changed" } },
        })).rejects.toBeTruthy();
        expect(listener).not.toHaveBeenCalled();
        window.removeEventListener(APP_ACCESS_SESSION_EXPIRED_EVENT, listener);
    });

    it("adds storage identity to axios product requests but not discovery requests", async () => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
        const requestUse = vi.fn((...handlers: Array<((value: unknown) => unknown) | null | undefined>) => {
            const fulfilled = handlers[0];
            if (!fulfilled) throw new Error("missing request fulfilled handler");
            expect(fulfilled({ url: "/projects", baseURL: API_URL })).toMatchObject({
                url: "/projects",
                baseURL: API_URL,
                withCredentials: true,
                headers: { "X-LumenX-Environment": "11111111-1111-4111-8111-111111111111" },
            });
            expect(fulfilled({ url: "/runtime/environment", baseURL: API_URL })).toEqual({
                url: "/runtime/environment",
                baseURL: API_URL,
                withCredentials: true,
            });
            return 21;
        });
        const responseUse = vi.fn(() => 22);

        installAppAccessAxiosInterceptors({
            interceptors: {
                request: { use: requestUse, eject: vi.fn() },
                response: { use: responseUse, eject: vi.fn() },
            },
        });
    });


    it("fails closed for axios product requests without an active runtime environment", async () => {
        const requestUse = vi.fn((...handlers: Array<((value: unknown) => unknown) | null | undefined>) => {
            const fulfilled = handlers[0];
            if (!fulfilled) throw new Error("missing request fulfilled handler");
            expect(() => fulfilled({ url: "/projects", baseURL: API_URL })).toThrow(/Runtime environment is not active/);
            return 31;
        });

        installAppAccessAxiosInterceptors({
            interceptors: {
                request: { use: requestUse, eject: vi.fn() },
                response: { use: vi.fn(() => 32), eject: vi.fn() },
            },
        });
    });

    it("rejects stale explicit axios environment headers instead of replacing them", async () => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
        const requestUse = vi.fn((...handlers: Array<((value: unknown) => unknown) | null | undefined>) => {
            const fulfilled = handlers[0];
            if (!fulfilled) throw new Error("missing request fulfilled handler");
            expect(() => fulfilled({
                url: "/projects/p",
                baseURL: API_URL,
                headers: { "X-LumenX-Environment": "11111111-1111-4111-8111-111111111111" },
            })).toThrow(/Runtime environment changed/);
            return 41;
        });

        installAppAccessAxiosInterceptors({
            interceptors: {
                request: { use: requestUse, eject: vi.fn() },
                response: { use: vi.fn(() => 42), eject: vi.fn() },
            },
        });
    });

    it("installs and ejects axios interceptors only when requested", async () => {
        const requestUse = vi.spyOn(axios.interceptors.request, "use");
        const responseUse = vi.spyOn(axios.interceptors.response, "use");
        const requestEject = vi.spyOn(axios.interceptors.request, "eject");
        const responseEject = vi.spyOn(axios.interceptors.response, "eject");

        const cleanup = installAppAccessAxiosInterceptors();

        expect(requestUse).toHaveBeenCalledTimes(1);
        expect(responseUse).toHaveBeenCalledTimes(1);
        cleanup();
        expect(requestEject).toHaveBeenCalledTimes(1);
        expect(responseEject).toHaveBeenCalledTimes(1);
    });
});
