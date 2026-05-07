import type { SeedanceWorkflow } from "@/lib/seedance";

export type SeedancePromptGenerationMode = "i2v" | "r2v";
export type SeedancePromptTemplateMode = "replace" | "append";
export type SeedancePromptTemplateCategory =
  | "all"
  | "cinematic"
  | "short_drama"
  | "anime"
  | "product"
  | "lifestyle"
  | "vfx"
  | "workflow";

type TemplateWorkflows = SeedanceWorkflow | "all";
type TemplateWorkflowMode =
  | "all"
  | "continue"
  | "prepend"
  | "trajectory"
  | "subject_replace"
  | "object_edit"
  | "inpaint";

export interface SeedancePromptBlock {
  id: string;
  title: string;
  summary: string;
  prompt: string;
  tags: string[];
  category: Exclude<SeedancePromptTemplateCategory, "all">;
  generationModes: SeedancePromptGenerationMode[];
  workflows: TemplateWorkflows[];
  workflowModes?: TemplateWorkflowMode[];
}

export const SEEDANCE_PROMPT_TEMPLATE_CATEGORIES: {
  value: SeedancePromptTemplateCategory;
  label: string;
}[] = [
  { value: "all", label: "全部" },
  { value: "cinematic", label: "电影感" },
  { value: "short_drama", label: "短剧情绪" },
  { value: "anime", label: "动漫动作" },
  { value: "product", label: "广告产品" },
  { value: "lifestyle", label: "生活化" },
  { value: "vfx", label: "特效奇观" },
  { value: "workflow", label: "工作流专用" },
];

export const SEEDANCE_PROMPT_SCAFFOLDS: SeedancePromptBlock[] = [
  {
    id: "structure-six-part",
    title: "六段控制骨架",
    summary: "通用 I2V 骨架，适合先搭结构再填细节。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["all"],
    tags: ["结构", "主体", "镜头", "约束"],
    prompt: [
      "[Main subject] in [environment].",
      "Opening state: [pose / emotion / screen direction].",
      "Action timeline: beat 1 -> beat 2 -> beat 3.",
      "Camera: [shot size] + [angle] + [movement path] + [movement speed].",
      "Light and texture: [lighting / atmosphere / material details].",
      "Constraints: keep the same identity, no extra subjects, no scene jump.",
    ].join("\n"),
  },
  {
    id: "structure-timeline",
    title: "三拍时间线骨架",
    summary: "适合补动作顺序和节奏，不容易写成堆词。",
    category: "workflow",
    generationModes: ["i2v", "r2v"],
    workflows: ["all"],
    tags: ["时间线", "节奏", "镜头"],
    prompt: [
      "Beat 1: [what the viewer sees first].",
      "Beat 2: [main action or emotional turn].",
      "Beat 3: [ending pose / landing frame].",
      "Camera follows with [static / slow push-in / arc / pan / handheld drift].",
      "Visible details: [wind / cloth / reflections / dust / particles / facial micro-expression].",
    ].join("\n"),
  },
  {
    id: "workflow-extend",
    title: "延长续写骨架",
    summary: "专门用于 extend，强调无缝接续和路径延续。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["extend"],
    workflowModes: ["all", "continue"],
    tags: ["extend", "续写", "无缝"],
    prompt: [
      "Continue the exact action, pacing, camera path, and lighting logic from the reference video.",
      "The subject keeps moving along the established trajectory without a scene reset.",
      "Preserve the same screen direction, subject scale, and background continuity.",
      "End on a clean continuation beat that can still be extended further.",
    ].join("\n"),
  },
  {
    id: "workflow-edit",
    title: "编辑改造骨架",
    summary: "专门用于 edit，强调只改目标，不破坏原镜头逻辑。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["edit"],
    workflowModes: ["all"],
    tags: ["edit", "保持原镜头", "局部改造"],
    prompt: [
      "Keep the original scene timing, camera path, composition, lighting, and background unchanged.",
      "Only edit the target element: [replace / remove / transform target].",
      "The edited subject must match the scene perspective, scale, material response, and motion continuity.",
      "Avoid collateral changes to unrelated characters or props.",
    ].join("\n"),
  },
  {
    id: "workflow-extend-prepend",
    title: "前置补帧骨架",
    summary: "专门用于 prepend，强调向前补镜头而不是重开一条新视频。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["extend"],
    workflowModes: ["prepend"],
    tags: ["extend", "prepend", "前置"],
    prompt: [
      "Generate a seamless lead-in that happens immediately before the reference video begins.",
      "Match the same subject identity, screen direction, lighting logic, and camera language from frame one of the reference clip.",
      "Build natural anticipation motion that lands exactly into the original opening state.",
      "No new subject, no scene reset, no style drift, no mismatch in pacing.",
    ].join("\n"),
  },
  {
    id: "workflow-extend-trajectory",
    title: "轨迹续写骨架",
    summary: "专门用于 trajectory，强调沿既有运动轨迹继续前进。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["extend"],
    workflowModes: ["trajectory"],
    tags: ["extend", "trajectory", "路径"],
    prompt: [
      "Continue the exact movement trajectory already established by the reference video.",
      "Preserve subject momentum, body direction, camera path curvature, and spatial relation to the background.",
      "Keep the same scene geography, motion arcs, and acceleration logic.",
      "The continuation must feel like the next beat of the same uninterrupted shot.",
    ].join("\n"),
  },
  {
    id: "workflow-edit-subject-replace",
    title: "主体替换骨架",
    summary: "专门用于 subject_replace，替换主体但不改镜头世界。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["edit"],
    workflowModes: ["subject_replace"],
    tags: ["edit", "subject_replace", "主体替换"],
    prompt: [
      "Keep the original timing, camera path, background, and interaction logic unchanged.",
      "Replace the main subject with [new subject] while matching the same scale, perspective, lighting, and motion behavior.",
      "The new subject must feel native to the shot and inherit the same emotional beat.",
      "Do not alter unrelated props, characters, or environment details.",
    ].join("\n"),
  },
  {
    id: "workflow-edit-object-edit",
    title: "对象增删改骨架",
    summary: "专门用于 object_edit，局部改造道具、物件或环境元素。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["edit"],
    workflowModes: ["object_edit"],
    tags: ["edit", "object_edit", "局部编辑"],
    prompt: [
      "Keep the original subject, timing, camera path, and lighting logic unchanged.",
      "Only edit the target object: [add / remove / replace / transform target object].",
      "Match the object's perspective, occlusion, material response, and motion blur to the original shot.",
      "Avoid any collateral change to faces, costume details, or background composition.",
    ].join("\n"),
  },
  {
    id: "workflow-edit-inpaint",
    title: "局部重绘骨架",
    summary: "专门用于 inpaint，修补遮挡、空洞或局部区域。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["edit"],
    workflowModes: ["inpaint"],
    tags: ["edit", "inpaint", "修补"],
    prompt: [
      "Only repair the masked area while preserving the rest of the shot exactly as-is.",
      "Blend the edited region with the original perspective, edge quality, lighting falloff, texture, and motion continuity.",
      "The patch must be invisible as an edit and should not shift composition or timing.",
      "No change to unrelated characters, props, or camera behavior.",
    ].join("\n"),
  },
  {
    id: "workflow-r2v-dialogue",
    title: "R2V 对戏骨架",
    summary: "适合角色驱动对白或互动场景。",
    category: "workflow",
    generationModes: ["r2v"],
    workflows: ["all"],
    workflowModes: ["all"],
    tags: ["r2v", "对白", "blocking"],
    prompt: [
      "Scene setup: [environment, mood, light].",
      "character1 starts with [pose / gaze / emotion].",
      "character2 enters or reacts with [gesture / movement].",
      "Action timeline: [character1 action] -> [character2 response] -> [shared ending beat].",
      'Dialogue: character1 says: "[line]"; character2 says: "[line]".',
      "Camera: [two-shot / over-the-shoulder / close-up] with [movement].",
    ].join("\n"),
  },
];

export const SEEDANCE_PROMPT_TEMPLATES: SeedancePromptBlock[] = [
  {
    id: "cinematic-neon-reveal",
    title: "霓虹电影揭示",
    summary: "典型电影感开场，适合都市、悬疑、赛博题材。",
    category: "cinematic",
    generationModes: ["i2v"],
    workflows: ["standard"],
    workflowModes: ["all"],
    tags: ["夜景", "电影感", "慢推"],
    prompt: [
      "A lone protagonist stands in a rain-soaked alley at night, neon reflections trembling on the wet pavement.",
      "The character slowly turns toward camera as coat hems lift in the wind.",
      "Camera starts from a medium rear three-quarter shot, then makes a slow arc into a close profile reveal.",
      "Cold rim light, drifting vapor, realistic fabric motion, restrained cinematic pacing, no scene change.",
    ].join(" "),
  },
  {
    id: "short-drama-emotional-turn",
    title: "短剧情绪转折",
    summary: "适合人物关系、沉默反应、表情戏。",
    category: "short_drama",
    generationModes: ["i2v", "r2v"],
    workflows: ["all"],
    workflowModes: ["all"],
    tags: ["微表情", "关系戏", "情绪"],
    prompt: [
      "Two characters face each other in a quiet room after an argument.",
      "One tightens their hand, exhales, then looks away; the other hesitates before stepping closer.",
      "Camera holds a tense medium two-shot, then slowly pushes toward the speaker at the emotional beat.",
      "Warm practical light, shallow depth, visible breathing rhythm, subtle facial micro-expression, grounded realism.",
    ].join(" "),
  },
  {
    id: "anime-action-chase",
    title: "动漫追击动作",
    summary: "适合战斗、跑酷、冲刺、能量释放。",
    category: "anime",
    generationModes: ["i2v"],
    workflows: ["standard"],
    workflowModes: ["all"],
    tags: ["动漫", "追击", "动势"],
    prompt: [
      "A young fighter dashes forward across shattered ground, cloak trailing and sparks scattering behind the heels.",
      "The body leans into acceleration, then snaps into a decisive slash at the end beat.",
      "Camera tracks low and fast beside the subject before tilting upward on the impact moment.",
      "Stylized speed lines, crisp silhouette, bold contrast, dynamic debris, high-energy anime action.",
    ].join(" "),
  },
  {
    id: "product-hero-commercial",
    title: "产品英雄广告",
    summary: "适合单品展示、质感特写和品牌感镜头。",
    category: "product",
    generationModes: ["i2v"],
    workflows: ["standard", "edit"],
    workflowModes: ["all", "object_edit"],
    tags: ["产品", "广告", "质感"],
    prompt: [
      "A premium product sits centered on a reflective platform with precise studio lighting.",
      "The object rotates slightly as highlights travel across the surface and tiny particles drift in the background.",
      "Camera begins with a clean macro close-up, then eases into a polished hero angle.",
      "Luxury commercial pacing, sharp material definition, controlled reflections, elegant minimal background.",
    ].join(" "),
  },
  {
    id: "lifestyle-observational",
    title: "生活化观察镜头",
    summary: "适合 vlog、日常记录、轻纪实氛围。",
    category: "lifestyle",
    generationModes: ["i2v"],
    workflows: ["standard"],
    workflowModes: ["all"],
    tags: ["生活化", "纪实", "轻运动"],
    prompt: [
      "A person prepares a simple everyday task in a sunlit room, moving naturally and without performance exaggeration.",
      "Hands adjust objects, the body shifts weight, and small secondary motions keep the frame alive.",
      "Camera uses a gentle handheld drift with documentary restraint.",
      "Soft daylight, believable shadows, tactile surfaces, calm observational pacing.",
    ].join(" "),
  },
  {
    id: "vfx-transformation",
    title: "特效形态变化",
    summary: "适合转化、能量爆发、奇观镜头。",
    category: "vfx",
    generationModes: ["i2v"],
    workflows: ["standard", "edit"],
    workflowModes: ["all", "subject_replace", "object_edit", "inpaint"],
    tags: ["变形", "特效", "粒子"],
    prompt: [
      "The subject begins in a stable pose, then a controlled wave of light spreads across the body and transforms the visible form.",
      "Energy wraps around edges, particles trail the motion, and surface details visibly shift during the change.",
      "Camera holds the center mass clearly, then pushes in at the peak transformation moment.",
      "Readable VFX layering, strong silhouette preservation, cinematic impact without losing the original scene logic.",
    ].join(" "),
  },
  {
    id: "workflow-extend-seamless",
    title: "无缝续帧延长",
    summary: "专门针对延长工作流的自然续接。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["extend"],
    workflowModes: ["all", "continue"],
    tags: ["extend", "连续", "镜头路径"],
    prompt: [
      "Continue the existing shot seamlessly from the current ending frame.",
      "The subject keeps the same momentum, facing direction, and emotional state while the background motion remains coherent.",
      "Camera continues the established path without resetting composition or speed.",
      "No jump cut feeling, no new subject, no sudden lighting change.",
    ].join(" "),
  },
  {
    id: "workflow-edit-target-replace",
    title: "局部替换编辑",
    summary: "专门针对 edit 的精准替换或改造。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["edit"],
    workflowModes: ["all", "object_edit"],
    tags: ["edit", "替换", "局部"],
    prompt: [
      "Keep the original scene and motion unchanged.",
      "Replace the target object with [new object] while preserving perspective, occlusion, lighting, and interaction timing.",
      "The edited target must feel native to the shot, with matching material response and motion blur.",
      "No changes to unrelated people, camera, or background.",
    ].join(" "),
  },
  {
    id: "workflow-extend-prepend-anticipation",
    title: "前置动作铺垫",
    summary: "补出进入原视频前的起手动作，适合人物起步和镜头预备。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["extend"],
    workflowModes: ["prepend"],
    tags: ["prepend", "铺垫", "前奏"],
    prompt: [
      "Generate the moments immediately before the reference clip starts.",
      "Show a clean anticipation beat that lands naturally into the exact first frame pose and camera composition of the source video.",
      "Maintain the same subject scale, light direction, and emotional state.",
      "No jump cut, no new subject, no different location.",
    ].join(" "),
  },
  {
    id: "workflow-extend-trajectory-chase",
    title: "轨迹跟续镜头",
    summary: "适合追逐、奔跑、转身等已经建立路径的镜头。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["extend"],
    workflowModes: ["trajectory"],
    tags: ["trajectory", "追随", "路径连续"],
    prompt: [
      "Continue the same trajectory already established in the source shot.",
      "The subject keeps moving on the same path with coherent acceleration, body lean, and screen direction.",
      "Camera follows the same curve and maintains the same relationship to the background.",
      "Preserve continuity of lighting, costume motion, and environment geography.",
    ].join(" "),
  },
  {
    id: "workflow-edit-subject-swap-clean",
    title: "主体无缝替换",
    summary: "适合换人、换主角或换主体，但保留整个镜头结构。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["edit"],
    workflowModes: ["subject_replace"],
    tags: ["subject_replace", "换主体"],
    prompt: [
      "Replace the main subject with [new subject] while preserving the original shot timing, camera move, blocking, lighting, and background.",
      "The replacement subject must match the same perspective, motion rhythm, and interaction logic.",
      "No changes to the untouched environment or secondary props.",
    ].join(" "),
  },
  {
    id: "workflow-edit-object-cleanup",
    title: "对象局部改造",
    summary: "适合单个道具、服装部件或局部环境元素的增删改。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["edit"],
    workflowModes: ["object_edit"],
    tags: ["object_edit", "道具编辑"],
    prompt: [
      "Only modify the target object and keep the original character, camera, and scene timing intact.",
      "Make the edited object physically plausible with matching perspective, shadows, reflections, and motion blur.",
      "Do not alter face identity, costume silhouette, or unrelated background elements.",
    ].join(" "),
  },
  {
    id: "workflow-edit-inpaint-repair",
    title: "遮挡修补",
    summary: "适合修手、补边、补道具、抹掉多余内容等局部重绘。",
    category: "workflow",
    generationModes: ["i2v"],
    workflows: ["edit"],
    workflowModes: ["inpaint"],
    tags: ["inpaint", "修补", "局部重绘"],
    prompt: [
      "Repair only the masked region and blend it seamlessly into the untouched shot.",
      "Match local texture, lighting falloff, edge softness, and motion continuity.",
      "The repaired area should be invisible as an edit and should not shift the global composition.",
    ].join(" "),
  },
  {
    id: "r2v-dialogue-closeup",
    title: "R2V 对白特写",
    summary: "适合双人对白、反应镜头、情绪拉扯。",
    category: "short_drama",
    generationModes: ["r2v"],
    workflows: ["all"],
    workflowModes: ["all"],
    tags: ["r2v", "对白", "近景"],
    prompt: [
      "character1 listens in silence, then slowly turns toward character2.",
      "character2 steps into the frame edge, pauses, and speaks with controlled emotion.",
      "Camera starts on a medium two-shot and slowly pushes into character1 at the reaction beat.",
      'Dialogue: character2 says: "[line]".',
      "Keep eye-line continuity, body direction, and emotional pacing grounded.",
    ].join(" "),
  },
];

function matchesContext(
  item: SeedancePromptBlock,
  generationMode: SeedancePromptGenerationMode,
  workflow: SeedanceWorkflow,
  workflowMode?: string,
) {
  const modeMatched = item.generationModes.includes(generationMode);
  const workflowMatched = item.workflows.includes("all") || item.workflows.includes(workflow);
  const workflowModes = item.workflowModes || ["all"];
  const workflowModeMatched =
    workflowModes.includes("all") || !workflowMode || workflowModes.includes(workflowMode as TemplateWorkflowMode);
  return modeMatched && workflowMatched && workflowModeMatched;
}

export function getSeedancePromptScaffolds(input: {
  generationMode: SeedancePromptGenerationMode;
  workflow: SeedanceWorkflow;
  workflowMode?: string;
}) {
  return SEEDANCE_PROMPT_SCAFFOLDS.filter((item) =>
    matchesContext(item, input.generationMode, input.workflow, input.workflowMode),
  );
}

export function getSeedancePromptTemplates(input: {
  generationMode: SeedancePromptGenerationMode;
  workflow: SeedanceWorkflow;
  workflowMode?: string;
}) {
  return SEEDANCE_PROMPT_TEMPLATES.filter((item) =>
    matchesContext(item, input.generationMode, input.workflow, input.workflowMode),
  );
}

export function applySeedancePromptBlock(
  currentPrompt: string,
  blockPrompt: string,
  mode: SeedancePromptTemplateMode = "replace",
) {
  const trimmedCurrent = currentPrompt.trim();
  const trimmedBlock = blockPrompt.trim();

  if (mode === "replace" || !trimmedCurrent) {
    return trimmedBlock;
  }

  if (!trimmedBlock) {
    return trimmedCurrent;
  }

  return `${trimmedCurrent}\n\n${trimmedBlock}`;
}
