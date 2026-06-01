// v0.8 (M) — Curated Browse content for the Asset Library.
//
// The Project tab projects over `project.nodes`. Until a creator has
// dropped their own files, that list is empty and the library feels
// dead. The Browse tab solves the cold-start: a hand-picked roster of
// real demo assets the repo already ships under `output/`, served
// through the existing `${API_URL}/files/...` mount.
//
// All `url` strings are repo-relative paths under `output/`. The
// AssetLibrary renderer feeds them through `getAssetUrl()` which adds
// the `${API_URL}/files/` prefix; the shell drop handler persists them
// verbatim into the new MediaNode's `media_urls` (mirroring how
// `createImageNode` stores `upload.url`).
//
// File inventory cross-checked against `output/uploads/`,
// `output/assets/{characters,scenes,props}/`, `output/video/`,
// `output/audio/` on 2026-06-02. If any of these paths get pruned by a
// cleanup script, replace the entry with another file from the same
// folder — the seed roster is intentionally flat and de-coupled from
// the actual node-graph state.

export type AssetSeedKind = "image" | "video" | "audio";

export type AssetSeed = {
  /** Stable id for selection / dedupe. Format: seed-<bucket>-<slug>. */
  id: string;
  kind: AssetSeedKind;
  title: string;
  /** Repo-relative path under output/. Fed through getAssetUrl(). */
  url: string;
  /** One-line affordance hint shown under the title on the card. */
  subtitle?: string;
  /** Display tags. Not used for filtering in v1. */
  tags?: string[];
  /** Image-only secondary category. Mirrors node.data.category. */
  category?: "character" | "scene" | "prop" | "style";
  /** Audio-only sub-role. Mirrors node.data.audio_role. */
  audioRole?: "music" | "sfx" | "voice";
};

export const ATELIER_ASSET_SEEDS: AssetSeed[] = [
  {
    id: "seed-char-portrait-default",
    kind: "image",
    title: "Hero portrait — default avatar",
    url: "uploads/0b31f555-d78e-484d-b9ea-bc6583f07b11.jpeg",
    subtitle: "Drop on a draft to lock the face.",
    category: "character",
    tags: ["portrait", "avatar"],
  },
  {
    id: "seed-char-portrait-alt",
    kind: "image",
    title: "Alt portrait",
    url: "uploads/92cb205e-5177-4c2c-b7c6-624369404f88.png",
    subtitle: "For character-consistency A/B tests.",
    category: "character",
    tags: ["portrait", "alt"],
  },
  {
    id: "seed-char-sheet",
    kind: "image",
    title: "Character turnaround sheet",
    url: "assets/characters/033fbf91-cb92-4c61-8407-d1ae4f547d55_sheet_c8fd4496-6902-4342-82ca-244a79465ef9.png",
    subtitle: "Anchors multi-shot generations.",
    category: "character",
    tags: ["sheet", "turnaround"],
  },
  {
    id: "seed-char-fullbody",
    kind: "image",
    title: "Full-body reference",
    url: "assets/characters/05f49e9b-56dd-42b1-9514-509f85089515_fullbody_39723bd5-4aa8-4d1e-8baa-87e5366f8674.png",
    subtitle: "For action / wide shots.",
    category: "character",
    tags: ["fullbody"],
  },
  {
    id: "seed-scene-wide",
    kind: "image",
    title: "Cinematic wide plate",
    url: "assets/scenes/020af153-56b5-4c54-aedd-1aa1d96ce79f.png",
    subtitle: "Environment establishing shot.",
    category: "scene",
    tags: ["scene", "wide"],
  },
  {
    id: "seed-scene-interior",
    kind: "image",
    title: "Interior set",
    url: "assets/scenes/12468edf-1054-4c41-b37b-cd0b86ee0a06.png",
    subtitle: "Dialogue-coverage backdrop.",
    category: "scene",
    tags: ["scene", "interior"],
  },
  {
    id: "seed-prop-hero",
    kind: "image",
    title: "Hero prop render",
    url: "assets/props/11637c35-ab2a-4582-b2b5-49aa4dc0827c.png",
    subtitle: "Drive an object close-up.",
    category: "prop",
    tags: ["prop", "hero"],
  },
  {
    id: "seed-prop-accent",
    kind: "image",
    title: "Accent prop",
    url: "assets/props/22dc29ae-403b-4985-8890-cacfcd334a5f.png",
    subtitle: "Set dressing detail.",
    category: "prop",
    tags: ["prop", "accent"],
  },
  {
    id: "seed-style-t2i-plate",
    kind: "image",
    title: "Style plate",
    url: "uploads/t2i_7298608358324822891f848e9e87a85a.png",
    subtitle: "Imprint a look onto a draft.",
    category: "style",
    tags: ["style", "t2i"],
  },
  {
    id: "seed-video-take",
    kind: "video",
    title: "Sample take",
    url: "video/merged_59407757-1fac-4d98-a85d-84c2403aaf7a_1768754186.mp4",
    subtitle: "Preview the sequence-strip flow.",
    tags: ["take", "sample"],
  },
  {
    id: "seed-audio-bgm",
    kind: "audio",
    title: "Cinematic BGM bed",
    url: "audio/bg_03f24e52-4ef8-4487-8123-34923583f279.wav",
    subtitle: "For sequence scoring.",
    audioRole: "music",
    tags: ["bgm", "music"],
  },
  {
    id: "seed-audio-voice",
    kind: "audio",
    title: "Voice-over sample",
    url: "audio/wanx_i2v_audio.wav",
    subtitle: "Wire into a dialogue-driven sequence.",
    audioRole: "voice",
    tags: ["voice", "vo"],
  },
];
