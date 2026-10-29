// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API_URL } from "@/lib/apiUrl";
import { atelierProjectPackageApi } from "@/lib/atelierProjectPackageApi";
import { PROJECT_PACKAGE_MAX_BYTES } from "@/lib/projectPackage";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import fixture from "./fixtures/atelier-project-package-wire.json";

const STORAGE_A = "11111111-1111-4111-8111-111111111111";
const STORAGE_B = "22222222-2222-4222-8222-222222222222";
const file = () => new File([new Uint8Array([80, 75, 3, 4])], "project.lumenx.zip", { type: "application/zip" });
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const changeEnvironment = () => activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });

beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
});

describe("Atelier independent package API", () => {
    it("imports the real backend wire through one protected raw ZIP request", async () => {
        const fetchMock = vi.fn().mockResolvedValue(json(fixture.prepared_project));
        vi.stubGlobal("fetch", fetchMock);
        const imported = await atelierProjectPackageApi.importProjectPackage(file());
        expect(imported.id).toBe(fixture.prepared_project.id);
        expect(imported.import_provenance).toEqual(fixture.prepared_project.import_provenance);
        expect(imported.agent_turns).toEqual([]);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/atelier/projects/package/import`, expect.objectContaining({
            method: "POST", credentials: "include", cache: "no-store", body: expect.any(ArrayBuffer),
        }));
        const headers = fetchMock.mock.calls[0][1].headers as Headers;
        expect(headers.get("Content-Type")).toBe("application/zip");
        expect(headers.get("X-LumenX-Environment")).toBe(STORAGE_A);
    });

    it("rejects empty and oversize inputs before reading or posting", async () => {
        const fetchMock = vi.fn();
        vi.stubGlobal("fetch", fetchMock);
        await expect(atelierProjectPackageApi.importProjectPackage(new Blob())).rejects.toMatchObject({ outcomeUnknown: false });
        const tooLarge = file();
        Object.defineProperty(tooLarge, "size", { value: PROJECT_PACKAGE_MAX_BYTES + 1 });
        await expect(atelierProjectPackageApi.importProjectPackage(tooLarge)).rejects.toMatchObject({ outcomeUnknown: false });
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it("reports file read failures as not submitted", async () => {
        const input = file();
        vi.spyOn(input, "arrayBuffer").mockRejectedValue(new Error("unreadable"));
        const fetchMock = vi.fn();
        vi.stubGlobal("fetch", fetchMock);
        await expect(atelierProjectPackageApi.importProjectPackage(input)).rejects.toMatchObject({ outcomeUnknown: false });
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it("blocks POST if environment changes while reading the file", async () => {
        const input = file();
        vi.spyOn(input, "arrayBuffer").mockImplementation(async () => { changeEnvironment(); return new ArrayBuffer(4); });
        const fetchMock = vi.fn();
        vi.stubGlobal("fetch", fetchMock);
        await expect(atelierProjectPackageApi.importProjectPackage(input)).rejects.toMatchObject({ outcomeUnknown: false, code: "environment_changed" });
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it("keeps a lost POST response unknown without retrying", async () => {
        const fetchMock = vi.fn().mockRejectedValue(new TypeError("network lost"));
        vi.stubGlobal("fetch", fetchMock);
        await expect(atelierProjectPackageApi.importProjectPackage(file())).rejects.toMatchObject({ outcomeUnknown: true });
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it.each([
        [422, { code: "atelier_project_package_invalid" }, false],
        [413, { code: "atelier_project_package_too_large" }, false],
        [422, { detail: { code: "atelier_project_package_invalid_request" } }, false],
        [401, { code: "access_required" }, false],
        [503, { code: "atelier_storage_unavailable", commit_uncertain: false }, false],
        [503, { code: "atelier_storage_unavailable", commit_uncertain: true }, true],
        [503, { code: "atelier_storage_unavailable" }, true],
        [422, { code: "unknown_service_error" }, true],
        [500, { detail: "/private/sensitive/debug" }, true],
    ])("classifies %s error without exposing response details", async (status, body, outcomeUnknown) => {
        const fetchMock = vi.fn().mockResolvedValue(json(body, status as number));
        vi.stubGlobal("fetch", fetchMock);
        const error = await atelierProjectPackageApi.importProjectPackage(file()).catch(e => e);
        expect(error).toMatchObject({ outcomeUnknown });
        expect(error.message).not.toContain("/private/");
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it.each(["invalid-json", "missing-provenance", "source-identity", "active-turn"])('rejects %s import ACK as unknown', async (kind) => {
        const value = structuredClone(fixture.prepared_project) as Record<string, unknown>;
        if (kind === "missing-provenance") delete value.import_provenance;
        if (kind === "source-identity") value.id = fixture.prepared_project.import_provenance.source_project_id;
        if (kind === "active-turn") value.agent_turns = [{ id: "active" }];
        const response = kind === "invalid-json" ? new Response("not-json") : json(value);
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
        await expect(atelierProjectPackageApi.importProjectPackage(file())).rejects.toMatchObject({ outcomeUnknown: true });
    });

    it("rejects a late import response after environment changes", async () => {
        vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => { changeEnvironment(); return json(fixture.prepared_project); }));
        await expect(atelierProjectPackageApi.importProjectPackage(file())).rejects.toMatchObject({ outcomeUnknown: true, code: "environment_changed" });
    });

    it("rechecks environment after asynchronous import JSON parsing", async () => {
        const response = json(fixture.prepared_project);
        vi.spyOn(response, "json").mockImplementation(async () => { changeEnvironment(); return fixture.prepared_project; });
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
        await expect(atelierProjectPackageApi.importProjectPackage(file())).rejects.toMatchObject({ outcomeUnknown: true, code: "environment_changed" });
    });

    it("exports through protected no-store GET and preserves safe filename metadata", async () => {
        const fetchMock = vi.fn().mockResolvedValue(new Response(new Uint8Array([80, 75, 3, 4]), { headers: {
            "Content-Type": "application/zip", "Content-Disposition": "attachment; filename*=UTF-8''copy.lumenx.zip",
        } }));
        vi.stubGlobal("fetch", fetchMock);
        const result = await atelierProjectPackageApi.exportProjectPackage("project / 1");
        expect(result.blob.size).toBe(4);
        expect(result.filename).toBe("copy.lumenx.zip");
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/atelier/projects/project%20%2F%201/package/export`, expect.objectContaining({
            method: "GET", cache: "no-store", credentials: "include",
        }));
    });

    it.each(["wrong-type", "empty", "oversize-header"])("rejects invalid export %s", async kind => {
        const headers: Record<string, string> = { "Content-Type": kind === "wrong-type" ? "text/html" : "application/zip" };
        if (kind === "oversize-header") headers["Content-Length"] = String(PROJECT_PACKAGE_MAX_BYTES + 1);
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(kind === "empty" ? "" : "PK", { headers })));
        await expect(atelierProjectPackageApi.exportProjectPackage("project")).rejects.toThrow();
    });

    it("rejects stale export body after environment changes", async () => {
        const response = new Response("PK", { headers: { "Content-Type": "application/zip" } });
        vi.spyOn(response, "blob").mockImplementation(async () => { changeEnvironment(); return file(); });
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
        await expect(atelierProjectPackageApi.exportProjectPackage("project")).rejects.toMatchObject({ code: "environment_changed" });
    });
});
