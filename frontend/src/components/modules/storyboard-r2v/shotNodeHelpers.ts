/**
 * ShotNode pure helpers — append/select T2I image history, derive
 * the active T2I image url, and migrate legacy shot drafts (where
 * the new t2iImageUrls / t2iSelectedIndex / videoTaskIdsByTab fields
 * don't exist) into the v2 shape.
 *
 * Kept free of React / state so unit tests can exercise the migration
 * + history bookkeeping independently of the UI layer.
 */
import { selectFrameAssemblyVideo, type FrameDubLike, type VideoTaskLike } from "@/lib/dubSource";
import { T2I_HISTORY_LIMIT, type ShotNode } from "./ShotCard";

type FrameToShotInput = FrameDubLike & {
    id: string;
    t2i_image_urls?: unknown;
    t2i_selected_index?: unknown;
    rendered_image_url?: string | null;
    image_url?: string | null;
    last_frame_url?: string | null;
    video_url?: string | null;
    dub_offset_ms?: unknown;
    visual_description?: string | null;
    action_description?: string | null;
    workbench_tab_mode?: string | null;
    duration?: number | null;
    assembled_prompt?: string | null;
    dialogue_structured?: ShotNode["dialogueStructured"];
    camera_movement_structured?: ShotNode["cameraMovementStructured"];
    shot_size?: string | null;
    camera_angle?: string | null;
    transition_hint?: string | null;
    is_video_pinned?: unknown;
    final_take_id?: string | null;
};

/** Bring a shot from disk (localStorage draft) into the v2 shape.
 *  Idempotent — calling on an already-v2 shot returns identity. */
export function migrateShotNode(shot: ShotNode): ShotNode {
    let next = shot;
    // t2iImageUrls: seed from legacy single-image field if needed.
    if (!Array.isArray(next.t2iImageUrls)) {
        const seed = typeof next.t2iImageUrl === "string" && next.t2iImageUrl
            ? [next.t2iImageUrl]
            : [];
        next = { ...next, t2iImageUrls: seed };
    }
    // t2iSelectedIndex: default to the last image if any, else 0.
    if (typeof next.t2iSelectedIndex !== "number" || next.t2iSelectedIndex < 0) {
        next = {
            ...next,
            t2iSelectedIndex: Math.max(0, (next.t2iImageUrls?.length ?? 1) - 1),
        };
    }
    // videoTaskIdsByTab: default empty buckets.
    if (!next.videoTaskIdsByTab) {
        // Backfill: legacy single videoTaskId belongs to the shot's
        // current tabMode so the candidates panel sees it instead of
        // pretending there's no history.
        const buckets: { t2i_i2v?: string[]; direct_r2v?: string[] } = {};
        if (next.videoTaskId) {
            buckets[next.tabMode] = [next.videoTaskId];
        }
        next = { ...next, videoTaskIdsByTab: buckets };
    }
    return next;
}

/** Append a new T2I image URL to the shot's history and make it
 *  the active selection. Caps at T2I_HISTORY_LIMIT (oldest dropped
 *  FIFO). De-dupes by URL: re-generating the same URL just bumps it
 *  to active without duplicating in history.
 *
 *  Also updates the legacy t2iImageUrl field so existing reads keep
 *  working until the rewrite removes them. */
export function appendT2IImage(shot: ShotNode, url: string): ShotNode {
    if (!url) return shot;
    const migrated = migrateShotNode(shot);
    const existing = migrated.t2iImageUrls ?? [];
    const dedupedIdx = existing.indexOf(url);
    let nextUrls: string[];
    let nextIndex: number;
    if (dedupedIdx >= 0) {
        nextUrls = existing;
        nextIndex = dedupedIdx;
    } else {
        const appended = [...existing, url];
        nextUrls = appended.length > T2I_HISTORY_LIMIT
            ? appended.slice(appended.length - T2I_HISTORY_LIMIT)
            : appended;
        nextIndex = nextUrls.length - 1;
    }
    return {
        ...migrated,
        t2iImageUrls: nextUrls,
        t2iSelectedIndex: nextIndex,
        t2iImageUrl: nextUrls[nextIndex],
    };
}

/** Set the active T2I image by index. Clamps to range; updates the
 *  legacy t2iImageUrl mirror so consumers that haven't migrated keep
 *  reading the right URL. No-op if the index is already active. */
export function setActiveT2IIndex(shot: ShotNode, index: number): ShotNode {
    const migrated = migrateShotNode(shot);
    const urls = migrated.t2iImageUrls ?? [];
    if (urls.length === 0) return migrated;
    const clamped = Math.max(0, Math.min(index, urls.length - 1));
    if (clamped === migrated.t2iSelectedIndex) return migrated;
    return {
        ...migrated,
        t2iSelectedIndex: clamped,
        t2iImageUrl: urls[clamped],
    };
}

/** Remove a T2I image at index. If the removed one was active, the
 *  active selection falls back to the nearest remaining image
 *  (prefer the one that took its slot, else the previous). Returns
 *  shot unchanged when index is out of range. */
export function removeT2IImage(shot: ShotNode, index: number): ShotNode {
    const migrated = migrateShotNode(shot);
    const urls = migrated.t2iImageUrls ?? [];
    if (index < 0 || index >= urls.length) return migrated;
    const nextUrls = [...urls.slice(0, index), ...urls.slice(index + 1)];
    let nextIndex = migrated.t2iSelectedIndex ?? 0;
    if (nextIndex === index) {
        // Removed the active one — bias toward the slot's new occupant
        // (the next image now sits at this index), else clamp back.
        nextIndex = Math.min(index, nextUrls.length - 1);
    } else if (nextIndex > index) {
        nextIndex -= 1;
    }
    if (nextUrls.length === 0) {
        // Empty history — surface back to "no T2I yet" so the legacy
        // empty-state UI still renders cleanly.
        return {
            ...migrated,
            t2iImageUrls: [],
            t2iSelectedIndex: 0,
            t2iImageUrl: undefined,
        };
    }
    return {
        ...migrated,
        t2iImageUrls: nextUrls,
        t2iSelectedIndex: Math.max(0, nextIndex),
        t2iImageUrl: nextUrls[Math.max(0, nextIndex)],
    };
}

/** Read the currently-active T2I image URL (or the legacy single
 *  field if the shot was never migrated). Convenience used by I2V
 *  to know "what's my first frame". */
export function getActiveT2IImageUrl(shot: ShotNode): string | undefined {
    if (shot.t2iImageUrls && shot.t2iImageUrls.length > 0) {
        const i = Math.max(0, Math.min(shot.t2iSelectedIndex ?? 0, shot.t2iImageUrls.length - 1));
        return shot.t2iImageUrls[i];
    }
    return shot.t2iImageUrl;
}

/** Append a freshly-created video task id to the per-tab bucket so
 *  the candidates panel can list it (cross-referenced against the
 *  script's video_tasks array). Idempotent on dup ids. */
export function appendVideoTaskId(
    shot: ShotNode,
    tabMode: "t2i_i2v" | "direct_r2v",
    taskId: string,
): ShotNode {
    if (!taskId) return shot;
    const migrated = migrateShotNode(shot);
    const buckets = { ...(migrated.videoTaskIdsByTab ?? {}) };
    const existing = buckets[tabMode] ?? [];
    if (existing.includes(taskId)) return migrated;
    buckets[tabMode] = [...existing, taskId];
    return {
        ...migrated,
        videoTaskIdsByTab: buckets,
        // Mirror to legacy single-id field so existing renderers
        // (and the spinner-state hooks) keep reading the latest task.
        videoTaskId: taskId,
    };
}

/** Convert a backend frame + the project's full video_tasks array into
 *  a ShotNode, including videoStatus / videoUrl / videoTaskId derived
 *  from in-flight or latest-completed tasks.
 *
 *  Single source of truth for frame→shot conversion. Init useState,
 *  handleRefineFrame, and any other "refresh from project state" path
 *  must route through here so the hero video stays consistent — earlier
 *  drift between init (had a latestCompleted fallback) and refine (only
 *  checked frame.video_url) caused hero to go blank after refine when
 *  the backend hadn't persisted frame.video_url yet.
 *
 *  `defaultTabMode` lets callers override the fallback ("direct_r2v")
 *  when they have more context (e.g. project-level i2v default). */
export function frameToShotNode(
    frame: FrameToShotInput,
    videoTasks: VideoTaskLike[],
    defaultTabMode: "t2i_i2v" | "direct_r2v" = "direct_r2v",
): ShotNode {
    const frameTasks = (videoTasks ?? []).filter((t: VideoTaskLike) => t.frame_id === frame.id);
    const inFlightTask = frameTasks.find((t: VideoTaskLike) =>
        t.status === "pending" || t.status === "processing"
    );
    const latestCompleted = frameTasks.find((t: VideoTaskLike) =>
        t.status === "completed" && t.video_url
    );
    const needsConfirmationTask = frameTasks.find((t: VideoTaskLike) =>
        t.status === "needs_confirmation"
    );

    let videoStatus: "pending" | "processing" | "completed" | "failed" | "needs_confirmation" | undefined;
    const selectedSource = selectFrameAssemblyVideo(frame, videoTasks ?? []);
    const selectedMismatch = selectedSource.kind === "none" && selectedSource.reason === "selected-mismatch";
    let videoUrl: string | undefined =
        selectedSource.kind === "preview" || selectedSource.kind === "applied" || selectedSource.kind === "original"
            ? selectedSource.url
            : selectedMismatch ? undefined : frame.video_url || undefined;
    const hasSelectedTaskVideo =
        selectedSource.kind === "preview" || selectedSource.kind === "applied" || selectedSource.kind === "original";
    let videoTaskId: string | undefined = hasSelectedTaskVideo ? selectedSource.taskId : undefined;

    if (inFlightTask) {
        videoStatus = inFlightTask.status === "pending" ? "pending" : "processing";
        videoTaskId = inFlightTask.id || undefined;
    } else if (!selectedMismatch && (hasSelectedTaskVideo || latestCompleted || (videoUrl && !needsConfirmationTask))) {
        videoStatus = "completed";
        videoUrl = videoUrl || latestCompleted?.video_url || undefined;
        videoTaskId = videoTaskId || latestCompleted?.id || undefined;
    } else if (needsConfirmationTask) {
        videoStatus = "needs_confirmation";
        videoTaskId = needsConfirmationTask.id || undefined;
        videoUrl = needsConfirmationTask.video_url || videoUrl;
    } else if (frameTasks.some((t: VideoTaskLike) => t.status === "failed")) {
        videoStatus = "failed";
    }

    const imageHistory = Array.isArray(frame.t2i_image_urls) ? frame.t2i_image_urls : [];
    const imageIndex = typeof frame.t2i_selected_index === 'number' ? frame.t2i_selected_index : 0;
    const selectedImage = imageHistory[Math.max(0, Math.min(imageIndex, imageHistory.length - 1))]
        || frame.rendered_image_url || frame.image_url || undefined;

    return migrateShotNode({
        id: frame.id,
        prompt: frame.visual_description ?? frame.action_description ?? "",
        tabMode: (frame.workbench_tab_mode as "t2i_i2v" | "direct_r2v" | undefined) ?? defaultTabMode,
        videoUrl,
        videoStatus,
        videoTaskId,
        imageUrl: frame.rendered_image_url || frame.image_url || undefined,
        lastFrameUrl: typeof frame.last_frame_url === "string" && frame.last_frame_url ? frame.last_frame_url : undefined,
        t2iImageUrl: selectedImage,
        t2iImageUrls: imageHistory,
        t2iSelectedIndex: imageIndex,
        duration: frame.duration ?? null,
        visualDescription: frame.visual_description ?? null,
        assembledPrompt: frame.assembled_prompt ?? null,
        dialogueStructured: frame.dialogue_structured ?? null,
        cameraMovementStructured: frame.camera_movement_structured ?? null,
        shotSize: frame.shot_size ?? null,
        cameraAngle: frame.camera_angle ?? null,
        transitionHint: frame.transition_hint ?? null,
        isVideoPinned: Boolean(frame.is_video_pinned),
        finalTakeId: frame.final_take_id ?? null,
    });
}

/** Per-tab task id list, with legacy single-id fallback. */
export function videoTaskIdsForTab(
    shot: ShotNode,
    tabMode: "t2i_i2v" | "direct_r2v",
): string[] {
    const direct = shot.videoTaskIdsByTab?.[tabMode];
    if (Array.isArray(direct)) return direct;
    // Legacy: only one task in shot.videoTaskId, assume it belonged
    // to the shot's current tabMode (which is the same tab as it was
    // generated under, since this snapshot is pre-multi-tab).
    if (shot.videoTaskId && shot.tabMode === tabMode) {
        return [shot.videoTaskId];
    }
    return [];
}
