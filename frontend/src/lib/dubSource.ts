export type DubSourceProof = {
    schema_version: 1;
    task_id: string;
    video_url: string;
    audio_url: string;
    offset_ms: number;
};

export type MediaRefsLike = {
    video_url?: unknown;
    audio_url?: unknown;
    [key: string]: unknown;
};

export type VideoTaskLike = {
    id?: string | null;
    frame_id?: string | null;
    status?: string | null;
    video_url?: string | null;
    media_refs?: MediaRefsLike | null;
};

export type FrameDubLike = {
    id?: string | null;
    audio_url?: string | null;
    media_refs?: MediaRefsLike | null;
    selected_video_id?: string | null;
    video_tasks?: VideoTaskLike[] | null;
    preview_video_url?: string | null;
    dubbed_video_url?: string | null;
    dub_preview_source?: unknown;
    dub_applied_source?: unknown;
};

export type CurrentVideoTaskSelection =
    | { kind: 'selected'; task: VideoTaskLike }
    | { kind: 'default'; task: VideoTaskLike }
    | { kind: 'selected-mismatch'; task: null }
    | { kind: 'none'; task: null };

export type DubDisplayResult =
    | { kind: 'current'; url: string; taskId: string; source: DubSourceProof }
    | { kind: 'stale'; url?: string; reason: string; source: DubSourceProof | null }
    | { kind: 'none'; reason: string };

export type FrameAssemblyVideoOptions = {
    allowPreview?: boolean;
};

export type FrameAssemblyVideoResult =
    | { kind: 'preview'; url: string; taskId: string; source: DubSourceProof; history?: StoredDubHistory[] }
    | { kind: 'applied'; url: string; taskId: string; source: DubSourceProof; history?: StoredDubHistory[] }
    | { kind: 'original'; url: string; taskId: string; history?: StoredDubHistory[] }
    | { kind: 'none'; reason: string; history?: StoredDubHistory[] };

export type StoredDubHistory = {
    kind: 'stored-history';
    url: string;
    reason: 'unknown-source' | 'stale-source';
    source: unknown;
};

function asNonEmptyString(value: unknown): string | null {
    return typeof value === 'string' && value.length > 0 ? value : null;
}

function getFrameId(frame: FrameDubLike): string | null {
    return asNonEmptyString(frame.id);
}

function readMediaRefField(refs: MediaRefsLike | null | undefined, field: string): { present: boolean; value: string | null } {
    if (!refs || typeof refs !== 'object' || Array.isArray(refs)) {
        return { present: false, value: null };
    }
    if (!Object.prototype.hasOwnProperty.call(refs, field)) {
        return { present: false, value: null };
    }
    return { present: true, value: asNonEmptyString(refs[field]) };
}

function comparableMediaValue(displayValue: string | null, refs: MediaRefsLike | null | undefined, field: string): string | null {
    const mediaRef = readMediaRefField(refs, field);
    return mediaRef.present ? mediaRef.value : displayValue;
}

function getFrameAudioUrl(frame: FrameDubLike): string | null {
    return asNonEmptyString(frame.audio_url);
}

function getFrameAudioRef(frame: FrameDubLike): string | null {
    return comparableMediaValue(getFrameAudioUrl(frame), frame.media_refs, 'audio_url');
}

function getSelectedTaskId(frame: FrameDubLike): string | null {
    return asNonEmptyString(frame.selected_video_id);
}

function getPreviewUrl(frame: FrameDubLike): string | null {
    return asNonEmptyString(frame.preview_video_url);
}

function getAppliedUrl(frame: FrameDubLike): string | null {
    return asNonEmptyString(frame.dubbed_video_url);
}

export function getVideoTaskId(task: VideoTaskLike): string | null {
    return asNonEmptyString(task.id);
}

export function getVideoTaskFrameId(task: VideoTaskLike): string | null {
    return asNonEmptyString(task.frame_id);
}

export function getVideoTaskUrl(task: VideoTaskLike): string | null {
    return asNonEmptyString(task.video_url);
}

function getVideoTaskVideoRef(task: VideoTaskLike): string | null {
    return comparableMediaValue(getVideoTaskUrl(task), task.media_refs, 'video_url');
}

export function isCompletedVideoTaskWithUrl(task: VideoTaskLike): boolean {
    return task.status === 'completed' && !!getVideoTaskUrl(task);
}

export function normalizeDubSource(value: unknown): DubSourceProof | null {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const record = value as Record<string, unknown>;
    const allowedKeys = ['schema_version', 'task_id', 'video_url', 'audio_url', 'offset_ms'];
    if (Object.keys(record).some((key) => !allowedKeys.includes(key))) return null;
    if (record.schema_version !== 1) return null;
    const taskId = asNonEmptyString(record.task_id);
    const videoUrl = asNonEmptyString(record.video_url);
    const audioUrl = asNonEmptyString(record.audio_url);
    const offsetMs = record.offset_ms;
    if (!taskId || !videoUrl || !audioUrl) return null;
    if (typeof offsetMs !== 'number' || !Number.isSafeInteger(offsetMs) || offsetMs < 0) return null;
    return {
        schema_version: 1,
        task_id: taskId,
        video_url: videoUrl,
        audio_url: audioUrl,
        offset_ms: offsetMs,
    };
}

function sameFrame(frame: FrameDubLike, task: VideoTaskLike): boolean {
    const frameId = getFrameId(frame);
    const taskFrameId = getVideoTaskFrameId(task);
    return !!frameId && taskFrameId === frameId;
}

function sourceMatchesTaskAndAudio(frame: FrameDubLike, task: VideoTaskLike, source: DubSourceProof): boolean {
    const taskId = getVideoTaskId(task);
    const taskUrl = getVideoTaskVideoRef(task);
    const audioUrl = getFrameAudioRef(frame);
    return !!taskId &&
        source.task_id === taskId &&
        !!taskUrl && source.video_url === taskUrl &&
        !!audioUrl && source.audio_url === audioUrl &&
        sameFrame(frame, task) &&
        isCompletedVideoTaskWithUrl(task);
}

export function selectCurrentVideoTask(frame: FrameDubLike, tasks: VideoTaskLike[]): CurrentVideoTaskSelection {
    const frameId = getFrameId(frame);
    const selectedTaskId = getSelectedTaskId(frame);
    const sameFrameCompleted = (task: VideoTaskLike) =>
        isCompletedVideoTaskWithUrl(task) && !!frameId && getVideoTaskFrameId(task) === frameId;

    if (selectedTaskId) {
        const selected = tasks.find((task) => getVideoTaskId(task) === selectedTaskId) ?? null;
        if (selected && sameFrameCompleted(selected)) {
            return { kind: 'selected', task: selected };
        }
        return { kind: 'selected-mismatch', task: null };
    }

    const firstCompleted = tasks.find(sameFrameCompleted) ?? null;
    if (firstCompleted) return { kind: 'default', task: firstCompleted };
    return { kind: 'none', task: null };
}

function allFrameTasks(frame: FrameDubLike, tasks: VideoTaskLike[] | undefined): VideoTaskLike[] {
    if (tasks) return tasks;
    if (Array.isArray(frame.video_tasks)) return frame.video_tasks;
    return [];
}

export function selectPreviewDubForTask(frame: FrameDubLike, task: VideoTaskLike): DubDisplayResult {
    const outputUrl = getPreviewUrl(frame);
    const source = normalizeDubSource(frame.dub_preview_source);
    if (!outputUrl) return { kind: 'none', reason: 'missing-preview-url' };
    if (!source) return { kind: 'stale', url: outputUrl, reason: 'missing-preview-source', source: null };
    if (!sourceMatchesTaskAndAudio(frame, task, source)) {
        return { kind: 'stale', url: outputUrl, reason: 'preview-source-mismatch', source };
    }
    const taskId = getVideoTaskId(task);
    if (!taskId) return { kind: 'stale', url: outputUrl, reason: 'missing-task-id', source };
    return { kind: 'current', url: outputUrl, taskId, source };
}

export function selectAppliedDubForTask(frame: FrameDubLike, task: VideoTaskLike): DubDisplayResult {
    const outputUrl = getAppliedUrl(frame);
    const source = normalizeDubSource(frame.dub_applied_source);
    if (!outputUrl) return { kind: 'none', reason: 'missing-applied-url' };
    if (!source) return { kind: 'stale', url: outputUrl, reason: 'missing-applied-source', source: null };
    if (!sourceMatchesTaskAndAudio(frame, task, source)) {
        return { kind: 'stale', url: outputUrl, reason: 'applied-source-mismatch', source };
    }
    const taskId = getVideoTaskId(task);
    if (!taskId) return { kind: 'stale', url: outputUrl, reason: 'missing-task-id', source };
    return { kind: 'current', url: outputUrl, taskId, source };
}

function storedHistory(frame: FrameDubLike): StoredDubHistory[] {
    const history: StoredDubHistory[] = [];
    const previewUrl = getPreviewUrl(frame);
    if (previewUrl) {
        const source = normalizeDubSource(frame.dub_preview_source);
        history.push({
            kind: 'stored-history',
            url: previewUrl,
            reason: source ? 'stale-source' : 'unknown-source',
            source: frame.dub_preview_source ?? null,
        });
    }
    const appliedUrl = getAppliedUrl(frame);
    if (appliedUrl) {
        const source = normalizeDubSource(frame.dub_applied_source);
        history.push({
            kind: 'stored-history',
            url: appliedUrl,
            reason: source ? 'stale-source' : 'unknown-source',
            source: frame.dub_applied_source ?? null,
        });
    }
    return history;
}

export function selectFrameAssemblyVideo(frame: FrameDubLike, tasks?: VideoTaskLike[], options: FrameAssemblyVideoOptions = {}): FrameAssemblyVideoResult {
    const frameTasks = allFrameTasks(frame, tasks);
    const history = storedHistory(frame);
    const selection = selectCurrentVideoTask(frame, frameTasks);
    if (!selection.task) {
        return { kind: 'none', reason: selection.kind, history };
    }

    if (options.allowPreview) {
        const preview = selectPreviewDubForTask(frame, selection.task);
        if (preview.kind === 'current') {
            return { kind: 'preview', url: preview.url, taskId: preview.taskId, source: preview.source, history };
        }
    }

    const applied = selectAppliedDubForTask(frame, selection.task);
    if (applied.kind === 'current') {
        return { kind: 'applied', url: applied.url, taskId: applied.taskId, source: applied.source, history };
    }

    const taskUrl = getVideoTaskUrl(selection.task);
    const taskId = getVideoTaskId(selection.task);
    if (taskUrl && taskId) return { kind: 'original', url: taskUrl, taskId, history };
    return { kind: 'none', reason: 'missing-task-url', history };
}

export function selectVerifiedAppliedDubUrlForTask(frame: FrameDubLike, task: VideoTaskLike): string | undefined {
    const result = selectAppliedDubForTask(frame, task);
    return result.kind === 'current' ? result.url : undefined;
}
