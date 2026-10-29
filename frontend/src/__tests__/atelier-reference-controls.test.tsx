// @vitest-environment happy-dom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ReferenceControls } from "@/components/atelier/v3/ReferenceControls";
import { parseAtelierProject } from "@/lib/atelierApi";
import fixture from "./fixtures/atelier-project.json";

afterEach(cleanup);
const labels = { title: "References", select: "Choose source", empty: "Upload media first", attach: "Attach", detach: "Remove", pending: "Pending media", pendingReady: "Ready to confirm", attachPending: "Attach as pending", confirmPending: "Use current media" };
function setup() {
    const project = parseAtelierProject(structuredClone(fixture));
    const base = project.nodes[0];
    project.nodes = [
        { ...base, id: "target", type: "video", title: "Target", data: {}, media_urls: [] },
        { ...base, id: "image", type: "image", title: "Image", data: {}, media_urls: ["uploads/shared.png"] },
        { ...base, id: "audio", type: "audio", title: "Audio", data: {}, media_urls: ["uploads/sound.wav"] },
        { ...base, id: "invalid", type: "video", title: "Invalid take", media_urls: ["uploads/stale.mp4"],
            data: { selected_candidate_id: "bad", candidates: [{ id: "bad", status: "completed" }, { id: "other", status: "completed", media_url: "uploads/other.mp4" }] } },
    ];
    return { project, targetId: "target", ownerKey: "environment-a", labels, onAttach: vi.fn(), onAttachPending: vi.fn(), onDetach: vi.fn() };
}

describe("Atelier reference controls", () => {
    it("offers compatible actual media and sends a source id without adding a local relation", () => {
        const props = setup();
        render(<ReferenceControls {...props} />);
        expect(screen.queryByRole("option", { name: "Invalid take" })).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Attach" })).toBeDisabled();
        fireEvent.change(screen.getByRole("combobox"), { target: { value: "image" } });
        fireEvent.click(screen.getByRole("button", { name: "Attach" }));
        expect(props.onAttach).toHaveBeenCalledWith({ sourceNodeId: "image" });
        expect(screen.queryByRole("button", { name: "Remove Image" })).not.toBeInTheDocument();
        expect(props.project.nodes[0].data).toEqual({});
    });

    it("uses source identity to remove one of two references sharing a URL and waits for ACK", () => {
        const props = setup();
        props.project.nodes.push({ ...props.project.nodes[1], id: "second", title: "Second" });
        props.project.nodes[0].data = { reference_node_ids: ["image", "second"], reference_image_urls: ["uploads/shared.png"] };
        const { rerender } = render(<ReferenceControls {...props} />);
        fireEvent.click(screen.getByRole("button", { name: "Remove Image" }));
        expect(props.onDetach).toHaveBeenCalledWith({ sourceNodeId: "image" });
        expect(screen.getByRole("button", { name: "Remove Image" })).toBeInTheDocument();
        rerender(<ReferenceControls {...props} disabled />);
        expect(screen.getByRole("button", { name: "Remove Image" })).toBeDisabled();
        expect(screen.getByRole("button", { name: "Remove Second" })).toBeDisabled();
    });

    it("filters image targets and clears selection when the environment or target changes", () => {
        const props = setup();
        const { rerender } = render(<ReferenceControls {...props} />);
        fireEvent.change(screen.getByRole("combobox"), { target: { value: "audio" } });
        rerender(<ReferenceControls {...props} ownerKey="environment-b" />);
        expect(screen.getByRole("button", { name: "Attach" })).toBeDisabled();
        props.project.nodes[0].type = "image";
        rerender(<ReferenceControls {...props} ownerKey="environment-b" />);
        expect(within(screen.getByRole("combobox")).queryByRole("option", { name: "Audio" })).not.toBeInTheDocument();
    });



    it("offers an explicit confirm action when a pending source now has usable media", () => {
        const props = setup();
        props.project.nodes[0].data = {
            reference_node_ids: ["image"],
            pending_reference_nodes: [{ schema_version: 1, source_node_id: "image", source_kind: "image", status: "pending_media" }],
        };
        const { rerender } = render(<ReferenceControls {...props} />);
        expect(screen.getByText("Ready to confirm")).toBeInTheDocument();
        expect(screen.getByText("Image")).toBeInTheDocument();
        expect(within(screen.getByRole("combobox")).queryByRole("option", { name: "Image" })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Use current media Image" }));
        expect(props.onAttach).toHaveBeenCalledWith({ sourceNodeId: "image" });
        expect(props.onAttachPending).not.toHaveBeenCalled();
        expect(props.onDetach).not.toHaveBeenCalled();
        rerender(<ReferenceControls {...props} disabled />);
        expect(screen.getByRole("button", { name: "Use current media Image" })).toBeDisabled();
    });

    it.each(["image", "audio"] as const)("keeps incompatible pending %s sources inert", sourceKind => {
        const props = setup();
        props.project.nodes[0].type = "image";
        props.project.nodes[0].data = {
            reference_node_ids: ["audio"],
            pending_reference_nodes: [{ schema_version: 1, source_node_id: "audio", source_kind: sourceKind, status: "pending_media" }],
        };
        render(<ReferenceControls {...props} />);
        expect(screen.getByText("Pending media")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /Use current media/ })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Remove Audio" }));
        expect(props.onDetach).toHaveBeenCalledWith({ sourceNodeId: "audio" });
        expect(props.onAttach).not.toHaveBeenCalled();
    });

    it("keeps pending rows inert when the source still has no current usable media", () => {
        const props = setup();
        props.project.nodes.push({ ...props.project.nodes[1], id: "blank", title: "Blank image", media_urls: [], data: {} });
        props.project.nodes.push({ ...props.project.nodes[1], id: "history", title: "Historical image", media_urls: [], data: { deleted_candidates: [{ id: "old", status: "completed", media_url: "uploads/old.png" }] } });
        props.project.nodes[0].data = {
            reference_node_ids: ["blank", "history"],
            pending_reference_nodes: [
                { schema_version: 1, source_node_id: "blank", source_kind: "image", status: "pending_media" },
                { schema_version: 1, source_node_id: "history", source_kind: "image", status: "pending_media" },
            ],
        };
        render(<ReferenceControls {...props} />);
        expect(screen.getAllByText("Pending media")).toHaveLength(2);
        expect(screen.queryByRole("button", { name: /Use current media/ })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Remove Blank image" }));
        expect(props.onDetach).toHaveBeenCalledWith({ sourceNodeId: "blank" });
        expect(props.onAttach).not.toHaveBeenCalled();
    });

    it("confirms the selected current video candidate for pending video references", () => {
        const props = setup();
        props.project.nodes.push({
            ...props.project.nodes[3],
            id: "video-source",
            title: "Video source",
            media_urls: ["uploads/stale-video.mp4"],
            data: {
                selected_candidate_id: "take-a",
                candidates: [
                    { id: "take-a", status: "completed", media_url: "uploads/current-video.mp4" },
                    { id: "take-b", status: "completed", media_url: "uploads/current-video.mp4" },
                ],
            },
        });
        props.project.nodes[0].data = {
            reference_node_ids: ["video-source"],
            pending_reference_nodes: [{ schema_version: 1, source_node_id: "video-source", source_kind: "video", status: "pending_media" }],
        };
        render(<ReferenceControls {...props} />);
        fireEvent.click(screen.getByRole("button", { name: "Use current media Video source" }));
        expect(props.onAttach).toHaveBeenCalledWith({ sourceNodeId: "video-source" });
    });

    it("shows pending references and attaches blank compatible sources through the pending command", () => {
        const props = setup();
        props.project.nodes.push({ ...props.project.nodes[1], id: "blank", title: "Blank image", media_urls: [], data: {} });
        props.project.nodes.push({ ...props.project.nodes[1], id: "future", title: "Future image", media_urls: [], data: {} });
        props.project.nodes.push({ ...props.project.nodes[1], id: "history", title: "Historical image", media_urls: [], data: { deleted_candidates: [{ id: "old", status: "completed", media_url: "uploads/old.png" }] } });
        props.project.nodes[0].data = {
            reference_node_ids: ["blank"],
            pending_reference_nodes: [{ schema_version: 1, source_node_id: "blank", source_kind: "image", status: "pending_media" }],
        };
        render(<ReferenceControls {...props} />);
        expect(screen.getByText("Pending media")).toBeInTheDocument();
        expect(screen.getByText("Blank image")).toBeInTheDocument();
        expect(within(screen.getByRole("combobox")).queryByRole("option", { name: "Historical image" })).not.toBeInTheDocument();
        fireEvent.change(screen.getByRole("combobox"), { target: { value: "audio" } });
        fireEvent.click(screen.getByRole("button", { name: "Attach" }));
        expect(props.onAttach).toHaveBeenCalledWith({ sourceNodeId: "audio" });
        fireEvent.change(screen.getByRole("combobox"), { target: { value: "future" } });
        fireEvent.click(screen.getByRole("button", { name: "Attach as pending" }));
        expect(props.onAttachPending).toHaveBeenCalledWith({ sourceNodeId: "future" });
        expect(props.project.nodes[0].data.reference_image_urls).toBeUndefined();
    });

    it("allows detaching an unowned legacy URL without inventing a source id", () => {
        const props = setup();
        props.project.nodes[0].data = { reference_image_urls: ["uploads/legacy.png"] };
        render(<ReferenceControls {...props} />);
        fireEvent.click(screen.getByRole("button", { name: "Remove uploads/legacy.png" }));
        expect(props.onDetach).toHaveBeenCalledWith({ referenceUrl: "uploads/legacy.png" });
    });

    it("can remove a source relationship after its media has disappeared", () => {
        const props = setup();
        props.project.nodes[0].data = { reference_node_ids: ["image"], reference_image_urls: ["uploads/old.png"] };
        props.project.nodes[1].media_urls = [];
        render(<ReferenceControls {...props} />);
        fireEvent.click(screen.getByRole("button", { name: "Remove Image" }));
        expect(props.onDetach).toHaveBeenCalledWith({ sourceNodeId: "image" });
    });
});
