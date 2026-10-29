// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from "vitest";
import {
    PlaygroundGenerationRequestError,
    beginPlaygroundGenerationRequest,
    listPlaygroundGenerationRequests,
    playgroundRequestStoragePrefix,
    readPlaygroundGenerationRequest,
    resolvePlaygroundGenerationRequest,
} from "@/lib/playgroundGenerationRequest";
import type { RuntimeEnvironment } from "@/lib/runtimeEnvironment";

const env = (storageId: string): RuntimeEnvironment => ({
    apiUrl: "http://playground.local/api",
    storageId,
    key: JSON.stringify(["http://playground.local/api", storageId]),
    generation: 1,
});

beforeEach(() => {
    localStorage.clear();
});

describe("Playground generation request journal", () => {
    it("persists the exact request body before dispatch with a stable client request id", () => {
        const record = beginPlaygroundGenerationRequest({
            environment: env("storage-a"),
            mode: "t2i",
            modelId: "wanx2.1-t2i",
            prompt: "glass city",
            negativePrompt: "",
            inputMedia: [],
            parameters: { seed: 0, prompt_extend: false },
            batchSize: 1,
            storage: localStorage,
            idFactory: () => "playground-stable",
            now: () => 100,
        });

        expect(record.request).toEqual({
            client_request_id: "playground-stable",
            mode: "t2i",
            model_id: "wanx2.1-t2i",
            prompt: "glass city",
            negative_prompt: "",
            parameters: { seed: 0, prompt_extend: false },
        });
        expect(readPlaygroundGenerationRequest({
            environment: env("storage-a"),
            clientRequestId: "playground-stable",
            storage: localStorage,
        })?.requestFingerprint).toBe(record.requestFingerprint);
    });

    it("isolates journals by runtime storage identity and fails closed on corrupt current records", () => {
        beginPlaygroundGenerationRequest({
            environment: env("storage-a"),
            mode: "i2i",
            modelId: "gpt-image-2",
            prompt: "A",
            inputMedia: ["uploads/a.png"],
            storage: localStorage,
            idFactory: () => "playground-a",
        });
        beginPlaygroundGenerationRequest({
            environment: env("storage-b"),
            mode: "i2i",
            modelId: "gpt-image-2",
            prompt: "B",
            inputMedia: ["uploads/b.png"],
            storage: localStorage,
            idFactory: () => "playground-b",
        });

        expect(listPlaygroundGenerationRequests({ environment: env("storage-a"), storage: localStorage }).map(item => item.clientRequestId))
            .toEqual(["playground-a"]);
        expect(listPlaygroundGenerationRequests({ environment: env("storage-b"), storage: localStorage }).map(item => item.clientRequestId))
            .toEqual(["playground-b"]);

        localStorage.setItem(`${playgroundRequestStoragePrefix({ environment: env("storage-a") })}:broken`, "{");
        expect(() => listPlaygroundGenerationRequests({ environment: env("storage-a"), storage: localStorage }))
            .toThrowError(new PlaygroundGenerationRequestError("playground_generation_request_store_corrupt"));
        expect(listPlaygroundGenerationRequests({ environment: env("storage-b"), storage: localStorage })).toHaveLength(1);
    });

    it("refuses to dispatch when storage write cannot be read back", () => {
        const memory = new Map<string, string>();
        const noReadbackStorage = {
            get length() { return memory.size; },
            key: (index: number) => Array.from(memory.keys())[index] ?? null,
            getItem: () => null,
            setItem: (key: string, value: string) => { memory.set(key, value); },
            removeItem: (key: string) => { memory.delete(key); },
            clear: () => { memory.clear(); },
        } as unknown as Storage;

        expect(() => beginPlaygroundGenerationRequest({
            environment: env("storage-a"),
            mode: "t2v",
            modelId: "wan2.7-t2v",
            prompt: "write unavailable",
            storage: noReadbackStorage,
            idFactory: () => "playground-no-readback",
        })).toThrowError(new PlaygroundGenerationRequestError("playground_generation_request_storage_unavailable"));
        expect(memory.size).toBe(1);
    });

    it("keeps an unresolved journal across reload until the matching terminal or deleted ACK resolves it", () => {
        const stored = beginPlaygroundGenerationRequest({
            environment: env("storage-a"),
            mode: "r2v",
            modelId: "wan2.7-r2v",
            prompt: "retry only by same id",
            inputMedia: ["uploads/ref.png"],
            parameters: { seed: -1, watermark: false },
            batchSize: 2,
            storage: localStorage,
            idFactory: () => "playground-reload",
        });

        expect(listPlaygroundGenerationRequests({ environment: env("storage-a"), storage: localStorage }).map(item => item.clientRequestId))
            .toEqual(["playground-reload"]);
        expect(resolvePlaygroundGenerationRequest({
            environment: env("storage-a"),
            clientRequestId: "playground-reload",
            request: { ...stored.request, prompt: "different" },
            storage: localStorage,
        })).toBe(false);
        expect(resolvePlaygroundGenerationRequest({
            environment: env("storage-a"),
            clientRequestId: "playground-reload",
            request: stored.request,
            storage: localStorage,
        })).toBe(true);
        expect(listPlaygroundGenerationRequests({ environment: env("storage-a"), storage: localStorage })).toEqual([]);
    });
});
