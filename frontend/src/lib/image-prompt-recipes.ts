export type ImagePromptTarget = "full_body" | "three_view" | "headshot" | "scene" | "prop";
export type ImagePromptTemplateMode = "replace" | "append";
export type ImageStyleMode = "photoreal" | "stylized" | "neutral";

type SupportedStyleMode = ImageStyleMode | "all";

export interface ImagePromptBlock {
  id: string;
  target: ImagePromptTarget;
  title: string;
  summary: string;
  prompt: string;
  tags: string[];
  styleModes: SupportedStyleMode[];
}

interface BuildDefaultImagePromptInput {
  target: ImagePromptTarget;
  name: string;
  description?: string;
  strictReference?: boolean;
  stylePrompt?: string;
}

const PHOTOREAL_MARKERS = [
  "写实",
  "真人",
  "实拍",
  "电影",
  "真实",
  "摄影",
  "photoreal",
  "photographic",
  "live-action",
  "cinematic",
  "realistic",
  "natural skin",
];

const STYLIZED_MARKERS = [
  "动漫",
  "二次元",
  "插画",
  "卡通",
  "手绘",
  "漫画",
  "anime",
  "cartoon",
  "illustration",
  "stylized",
  "cel shading",
  "comic",
];

function hasMarker(text: string, markers: string[]) {
  const lower = text.toLowerCase();
  return markers.some((marker) => lower.includes(marker.toLowerCase()));
}

function cleanText(text?: string) {
  return (text || "").replace(/\s+/g, " ").trim();
}

function joinPromptLines(lines: Array<string | undefined>) {
  return lines.filter((line) => !!line && line.trim().length > 0).join("\n");
}

function getReferenceGuardrail(target: ImagePromptTarget, strictReference: boolean) {
  if (!strictReference) {
    return "";
  }

  if (target === "headshot") {
    return "Strictly preserve the same face, hairstyle, skin tone, facial proportions, and signature accessories as the reference image.";
  }

  if (target === "scene") {
    return "Strictly preserve the same spatial layout, lighting direction, architectural language, and hero props as the existing reference scene.";
  }

  if (target === "prop") {
    return "Strictly preserve the same silhouette, material response, wear pattern, and scale logic as the reference object.";
  }

  return "Strictly preserve the same face, hairstyle, body proportions, costume silhouette, and signature accessories as the reference image.";
}

function getStyleGuardrails(styleMode: ImageStyleMode, target: ImagePromptTarget) {
  if (styleMode === "photoreal") {
    if (target === "scene" || target === "prop") {
      return [
        "Photorealistic lighting, grounded materials, believable lens response, cinematic production design.",
        "Avoid anime rendering, cartoon outlines, cel shading, and exaggerated proportions.",
      ];
    }

    return [
      "Live-action human subject, photorealistic facial structure, realistic skin texture, real-world anatomy, natural fabric response.",
      "Avoid anime rendering, cartoon stylization, cel shading, illustrated faces, and doll-like proportions.",
    ];
  }

  if (styleMode === "stylized") {
    return [
      "Stylized illustration language, intentional shape design, clear silhouette rhythm, cohesive art direction.",
      "Avoid accidental live-action skin texture, uncanny photoreal pores, and mismatched photographic lighting.",
    ];
  }

  return [
    "Cohesive visual language, clear hierarchy, readable silhouette, consistent materials and lighting logic.",
  ];
}

function getTargetSpecificLines(target: ImagePromptTarget, name: string, description: string) {
  switch (target) {
    case "full_body":
      return [
        `Full-body hero reference of ${name}.`,
        description,
        "One human subject only, standing neutral pose, full costume visible from head to toe, facing camera.",
        "White or very clean studio background, no scenery, no props, no extra body parts.",
      ];
    case "three_view":
      return [
        `Three-view turnaround sheet for ${name}.`,
        description,
        "Front view, side view, and back view in a single sheet, matching height and costume details.",
        "Neutral pose, orthographic studio layout, even lighting, no dramatic perspective distortion.",
      ];
    case "headshot":
      return [
        `Headshot casting portrait of ${name}.`,
        description,
        "Head and shoulders framing, looking toward camera, neutral expression, identity-first details.",
        "Simple portrait background, keep hairline, eyes, nose, mouth, and skin texture readable.",
      ];
    case "scene":
      return [
        `Establishing environment concept for ${name}.`,
        description,
        "No main character in frame unless explicitly required, prioritize geography and spatial continuity.",
        "Foreground, midground, background separation; clear entry points and practical light sources.",
      ];
    case "prop":
      return [
        `Hero prop concept render of ${name}.`,
        description,
        "Single object only, centered or presentation-ready composition, no hands, no people, no extra props.",
        "Readable silhouette, material close-read, manufacturing or wear details clearly visible.",
      ];
    default:
      return [];
  }
}

export function detectImageStyleMode(...texts: Array<string | undefined>): ImageStyleMode {
  const merged = texts.map(cleanText).filter(Boolean).join(" ").toLowerCase();
  if (!merged) {
    return "neutral";
  }

  const hasPhotoreal = hasMarker(merged, PHOTOREAL_MARKERS);
  const hasStylized = hasMarker(merged, STYLIZED_MARKERS);

  if (hasPhotoreal && !hasStylized) {
    return "photoreal";
  }

  if (hasStylized && !hasPhotoreal) {
    return "stylized";
  }

  return "neutral";
}

export function buildDefaultImagePrompt(input: BuildDefaultImagePromptInput) {
  const description = cleanText(input.description);
  const styleMode = detectImageStyleMode(input.stylePrompt, description);
  const referenceGuardrail = getReferenceGuardrail(input.target, Boolean(input.strictReference));
  const styleGuardrails = getStyleGuardrails(styleMode, input.target);

  return joinPromptLines([
    ...getTargetSpecificLines(input.target, input.name || "Unnamed asset", description),
    referenceGuardrail,
    ...styleGuardrails,
    "Keep the subject readable, avoid extra subjects, text, watermark, and contradictory staging.",
  ]);
}

export const IMAGE_PROMPT_SCAFFOLDS: ImagePromptBlock[] = [
  {
    id: "full-body-real-human",
    target: "full_body",
    title: "全身主参考骨架",
    summary: "适合先把角色身份、服装和真人感锁稳，再去出三视图和头像。",
    styleModes: ["all"],
    tags: ["角色", "全身", "主参考"],
    prompt: joinPromptLines([
      "Full-body hero reference of [character name].",
      "[identity + costume + physique + age impression].",
      "One human subject only, standing neutral pose, facing camera, full costume visible from head to toe.",
      "Live-action human actor feel, realistic skin texture, real-world anatomy, natural fabric response.",
      "White studio background, no scenery, no props, no extra limbs, no cartoon rendering.",
    ]),
  },
  {
    id: "three-view-turnaround",
    target: "three_view",
    title: "三视图定板骨架",
    summary: "适合服装细节和结构统一，减少后续分镜跳脸跳衣服。",
    styleModes: ["all"],
    tags: ["角色", "三视图", "一致性"],
    prompt: joinPromptLines([
      "Three-view turnaround sheet for [character name].",
      "[identity + costume + silhouette].",
      "Front view, side view, and back view in one sheet, same body proportions and same costume details.",
      "Orthographic reference sheet, even lighting, clean background, no perspective distortion.",
      "Strictly preserve face, hairstyle, proportions, costume silhouette, and accessories from the reference image.",
    ]),
  },
  {
    id: "headshot-casting",
    target: "headshot",
    title: "头像特写骨架",
    summary: "适合把脸、发际线、眼神和皮肤质感锁定成演员定妆照。",
    styleModes: ["all"],
    tags: ["角色", "头像", "写实"],
    prompt: joinPromptLines([
      "Headshot casting portrait of [character name].",
      "[facial identity + hairstyle + mood baseline].",
      "Head and shoulders framing, looking toward camera, neutral expression, actor-style realism.",
      "Realistic skin texture, clear eyes, hairline, pores, subtle fabric details near the collar.",
      "Simple portrait background, no illustration look, no cartoon stylization, no extra subjects.",
    ]),
  },
  {
    id: "scene-geography",
    target: "scene",
    title: "场景空间骨架",
    summary: "适合持续出现的场景，强调空间布局和分镜连续性。",
    styleModes: ["all"],
    tags: ["场景", "空间", "连续性"],
    prompt: joinPromptLines([
      "Establishing environment concept for [scene name].",
      "[architecture + props + surface materials + time of day].",
      "Foreground, midground, background separation, clear circulation path, practical light sources visible.",
      "Preserve the same spatial layout, lighting direction, entry points, and hero props across adjacent storyboard frames.",
      "Environment first, no extra characters unless explicitly required.",
    ]),
  },
  {
    id: "prop-hero",
    target: "prop",
    title: "道具展示骨架",
    summary: "适合武器、关键物件、品牌道具或剧情物证。",
    styleModes: ["all"],
    tags: ["道具", "展示", "材质"],
    prompt: joinPromptLines([
      "Hero prop concept render of [prop name].",
      "[shape + material + wear + scale].",
      "Single object only, centered composition or presentation angle, no hands, no people, no extra props.",
      "Readable silhouette, crisp material definition, believable reflections, controlled background.",
      "Keep the same silhouette, material response, and wear pattern if a reference image already exists.",
    ]),
  },
];

export const IMAGE_PROMPT_TEMPLATES: ImagePromptBlock[] = [
  {
    id: "full-body-fashion-sheet",
    target: "full_body",
    title: "服装展示版",
    summary: "强调整体穿搭、版型和材质，适合先锁角色主装。",
    styleModes: ["all"],
    tags: ["服装", "材质"],
    prompt:
      "Show the full outfit cleanly, preserve the exact silhouette, seam lines, accessories, and footwear details. Keep the body relaxed and readable, with no dramatic motion.",
  },
  {
    id: "full-body-photoreal-casting",
    target: "full_body",
    title: "真人选角版",
    summary: "增强真人、演员、自然皮肤与真实比例，压制卡通味。",
    styleModes: ["photoreal", "neutral"],
    tags: ["真人", "演员", "压卡通"],
    prompt:
      "Treat the subject as a live-action actor reference. Prioritize realistic skin texture, natural anatomy, grounded lighting, subtle facial asymmetry, and real fabric weight. Avoid anime, cartoon, illustration, or plastic skin.",
  },
  {
    id: "three-view-costume-lock",
    target: "three_view",
    title: "服装锁定版",
    summary: "强调同一套服装在三视图中的完整一致性。",
    styleModes: ["all"],
    tags: ["三视图", "服装", "一致"],
    prompt:
      "Keep every garment panel, trim color, accessory placement, hairstyle mass, and body proportion perfectly consistent across all three views. No dramatic pose change or lens distortion.",
  },
  {
    id: "headshot-skin-detail",
    target: "headshot",
    title: "皮肤质感版",
    summary: "给真人头像补足皮肤、头发和微表情细节。",
    styleModes: ["photoreal", "neutral"],
    tags: ["皮肤", "质感", "真人"],
    prompt:
      "Emphasize believable skin texture, eyebrow and eyelash detail, hair strands, lip texture, and realistic catchlight. Keep expression restrained and identity stable.",
  },
  {
    id: "headshot-stylized-key-visual",
    target: "headshot",
    title: "风格主视觉版",
    summary: "适合本身就希望做成插画、漫画或风格化头像。",
    styleModes: ["stylized", "neutral"],
    tags: ["风格化", "头像"],
    prompt:
      "Push the portrait toward a clean key visual: strong silhouette, intentional color design, graphic facial read, and controlled stylization while preserving the character identity.",
  },
  {
    id: "scene-continuity-lock",
    target: "scene",
    title: "连续场景版",
    summary: "专门给会反复出现的同一空间，用于减少前后镜头跳场。",
    styleModes: ["all"],
    tags: ["连续性", "同场景"],
    prompt:
      "Treat this as a reusable master location. Preserve the same wall rhythm, furniture placement, prop density, weather logic, and light direction so consecutive storyboard frames feel like the same time and space.",
  },
  {
    id: "scene-mood-board",
    target: "scene",
    title: "氛围建立版",
    summary: "强调时间、光色和空气感，适合场景定调。",
    styleModes: ["all"],
    tags: ["氛围", "光线"],
    prompt:
      "Focus on atmosphere: time of day, color temperature, haze, practical lights, weather traces, and surface response. Keep geography readable rather than abstract.",
  },
  {
    id: "prop-material-close-read",
    target: "prop",
    title: "材质近读版",
    summary: "强调金属、玻璃、皮革、磨损等材质反馈。",
    styleModes: ["all"],
    tags: ["材质", "磨损"],
    prompt:
      "Show surface material response clearly: scratches, edge wear, fingerprints, stitched seams, and subtle reflections. Keep the silhouette simple and unmistakable.",
  },
  {
    id: "prop-product-hero",
    target: "prop",
    title: "英雄展示版",
    summary: "适合武器、证物、关键道具的单体展示镜头。",
    styleModes: ["all"],
    tags: ["展示", "单体"],
    prompt:
      "Present the object as a hero asset with centered framing, controlled studio lighting, crisp contour separation, and no clutter. The object must feel production-ready and immediately readable.",
  },
];

function matchesTarget(item: ImagePromptBlock, target: ImagePromptTarget, styleMode: ImageStyleMode) {
  return item.target === target && (item.styleModes.includes("all") || item.styleModes.includes(styleMode));
}

export function getImagePromptScaffolds(input: {
  target: ImagePromptTarget;
  stylePrompt?: string;
  description?: string;
}) {
  const styleMode = detectImageStyleMode(input.stylePrompt, input.description);
  return IMAGE_PROMPT_SCAFFOLDS.filter((item) => matchesTarget(item, input.target, styleMode));
}

export function getImagePromptTemplates(input: {
  target: ImagePromptTarget;
  stylePrompt?: string;
  description?: string;
}) {
  const styleMode = detectImageStyleMode(input.stylePrompt, input.description);
  return IMAGE_PROMPT_TEMPLATES.filter((item) => matchesTarget(item, input.target, styleMode));
}

export function applyImagePromptBlock(
  currentPrompt: string,
  blockPrompt: string,
  mode: ImagePromptTemplateMode = "replace",
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
