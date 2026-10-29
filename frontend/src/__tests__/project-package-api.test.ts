// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API_URL, api, ProjectPackageImportError } from "@/lib/api";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";

const STORAGE_ID = "11111111-1111-4111-8111-111111111111";

function response(body: BodyInit, init: ResponseInit = {}) {
    return new Response(body, { status: 200, ...init });
}

describe("project package API client", () => {
    beforeEach(() => {
        clearRuntimeEnvironment();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_ID });
        vi.restoreAllMocks();
    });

    it("exports a project package as an authenticated zip Blob with no-store headers", async () => {
        const fetchMock = vi.fn().mockResolvedValue(response(new Blob(["zip-bytes"], { type: "application/zip" }), {
            headers: {
                "Content-Type": "application/zip",
                "Cache-Control": "no-store",
                "Content-Disposition": "attachment; filename*=UTF-8''lumenx-project-p.lumenx.zip",
            },
        }));
        vi.stubGlobal("fetch", fetchMock);

        const result = await api.exportProjectPackage("p");

        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/projects/p/package/export`, expect.objectContaining({
            method: "GET",
            credentials: "include",
            cache: "no-store",
        }));
        expect((fetchMock.mock.calls[0][1].headers as Headers).get("Accept")).toBe("application/zip");
        expect((fetchMock.mock.calls[0][1].headers as Headers).get("X-LumenX-Environment")).toBe(STORAGE_ID);
        expect(result.blob.type).toBe("application/zip");
        expect(result.filename).toBe("lumenx-project-p.lumenx.zip");
        expect(result.cacheControl).toBe("no-store");
    });

    it("uploads raw zip bytes once and rejects non-success responses without retrying", async () => {
        const fetchMock = vi.fn().mockResolvedValue(response(JSON.stringify({ detail: "bad zip" }), {
            status: 422,
            headers: { "Content-Type": "application/json" },
        }));
        vi.stubGlobal("fetch", fetchMock);

        await expect(api.importProjectPackage(new File(["bad"], "bad.zip", { type: "application/zip" }))).rejects.toThrow("bad zip");

        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/projects/package/import`, expect.objectContaining({
            method: "POST",
            body: expect.any(ArrayBuffer),
            credentials: "include",
        }));
        expect((fetchMock.mock.calls[0][1].headers as Headers).get("Content-Type")).toBe("application/zip");
        expect((fetchMock.mock.calls[0][1].headers as Headers).get("X-LumenX-Environment")).toBe(STORAGE_ID);
    });


    it("marks network import failures as unknown after local bytes are read", async () => {
        vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network lost")));

        await expect(api.importProjectPackage(new File(["zip"], "p.zip", { type: "application/zip" })))
            .rejects.toMatchObject({ outcomeUnknown: true });
    });

    it("does not mark explicit backend import rejection as unknown", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(JSON.stringify({ detail: "bad zip" }), {
            status: 422,
            headers: { "Content-Type": "application/json" },
        })));

        try {
            await api.importProjectPackage(new File(["bad"], "bad.zip", { type: "application/zip" }));
            throw new Error("expected rejection");
        } catch (error) {
            expect(error).not.toBeInstanceOf(ProjectPackageImportError);
            expect((error as Error).message).toBe("bad zip");
            expect((error as { outcomeUnknown?: boolean }).outcomeUnknown).not.toBe(true);
        }
    });


    it("marks uncertain server import failures as unknown", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(JSON.stringify({ detail: "commit uncertain" }), {
            status: 503,
            headers: { "Content-Type": "application/json" },
        })));

        await expect(api.importProjectPackage(new File(["zip"], "p.zip", { type: "application/zip" })))
            .rejects.toMatchObject({ outcomeUnknown: true, message: "commit uncertain" });
    });

    it("does not dispatch an import if the runtime changes while reading local zip bytes", async () => {
        const fetchMock = vi.fn();
        vi.stubGlobal("fetch", fetchMock);
        const packageBlob = new Blob(["zip"], { type: "application/zip" });
        vi.spyOn(packageBlob, "arrayBuffer").mockImplementation(async () => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
            return new ArrayBuffer(3);
        });

        await expect(api.importProjectPackage(packageBlob)).rejects.toThrow(/environment changed/i);

        expect(fetchMock).not.toHaveBeenCalled();
    });

    it("rejects an export if the runtime changes while reading the zip Blob", async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true,
            headers: new Headers({ "Content-Type": "application/zip" }),
            blob: async () => {
                activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
                return new Blob(["zip"], { type: "application/zip" });
            },
        });
        vi.stubGlobal("fetch", fetchMock);

        await expect(api.exportProjectPackage("p")).rejects.toThrow(/environment changed/i);
    });
});
