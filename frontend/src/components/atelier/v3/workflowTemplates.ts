// Workflow templates — local registry (Sprint C, per Codex doc §4.7 / §7.7).
//
// Each template is a recipe that creates a small group of nodes + reference
// edges + sensible default model/params. Designed to teach the canvas
// vocabulary and unblock first-five-minute users — instead of starting on
// a blank screen, pick a recipe that mirrors what you want to make.
//
// Schema is deliberately small. Coordinates are template-local (anchored
// at 0,0); the shell's insert handler offsets them to the current viewport
// center so the new cluster lands wherever the user is looking.
//
// Why local JSON and not a remote catalog?
//   - Zero backend dependency for v1 — ships with the frontend.
//   - Open-source friendly: anyone can fork and add their own.
//   - Gives the user a working sample to learn from without trusting
//     a marketplace's curation.

export type TemplateCategory =
  | "story"
  | "character"
  | "scene"
  | "product"
  | "motion"
  | "utility";

export interface TemplateNode {
  /** Local id used by edges. Not persisted; shell maps to real ids on
   *  insert. */
  localId: string;
  type: "image" | "video" | "idea" | "comment";
  x: number;
  y: number;
  title?: string;
  /** For drafts: data.intent + data.model + data.config_summary +
   *  data.prompt. For ideas / comments: data.body. */
  data?: Record<string, unknown>;
}

export interface TemplateEdge {
  /** Source localId (must match a node's localId). */
  from: string;
  /** Target localId. */
  to: string;
  /** Currently only "reference" is supported — attaches `from` as a
   *  reference image on the target draft. (Other edge kinds reserved
   *  for future expansion.) */
  kind: "reference";
}

export interface WorkflowTemplate {
  id: string;
  name: string;
  category: TemplateCategory;
  description: string;
  /** Tag chips shown on the template card — short hints about what
   *  the user will get out of running it. */
  tags: string[];
  nodes: TemplateNode[];
  edges: TemplateEdge[];
}

const T = (
  id: string,
  name: string,
  category: TemplateCategory,
  description: string,
  tags: string[],
  nodes: TemplateNode[],
  edges: TemplateEdge[],
): WorkflowTemplate => ({ id, name, category, description, tags, nodes, edges });

// Default geometry. A cluster takes ~700-900 px wide, fits 2-3 layers of
// nodes deep. Cards stack with 320 px horizontal gap, 220 px vertical.
const DRAFT_W = 244;
const IMG_W = 244;
const COL_GAP = 80;
const ROW_GAP = 60;

export const WORKFLOW_TEMPLATES: WorkflowTemplate[] = [
  T(
    "hero-shot-composer",
    "Hero shot composer",
    "character",
    "Layer a character, scene, and lighting reference into one polished hero video.",
    ["3 refs", "1 draft", "Wan 2.7"],
    [
      { localId: "charRef",  type: "image", x: 0,                   y: 0,             title: "Character ref" },
      { localId: "sceneRef", type: "image", x: 0,                   y: ROW_GAP * 3,   title: "Scene ref" },
      { localId: "lightRef", type: "image", x: 0,                   y: ROW_GAP * 6,   title: "Lighting ref" },
      { localId: "hero",     type: "video", x: IMG_W + COL_GAP,     y: ROW_GAP * 3,   title: "Hero shot",     data: { intent: "Hero shot",     model: "Wan 2.7", config_summary: "1920×1080 · 5s · 4×", prompt: "Hero centered in the scene, light keyed from the lighting ref, soft fall-off, cinematic depth." } },
    ],
    [
      { from: "charRef",  to: "hero", kind: "reference" },
      { from: "sceneRef", to: "hero", kind: "reference" },
      { from: "lightRef", to: "hero", kind: "reference" },
    ],
  ),
  T(
    "storyboard-beat-sweep",
    "Storyboard beat sweep",
    "story",
    "Five beat ideas wired left-to-right above five matching video drafts — a sequence skeleton.",
    ["5 drafts", "sequence", "16:9"],
    [
      { localId: "idea1", type: "idea",  x: 0 * (IMG_W + COL_GAP), y: 0,           data: { body: "Open — establish the world." } },
      { localId: "idea2", type: "idea",  x: 1 * (IMG_W + COL_GAP), y: 0,           data: { body: "Build — pressure mounts." } },
      { localId: "idea3", type: "idea",  x: 2 * (IMG_W + COL_GAP), y: 0,           data: { body: "Hit — the moment." } },
      { localId: "idea4", type: "idea",  x: 3 * (IMG_W + COL_GAP), y: 0,           data: { body: "Aftermath — let it land." } },
      { localId: "idea5", type: "idea",  x: 4 * (IMG_W + COL_GAP), y: 0,           data: { body: "Tail — exit on a hook." } },
      { localId: "draft1", type: "video", x: 0 * (IMG_W + COL_GAP), y: ROW_GAP * 3, title: "Open",       data: { intent: "Open — establish the world.",  model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Open — establish the world." } },
      { localId: "draft2", type: "video", x: 1 * (IMG_W + COL_GAP), y: ROW_GAP * 3, title: "Build",      data: { intent: "Build — pressure mounts.",     model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Build — pressure mounts." } },
      { localId: "draft3", type: "video", x: 2 * (IMG_W + COL_GAP), y: ROW_GAP * 3, title: "Hit",        data: { intent: "Hit — the moment.",            model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Hit — the moment." } },
      { localId: "draft4", type: "video", x: 3 * (IMG_W + COL_GAP), y: ROW_GAP * 3, title: "Aftermath",  data: { intent: "Aftermath — let it land.",     model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Aftermath — let it land." } },
      { localId: "draft5", type: "video", x: 4 * (IMG_W + COL_GAP), y: ROW_GAP * 3, title: "Tail",       data: { intent: "Tail — exit on a hook.",       model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Tail — exit on a hook." } },
    ],
    [],
  ),
  T(
    "tonal-variant-pass",
    "Tonal variant pass",
    "motion",
    "One scene ref, three drafts tuned for a different emotional read.",
    ["1 ref", "3 drafts", "mood"],
    [
      { localId: "ref",   type: "image", x: 0,                   y: ROW_GAP * 3, title: "Source ref" },
      { localId: "warm",  type: "video", x: IMG_W + COL_GAP,     y: 0,           title: "Warm read",  data: { intent: "Warm read",  model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Same scene, warm key + slow pace, lens breathing." } },
      { localId: "cool",  type: "video", x: IMG_W + COL_GAP,     y: ROW_GAP * 3, title: "Cool read",  data: { intent: "Cool read",  model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Same scene, cool palette + clipped pace, locked-off frame." } },
      { localId: "tense", type: "video", x: IMG_W + COL_GAP,     y: ROW_GAP * 6, title: "Tense read", data: { intent: "Tense read", model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Same scene, hand-held, quick parallax, low-key fill." } },
    ],
    [
      { from: "ref", to: "warm",  kind: "reference" },
      { from: "ref", to: "cool",  kind: "reference" },
      { from: "ref", to: "tense", kind: "reference" },
    ],
  ),
  T(
    "voice-and-footage-mix",
    "Voice and footage mix",
    "utility",
    "Image ref into a B-roll draft with the VO line scaffolded as a comment for handoff.",
    ["1 ref", "1 draft", "VO"],
    [
      { localId: "ref",   type: "image",   x: 0,                   y: 0,           title: "B-roll ref" },
      { localId: "draft", type: "video",   x: IMG_W + COL_GAP,     y: 0,           title: "B-roll clip", data: { intent: "B-roll clip", model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Locked-off B-roll with gentle parallax; leave headroom for VO." } },
      { localId: "vo",    type: "comment", x: IMG_W + COL_GAP,     y: ROW_GAP * 3, data: { body: "VO line: \"[Write the voiceover here — keep it under 12 seconds, match the cut.]\"" } },
    ],
    [{ from: "ref", to: "draft", kind: "reference" }],
  ),
  T(
    "product-spin-reveal",
    "Product spin reveal",
    "product",
    "One packshot, a clean 360 spin, and a tight macro insert for the detail cut.",
    ["1 ref", "2 drafts", "1:1"],
    [
      { localId: "ref",   type: "image", x: 0,                   y: ROW_GAP * 1.5, title: "Product packshot" },
      { localId: "spin",  type: "video", x: IMG_W + COL_GAP,     y: 0,             title: "360 reveal",    data: { intent: "360 reveal",   model: "Wan 2.7", config_summary: "1080×1080 · 3s · 4×", prompt: "Slow 360 rotation, neutral seamless backdrop, soft three-point lighting." } },
      { localId: "macro", type: "video", x: IMG_W + COL_GAP,     y: ROW_GAP * 3,   title: "Macro insert",  data: { intent: "Macro insert", model: "Wan 2.7", config_summary: "1080×1080 · 3s · 4×", prompt: "Macro pass across the product surface — material, seams, micro highlights." } },
    ],
    [
      { from: "ref", to: "spin",  kind: "reference" },
      { from: "ref", to: "macro", kind: "reference" },
    ],
  ),
  T(
    "wide-to-tight-coverage",
    "Wide to tight coverage",
    "scene",
    "One scene ref drives a wide establishing shot and a paired tight detail.",
    ["1 ref", "2 drafts", "coverage"],
    [
      { localId: "ref",   type: "image", x: 0,                   y: ROW_GAP * 1.5, title: "Scene ref" },
      { localId: "wide",  type: "video", x: IMG_W + COL_GAP,     y: 0,             title: "Wide establishing", data: { intent: "Wide establishing", model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Slow push from wide, atmospheric, hold negative space." } },
      { localId: "tight", type: "video", x: IMG_W + COL_GAP,     y: ROW_GAP * 3,   title: "Tight detail",      data: { intent: "Tight detail",      model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Macro inserts of the small textures and edges in the scene." } },
    ],
    [
      { from: "ref", to: "wide",  kind: "reference" },
      { from: "ref", to: "tight", kind: "reference" },
    ],
  ),
];

export const TEMPLATE_CATEGORY_LABELS: Record<TemplateCategory, string> = {
  story: "Story",
  character: "Character",
  scene: "Scene",
  product: "Product",
  motion: "Motion",
  utility: "Utility",
};

// Use these when computing layout — exposed so the shell's insert handler
// stays in sync with template authors' assumptions.
export const TEMPLATE_GEOMETRY = {
  DRAFT_W,
  IMG_W,
  COL_GAP,
  ROW_GAP,
};
