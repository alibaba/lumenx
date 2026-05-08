import { describe, expect, it } from "vitest";

import { buildStoryboardCompositionData } from "../lib/storyboard-references";

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
