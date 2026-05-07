// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import VoiceActingStudio from "../components/modules/VoiceActingStudio";

const { mockApi, mockState, mockUpdateProject } = vi.hoisted(() => {
  const updateProject = vi.fn();
  return {
    mockUpdateProject: updateProject,
    mockApi: {
      getVoices: vi.fn(),
      bindVoice: vi.fn(),
      updateVoiceParams: vi.fn(),
      generateAudio: vi.fn(),
      generateLineAudio: vi.fn(),
    },
    mockState: {
      currentProject: {
        id: "project-1",
        characters: [
          {
            id: "char-1",
            name: "小雨",
            gender: "女",
            age: "18",
            voice_id: "",
            voice_name: "",
            voice_speed: 1,
            voice_pitch: 1,
            voice_volume: 50,
          },
        ],
        frames: [],
      },
      updateProject,
    },
  };
});

vi.mock("@/store/projectStore", () => ({
  useProjectStore: (selector: (state: typeof mockState) => unknown) => selector(mockState),
}));

vi.mock("@/lib/api", () => ({
  api: mockApi,
}));

vi.mock("@/lib/utils", () => ({
  getAssetUrl: (url: string) => url,
}));

describe("VoiceActingStudio", () => {
  beforeEach(() => {
    mockUpdateProject.mockReset();
    mockApi.getVoices.mockReset();
    mockApi.bindVoice.mockReset();
    mockApi.updateVoiceParams.mockReset();
    mockApi.generateAudio.mockReset();
    mockApi.generateLineAudio.mockReset();

    mockApi.getVoices.mockResolvedValue([
      { id: "alloy", name: "Alloy - OpenAI-compatible" },
    ]);
    mockApi.bindVoice.mockResolvedValue(mockState.currentProject);
  });

  it("supports binding a custom provider voice id", async () => {
    render(<VoiceActingStudio />);

    await waitFor(() => {
      expect(mockApi.getVoices).toHaveBeenCalled();
    });

    fireEvent.change(
      screen.getByPlaceholderText("自定义 voice id，例如 cherry / longxiaochun / alloy"),
      { target: { value: "heroine-custom-voice" } },
    );
    fireEvent.change(
      screen.getByPlaceholderText("显示名称（可选，留空则沿用 voice id）"),
      { target: { value: "女主角定制音色" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "绑定自定义音色" }));

    await waitFor(() => {
      expect(mockApi.bindVoice).toHaveBeenCalledWith(
        "project-1",
        "char-1",
        "heroine-custom-voice",
        "女主角定制音色",
      );
    });
  });
});
