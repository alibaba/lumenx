// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from "vitest";
import {
    AtelierExportRequestError,
    beginAtelierExportRequest,
    listAtelierExportRequests,
    readAtelierExportRequest,
    resolveAtelierExportRequest,
    atelierExportProjectPendingKey,
} from "@/lib/atelierExportRequest";
import { beginAtelierExportDeletionRequest, listAtelierExportDeletionRequests, resolveAtelierExportDeletionRequest, AtelierExportDeletionRequestError } from "@/lib/atelierExportDeletionRequest";
import type { RuntimeEnvironment } from "@/lib/runtimeEnvironment";

const env = (storageId: string): RuntimeEnvironment => ({
    apiUrl: "http://atelier.local/api",
    storageId,
    key: JSON.stringify(["http://atelier.local/api", storageId]),
    generation: 1,
});

const ownerA = { environment: env("storage-a"), projectId: "project-1" };
const ownerB = { environment: env("storage-b"), projectId: "project-1" };

const entry = (parentId: string, gain = 1) => ({
    parentId,
    candidateId: null,
    trimStart: 0,
    trimEnd: 4,
    gain,
    transition: { kind: "cut", duration: 0 },
});

const track = (id: string) => ({
    id,
    node_id: `node-${id}`,
    start: 0,
    gain: 1,
    loop: false,
});

beforeEach(() => {
    localStorage.clear();
});

describe("Atelier export request journal", () => {
    it("isolates same-URL owners by runtime storage id and lists competing pending requests", () => {
        const first = beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 7,
            entries: [entry("video-a")],
            storage: localStorage,
            idFactory: () => "export-a",
        });
        const second = beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 8,
            entries: [entry("video-b")],
            storage: localStorage,
            idFactory: () => "export-b",
        });
        beginAtelierExportRequest({
            ...ownerB,
            expectedRevision: 9,
            entries: [entry("video-c")],
            storage: localStorage,
            idFactory: () => "export-c",
        });

        expect(first.status).toBe("created");
        expect(second.status).toBe("created");
        expect(atelierExportProjectPendingKey(ownerA)).not.toBe(atelierExportProjectPendingKey(ownerB));
        expect(listAtelierExportRequests({ ...ownerA, storage: localStorage }).map(item => item.clientRequestId)).toEqual(["export-a", "export-b"]);
        expect(listAtelierExportRequests({ ...ownerB, storage: localStorage }).map(item => item.clientRequestId)).toEqual(["export-c"]);
        expect(readAtelierExportRequest({ ...ownerA, clientRequestId: "export-b", storage: localStorage })?.request.expected_revision).toBe(8);
    });

    it("freezes a JSON snapshot before callers can mutate request inputs", () => {
        const entries = [entry("video-a")];
        const audioTracks = [track("music")];
        const result = beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 3,
            entries,
            audioTracks,
            storage: localStorage,
            idFactory: () => "export-freeze",
        });

        entries[0].gain = 9;
        audioTracks[0].start = 42;

        const stored = readAtelierExportRequest({ ...ownerA, clientRequestId: "export-freeze", storage: localStorage })!;
        expect(stored.request.entries?.[0]).toMatchObject({ parentId: "video-a", gain: 1 });
        expect(stored.request.audio_tracks?.[0]).toMatchObject({ id: "music", start: 0 });
        expect(result.request.request.entries?.[0]).toMatchObject({ parentId: "video-a", gain: 1 });
        expect(Object.isFrozen(result.request.request.entries)).toBe(true);
        expect(Object.isFrozen(result.request.request.entries?.[0])).toBe(true);
    });

    it("preserves omitted arrays as omitted and explicit arrays as present", () => {
        const omitted = beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 1,
            storage: localStorage,
            idFactory: () => "export-omitted",
        });
        const explicitEmpty = beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 1,
            entries: [],
            audioTracks: [],
            storage: localStorage,
            idFactory: () => "export-empty",
        });

        expect(omitted.request.request).not.toHaveProperty("entries");
        expect(omitted.request.request).not.toHaveProperty("audio_tracks");
        expect(explicitEmpty.request.request).toHaveProperty("entries", []);
        expect(explicitEmpty.request.request).toHaveProperty("audio_tracks", []);
    });

    it("fails closed for current-owner storage corruption or write failure while ignoring other-owner corruption", () => {
        const brokenStorage = {
            get length() { return 0; },
            key: () => null,
            getItem: () => null,
            setItem: () => { throw new Error("quota"); },
            removeItem: () => undefined,
            clear: () => undefined,
        } as unknown as Storage;

        expect(() => beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 1,
            storage: brokenStorage,
            idFactory: () => "export-fail",
        })).toThrowError(new AtelierExportRequestError("atelier_export_request_storage_unavailable"));

        localStorage.setItem(`${atelierExportProjectPendingKey(ownerB)}:export-bad`, "{broken");
        expect(listAtelierExportRequests({ ...ownerA, storage: localStorage })).toEqual([]);

        localStorage.setItem(`${atelierExportProjectPendingKey(ownerA)}:export-bad`, "{broken");
        expect(() => listAtelierExportRequests({ ...ownerA, storage: localStorage }))
            .toThrowError(new AtelierExportRequestError("atelier_export_request_store_corrupt"));
    });

    it("does not silently reuse an existing request id for a different payload", () => {
        const original = beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 1,
            entries: [entry("original")],
            storage: localStorage,
            idFactory: () => "export-collision",
        });

        const replay = beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 1,
            entries: [entry("original")],
            storage: localStorage,
            idFactory: () => "export-collision",
        });
        expect(replay.status).toBe("pending");
        expect(replay.request.request).toEqual(original.request.request);

        expect(() => beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 2,
            entries: [entry("different")],
            storage: localStorage,
            idFactory: () => "export-collision",
        })).toThrowError(new AtelierExportRequestError("atelier_export_request_store_corrupt"));
        expect(listAtelierExportRequests({ ...ownerA, storage: localStorage }).map(item => item.clientRequestId)).toEqual(["export-collision"]);
    });

    it("resolves only the exact matching request id and canonical payload", () => {
        const oldRequest = beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 1,
            entries: [entry("old")],
            storage: localStorage,
            idFactory: () => "export-old",
        }).request.request;
        const newRequest = beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 2,
            entries: [entry("new")],
            storage: localStorage,
            idFactory: () => "export-new",
        }).request.request;

        expect(resolveAtelierExportRequest({
            ...ownerA,
            clientRequestId: "export-old",
            request: newRequest,
            storage: localStorage,
        })).toBe(false);
        expect(listAtelierExportRequests({ ...ownerA, storage: localStorage }).map(item => item.clientRequestId)).toEqual(["export-new", "export-old"]);

        expect(resolveAtelierExportRequest({
            ...ownerA,
            clientRequestId: "export-old",
            request: oldRequest,
            storage: localStorage,
        })).toBe(true);
        expect(listAtelierExportRequests({ ...ownerA, storage: localStorage }).map(item => item.clientRequestId)).toEqual(["export-new"]);

        expect(resolveAtelierExportRequest({
            ...ownerA,
            clientRequestId: "export-new",
            request: oldRequest,
            storage: localStorage,
        })).toBe(false);
        expect(listAtelierExportRequests({ ...ownerA, storage: localStorage }).map(item => item.clientRequestId)).toEqual(["export-new"]);
    });

    it("stores redo requests as a separate operation while preserving legacy create journals", () => {
        const create = beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 4,
            storage: localStorage,
            idFactory: () => "create-1",
        }).request;
        const redo = beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 5,
            sourceExportId: "export-source-1",
            storage: localStorage,
            idFactory: () => "redo-1",
        }).request;

        expect(create.operation).toBe("create");
        expect(create.sourceExportId).toBeNull();
        expect(redo.operation).toBe("redo");
        expect(redo.sourceExportId).toBe("export-source-1");
        expect(redo.request).toEqual({ expected_revision: 5, client_request_id: "redo-1" });
        expect(() => beginAtelierExportRequest({
            ...ownerA,
            expectedRevision: 5,
            sourceExportId: "export-source-2",
            storage: localStorage,
            idFactory: () => "redo-1",
        })).toThrowError(new AtelierExportRequestError("atelier_export_request_store_corrupt"));

        const stored = listAtelierExportRequests({ ...ownerA, storage: localStorage }).find(item => item.clientRequestId === "redo-1")!;
        expect(resolveAtelierExportRequest({
            ...ownerA,
            clientRequestId: "redo-1",
            request: stored.request,
            operation: "create",
            sourceExportId: null,
            storage: localStorage,
        })).toBe(false);
        expect(resolveAtelierExportRequest({
            ...ownerA,
            clientRequestId: "redo-1",
            request: stored.request,
            operation: "redo",
            sourceExportId: "export-source-1",
            storage: localStorage,
        })).toBe(true);
    });
});


describe("Atelier export deletion request journal", () => {
    it("persists export ownership and delete_file before DELETE while isolating owners", () => {
        const first = beginAtelierExportDeletionRequest({
            ...ownerA,
            exportId: "export-1",
            expectedRevision: 7,
            deleteFile: true,
            storage: localStorage,
            idFactory: () => "delete-1",
        });
        beginAtelierExportDeletionRequest({
            ...ownerB,
            exportId: "export-1",
            expectedRevision: 7,
            deleteFile: true,
            storage: localStorage,
            idFactory: () => "delete-other-owner",
        });

        expect(first.status).toBe("created");
        expect(first.request).toMatchObject({ exportId: "export-1", clientRequestId: "delete-1", request: { delete_file: true } });
        expect(listAtelierExportDeletionRequests({ ...ownerA, storage: localStorage }).map(item => item.clientRequestId)).toEqual(["delete-1"]);
        expect(listAtelierExportDeletionRequests({ ...ownerB, storage: localStorage }).map(item => item.clientRequestId)).toEqual(["delete-other-owner"]);
    });

    it("fails closed on storage write failure and rejects request-id parameter drift", () => {
        const brokenStorage = {
            get length() { return 0; }, key: () => null, getItem: () => null,
            setItem: () => { throw new Error("quota"); }, removeItem: () => undefined, clear: () => undefined,
        } as unknown as Storage;
        expect(() => beginAtelierExportDeletionRequest({
            ...ownerA, exportId: "export-1", expectedRevision: 7, storage: brokenStorage, idFactory: () => "delete-fail",
        })).toThrowError(new AtelierExportDeletionRequestError("atelier_export_deletion_request_storage_unavailable"));

        beginAtelierExportDeletionRequest({
            ...ownerA, exportId: "export-1", expectedRevision: 7, deleteFile: true, storage: localStorage, idFactory: () => "delete-collision",
        });
        expect(() => beginAtelierExportDeletionRequest({
            ...ownerA, exportId: "export-2", expectedRevision: 7, deleteFile: true, storage: localStorage, idFactory: () => "delete-collision",
        })).toThrowError(new AtelierExportDeletionRequestError("atelier_export_deletion_request_store_corrupt"));
    });

    it("resolves only the exact original delete request", () => {
        const stored = beginAtelierExportDeletionRequest({
            ...ownerA, exportId: "export-1", expectedRevision: 7, deleteFile: true, storage: localStorage, idFactory: () => "delete-resolve",
        }).request;
        expect(resolveAtelierExportDeletionRequest({
            ...ownerA, exportId: "export-1", clientRequestId: "delete-resolve", request: { ...stored.request, delete_file: false }, storage: localStorage,
        })).toBe(false);
        expect(resolveAtelierExportDeletionRequest({
            ...ownerA, exportId: "export-1", clientRequestId: "delete-resolve", request: stored.request, storage: localStorage,
        })).toBe(true);
        expect(listAtelierExportDeletionRequests({ ...ownerA, storage: localStorage })).toEqual([]);
    });
});
