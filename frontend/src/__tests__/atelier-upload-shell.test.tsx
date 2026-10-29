// @vitest-environment happy-dom
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { AtelierShell } from "@/components/atelier/v3/AtelierShell";
import { useAtelierStore } from "@/store/atelierStore";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { parseAtelierProject } from "@/lib/atelierApi";
import { API_URL } from "@/lib/apiUrl";
import fixture from "./fixtures/atelier-upload-ack.json";
import messages from "../../messages/en.json";

const STORAGE = "11111111-1111-4111-8111-111111111111";
const HASH = "a".repeat(64);
const REQUEST_HASH = "b".repeat(64);
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const mediaFile = () => new File([new Uint8Array(68)], "pixel.png", { type: "image/png" });
const mount = () => render(<NextIntlClientProvider locale="en" messages={messages}><AtelierShell /></NextIntlClientProvider>);
const pick = (label: string) => {
    fireEvent.click(screen.getByRole("button", { name: label }));
    fireEvent.change(screen.getByLabelText(label, { selector: "input" }), { target: { files: [mediaFile()] } });
};

function backend(withTarget = false) {
    let project = parseAtelierProject({ ...structuredClone(fixture.project), revision: 0, nodes: [], node_upload_receipts: [] });
    if (withTarget) project.nodes = [{ ...structuredClone(fixture.project.nodes[0]), id: "target", title: "Target", type: "video", status: "draft", data: {}, media_urls: [] }];
    const control = { lost: false, missing: false, release: null as (() => void) | null, delay: false };
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
        const request = new URL(url);
        if (init?.method === "POST" && request.pathname.includes("/uploads/")) {
            const reference = request.pathname.endsWith("/references/target");
            const next = structuredClone(fixture);
            const source = next.project.nodes[0];
            source.title = request.searchParams.get("title") || "Uploaded image: pixel.png";
            source.x = Number(request.searchParams.get("x") ?? 0);
            source.y = Number(request.searchParams.get("y") ?? 0);
            source.width = Number(request.searchParams.get("width") ?? source.width);
            source.height = Number(request.searchParams.get("height") ?? source.height);
            const receipt = next.project.node_upload_receipts[0];
            receipt.client_request_id = request.searchParams.get("client_request_id")!;
            receipt.base_revision = project.revision;
            receipt.committed_revision = project.revision + 1;
            if (reference) {
                Object.assign(source.data, { parent_node_id: "target", reference_role: "image" });
                Object.assign(receipt, { operation: "upload_reference_attach", target_node_id: "target", node_ids: ["target", source.id] });
            }
            const previous = project.nodes.map(node => reference && node.id === "target" ? { ...node, data: { reference_node_ids: [source.id], reference_image_urls: source.media_urls } } : node);
            project = parseAtelierProject({ ...project, revision: project.revision + 1, nodes: [...previous, source], node_upload_receipts: [receipt] });
            if (control.delay) await new Promise<void>(resolve => { control.release = resolve; });
            if (control.lost) throw new TypeError("Connection lost");
            return response({ ...next, project, node_ids: receipt.node_ids });
        }
        if (request.pathname.includes("/uploads/receipts/")) {
            if (control.missing) return response({ detail: "Not found" }, 404);
            return response({ status: "found", project, receipt: project.node_upload_receipts[0] });
        }
        if (request.pathname === "/atelier/projects") return response([project]);
        if (request.pathname === "/atelier/projects/p1") return response(project);
        throw new Error(`Unexpected request ${request.pathname}`);
    });
    vi.stubGlobal("fetch", fetch);
    return { control, fetch };
}

const generationProvider = () => ({
    schema_version: 1,
    attempt_id: "attempt-deleted",
    phase: "accepted",
    provider_model_id: "wan2.7-i2v",
    intent: {},
    media: [],
    scope: {},
    credential_ref: "credential",
    fingerprint: HASH,
    handle: null,
    observation: null,
    prepared_artifact: null,
    artifact: null,
});

const generationAttempt = () => ({
    schema_version: 1,
    project_id: "p1",
    node_id: "deleted-video",
    candidate_id: "candidate-deleted",
    attempt_id: "attempt-deleted",
    attempt_revision: 0,
    status: "cancelled",
    error_code: null,
    first_frame_source: { node_id: "image-deleted", media_url: "uploads/deleted.png" },
    output_ref: null,
    provider_execution: { ...generationProvider(), phase: "cancelled" },
    created_at: 1,
    updated_at: 1,
});

const generationReceipt = () => ({
    schema_version: 1,
    project_id: "p1",
    entrypoint: "atelier.provider_video.v1",
    request_contract_version: "request-json-v1",
    client_request_id: "generation-shell",
    request_fingerprint: REQUEST_HASH,
    node_id: "deleted-video",
    candidate_id: "candidate-deleted",
    attempt_id: "attempt-deleted",
    execution_fingerprint: HASH,
    base_revision: 0,
    committed_revision: 1,
    created_at: 1,
});

const pendingGenerationRecord = () => ({
    schema_version: 1,
    apiBase: API_URL,
    storageId: STORAGE,
    projectId: "p1",
    nodeId: "deleted-video",
    clientRequestId: "generation-shell",
    request: {
        expected_revision: 0,
        client_request_id: "generation-shell",
        model_id: "wan/wan2.7-video#i2v",
        mode: "i2v",
        prompt: "lost deleted node",
        first_frame: { node_id: "image-deleted", media_url: "uploads/deleted.png" },
        params: { duration: 5, resolution: "1080p", prompt_extend: false, watermark: false },
    },
});

function canonicalJson(value: unknown): string {
    if (value === null || typeof value !== "object") return JSON.stringify(value);
    if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
    return `{${Object.keys(value as Record<string, unknown>).sort().map(key => `${JSON.stringify(key)}:${canonicalJson((value as Record<string, unknown>)[key])}`).join(",")}}`;
}

function storePendingGeneration() {
    const owner = encodeURIComponent(canonicalJson({ apiBase: API_URL, storageId: STORAGE, projectId: "p1", nodeId: "deleted-video" }));
    window.localStorage.setItem(`lumenx.atelierGeneration.request.v1:${owner}:generation-shell`, canonicalJson(pendingGenerationRecord()));
}

beforeEach(() => {
    vi.restoreAllMocks(); window.localStorage.clear(); clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE });
});

describe("Atelier upload UI with real store and client", () => {
    it("clears a recovered pending-upload warning without hiding another write error", async () => {
        backend();
        const original = useAtelierStore.getState().loadPendingUploads;
        let unavailable = true;
        useAtelierStore.setState({ loadPendingUploads: () => {
            if (unavailable) throw new Error("Upload journal unavailable");
            return original();
        } });
        try {
            mount();
            await screen.findByText("Upload journal unavailable");
            unavailable = false;
            act(() => { window.dispatchEvent(new Event("focus")); });
            await waitFor(() => expect(screen.queryByText("Upload journal unavailable")).not.toBeInTheDocument());
            act(() => { useAtelierStore.setState({ error: "A different save failed" }); window.dispatchEvent(new Event("focus")); });
            expect(screen.getByText("A different save failed")).toBeInTheDocument();
        } finally {
            useAtelierStore.setState({ loadPendingUploads: original });
        }
    });

    it("shows the new media only after its upload commit and selects it", async () => {
        const { control, fetch } = backend(); control.delay = true;
        mount();
        await waitFor(() => expect(screen.getByRole("button", { name: "Upload media" })).toBeEnabled());
        pick("Upload media");
        await waitFor(() => expect(control.release).not.toBeNull());
        expect(useAtelierStore.getState().currentProject?.nodes).toEqual([]);
        expect(screen.getByRole("button", { name: "Uploading…" })).toBeDisabled();
        act(() => { control.release!(); });
        await waitFor(() => expect(useAtelierStore.getState().selectedNodeId).toBe("id-2"));
        expect(within(screen.getByLabelText("Atelier canvas")).getByText("pixel.png")).toBeInTheDocument();
        expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    });

    it("keeps an unknown upload through reload, leaves 404 pending and reads back without another POST", async () => {
        const { control, fetch } = backend(); control.lost = true;
        const first = mount();
        await waitFor(() => expect(screen.getByRole("button", { name: "Upload media" })).toBeEnabled());
        pick("Upload media");
        await screen.findByRole("button", { name: "Check upload result" });
        first.unmount();
        act(() => { clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE }); });
        mount(); control.missing = true;
        const check = await screen.findByRole("button", { name: "Check upload result" });
        fireEvent.click(check);
        await screen.findByText("The upload result is still unconfirmed. Check again shortly.");
        expect(screen.getByRole("button", { name: "Upload media" })).toBeDisabled();
        control.missing = false;
        fireEvent.click(check);
        await waitFor(() => expect(screen.queryByRole("button", { name: "Check upload result" })).not.toBeInTheDocument());
        expect(screen.getByRole("button", { name: "Upload media" })).toBeEnabled();
        expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    });

    it("uploads and attaches a reference through one atomic request", async () => {
        const { fetch } = backend(true); mount();
        await waitFor(() => expect(useAtelierStore.getState().currentProject?.nodes).toHaveLength(1));
        act(() => useAtelierStore.getState().selectNode("target"));
        pick("Upload reference");
        await waitFor(() => expect(useAtelierStore.getState().currentProject?.nodes[0].data.reference_node_ids).toEqual(["id-2"]));
        expect(screen.getByRole("button", { name: "Detach reference pixel.png" })).toBeInTheDocument();
        const posts = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
        expect(posts).toHaveLength(1);
        expect(new URL(posts[0][0]).pathname).toBe("/atelier/projects/p1/uploads/references/target");
    });

    it("keeps generation readback reachable when no node is selected and the source node is gone", async () => {
        storePendingGeneration();
        const { fetch } = backend(false);
        fetch.mockImplementation(async (url: string) => {
            const request = new URL(url);
            const current = parseAtelierProject({ ...structuredClone(fixture.project), revision: 1, nodes: [], node_upload_receipts: [],
                generation_attempts: [generationAttempt()], generation_request_receipts: [generationReceipt()] });
            if (request.pathname === "/atelier/projects") return response([current]);
            if (request.pathname === "/atelier/projects/p1") return response(current);
            if (request.pathname === "/atelier/projects/p1/generation/requests/generation-shell") {
                return response({ project: current, receipt: generationReceipt(), attempt: generationAttempt(), candidate: null, replayed: true, new_candidate_ids: [], reason: "owner_missing" });
            }
            throw new Error(`Unexpected request ${request.pathname}`);
        });

        mount();
        fireEvent.click(await screen.findByRole("button", { name: "Read original request" }));

        await waitFor(() => expect(fetch.mock.calls.some(([url]) => new URL(String(url)).pathname.endsWith("/generation/requests/generation-shell"))).toBe(true));
        expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    });
});
