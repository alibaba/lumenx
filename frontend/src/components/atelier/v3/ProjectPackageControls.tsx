"use client";

import * as React from "react";
import { useTranslations } from "next-intl";
import {
  atelierProjectPackageApi,
  AtelierPackageImportError,
} from "@/lib/atelierProjectPackageApi";
import { safeProjectPackageFilename } from "@/lib/projectPackage";
import type { AtelierProject } from "@/lib/atelierTypes";

type ImportJournalStatus = "importing" | "unknown" | "blocked";

type ImportJournal = {
  operationId: string;
  status: ImportJournalStatus;
  ownerKey: string;
  projectId: string;
  fileName: string;
  updatedAt: number;
  environmentKey: string;
  importedProjectId?: string;
  importedTitle?: string;
  reason?: "corrupt" | "storage";
};

type Notice = { kind: "info" | "success" | "error" | "warning"; text: string };

type Props = {
  project: AtelierProject | null;
  ownerKey: string;
  environmentKey: string | null;
  disabled?: boolean;
  loadProjects: () => Promise<AtelierProject[]>;
  switchProject: (projectId: string) => Promise<AtelierProject>;
};

const JOURNAL_KEY = "lumenx:atelier:project-package-import:v1";
let memoryJournal: ImportJournal | null = null;
const journalListeners = new Set<() => void>();

function emitJournalChange(): void {
  journalListeners.forEach(listener => listener());
}

function subscribeJournal(listener: () => void): () => void {
  journalListeners.add(listener);
  return () => journalListeners.delete(listener);
}

function nextOperationId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `atelier-package-${Date.now()}-${Math.random()}`;
}

function blockedJournal(reason: "corrupt" | "storage"): ImportJournal {
  return {
    operationId: nextOperationId(),
    status: "blocked",
    ownerKey: "",
    projectId: "",
    environmentKey: "",
    fileName: "project package",
    updatedAt: Date.now(),
    reason,
  };
}

function isJournalShape(value: Partial<ImportJournal>): value is ImportJournal {
  return typeof value.operationId === "string"
    && (value.status === "importing" || value.status === "unknown" || value.status === "blocked")
    && typeof value.ownerKey === "string"
    && typeof value.projectId === "string"
    && typeof value.environmentKey === "string"
    && typeof value.fileName === "string"
    && typeof value.updatedAt === "number"
    && (value.importedProjectId === undefined || typeof value.importedProjectId === "string")
    && (value.importedTitle === undefined || typeof value.importedTitle === "string")
    && (value.reason === undefined || value.reason === "corrupt" || value.reason === "storage");
}

function readJournal(): ImportJournal | null {
  if (memoryJournal) return memoryJournal;
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(JOURNAL_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ImportJournal>;
    if (!isJournalShape(parsed)) {
      memoryJournal = blockedJournal("corrupt");
      return memoryJournal;
    }
    memoryJournal = parsed.status === "importing"
      ? { ...parsed, status: "unknown", updatedAt: Date.now() }
      : parsed;
    return memoryJournal;
  } catch {
    memoryJournal = blockedJournal("corrupt");
    return memoryJournal;
  }
}

function writeJournal(next: ImportJournal | null): boolean {
  if (typeof window === "undefined") {
    memoryJournal = next;
    emitJournalChange();
    return true;
  }
  try {
    if (next) window.sessionStorage.setItem(JOURNAL_KEY, JSON.stringify(next));
    else window.sessionStorage.removeItem(JOURNAL_KEY);
    memoryJournal = next;
    emitJournalChange();
    return true;
  } catch {
    return false;
  }
}

function forceMemoryJournal(next: ImportJournal): void {
  memoryJournal = next;
  emitJournalChange();
}

function isSameOwner(
  current: React.MutableRefObject<{ ownerKey: string; projectId: string | null }>,
  ownerKey: string,
  projectId: string,
): boolean {
  return current.current.ownerKey === ownerKey && current.current.projectId === projectId;
}

function isCurrentOperation(operationId: string): boolean {
  return memoryJournal?.operationId === operationId;
}

function journalSnapshot(): ImportJournal | null {
  return readJournal();
}

function emptyJournalSnapshot(): ImportJournal | null {
  return null;
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function clearProjectPackageImportJournalForTests(): void {
  memoryJournal = null;
  if (typeof window !== "undefined") window.sessionStorage.removeItem(JOURNAL_KEY);
  emitJournalChange();
}

export function resetProjectPackageImportJournalMemoryForTests(): void {
  memoryJournal = null;
  emitJournalChange();
}

export function ProjectPackageControls({
  project,
  ownerKey,
  environmentKey,
  disabled = false,
  loadProjects,
  switchProject,
}: Props) {
  const t = useTranslations("atelier.projectPackage");
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);
  const ownerRef = React.useRef({ ownerKey, projectId: project?.id ?? null });
  const mountedRef = React.useRef(false);
  const journal = React.useSyncExternalStore(subscribeJournal, journalSnapshot, emptyJournalSnapshot);
  const [notice, setNotice] = React.useState<Notice | null>(null);
  const [exporting, setExporting] = React.useState(false);
  const [checking, setChecking] = React.useState(false);

  React.useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  React.useEffect(() => {
    ownerRef.current = { ownerKey, projectId: project?.id ?? null };
  }, [ownerKey, project?.id]);

  const publishJournal = React.useCallback((next: ImportJournal | null) => {
    return writeJournal(next);
  }, []);

  const publishBlocked = React.useCallback((reason: "corrupt" | "storage") => {
    const blocked = blockedJournal(reason);
    forceMemoryJournal(blocked);
    if (mountedRef.current) setNotice({ kind: "error", text: t("journalBlocked") });
  }, [t]);

  const publishCurrentJournal = React.useCallback((operationId: string, next: ImportJournal | null) => {
    if (!isCurrentOperation(operationId)) return false;
    if (publishJournal(next)) return true;
    publishBlocked("storage");
    return false;
  }, [publishBlocked, publishJournal]);

  const lockActive = !!journal;
  const journalMatchesEnvironment = !journal || (!!environmentKey && journal.environmentKey === environmentKey);
  const canUseProject = !!project && !disabled && !!environmentKey;
  const importDisabled = !canUseProject || lockActive;
  const exportDisabled = !canUseProject || exporting || lockActive;

  const beginImport = React.useCallback(() => {
    if (importDisabled) return;
    fileInputRef.current?.click();
  }, [importDisabled]);

  const handleImportFile = React.useCallback(async (file: File | null) => {
    if (!file || !project || disabled || !environmentKey || readJournal()) return;
    const capturedOwner = ownerKey;
    const capturedProjectId = project.id;
    const pending: ImportJournal = {
      operationId: nextOperationId(),
      status: "importing",
      ownerKey: capturedOwner,
      projectId: capturedProjectId,
      environmentKey,
      fileName: file.name,
      updatedAt: Date.now(),
    };
    if (!publishJournal(pending)) {
      publishBlocked("storage");
      return;
    }
    setNotice({ kind: "info", text: t("importing") });

    const markUnknown = (imported?: AtelierProject) => {
      const next: ImportJournal = {
        ...pending,
        status: "unknown",
        updatedAt: Date.now(),
        importedProjectId: imported?.id ?? pending.importedProjectId,
        importedTitle: imported?.title ?? pending.importedTitle,
      };
      const persisted = publishCurrentJournal(pending.operationId, next);
      if (persisted && mountedRef.current) {
        setNotice({ kind: "warning", text: t("unknown") });
      }
      return persisted;
    };

    let imported: AtelierProject;
    try {
      imported = await atelierProjectPackageApi.importProjectPackage(file);
    } catch (error) {
      if (error instanceof AtelierPackageImportError && error.outcomeUnknown) {
        markUnknown();
        return;
      }
      if (publishCurrentJournal(pending.operationId, null)
        && mountedRef.current
        && isSameOwner(ownerRef, capturedOwner, capturedProjectId)) {
        setNotice({
          kind: "error",
          text: error instanceof Error ? `${t("importFailed")}: ${error.message}` : t("importFailed"),
        });
      }
      return;
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }

    if (!markUnknown(imported)) return;
    if (!mountedRef.current || !isSameOwner(ownerRef, capturedOwner, capturedProjectId)) {
      return;
    }

    let projects: AtelierProject[];
    try {
      projects = await loadProjects();
    } catch {
      markUnknown(imported);
      return;
    }

    if (!isCurrentOperation(pending.operationId)
      || !mountedRef.current || !isSameOwner(ownerRef, capturedOwner, capturedProjectId)) {
      markUnknown(imported);
      return;
    }
    if (!projects.some(item => item.id === imported.id)) {
      markUnknown(imported);
      return;
    }

    try {
      await switchProject(imported.id);
    } catch {
      markUnknown(imported);
      return;
    }
    if (publishCurrentJournal(pending.operationId, null)) {
      if (mountedRef.current) setNotice({
        kind: "success",
        text: t("imported", { title: imported.title || imported.id }),
      });
    }
  }, [disabled, environmentKey, loadProjects, ownerKey, project, publishBlocked, publishCurrentJournal, publishJournal, switchProject, t]);

  const exportProject = React.useCallback(async () => {
    if (!project || exportDisabled) return;
    const capturedOwner = ownerKey;
    const capturedProjectId = project.id;
    setExporting(true);
    setNotice(null);
    try {
      const result = await atelierProjectPackageApi.exportProjectPackage(project.id);
      if (!isSameOwner(ownerRef, capturedOwner, capturedProjectId)) return;
      downloadBlob(
        result.blob,
        safeProjectPackageFilename(project.title, project.id, result.filename),
      );
      if (mountedRef.current) setNotice({ kind: "success", text: t("exported") });
    } catch (error) {
      if (isSameOwner(ownerRef, capturedOwner, capturedProjectId)) {
        if (mountedRef.current) setNotice({
          kind: "error",
          text: error instanceof Error ? `${t("exportFailed")}: ${error.message}` : t("exportFailed"),
        });
      }
    } finally {
      if (mountedRef.current) setExporting(false);
    }
  }, [exportDisabled, ownerKey, project, t]);

  const checkUnknown = React.useCallback(async () => {
    if (!journal || journal.status !== "unknown") return;
    const capturedOperationId = journal.operationId;
    const capturedOwner = ownerRef.current.ownerKey;
    const capturedProjectId = ownerRef.current.projectId;
    const capturedEnvironmentKey = environmentKey;
    if (!capturedEnvironmentKey || journal.environmentKey !== capturedEnvironmentKey) {
      setNotice({ kind: "warning", text: t("wrongEnvironment") });
      return;
    }
    setChecking(true);
    try {
      const projects = await loadProjects();
      if (!mountedRef.current || !isCurrentOperation(capturedOperationId)
        || journal.environmentKey !== capturedEnvironmentKey
        || ownerRef.current.ownerKey !== capturedOwner
        || ownerRef.current.projectId !== capturedProjectId) return;
      const importedId = journal.importedProjectId;
      if (importedId && projects.some(item => item.id === importedId)) {
        try {
          await switchProject(importedId);
        } catch {
          if (isCurrentOperation(capturedOperationId)) {
            if (mountedRef.current) setNotice({ kind: "warning", text: t("unknown") });
          }
          return;
        }
        if (publishCurrentJournal(capturedOperationId, null)) {
          if (mountedRef.current) setNotice({
            kind: "success",
            text: t("imported", { title: journal.importedTitle || importedId }),
          });
        }
        return;
      }
      if (mountedRef.current) setNotice({ kind: "info", text: t("checkedList") });
    } catch (error) {
      if (isCurrentOperation(capturedOperationId)) {
        if (mountedRef.current) setNotice({
          kind: "error",
          text: error instanceof Error ? `${t("checkFailed")}: ${error.message}` : t("checkFailed"),
        });
      }
    } finally {
      if (isCurrentOperation(capturedOperationId)) setChecking(false);
    }
  }, [environmentKey, journal, loadProjects, publishCurrentJournal, switchProject, t]);

  const acknowledgeRecovery = React.useCallback(() => {
    if (!publishJournal(null)) {
      publishBlocked("storage");
      return;
    }
    setNotice({ kind: "info", text: t("acknowledged") });
  }, [publishBlocked, publishJournal, t]);

  const statusText = journal?.status === "importing"
    ? t("pending", { file: journal.fileName })
    : journal?.status === "unknown"
      ? !journalMatchesEnvironment
        ? t("wrongEnvironment")
        : journal.importedProjectId
          ? t("knownPending", { title: journal.importedTitle || journal.importedProjectId })
          : t("unknown")
      : journal?.status === "blocked"
        ? t("journalBlocked")
        : notice?.text ?? t("description");

  const canCheckUnknown = journal?.status === "unknown" && journalMatchesEnvironment;
  const showRecoveryActions = journal?.status === "unknown" || journal?.status === "blocked";

  return (
    <section className="rounded-2xl border border-white/[0.08] bg-white/[0.04] p-3" aria-label={t("title")}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-sm font-medium text-white/80">{t("title")}</h3>
          <p className="mt-1 text-xs leading-5 text-white/45">{t("copyExplanation")}</p>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          className="rounded-xl border border-white/[0.1] bg-white/[0.06] px-3 py-2 text-xs text-white/75 disabled:cursor-not-allowed disabled:opacity-40"
          disabled={exportDisabled}
          onClick={exportProject}
        >
          {exporting ? t("exporting") : t("export")}
        </button>
        <button
          type="button"
          className="rounded-xl border border-white/[0.1] bg-white/[0.06] px-3 py-2 text-xs text-white/75 disabled:cursor-not-allowed disabled:opacity-40"
          disabled={importDisabled}
          onClick={beginImport}
        >
          {journal?.status === "importing" ? t("importingShort") : t("import")}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          className="sr-only"
          accept=".zip,application/zip,application/octet-stream"
          aria-label={t("chooseFile")}
          disabled={importDisabled}
          onChange={event => void handleImportFile(event.currentTarget.files?.[0] ?? null)}
        />
        {showRecoveryActions ? (
          <>
            {canCheckUnknown ? (
              <button
                type="button"
                className="rounded-xl border border-amber-200/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-50 disabled:opacity-40"
                disabled={checking}
                onClick={checkUnknown}
              >
                {checking ? t("checking") : t("checkList")}
              </button>
            ) : null}
            <button
              type="button"
              className="rounded-xl border border-white/[0.1] px-3 py-2 text-xs text-white/65 disabled:opacity-40"
              disabled={checking}
              onClick={acknowledgeRecovery}
            >
              {journal.status === "blocked" ? t("clearRecovery") : t("acknowledge")}
            </button>
          </>
        ) : null}
      </div>
      <p
        className={`mt-3 text-xs leading-5 ${
          notice?.kind === "error" || journal?.status === "blocked"
            ? "text-rose-200"
            : notice?.kind === "warning" || journal?.status === "unknown"
              ? "text-amber-100"
              : "text-white/45"
        }`}
        role={notice?.kind === "error" || journal?.status === "unknown" || journal?.status === "blocked" ? "alert" : "status"}
      >
        {statusText}
      </p>
    </section>
  );
}

export default ProjectPackageControls;
