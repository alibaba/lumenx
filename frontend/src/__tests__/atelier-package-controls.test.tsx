// @vitest-environment happy-dom
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import {
    clearProjectPackageImportJournalForTests,
    ProjectPackageControls,
    resetProjectPackageImportJournalMemoryForTests,
} from "@/components/atelier/v3/ProjectPackageControls";
import { parseAtelierProject } from "@/lib/atelierApi";
import type { AtelierProject } from "@/lib/atelierTypes";
import en from "../../messages/en.json";
import zh from "../../messages/zh.json";
import packageFixture from "./fixtures/atelier-project-package-wire.json";
import baseFixture from "./fixtures/atelier-project.json";

const api = vi.hoisted(() => ({
    importProjectPackage: vi.fn(),
    exportProjectPackage: vi.fn(),
}));

vi.mock("@/lib/atelierProjectPackageApi", () => {
    class AtelierPackageImportError extends Error {
        constructor(
            message: string,
            public readonly outcomeUnknown: boolean,
            public readonly code = "atelier_package_import_failed",
            public readonly status?: number,
        ) {
            super(message);
            this.name = "AtelierPackageImportError";
        }
    }
    return { atelierProjectPackageApi: api, AtelierPackageImportError };
});

const sourceProject = (): AtelierProject => parseAtelierProject({
    ...structuredClone(baseFixture),
    title: "Source Project",
    revision: 2,
    agent_turns: [],
    sequence: [],
    audio_tracks: [],
    exports: [],
    node_delete_receipts: [],
    node_upload_receipts: [],
    sequence_export_jobs: [],
    generation_attempts: [],
    generation_request_receipts: [],
});
const importedProject = (): AtelierProject => parseAtelierProject(packageFixture.prepared_project);
const file = () => new File([new Uint8Array([80, 75, 3, 4])], "copy.lumenx.zip", { type: "application/zip" });

const PROJECT_PACKAGE_KEYS = [
    "title",
    "description",
    "copyExplanation",
    "export",
    "exporting",
    "exported",
    "exportFailed",
    "import",
    "importing",
    "importingShort",
    "chooseFile",
    "pending",
    "imported",
    "importFailed",
    "unknown",
    "knownPending",
    "journalBlocked",
    "wrongEnvironment",
    "checkList",
    "checking",
    "checkedList",
    "checkFailed",
    "acknowledge",
    "clearRecovery",
    "acknowledged",
].sort();

const JOURNAL_KEY = "lumenx:atelier:project-package-import:v1";

function renderControls(options: {
    locale?: "en" | "zh";
    project?: AtelierProject | null;
    ownerKey?: string;
    environmentKey?: string | null;
    loadProjects?: () => Promise<AtelierProject[]>;
    switchProject?: (projectId: string) => Promise<AtelierProject>;
} = {}) {
    const props = {
        project: options.project === undefined ? sourceProject() : options.project,
        ownerKey: options.ownerKey ?? "owner-a",
        environmentKey: options.environmentKey ?? "env-a",
        loadProjects: options.loadProjects ?? vi.fn(async () => [sourceProject()]),
        switchProject: options.switchProject ?? vi.fn(async () => importedProject()),
    };
    const messages = options.locale === "zh" ? zh : en;
    const view = render(
        <NextIntlClientProvider locale={options.locale ?? "en"} messages={messages}>
            <ProjectPackageControls {...props} />
        </NextIntlClientProvider>,
    );
    return { ...view, props };
}

function choosePackage(input = file()) {
    fireEvent.change(screen.getByLabelText(/project package|项目包/i, { selector: "input" }), {
        target: { files: [input] },
    });
}

beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    clearProjectPackageImportJournalForTests();
    vi.stubGlobal("URL", {
        createObjectURL: vi.fn(() => "blob:package"),
        revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});

describe("ProjectPackageControls", () => {
    it("renders localized controls in English and Chinese", () => {
        const { unmount } = renderControls({ locale: "en" });
        expect(screen.getByRole("region", { name: "Project package" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Export package" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Import package" })).toBeInTheDocument();
        unmount();

        renderControls({ locale: "zh" });
        expect(screen.getByRole("region", { name: "项目包" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "导出项目包" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "导入项目包" })).toBeInTheDocument();
    });

    it("keeps project package messages complete in English and Chinese", () => {
        expect(Object.keys(en.atelier.projectPackage).sort()).toEqual(PROJECT_PACKAGE_KEYS);
        expect(Object.keys(zh.atelier.projectPackage).sort()).toEqual(PROJECT_PACKAGE_KEYS);
    });

    it("exports current project with a safe filename", async () => {
        const anchors: HTMLAnchorElement[] = [];
        const realCreateElement = document.createElement.bind(document);
        const createElementSpy = vi.spyOn(document, "createElement").mockImplementation((tagName, options) => {
            const element = realCreateElement(tagName, options as ElementCreationOptions);
            if (tagName.toLowerCase() === "a") anchors.push(element as HTMLAnchorElement);
            return element;
        });
        api.exportProjectPackage.mockResolvedValue({
            blob: new Blob(["PK"], { type: "application/zip" }),
            filename: "../unsafe.zip",
        });
        renderControls();
        fireEvent.click(screen.getByRole("button", { name: "Export package" }));

        await waitFor(() => expect(api.exportProjectPackage).toHaveBeenCalledWith("atelier-old-1"));
        expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
        expect(anchors[0]?.download).toBe("Source-Project-atelier-old-1.lumenx.zip");
        expect(URL.createObjectURL).toHaveBeenCalled();
        createElementSpy.mockRestore();
    });

    it("imports only after list readback confirms the copied project", async () => {
        const imported = importedProject();
        const events: string[] = [];
        api.importProjectPackage.mockImplementation(async () => {
            events.push("post");
            return imported;
        });
        const loadProjects = vi.fn(async () => {
            events.push("list");
            return [sourceProject(), imported];
        });
        const switchProject = vi.fn(async (id: string) => {
            events.push(`switch:${id}`);
            return imported;
        });
        renderControls({ loadProjects, switchProject });
        choosePackage();

        await waitFor(() => expect(switchProject).toHaveBeenCalledWith(imported.id));
        expect(events).toEqual(["post", "list", `switch:${imported.id}`]);
        expect(await screen.findByText(/Imported independent project/)).toBeInTheDocument();
    });

    it("keeps missing readback locked to the known imported copy", async () => {
        api.importProjectPackage.mockResolvedValue(importedProject());
        const loadProjects = vi.fn(async () => [sourceProject()]);
        const switchProject = vi.fn();
        renderControls({ loadProjects, switchProject });
        choosePackage();

        expect(await screen.findByRole("alert")).toHaveTextContent("Imported copy");
        expect(loadProjects).toHaveBeenCalledTimes(1);
        expect(switchProject).not.toHaveBeenCalled();
    });

    it("does not duplicate POST on remount and lets the new mount observe original settlement", async () => {
        let resolveImport!: (project: AtelierProject) => void;
        api.importProjectPackage.mockImplementation(() => new Promise(resolve => { resolveImport = resolve; }));
        const loadProjects = vi.fn(async () => [sourceProject(), importedProject()]);
        const switchProject = vi.fn(async () => importedProject());
        const view = renderControls({ loadProjects, switchProject });
        choosePackage();
        choosePackage(file());
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);

        view.unmount();
        renderControls({ loadProjects, switchProject });
        expect(screen.getByRole("button", { name: "Importing…" })).toBeDisabled();
        choosePackage(file());
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);

        resolveImport(importedProject());
        expect(await screen.findByRole("alert")).toHaveTextContent("Imported copy");
        expect(loadProjects).not.toHaveBeenCalled();
        expect(switchProject).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole("button", { name: "Check project list" }));
        await waitFor(() => expect(switchProject).toHaveBeenCalledWith(importedProject().id));
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
    });

    it("keeps unknown outcome locked until list check and explicit acknowledgement", async () => {
        const { AtelierPackageImportError } = await import("@/lib/atelierProjectPackageApi");
        api.importProjectPackage.mockRejectedValueOnce(new AtelierPackageImportError("lost", true));
        const loadProjects = vi.fn(async () => [sourceProject()]);
        renderControls({ loadProjects });
        choosePackage();

        expect(await screen.findByRole("alert")).toHaveTextContent("Import outcome is unknown");
        expect(screen.getByRole("button", { name: "Import package" })).toBeDisabled();
        choosePackage(file());
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);

        fireEvent.click(screen.getByRole("button", { name: "Check project list" }));
        await waitFor(() => expect(loadProjects).toHaveBeenCalledTimes(1));
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
        expect(screen.getByRole("button", { name: "Import package" })).toBeDisabled();

        fireEvent.click(screen.getByRole("button", { name: "I checked the list" }));
        expect(screen.getByRole("button", { name: "Import package" })).toBeEnabled();
    });

    it("restores an unknown journal after reload without resubmitting", async () => {
        window.sessionStorage.setItem(JOURNAL_KEY, JSON.stringify({
            operationId: "stale-unknown",
            status: "unknown",
            ownerKey: "owner-a",
            projectId: "atelier-old-1",
            environmentKey: "env-a",
            fileName: "copy.lumenx.zip",
            updatedAt: Date.now(),
        }));
        const loadProjects = vi.fn(async () => [sourceProject()]);
        renderControls({ loadProjects });

        expect(screen.getByRole("alert")).toHaveTextContent("Import outcome is unknown");
        expect(screen.getByRole("button", { name: "Import package" })).toBeDisabled();
        expect(api.importProjectPackage).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole("button", { name: "Check project list" }));
        await waitFor(() => expect(loadProjects).toHaveBeenCalledTimes(1));
        expect(api.importProjectPackage).not.toHaveBeenCalled();
    });

    it("treats persisted importing journal after hard reload as recovery without reposting", async () => {
        window.sessionStorage.setItem(JOURNAL_KEY, JSON.stringify({
            operationId: "stale-importing",
            status: "importing",
            ownerKey: "owner-a",
            projectId: "atelier-old-1",
            environmentKey: "env-a",
            fileName: "copy.lumenx.zip",
            updatedAt: Date.now(),
        }));
        renderControls();

        expect(screen.getByRole("alert")).toHaveTextContent("Import outcome is unknown");
        expect(screen.getByRole("button", { name: "Import package" })).toBeDisabled();
        expect(api.importProjectPackage).not.toHaveBeenCalled();
    });

    it("recovers persisted known imported id after hard reload without switching environments", async () => {
        const imported = importedProject();
        window.sessionStorage.setItem(JOURNAL_KEY, JSON.stringify({
            operationId: "known-after-reload",
            status: "unknown",
            ownerKey: "owner-a",
            projectId: "atelier-old-1",
            environmentKey: "env-a",
            fileName: "copy.lumenx.zip",
            importedProjectId: imported.id,
            importedTitle: imported.title,
            updatedAt: Date.now(),
        }));
        const loadProjects = vi.fn(async () => [sourceProject(), imported]);
        const switchProject = vi.fn(async () => imported);
        renderControls({ loadProjects, switchProject });

        expect(screen.getByRole("alert")).toHaveTextContent("Imported copy");
        fireEvent.click(screen.getByRole("button", { name: "Check project list" }));
        await waitFor(() => expect(switchProject).toHaveBeenCalledWith(imported.id));
        expect(api.importProjectPackage).not.toHaveBeenCalled();
    });

    it("opens the known independent copy only after explicit recovery from the current project in the same environment", async () => {
        const imported = importedProject();
        window.sessionStorage.setItem(JOURNAL_KEY, JSON.stringify({
            operationId: "known-other-source", status: "unknown", ownerKey: "old-session",
            projectId: "original-source", environmentKey: "env-a", fileName: "copy.zip",
            importedProjectId: imported.id, updatedAt: Date.now(),
        }));
        const loadProjects = vi.fn(async () => [sourceProject(), imported]);
        const switchProject = vi.fn(async () => imported);
        renderControls({ ownerKey: "new-session", loadProjects, switchProject });
        expect(loadProjects).not.toHaveBeenCalled();
        expect(switchProject).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: "Check project list" }));
        await waitFor(() => expect(switchProject).toHaveBeenCalledWith(imported.id));
        expect(api.importProjectPackage).not.toHaveBeenCalled();
    });

    it("blocks recovery in a different runtime environment without list or switch", () => {
        const imported = importedProject();
        window.sessionStorage.setItem(JOURNAL_KEY, JSON.stringify({
            operationId: "wrong-env",
            status: "unknown",
            ownerKey: "owner-a",
            projectId: "atelier-old-1",
            environmentKey: "env-a",
            fileName: "copy.lumenx.zip",
            importedProjectId: imported.id,
            importedTitle: imported.title,
            updatedAt: Date.now(),
        }));
        const loadProjects = vi.fn(async () => [sourceProject(), imported]);
        const switchProject = vi.fn(async () => imported);
        renderControls({ environmentKey: "env-b", loadProjects, switchProject });

        expect(screen.getByRole("alert")).toHaveTextContent("another runtime environment");
        expect(screen.queryByRole("button", { name: "Check project list" })).not.toBeInTheDocument();
        expect(loadProjects).not.toHaveBeenCalled();
        expect(switchProject).not.toHaveBeenCalled();
    });

    it("persists the known imported id before readback and recovers after simulated reload", async () => {
        const imported = importedProject();
        let resolveLoad!: (projects: AtelierProject[]) => void;
        api.importProjectPackage.mockResolvedValue(imported);
        const loadProjects = vi.fn(() => new Promise<AtelierProject[]>(resolve => { resolveLoad = resolve; }));
        const switchProject = vi.fn(async () => imported);
        const view = renderControls({ loadProjects, switchProject });
        choosePackage();

        await waitFor(() => {
            const raw = window.sessionStorage.getItem(JOURNAL_KEY);
            expect(raw ? JSON.parse(raw).importedProjectId : null).toBe(imported.id);
        });
        view.unmount();
        resolveLoad([sourceProject(), imported]);
        await new Promise(resolve => setTimeout(resolve, 0));
        expect(switchProject).not.toHaveBeenCalled();

        resetProjectPackageImportJournalMemoryForTests();
        renderControls({ loadProjects: vi.fn(async () => [sourceProject(), imported]), switchProject });
        expect(screen.getByRole("alert")).toHaveTextContent("Imported copy");
        fireEvent.click(screen.getByRole("button", { name: "Check project list" }));
        await waitFor(() => expect(switchProject).toHaveBeenCalledWith(imported.id));
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
    });

    it("does not navigate an old import after its recovery lock was explicitly cleared", async () => {
        const imported = importedProject();
        let resolveLoad!: (projects: AtelierProject[]) => void;
        api.importProjectPackage.mockResolvedValue(imported);
        const loadProjects = vi.fn(() => new Promise<AtelierProject[]>(resolve => { resolveLoad = resolve; }));
        const switchProject = vi.fn(async () => imported);
        renderControls({ loadProjects, switchProject });
        choosePackage();
        await waitFor(() => expect(loadProjects).toHaveBeenCalledTimes(1));
        fireEvent.click(screen.getByRole("button", { name: "I checked the list" }));
        resolveLoad([sourceProject(), imported]);
        await new Promise(resolve => setTimeout(resolve, 0));
        expect(switchProject).not.toHaveBeenCalled();
    });

    it("does not navigate after unmounting during explicit known-copy recovery", async () => {
        const imported = importedProject();
        api.importProjectPackage.mockResolvedValue(imported);
        let resolveLoad!: (projects: AtelierProject[]) => void;
        const loadProjects = vi.fn().mockRejectedValueOnce(new Error("offline"))
            .mockImplementationOnce(() => new Promise<AtelierProject[]>(resolve => { resolveLoad = resolve; }));
        const switchProject = vi.fn(async () => imported);
        const view = renderControls({ loadProjects, switchProject });
        choosePackage();
        await waitFor(() => expect(loadProjects).toHaveBeenCalledTimes(1));
        fireEvent.click(screen.getByRole("button", { name: "Check project list" }));
        await waitFor(() => expect(loadProjects).toHaveBeenCalledTimes(2));
        view.unmount();
        resolveLoad([sourceProject(), imported]);
        await new Promise(resolve => setTimeout(resolve, 0));
        expect(switchProject).not.toHaveBeenCalled();
        expect(JSON.parse(window.sessionStorage.getItem(JOURNAL_KEY)!).importedProjectId).toBe(imported.id);
    });

    it("stops readback when persisting a committed import acknowledgement fails", async () => {
        const imported = importedProject();
        const originalSetItem = window.sessionStorage.setItem.bind(window.sessionStorage);
        Object.defineProperty(window.sessionStorage, "setItem", {
            configurable: true,
            value: vi.fn((key: string, value: string) => {
                if (JSON.parse(value).importedProjectId) throw new Error("quota");
                originalSetItem(key, value);
            }),
        });
        api.importProjectPackage.mockResolvedValue(imported);
        const loadProjects = vi.fn(async () => [sourceProject(), imported]);
        const switchProject = vi.fn(async () => imported);
        try {
            renderControls({ loadProjects, switchProject });
            choosePackage();
            expect(await screen.findByRole("alert")).toHaveTextContent("local import journal is unavailable or corrupt");
            expect(loadProjects).not.toHaveBeenCalled();
            expect(switchProject).not.toHaveBeenCalled();
        } finally {
            Object.defineProperty(window.sessionStorage, "setItem", { configurable: true, value: originalSetItem });
        }
    });

    it("fails closed on a corrupt persisted journal until explicit recovery", () => {
        window.sessionStorage.setItem(JOURNAL_KEY, "not-json");
        renderControls();

        expect(screen.getByRole("alert")).toHaveTextContent("local import journal is unavailable or corrupt");
        expect(screen.getByRole("button", { name: "Import package" })).toBeDisabled();
        expect(api.importProjectPackage).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole("button", { name: "Clear recovery lock" }));
        expect(screen.getByRole("button", { name: "Import package" })).toBeEnabled();
    });

    it("fails closed before POST when the import journal cannot be persisted", async () => {
        const originalSetItem = window.sessionStorage.setItem.bind(window.sessionStorage);
        Object.defineProperty(window.sessionStorage, "setItem", {
            configurable: true,
            value: vi.fn(() => { throw new Error("quota"); }),
        });
        renderControls();
        choosePackage();

        expect(await screen.findByRole("alert")).toHaveTextContent("local import journal is unavailable or corrupt");
        expect(api.importProjectPackage).not.toHaveBeenCalled();
        expect(screen.getByRole("button", { name: "Import package" })).toBeDisabled();
        Object.defineProperty(window.sessionStorage, "setItem", { configurable: true, value: originalSetItem });
    });

    it("keeps a known imported copy locked when list readback fails, then recovers that copy", async () => {
        const imported = importedProject();
        api.importProjectPackage.mockResolvedValue(imported);
        const loadProjects = vi.fn()
            .mockRejectedValueOnce(new Error("readback unavailable"))
            .mockResolvedValueOnce([sourceProject(), imported]);
        const switchProject = vi.fn(async () => imported);
        renderControls({ loadProjects, switchProject });
        choosePackage();

        expect(await screen.findByRole("alert")).toHaveTextContent("Imported copy");
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
        expect(switchProject).not.toHaveBeenCalled();
        expect(screen.getByRole("button", { name: "Import package" })).toBeDisabled();

        fireEvent.click(screen.getByRole("button", { name: "Check project list" }));
        await waitFor(() => expect(switchProject).toHaveBeenCalledWith(imported.id));
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
        expect(await screen.findByText(/Imported independent project/)).toBeInTheDocument();
    });

    it("keeps a known imported copy locked when project switch readback fails", async () => {
        const imported = importedProject();
        api.importProjectPackage.mockResolvedValue(imported);
        const loadProjects = vi.fn(async () => [sourceProject(), imported]);
        const switchProject = vi.fn()
            .mockRejectedValueOnce(new Error("GET failed"))
            .mockResolvedValueOnce(imported);
        renderControls({ loadProjects, switchProject });
        choosePackage();

        expect(await screen.findByRole("alert")).toHaveTextContent("Imported copy");
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
        expect(switchProject).toHaveBeenCalledTimes(1);

        fireEvent.click(screen.getByRole("button", { name: "Check project list" }));
        await waitFor(() => expect(switchProject).toHaveBeenCalledTimes(2));
        expect(switchProject).toHaveBeenLastCalledWith(imported.id);
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
    });


    it("does not navigate when import resolves after owner changes", async () => {
        let resolveImport!: (project: AtelierProject) => void;
        api.importProjectPackage.mockImplementation(() => new Promise(resolve => { resolveImport = resolve; }));
        const loadProjects = vi.fn(async () => [sourceProject(), importedProject()]);
        const switchProject = vi.fn(async () => importedProject());
        const view = renderControls({ loadProjects, switchProject });
        choosePackage();
        view.rerender(<NextIntlClientProvider locale="en" messages={en}>
            <ProjectPackageControls project={{ ...sourceProject(), id: "other" }} ownerKey="owner-b" environmentKey="env-a" loadProjects={loadProjects} switchProject={switchProject} />
        </NextIntlClientProvider>);
        resolveImport(importedProject());
        expect(await screen.findByRole("alert")).toHaveTextContent("Imported copy");
        expect(loadProjects).not.toHaveBeenCalled();
        expect(switchProject).not.toHaveBeenCalled();
    });

    it("does not download when export resolves after owner changes", async () => {
        let resolveExport!: (value: { blob: Blob; filename?: string }) => void;
        api.exportProjectPackage.mockImplementation(() => new Promise(resolve => { resolveExport = resolve; }));
        const view = renderControls();
        fireEvent.click(screen.getByRole("button", { name: "Export package" }));
        view.rerender(<NextIntlClientProvider locale="en" messages={en}>
            <ProjectPackageControls project={{ ...sourceProject(), id: "other" }} ownerKey="owner-b" environmentKey="env-a" loadProjects={vi.fn()} switchProject={vi.fn()} />
        </NextIntlClientProvider>);
        resolveExport({ blob: new Blob(["PK"]), filename: "copy.zip" });
        await new Promise(resolve => setTimeout(resolve, 0));
        expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
    });
});
