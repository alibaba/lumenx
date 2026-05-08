import { describe, expect, it } from "vitest";

import {
  buildStoryboardCompositionData,
  buildStoryboardReferencePreview,
  recommendCodexImagegenMode,
} from "../lib/storyboard-references";

const project = {
  scenes: [
    {
      id: "scene-1",
      name: "夜巷",
      image_url: "https://example.com/current-scene.png",
    },
  ],
  characters: [
    {
      id: "char-1",
      name: "林夏",
      full_body_asset: {
        selected_id: "char-current",
        variants: [
          {
            id: "char-current",
            url: "https://example.com/current-character.png",
          },
        ],
      },
    },
  ],
  props: [
    {
      id: "prop-1",
      name: "红色纸鹤",
      image_url: "https://example.com/current-prop.png",
    },
  ],
};

describe("buildStoryboardCompositionData", () => {
  it("rebuilds managed references instead of carrying stale urls forward", () => {
    const frame = {
      id: "frame-1",
      scene_id: "scene-1",
      character_ids: ["char-1"],
      prop_ids: ["prop-1"],
      composition_data: {
        reference_binding_version: 1,
        reference_image_url: "https://example.com/stale-first.png",
        reference_image_urls: [
          "https://example.com/stale-first.png",
          "https://example.com/stale-second.png",
          "https://example.com/current-scene.png",
        ],
      },
    };

    const composition = buildStoryboardCompositionData(project, frame, {
      continuityLock: false,
      includeStyleReferences: false,
    });

    expect(composition.reference_image_urls).toEqual([
      "https://example.com/current-scene.png",
      "https://example.com/current-character.png",
      "https://example.com/current-prop.png",
    ]);
    expect(composition.reference_image_urls).not.toContain("https://example.com/stale-first.png");
    expect(composition.reference_image_urls).not.toContain("https://example.com/stale-second.png");
  });

  it("preserves unmanaged custom reference urls", () => {
    const frame = {
      id: "frame-1",
      scene_id: "scene-1",
      character_ids: ["char-1"],
      prop_ids: [],
      composition_data: {
        reference_image_urls: ["https://example.com/custom-reference.png"],
      },
    };

    const composition = buildStoryboardCompositionData(project, frame, {
      continuityLock: false,
      includeStyleReferences: false,
    });

    expect(composition.reference_image_urls).toEqual([
      "https://example.com/custom-reference.png",
      "https://example.com/current-scene.png",
      "https://example.com/current-character.png",
    ]);
  });
});

describe("recommendCodexImagegenMode", () => {
  it("keeps small high-value reference sets on safe direct mode", () => {
    const frame = {
      id: "frame-2",
      scene_id: "scene-1",
      character_ids: ["char-1"],
      prop_ids: ["prop-1"],
      composition_data: null,
    };
    const preview = buildStoryboardReferencePreview({
      ...project,
      frames: [
        {
          id: "frame-1",
          scene_id: "scene-1",
          rendered_image_url: "https://example.com/prev-frame.png",
        },
        frame,
      ],
    }, frame, {
      continuityLock: true,
      includeStyleReferences: false,
    });

    const recommendation = recommendCodexImagegenMode(preview);

    expect(recommendation.mode).toBe("safe_refs_only");
    expect(recommendation.metrics.readyCount).toBe(4);
    expect(recommendation.score).toBeLessThan(60);
  });

  it("recommends two-stage mode for multi-reference identity-heavy frames", () => {
    const frame = {
      id: "frame-3",
      scene_id: "scene-1",
      character_ids: ["char-1", "char-2"],
      prop_ids: ["prop-1", "prop-2"],
      composition_data: null,
    };
    const complexProject = {
      ...project,
      frames: [
        {
          id: "frame-2",
          scene_id: "scene-1",
          rendered_image_url: "https://example.com/prev-frame.png",
        },
        frame,
      ],
      characters: [
        ...(project.characters || []),
        {
          id: "char-2",
          name: "周沉",
          full_body_asset: {
            selected_id: "char-2-current",
            variants: [
              {
                id: "char-2-current",
                url: "https://example.com/current-character-2.png",
              },
            ],
          },
        },
      ],
      props: [
        ...(project.props || []),
        {
          id: "prop-2",
          name: "白熊",
          image_url: "https://example.com/current-prop-2.png",
        },
      ],
    };
    const composition = buildStoryboardCompositionData(complexProject, frame, {
      continuityLock: true,
      includeStyleReferences: false,
      codexRecommendationIncludeStyleReferences: true,
    });

    expect(composition.codex_imagegen_recommended_mode).toBe("two_stage_high_consistency");
    expect(composition.codex_imagegen_recommendation).toMatchObject({
      mode: "two_stage_high_consistency",
      metrics: {
        readyCount: 6,
        characterCount: 2,
        propCount: 2,
      },
    });
  });

  it("uses backend recommendation payloads and normalizes snake_case metrics", () => {
    const frame = {
      id: "frame-backend",
      scene_id: "scene-1",
      character_ids: ["char-1"],
      prop_ids: ["prop-1"],
      composition_data: {
        codex_imagegen_recommendation: {
          mode: "two_stage_high_consistency",
          score: 72,
          reason: "后端已计算。",
          metrics: {
            ready_count: 6,
            total_count: 7,
            required_ready_count: 5,
            missing_required_count: 0,
            continuity_count: 1,
            scene_count: 1,
            character_count: 2,
            prop_count: 2,
            style_count: 0,
            identity_count: 4,
            environment_count: 2,
            locked_count: 3,
          },
        },
      },
    };

    const composition = buildStoryboardCompositionData(project, frame, {
      continuityLock: false,
      includeStyleReferences: false,
    });

    expect(composition.codex_imagegen_recommended_mode).toBe("two_stage_high_consistency");
    expect(composition.codex_imagegen_recommendation.metrics).toMatchObject({
      readyCount: 6,
      totalCount: 7,
      characterCount: 2,
      propCount: 2,
      environmentCount: 2,
    });
  });
});
