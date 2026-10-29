// @vitest-environment happy-dom
import React from "react";
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { AtelierShell } from "@/components/atelier/v3/AtelierShell";
import { MediaNode } from "@/components/atelier/v3/MediaNode";
import { parseAtelierProject } from "@/lib/atelierApi";
import { ATELIER_MEDIA_RENDERED_BOUNDS, findOpenAtelierCanvasPosition, getAtelierCanvasPlacementRect } from "@/lib/atelierCanvas";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { useAtelierStore } from "@/store/atelierStore";
import type { AtelierNode, AtelierNodeUploadReceipt, AtelierProject } from "@/lib/atelierTypes";
import fixture from "./fixtures/atelier-project.json";
import uploadFixture from "./fixtures/atelier-upload-ack.json";
import messages from "../../messages/en.json";

const STORAGE = "11111111-1111-4111-8111-111111111111";
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

function node(overrides: Partial<AtelierNode>): AtelierNode {
    const base = parseAtelierProject(fixture).nodes[0];
    return {
        ...structuredClone(base),
        id: "node",
        project_id: "p1",
        type: "image",
        x: 80,
        y: 96,
        width: 260,
        height: 180,
        data: {},
        media_urls: [],
        ...overrides,
    };
}

function project(nodes: AtelierNode[] = []): AtelierProject {
    return parseAtelierProject({ ...structuredClone(fixture), id: "p1", revision: 0, nodes, agent_turns: [], sequence: [], audio_tracks: [], exports: [], node_delete_receipts: [], node_upload_receipts: [] });
}

function overlaps(a: { x: number; y: number; width: number; height: number }, b: { x: number; y: number; width: number; height: number }) {
    return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
}

function mount() {
    return render(<NextIntlClientProvider locale="en" messages={messages}><AtelierShell /></NextIntlClientProvider>);
}

function uploadFile(label: string, name = "pixel.png") {
    fireEvent.click(screen.getByRole("button", { name: label }));
    fireEvent.change(screen.getByLabelText(label, { selector: "input" }), {
        target: { files: [new File([new Uint8Array([1, 2, 3, 4])], name, { type: "image/png" })] },
    });
}

beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE });
});

describe("Atelier canvas placement", () => {
    it("keeps bloom decoration from overriding canvas node positioning", () => {
        const css = readFileSync("src/components/atelier/v3/atelier-v3.css", "utf8");
        expect(css).not.toMatch(/\.atelier-v3-root\s+\.atelier-bloom\s*\{[\s\S]*?position\s*:/);
        const style = document.createElement("style");
        style.textContent = css;
        document.head.append(style);
        const root = document.createElement("div");
        root.className = "atelier-v3-root";
        const bloom = document.createElement("div");
        bloom.className = "atelier-bloom";
        root.append(bloom);
        document.body.append(root);
        expect(getComputedStyle(bloom).position).not.toBe("relative");
    });

    it("finds a non-overlapping default position without mutating existing nodes", () => {
        const existing = [node({ id: "first", x: 80, y: 96, width: 260, height: 180 })];
        const snapshot = structuredClone(existing);
        const position = findOpenAtelierCanvasPosition(existing, { width: 260, height: 180 });
        expect(overlaps({ ...position, width: 260, height: 180 }, existing[0])).toBe(false);
        expect(existing).toEqual(snapshot);
    });

    it("uses rendered media bounds and a non-overlapping fallback", () => {
        const tallCompletedImage = node({ id: "image", type: "image", status: "completed", x: 80, y: 96, width: 240, height: 180, media_urls: ["uploads/a.png"] });
        expect(getAtelierCanvasPlacementRect(tallCompletedImage)).toMatchObject({ x: 80, y: 96, width: 260, height: 433 });
        const genericAudio = node({ id: "audio", type: "audio", status: "completed", x: 80, y: 96, width: 260, height: 56, media_urls: ["uploads/a.mp3"] });
        expect(getAtelierCanvasPlacementRect(genericAudio)).toMatchObject({ width: ATELIER_MEDIA_RENDERED_BOUNDS.audio.width, height: ATELIER_MEDIA_RENDERED_BOUNDS.audio.height });
        const occupied = [
            { x: 80, y: 96, width: 260, height: 1200 },
            { x: 388, y: 96, width: 260, height: 1200 },
            { x: 696, y: 96, width: 260, height: 1200 },
        ];
        const position = findOpenAtelierCanvasPosition(occupied, { width: 260, height: 180 }, { startX: 80, startY: 96, columns: 3 });
        expect(occupied.some(existing => overlaps(existing, { ...position, width: 260, height: 180 }))).toBe(false);
    });

    it("renders generic audio media at the shared media width", () => {
        render(<NextIntlClientProvider locale="en" messages={messages}>
            <MediaNode id="audio" kind="audio" status="completed" src="/audio.mp3" filename="audio.mp3" x={0} y={0} width={ATELIER_MEDIA_RENDERED_BOUNDS.audio.width} height={ATELIER_MEDIA_RENDERED_BOUNDS.audio.height} />
        </NextIntlClientProvider>);
        const audio = screen.getByLabelText("audio.mp3");
        const shell = audio.closest('[role="button"]') as HTMLElement | null;
        expect(shell).not.toBeNull();
        expect(shell).toHaveStyle({ width: `${ATELIER_MEDIA_RENDERED_BOUNDS.audio.width}px` });
    });

    it("sends non-overlapping positions for new canvas nodes", async () => {
        let current = project();
        const fetch = vi.fn(async (url: string, init?: RequestInit) => {
            const request = new URL(url);
            if (request.pathname === "/atelier/projects" && init?.method === "GET") return response([current]);
            if (request.pathname === "/atelier/projects/p1/nodes" && init?.method === "POST") {
                const body = JSON.parse(String(init.body));
                const nextNode = node({ id: `created-${current.nodes.length}`, type: body.type, title: body.title, x: body.x, y: body.y, width: body.width, height: body.height });
                current = { ...current, revision: current.revision + 1, nodes: [...current.nodes, nextNode] };
                return response({ project: current, node_ids: [nextNode.id] });
            }
            throw new Error(`Unexpected request ${request.pathname}`);
        });
        vi.stubGlobal("fetch", fetch);
        mount();
        await screen.findByRole("button", { name: "Image" });
        fireEvent.click(screen.getByRole("button", { name: "Image" }));
        await waitFor(() => expect(current.nodes).toHaveLength(1));
        fireEvent.click(screen.getByRole("button", { name: "Video" }));
        await waitFor(() => expect(current.nodes).toHaveLength(2));
        expect(overlaps(current.nodes[0], current.nodes[1])).toBe(false);
    });

    it("sends non-overlapping upload and reference-upload positions", async () => {
        let current = project([node({ id: "target", type: "video", title: "Target", x: 80, y: 96, width: 260, height: 220 })]);
        const fetch = vi.fn(async (url: string, init?: RequestInit) => {
            const request = new URL(url);
            if (request.pathname === "/atelier/projects" && init?.method === "GET") return response([current]);
            if (init?.method === "POST" && request.pathname.includes("/uploads/")) {
                const next = structuredClone(uploadFixture) as unknown as {
                    project: { nodes: AtelierNode[]; node_upload_receipts: AtelierNodeUploadReceipt[] };
                    node_ids: string[];
                    receipt_id: string;
                    replayed: boolean;
                };
                const source = next.project.nodes[0];
                const requestFile = init.body instanceof File ? init.body : null;
                source.id = `upload-${current.nodes.length}`;
                source.project_id = "p1";
                source.title = request.searchParams.get("title") ?? source.title;
                source.x = Number(request.searchParams.get("x"));
                source.y = Number(request.searchParams.get("y"));
                source.width = Number(request.searchParams.get("width") ?? 260);
                source.height = Number(request.searchParams.get("height") ?? 180);
                const receipt = next.project.node_upload_receipts[0];
                receipt.client_request_id = request.searchParams.get("client_request_id")!;
                receipt.base_revision = Number(request.searchParams.get("expected_revision"));
                receipt.committed_revision = current.revision + 1;
                receipt.source_node_id = source.id;
                receipt.node_ids = [source.id];
                if (requestFile) {
                    receipt.media.size_bytes = requestFile.size;
                    source.data.size_bytes = requestFile.size;
                    source.data.filename = requestFile.name;
                }
                const targetId = request.pathname.match(/\/references\/([^/]+)$/)?.[1] ?? null;
                if (targetId) {
                    const decodedTargetId = decodeURIComponent(targetId);
                    source.data.parent_node_id = decodedTargetId;
                    receipt.operation = "upload_reference_attach";
                    receipt.target_node_id = decodedTargetId;
                    receipt.node_ids = [decodedTargetId, source.id];
                }
                const updatedNodes = current.nodes.map(existing => {
                    if (existing.id !== receipt.target_node_id) return existing;
                    return {
                        ...existing,
                        data: {
                            ...existing.data,
                            reference_node_ids: [source.id],
                            reference_image_urls: source.media_urls,
                        },
                    };
                });
                current = parseAtelierProject({ ...current, revision: current.revision + 1, nodes: [...updatedNodes, source], node_upload_receipts: [receipt] });
                return response({ ...next, project: current, node_ids: receipt.node_ids, receipt_id: receipt.receipt_id, replayed: false });
            }
            throw new Error(`Unexpected request ${request.pathname}`);
        });
        vi.stubGlobal("fetch", fetch);
        mount();
        await waitFor(() => expect(screen.getByRole("button", { name: "Upload media" })).toBeEnabled());
        uploadFile("Upload media", "first.png");
        await waitFor(() => expect(useAtelierStore.getState().currentProject?.nodes).toHaveLength(2));
        expect(useAtelierStore.getState().error).toBeNull();
        const firstStoreNodes = useAtelierStore.getState().currentProject?.nodes ?? [];
        expect(overlaps(getAtelierCanvasPlacementRect(firstStoreNodes[0]), getAtelierCanvasPlacementRect(firstStoreNodes[1]))).toBe(false);
        act(() => useAtelierStore.getState().selectNode("target"));
        await waitFor(() => expect(screen.getByRole("button", { name: "Upload reference" })).toBeEnabled());
        uploadFile("Upload reference", "second.png");
        await waitFor(() => expect(useAtelierStore.getState().currentProject?.nodes).toHaveLength(3));
        expect(useAtelierStore.getState().error).toBeNull();
        expect(useAtelierStore.getState().currentProject?.nodes[0].data.reference_node_ids).toEqual([useAtelierStore.getState().currentProject?.nodes[2].id]);
        const storeNodes = useAtelierStore.getState().currentProject?.nodes ?? [];
        expect(storeNodes.slice(0, 2).some(existing => overlaps(getAtelierCanvasPlacementRect(existing), getAtelierCanvasPlacementRect(storeNodes[2])))).toBe(false);
    });
});
