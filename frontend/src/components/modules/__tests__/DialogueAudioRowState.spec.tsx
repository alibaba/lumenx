import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import DialogueAudioRow from "../storyboard-r2v/DialogueAudioRow";
import { api, type DialogueAudioState } from "@/lib/api";
import { useProjectStore } from "@/store/projectStore";
import { ack, deferred, enriched, frame } from "./frame-command-fixtures";
import { renderWithIntl } from "../../series/__tests__/test-utils";
const TEST_STORAGE_ID = '11111111-1111-4111-8111-111111111111';

vi.mock("@/lib/api", () => ({
    API_URL: "http://test.invalid",
    api: {
        getDialogueAudioState: vi.fn(),
        generateLineAudio: vi.fn(),
        getProject: vi.fn(),
    },
}));

const state: DialogueAudioState = {
    project_id: "p",
    frame_id: "f1",
    read_versions: { project: 4, series: null, library: 1 },
    audio_url: null,
    audio_error: null,
    historical_instructions: "上次成功的温柔语气",
    dialogue: "old line",
    voice_id: "voice-backend",
    options: { speed: 0, volume: 70, instructions: null },
    resolved_request: null,
    sources: null,
    prepared: null,
    freshness: "stale",
    reason: "parameters_changed",
};

function renderRow(overrides: Partial<React.ComponentProps<typeof DialogueAudioRow>> = {}) {
    return renderWithIntl(<DialogueAudioRow
        scriptId="p"
        frameId="f1"
        dialogue="old line"
        voiceId="voice-ui"
        audioUrl={undefined}
        audioError={null}
        {...overrides}
    />);
}

beforeEach(() => {
    activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID });
    vi.resetAllMocks();
    localStorage.clear();
    useProjectStore.setState({ currentProject: null, projects: [], readWatermarks: {}, runningOps: {} });
    useProjectStore.getState().applyProjectReadSnapshot("p", enriched(ack([frame("f1")], "p", 4), 1), true);
    vi.mocked(api.getDialogueAudioState).mockResolvedValue(state);
    vi.mocked(api.generateLineAudio).mockResolvedValue(ack([frame("f1")], "p", 4));
    vi.mocked(api.getProject).mockResolvedValue(enriched(ack([frame("f1")], "p", 4), 1));
});

describe("DialogueAudioRow audio state workflow", () => {
    it("loads desired options on open and sends history only after explicit reuse", async () => {
        const onUpdateDialogue = vi.fn().mockResolvedValue(undefined);
        renderRow({ onUpdateDialogue });
        fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));

        await waitFor(() => expect(api.getDialogueAudioState).toHaveBeenCalledWith("p", "f1"));
        expect(screen.getByDisplayValue("0")).toBeInTheDocument();
        expect(screen.getByDisplayValue("70")).toBeInTheDocument();

        fireEvent.change(screen.getByPlaceholderText(/输入该镜头/), { target: { value: "new line" } });
        fireEvent.click(screen.getByText("沿用上次成功指令"));
        fireEvent.click(screen.getByRole("button", { name: "生成对白" }));

        await waitFor(() => expect(api.generateLineAudio).toHaveBeenCalledOnce());
        expect(onUpdateDialogue).toHaveBeenCalledOnce();
        expect(onUpdateDialogue).toHaveBeenCalledWith("new line");
        expect(onUpdateDialogue.mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api.generateLineAudio).mock.invocationCallOrder[0]);
        expect(api.generateLineAudio).toHaveBeenCalledWith("p", "f1", { instructions: "上次成功的温柔语气" });
        expect(api.getProject).toHaveBeenCalledOnce();
        expect(api.getProject).toHaveBeenCalledWith("p");
        expect(api.getDialogueAudioState).toHaveBeenCalledTimes(3);
    });

    it("does not submit paid generation when dialogue save is rejected", async () => {
        const onUpdateDialogue = vi.fn().mockRejectedValue(new Error("save rejected"));
        renderRow({ onUpdateDialogue });
        fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
        await waitFor(() => expect(api.getDialogueAudioState).toHaveBeenCalledOnce());

        fireEvent.change(screen.getByPlaceholderText(/输入该镜头/), { target: { value: "new line" } });
        fireEvent.click(screen.getByRole("button", { name: "生成对白" }));

        await waitFor(() => expect(screen.getByText("save rejected")).toBeInTheDocument());
        expect(api.generateLineAudio).not.toHaveBeenCalled();
        expect(screen.getByDisplayValue("new line")).toBeInTheDocument();
    });
});

it("uses the backend-resolved voice when the row could not guess a binding", async () => {
    renderRow({ voiceId: undefined });
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByText(/voice-backend/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "生成对白" })).not.toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "生成对白" }));
    await waitFor(() => expect(api.generateLineAudio).toHaveBeenCalledOnce());
});

it("shows effective provider parameters separately from editable overrides", async () => {
    vi.mocked(api.getDialogueAudioState).mockResolvedValue({ ...state,
        options: { speed: null, volume: 0, instructions: null },
        sources: { speed: "character_binding", volume: "override" },
        prepared: { parameters: { speech_rate: 1.3, volume: 0, instruction: "speak gently" } },
    });
    renderRow();
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    expect(await screen.findByText("本次核验的有效参数")).toBeInTheDocument();
    expect(screen.getByText(/1.3.*继承角色/)).toBeInTheDocument();
    expect(screen.getByText(/0.*镜头覆盖/)).toBeInTheDocument();
    expect(screen.getByText("speak gently")).toBeInTheDocument();
    expect(screen.queryByText("parameters_changed")).not.toBeInTheDocument();
});

it("records newer audio-only source versions without refetching itself and rejects older reads", async () => {
    vi.mocked(api.getDialogueAudioState).mockResolvedValueOnce({ ...state, read_versions: { ...state.read_versions, library: 10 } });
    renderRow();
    fireEvent.click(screen.getByRole('button', { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: '生成对白' })).not.toBeDisabled());
    expect(api.getDialogueAudioState).toHaveBeenCalledOnce();
    vi.mocked(api.getDialogueAudioState).mockResolvedValue({ ...state, read_versions: { ...state.read_versions, library: 2 } });
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(api.getDialogueAudioState).toHaveBeenCalledTimes(2));
    expect(screen.getByRole('button', { name: '生成对白' })).toBeDisabled();
});

it("rejects a project revision older than the last audio state shown in this modal", async () => {
    vi.mocked(api.getDialogueAudioState).mockResolvedValueOnce({ ...state, read_versions: { ...state.read_versions, project: 5 } });
    renderRow();
    fireEvent.click(screen.getByRole('button', { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: '生成对白' })).not.toBeDisabled());
    vi.mocked(api.getDialogueAudioState).mockResolvedValue(state);
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(api.getDialogueAudioState).toHaveBeenCalledTimes(2));
    expect(screen.getByRole('button', { name: '生成对白' })).toBeDisabled();
});

it("preserves edited desired options while refreshing the server state", async () => {
    renderRow();
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByDisplayValue("70")).toBeInTheDocument());
    fireEvent.change(screen.getByDisplayValue("70"), { target: { value: "0" } });
    fireEvent.click(screen.getByText("沿用上次成功指令"));
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(api.getDialogueAudioState).toHaveBeenCalledTimes(2));
    expect(screen.getByDisplayValue("上次成功的温柔语气")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "生成对白" }));
    await waitFor(() => expect(api.generateLineAudio).toHaveBeenCalledWith("p", "f1", {
        volume: 0, instructions: "上次成功的温柔语气",
    }));
});

it("does not keep a verified state after a failed refresh", async () => {
    vi.mocked(api.getDialogueAudioState).mockResolvedValueOnce({ ...state, freshness: "current" })
        .mockRejectedValueOnce(new Error("offline"));
    renderRow();
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByText(/voice-backend/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(screen.getByText("offline")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "生成对白" })).toBeDisabled();
});

it("deduplicates blur save before generation and waits for it", async () => {
    const save = deferred<void>();
    const onUpdateDialogue = vi.fn().mockReturnValue(save.promise);
    renderRow({ onUpdateDialogue });
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByDisplayValue("70")).toBeInTheDocument());
    const text = screen.getByPlaceholderText(/输入该镜头/);
    fireEvent.change(text, { target: { value: "new line" } });
    fireEvent.blur(text);
    fireEvent.click(screen.getByRole("button", { name: "生成对白" }));
    await waitFor(() => expect(onUpdateDialogue).toHaveBeenCalledOnce());
    expect(api.generateLineAudio).not.toHaveBeenCalled();
    await act(async () => save.resolve());
    await waitFor(() => expect(api.generateLineAudio).toHaveBeenCalledOnce());
    expect(onUpdateDialogue).toHaveBeenCalledOnce();
});

it("does not submit after closing while text save is pending", async () => {
    const save = deferred<void>();
    renderRow({ onUpdateDialogue: vi.fn().mockReturnValue(save.promise) });
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByDisplayValue("70")).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/输入该镜头/), { target: { value: "new line" } });
    fireEvent.click(screen.getByRole("button", { name: "生成对白" }));
    fireEvent.click(screen.getByRole("button", { name: "×" }));
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await act(async () => save.resolve());
    expect(api.generateLineAudio).not.toHaveBeenCalled();
});

it("does not let an old generation error enter a reopened modal or repeat generation", async () => {
    const generation = deferred();
    vi.mocked(api.generateLineAudio).mockReturnValue(generation.promise);
    renderRow();
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByDisplayValue("70")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "生成对白" }));
    await waitFor(() => expect(api.generateLineAudio).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "×" }));
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByDisplayValue("70")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "生成对白" })).toBeDisabled();
    await act(async () => generation.reject(new Error("old generation failed")));
    expect(screen.queryByText("old generation failed")).not.toBeInTheDocument();
    expect(api.generateLineAudio).toHaveBeenCalledOnce();
});

it("refreshes a reopened modal after its earlier generation finishes without resubmitting", async () => {
    const generation = deferred();
    vi.mocked(api.generateLineAudio).mockReturnValue(generation.promise);
    renderRow();
    fireEvent.click(screen.getByRole('button', { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: '生成对白' })).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: '生成对白' }));
    await waitFor(() => expect(api.generateLineAudio).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole('button', { name: '关闭' }));
    fireEvent.click(screen.getByRole('button', { name: /对白音频/ }));
    await waitFor(() => expect(api.getDialogueAudioState).toHaveBeenCalledTimes(2));
    vi.mocked(api.getDialogueAudioState).mockResolvedValue({ ...state, audio_url: 'audio/new.mp3', freshness: 'current' });
    await act(async () => generation.resolve(ack([frame('f1')], 'p', 4)));
    expect(await screen.findByRole('button', { name: '重新生成' })).not.toBeDisabled();
    expect(api.getDialogueAudioState).toHaveBeenCalledTimes(3);
    expect(api.generateLineAudio).toHaveBeenCalledOnce();
});

it("confirms ordinary failed generation once and keeps the user's options", async () => {
    vi.mocked(api.generateLineAudio).mockResolvedValue(ack([{ ...frame("f1"), audio_error: "provider failed" }], "p", 4));
    renderRow();
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByDisplayValue("70")).toBeInTheDocument());
    fireEvent.change(screen.getByDisplayValue("70"), { target: { value: "83" } });
    fireEvent.click(screen.getByRole("button", { name: "生成对白" }));
    await waitFor(() => expect(screen.getByText("provider failed")).toBeInTheDocument());
    expect(api.getProject).toHaveBeenCalledOnce();
    expect(api.getDialogueAudioState).toHaveBeenCalledTimes(2);
    expect(screen.getByDisplayValue("83")).toBeInTheDocument();
    expect(api.generateLineAudio).toHaveBeenCalledOnce();
});

it("stops after failed confirmation and allows a read-only refresh", async () => {
    vi.mocked(api.getProject).mockRejectedValue(new Error("offline"));
    renderRow();
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByDisplayValue("70")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "生成对白" }));
    await waitFor(() => expect(screen.getByText(/无法确认最新项目状态/)).toBeInTheDocument());
    expect(api.getProject).toHaveBeenCalledOnce();
    expect(api.getDialogueAudioState).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(api.getDialogueAudioState).toHaveBeenCalledTimes(2));
    expect(api.generateLineAudio).toHaveBeenCalledOnce();
});

it("rejects an older state after the project version advances", async () => {
    renderRow();
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByDisplayValue("70")).toBeInTheDocument());
    act(() => useProjectStore.getState().applyProjectReadSnapshot("p", enriched(ack([frame("f1")], "p", 5), 1)));
    await waitFor(() => expect(api.getDialogueAudioState).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "生成对白" })).toBeDisabled();
    expect(screen.getByText("无法读取当前音频参数状态")).toBeInTheDocument();
});

it("sends null to restore inherited instructions and zero for explicit volume", async () => {
    renderRow();
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByDisplayValue("70")).toBeInTheDocument());
    fireEvent.change(screen.getByDisplayValue("70"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "恢复继承语气" }));
    fireEvent.click(screen.getByRole("button", { name: "生成对白" }));
    await waitFor(() => expect(api.generateLineAudio).toHaveBeenCalledWith("p", "f1", { volume: 0, instructions: null }));
});

it("keeps an unsaved dialogue draft when confirmed parent text changes", async () => {
    const view = renderRow();
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByDisplayValue("70")).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/输入该镜头/), { target: { value: "my unsaved line" } });
    view.rerender(<DialogueAudioRow scriptId="p" frameId="f1" dialogue="new parent line" voiceId="voice-ui" audioUrl={undefined} audioError={null} />);
    expect(screen.getByDisplayValue("my unsaved line")).toBeInTheDocument();
});

afterEach(() => {
    cleanup();
    clearRuntimeEnvironment();
});


it("queues a changed blur draft behind the pending dialogue save", async () => {
    const first = deferred<void>(); const second = deferred<void>();
    const onUpdateDialogue = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    renderRow({ onUpdateDialogue });
    fireEvent.click(screen.getByRole("button", { name: /对白音频/ }));
    await waitFor(() => expect(screen.getByDisplayValue("70")).toBeInTheDocument());
    const text = screen.getByPlaceholderText(/输入该镜头/);
    fireEvent.change(text, { target: { value: "first line" } }); fireEvent.blur(text);
    await waitFor(() => expect(onUpdateDialogue).toHaveBeenCalledTimes(1));
    fireEvent.change(text, { target: { value: "old line" } }); fireEvent.blur(text);
    try {
        await act(async () => first.resolve());
        await waitFor(() => expect(onUpdateDialogue).toHaveBeenCalledTimes(2));
        expect(onUpdateDialogue).toHaveBeenLastCalledWith("old line");
        expect(text).toHaveValue("old line");
    } finally { await act(async () => { first.resolve(); second.resolve(); }); }
});
