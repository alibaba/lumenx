// @vitest-environment happy-dom
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PromptTemplateModal from "@/components/modules/playground/PromptTemplateModal";
import { usePlaygroundStore, type PlaygroundTemplate } from "@/components/modules/playground/usePlaygroundStore";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";

const playgroundApiMock = vi.hoisted(() => ({
    createTemplate: vi.fn(),
    deleteTemplate: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
    const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    return {
        ...actual,
        playgroundApi: playgroundApiMock,
    };
});

vi.mock("next-intl", () => ({
    useTranslations: () => (key: string) => key,
}));

const template: PlaygroundTemplate = {
    id: "tpl-a",
    name: "Template A",
    category: "image",
    prompt: "template prompt",
    negative_prompt: "",
    default_mode: "t2v",
    default_model_id: "",
    default_parameters: {},
    created_at: "2026-10-13T00:00:00.000Z",
    updated_at: "2026-10-13T00:00:00.000Z",
};

function resetStore(overrides: Partial<ReturnType<typeof usePlaygroundStore.getState>> = {}) {
    usePlaygroundStore.setState({
        prompt: "current prompt",
        negativePrompt: "old negative",
        mode: "t2i",
        modelId: "old-model",
        parameters: { seed: 1 },
        templates: [],
        showTemplateModal: false,
        favoriteTemplateIds: [],
        ...overrides,
    });
}

describe("Playground template actions", () => {
    beforeEach(() => {
        vi.restoreAllMocks();
        vi.spyOn(console, "error").mockImplementation(() => undefined);
        playgroundApiMock.createTemplate.mockResolvedValue({ ...template, id: "created-template" });
        playgroundApiMock.deleteTemplate.mockResolvedValue(undefined);
        clearRuntimeEnvironment();
        resetStore();
    });

    it("applies explicit empty template fields as replacements", () => {
        resetStore({ templates: [template] });

        usePlaygroundStore.getState().applyTemplate(template);

        expect(usePlaygroundStore.getState().prompt).toBe("template prompt");
        expect(usePlaygroundStore.getState().negativePrompt).toBe("");
        expect(usePlaygroundStore.getState().mode).toBe("t2v");
        expect(usePlaygroundStore.getState().modelId).toBe("");
        expect(usePlaygroundStore.getState().parameters).toEqual({});
    });

    it("clears stale negative prompt and params when a template omits them", () => {
        usePlaygroundStore.getState().applyTemplate({
            id: "tpl-minimal",
            name: "Minimal",
            category: "general",
            prompt: "minimal prompt",
            created_at: "2026-10-13T00:00:00.000Z",
            updated_at: "2026-10-13T00:00:00.000Z",
        });

        expect(usePlaygroundStore.getState().prompt).toBe("minimal prompt");
        expect(usePlaygroundStore.getState().negativePrompt).toBe("");
        expect(usePlaygroundStore.getState().parameters).toEqual({});
        expect(usePlaygroundStore.getState().mode).toBe("t2i");
        expect(usePlaygroundStore.getState().modelId).toBe("old-model");
    });

    it("clears stale negative prompt and params when a template sets null", () => {
        usePlaygroundStore.getState().applyTemplate({
            id: "tpl-null",
            name: "Null",
            category: "general",
            prompt: "null prompt",
            negative_prompt: null,
            default_parameters: null,
            created_at: "2026-10-13T00:00:00.000Z",
            updated_at: "2026-10-13T00:00:00.000Z",
        });

        expect(usePlaygroundStore.getState().negativePrompt).toBe("");
        expect(usePlaygroundStore.getState().parameters).toEqual({});
    });

    it("keeps template create ACK active under React StrictMode effect replay", async () => {
        resetStore({ showTemplateModal: true, prompt: "draft prompt" });
        render(
            <React.StrictMode>
                <PromptTemplateModal />
            </React.StrictMode>,
        );

        fireEvent.change(screen.getByPlaceholderText("template.namePlaceholder"), { target: { value: "Strict Name" } });
        fireEvent.change(screen.getByPlaceholderText("template.promptPlaceholder"), { target: { value: "Strict body" } });
        fireEvent.click(screen.getByRole("button", { name: "template.save" }));

        await waitFor(() => expect(usePlaygroundStore.getState().templates.map((item) => item.id)).toEqual(["created-template"]));
    });

    it("keeps template delete ACK active under React StrictMode effect replay", async () => {
        resetStore({ showTemplateModal: true, prompt: "", templates: [template] });
        render(
            <React.StrictMode>
                <PromptTemplateModal />
            </React.StrictMode>,
        );

        fireEvent.click(screen.getByTitle("template.delete"));

        await waitFor(() => expect(usePlaygroundStore.getState().templates).toEqual([]));
    });

    it("keeps a new runtime template create pending when an old runtime ACK arrives late", async () => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000001" });
        resetStore({ showTemplateModal: true, prompt: "runtime A draft" });
        let resolveOldCreate!: (value: PlaygroundTemplate) => void;
        let resolveNewCreate!: (value: PlaygroundTemplate) => void;
        playgroundApiMock.createTemplate
            .mockImplementationOnce(() => new Promise<PlaygroundTemplate>((resolve) => {
                resolveOldCreate = resolve;
            }))
            .mockImplementationOnce(() => new Promise<PlaygroundTemplate>((resolve) => {
                resolveNewCreate = resolve;
            }));
        render(<PromptTemplateModal />);

        fireEvent.change(screen.getByPlaceholderText("template.namePlaceholder"), { target: { value: "Old" } });
        fireEvent.click(screen.getByRole("button", { name: "template.save" }));
        expect(screen.getByRole("button", { name: "template.saving" })).toBeTruthy();

        activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000002" });

        await waitFor(() => expect(screen.getByRole("button", { name: "template.save" })).toBeTruthy());
        fireEvent.change(screen.getByPlaceholderText("template.namePlaceholder"), { target: { value: "New" } });
        fireEvent.change(screen.getByPlaceholderText("template.promptPlaceholder"), { target: { value: "runtime B draft" } });
        fireEvent.click(screen.getByRole("button", { name: "template.save" }));
        expect(screen.getByRole("button", { name: "template.saving" })).toBeTruthy();

        resolveOldCreate({ ...template, id: "old-template", name: "Old" });
        await Promise.resolve();
        expect(screen.getByRole("button", { name: "template.saving" })).toBeTruthy();
        expect(usePlaygroundStore.getState().templates.map((item) => item.id)).toEqual([]);

        resolveNewCreate({ ...template, id: "new-template", name: "New" });
        await waitFor(() => expect(usePlaygroundStore.getState().templates.map((item) => item.id)).toEqual(["new-template"]));
    });

    it("reinitializes the template draft when runtime changes while the modal stays open", async () => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000001" });
        resetStore({ showTemplateModal: true, prompt: "runtime A draft" });
        render(<PromptTemplateModal />);

        fireEvent.change(screen.getByPlaceholderText("template.namePlaceholder"), { target: { value: "Old" } });
        fireEvent.change(screen.getByPlaceholderText("template.promptPlaceholder"), { target: { value: "edited A draft" } });

        usePlaygroundStore.setState({ prompt: "runtime B prompt" });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000002" });

        await waitFor(() => expect(screen.queryByDisplayValue("Old")).toBeNull());
        expect(screen.getByDisplayValue("runtime B prompt")).toBeTruthy();
    });

    it("shows create errors and preserves the template draft", async () => {
        playgroundApiMock.createTemplate.mockRejectedValueOnce(new Error("create failed"));
        resetStore({ showTemplateModal: true, prompt: "draft prompt" });
        render(<PromptTemplateModal />);

        fireEvent.change(screen.getByPlaceholderText("template.namePlaceholder"), { target: { value: "Draft Name" } });
        fireEvent.change(screen.getByPlaceholderText("template.promptPlaceholder"), { target: { value: "Draft body" } });
        fireEvent.click(screen.getByRole("button", { name: "template.save" }));

        await waitFor(() => expect(screen.getByText("template.createFailed")).toBeTruthy());
        expect(screen.getByDisplayValue("Draft Name")).toBeTruthy();
        expect(screen.getByDisplayValue("Draft body")).toBeTruthy();
        expect(usePlaygroundStore.getState().templates).toEqual([]);
    });

    it("shows delete errors and keeps the template in the list", async () => {
        playgroundApiMock.deleteTemplate.mockRejectedValueOnce(new Error("delete failed"));
        resetStore({ showTemplateModal: true, prompt: "", templates: [template] });
        render(<PromptTemplateModal />);

        fireEvent.click(screen.getByTitle("template.delete"));

        await waitFor(() => expect(screen.getByText("template.deleteFailed")).toBeTruthy());
        expect(screen.getByText("Template A")).toBeTruthy();
        expect(usePlaygroundStore.getState().templates.map((item) => item.id)).toEqual(["tpl-a"]);
    });
});
