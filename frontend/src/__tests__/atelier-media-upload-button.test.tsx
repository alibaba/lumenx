// @vitest-environment happy-dom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MediaUploadButton } from "@/components/atelier/v3/MediaUploadButton";

afterEach(cleanup);
const file = () => new File(["media"], "hero.png", { type: "image/png" });
const props = () => ({ ownerKey: "A", label: "Upload", busyLabel: "Uploading", accept: ".png", onUpload: vi.fn().mockResolvedValue(undefined) });
const choose = (selected: File) => {
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    fireEvent.change(screen.getByLabelText("Upload", { selector: "input" }), { target: { files: [selected] } });
};

describe("Atelier media upload picker", () => {
    it("passes the same file once and blocks a second selection while awaiting the result", async () => {
        const config = props(); let finish!: () => void;
        config.onUpload.mockReturnValue(new Promise<void>(resolve => { finish = resolve; }));
        render(<MediaUploadButton {...config} />);
        const selected = file(); choose(selected);
        expect(config.onUpload).toHaveBeenCalledExactlyOnceWith(selected);
        expect(screen.getByRole("button", { name: "Uploading" })).toBeDisabled();
        fireEvent.change(screen.getByLabelText("Upload", { selector: "input" }), { target: { files: [selected] } });
        expect(config.onUpload).toHaveBeenCalledTimes(1);
        finish(); await waitFor(() => expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled());
    });

    it("discards a file chosen after its project or target changed", () => {
        const config = props(); const { rerender } = render(<MediaUploadButton {...config} />);
        fireEvent.click(screen.getByRole("button", { name: "Upload" }));
        rerender(<MediaUploadButton {...config} ownerKey="B" />);
        fireEvent.change(screen.getByLabelText("Upload", { selector: "input" }), { target: { files: [file()] } });
        expect(config.onUpload).not.toHaveBeenCalled();
        rerender(<MediaUploadButton {...config} ownerKey="A:new-session" />);
        fireEvent.change(screen.getByLabelText("Upload", { selector: "input" }), { target: { files: [file()] } });
        expect(config.onUpload).not.toHaveBeenCalled();
    });

    it("surfaces an upload error and allows a new explicit choice only when enabled", async () => {
        const config = props(); config.onUpload.mockRejectedValue(new Error("Result unknown"));
        const { rerender } = render(<MediaUploadButton {...config} />);
        choose(file());
        expect(await screen.findByRole("alert")).toHaveTextContent("Result unknown");
        rerender(<MediaUploadButton {...config} disabled />);
        expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();
        fireEvent.change(screen.getByLabelText("Upload", { selector: "input" }), { target: { files: [file()] } });
        expect(config.onUpload).toHaveBeenCalledTimes(1);
        rerender(<MediaUploadButton {...config} ownerKey="B" />);
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
});
