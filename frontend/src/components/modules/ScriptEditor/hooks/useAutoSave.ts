import { useEffect, useRef, useCallback, useState } from 'react';
import { Editor } from '@tiptap/react';
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { scriptEditorApi, type DocumentResponse } from '@/lib/scriptEditorApi';
import { useEditorStore } from '@/store/editorStore';

const AUTOSAVE_INTERVAL_MS = 30_000; // 30 seconds

type LoadStatus = 'idle' | 'loading' | 'ready' | 'local-only' | 'blocked-dirty' | 'recovered-draft' | 'error';
type SaveStatus = 'idle' | 'saving' | 'saved' | 'conflict' | 'error';
type ErrorKey =
  | 'dirtyRemote'
  | 'loadFailed'
  | 'saveNeedsRevision'
  | 'saveConflict'
  | 'saveFailed'
  | 'saveAckInvalid'
  | 'restoreBlocked'
  | 'restoreInvalid'
  | 'recoveredDraft';

type JsonDocument = Record<string, unknown>;

interface DraftRecord {
  content: JsonDocument;
  revision: number | null;
  timestamp: number;
}

const draftByProject = new Map<string, DraftRecord>();

function isAbortError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const candidate = error as { name?: unknown; code?: unknown };
  return candidate.name === 'AbortError' || candidate.name === 'CanceledError' || candidate.code === 'ERR_CANCELED';
}

function isConflictError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const response = (error as { response?: { status?: unknown } }).response;
  return response?.status === 409;
}

function validateEnvelope(value: DocumentResponse, projectId: string): DocumentResponse {
  if (!value || typeof value !== 'object') throw new Error('Invalid document response');
  if (value.project_id !== projectId) throw new Error('Document response project mismatch');
  if (!Number.isSafeInteger(value.revision) || value.revision < 0) throw new Error('Invalid document revision');
  if (!value.content || typeof value.content !== 'object') throw new Error('Invalid document content');
  return value;
}

function stableJson(value: unknown): string {
  return JSON.stringify(value);
}

function applyEditorContent(editor: Editor, content: object) {
  editor.commands.setContent(content, { emitUpdate: false });
}

function setEditorReadonly(editor: Editor | null, readonly: boolean) {
  // Tiptap emits a content update by default even for an editable-state change.
  // Loading/saving UI transitions must not create an unsaved document draft.
  const editableEditor = editor as Editor & { setEditable?: (editable: boolean, emitUpdate?: boolean) => void } | null;
  editableEditor?.setEditable?.(!readonly, false);
}

function updatedDate(envelope: DocumentResponse): Date | null {
  if (!envelope.updated_at) return null;
  const date = new Date(envelope.updated_at);
  return Number.isNaN(date.getTime()) ? null : date;
}

function errorText(key: ErrorKey): string {
  switch (key) {
    case 'dirtyRemote':
      return 'Remote document loaded after local edits. Your draft was kept.';
    case 'loadFailed':
      return 'Failed to load the saved document. Local edits are preserved.';
    case 'saveNeedsRevision':
      return 'Cannot save until the project document revision is loaded.';
    case 'saveConflict':
      return 'Document conflict: the saved version changed. Your draft was kept.';
    case 'saveAckInvalid':
      return 'Invalid save acknowledgement. Your draft was kept.';
    case 'restoreBlocked':
      return 'Finish or resolve the current draft before restoring a snapshot.';
    case 'restoreInvalid':
      return 'Invalid snapshot restore acknowledgement. Your draft was kept.';
    case 'recoveredDraft':
      return 'Recovered an unsaved local draft for this project.';
    case 'saveFailed':
    default:
      return 'Failed to save the document. Your draft was kept.';
  }
}

/**
 * 自动保存 Hook
 * - Loads project-owned document envelopes when a real projectId is present.
 * - Saves with expected_revision CAS and preserves local drafts on late reads/conflicts.
 * - Full no-project editor remains local-only and never fakes a server save.
 */
export function useAutoSave(editor: Editor | null, projectId: string | null) {
  const { setDirty, setLastSavedAt } = useEditorStore();
  const [documentRevision, setDocumentRevision] = useState<number | null>(null);
  const [loadStatus, setLoadStatus] = useState<LoadStatus>(projectId ? 'idle' : 'local-only');
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const [errorKey, setErrorKey] = useState<ErrorKey | null>(null);

  const isSavingRef = useRef(false);
  const haltedAutosaveRef = useRef(false);
  const queuedSaveRef = useRef(false);
  const queuedSnapshotRef = useRef(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sessionRef = useRef(0);
  const saveRunRef = useRef(0);
  const previousProjectRef = useRef<string | null>(null);
  const activeProjectRef = useRef<string | null>(projectId);
  activeProjectRef.current = projectId;
  const revisionRef = useRef<number | null>(null);
  const mountedRef = useRef(false);
  const isReloadingRef = useRef(false);
  const documentEnvironmentRef = useRef<ReturnType<typeof getRuntimeEnvironment>>(null);

  const setError = useCallback((key: ErrorKey | null) => {
    setErrorKey(key);
  }, []);

  const adoptEnvelope = useCallback(
    (envelope: DocumentResponse) => {
      revisionRef.current = envelope.revision;
      setDocumentRevision(envelope.revision);
      setLastSavedAt(updatedDate(envelope));
    },
    [setLastSavedAt]
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      sessionRef.current += 1;
      saveRunRef.current += 1;
      isSavingRef.current = false;
      queuedSaveRef.current = false;
      queuedSnapshotRef.current = false;
      isReloadingRef.current = false;
    };
  }, []);

  useEffect(() => {
    const previousProject = previousProjectRef.current;
    const projectChanged = previousProject !== null && previousProject !== projectId;
    sessionRef.current += 1;
    const session = sessionRef.current;
    previousProjectRef.current = projectId;
    saveRunRef.current += 1;
    isSavingRef.current = false;
    isReloadingRef.current = false;
    queuedSaveRef.current = false;
    queuedSnapshotRef.current = false;
    haltedAutosaveRef.current = false;
    revisionRef.current = null;
    documentEnvironmentRef.current = null;
    setDocumentRevision(null);
    setSaveStatus('idle');
    setError(null);

    if (!editor || !projectId) {
      setEditorReadonly(editor, false);
      setLoadStatus('local-only');
      setLastSavedAt(null);
      return;
    }

    const environment = requireActiveRuntimeEnvironment();
    documentEnvironmentRef.current = environment;
    setEditorReadonly(editor, true);
    const key = `${environment.key}::${projectId}`;
    const controller = new AbortController();
    setLoadStatus('loading');

    scriptEditorApi
      .loadDocument(projectId, { signal: controller.signal })
      .then((raw) => {
        if (!mountedRef.current || sessionRef.current !== session || controller.signal.aborted
          || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
        const envelope = validateEnvelope(raw, projectId);
        adoptEnvelope(envelope);
        const recoveredDraft = draftByProject.get(key);
        if (recoveredDraft) {
          applyEditorContent(editor, recoveredDraft.content);
          setDirty(true);
          setLoadStatus('recovered-draft');
          if (recoveredDraft.revision !== null && recoveredDraft.revision !== envelope.revision) {
            revisionRef.current = recoveredDraft.revision;
            setDocumentRevision(recoveredDraft.revision);
            haltedAutosaveRef.current = true;
            setSaveStatus('conflict');
            setError('saveConflict');
          } else {
            setError('recoveredDraft');
          }
          setEditorReadonly(editor, false);
          return;
        }
        if (!projectChanged && useEditorStore.getState().isDirty) {
          draftByProject.set(key, {
            content: editor.getJSON() as JsonDocument,
            revision: envelope.revision,
            timestamp: Date.now(),
          });
          setDirty(true);
          setLoadStatus('blocked-dirty');
          setError('dirtyRemote');
          setEditorReadonly(editor, false);
          return;
        }
        applyEditorContent(editor, envelope.content);
        setDirty(false);
        setLoadStatus('ready');
        setEditorReadonly(editor, false);
      })
      .catch((error) => {
        if (isAbortError(error) || !mountedRef.current || sessionRef.current !== session
          || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
        setLoadStatus('error');
        setError('loadFailed');
        setEditorReadonly(editor, false);
      });

    return () => {
      controller.abort();
      if (documentEnvironmentRef.current === environment) documentEnvironmentRef.current = null;
      // The gate may already have cleared or replaced runtime identity here.
      // Keep this editor's draft under the owner captured when it was loaded.
      if (!editor.isDestroyed && useEditorStore.getState().isDirty) {
        draftByProject.set(key, {
          content: editor.getJSON() as JsonDocument,
          revision: revisionRef.current,
          timestamp: Date.now(),
        });
      }
    };
  }, [editor, projectId, adoptEnvelope, setDirty, setError, setLastSavedAt]);

  const save = useCallback(
    async (createSnapshot = false, options: { force?: boolean } = { force: true }): Promise<void> => {
      if (!editor || !projectId || isReloadingRef.current) return;
      const environment = documentEnvironmentRef.current;
      if (!environment || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
      if (haltedAutosaveRef.current && !options.force) return;
      if (isSavingRef.current) {
        queuedSaveRef.current = true;
        queuedSnapshotRef.current = queuedSnapshotRef.current || createSnapshot;
        return;
      }
      const expectedRevision = revisionRef.current;
      if (expectedRevision === null) {
        setSaveStatus('error');
        setError('saveNeedsRevision');
        return;
      }

      const session = sessionRef.current;
      const saveRun = ++saveRunRef.current;
      const content = editor.getJSON() as JsonDocument;
      const submitted = stableJson(content);
      const key = `${environment.key}::${projectId}`;
      draftByProject.set(key, { content, revision: expectedRevision, timestamp: Date.now() });
      isSavingRef.current = true;
      let flushQueuedSave = true;
      setSaveStatus('saving');
      setError(null);

      try {
        const envelope = validateEnvelope(
          await scriptEditorApi.saveDocument(projectId, content, {
            expectedRevision,
            createSnapshot,
          }),
          projectId
        );
        if (!mountedRef.current || sessionRef.current !== session
          || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
        if (envelope.revision < expectedRevision || stableJson(envelope.content) !== submitted) {
          flushQueuedSave = false;
          haltedAutosaveRef.current = true;
          setDirty(true);
          setSaveStatus('error');
          setError('saveAckInvalid');
          return;
        }
        haltedAutosaveRef.current = false;
        adoptEnvelope(envelope);
        const current = stableJson(editor.getJSON());
        if (current === submitted) {
          draftByProject.delete(key);
          setDirty(false);
          setSaveStatus('saved');
        } else {
          draftByProject.set(key, {
            content: editor.getJSON() as JsonDocument,
            revision: envelope.revision,
            timestamp: Date.now(),
          });
          setDirty(true);
          queuedSaveRef.current = true;
          setSaveStatus('idle');
        }
      } catch (error) {
        if (!mountedRef.current || sessionRef.current !== session || isAbortError(error)
          || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
        flushQueuedSave = false;
        haltedAutosaveRef.current = true;
        queuedSaveRef.current = false;
        queuedSnapshotRef.current = false;
        setDirty(true);
        if (isConflictError(error)) {
          setSaveStatus('conflict');
          setError('saveConflict');
        } else {
          setSaveStatus('error');
          setError('saveFailed');
        }
      } finally {
        if (mountedRef.current && sessionRef.current === session && saveRunRef.current === saveRun
          && isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
          isSavingRef.current = false;
          if (flushQueuedSave && queuedSaveRef.current && !haltedAutosaveRef.current) {
            const snapshot = queuedSnapshotRef.current;
            queuedSaveRef.current = false;
            queuedSnapshotRef.current = false;
            void save(snapshot, { force: true });
          }
        }
      }
    },
    [editor, projectId, adoptEnvelope, setDirty, setError]
  );


  const discardAndReload = useCallback(async (): Promise<boolean> => {
    if (!editor || !projectId || isReloadingRef.current) return false;
    const session = sessionRef.current;
    const environment = requireActiveRuntimeEnvironment();
    isReloadingRef.current = true;
    queuedSaveRef.current = false;
    queuedSnapshotRef.current = false;
    setEditorReadonly(editor, true);
    setLoadStatus('loading');
    setError(null);

    try {
      const envelope = validateEnvelope(await scriptEditorApi.loadDocument(projectId), projectId);
      if (!mountedRef.current || sessionRef.current !== session
        || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return false;
      applyEditorContent(editor, envelope.content);
      adoptEnvelope(envelope);
      draftByProject.delete(`${environment.key}::${projectId}`);
      haltedAutosaveRef.current = false;
      setDirty(false);
      setLoadStatus('ready');
      setSaveStatus('saved');
      setError(null);
      setEditorReadonly(editor, false);
      return true;
    } catch (error) {
      if (isAbortError(error) || !mountedRef.current || sessionRef.current !== session
        || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return false;
      haltedAutosaveRef.current = true;
      setDirty(true);
      setLoadStatus('recovered-draft');
      setSaveStatus('conflict');
      setError('loadFailed');
      setEditorReadonly(editor, false);
      return false;
    } finally {
      if (mountedRef.current && sessionRef.current === session
        && isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
        isReloadingRef.current = false;
      }
    }
  }, [editor, projectId, adoptEnvelope, setDirty, setError]);

  const restoreSession = sessionRef.current;
  const restoreEnvironment = getRuntimeEnvironment();
  const adoptRestoredEnvelope = useCallback(
    (raw: DocumentResponse): boolean => {
      if (!editor || !projectId) return false;
      if (sessionRef.current !== restoreSession || activeProjectRef.current !== projectId) {
        return false;
      }
      if (!restoreEnvironment || !isCurrentRuntimeEnvironment(restoreEnvironment.key, restoreEnvironment.generation)) return false;
      if (isSavingRef.current || isReloadingRef.current || useEditorStore.getState().isDirty) {
        setSaveStatus('error');
        setError('restoreBlocked');
        return false;
      }
      try {
        const envelope = validateEnvelope(raw, projectId);
        if (sessionRef.current !== restoreSession || activeProjectRef.current !== projectId) {
          return false;
        }
        if (revisionRef.current !== null && envelope.revision < revisionRef.current) {
          throw new Error('Restore revision regressed');
        }
        applyEditorContent(editor, envelope.content);
        adoptEnvelope(envelope);
        draftByProject.delete(`${restoreEnvironment.key}::${projectId}`);
        setDirty(false);
        setSaveStatus('saved');
        setLoadStatus('ready');
        setError(null);
        return true;
      } catch {
        if (sessionRef.current !== restoreSession || activeProjectRef.current !== projectId) {
          return false;
        }
        setSaveStatus('error');
        setError('restoreInvalid');
        return false;
      }
    },
    [editor, projectId, restoreSession, restoreEnvironment, adoptEnvelope, setDirty, setError]
  );

  // 30s 周期自动保存
  useEffect(() => {
    intervalRef.current = setInterval(() => {
      if (useEditorStore.getState().isDirty) {
        void save(false, { force: false });
      }
    }, AUTOSAVE_INTERVAL_MS);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [save]);

  // Cmd+S / Ctrl+S 手动保存（创建快照）
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        void save(true, { force: true });
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [save]);

  // beforeunload 拦截
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (useEditorStore.getState().isDirty) {
        e.preventDefault();
      }
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [editor, projectId]);

  return {
    save,
    adoptRestoredEnvelope,
    discardAndReload,
    documentRevision,
    loadStatus,
    saveStatus,
    errorKey,
    errorMessage: errorKey ? errorText(errorKey) : null,
  };
}
