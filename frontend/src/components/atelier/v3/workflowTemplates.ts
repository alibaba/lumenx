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
    "story-3-shots",
    "Story · 3 cinematic shots",
    "story",
    "Three video drafts in a row, each starting from a beat idea. Use as the bones of a 15-second story: setup, turn, payoff.",
    ["3 drafts", "Wan 2.7", "16:9"],
    [
      { localId: "idea1", type: "idea", x: 0,                    y: 0,            data: { body: "Setup — the world before anything happens." } },
      { localId: "idea2", type: "idea", x: 0,                    y: 220,          data: { body: "Turn — the moment that changes everything." } },
      { localId: "idea3", type: "idea", x: 0,                    y: 440,          data: { body: "Payoff — the resolution or the cliffhanger." } },
      { localId: "draft1", type: "video", x: 320,                y: 0,            title: "Setup shot",  data: { intent: "Setup shot", model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Setup the scene cinematically." } },
      { localId: "draft2", type: "video", x: 320,                y: 220,          title: "Turn shot",   data: { intent: "Turn shot",  model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "The turning moment — camera reacts." } },
      { localId: "draft3", type: "video", x: 320,                y: 440,          title: "Payoff shot", data: { intent: "Payoff shot", model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Payoff — final beat, hold the frame." } },
    ],
    [],
  ),
  T(
    "character-ref-video",
    "Character · ref → video",
    "character",
    "An empty image draft (drop your character ref into it) feeding one video draft. The reference auto-attaches when the image lands.",
    ["1 ref", "1 draft", "I2V"],
    [
      { localId: "ref",   type: "image", x: 0,                   y: 0,            title: "Character ref" },
      { localId: "draft", type: "video", x: IMG_W + COL_GAP,     y: 0,            title: "Character shot", data: { intent: "Character shot", model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Hero turns to camera, soft falloff." } },
    ],
    [{ from: "ref", to: "draft", kind: "reference" }],
  ),
  T(
    "scene-ref-video",
    "Scene · ref → video",
    "scene",
    "Scene reference image plus a video draft inheriting its mood. Pair with a character template later by dragging the character image in too.",
    ["1 ref", "1 draft", "scene"],
    [
      { localId: "ref",   type: "image", x: 0,                   y: 0,            title: "Scene ref" },
      { localId: "draft", type: "video", x: IMG_W + COL_GAP,     y: 0,            title: "Establishing shot", data: { intent: "Establishing shot", model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Slow push into the location, atmospheric." } },
    ],
    [{ from: "ref", to: "draft", kind: "reference" }],
  ),
  T(
    "product-reveal",
    "Product · 360 reveal",
    "product",
    "Product image plus a video draft tuned for a clean rotating reveal. Drop your packshot into the ref slot; the prompt assumes a neutral background.",
    ["1 ref", "rotation", "1:1"],
    [
      { localId: "ref",   type: "image", x: 0,                   y: 0,            title: "Product packshot" },
      { localId: "draft", type: "video", x: IMG_W + COL_GAP,     y: 0,            title: "Product reveal", data: { intent: "Product reveal", model: "Wan 2.7", config_summary: "1080×1080 · 3s · 4×", prompt: "Slow 360 rotation, neutral seamless backdrop, soft three-point lighting." } },
    ],
    [{ from: "ref", to: "draft", kind: "reference" }],
  ),
  T(
    "motion-study",
    "Motion · 4 variants from one ref",
    "motion",
    "One reference image feeding four parallel video drafts with different motion intents. Generate, judge, pick the winner.",
    ["1 ref", "4 drafts", "compare"],
    [
      { localId: "ref",  type: "image", x: 0,                   y: ROW_GAP * 1.5, title: "Source ref" },
      { localId: "v1",   type: "video", x: IMG_W + COL_GAP,     y: 0,             title: "Slow push",  data: { intent: "Slow push",  model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Slow camera push toward the subject, almost imperceptible." } },
      { localId: "v2",   type: "video", x: IMG_W + COL_GAP,     y: ROW_GAP * 1.5, title: "Whip pan",   data: { intent: "Whip pan",   model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Whip pan around the subject ending tight on the eyes." } },
      { localId: "v3",   type: "video", x: IMG_W + COL_GAP,     y: ROW_GAP * 3,   title: "Pull back",  data: { intent: "Pull back",  model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Camera pulls back fast revealing the wider context." } },
      { localId: "v4",   type: "video", x: IMG_W + COL_GAP,     y: ROW_GAP * 4.5, title: "Hold still", data: { intent: "Hold still", model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×", prompt: "Locked-off hold; only ambient micromovement, breathing." } },
    ],
    [
      { from: "ref", to: "v1", kind: "reference" },
      { from: "ref", to: "v2", kind: "reference" },
      { from: "ref", to: "v3", kind: "reference" },
      { from: "ref", to: "v4", kind: "reference" },
    ],
  ),
  T(
    "blank-canvas",
    "Blank · single draft",
    "utility",
    "Just one empty video draft at the cursor. Useful when you'd otherwise reach for the V shortcut but want it positioned exactly where you're looking.",
    ["1 draft"],
    [
      { localId: "draft", type: "video", x: 0, y: 0, title: "New draft", data: { intent: "New draft", model: "Wan 2.7", config_summary: "1280×720 · 5s · 4×" } },
    ],
    [],
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
