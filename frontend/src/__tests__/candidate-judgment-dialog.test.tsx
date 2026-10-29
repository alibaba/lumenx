// @vitest-environment happy-dom
import React, { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import messages from "../../messages/en.json";
import { CandidateJudgmentDialog } from "@/components/atelier/v3/CandidateJudgmentDialog";

function mount(open = true, props: Partial<React.ComponentProps<typeof CandidateJudgmentDialog>> = {}) {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <CandidateJudgmentDialog
            candidateId="candidate-1"
            mediaKind="video"
            history={[]}
            open={open}
            onClose={onClose}
            onSave={onSave}
            {...props}
        />
    </NextIntlClientProvider>);
    return { onSave, onClose };
}

function ControlledDialog({ onSave = vi.fn().mockResolvedValue(undefined) }: { onSave?: (input: { verdict: "unreviewed" | "keep" | "reject"; reason: string }) => Promise<void> }) {
    const [open, setOpen] = useState(false);
    return <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <button type="button" onClick={() => setOpen(true)}>Open review</button>
        <CandidateJudgmentDialog
            candidateId="candidate-1"
            mediaKind="video"
            history={[]}
            open={open}
            onClose={() => setOpen(false)}
            onSave={onSave}
        />
    </NextIntlClientProvider>;
}

describe("Candidate judgment dialog", () => {
    it("saves a 2000-code-point emoji reason without truncating it", async () => {
        const { onSave } = mount();
        const reason = "🔥".repeat(2000);

        fireEvent.click(screen.getByLabelText("Keep"));
        fireEvent.change(screen.getByLabelText("Reason"), { target: { value: reason } });

        expect(screen.getByLabelText("Reason")).toHaveValue(reason);
        expect(screen.getByText("2000/2000")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Save review" }));

        await waitFor(() => expect(onSave).toHaveBeenCalledWith({ verdict: "keep", reason }));
    });

    it("keeps over-limit input visible and disables save by code-point length", () => {
        const { onSave } = mount();
        const reason = "🔥".repeat(2001);

        fireEvent.change(screen.getByLabelText("Reason"), { target: { value: reason } });

        expect(screen.getByLabelText("Reason")).toHaveValue(reason);
        expect(screen.getByText("2001/2000")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Save review" })).toBeDisabled();
        expect(onSave).not.toHaveBeenCalled();
    });

    it("contains focus and restores the opener when Escape closes the dialog", async () => {
        render(<ControlledDialog />);
        const opener = screen.getByRole("button", { name: "Open review" });

        opener.focus();
        fireEvent.click(opener);
        await waitFor(() => expect(screen.getByLabelText("Reason")).toHaveFocus());

        screen.getByRole("button", { name: "Save review" }).focus();
        fireEvent.keyDown(window, { key: "Tab" });
        expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();

        fireEvent.keyDown(window, { key: "Escape" });
        await waitFor(() => expect(screen.queryByRole("dialog", { name: "Candidate review" })).not.toBeInTheDocument());
        expect(opener).toHaveFocus();
    });
});
