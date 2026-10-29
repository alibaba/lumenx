// @vitest-environment happy-dom
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { ExportsPanel } from "@/components/atelier/v3/ExportsPanel";
import { SequenceStrip, type SequenceClipView } from "@/components/atelier/v3/SequenceStrip";
import type { AtelierExportRecord, AtelierSequenceEntry } from "@/lib/atelierTypes";

function entry(overrides: Partial<AtelierSequenceEntry> = {}): AtelierSequenceEntry {
    return {
        parentId: "video-1",
        candidateId: null,
        id: "clip-1",
        trimStart: null,
        trimEnd: null,
        gain: 1,
        transition: { kind: "cut", duration: 0 },
        ...overrides,
    };
}

function clip(overrides: Partial<SequenceClipView> = {}): SequenceClipView {
    const value = overrides.entry ?? entry();
    return {
        entry: value,
        title: value.id ?? value.parentId,
        subtitle: value.parentId,
        mediaUrl: "uploads/clip.mp4",
        ...overrides,
    };
}

function renderStrip(props: Partial<React.ComponentProps<typeof SequenceStrip>> = {}) {
    return render(
        <SequenceStrip
            entries={[clip()]}
            onReorder={vi.fn()}
            onTrim={vi.fn()}
            onRemove={vi.fn()}
            onExport={vi.fn()}
            resolveAssetUrl={(url) => (url ? `https://media.example/${url}` : "")}
            {...props}
        />,
    );
}

describe("SequenceStrip presentation", () => {
    it("keeps unavailable raw entries visible and disables export with a reason", () => {
        const onExport = vi.fn();
        renderStrip({
            entries: [clip({ unavailableReason: "Source take was deleted" })],
            onExport,
        });

        expect(screen.getAllByText("Source take was deleted")).toHaveLength(2);
        const button = screen.getByRole("button", { name: "Export" });
        expect(button).toBeDisabled();
        expect(screen.getByText("Resolve unavailable clips before exporting.")).toBeInTheDocument();
        fireEvent.click(button);
        expect(onExport).not.toHaveBeenCalled();
    });

    it("reorders with keyboard and keeps repeated material clip ids unique", () => {
        const onReorder = vi.fn();
        const onRemove = vi.fn();
        const repeated = [
            clip({ entry: entry({ id: null, parentId: "video-repeat", candidateId: null }), title: "Repeat A" }),
            clip({ entry: entry({ id: null, parentId: "video-repeat", candidateId: null }), title: "Repeat B" }),
        ];
        renderStrip({ entries: repeated, onReorder, onRemove });

        fireEvent.keyDown(screen.getByLabelText("Repeat A 1"), { key: "ArrowDown", altKey: true });
        expect(onReorder).toHaveBeenCalledTimes(1);
        expect(onReorder.mock.calls[0][0].map((item: AtelierSequenceEntry) => item.parentId)).toEqual([
            "video-repeat",
            "video-repeat",
        ]);
        expect(onReorder.mock.calls[0][1]).toMatchObject({ fromIndex: 0, toIndex: 1, clipId: "video-repeat:uploaded:0" });

        fireEvent.click(screen.getByRole("button", { name: "Remove 2" }));
        expect(onRemove).toHaveBeenCalledWith("video-repeat:uploaded:1");
    });

    it("applies trim once and cancel leaves parent untouched", () => {
        const onTrim = vi.fn();
        renderStrip({ entries: [clip({ entry: entry({ trimStart: 0.1, trimEnd: 0.8 }) })], onTrim });

        fireEvent.click(screen.getByRole("button", { name: "Trim 1" }));
        fireEvent.change(screen.getByLabelText("Start"), { target: { value: "0.2" } });
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        expect(onTrim).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole("button", { name: "Trim 1" }));
        fireEvent.change(screen.getByLabelText("Start"), { target: { value: "0.3" } });
        fireEvent.change(screen.getByLabelText("End"), { target: { value: "0.7" } });
        fireEvent.click(screen.getByRole("button", { name: "Apply trim" }));
        expect(onTrim).toHaveBeenCalledTimes(1);
        expect(onTrim).toHaveBeenCalledWith("clip-1", { trimStart: 0.3, trimEnd: 0.7 });
    });

    it("keeps invalid trim text visible instead of changing it to an untrimmed clip", () => {
        const onTrim = vi.fn();
        renderStrip({ onTrim });
        fireEvent.click(screen.getByRole("button", { name: "Trim 1" }));
        for (const value of ["oops", "-1", "Infinity"]) {
            fireEvent.change(screen.getByLabelText("Start"), { target: { value } });
            expect(screen.getByRole("button", { name: "Apply trim" })).toBeDisabled();
            expect(screen.getByLabelText("Start")).toHaveValue(value);
        }
        fireEvent.change(screen.getByLabelText("Start"), { target: { value: "2" } });
        fireEvent.change(screen.getByLabelText("End"), { target: { value: "1" } });
        expect(screen.getByRole("button", { name: "Apply trim" })).toBeDisabled();
        expect(onTrim).not.toHaveBeenCalled();
    });

    it("cannot override a busy export guard with an empty message", () => {
        renderStrip({ exportState: { status: "exporting" }, exportDisabledReason: "" });
        expect(screen.getByRole("button", { name: "Export" })).toBeDisabled();
    });

    it("strictly parses atelier take drops and ignores untrusted payload types", () => {
        const onInsertFromDrop = vi.fn();
        renderStrip({ onInsertFromDrop });
        const item = screen.getByRole("listitem");

        fireEvent.drop(item, {
            dataTransfer: { types: ["text/plain"], getData: () => JSON.stringify({ parentId: "bad" }) },
        });
        fireEvent.drop(item, {
            dataTransfer: { types: ["application/x-atelier-take"], getData: () => "not-json" },
        });
        expect(onInsertFromDrop).not.toHaveBeenCalled();

        fireEvent.drop(item, {
            dataTransfer: {
                types: ["application/x-atelier-take"],
                getData: () => JSON.stringify({ parentId: "video-2", candidateId: null, id: "clip-2" }),
            },
        });
        expect(onInsertFromDrop).toHaveBeenCalledWith(
            expect.objectContaining({ parentId: "video-2", candidateId: null, id: "clip-2" }),
            { index: 0 },
        );
    });

    it("accepts the first clip dropped onto an empty sequence", () => {
        const onInsertFromDrop = vi.fn();
        renderStrip({ entries: [], onInsertFromDrop });
        fireEvent.drop(screen.getByText("Add completed clips to build an export timeline."), {
            dataTransfer: { types: ["application/x-atelier-take"], getData: () => JSON.stringify({ parentId: "video-1", candidateId: null }) },
        });
        expect(onInsertFromDrop).toHaveBeenCalledWith(expect.objectContaining({ parentId: "video-1" }), { index: 0 });
    });

    it("surfaces busy error repair and resume callbacks without internal polling", () => {
        const onCancel = vi.fn();
        const onRepair = vi.fn();
        const onResume = vi.fn();
        const { rerender } = renderStrip({
            exportState: { status: "exporting", progressLabel: "Rendering" },
            onCancel,
            onRepair,
            onResume,
        });
        expect(screen.getByText("Rendering")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        expect(onCancel).toHaveBeenCalledTimes(1);

        rerender(
            <SequenceStrip
                entries={[clip()]}
                exportState={{ status: "repair_required", readableError: "File needs repair" }}
                onReorder={vi.fn()}
                onTrim={vi.fn()}
                onRemove={vi.fn()}
                onExport={vi.fn()}
                onRepair={onRepair}
                onResume={onResume}
            />,
        );
        expect(screen.getByText("File needs repair")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Repair" }));
        expect(onRepair).toHaveBeenCalledTimes(1);

        rerender(
            <SequenceStrip
                entries={[clip()]}
                exportState={{ status: "failed", readableError: "Render failed" }}
                onReorder={vi.fn()}
                onTrim={vi.fn()}
                onRemove={vi.fn()}
                onExport={vi.fn()}
                onResume={onResume}
            />,
        );
        fireEvent.click(screen.getByRole("button", { name: "Resume" }));
        expect(onResume).toHaveBeenCalledTimes(1);
    });
});

function exportRecord(overrides: Partial<AtelierExportRecord> = {}): AtelierExportRecord {
    return {
        id: "export-1",
        project_id: "p1",
        created_at: 10,
        clip_count: 2,
        size_mb: 1.5,
        video_url: "video/out.mp4",
        filename: "out.mp4",
        source_entries: [entry()],
        timeline_snapshot: { schema_version: "atelier.timeline.v1", clips: [] },
        ...overrides,
    };
}

describe("ExportsPanel presentation", () => {
    it("reports a failed injected copy action", async () => {
        render(<ExportsPanel records={[exportRecord()]} onCopy={async () => { throw new Error("Clipboard blocked"); }} />);
        fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
        expect(await screen.findByRole("alert")).toHaveTextContent("Clipboard blocked");
    });
    it("shows loading and error states", () => {
        const { rerender } = render(<ExportsPanel records={[]} loading />);
        expect(screen.getByRole("status")).toHaveTextContent("Loading exports");

        rerender(<ExportsPanel records={[]} error="Storage unavailable" />);
        expect(screen.getByRole("alert")).toHaveTextContent("Storage unavailable");
    });

    it("renders safe history actions and only shows redo/delete when wired", () => {
        const onCopy = vi.fn();
        const record = exportRecord();
        const { rerender } = render(
            <ExportsPanel
                records={[record]}
                onCopy={onCopy}
                resolveAssetUrl={(url) => (url ? `https://media.example/${url}` : "")}
            />,
        );

        const row = screen.getByRole("listitem");
        expect(within(row).getByText("out.mp4")).toBeInTheDocument();
        expect(within(row).getByRole("link", { name: "Download" })).toHaveAttribute(
            "href",
            "https://media.example/video/out.mp4",
        );
        expect(within(row).queryByRole("button", { name: "Redo" })).not.toBeInTheDocument();
        expect(within(row).queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
        fireEvent.click(within(row).getByRole("button", { name: "Copy link" }));
        expect(onCopy).toHaveBeenCalledWith(record, "https://media.example/video/out.mp4");

        const onRedo = vi.fn();
        const onDelete = vi.fn();
        rerender(
            <ExportsPanel
                records={[record]}
                onCopy={onCopy}
                onRedo={onRedo}
                onDelete={onDelete}
                resolveAssetUrl={(url) => (url ? `https://media.example/${url}` : "")}
            />,
        );
        fireEvent.click(screen.getByRole("button", { name: "Redo" }));
        fireEvent.click(screen.getByRole("button", { name: "Delete" }));
        expect(onRedo).toHaveBeenCalledWith(record);
        expect(onDelete).toHaveBeenCalledWith(record);
    });

    it("localizes clip count, formats size, and explains exports that cannot be redone", () => {
        const onRedo = vi.fn();
        render(
            <ExportsPanel
                records={[exportRecord({ clip_count: 1, size_mb: 0.00936126708984375, timeline_snapshot: null })]}
                labels={{
                    redoUnavailable: "This export cannot be redone.",
                    clipCount: (count) => `${count} clip${count === 1 ? "" : "s"}`,
                }}
                onRedo={onRedo}
            />,
        );

        expect(screen.getByText("1 clip")).toBeInTheDocument();
        expect(screen.getByText(/0\.01 MB/)).toBeInTheDocument();
        expect(screen.getByText("This export cannot be redone.")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Redo" })).not.toBeInTheDocument();
    });

    it("does not expose unsafe export URLs", () => {
        render(<ExportsPanel records={[exportRecord({ video_url: "javascript:alert(1)" })]} resolveAssetUrl={(url) => url ?? ""} />);
        const download = screen.getByText("Download");
        expect(download).not.toHaveAttribute("href", "javascript:alert(1)");
        expect(download).toHaveAttribute("aria-disabled", "true");
    });
});
