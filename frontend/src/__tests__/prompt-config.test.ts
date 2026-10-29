/**
 * Tests for Phase 2: Custom prompt configuration logic.
 * - PromptConfig defaults and fallback
 * - API payload construction for prompt config
 * Polish API payloads are covered by the actual PolishPanel component tests.
 */
import { describe, it, expect } from 'vitest';

// ── PromptConfig fallback logic (mirrors backend get_effective_prompt) ──

function getEffectivePrompt(customValue: string, systemDefault: string): string {
    return customValue.trim() ? customValue : systemDefault;
}

describe('getEffectivePrompt (fallback logic)', () => {
    const SYSTEM_DEFAULT = "You are an expert video prompt engineer...";

    it('should return system default when custom is empty', () => {
        expect(getEffectivePrompt("", SYSTEM_DEFAULT)).toBe(SYSTEM_DEFAULT);
    });

    it('should return system default when custom is whitespace', () => {
        expect(getEffectivePrompt("   ", SYSTEM_DEFAULT)).toBe(SYSTEM_DEFAULT);
    });

    it('should return custom value when provided', () => {
        const custom = "My custom prompt for video generation";
        expect(getEffectivePrompt(custom, SYSTEM_DEFAULT)).toBe(custom);
    });

    it('should preserve custom value with leading/trailing whitespace', () => {
        const custom = "  My custom prompt  ";
        expect(getEffectivePrompt(custom, SYSTEM_DEFAULT)).toBe(custom);
    });
});

// ── Placeholder substitution (mirrors llm.py template.replace()) ──

function substituteStoryboardPlaceholders(template: string, assets: string, draft: string): string {
    return template.replace("{ASSETS}", assets).replace("{DRAFT}", draft);
}

function substituteR2VPlaceholders(template: string, slots: string): string {
    return template.replace("{SLOTS}", slots);
}

describe('substituteStoryboardPlaceholders', () => {
    it('should replace both {ASSETS} and {DRAFT}', () => {
        const template = "Assets: {ASSETS}\nDraft: {DRAFT}";
        const result = substituteStoryboardPlaceholders(template, "Image 1: Hero", "A hero stands");
        expect(result).toBe("Assets: Image 1: Hero\nDraft: A hero stands");
    });

    it('should handle template with no placeholders', () => {
        const template = "No placeholders here";
        const result = substituteStoryboardPlaceholders(template, "assets", "draft");
        expect(result).toBe("No placeholders here");
    });

    it('should handle empty assets and draft', () => {
        const template = "Assets: {ASSETS}\nDraft: {DRAFT}";
        const result = substituteStoryboardPlaceholders(template, "", "");
        expect(result).toBe("Assets: \nDraft: ");
    });
});

describe('substituteR2VPlaceholders', () => {
    it('should replace {SLOTS} with slot context', () => {
        const template = "Reference videos:\n{SLOTS}\n\nRewrite the prompt.";
        const slots = "- character1: White rabbit\n- character2: Robot dog";
        const result = substituteR2VPlaceholders(template, slots);
        expect(result).toContain("- character1: White rabbit");
        expect(result).toContain("- character2: Robot dog");
    });

    it('should handle empty slots', () => {
        const template = "Slots: {SLOTS}";
        const result = substituteR2VPlaceholders(template, "No reference videos provided.");
        expect(result).toBe("Slots: No reference videos provided.");
    });
});

// ── PromptConfig update payload ──

function buildPromptConfigPayload(config: {
    storyboard_polish?: string;
    video_polish?: string;
    r2v_polish?: string;
}) {
    return {
        storyboard_polish: config.storyboard_polish ?? "",
        video_polish: config.video_polish ?? "",
        r2v_polish: config.r2v_polish ?? "",
    };
}

describe('buildPromptConfigPayload', () => {
    it('should default all fields to empty string', () => {
        const payload = buildPromptConfigPayload({});
        expect(payload).toEqual({
            storyboard_polish: "",
            video_polish: "",
            r2v_polish: "",
        });
    });

    it('should preserve provided values', () => {
        const payload = buildPromptConfigPayload({
            video_polish: "Custom video prompt",
        });
        expect(payload.video_polish).toBe("Custom video prompt");
        expect(payload.storyboard_polish).toBe("");
        expect(payload.r2v_polish).toBe("");
    });

    it('should handle all fields provided', () => {
        const payload = buildPromptConfigPayload({
            storyboard_polish: "A",
            video_polish: "B",
            r2v_polish: "C",
        });
        expect(payload).toEqual({
            storyboard_polish: "A",
            video_polish: "B",
            r2v_polish: "C",
        });
    });
});

// ── _get_custom_prompt helper logic (mirrors api.py helper) ──

function getCustomPrompt(
    scriptId: string,
    field: string,
    projects: Record<string, { prompt_config?: Record<string, string> }>,
): string {
    if (!scriptId) return "";
    const script = projects[scriptId];
    if (script && script.prompt_config) {
        return script.prompt_config[field] || "";
    }
    return "";
}

describe('getCustomPrompt (backend helper mirror)', () => {
    const projects: Record<string, { prompt_config?: Record<string, string> }> = {
        "proj-1": {
            prompt_config: {
                storyboard_polish: "Custom storyboard",
                video_polish: "",
                r2v_polish: "Custom R2V",
            }
        },
        "proj-2": {}
    };

    it('should return empty string when scriptId is empty', () => {
        expect(getCustomPrompt("", "video_polish", projects)).toBe("");
    });

    it('should return custom value when set', () => {
        expect(getCustomPrompt("proj-1", "storyboard_polish", projects)).toBe("Custom storyboard");
    });

    it('should return empty string when field is empty', () => {
        expect(getCustomPrompt("proj-1", "video_polish", projects)).toBe("");
    });

    it('should return empty string when project has no prompt_config', () => {
        expect(getCustomPrompt("proj-2", "video_polish", projects)).toBe("");
    });

    it('should return empty string when project does not exist', () => {
        expect(getCustomPrompt("nonexistent", "video_polish", projects)).toBe("");
    });
});
