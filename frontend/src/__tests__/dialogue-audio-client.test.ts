import { afterEach, describe, expect, it, vi } from "vitest";
import { API_URL, api } from "@/lib/api";
import { validateDialogueAudioState } from "@/lib/dialogueAudioState";

afterEach(() => {
    vi.unstubAllGlobals();
});

function response(body: unknown, init: ResponseInit = {}) {
    return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
        ...init,
    });
}

describe("dialogue audio client", () => {
    it("posts an empty options patch by default", async () => {
        const fetchMock = vi.fn().mockResolvedValue(response({ id: "p" }));
        vi.stubGlobal("fetch", fetchMock);
        await api.generateLineAudio("p", "f1");
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/projects/p/frames/f1/audio`, expect.objectContaining({
            method: "POST",
            body: "{}",
        }));
    });

    it("preserves explicit zero and empty instructions in the options patch", async () => {
        const fetchMock = vi.fn().mockResolvedValue(response({ id: "p" }));
        vi.stubGlobal("fetch", fetchMock);
        await api.generateLineAudio("p", "f1", { speed: 0, volume: 0, instructions: "" });
        expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ speed: 0, volume: 0, instructions: "" });
    });

    it("parses structured validation errors from failed generation", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
            detail: [{ loc: ["body", "speed"], msg: "Input should be a valid number" }],
        }, { status: 422 })));
        await expect(api.generateLineAudio("p", "f1", { speed: 1 }))
            .rejects.toThrow(/body\.speed: Input should be a valid number/);
    });

    it("reads no-store audio state and rejects mismatched frame state", async () => {
        const state = {
            project_id: "p",
            frame_id: "f1",
            read_versions: { project: 4, series: null, library: 2 },
            options: {},
            freshness: "current",
        };
        const fetchMock = vi.fn().mockResolvedValue(response(state));
        vi.stubGlobal("fetch", fetchMock);
        await expect(api.getDialogueAudioState("p", "f1")).resolves.toMatchObject({ freshness: "current" });
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/projects/p/frames/f1/audio/state`, expect.objectContaining({
            method: "GET",
            cache: "no-store",
        }));
        expect(() => validateDialogueAudioState("p", "other", state)).toThrow(/current frame/);
    });
});

it.each([Number.NaN, Infinity, -Infinity])("rejects non-finite options before fetch: %s", async speed => {
    const fetchMock = vi.fn().mockResolvedValue(response({ id: "p" }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.generateLineAudio("p", "f1", { speed })).rejects.toThrow(/speed/);
    expect(fetchMock).not.toHaveBeenCalled();
});
