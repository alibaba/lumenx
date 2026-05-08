import { describe, expect, it } from "vitest";

import {
  normalizeProjectPayload,
  normalizePromptConfigResponse,
  normalizeSeriesAssetsPayload,
  normalizeSeriesDetailPayload,
  normalizeSeriesPayload,
} from "@/lib/api";

describe("normalizeProjectPayload", () => {
  it("normalizes backend Script payloads into frontend Project shape", () => {
    const project = normalizeProjectPayload({
      id: "project-1",
      title: "Smoke DTO",
      original_text: "source text",
      created_at: 1,
      updated_at: "2",
      generation_metadata: { parser: { source: "local" } },
    });

    expect(project).toMatchObject({
      id: "project-1",
      title: "Smoke DTO",
      originalText: "source text",
      status: "pending",
      characters: [],
      scenes: [],
      props: [],
      frames: [],
      generation_metadata: { parser: { source: "local" } },
    });
    expect(project.createdAt).toBe(new Date(1000).toISOString());
    expect(project.updatedAt).toBe(new Date(2000).toISOString());
    expect(project.codex_imagegen_policy).toMatchObject({
      enabled: true,
      mode: "safe_refs_only",
      max_total_bytes: 1048576,
      recommendation: {
        enabled: true,
        auto_apply: false,
        two_stage_min_ready_refs: 5,
      },
    });
  });

  it("normalizes codex imagegen two-stage aliases into the project policy", () => {
    const project = normalizeProjectPayload({
      id: "project-two-stage",
      title: "Two Stage DTO",
      codex_imagegen_policy: {
        enabled: true,
        mode: "two_stage",
        max_total_bytes: 1048576,
        recommendation: {
          auto_apply: true,
          two_stage_min_ready_refs: 6,
          shot_type_overrides: {
            closeup: {
              two_stage_min_ready_refs: 3,
            },
          },
        },
      },
    });

    expect(project.codex_imagegen_policy).toMatchObject({
      enabled: true,
      mode: "two_stage_high_consistency",
      max_total_bytes: 1048576,
      recommendation: {
        auto_apply: true,
        two_stage_min_ready_refs: 6,
        shot_type_overrides: {
          closeup: {
            two_stage_min_ready_refs: 3,
          },
        },
      },
    });
  });

  it("accepts generated storyboard DTO fields while keeping legacy aliases as fallbacks", () => {
    const project = normalizeProjectPayload({
      id: "project-frames",
      title: "Storyboard DTO",
      originalText: "legacy source",
      createdAt: 3000,
      updatedAt: 4000,
      aspectRatio: "16:9",
      frames: [
        {
          id: "frame-1",
          scene_id: "scene-1",
          action_description: "人物在校门口抬手挥手",
          rendered_image_url: "storyboard/frame-1.png",
        },
      ],
      video_tasks: [
        {
          id: "task-1",
          project_id: "project-frames",
          image_url: "storyboard/frame-1.png",
          prompt: "人物挥手",
          status: "completed",
          video_url: "video/task-1.mp4",
          duration: 5,
          resolution: "720p",
          generate_audio: false,
          prompt_extend: false,
          created_at: 4,
        },
      ],
    });

    expect(project.originalText).toBe("legacy source");
    expect(project.aspectRatio).toBe("16:9");
    expect(project.frames?.[0]).toMatchObject({
      id: "frame-1",
      rendered_image_url: "storyboard/frame-1.png",
    });
    expect(project.video_tasks?.[0]).toMatchObject({
      id: "task-1",
      status: "completed",
    });
  });

  it("normalizes generated DTO nullables before storing project data", () => {
    const project = normalizeProjectPayload({
      id: "project-nullables",
      title: "Nullable DTO",
      original_text: "source",
      created_at: 1,
      updated_at: 2,
      model_settings: { t2i_model: "openai-image" },
      characters: [
        {
          id: "char-1",
          name: "小七",
          description: "角色描述",
          age: null,
          full_body_asset: {
            selected_id: null,
            variants: [{ id: "img-1", url: "assets/char.png", prompt_used: null }],
          },
          video_assets: [
            {
              id: "asset-video-1",
              project_id: "project-nullables",
              asset_id: null,
              frame_id: null,
              image_url: "assets/char.png",
              prompt: "本地参考动作",
              status: "completed",
              video_url: null,
            },
          ],
        },
      ],
      scenes: [
        {
          id: "scene-1",
          name: "校门",
          description: "学校门口",
          image_url: null,
          time_of_day: null,
        },
      ],
      props: [
        {
          id: "prop-1",
          name: "通知书",
          description: "录取通知书",
          image_url: null,
        },
      ],
      frames: [
        {
          id: "frame-1",
          scene_id: "scene-1",
          dialogue: null,
          selected_video_id: null,
          image_asset: {
            selected_id: null,
            variants: [{ id: "frame-img-1", url: "storyboard/frame.png" }],
          },
        },
      ],
      video_tasks: [
        {
          id: "task-1",
          project_id: "project-nullables",
          asset_id: null,
          frame_id: null,
          image_url: "storyboard/frame.png",
          prompt: "本地视频任务",
          status: "completed",
          video_url: null,
        },
      ],
    });

    expect(project.characters[0].age).toBeUndefined();
    expect(project.characters[0].full_body_asset?.selected_id).toBeNull();
    expect(project.characters[0].full_body_asset?.variants[0]).toMatchObject({
      id: "img-1",
      created_at: 0,
    });
    expect(project.characters[0].full_body_asset?.variants[0].prompt_used).toBeUndefined();
    expect(project.characters[0].video_assets?.[0].asset_id).toBeUndefined();
    expect(project.characters[0].video_assets?.[0].duration).toBe(5);
    expect(project.scenes[0].image_url).toBeUndefined();
    expect(project.props[0].image_url).toBeUndefined();
    expect(project.frames[0].dialogue).toBeUndefined();
    expect(project.frames[0].selected_video_id).toBeUndefined();
    expect(project.video_tasks?.[0].asset_id).toBeUndefined();
    expect(project.video_tasks?.[0].video_url).toBeUndefined();
    expect(project.model_settings?.i2v_model).toBe("doubao-seedance-2-0-260128");
  });

  it("fails fast when required backend project fields are missing", () => {
    expect(() => normalizeProjectPayload({ title: "Missing id" })).toThrow(
      "Project API payload missing required string field: id",
    );
  });
});

describe("series DTO normalization", () => {
  it("normalizes backend Series payloads into the stable store shape", () => {
    const series = normalizeSeriesPayload({
      id: "series-1",
      title: "Series DTO",
      created_at: 11,
      updated_at: 22,
    });

    expect(series).toMatchObject({
      id: "series-1",
      title: "Series DTO",
      description: "",
      characters: [],
      scenes: [],
      props: [],
      episode_ids: [],
    });
  });

  it("normalizes generated Series DTO assets and nullables into store shape", () => {
    const series = normalizeSeriesPayload({
      id: "series-nullables",
      title: "Series Nullables",
      description: null,
      created_at: 11,
      updated_at: 22,
      episode_ids: ["ep-1", 2, "ep-2"],
      model_settings: { t2i_model: "openai-image" },
      prompt_config: { storyboard_polish: "分镜规则" },
      characters: [
        {
          id: "char-1",
          name: "小七",
          description: "角色描述",
          age: null,
          full_body_asset: {
            selected_id: null,
            variants: [{ id: "img-1", url: "assets/char.png", prompt_used: null }],
          },
        },
      ],
      scenes: [
        {
          id: "scene-1",
          name: "校门",
          description: "学校门口",
          image_url: null,
        },
      ],
      props: [
        {
          id: "prop-1",
          name: "通知书",
          description: "录取通知书",
          image_url: null,
        },
      ],
    });

    expect(series.description).toBe("");
    expect(series.episode_ids).toEqual(["ep-1", "ep-2"]);
    expect(series.characters[0].age).toBeUndefined();
    expect(series.characters[0].full_body_asset?.selected_id).toBeNull();
    expect(series.characters[0].full_body_asset?.variants[0].prompt_used).toBeUndefined();
    expect(series.scenes[0].image_url).toBeUndefined();
    expect(series.props[0].image_url).toBeUndefined();
    expect(series.prompt_config).toEqual({
      storyboard_polish: "分镜规则",
      video_polish: "",
      r2v_polish: "",
    });
    expect(series.model_settings?.i2v_model).toBe("doubao-seedance-2-0-260128");
  });

  it("normalizes Series detail payloads with episodes", () => {
    const detail = normalizeSeriesDetailPayload({
      id: "series-2",
      title: "Series Detail",
      created_at: 1,
      updated_at: 2,
      episodes: [
        { id: "ep-1", title: "Episode 1", created_at: 3, updated_at: 4 },
        { id: "ep-2", title: "Episode 2", episode_number: null, created_at: 5, updated_at: 6 },
      ],
    });

    expect(detail.episodes).toHaveLength(2);
    expect(detail.episodes?.[0].id).toBe("ep-1");
    expect(detail.episodes?.[1].episode_number).toBeUndefined();
  });

  it("normalizes series asset payloads into non-optional arrays", () => {
    const assets = normalizeSeriesAssetsPayload({
      characters: [
        {
          id: "char-1",
          name: "小七",
          description: "角色描述",
          age: null,
        },
      ],
      scenes: [
        {
          id: "scene-1",
          name: "校门",
          description: "学校门口",
          image_url: null,
        },
      ],
      props: [
        {
          id: "prop-1",
          name: "通知书",
          description: "录取通知书",
          image_url: null,
        },
      ],
    });

    expect(assets.characters[0].age).toBeUndefined();
    expect(assets.scenes[0].image_url).toBeUndefined();
    expect(assets.props[0].image_url).toBeUndefined();
  });
});

describe("prompt config DTO normalization", () => {
  it("fills prompt config defaults when backend fields are missing", () => {
    const result = normalizePromptConfigResponse({
      prompt_config: {},
    });

    expect(result.prompt_config).toEqual({
      storyboard_polish: "",
      video_polish: "",
      r2v_polish: "",
    });
    expect(result.defaults).toBeUndefined();
  });
});
