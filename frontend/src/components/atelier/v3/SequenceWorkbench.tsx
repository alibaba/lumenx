"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { useAtelierStore } from "@/store/atelierStore";
import { clearSequenceDraft, readSequenceDraft, saveSequenceDraft, type SequenceDraft, type SequenceDraftOwner } from "@/lib/atelierSequenceDraft";
import { candidateSequenceOptions, createSequenceClipId, getSequenceSource, sequenceEntryForCandidate, sequenceEntryForVideo } from "@/lib/atelierSequenceSources";
import { atelierSequenceApi } from "@/lib/atelierSequenceApi";
import type { AtelierNode, AtelierProject, AtelierSequenceEntry } from "@/lib/atelierTypes";
import { ExportsPanel } from "./ExportsPanel";
import { SequenceStrip, sequenceClipId } from "./SequenceStrip";

const buttonClass = "rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-xs text-white/75 hover:bg-white/10 disabled:opacity-35";

/** Mounted with the shell's environment/project/session key so late UI work stays local. */
export function SequenceWorkbench({ project, selectedNode, owner }: {
    project: AtelierProject; selectedNode: AtelierNode | null; owner: SequenceDraftOwner;
}) {
    const t = useTranslations("atelier.sequenceEditor");
    const saveSequence = useAtelierStore(state => state.saveSequence);
    const createExport = useAtelierStore(state => state.createSequenceExport);
    const redoExport = useAtelierStore(state => state.redoSequenceExport);
    const deleteExport = useAtelierStore(state => state.deleteSequenceExport);
    const refreshProject = useAtelierStore(state => state.refreshProject);
    const pending = useAtelierStore(state => state.pendingExports);
    const pendingDeletes = useAtelierStore(state => state.pendingExportDeletions);
    const jobs = useAtelierStore(state => state.sequenceExportJobs);
    const loadPending = useAtelierStore(state => state.loadPendingExports);
    const loadPendingDeletes = useAtelierStore(state => state.loadPendingExportDeletions);
    const readback = useAtelierStore(state => state.readbackPendingExport);
    const readbackDelete = useAtelierStore(state => state.readbackPendingExportDeletion);
    const refreshJob = useAtelierStore(state => state.refreshSequenceExport);
    const cancelJob = useAtelierStore(state => state.cancelSequenceExport);
    const repairJob = useAtelierStore(state => state.repairSequenceExport);
    const resumeJob = useAtelierStore(state => state.resumeSequenceExport);
    const needsRefresh = useAtelierStore(state => state.needsRefresh);
    const isSaving = useAtelierStore(state => state.isSaving);
    const [draft, setDraft] = useState<SequenceDraft | null>(null);
    const [ready, setReady] = useState(false);
    const [storageBlocked, setStorageBlocked] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [candidateId, setCandidateId] = useState("");
    const alive = useRef(true);
    const operationInFlight = useRef(false);
    const ownerKey = JSON.stringify(owner);

    useEffect(() => {
        alive.current = true;
        try { setDraft(readSequenceDraft(owner)); loadPending(); loadPendingDeletes(); }
        catch (err) { setStorageBlocked(true); setError(err instanceof Error ? err.message : String(err)); }
        setReady(true);
        return () => { alive.current = false; };
        // Parent remounts on every owner/session change. Avoid object-identity reloads.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [ownerKey, loadPending, loadPendingDeletes]);

    const activeIds = jobs.filter(job => ["queued", "running", "cancelling"].includes(job.status)).map(job => job.job_id).join(",");
    useEffect(() => {
        if (!activeIds) return;
        let stopped = false;
        let timer: ReturnType<typeof setTimeout>;
        const poll = async () => {
            for (const id of activeIds.split(",")) {
                if (stopped) return;
                try { await refreshJob(id); }
                catch (err) { if (!stopped) setError(err instanceof Error ? err.message : String(err)); }
            }
            if (!stopped) timer = setTimeout(() => void poll(), 2000);
        };
        timer = setTimeout(() => void poll(), 1000);
        return () => { stopped = true; clearTimeout(timer); };
    }, [activeIds, refreshJob]);

    const run = useCallback(async (operation: () => Promise<unknown>) => {
        if (operationInFlight.current) return;
        operationInFlight.current = true;
        setBusy(true); setError(null); setNotice(null);
        try { await operation(); }
        catch (err) { if (alive.current) setError(err instanceof Error ? err.message : String(err)); }
        finally { operationInFlight.current = false; if (alive.current) setBusy(false); }
    }, []);
    const entries = draft?.entries ?? project.sequence;
    const blocked = !ready || storageBlocked || busy || isSaving;
    const staleDraft = !!draft && draft.baseRevision !== project.revision;
    const candidateOptions = useMemo(() => selectedNode ? candidateSequenceOptions(selectedNode) : [], [selectedNode]);
    const candidateOptionsKey = candidateOptions.map(option => option.candidateId).join("\u001f");
    useEffect(() => {
        setCandidateId(current => candidateOptions.some(option => option.candidateId === current) ? current : candidateOptions[0]?.candidateId ?? "");
    }, [candidateOptionsKey, selectedNode?.id, candidateOptions]);
    const edit = (next: AtelierSequenceEntry[]) => {
        if (blocked) return;
        try { setDraft(saveSequenceDraft(owner, draft?.baseRevision ?? project.revision, next)); setNotice(null); }
        catch (err) { setStorageBlocked(true); setError(err instanceof Error ? err.message : String(err)); }
    };
    const save = async () => {
        if (!draft) return project;
        if (staleDraft || needsRefresh) throw new Error(t("staleDraft"));
        if (draft.entries.length > 0) await checkSequence(draft.entries, draft.baseRevision);
        const saved = await saveSequence(draft.entries, draft.baseRevision);
        // Do not erase this durable draft after navigation or a newer local edit.
        if (alive.current && clearSequenceDraft(owner, draft)) setDraft(null);
        return saved;
    };
    const checkSequence = async (sequence: AtelierSequenceEntry[], revision: number) => {
        const result = await atelierSequenceApi.preflight(project.id, { entries: sequence });
        if (!alive.current) throw new Error("Atelier sequence owner changed");
        if (result.revision !== revision) throw new Error(t("staleDraft"));
        if (result.report.errors.length) throw new Error(result.report.errors.map(issue => issue.message).join("; "));
    };
    const exportCurrent = () => run(async () => {
        if (!draft) await checkSequence(project.sequence, project.revision);
        await save();
        if (alive.current) await createExport();
    });
    const redoHistoryExport: React.ComponentProps<typeof ExportsPanel>["onRedo"] = record => {
        void run(() => redoExport(record));
    };
    const deleteHistoryExport: React.ComponentProps<typeof ExportsPanel>["onDelete"] = record => {
        if (!record.deletion && !window.confirm(t("deleteConfirm"))) return;
        void run(() => deleteExport(record, true));
    };
    const clips = entries.map(entry => {
        const source = getSequenceSource(project.nodes, entry);
        return { entry, title: source.node?.title || entry.parentId, mediaUrl: source.url,
            unavailableReason: source.unavailable ? t(`source.${source.unavailable}`) : null };
    });
    const insert = (entry: AtelierSequenceEntry, index = entries.length) => {
        const next = [...entries];
        next.splice(index, 0, { ...entry, id: createSequenceClipId() });
        edit(next);
    };
    const selectedEntry = selectedNode ? sequenceEntryForVideo(selectedNode) : null;
    const candidateEntry = selectedNode && candidateId ? sequenceEntryForCandidate(selectedNode, candidateId) : null;
    const candidateInsertBlocked = blocked || staleDraft || needsRefresh || !candidateEntry;
    const active = jobs.find(job => ["queued", "running", "cancelling", "repair_required"].includes(job.status));
    const historyActionDisabled = busy || pending.length > 0 || pendingDeletes.length > 0 || !!active;
    const disabledReason = !ready || storageBlocked ? t("storageBlocked") : busy ? t("processing") : needsRefresh || staleDraft ? t("staleDraft")
        : pending.length || pendingDeletes.length ? t("pending") : active ? t("inProgress") : clips.some(clip => clip.unavailableReason) ? t("invalidClips") : entries.length === 0 ? t("empty") : null;

    return <section className="space-y-3 border-t border-white/10 bg-black/25 p-4" aria-label={t("title")}>
        <div className="flex flex-wrap items-center gap-2">
            <button className={buttonClass} disabled={blocked || !selectedEntry} onClick={() => selectedEntry && insert(selectedEntry)}>{t("addSelected")}</button>
            {candidateOptions.length > 0 && <label className="flex min-w-[220px] items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs text-white/65">
                <span>{t("candidatePickerLabel")}</span>
                <select aria-label={t("candidatePickerLabel")} className="min-w-0 flex-1 bg-transparent text-white outline-none disabled:opacity-45"
                    disabled={blocked || staleDraft || needsRefresh} value={candidateId} onChange={event => setCandidateId(event.target.value)}>
                    {candidateOptions.map(option => <option className="bg-black text-white" key={option.candidateId} value={option.candidateId}>
                        {t("candidateOption", { index: option.ordinal, id: option.candidateId.slice(0, 8) })}
                    </option>)}
                </select>
            </label>}
            {candidateOptions.length > 0 && <button className={buttonClass} disabled={candidateInsertBlocked}
                onClick={() => candidateEntry && insert(candidateEntry)}>{t("addCandidate")}</button>}
            <button className={buttonClass} disabled={blocked || !draft || staleDraft || needsRefresh} onClick={() => void run(async () => { await save(); if (alive.current) setNotice(t("saved")); })}>{t("save")}</button>
            {(staleDraft || needsRefresh) && <button className={buttonClass} disabled={blocked} onClick={() => void run(async () => {
                const fresh = await refreshProject();
                if (alive.current && draft) setDraft(saveSequenceDraft(owner, fresh.revision, draft.entries));
            })}>{t("reloadDraft")}</button>}
            <span className="text-xs text-white/45">{draft ? t("unsaved") : t("saved")}</span>
        </div>
        {error && <p role="alert" className="text-xs text-amber-200">{error}</p>}
        {notice && <p role="status" className="text-xs text-white/65">{notice}</p>}
        {pending.map(request => <div key={request.clientRequestId} className="flex items-center gap-3 text-xs text-amber-200">
            <span>{t("pending")}</span><button className={buttonClass} onClick={() => void run(async () => {
                const ack = await readback(request.clientRequestId);
                if (alive.current) setNotice(ack ? t("requestFound") : t("notFound"));
            })}>{t("readOriginal")}</button>
        </div>)}
        {pendingDeletes.map(request => <div key={request.clientRequestId} className="flex items-center gap-3 text-xs text-amber-200">
            <span>{t("deletePending")}</span><button className={buttonClass} onClick={() => void run(async () => {
                const ack = await readbackDelete(request.clientRequestId);
                if (alive.current) setNotice(ack ? t("deleteFound") : t("notFound"));
            })}>{t("readOriginal")}</button>
        </div>)}
        <fieldset disabled={blocked} className="min-w-0">
            <SequenceStrip entries={clips} exportState={{ status: busy ? "saving" : "idle" }} exportDisabledReason={disabledReason}
                labels={{ title: t("title"), empty: t("empty"), export: draft ? t("saveExport") : t("export"), cancel: t("cancel"), repair: t("repair"), resume: t("resume"), remove: t("remove"), trim: t("trim"), applyTrim: t("applyTrim"), cancelTrim: t("cancel"), moveUp: t("moveUp"), moveDown: t("moveDown"), unavailable: t("unavailable"), trimStart: t("trimStart"), trimEnd: t("trimEnd"), invalidTrim: t("invalidTrim") }}
                onReorder={edit} onTrim={(id, patch) => edit(entries.map((entry, index) => sequenceClipId(entry, index) === id ? { ...entry, ...patch } : entry))}
                onRemove={id => edit(entries.filter((entry, index) => sequenceClipId(entry, index) !== id))}
                onInsertFromDrop={(entry, { index }) => insert(entry, index)} onExport={() => void exportCurrent()} />
        </fieldset>
        {jobs.length > 0 && <ul className="space-y-2 text-xs text-white/65" aria-label={t("jobs")}>
            {jobs.map(job => <li key={job.job_id} className="flex flex-wrap items-center gap-2 rounded-xl bg-white/[0.035] p-2">
                <span>{t(`status.${job.status}`)}</span><span className="font-mono text-white/35">{job.job_id.slice(0, 8)}</span>
                {job.error_message && <span className="text-amber-200">{job.error_message}</span>}
                {job.status === "queued" && <button className={buttonClass} disabled={busy} onClick={() => void run(() => resumeJob(job.job_id))}>{t("resume")}</button>}
                {["queued", "running", "cancelling"].includes(job.status) && <button className={buttonClass} disabled={busy} onClick={() => void run(() => cancelJob(job.job_id))}>{t("cancel")}</button>}
                {["running", "cancelling", "repair_required"].includes(job.status) && <button className={buttonClass} disabled={busy} onClick={() => void run(() => repairJob(job.job_id))}>{t("repair")}</button>}
            </li>)}
        </ul>}
        <ExportsPanel records={project.exports} onRedo={redoHistoryExport} labels={{
            title: t("history"), empty: t("noExports"), loading: t("loading"), errorTitle: t("historyError"), retry: t("refresh"),
            download: t("download"), copy: t("copy"), redo: t("redo"), redoUnavailable: t("redoUnavailable"), delete: t("delete"),
            retryDelete: t("retryDelete"), deletionPending: t("deletePending"), deletionBlocked: message => message || t("deleteBlocked"),
            clipCount: count => t("clipCount", { count }),
        }} actionDisabled={historyActionDisabled} onDelete={deleteHistoryExport} />
    </section>;
}
