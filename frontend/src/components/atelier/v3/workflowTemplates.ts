import type { AtelierNodeType } from "@/lib/atelierTypes";

export const WORKFLOW_TEMPLATE_MIME = "application/x-lumenx-workflow-template";

export type WorkflowTemplateCategory = "story" | "image" | "video" | "audio";

export type WorkflowTemplateNode = {
  localId: string;
  type: AtelierNodeType;
  title: string;
  prompt?: string;
  body?: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

export type WorkflowTemplateEdge = {
  id: string;
  kind: "reference";
  source: string;
  target: string;
};

export type WorkflowTemplate = {
  id: string;
  version: 1;
  category: WorkflowTemplateCategory;
  title: string;
  summary: string;
  nodes: WorkflowTemplateNode[];
  edges: WorkflowTemplateEdge[];
};

export type WorkflowTemplateValidation = { ok: true } | { ok: false; reason: string };

const node = (value: WorkflowTemplateNode): WorkflowTemplateNode => value;
const edge = (id: string, source: string, target: string): WorkflowTemplateEdge => ({ id, source, target, kind: "reference" });

export const BUILT_IN_WORKFLOW_TEMPLATES: WorkflowTemplate[] = [
  {
    id: "story-seed-to-shot",
    version: 1,
    category: "story",
    title: "Seed to shot",
    summary: "Turn a rough idea into a draft beat, reference image, and first motion target.",
    nodes: [
      node({ localId: "idea", type: "idea", title: "Story seed", body: "Write the one-sentence emotional hook here.", x: 0, y: 0, width: 260, height: 160 }),
      node({ localId: "draft", type: "draft", title: "Draft beat", prompt: "Describe the beat, camera intent, and subject action.", x: 320, y: 0, width: 300, height: 180 }),
      node({ localId: "reference", type: "image", title: "Future key image", prompt: "Create or upload the visual reference for the beat.", x: 320, y: 240, width: 260, height: 180 }),
      node({ localId: "motion", type: "video", title: "Motion target", prompt: "Use the key image as the first visual anchor for motion.", x: 680, y: 120, width: 260, height: 146 }),
    ],
    edges: [edge("reference-to-motion", "reference", "motion")],
  },
  {
    id: "image-variant-ladder",
    version: 1,
    category: "image",
    title: "Image variant ladder",
    summary: "Start with a base image placeholder, then branch into two refinement targets.",
    nodes: [
      node({ localId: "base", type: "image", title: "Base image", prompt: "Upload or generate the base composition.", x: 0, y: 100, width: 260, height: 180 }),
      node({ localId: "style", type: "image", title: "Style refinement", prompt: "Explore lighting, palette, and texture changes from the base image.", x: 340, y: 0, width: 260, height: 180 }),
      node({ localId: "detail", type: "image", title: "Detail refinement", prompt: "Explore detail, expression, and prop changes from the base image.", x: 340, y: 230, width: 260, height: 180 }),
    ],
    edges: [edge("base-to-style", "base", "style"), edge("base-to-detail", "base", "detail")],
  },
  {
    id: "video-shot-builder",
    version: 1,
    category: "video",
    title: "Shot builder",
    summary: "Prepare first-frame, optional sound cue, and two motion takes for comparison.",
    nodes: [
      node({ localId: "frame", type: "image", title: "First frame", prompt: "The still frame that anchors the motion.", x: 0, y: 120, width: 260, height: 180 }),
      node({ localId: "sound", type: "audio", title: "Sound cue", prompt: "Narration, ambience, or cue for this shot.", x: 0, y: 360, width: 260, height: 96 }),
      node({ localId: "takeA", type: "video", title: "Motion take A", prompt: "A controlled, readable motion pass.", x: 360, y: 40, width: 260, height: 146 }),
      node({ localId: "takeB", type: "video", title: "Motion take B", prompt: "A more expressive alternate motion pass.", x: 360, y: 260, width: 260, height: 146 }),
    ],
    edges: [edge("frame-to-a", "frame", "takeA"), edge("frame-to-b", "frame", "takeB"), edge("sound-to-a", "sound", "takeA"), edge("sound-to-b", "sound", "takeB")],
  },
  {
    id: "audio-narration-pass",
    version: 1,
    category: "audio",
    title: "Narration pass",
    summary: "Draft narration text, generate a voice placeholder, and connect it to a scene target.",
    nodes: [
      node({ localId: "script", type: "comment", title: "Narration script", body: "Write the line or voice direction here.", x: 0, y: 0, width: 260, height: 160 }),
      node({ localId: "voice", type: "audio", title: "Voice placeholder", prompt: "Generate or upload narration audio.", x: 320, y: 0, width: 260, height: 96 }),
      node({ localId: "scene", type: "video", title: "Scene target", prompt: "Attach narration once the voice placeholder is filled.", x: 660, y: 0, width: 260, height: 146 }),
    ],
    edges: [edge("voice-to-scene", "voice", "scene")],
  },
  {
    id: "character-intro-beat",
    version: 1,
    category: "story",
    title: "Character intro beat",
    summary: "Shape a character note, key portrait, and intro motion target.",
    nodes: [
      node({ localId: "note", type: "idea", title: "Character note", body: "Who is this character and what changes in this beat?", x: 0, y: 0, width: 260, height: 160 }),
      node({ localId: "portrait", type: "image", title: "Portrait reference", prompt: "A clear character portrait or identity reference.", x: 320, y: 0, width: 260, height: 180 }),
      node({ localId: "intro", type: "video", title: "Intro shot", prompt: "Introduce the character through motion, framing, and rhythm.", x: 660, y: 0, width: 260, height: 146 }),
    ],
    edges: [edge("portrait-to-intro", "portrait", "intro")],
  },
  {
    id: "sequence-beat-board",
    version: 1,
    category: "video",
    title: "Beat board",
    summary: "Lay out three connected visual beats ready for future media fill-in.",
    nodes: [
      node({ localId: "beat1", type: "image", title: "Beat 1 image", prompt: "Opening visual anchor.", x: 0, y: 0, width: 260, height: 180 }),
      node({ localId: "beat2", type: "image", title: "Beat 2 image", prompt: "Middle visual anchor.", x: 0, y: 240, width: 260, height: 180 }),
      node({ localId: "clip1", type: "video", title: "Clip 1", prompt: "Motion for beat 1.", x: 340, y: 0, width: 260, height: 146 }),
      node({ localId: "clip2", type: "video", title: "Clip 2", prompt: "Motion for beat 2.", x: 340, y: 240, width: 260, height: 146 }),
    ],
    edges: [edge("beat1-to-clip1", "beat1", "clip1"), edge("beat2-to-clip2", "beat2", "clip2")],
  },
];

const safeNodeKeys = new Set(["localId", "type", "title", "prompt", "body", "x", "y", "width", "height"]);
const blockedPattern = /provider_execution|credential|fingerprint|generation_attempt|generation_request|candidate|handle|authority|agent_authorization|canvas_approval|absolute_path/i;

export function validateBuiltInWorkflowTemplate(template: WorkflowTemplate): WorkflowTemplateValidation {
  if (!template.id || template.version !== 1 || !template.title || !template.nodes.length) return { ok: false, reason: "invalid template identity" };
  if (blockedPattern.test(JSON.stringify(template)) || /Wan 2\.7/i.test(JSON.stringify(template))) return { ok: false, reason: "template contains authority or stale provider copy" };
  const ids = new Set<string>();
  for (const node of template.nodes) {
    if (!node.localId || ids.has(node.localId)) return { ok: false, reason: "duplicate local node id" };
    ids.add(node.localId);
    if (!node.title || !node.type || !Number.isFinite(node.x) || !Number.isFinite(node.y) || node.width <= 0 || node.height <= 0) return { ok: false, reason: "invalid node fields" };
    if (Object.keys(node).some(key => !safeNodeKeys.has(key))) return { ok: false, reason: "unsupported node field" };
  }
  const edgeIds = new Set<string>();
  for (const item of template.edges) {
    if (!item.id || edgeIds.has(item.id) || item.kind !== "reference" || !ids.has(item.source) || !ids.has(item.target)) return { ok: false, reason: "invalid edge" };
    edgeIds.add(item.id);
  }
  return { ok: true };
}

export function getBuiltInWorkflowTemplate(id: string): WorkflowTemplate | null {
  return BUILT_IN_WORKFLOW_TEMPLATES.find(template => template.id === id) ?? null;
}
