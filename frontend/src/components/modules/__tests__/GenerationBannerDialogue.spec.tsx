import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GenerationBanner } from "../storyboard-r2v/GenerationBanner";
import { renderWithIntl } from "../../series/__tests__/test-utils";

describe("GenerationBanner dialogue CTA", () => {
    it("shows batch synthesis when dialogue exists even if the frontend cannot prove missing audio", () => {
        const onGenerateDialogue = vi.fn();
        renderWithIntl(<GenerationBanner
            state="summary"
            phase1Captions={[]}
            summary={{ frameCount: 2, dialogueReady: 2, dialogueMissing: 0 }}
            onGenerateDialogue={onGenerateDialogue}
        />);
        fireEvent.click(screen.getByRole("button", { name: /合成对白语音/ }));
        expect(onGenerateDialogue).toHaveBeenCalledOnce();
    });

    it("keeps the CTA visible but disabled while dialogue generation is busy", () => {
        renderWithIntl(<GenerationBanner
            state="summary"
            phase1Captions={[]}
            summary={{ frameCount: 2, dialogueReady: 2, dialogueMissing: 1 }}
            dialogueBusy
            onGenerateDialogue={vi.fn()}
        />);
        expect(screen.getByRole("button", { name: /合成中/ })).toBeDisabled();
    });
});
