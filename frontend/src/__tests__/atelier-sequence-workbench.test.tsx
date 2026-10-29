// @vitest-environment happy-dom
import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import messages from "../../messages/zh.json";
import { SequenceWorkbench } from "@/components/atelier/v3/SequenceWorkbench";
import { parseAtelierProject } from "@/lib/atelierApi";
import fixture from "./fixtures/atelier-project.json";

const store = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
const preflight = vi.hoisted(() => vi.fn());
vi.mock("@/store/atelierStore", () => ({ useAtelierStore: (selector: (state: Record<string, unknown>) => unknown) => selector(store.state) }));
vi.mock("@/lib/atelierSequenceApi", () => ({ atelierSequenceApi: { preflight } }));
const owner = { apiBase: "http://localhost:17177", storageId: "test-storage", projectId: fixture.id };
function project() {
    const p = parseAtelierProject(structuredClone(fixture)); p.sequence = []; p.exports = []; p.sequence_export_jobs = [];
    p.nodes = [{ ...p.nodes[0], id: "video", project_id: p.id, type: "video", title: "My clip", media_urls: ["uploads/video.mp4"], data: {} }];
    return p;
}
function mount(p = project()) {
    return render(<NextIntlClientProvider locale="zh" messages={messages}><SequenceWorkbench project={p} selectedNode={p.nodes[0]} owner={owner} /></NextIntlClientProvider>);
}
beforeEach(() => {
    localStorage.clear();
    preflight.mockReset().mockResolvedValue({ project_id: fixture.id, revision: project().revision, report: { errors: [], warnings: [] } });
    store.state = { pendingExports: [], pendingExportDeletions: [], sequenceExportJobs: [], needsRefresh: false, isSaving: false,
        loadPendingExports: vi.fn().mockReturnValue([]), loadPendingExportDeletions: vi.fn().mockReturnValue([]), refreshSequenceExport: vi.fn(), refreshProject: vi.fn().mockResolvedValue(project()),
        saveSequence: vi.fn().mockResolvedValue(project()), createSequenceExport: vi.fn().mockResolvedValue({}), redoSequenceExport: vi.fn().mockResolvedValue({}), deleteSequenceExport: vi.fn().mockResolvedValue({}),
        readbackPendingExport: vi.fn().mockResolvedValue(null), readbackPendingExportDeletion: vi.fn().mockResolvedValue(null), cancelSequenceExport: vi.fn(), repairSequenceExport: vi.fn(), resumeSequenceExport: vi.fn() };
});

describe("Atelier sequence workbench", () => {
    it("shows Core preflight failures without saving or submitting an export", async () => {
        preflight.mockResolvedValue({ project_id: fixture.id, revision: project().revision, report: { errors: [{ code: "invalid_trim", message: "Clip ends beyond its media duration", clip_id: "clip" }], warnings: [] } });
        mount();
        fireEvent.click(await screen.findByRole("button", { name: "加入当前视频" }));
        fireEvent.click(screen.getByRole("button", { name: "保存并导出" }));
        expect(await screen.findByRole("alert")).toHaveTextContent("Clip ends beyond its media duration");
        expect(store.state.saveSequence).not.toHaveBeenCalled();
        expect(store.state.createSequenceExport).not.toHaveBeenCalled();
    });
    it("waits for the saved sequence acknowledgement before admitting an export", async () => {
        let finish!: (value: ReturnType<typeof project>) => void;
        store.state.saveSequence = vi.fn(() => new Promise(resolve => { finish = resolve; }));
        mount();
        fireEvent.click(await screen.findByRole("button", { name: "加入当前视频" }));
        fireEvent.click(screen.getByRole("button", { name: "保存并导出" }));
        await waitFor(() => expect(store.state.saveSequence).toHaveBeenCalledTimes(1));
        expect(store.state.createSequenceExport).not.toHaveBeenCalled();
        await act(async () => finish(project()));
        await waitFor(() => expect(store.state.createSequenceExport).toHaveBeenCalledTimes(1));
    });

    it("keeps a missing source visible and blocks export", async () => {
        const p = project(); p.sequence = [{ id: "missing", parentId: "deleted", candidateId: null, gain: 1, transition: { kind: "cut", duration: 0 } }];
        mount(p);
        expect(await screen.findAllByText("来源节点已删除")).not.toHaveLength(0);
        expect(screen.getByRole("button", { name: "导出" })).toBeDisabled();
        expect(store.state.createSequenceExport).not.toHaveBeenCalled();
    });

    it("does not start an export after its owner unmounts during save", async () => {
        let finish!: (value: ReturnType<typeof project>) => void;
        store.state.saveSequence = vi.fn(() => new Promise(resolve => { finish = resolve; }));
        const view = mount();
        fireEvent.click(await screen.findByRole("button", { name: "加入当前视频" }));
        fireEvent.click(screen.getByRole("button", { name: "保存并导出" }));
        await waitFor(() => expect(store.state.saveSequence).toHaveBeenCalled());
        view.unmount(); await act(async () => finish(project()));
        expect(store.state.createSequenceExport).not.toHaveBeenCalled();
    });

    it("reads an unknown original request without submitting a replacement", async () => {
        store.state.pendingExports = [{ clientRequestId: "original" }];
        mount();
        fireEvent.click(await screen.findByRole("button", { name: "查询原请求" }));
        await waitFor(() => expect(store.state.readbackPendingExport).toHaveBeenCalledWith("original"));
        expect(await screen.findByText("尚未确认结果，原请求已保留。请稍后再次查询。")).toBeInTheDocument();
        expect(store.state.createSequenceExport).not.toHaveBeenCalled();
    });

    it("restarts a history export without saving the current draft sequence", async () => {
        const p = project();
        p.exports = [{
            id: "export-1",
            project_id: p.id,
            created_at: 10,
            clip_count: 1,
            size_mb: 0.00936126708984375,
            video_url: "video/out.mp4",
            filename: "out.mp4",
            source_entries: [],
            timeline_snapshot: { schema_version: "atelier.timeline.v1", clips: [] },
        }];
        mount(p);

        fireEvent.click(await screen.findByRole("button", { name: "加入当前视频" }));
        fireEvent.click(screen.getByRole("button", { name: "再次导出" }));

        await waitFor(() => expect(store.state.redoSequenceExport).toHaveBeenCalledWith(p.exports[0]));
        expect(store.state.saveSequence).not.toHaveBeenCalled();
        expect(store.state.createSequenceExport).not.toHaveBeenCalled();
    });

    it("confirms history deletion, hides deleted tombstones, and keeps blocked tombstones retryable", async () => {
        const p = project();
        const active = {
            id: "export-active",
            project_id: p.id,
            created_at: 10,
            clip_count: 1,
            size_mb: 1,
            video_url: "video/active.mp4",
            filename: "active.mp4",
            source_entries: [],
            timeline_snapshot: { schema_version: "atelier.timeline.v1", clips: [] },
        };
        const deleted = { ...active, id: "export-deleted", filename: "deleted.mp4", deletion: {
            client_request_id: "delete-done", delete_file: true, deleted_at: 20, file_status: "deleted", checked_at: 21, error_code: null, error_message: null,
        } };
        const blocked = { ...active, id: "export-blocked", filename: "blocked.mp4", deletion: {
            client_request_id: "delete-blocked", delete_file: true, deleted_at: 22, file_status: "blocked", checked_at: 23, error_code: "export_file_still_referenced", error_message: "File still referenced",
        } };
        p.exports = [active, deleted, blocked] as never;
        const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
        mount(p);

        expect(screen.queryByText("deleted.mp4")).not.toBeInTheDocument();
        expect(await screen.findByText("blocked.mp4")).toBeInTheDocument();
        expect(screen.getByText("File still referenced")).toBeInTheDocument();
        fireEvent.click(screen.getAllByRole("button", { name: "删除" })[0]);
        expect(store.state.deleteSequenceExport).not.toHaveBeenCalled();
        fireEvent.click(screen.getAllByRole("button", { name: "删除" })[0]);
        await waitFor(() => expect(store.state.deleteSequenceExport).toHaveBeenCalledWith(active, true));
        expect(confirm).toHaveBeenCalledWith(expect.stringContaining("删除历史记录并尝试删除成片文件"));

        fireEvent.click(screen.getByRole("button", { name: "重试删除文件" }));
        await waitFor(() => expect(store.state.deleteSequenceExport).toHaveBeenCalledWith(blocked, true));
    });

    it("separates deletion status from redo availability for blocked and pending tombstones", async () => {
        const p = project();
        const base = {
            id: "export-base",
            project_id: p.id,
            created_at: 10,
            clip_count: 1,
            size_mb: 1,
            video_url: "video/base.mp4",
            filename: "base.mp4",
            source_entries: [],
            timeline_snapshot: { schema_version: "atelier.timeline.v1", clips: [] },
        };
        const blocked = { ...base, id: "export-blocked", filename: "blocked.mp4", deletion: {
            client_request_id: "delete-blocked", delete_file: true, deleted_at: 22, file_status: "blocked", checked_at: 23, error_code: "media_proof_mismatch", error_message: "media_proof_mismatch",
        } };
        const pending = { ...base, id: "export-pending", filename: "pending.mp4", deletion: {
            client_request_id: "delete-pending", delete_file: true, deleted_at: 24, file_status: "pending", checked_at: null, error_code: null, error_message: null,
        } };
        p.exports = [blocked, pending] as never;
        mount(p);

        expect(await screen.findByText("blocked.mp4")).toBeInTheDocument();
        expect(screen.getByText("media_proof_mismatch")).toBeInTheDocument();
        expect(screen.getAllByText("历史删除结果待确认，原请求已保留。")).toHaveLength(1);
        expect(screen.queryByRole("button", { name: "再次导出" })).not.toBeInTheDocument();

        fireEvent.click(screen.getAllByRole("button", { name: "重试删除文件" })[0]);
        await waitFor(() => expect(store.state.deleteSequenceExport).toHaveBeenCalledWith(blocked, true));
    });

    it("disables history redo and delete while an export operation is pending but keeps original readback available", async () => {
        const p = project();
        p.exports = [{
            id: "export-1",
            project_id: p.id,
            created_at: 10,
            clip_count: 1,
            size_mb: 1,
            video_url: "video/out.mp4",
            filename: "out.mp4",
            source_entries: [],
            timeline_snapshot: { schema_version: "atelier.timeline.v1", clips: [] },
        }];
        store.state.pendingExports = [{ clientRequestId: "original" }];
        mount(p);

        expect(await screen.findByRole("button", { name: "查询原请求" })).not.toBeDisabled();
        expect(screen.getByRole("button", { name: "再次导出" })).toBeDisabled();
        expect(screen.getByRole("button", { name: "删除" })).toBeDisabled();
        expect(screen.getByRole("link", { name: "下载" })).toHaveAttribute("href");
        expect(screen.getByRole("button", { name: "复制链接" })).not.toBeDisabled();
    });

    it("adds an unselected video candidate to the draft without selecting it or changing judgments", async () => {
        const p = project();
        p.candidate_judgments = [{
            schema_version: 1,
            id: "judgment-b",
            project_id: p.id,
            node_id: "video",
            candidate_id: "take-b",
            media_kind: "video",
            verdict: "keep",
            reason: "Use in sequence only",
            created_at: 10,
        }];
        p.nodes[0].data = { selected_candidate_id: "take-a", candidates: [
            { id: "take-a", status: "completed", media_kind: "video", video_url: "video/a.mp4" },
            { id: "take-b", status: "completed", media_kind: "video", video_url: "video/b.mp4" },
        ] };
        store.state.saveSequence = vi.fn().mockImplementation((entries: unknown[]) => Promise.resolve({ ...p, sequence: entries }));
        mount(p);

        fireEvent.change(await screen.findByLabelText("选择候选视频"), { target: { value: "take-b" } });
        fireEvent.click(screen.getByRole("button", { name: "将此候选加入序列" }));
        fireEvent.click(screen.getByRole("button", { name: "保存序列" }));

        await waitFor(() => expect(store.state.saveSequence).toHaveBeenCalledWith([expect.objectContaining({
            parentId: "video",
            candidateId: "take-b",
        })], p.revision));
        expect(p.nodes[0].data.selected_candidate_id).toBe("take-a");
        expect(p.candidate_judgments).toEqual([expect.objectContaining({ candidate_id: "take-b", verdict: "keep" })]);
    });

    it("resets the candidate picker when the selected node changes or the choice disappears", async () => {
        const p = project();
        p.nodes[0].data = { selected_candidate_id: "take-a", candidates: [
            { id: "take-a", status: "completed", media_kind: "video", video_url: "video/a.mp4" },
            { id: "take-b", status: "completed", media_kind: "video", video_url: "video/b.mp4" },
        ] };
        const view = mount(p);
        fireEvent.change(await screen.findByLabelText("选择候选视频"), { target: { value: "take-b" } });

        const next = project();
        next.nodes[0] = { ...next.nodes[0], id: "video-next", data: { selected_candidate_id: "take-c", candidates: [
            { id: "take-c", status: "completed", media_kind: "video", video_url: "video/c.mp4" },
        ] } };
        view.rerender(<NextIntlClientProvider locale="zh" messages={messages}><SequenceWorkbench project={next} selectedNode={next.nodes[0]} owner={owner} /></NextIntlClientProvider>);

        expect(screen.getByLabelText("选择候选视频")).toHaveValue("take-c");
        fireEvent.click(screen.getByRole("button", { name: "将此候选加入序列" }));
        fireEvent.click(screen.getByRole("button", { name: "保存序列" }));
        await waitFor(() => expect(store.state.saveSequence).toHaveBeenCalledWith([expect.objectContaining({
            parentId: "video-next",
            candidateId: "take-c",
        })], next.revision));
        expect(store.state.saveSequence).not.toHaveBeenCalledWith(expect.arrayContaining([expect.objectContaining({ candidateId: "take-b" })]), expect.anything());
    });

});
