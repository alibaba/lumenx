import axios from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API_URL, api } from "@/lib/api";
import { diagnosticsApi } from "@/lib/diagnosticsApi";

vi.mock("axios", () => ({ default: { get: vi.fn() } }));
beforeEach(() => { vi.mocked(axios.get).mockReset(); });

describe("shared task diagnostics transport", () => {
    it("keeps Studio aliases bound to the same neutral diagnostics calls", () => {
        expect(api.healthCheck).toBe(diagnosticsApi.healthCheck);
        expect(api.diagnoseLogTail).toBe(diagnosticsApi.diagnoseLogTail);
    });
    it("keeps the liveness timeout and returned health data", async () => {
        const data = { ok: true, time: 1, log_dir: "/logs", log_file: "/logs/app.log", studio_projects: 3 };
        vi.mocked(axios.get).mockResolvedValue({ data });
        expect(await api.healthCheck()).toEqual(data);
        expect(axios.get).toHaveBeenCalledExactlyOnceWith(`${API_URL}/health`, { timeout: 5000 });
    });
    it("keeps default and explicit log-tail limits plus timeout", async () => {
        const data = { path: "/logs/app.log", lines: ["ready"], errors: [], missing: false };
        vi.mocked(axios.get).mockResolvedValue({ data });
        expect(await api.diagnoseLogTail()).toEqual(data);
        expect(await api.diagnoseLogTail(25)).toEqual(data);
        expect(axios.get).toHaveBeenNthCalledWith(1, `${API_URL}/diagnose/log_tail`, { params: { lines: 200 }, timeout: 8000 });
        expect(axios.get).toHaveBeenNthCalledWith(2, `${API_URL}/diagnose/log_tail`, { params: { lines: 25 }, timeout: 8000 });
    });
    it("preserves failures without retrying read requests", async () => {
        const failure = new Error("offline");
        vi.mocked(axios.get).mockRejectedValue(failure);
        await expect(api.healthCheck()).rejects.toBe(failure);
        expect(axios.get).toHaveBeenCalledTimes(1);
    });
});
