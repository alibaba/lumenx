import type { ImagePromptTarget } from "@/lib/image-prompt-recipes";
import type { StoryboardReferencePreviewItem } from "@/lib/storyboard-references";

export type PromptQualitySeverity = "error" | "warning" | "info";

export interface PromptQualityIssue {
  code: string;
  severity: PromptQualitySeverity;
  title: string;
  detail: string;
}

interface InspectVideoPromptInput {
  prompt: string;
  workflow?: string;
  workflowMode?: string;
  generationMode?: string;
}

interface InspectImagePromptInput {
  prompt: string;
  target: ImagePromptTarget;
  stylePrompt?: string;
}

interface InspectStoryboardPromptInput {
  prompt: string;
  sameSceneContinuity?: boolean;
  stylePrompt?: string;
  referencePreview?: StoryboardReferencePreviewItem[];
}

const REALISTIC_MARKERS = [
  "写实",
  "真人",
  "真实",
  "实拍",
  "摄影",
  "photoreal",
  "photographic",
  "live-action",
  "realistic",
  "human actor",
];

const STYLIZED_MARKERS = [
  "动漫",
  "卡通",
  "插画",
  "漫画",
  "anime",
  "cartoon",
  "illustration",
  "stylized",
  "cel shading",
];

const SUBJECT_MARKERS = [
  "character",
  "subject",
  "person",
  "man",
  "woman",
  "actor",
  "girl",
  "boy",
  "hero",
  "人物",
  "角色",
  "主体",
  "主角",
  "演员",
  "scene",
  "environment",
  "场景",
  "环境",
  "prop",
  "object",
  "product",
  "道具",
  "物件",
  "产品",
];

const CONSTRAINT_MARKERS = [
  "keep same",
  "preserve",
  "avoid",
  "no extra",
  "no ",
  "without",
  "only",
  "consistent",
  "same identity",
  "same scene",
  "一致",
  "保持",
  "不要",
  "禁止",
  "仅",
  "不出现",
  "固定",
  "连续",
];

const CONTINUITY_MARKERS = [
  "keep same",
  "preserve",
  "same identity",
  "same scene",
  "continuity",
  "same lighting",
  "same costume",
  "same environment",
  "保持一致",
  "同一场景",
  "连续",
  "统一",
];

const ACTION_MARKERS = [
  "run",
  "walk",
  "turn",
  "look",
  "raise",
  "grab",
  "jump",
  "sit",
  "stand",
  "speak",
  "talk",
  "cry",
  "smile",
  "fight",
  "open",
  "close",
  "dash",
  "swing",
  "step",
  "push",
  "pull",
  "跑",
  "走",
  "转身",
  "看向",
  "抬手",
  "抓",
  "跳",
  "坐下",
  "站起",
  "说话",
  "哭",
  "笑",
  "打斗",
  "打开",
  "关闭",
  "靠近",
  "后退",
];

const SHOT_GROUPS = [
  ["close-up", "close up", "cu", "特写", "近景"],
  ["medium shot", "medium", "中景"],
  ["wide shot", "wide", "establishing shot", "全景", "远景"],
];

const MOVEMENT_MARKERS = [
  "pan",
  "tilt",
  "zoom",
  "push-in",
  "push in",
  "dolly",
  "orbit",
  "arc",
  "truck",
  "handheld",
  "tracking",
  "推镜",
  "拉镜",
  "摇镜",
  "移镜",
  "环绕",
  "跟拍",
  "手持",
];

function normalizeText(text: string) {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

function containsAny(text: string, markers: string[]) {
  return markers.some((marker) => text.includes(marker.toLowerCase()));
}

function countUniqueMatches(text: string, markers: string[]) {
  const matched = new Set<string>();
  markers.forEach((marker) => {
    if (text.includes(marker.toLowerCase())) {
      matched.add(marker.toLowerCase());
    }
  });
  return matched.size;
}

function inspectCameraConflicts(prompt: string) {
  const issues: PromptQualityIssue[] = [];
  const normalized = normalizeText(prompt);
  const shotGroupHits = SHOT_GROUPS.filter((group) => containsAny(normalized, group));
  const hasStatic = normalized.includes("static camera") || normalized.includes("固定机位");
  const movementCount = countUniqueMatches(normalized, MOVEMENT_MARKERS);

  if (shotGroupHits.length >= 2) {
    issues.push({
      code: "conflicting_camera",
      severity: "warning",
      title: "镜头景别冲突",
      detail: "同一条提示词里同时出现了多个景别，容易让模型不知道该以特写还是全景为主。",
    });
  }

  if (hasStatic && movementCount > 0) {
    issues.push({
      code: "static_vs_motion",
      severity: "error",
      title: "固定机位与运镜冲突",
      detail: "你同时要求了固定机位和明显运镜，这会直接让视频指令互相打架。",
    });
  }

  return issues;
}

function inspectActionDensity(prompt: string) {
  const normalized = normalizeText(prompt);
  const actionCount = countUniqueMatches(normalized, ACTION_MARKERS);

  if (actionCount >= 7) {
    return [
      {
        code: "too_many_actions",
        severity: "error" as const,
        title: "动作过多",
        detail: "一个镜头里塞了过多动作节点，建议压缩成 1 条主动作线 + 1 个收尾动作。",
      },
    ];
  }

  if (actionCount >= 5) {
    return [
      {
        code: "action_density_high",
        severity: "warning" as const,
        title: "动作密度偏高",
        detail: "当前动作节拍偏多，模型可能会漏动作或把主体做乱。",
      },
    ];
  }

  return [];
}

function inspectSubjectClarity(prompt: string, extraMarkers: string[] = []) {
  const normalized = normalizeText(prompt);
  const hasSubject = containsAny(normalized, [...SUBJECT_MARKERS, ...extraMarkers]);

  if (!hasSubject) {
    return [
      {
        code: "unclear_subject",
        severity: "error" as const,
        title: "主体不清",
        detail: "提示词里没有明确谁或什么是主主体，建议第一句先写清主角、场景或道具。",
      },
    ];
  }

  return [];
}

function inspectConstraints(prompt: string, severity: PromptQualitySeverity = "info") {
  const normalized = normalizeText(prompt);
  if (containsAny(normalized, CONSTRAINT_MARKERS)) {
    return [];
  }

  return [
    {
      code: "missing_constraints",
      severity,
      title: "约束偏少",
      detail: "建议补上“保持一致 / 不要新增主体 / 不要跳场 / 禁止卡通化”这类约束，稳定性会明显更好。",
    },
  ];
}

function inspectStyleConflict(prompt: string, stylePrompt?: string) {
  const normalized = normalizeText(`${prompt} ${stylePrompt || ""}`);
  const hasRealistic = containsAny(normalized, REALISTIC_MARKERS);
  const hasStylized = containsAny(normalized, STYLIZED_MARKERS);

  if (hasRealistic && hasStylized) {
    return [
      {
        code: "style_conflict",
        severity: "warning" as const,
        title: "风格信号冲突",
        detail: "同一条提示词里同时出现了真人写实和动漫插画信号，模型很容易输出半写实半卡通。",
      },
    ];
  }

  return [];
}

export function inspectVideoPrompt(input: InspectVideoPromptInput) {
  const issues: PromptQualityIssue[] = [];
  const normalized = normalizeText(input.prompt);

  issues.push(...inspectCameraConflicts(input.prompt));
  issues.push(...inspectActionDensity(input.prompt));
  issues.push(...inspectSubjectClarity(input.prompt));
  issues.push(...inspectStyleConflict(input.prompt));
  issues.push(...inspectConstraints(input.prompt, "warning"));

  if ((input.workflow === "extend" || input.workflow === "edit") && !containsAny(normalized, CONTINUITY_MARKERS)) {
    issues.push({
      code: "workflow_continuity_missing",
      severity: "warning",
      title: "工作流连续性约束不足",
      detail: "当前是 extend/edit，但提示词没强调保持原镜头、原运动路径和同一主体一致性。",
    });
  }

  if (input.generationMode === "r2v" && !normalized.includes("character1")) {
    issues.push({
      code: "r2v_casting_missing",
      severity: "info",
      title: "R2V 角色指向偏弱",
      detail: "R2V 更稳的写法通常会直接点名 character1 / character2 的动作和对位。",
    });
  }

  if (input.workflowMode === "subject_replace" && !normalized.includes("replace")) {
    issues.push({
      code: "workflow_mode_signal_weak",
      severity: "info",
      title: "工作流模式语义偏弱",
      detail: "当前是 subject_replace，但提示词里还没明确“替换主体”这个动作意图。",
    });
  }

  return issues;
}

export function inspectImagePrompt(input: InspectImagePromptInput) {
  const issues: PromptQualityIssue[] = [];
  const normalized = normalizeText(input.prompt);
  const targetMarkers =
    input.target === "scene"
      ? ["scene", "environment", "场景", "环境"]
      : input.target === "prop"
        ? ["prop", "object", "product", "道具", "物件"]
        : ["character", "person", "actor", "角色", "人物", "演员"];

  issues.push(...inspectSubjectClarity(input.prompt, targetMarkers));
  issues.push(...inspectStyleConflict(input.prompt, input.stylePrompt));
  issues.push(...inspectConstraints(input.prompt));

  const styleNormalized = normalizeText(input.stylePrompt || "");
  const wantsRealistic = containsAny(styleNormalized, REALISTIC_MARKERS);

  if (
    wantsRealistic &&
    (input.target === "full_body" || input.target === "three_view" || input.target === "headshot") &&
    !containsAny(normalized, ["live-action", "photoreal", "realistic", "human actor", "真人", "写实"])
  ) {
    issues.push({
      code: "realism_signal_weak",
      severity: "warning",
      title: "真人约束偏弱",
      detail: "当前项目风格更偏真人，但这条提示词没有明确写实/真人/真实皮肤质感，容易漂成卡通脸。",
    });
  }

  if (input.target === "scene" && !containsAny(normalized, CONTINUITY_MARKERS)) {
    issues.push({
      code: "scene_continuity_missing",
      severity: "info",
      title: "场景连续性可再加强",
      detail: "如果这个场景会反复出现，建议补“保持同一空间布局/光线方向/道具位置”。",
    });
  }

  return issues;
}

export function inspectStoryboardPrompt(input: InspectStoryboardPromptInput) {
  const issues: PromptQualityIssue[] = [];
  const normalized = normalizeText(input.prompt);

  issues.push(...inspectCameraConflicts(input.prompt));
  issues.push(...inspectActionDensity(input.prompt));
  issues.push(...inspectSubjectClarity(input.prompt, ["scene", "shot", "frame", "镜头", "画面"]));
  issues.push(...inspectStyleConflict(input.prompt, input.stylePrompt));
  issues.push(...inspectConstraints(input.prompt));

  const styleNormalized = normalizeText(input.stylePrompt || "");
  if (
    containsAny(styleNormalized, REALISTIC_MARKERS)
    && !containsAny(normalized, ["live-action", "photoreal", "realistic", "human actor", "真人", "写实", "真实中国面孔"])
  ) {
    issues.push({
      code: "storyboard_realism_signal_weak",
      severity: "warning",
      title: "真人写实约束偏弱",
      detail: "当前项目风格偏真人写实，但这帧提示词没有明确写实/真人/真实中国面孔，容易漂成卡通或插画感。",
    });
  }

  if (input.sameSceneContinuity && !containsAny(normalized, CONTINUITY_MARKERS)) {
    issues.push({
      code: "storyboard_continuity_missing",
      severity: "warning",
      title: "同场景连续性不足",
      detail: "当前镜头大概率承接同一场景，建议明确保持人物造型、空间布局和光线逻辑一致。",
    });
  }

  const missingRequiredReferences = (input.referencePreview || []).filter(
    (item) => item.required && item.status === "missing",
  );
  if (missingRequiredReferences.length > 0) {
    issues.push({
      code: "missing_required_reference",
      severity: "warning",
      title: "关键引用缺失",
      detail: `当前有 ${missingRequiredReferences.length} 个关键引用还没有就绪，建议先补齐主参考再重绘。`,
    });
  }

  return issues;
}

export function hasBlockingPromptIssues(issues: PromptQualityIssue[]) {
  return issues.some((issue) => issue.severity === "error");
}

export function formatPromptIssues(issues: PromptQualityIssue[]) {
  return issues.map((issue) => `- [${issue.severity}] ${issue.title}：${issue.detail}`).join("\n");
}
