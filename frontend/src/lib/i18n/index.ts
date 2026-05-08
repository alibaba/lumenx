import { zhCN } from "./zh-CN";

export type Locale = "zh-CN";

export const defaultLocale: Locale = "zh-CN";
export const messagesByLocale = {
  "zh-CN": zhCN,
} as const;

export const messages = messagesByLocale[defaultLocale];
export { zhCN };

export function getMessages(locale: Locale = defaultLocale) {
  return messagesByLocale[locale] ?? messagesByLocale[defaultLocale];
}

type DictionaryValue = unknown;

export function t<T = string>(path: string, fallback?: T, locale: Locale = defaultLocale): T | string {
  const value = path.split(".").reduce<DictionaryValue>((acc, key) => {
    if (acc && typeof acc === "object" && key in (acc as Record<string, unknown>)) {
      return (acc as Record<string, unknown>)[key];
    }
    return undefined;
  }, getMessages(locale));

  return (value ?? fallback ?? path) as T | string;
}

export const terms = messages.terms;
export const assetTypeTerms = terms.assetTypes;
export const shotTerms = terms.shotAngles;
export const cameraTerms = terms.cameraMovements;
export const subjectMotionTerms = terms.subjectMotions;
export const shotTypeTerms = terms.shotTypes;
export const styleTerms = terms.styles;
export const seedanceTerms = terms.seedance;
export const referenceVideoTypeTerms = terms.referenceVideoTypes;

type PromptBuilderCameraGroup = (typeof messages.modules.promptBuilder.cameraGroups)[number];
type PromptBuilderCameraOption = PromptBuilderCameraGroup["options"][number];

export const promptBuilderCameraGroups = messages.modules.promptBuilder.cameraGroups as readonly PromptBuilderCameraGroup[];

export const shotTermList = Object.values(shotTerms);
export const cameraTermList = Object.values(cameraTerms);
export const subjectMotionTermList = Object.values(subjectMotionTerms);
export const shotTypeTermList = Object.values(shotTypeTerms);
export const styleTermList = Object.values(styleTerms);
export const promptBuilderCameraOptionList = promptBuilderCameraGroups.flatMap((group) => [...group.options]) as PromptBuilderCameraOption[];

export function getAssetTypeTerm(type?: string | null) {
  if (!type) return null;
  return assetTypeTerms[type as keyof typeof assetTypeTerms] ?? null;
}

export function getShotTerm(value?: string | null) {
  if (!value) return null;
  return shotTermList.find((term) => term.value === value) ?? null;
}

export function getCameraTerm(value?: string | null) {
  if (!value) return null;
  return cameraTermList.find((term) => term.value === value || term.prompt === value) ?? null;
}

export function getSubjectMotionTerm(value?: string | null) {
  if (!value) return null;
  return subjectMotionTermList.find((term) => term.value === value || term.prompt === value) ?? null;
}

export function getShotTypeTerm(value?: string | null) {
  if (!value) return null;
  return shotTypeTermList.find((term) => term.value === value) ?? null;
}

export function getStyleTerm(value?: string | null) {
  if (!value) return null;
  const direct = styleTerms[value as keyof typeof styleTerms];
  if (direct) return direct;
  return styleTermList.find((term) => term.value === value || term.label === value) ?? null;
}

export function getStylePresetCopy(id?: string | null) {
  if (!id) return null;
  return messages.projectSettings.stylePresets[id as keyof typeof messages.projectSettings.stylePresets] ?? null;
}

export function getPromptBuilderCameraOption(value?: string | null) {
  if (!value) return null;
  return promptBuilderCameraOptionList.find((option) => option.value === value) ?? null;
}

export function getReferenceVideoTypeLabel(type?: string | null) {
  if (!type) return "";
  return referenceVideoTypeTerms[type as keyof typeof referenceVideoTypeTerms] ?? type;
}
