/**
 * Remaining legacy frame-refinement payload mirrors, not HTTP verification.
 * Video polish now runs the actual mounted component and API client in
 * components/modules/__tests__/PolishPanel.spec.tsx.
 */
import { describe, it, expect } from 'vitest';

/** Mirrors api.refineFramePrompt payload */
function buildRefineFramePayload(
    frameId: string,
    rawPrompt: string,
    assets: any[] = [],
    feedback: string = "",
) {
    return {
        frame_id: frameId,
        raw_prompt: rawPrompt,
        assets: assets,
        feedback: feedback,
    };
}

describe('buildRefineFramePayload', () => {
    it('should include frame_id, raw_prompt and empty defaults', () => {
        const payload = buildRefineFramePayload("frame-1", "raw prompt");
        expect(payload).toEqual({
            frame_id: "frame-1",
            raw_prompt: "raw prompt",
            assets: [],
            feedback: "",
        });
    });

    it('should include assets and feedback when provided', () => {
        const assets = [{ type: "Character", name: "Hero", description: "brave warrior" }];
        const payload = buildRefineFramePayload("frame-1", "raw prompt", assets, "more detail on hero");
        expect(payload.assets).toHaveLength(1);
        expect(payload.feedback).toBe("more detail on hero");
    });
});
