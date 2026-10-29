import type { ComponentProps } from "react";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { api } from "@/lib/api";
import { useProjectStore, type Project, type VideoParams } from "@/store/projectStore";
import { renderWithIntl } from "../../series/__tests__/test-utils";
import VideoCreator from "../VideoCreator";
import type PromptBuilder from "../PromptBuilder";
import { ack, deferred, enriched, frame, project } from "./frame-command-fixtures";
import images from "@/__tests__/fixtures/inline-raster-display.json";
const TEST_STORAGE_ID = '11111111-1111-4111-8111-111111111111';

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      extractLastFrame: vi.fn(),
      getProject: vi.fn(),
      createVideoTask: vi.fn(),
      uploadFile: vi.fn(),
    },
  };
});
vi.mock("../PromptBuilder", async () => {
  const React = await import("react");
  const MockPromptBuilder = React.forwardRef((_props: ComponentProps<typeof PromptBuilder>, ref) => {
    const props = _props as ComponentProps<typeof PromptBuilder>;
    React.useImperativeHandle(ref, () => ({ insertCharacter: vi.fn(), insertCamera: vi.fn() }));
    return <textarea aria-label="prompt" value={props.segments.map((segment) => segment.value).join(" ")} onChange={(event) => props.onChange([{ type: "text", value: event.target.value, id: "test" }])} />;
  });
  MockPromptBuilder.displayName = "MockPromptBuilder";
  return { default: MockPromptBuilder };
});

const baseParams: VideoParams = {
  resolution: "720p", duration: 5, generateAudio: false, audioUrl: "", promptExtend: true,
  negativePrompt: "", batchSize: 1, cameraMovement: "none", subjectMotion: "still", model: "wan2.5-i2v",
  shotType: "single", generationMode: "i2v", referenceVideoUrls: [], mode: "std", sound: false,
  cfgScale: 0.5, viduAudio: true, movementAmplitude: "auto", seed: undefined,
};

function videoTask(id: string) {
  return { id, project_id: "a", frame_id: "f1", image_url: "/f1.png", prompt: "move", status: "completed", video_url: "/done.mp4", created_at: 1 };
}

function videoProject(): Project {
  return { ...project("a", 3), frames: [
    { ...frame("f1"), image_url: "/f1.png", selected_video_id: "task-a" },
    { ...frame("f2"), image_url: "/f2.png" },
  ], video_tasks: [videoTask("task-a")] } as Project;
}

function rawProject(value: Project, revision = value.revision) {
  return { ...ack(value.frames as Record<string, unknown>[], value.id, revision), characters: value.characters,
    scenes: value.scenes, props: value.props, video_tasks: value.video_tasks };
}

function read(value: Project, library = 8) {
  return enriched(rawProject(value), library);
}

function renderCreator(overrides: Partial<VideoParams> = {}, onTaskCreated = vi.fn()) {
  const view = renderWithIntl(<VideoCreator
    onTaskCreated={onTaskCreated}
    remixData={null}
    onRemixClear={vi.fn()}
    params={{ ...baseParams, ...overrides }}
    onParamsChange={vi.fn()}
  />);
  return { ...view, onTaskCreated };
}

function selectFrameAndPrompt() {
  fireEvent.click(screen.getByAltText("Frame f1"));
  fireEvent.change(screen.getByLabelText("prompt"), { target: { value: "motion prompt" } });
}

function chooseUploadFiles(container: HTMLElement, files: File[]) {
  fireEvent.click(screen.getByRole("button", { name: /Upload|上传/ }));
  const input = container.querySelector<HTMLInputElement>("#image-upload");
  expect(input).not.toBeNull();
  fireEvent.change(input!, { target: { files } });
}

beforeEach(() => {
    activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID });
  vi.resetAllMocks();
  vi.spyOn(window, "alert").mockImplementation(() => undefined);
  vi.spyOn(URL, "createObjectURL").mockImplementation((file) => `blob:${(file as File).name}`);
  const current = videoProject();
  useProjectStore.setState({ projects: [], currentProject: null, currentSeries: null, readWatermarks: {}, runningOps: {}, renderingFrames: new Set() });
  useProjectStore.getState().applyProjectReadSnapshot("a", read(current), true);
});

describe("VideoCreator guarded readbacks", () => {
  it.each([
    ["inline JPEG", images.jpeg, images.jpeg],
    ["HTML data URL", "data:text/html,<script>alert(1)</script>", ""],
    ["SVG data URL", 'data:image/svg+xml,<svg onload="alert(1)"></svg>', ""],
  ])("keeps both selected-image views safe for %s", (_kind, url, expected) => {
    const current = useProjectStore.getState().currentProject!;
    useProjectStore.setState({ currentProject: {
      ...current,
      frames: current.frames.map((item) => item.id === "f1" ? { ...item, image_url: url } : item),
    } });
    const { container } = renderCreator();
    fireEvent.click(screen.getByAltText("Frame f1"));
    expect(screen.getByAltText("Selected")).toHaveAttribute("src", expected);

    fireEvent.click(screen.getByRole("button", { name: /Upload|上传/ }));
    expect(screen.getByAltText("Input 0")).toHaveAttribute("src", expected);
    expect(container.querySelector("script, iframe")).toBeNull();
    expect(api.createVideoTask).not.toHaveBeenCalled();
  });

  it("stops queued batch submissions after the submitting session is unmounted", async () => {
    vi.mocked(api.uploadFile).mockResolvedValueOnce({ url: '/one.png' }).mockResolvedValueOnce({ url: '/two.png' });
    const request = deferred<unknown>();
    vi.mocked(api.createVideoTask).mockReturnValue(request.promise);
    const view = renderCreator();
    fireEvent.change(screen.getByLabelText('prompt'), { target: { value: 'motion prompt' } });
    chooseUploadFiles(view.container, [new File(['1'], 'one.png'), new File(['2'], 'two.png')]);
    const generate = screen.getByRole('button', { name: /Generate Video|生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    expect(api.createVideoTask).toHaveBeenCalledTimes(1);
    view.unmount();
    await act(async () => request.resolve([videoTask('accepted')]));
    expect(api.createVideoTask).toHaveBeenCalledTimes(1);
    expect(api.getProject).not.toHaveBeenCalled();
  });
  it("drops a paid ACK after unmount before requesting project readback", async () => {
    const request = deferred<unknown>();
    vi.mocked(api.createVideoTask).mockReturnValue(request.promise);
    const view = renderCreator(); selectFrameAndPrompt();
    fireEvent.click(screen.getByRole("button", { name: /Generate Video|生成视频/ }));
    view.unmount();
    await act(async () => request.resolve([videoTask("new-task")]));
    expect(api.getProject).not.toHaveBeenCalled();
  });
  it("does not claim success or release retry protection when readback omits an accepted task", async () => {
    vi.mocked(api.createVideoTask).mockResolvedValue([videoTask("new-task")]);
    vi.mocked(api.getProject).mockResolvedValue(read(videoProject()));
    const { onTaskCreated } = renderCreator(); selectFrameAndPrompt();
    fireEvent.click(screen.getByRole("button", { name: /Generate Video|生成视频/ }));
    await screen.findByRole("alert");
    expect(onTaskCreated).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Read again|重新读取/ }));
    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(api.createVideoTask).toHaveBeenCalledTimes(1);
  });
  it("does not dispatch video generation from a different API environment", async () => {
    act(() => useProjectStore.setState({ currentProject: { ...videoProject(), readEnvironment: 'http://other.test' } }));
    renderCreator(); selectFrameAndPrompt();
    fireEvent.click(screen.getByRole("button", { name: /Generate Video|生成视频/ }));
    await act(async () => {});
    expect(api.createVideoTask).not.toHaveBeenCalled();
  });

  it("rejects stale ordered Wan3 selections before the legacy video task POST", async () => {
    renderCreator({ model: "wan3.0-video" });
    selectFrameAndPrompt();

    fireEvent.click(screen.getByRole("button", { name: /Generate Video|生成视频/ }));
    await act(async () => {});

    expect(api.createVideoTask).not.toHaveBeenCalled();
    expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/storyboard|分镜|Wan3/i));
  });

  it("confirms extract-last-frame raw ACK with exactly one guarded project read", async () => {
    const current = useProjectStore.getState().currentProject!;
    const rawAckProject = { ...current, revision: 4, frames: current.frames.map((item) => item.id === "f2" ? { ...item, rendered_image_url: "/raw-stale.png" } : item) } as Project;
    const confirmed = { ...rawAckProject, frames: rawAckProject.frames.map((item) => item.id === "f2" ? { ...item, rendered_image_url: "/confirmed.png" } : item) } as Project;
    vi.mocked(api.extractLastFrame).mockResolvedValue(rawProject(rawAckProject, 4));
    vi.mocked(api.getProject).mockResolvedValue(read(confirmed));

    renderCreator();
    fireEvent.click(screen.getByTitle("Use previous video's last frame as input"));

    await waitFor(() => expect(api.extractLastFrame).toHaveBeenCalledWith("a", "f2", "task-a"));
    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1));
    expect(useProjectStore.getState().currentProject?.frames.find((item) => item.id === "f2")?.rendered_image_url).toBe("/confirmed.png");
  });

  it("reports submit readback failure separately, blocks paid resubmit, and retries with one read", async () => {
    const confirmed = { ...videoProject(), revision: 4, video_tasks: [videoTask("task-a"), videoTask("new-task")] } as Project;
    const { onTaskCreated } = renderCreator();
    vi.mocked(api.createVideoTask).mockResolvedValue([videoTask("new-task")]);
    vi.mocked(api.getProject).mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(read(confirmed));

    selectFrameAndPrompt();
    const generate = screen.getByRole("button", { name: /Generate Video|生成视频/ });
    fireEvent.click(generate);

    await waitFor(() => expect(api.createVideoTask).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1));
    expect(window.alert).not.toHaveBeenCalledWith(expect.stringMatching(/submit|提交|失败/));
    expect(screen.getByRole("alert")).toHaveTextContent(/read|读取|saved|保存/i);

    fireEvent.click(generate);
    expect(api.createVideoTask).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /Read again|重新读取/ }));
    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(onTaskCreated).toHaveBeenCalledWith(expect.objectContaining({ id: "a", video_tasks: expect.arrayContaining([expect.objectContaining({ id: "new-task" })]) })));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("uses a ref guard so double submit only creates one paid task while the first is pending", async () => {
    const request = deferred<unknown>();
    vi.mocked(api.createVideoTask).mockReturnValue(request.promise);
    vi.mocked(api.getProject).mockResolvedValue(read(videoProject()));

    renderCreator();
    selectFrameAndPrompt();
    const generate = screen.getByRole("button", { name: /Generate Video|生成视频/ });
    fireEvent.click(generate);
    fireEvent.click(generate);

    await waitFor(() => expect(api.createVideoTask).toHaveBeenCalledTimes(1));
    await act(async () => request.resolve([videoTask("new-task")]));
  });

  it("rejects stale A→B→A submit readbacks before calling the parent updater", async () => {
    const { onTaskCreated } = renderCreator();
    const readRequest = deferred<unknown>();
    vi.mocked(api.createVideoTask).mockResolvedValue([videoTask("new-task")]);
    vi.mocked(api.getProject).mockReturnValue(readRequest.promise);

    selectFrameAndPrompt();
    fireEvent.click(screen.getByRole("button", { name: /Generate Video|生成视频/ }));
    await waitFor(() => expect(api.createVideoTask).toHaveBeenCalledTimes(1));

    act(() => useProjectStore.getState().applyProjectReadSnapshot("b", read({ ...project("b", 3), frames: [frame("b1")], video_tasks: [] } as Project), true));
    act(() => useProjectStore.getState().applyProjectReadSnapshot("a", read({ ...videoProject(), revision: 5 } as Project), true));
    await act(async () => readRequest.resolve(read({ ...videoProject(), revision: 4, video_tasks: [videoTask("task-a"), videoTask("new-task")] } as Project)));

    expect(onTaskCreated).not.toHaveBeenCalled();
    expect(useProjectStore.getState().currentProject?.id).toBe("a");
    expect(useProjectStore.getState().currentProject?.revision).toBe(5);
  });

  it("keeps submit locked across remounts until the first paid request resolves", async () => {
    const request = deferred<unknown>();
    vi.mocked(api.createVideoTask).mockReturnValue(request.promise);
    vi.mocked(api.getProject).mockResolvedValue(read(videoProject()));

    const first = renderCreator();
    selectFrameAndPrompt();
    fireEvent.click(screen.getByRole("button", { name: /Generate Video|生成视频/ }));
    await waitFor(() => expect(api.createVideoTask).toHaveBeenCalledTimes(1));

    first.unmount();
    renderCreator();
    selectFrameAndPrompt();
    fireEvent.click(screen.getByRole("button", { name: /Generate Video|生成视频/ }));
    expect(api.createVideoTask).toHaveBeenCalledTimes(1);

    await act(async () => request.resolve([videoTask("new-task")]));
  });

  it("retains the extract ACK revision floor for explicit read retries", async () => {
    const current = useProjectStore.getState().currentProject!;
    const rawAckProject = { ...current, revision: 4, frames: current.frames.map((item) => item.id === "f2" ? { ...item, rendered_image_url: "/raw-stale.png" } : item) } as Project;
    const stale = { ...current, revision: 3 } as Project;
    const { onTaskCreated } = renderCreator();
    vi.mocked(api.extractLastFrame).mockResolvedValue(rawProject(rawAckProject, 4));
    vi.mocked(api.getProject).mockResolvedValueOnce(read(stale)).mockResolvedValueOnce(read(stale));

    fireEvent.click(screen.getByTitle("Use previous video's last frame as input"));
    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("alert")).toHaveTextContent(/read|读取|saved|保存/i);

    fireEvent.click(screen.getByRole("button", { name: /Read again|重新读取/ }));
    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("alert")).toHaveTextContent(/read|读取|saved|保存/i);
    expect(onTaskCreated).not.toHaveBeenCalled();
  });


  it("does not submit while uploaded inputs are still pending", async () => {
    const pendingUpload = deferred<{ url: string }>();
    vi.mocked(api.uploadFile).mockReturnValue(pendingUpload.promise);
    const { container } = renderCreator();

    fireEvent.change(screen.getByLabelText("prompt"), { target: { value: "motion prompt" } });
    chooseUploadFiles(container, [new File(["image"], "pending.png", { type: "image/png" })]);

    const generate = screen.getByRole("button", { name: /Generate Video|生成视频/ });
    await waitFor(() => expect(generate).toBeDisabled());
    fireEvent.click(generate);

    expect(api.createVideoTask).not.toHaveBeenCalled();
    await act(async () => pendingUpload.resolve({ url: "/uploaded.png" }));
  });

  it("shows partial submit failure even when confirmed readback succeeds", async () => {
    const confirmed = { ...videoProject(), revision: 4, video_tasks: [videoTask("task-a"), videoTask("new-task")] } as Project;
    vi.mocked(api.uploadFile)
      .mockResolvedValueOnce({ url: "/upload-1.png" })
      .mockResolvedValueOnce({ url: "/upload-2.png" });
    vi.mocked(api.createVideoTask)
      .mockResolvedValueOnce([videoTask("new-task")])
      .mockRejectedValueOnce(new Error("provider rejected second item"));
    vi.mocked(api.getProject).mockResolvedValue(read(confirmed));
    const { container, onTaskCreated } = renderCreator();

    fireEvent.change(screen.getByLabelText("prompt"), { target: { value: "motion prompt" } });
    chooseUploadFiles(container, [
      new File(["image-1"], "one.png", { type: "image/png" }),
      new File(["image-2"], "two.png", { type: "image/png" }),
    ]);

    const generate = screen.getByRole("button", { name: /Generate Video|生成视频/ });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);

    await waitFor(() => expect(api.createVideoTask).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1));
    expect(onTaskCreated).toHaveBeenCalledWith(expect.objectContaining({ id: "a", video_tasks: expect.arrayContaining([expect.objectContaining({ id: "new-task" })]) }));
    expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/Submitted 1\/2|已提交 1\/2/));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not let an unresolved submit readback alert start extract and clear the safety block", async () => {
    renderCreator();
    vi.mocked(api.createVideoTask).mockResolvedValue([videoTask("new-task")]);
    vi.mocked(api.getProject).mockRejectedValue(new Error("offline"));

    selectFrameAndPrompt();
    fireEvent.click(screen.getByRole("button", { name: /Generate Video|生成视频/ }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByTitle("Use previous video's last frame as input"));

    expect(api.extractLastFrame).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/read|读取|saved|保存/i);
  });
});

afterEach(() => {
    clearRuntimeEnvironment();
});
