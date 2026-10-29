// @vitest-environment happy-dom
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { AgentTurnPanel } from "@/components/atelier/v3/AgentTurnPanel";
import type { AtelierProject } from "@/lib/atelierTypes";
import messages from "../../messages/en.json";
import agentAcks from "./fixtures/atelier-agent-turn-acks.json";
import loopWire from "./fixtures/atelier-agent-loop-http-wire.json";
import productSkillWire from "./fixtures/atelier-product-skills-wire.json";

const productSkillApi = vi.hoisted(() => ({ listProductSkills: vi.fn() }));
const store = vi.hoisted(() => ({
    startAgentTurn: vi.fn(),
    startAgentLoop: vi.fn(),
    loadPendingAgentTurns: vi.fn(),
    readbackPendingAgentTurn: vi.fn(),
    retryPendingAgentTurn: vi.fn(),
    approveAgentCall: vi.fn(),
    denyAgentCall: vi.fn(),
    revokeAgentCall: vi.fn(),
    continueAgentCall: vi.fn(),
    recordAgentLoopCanvasApproval: vi.fn(),
    resumeAgentLoop: vi.fn(),
    refreshAgentTurn: vi.fn(),
    refreshAgentLoop: vi.fn(),
    cancelAgentTurn: vi.fn(),
    pendingAgentTurns: [] as unknown[],
    isSaving: false,
    needsRefresh: false,
}));
vi.mock("@/store/atelierStore", () => ({ useAtelierStore: (selector: (state: typeof store) => unknown) => selector(store) }));
vi.mock("@/lib/atelierAgentLoopApi", () => ({ atelierAgentLoopApi: productSkillApi }));

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const projectFrom = (name: keyof typeof agentAcks): AtelierProject => clone(agentAcks[name].project) as unknown as AtelierProject;
const generationCallId = (project: AtelierProject, turnId: string) => {
    const turn = project.agent_turns.find(item => item.id === turnId) as Record<string, unknown> | undefined;
    const call = (turn?.tool_calls as Record<string, unknown>[] | undefined)?.find(item => item.tool_name === "generation.createVideoCandidates");
    if (typeof call?.call_id !== "string") throw new Error("fixture missing generation call");
    return call.call_id;
};

function renderPanel(project = projectFrom("started"), selectedNode = null as AtelierProject["nodes"][number] | null) {
    return render(
        <NextIntlClientProvider locale="en" timeZone="Asia/Shanghai" messages={messages}>
            <AgentTurnPanel project={project} selectedNode={selectedNode} ownerKey="owner" disabled={false} />
        </NextIntlClientProvider>,
    );
}

beforeEach(() => {
    vi.clearAllMocks();
    store.pendingAgentTurns = [];
    store.isSaving = false;
    store.needsRefresh = false;
    store.startAgentTurn.mockResolvedValue(agentAcks.started);
    store.startAgentLoop.mockResolvedValue(loopWire.start_stream.at(-1)!.ack);
    store.loadPendingAgentTurns.mockReturnValue([]);
    store.readbackPendingAgentTurn.mockResolvedValue(agentAcks.started);
    store.retryPendingAgentTurn.mockResolvedValue(agentAcks.started);
    store.approveAgentCall.mockResolvedValue(agentAcks.approved);
    store.denyAgentCall.mockResolvedValue(agentAcks.denied);
    store.revokeAgentCall.mockResolvedValue(agentAcks.revoked);
    store.continueAgentCall.mockResolvedValue(agentAcks.admitted);
    store.recordAgentLoopCanvasApproval.mockResolvedValue(loopWire.canvas_approval_ack);
    store.resumeAgentLoop.mockResolvedValue(loopWire.canvas_resume_ack);
    store.refreshAgentTurn.mockResolvedValue(agentAcks.approved);
    store.refreshAgentLoop.mockResolvedValue(loopWire.canvas_approval_ack);
    store.cancelAgentTurn.mockResolvedValue(agentAcks.canceled);
    productSkillApi.listProductSkills.mockResolvedValue([productSkillWire.catalog[0], {
        schema_version: 1, name: "shot-builder", version: "1.0.0", title: "Shot Builder",
        description: "Unavailable recipe.", category: "shot", expected_tools: ["canvas.createVideoNode"],
        default_iteration_cap: 1, content_hash: "b".repeat(64), enabled: false, disabled_reason: "requires missing tools",
    }]);
});

describe("AgentTurnPanel", () => {
    it("starts a guided run and keeps the one-step proposal entrypoint", async () => {
        renderPanel(projectFrom("started"), null);
        fireEvent.change(screen.getByRole("textbox", { name: "Agent request" }), { target: { value: "Prepare a take" } });
        fireEvent.click(screen.getByRole("button", { name: "Start guided run" }));
        await waitFor(() => expect(store.startAgentLoop).toHaveBeenCalledWith(
            { userMessage: "Prepare a take", selectedNodeId: null },
            expect.objectContaining({ transport: "stream", signal: expect.any(AbortSignal), onEvent: expect.any(Function) }),
        ));

        fireEvent.click(screen.getByRole("button", { name: "Start Agent turn" }));
        await waitFor(() => expect(store.startAgentTurn).toHaveBeenCalled());
    });

    it("starts an explicit model-planning guided run with a cost warning", async () => {
        renderPanel(projectFrom("started"), null);
        fireEvent.change(screen.getByRole("textbox", { name: "Agent request" }), { target: { value: "Prepare with model" } });
        expect(screen.getByText(/configured text model/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Start with model planning" }));
        await waitFor(() => expect(store.startAgentLoop).toHaveBeenCalledWith(
            { userMessage: "Prepare with model", selectedNodeId: null, planner: "model_adapter" },
            expect.objectContaining({ transport: "stream", signal: expect.any(AbortSignal), onEvent: expect.any(Function) }),
        ));
    });

    it("starts enabled Product Skill cards as name-only model_adapter loops and keeps disabled cards inert", async () => {
        renderPanel(projectFrom("started"), null);
        fireEvent.change(screen.getByRole("textbox", { name: "Agent request" }), { target: { value: "Scout this canvas" } });
        expect(await screen.findByRole("button", { name: /Story Scout/ })).toBeEnabled();
        expect(screen.getByRole("button", { name: /Shot Builder/ })).toBeDisabled();

        fireEvent.click(screen.getByRole("button", { name: /Story Scout/ }));
        await waitFor(() => expect(store.startAgentLoop).toHaveBeenCalledWith(
            { userMessage: "Scout this canvas", selectedNodeId: null, planner: "model_adapter", skillName: "story-scout" },
            expect.objectContaining({ transport: "stream", signal: expect.any(AbortSignal), onEvent: expect.any(Function) }),
        ));
        expect(JSON.stringify(store.startAgentLoop.mock.calls[0][0])).not.toContain("prompt_template");
        expect(store.startAgentLoop).toHaveBeenCalledTimes(1);
        fireEvent.click(screen.getByRole("button", { name: /Shot Builder/ }));
        expect(store.startAgentLoop).toHaveBeenCalledTimes(1);
    });

    it("does not show Product Skill starts until the app-owned catalog loads", async () => {
        productSkillApi.listProductSkills.mockResolvedValueOnce([]);
        renderPanel(projectFrom("started"), null);
        await waitFor(() => expect(productSkillApi.listProductSkills).toHaveBeenCalled());
        expect(screen.queryByRole("button", { name: /Story Scout/ })).not.toBeInTheDocument();
    });

    it("starts a project-level turn without requiring a selected node", async () => {
        renderPanel(projectFrom("started"), null);
        fireEvent.change(screen.getByRole("textbox", { name: "Agent request" }), { target: { value: "Prepare a take" } });
        fireEvent.click(screen.getByRole("button", { name: "Start Agent turn" }));
        await waitFor(() => expect(store.startAgentTurn).toHaveBeenCalledWith(
            { userMessage: "Prepare a take", selectedNodeId: null },
            expect.objectContaining({ transport: "stream", signal: expect.any(AbortSignal), onEvent: expect.any(Function) }),
        ));
    });

    it("explains a definite pre-save rejection without asking to recover a nonexistent turn", async () => {
        store.startAgentTurn.mockImplementationOnce((_input, options) => {
            options.onEvent({ type: "agent_error", project_id: "p", turn_id: "new", code: "atelier_agent_start_rejected", detail: "Agent stream stopped; read the saved turn before retrying" });
            return Promise.reject(Object.assign(new Error("Agent stream stopped; read the saved turn before retrying"), { code: "atelier_agent_start_rejected" }));
        });
        renderPanel(projectFrom("started"));
        fireEvent.change(screen.getByRole("textbox", { name: "Agent request" }), { target: { value: "Prepare a take" } });
        fireEvent.click(screen.getByRole("button", { name: "Start Agent turn" }));
        expect(await screen.findByRole("alert")).toHaveTextContent("The request could not be prepared. No turn was saved. Check the selected node and its references, then try again.");
        expect(screen.queryByText(/read the saved turn/)).not.toBeInTheDocument();
    });

    it("shows stream progress locally and aborts the stream on unmount", async () => {
        let options: { signal: AbortSignal; onEvent: (event: { type: string; project_id: string; turn_id: string | null; message: string | null }) => void } | undefined;
        store.startAgentTurn.mockImplementationOnce((_input, streamOptions) => {
            options = streamOptions;
            streamOptions.onEvent({ type: "turn_planning_started", project_id: "p", turn_id: "turn-1", message: null });
            return new Promise(() => undefined);
        });
        const { unmount } = renderPanel(projectFrom("started"), null);

        fireEvent.change(screen.getByRole("textbox", { name: "Agent request" }), { target: { value: "Prepare a take" } });
        fireEvent.click(screen.getByRole("button", { name: "Start Agent turn" }));

        expect(await screen.findByText("Planning the turn…")).toBeInTheDocument();
        expect(options?.signal.aborted).toBe(false);
        unmount();
        expect(options?.signal.aborted).toBe(true);
    });

    it("does not let an old stream settlement clear the active owner stream controller", async () => {
        let first: { signal: AbortSignal; onEvent: (event: { type: string; project_id: string; turn_id: string | null; message: string | null }) => void } | undefined;
        let rejectFirst!: (error: Error) => void;
        let second: { signal: AbortSignal; onEvent: (event: { type: string; project_id: string; turn_id: string | null; message: string | null }) => void } | undefined;
        store.startAgentTurn
            .mockImplementationOnce((_input, streamOptions) => {
                first = streamOptions;
                return new Promise((_resolve, reject) => { rejectFirst = reject; });
            })
            .mockImplementationOnce((_input, streamOptions) => {
                second = streamOptions;
                return new Promise(() => undefined);
            });
        const { rerender, unmount } = renderPanel(projectFrom("started"), null);

        fireEvent.change(screen.getByRole("textbox", { name: "Agent request" }), { target: { value: "First" } });
        fireEvent.click(screen.getByRole("button", { name: "Start Agent turn" }));
        await waitFor(() => expect(first).toBeDefined());
        rerender(<NextIntlClientProvider locale="en" timeZone="Asia/Shanghai" messages={messages}>
            <AgentTurnPanel project={projectFrom("started")} selectedNode={null} ownerKey="owner-2" disabled={false} />
        </NextIntlClientProvider>);
        expect(first?.signal.aborted).toBe(true);

        fireEvent.change(screen.getByRole("textbox", { name: "Agent request" }), { target: { value: "Second" } });
        fireEvent.click(screen.getByRole("button", { name: "Start Agent turn" }));
        await waitFor(() => expect(second).toBeDefined());
        rejectFirst(new Error("old failed"));
        await Promise.resolve();

        expect(second?.signal.aborted).toBe(false);
        unmount();
        expect(second?.signal.aborted).toBe(true);
    });

    it("shows lost-start readback and explicit same-request retry at project level", async () => {
        store.pendingAgentTurns = [{ clientTurnId: "agent-turn-1", request: { user_message: "Prepare" } }];
        renderPanel(projectFrom("started"), null);
        const block = screen.getByText(/previous Agent request/).closest("div")!;
        fireEvent.click(within(block).getByRole("button", { name: "Read original turn" }));
        await waitFor(() => expect(store.readbackPendingAgentTurn).toHaveBeenCalledWith("agent-turn-1"));
        fireEvent.click(within(block).getByRole("button", { name: "Retry original request" }));
        await waitFor(() => expect(store.retryPendingAgentTurn).toHaveBeenCalledWith("agent-turn-1"));
    });

    it("renders proposal essentials while keeping machine identity in collapsed details", () => {
        renderPanel(projectFrom("started"), null);
        expect(screen.getByText(/生成视频/)).toBeInTheDocument();
        expect(screen.getByText(/wan\/wan2.7-video#i2v/)).toBeInTheDocument();
        expect(screen.getByText(/duration: 5/)).toBeInTheDocument();
        expect(screen.getByText(/first_frame · image · 1ba05c4747… · 68 B/)).toBeInTheDocument();
        expect(screen.queryByText(/uploads\/frame.png/)).not.toBeInTheDocument();
        expect(screen.getByText("Unavailable before provider submission")).toBeInTheDocument();
        expect(screen.getByText("Technical details")).toBeInTheDocument();
        expect(screen.getByText(/1ba05c474761e0ddb53dce893d2883fa50d58905f57f1166d372968d5ec6567d/)).toBeInTheDocument();
    });

    it("keeps approve and paid continue as separate explicit actions", async () => {
        renderPanel(projectFrom("started"), null);
        fireEvent.click(screen.getByRole("button", { name: "Approve" }));
        await waitFor(() => expect(store.approveAgentCall).toHaveBeenCalledWith("turn-http-1", "840a7ca5162e4ba0af14937cb229632e"));
        expect(store.continueAgentCall).not.toHaveBeenCalled();

        renderPanel(projectFrom("approved"), null);
        fireEvent.click(screen.getByRole("button", { name: "Submit approved generation" }));
        await waitFor(() => expect(store.continueAgentCall).toHaveBeenCalledWith(
            "turn-http-1",
            "840a7ca5162e4ba0af14937cb229632e",
            expect.objectContaining({ transport: "stream", signal: expect.any(AbortSignal), onEvent: expect.any(Function) }),
        ));
    });



    it("offers approve and deny for guided generation proposals through the existing approval actions", async () => {
        const ack = loopWire.start_stream.at(-1)?.ack;
        if (!ack) throw new Error("loop fixture missing start ACK");
        const project = ack.project as unknown as AtelierProject;
        const callId = generationCallId(project, "loop-http-1");
        const first = renderPanel(project, null);

        fireEvent.click(screen.getByRole("button", { name: "Approve" }));
        await waitFor(() => expect(store.approveAgentCall).toHaveBeenCalledWith("loop-http-1", callId));
        expect(store.resumeAgentLoop).not.toHaveBeenCalled();

        first.unmount();
        renderPanel(project, null);
        fireEvent.click(screen.getByRole("button", { name: "Deny" }));
        await waitFor(() => expect(store.denyAgentCall).toHaveBeenCalledWith("loop-http-1", callId));
    });

    it("continues approved guided generation through loop resume and keeps revoke available before admission", async () => {
        const project = loopWire.approval.project as unknown as AtelierProject;
        const callId = generationCallId(project, "loop-http-1");
        const first = renderPanel(project, null);
        fireEvent.click(screen.getByRole("button", { name: "Continue guided run" }));
        await waitFor(() => expect(store.resumeAgentLoop).toHaveBeenCalledWith("loop-http-1", expect.objectContaining({ transport: "stream", signal: expect.any(AbortSignal), onEvent: expect.any(Function) })));
        expect(store.continueAgentCall).not.toHaveBeenCalled();

        first.unmount();
        renderPanel(project, null);
        fireEvent.click(screen.getByRole("button", { name: "Revoke approval" }));
        await waitFor(() => expect(store.revokeAgentCall).toHaveBeenCalledWith("loop-http-1", callId));
    });

    it("shows a guided run recovery card after generation is saved without a paid duplicate action", async () => {
        renderPanel(loopWire.readback.project as unknown as AtelierProject, null);
        expect(screen.getByText(/Generation attempt d90c0050457b4a1d97365a6931b3a4aa is saved/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Refresh turn" }));
        await waitFor(() => expect(store.refreshAgentLoop).toHaveBeenCalledWith("loop-http-1"));
        fireEvent.click(screen.getByRole("button", { name: "Continue guided run" }));
        await waitFor(() => expect(store.resumeAgentLoop).toHaveBeenCalledWith("loop-http-1", expect.objectContaining({ transport: "stream", signal: expect.any(AbortSignal), onEvent: expect.any(Function) })));
        expect(store.continueAgentCall).not.toHaveBeenCalled();
    });

    it("keeps no-call and unknown guided loop turns from exposing call execution controls", () => {
        const noCallProject = clone(loopWire.approval.project) as unknown as AtelierProject;
        const noCallTurn = noCallProject.agent_turns.find(turn => turn.id === "loop-http-1") as Record<string, unknown>;
        noCallTurn.tool_calls = [];
        const first = renderPanel(noCallProject, null);
        expect(screen.getByText(/Resume only continues the saved run/)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Continue guided run" })).toBeInTheDocument();

        first.unmount();
        const unknownStartAck = loopWire.start_stream.at(-1)?.ack;
        if (!unknownStartAck) throw new Error("loop fixture missing start ACK");
        const unknown = clone(unknownStartAck.project) as unknown as AtelierProject;
        const turn = unknown.agent_turns.find(item => item.id === "loop-http-1") as Record<string, unknown>;
        const call = (turn.tool_calls as Record<string, unknown>[]).find(item => item.tool_name === "generation.createVideoCandidates");
        if (!call) throw new Error("fixture missing generation call");
        call.tool_name = "canvas.unknownPending";
        renderPanel(unknown, null);
        expect(screen.getByText("Read-only: canvas.unknownPending")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Deny" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Continue guided run" })).not.toBeInTheDocument();
    });


    it.each(["completed", "failed", "canceled"])("keeps a %s guided turn visible when no tool was proposed", status => {
        const project = clone(loopWire.approval.project) as unknown as AtelierProject;
        const turn = project.agent_turns.find(item => item.id === "loop-http-1") as Record<string, unknown>;
        turn.status = status;
        turn.tool_calls = [];
        renderPanel(project, null);
        expect(screen.getByText("Guided run · loop-http-1")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Refresh turn" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Continue guided run" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Cancel turn" })).not.toBeInTheDocument();
    });

    it("shows a guided recovery card for completed canvas work that still needs another loop step", async () => {
        const project = clone(loopWire.canvas_resume_ack.project) as unknown as AtelierProject;
        const turn = project.agent_turns.find(item => item.id === "canvas-loop-1") as Record<string, unknown>;
        turn.status = "pending";
        renderPanel(project, null);
        const card = screen.getAllByText("Guided run · canvas-loop-1")
            .map(element => element.closest("div.space-y-2"))
            .find((element): element is HTMLElement => element instanceof HTMLElement && within(element).queryByText(/Resume only continues the saved run/) !== null);
        if (!card) throw new Error("canvas loop recovery card missing");
        expect(within(card).getByText(/Resume only continues the saved run/)).toBeInTheDocument();
        fireEvent.click(within(card).getByRole("button", { name: "Refresh turn" }));
        await waitFor(() => expect(store.refreshAgentLoop).toHaveBeenCalledWith("canvas-loop-1"));
        fireEvent.click(within(card).getByRole("button", { name: "Continue guided run" }));
        await waitFor(() => expect(store.resumeAgentLoop).toHaveBeenCalledWith("canvas-loop-1", expect.objectContaining({ transport: "stream", signal: expect.any(AbortSignal), onEvent: expect.any(Function) })));
    });

    it("does not expose continue for unknown approved guided calls", () => {
        const project = clone(loopWire.approval.project) as unknown as AtelierProject;
        const turn = project.agent_turns.find(item => item.id === "loop-http-1") as Record<string, unknown>;
        const call = (turn.tool_calls as Record<string, unknown>[]).find(item => item.tool_name === "generation.createVideoCandidates");
        if (!call) throw new Error("fixture missing generation call");
        call.tool_name = "canvas.unknownApproved";
        renderPanel(project, null);
        expect(screen.getByText("Read-only: canvas.unknownApproved")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Continue guided run" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Revoke approval" })).not.toBeInTheDocument();
    });

    it("renders guided canvas approval and explicit resume without auto-running it", async () => {
        const canvasStartAck = loopWire.canvas_start_stream.at(-1)?.ack;
        if (!canvasStartAck) throw new Error("loop fixture missing canvas start ACK");
        const project = canvasStartAck.project as unknown as AtelierProject;
        const first = renderPanel(project, null);
        expect(screen.getAllByText(/Guided run/).length).toBeGreaterThan(0);
        expect(screen.getAllByText(/Agent take/).length).toBeGreaterThan(0);

        fireEvent.click(screen.getByRole("button", { name: "Approve canvas change" }));
        await waitFor(() => expect(store.recordAgentLoopCanvasApproval).toHaveBeenCalledWith("canvas-loop-1", "0d1d265587294c78b101c2c5d9652476", "approved"));
        expect(store.resumeAgentLoop).not.toHaveBeenCalled();

        first.unmount();
        renderPanel(loopWire.canvas_approval_ack.project as unknown as AtelierProject, null);
        const continueButtons = screen.getAllByRole("button", { name: "Continue guided run" });
        fireEvent.click(continueButtons[continueButtons.length - 1]);
        await waitFor(() => expect(store.resumeAgentLoop).toHaveBeenCalledWith("canvas-loop-1", expect.objectContaining({ transport: "stream", signal: expect.any(AbortSignal), onEvent: expect.any(Function) })));
    });

    it("supports deny, revoke, and cancel without auto-selecting a candidate", async () => {
        const { rerender } = renderPanel(projectFrom("started"), null);
        fireEvent.click(screen.getByRole("button", { name: "Deny" }));
        await waitFor(() => expect(store.denyAgentCall).toHaveBeenCalledWith("turn-http-1", "840a7ca5162e4ba0af14937cb229632e"));
        fireEvent.click(screen.getByRole("button", { name: "Cancel turn" }));
        await waitFor(() => expect(store.cancelAgentTurn).toHaveBeenCalledWith("turn-http-1"));

        rerender(<NextIntlClientProvider locale="en" timeZone="Asia/Shanghai" messages={messages}>
            <AgentTurnPanel project={projectFrom("approved")} selectedNode={null} ownerKey="owner" disabled={false} />
        </NextIntlClientProvider>);
        fireEvent.click(screen.getByRole("button", { name: "Revoke approval" }));
        await waitFor(() => expect(store.revokeAgentCall).toHaveBeenCalledWith("turn-http-1", "840a7ca5162e4ba0af14937cb229632e"));
    });

    it("keeps admitted or terminal approval history read-only with original attempt recovery text", () => {
        const { rerender } = renderPanel(projectFrom("admitted"), null);
        expect(screen.queryByRole("button", { name: "Submit approved generation" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Revoke approval" })).not.toBeInTheDocument();
        expect(screen.getByText(/ce56f4e894df4fe28756be26f7755fd0/)).toBeInTheDocument();
        expect(screen.getByText(/recover progress in the generation panel/)).toBeInTheDocument();

        const denied = projectFrom("approved");
        const turn = denied.agent_turns[0] as Record<string, unknown>;
        turn.status = "failed";
        const call = (turn.tool_calls as Record<string, unknown>[])[0];
        call.status = "completed";
        call.result_snapshot = null;
        call.approval_record = { schema_version: 1, approval_id: "approval", authorization_hash: "0".repeat(64), decision: "denied", decided_at: 1 };
        rerender(<NextIntlClientProvider locale="en" timeZone="Asia/Shanghai" messages={messages}>
            <AgentTurnPanel project={denied} selectedNode={null} ownerKey="owner" disabled={false} />
        </NextIntlClientProvider>);
        expect(screen.getByText("Denied")).toBeInTheDocument();
        expect(screen.getByText("This historical decision is read-only.")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Cancel turn" })).not.toBeInTheDocument();
    });

    it.each(["canceled", "failed", "completed"])("keeps an unapproved call read-only when its turn is %s", status => {
        const project = projectFrom("started");
        project.agent_turns[0].status = status;
        renderPanel(project);
        expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Deny" })).not.toBeInTheDocument();
        expect(screen.queryByText("Waiting for approval")).not.toBeInTheDocument();
        expect(screen.getByText("This historical decision is read-only.")).toBeInTheDocument();
    });

    it("reports schedule failures as saved attempts that require generation recovery", async () => {
        store.continueAgentCall.mockResolvedValueOnce({ ...agentAcks.approved, schedule_error: "worker offline" });
        renderPanel(projectFrom("approved"), null);
        fireEvent.click(screen.getByRole("button", { name: "Submit approved generation" }));
        expect(await screen.findByRole("alert")).toHaveTextContent(/saved but not scheduled: worker offline/);
        expect(screen.queryByText("Approved generation submitted. Candidate selection remains manual.")).not.toBeInTheDocument();
    });

    it("offers turn refresh after an ambiguous continue stream result", async () => {
        store.continueAgentCall.mockRejectedValueOnce(Object.assign(new Error("missing terminal"), { code: "atelier_agent_stream_missing_terminal" }));
        renderPanel(projectFrom("approved"), null);

        fireEvent.click(screen.getByRole("button", { name: "Submit approved generation" }));
        expect(await screen.findByRole("alert")).toHaveTextContent("missing terminal");

        fireEvent.click(screen.getByRole("button", { name: "Refresh turn" }));
        await waitFor(() => expect(store.refreshAgentTurn).toHaveBeenCalledWith("turn-http-1"));
    });

    it("allows recovery reads while an ambiguous write has blocked further mutations", async () => {
        store.needsRefresh = true;
        render(<NextIntlClientProvider locale="en" timeZone="Asia/Shanghai" messages={messages}>
            <AgentTurnPanel project={projectFrom("approved")} selectedNode={null} ownerKey="owner" disabled />
        </NextIntlClientProvider>);
        expect(screen.getByRole("button", { name: "Submit approved generation" })).toBeDisabled();
        const refresh = screen.getByRole("button", { name: "Refresh turn" });
        expect(refresh).toBeEnabled();
        fireEvent.click(refresh);
        await waitFor(() => expect(store.refreshAgentTurn).toHaveBeenCalledWith("turn-http-1"));
        expect(store.continueAgentCall).not.toHaveBeenCalled();
    });


    it("renders completed readProject calls as supported read-only results without generation recovery fields", () => {
        const project = projectFrom("started");
        project.agent_turns = [{
            id: "read-turn", project_id: project.id, status: "completed", user_message: "Read the canvas", created_at: 1, updated_at: 1,
            tool_calls: [{
                call_id: "read-call", tool_name: "canvas.readProject", status: "completed", arguments: {}, result_snapshot: { node_count: 2 },
            }],
        }] as never;

        renderPanel(project, null);

        expect(screen.getByText("Canvas read result")).toBeInTheDocument();
        expect(screen.queryByText("Read-only: canvas.readProject")).not.toBeInTheDocument();
        expect(screen.queryByText(/shown for diagnosis only/)).not.toBeInTheDocument();
        expect(screen.queryByText("Original attempt")).not.toBeInTheDocument();
        expect(screen.queryByText(/saved; recover progress/)).not.toBeInTheDocument();
        expect(screen.queryByText("Unavailable before provider submission")).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Continue guided run" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Cancel turn" })).not.toBeInTheDocument();
    });

    it("does not present completed canvas mutation results as saved generation attempts", () => {
        const project = projectFrom("started");
        project.agent_turns = [{
            id: "canvas-turn", project_id: project.id, status: "completed", user_message: "Add a node", created_at: 1, updated_at: 1,
            tool_calls: [{
                call_id: "canvas-call", tool_name: "canvas.createVideoNode", status: "completed", arguments: {},
                canvas_approval_binding: { normalized_arguments: { title: "Agent take", prompt: "Make it move" } },
                result_snapshot: { node_id: "node-new", attempt_id: "not-a-generation-attempt" },
            }],
        }] as never;

        renderPanel(project, null);

        expect(screen.getAllByText(/Agent take/).length).toBeGreaterThan(0);
        expect(screen.getByText("No provider cost")).toBeInTheDocument();
        expect(screen.queryByText("Original attempt")).not.toBeInTheDocument();
        expect(screen.queryByText(/not-a-generation-attempt/)).not.toBeInTheDocument();
        expect(screen.queryByText(/saved; recover progress/)).not.toBeInTheDocument();
        expect(screen.queryByText("Unavailable before provider submission")).not.toBeInTheDocument();
    });

    it("keeps unknown tools visible as read-only diagnostics without execution controls", () => {
        const unknown = projectFrom("started");
        const turn = unknown.agent_turns[0] as Record<string, unknown>;
        const call = (turn.tool_calls as Record<string, unknown>[])[0];
        call.tool_name = "canvas.legacyThing";
        renderPanel(unknown, null);
        expect(screen.getByText("Read-only: canvas.legacyThing")).toBeInTheDocument();
        expect(screen.getByText(/shown for diagnosis only/)).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Deny" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Submit approved generation" })).not.toBeInTheDocument();
    });
});
