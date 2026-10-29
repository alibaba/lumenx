import type { AtelierMediaKind, AtelierNode, AtelierPendingReference } from "@/lib/atelierTypes";

export type AtelierCanvasNodeData = {
    reference_image_urls?: string[];
    reference_video_urls?: string[];
    reference_audio_urls?: string[];
    reference_node_ids?: string[];
    pending_reference_nodes?: AtelierPendingReference[];
    parent_node_id?: string | null;
};

export type AtelierCanvasPoint = {
    x: number;
    y: number;
};

export type AtelierCanvasSize = {
    width: number;
    height: number;
};

export type AtelierCanvasPlacementOptions = {
    startX?: number;
    startY?: number;
    gapX?: number;
    gapY?: number;
    columns?: number;
};

export type AtelierReferenceLink = {
    key: string;
    from: AtelierNode;
    to: AtelierNode;
    url: string;
    stale?: boolean;
};

export type AtelierCanvasPlacementRect = AtelierCanvasPoint & AtelierCanvasSize;

export const ATELIER_MEDIA_RENDERED_BOUNDS = {
    // Image height is a conservative placement envelope for completed
    // image cards with metadata/action chrome; it is not an exact visual
    // height for every natural image aspect ratio.
    image: { width: 260, height: 433 },
    video: { width: 260, height: 146 },
    audio: { width: 260, height: 80 },
} as const;

const isRecord = (value: unknown): value is Record<string, unknown> => (
    typeof value === "object" && value !== null && !Array.isArray(value)
);

const strings = (value: unknown): string[] => Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];

const records = (value: unknown): Record<string, unknown>[] => Array.isArray(value)
    ? value.filter(isRecord) : [];

const hasOwn = (value: object, key: string): boolean => Object.prototype.hasOwnProperty.call(value, key);

const stringField = (record: Record<string, unknown>, field: string): string | null => {
    const value = record[field];
    return typeof value === "string" && value.length > 0 ? value : null;
};


const ACTIVE_EXECUTION_STATUSES = new Set(["pending", "processing", "running", "submitting", "submitted", "observed", "accepted", "needs_confirmation", "unknown"]);
const TERMINAL_EXECUTION_STATUSES = new Set(["completed", "failed", "canceled", "cancelled", "deleted"]);
const EXECUTION_EVIDENCE_KEYS = new Set([
    "attempt_token", "attempt_count", "attempts", "runtime_snapshot", "generation_snapshot", "request_id", "task_id",
    "provider_task_id", "provider_request_id", "operation_id", "owner_submission_started", "storage_error", "retained_media_url",
    "retained_video_url", "tracking_stopped", "submitted_at", "attempt_started_at",
]);

const activeStatus = (value: unknown): boolean => typeof value === "string" && ACTIVE_EXECUTION_STATUSES.has(value.toLowerCase());
const terminalStatus = (value: unknown): boolean => typeof value === "string" && TERMINAL_EXECUTION_STATUSES.has(value.toLowerCase());
const hasExecutionEvidence = (value: Record<string, unknown>): boolean => Array.from(EXECUTION_EVIDENCE_KEYS).some(key => value[key] !== undefined && value[key] !== null);
const nestedActive = (value: Record<string, unknown>): boolean => Object.entries(value).some(([key, item]) => {
    if (key === "runtime_snapshot" || key === "generation_snapshot" || key === "params" || key === "payload") return false;
    if (isRecord(item)) return recordActive(item);
    if (Array.isArray(item)) return item.some(child => isRecord(child) && recordActive(child));
    return false;
});
const recordActive = (value: Record<string, unknown>): boolean => {
    const status = value.status;
    if (activeStatus(status)) return true;
    if (terminalStatus(status)) return Boolean(value.storage_error || value.tracking_stopped || nestedActive(value));
    return hasExecutionEvidence(value) || nestedActive(value);
};

const completedCandidateUrl = (candidate: Record<string, unknown>): string | null => {
    if (candidate.status !== "completed") return null;
    const url = candidate.media_url || candidate.video_url;
    return typeof url === "string" && url.trim().length > 0 ? url : null;
};

const completedCandidateUrls = (candidate: Record<string, unknown>): string[] => {
    if (candidate.status !== "completed") return [];
    return strings([stringField(candidate, "media_url"), stringField(candidate, "video_url")]);
};

export const ATELIER_REFERENCE_KINDS = ["image", "video", "audio"] as const;
const ATELIER_REFERENCE_KIND_SET = new Set<string>(ATELIER_REFERENCE_KINDS);

function pendingReference(record: Record<string, unknown>): AtelierPendingReference | null {
    if (record.schema_version !== 1 || record.status !== "pending_media") return null;
    const sourceNodeId = stringField(record, "source_node_id");
    const sourceKind = stringField(record, "source_kind");
    if (!sourceNodeId || !sourceKind || !ATELIER_REFERENCE_KIND_SET.has(sourceKind)) return null;
    return {
        schema_version: 1,
        source_node_id: sourceNodeId,
        source_kind: sourceKind as AtelierMediaKind,
        status: "pending_media",
    };
}

export function getAtelierPendingReferenceNodes(node: AtelierNode): AtelierPendingReference[] {
    const data = isRecord(node.data) ? node.data : {};
    return records(data.pending_reference_nodes).map(pendingReference).filter((item): item is AtelierPendingReference => item !== null);
}

export function getAtelierCanvasNodeData(node: AtelierNode): AtelierCanvasNodeData {
    const data = isRecord(node.data) ? node.data : {};
    return {
        reference_image_urls: strings(data.reference_image_urls),
        reference_video_urls: strings(data.reference_video_urls),
        reference_audio_urls: strings(data.reference_audio_urls),
        reference_node_ids: strings(data.reference_node_ids),
        pending_reference_nodes: getAtelierPendingReferenceNodes(node),
        parent_node_id: typeof data.parent_node_id === "string" ? data.parent_node_id : null,
    };
}

export function getAtelierReferenceNodeIds(node: AtelierNode): string[] {
    return getAtelierCanvasNodeData(node).reference_node_ids ?? [];
}

export function canAttachAtelierReference(target: AtelierNode, source: AtelierNode): boolean {
    return target.id !== source.id && target.project_id === source.project_id &&
        (target.type === "video" ? ATELIER_REFERENCE_KINDS.some(kind => kind === source.type)
            : target.type === "image" && source.type === "image");
}

/** Matches Core: video candidates take precedence over stale selected media. */
export function resolveAtelierReferenceMedia(node: AtelierNode): string | null {
    if (!ATELIER_REFERENCE_KINDS.some(kind => kind === node.type)) return null;
    const rawCandidates = node.data?.candidates;
    const candidates = records(rawCandidates);
    if (node.type === "video" && Array.isArray(rawCandidates) && rawCandidates.length) {
        const completed = candidates.filter(candidate => candidate.status === "completed");
        const selectedCandidateId = typeof node.data?.selected_candidate_id === "string" ? node.data.selected_candidate_id : null;
        const picked = completed.find(candidate => candidate.id === selectedCandidateId) ?? completed[0];
        return picked ? completedCandidateUrl(picked) : null;
    }
    return strings(node.media_urls)[0] ?? null;
}

/** All known source URLs, including older completed takes, for unlink/migration. */
export function getAtelierReferenceMediaUrls(node: AtelierNode): string[] {
    const candidates = [
        ...records(node.data?.candidates),
        ...records(node.data?.deleted_candidates),
    ];
    return Array.from(new Set([
        ...strings(node.media_urls),
        ...candidates.flatMap(completedCandidateUrls),
    ]));
}

export function hasAtelierNodeActiveExecution(node: AtelierNode): boolean {
    if (activeStatus(node.status) || (node.video_task_id && !terminalStatus(node.status))) return true;
    const data = isRecord(node.data) ? node.data : {};
    for (const candidate of [...records(data.candidates), ...records(data.deleted_candidates)]) {
        if (recordActive(candidate)) return true;
    }
    return isRecord(data.generation) && recordActive(data.generation);
}

/** Reference-only data; never send prompt, params or worker-owned fields in a PUT. */
export function canonicalAtelierReferenceData(target: AtelierNode, nodes: AtelierNode[]): Record<string, string[] | AtelierPendingReference[]> {
    const rawTargetData = isRecord(target.data) ? target.data : {};
    const data: Record<string, string[] | AtelierPendingReference[]> = {};
    for (const kind of ATELIER_REFERENCE_KINDS) {
        const field = `reference_${kind}_urls` as keyof AtelierCanvasNodeData;
        const rawField = `reference_${kind}_urls`;
        if (hasOwn(rawTargetData, rawField)) {
            data[field] = Array.from(new Set(strings(rawTargetData[rawField])));
        }
    }
    const pending = getAtelierPendingReferenceNodes(target).filter(reference => {
        const source = nodes.find(node => node.id === reference.source_node_id);
        return source && source.type === reference.source_kind && canAttachAtelierReference(target, source);
    });
    const sources = nodes.filter(source => canAttachAtelierReference(target, source) &&
        (getAtelierReferenceNodeIds(target).includes(source.id) || getAtelierCanvasNodeData(source).parent_node_id === target.id));
    data.reference_node_ids = Array.from(new Set([
        ...getAtelierReferenceNodeIds(target).filter(id => sources.some(source => source.id === id)),
        ...sources.map(source => source.id),
    ]));
    if (pending.length) {
        const pendingRecords: AtelierPendingReference[] = pending.map(reference => ({
            schema_version: 1,
            source_node_id: reference.source_node_id,
            source_kind: reference.source_kind,
            status: "pending_media",
        }));
        data.pending_reference_nodes = pendingRecords;
    }
    // Only node identity/media evidence can reclassify old image-bucket entries.
    // Unknown URLs stay untouched, even if their filename looks like a video.
    for (const source of sources) {
        if (source.type === "image") continue;
        const known = new Set(getAtelierReferenceMediaUrls(source));
        const imageUrls = (data.reference_image_urls as string[] | undefined) ?? [];
        const misplaced = imageUrls.filter(url => known.has(url));
        if (!misplaced.length) continue;
        data.reference_image_urls = imageUrls.filter(url => !known.has(url));
        const field = `reference_${source.type}_urls`;
        data[field] = Array.from(new Set([...((data[field] as string[] | undefined) ?? []), ...misplaced]));
    }
    return data;
}

export function isPointInsideAtelierNode(point: AtelierCanvasPoint, node: AtelierNode): boolean {
    return point.x >= node.x &&
        point.x <= node.x + node.width &&
        point.y >= node.y &&
        point.y <= node.y + node.height;
}

export function getAtelierCanvasPlacementRect(node: AtelierNode): AtelierCanvasPlacementRect {
    const media = node.type === "image" || node.type === "video" || node.type === "audio"
        ? ATELIER_MEDIA_RENDERED_BOUNDS[node.type]
        : null;
    return {
        x: node.x,
        y: node.y,
        width: Math.max(node.width, media?.width ?? 0),
        height: Math.max(node.height, media?.height ?? 0),
    };
}

export function getAtelierCanvasPlacementRects(nodes: AtelierNode[]): AtelierCanvasPlacementRect[] {
    return nodes.map(getAtelierCanvasPlacementRect);
}

function overlaps(a: { x: number; y: number; width: number; height: number }, b: { x: number; y: number; width: number; height: number }): boolean {
    return a.x < b.x + b.width &&
        a.x + a.width > b.x &&
        a.y < b.y + b.height &&
        a.y + a.height > b.y;
}

export function findOpenAtelierCanvasPosition(
    nodes: Pick<AtelierNode, "x" | "y" | "width" | "height">[],
    size: AtelierCanvasSize,
    options: AtelierCanvasPlacementOptions = {},
): AtelierCanvasPoint {
    const startX = options.startX ?? 80;
    const startY = options.startY ?? 96;
    const gapX = options.gapX ?? 48;
    const gapY = options.gapY ?? 40;
    const columns = Math.max(1, Math.floor(options.columns ?? 3));
    const safeSize = {
        width: Math.max(1, size.width),
        height: Math.max(1, size.height),
    };
    const occupied = nodes.filter(node => (
        Number.isFinite(node.x) &&
        Number.isFinite(node.y) &&
        Number.isFinite(node.width) &&
        Number.isFinite(node.height) &&
        node.width > 0 &&
        node.height > 0
    ));
    const maxAttempts = Math.max(24, (occupied.length + 1) * columns * 2);
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
        const column = attempt % columns;
        const row = Math.floor(attempt / columns);
        const candidate = {
            x: startX + column * (safeSize.width + gapX),
            y: startY + row * (safeSize.height + gapY),
            ...safeSize,
        };
        if (!occupied.some(node => overlaps(candidate, node))) return { x: candidate.x, y: candidate.y };
    }
    return {
        x: startX,
        y: Math.max(startY, ...occupied.map(node => node.y + node.height + gapY)),
    };
}

export function findVideoDropTarget(nodes: AtelierNode[], point: AtelierCanvasPoint): AtelierNode | null {
    return [...nodes].reverse().find((node) => node.type === "video" && isPointInsideAtelierNode(point, node)) ?? null;
}

export function buildReferenceLinks(nodes: AtelierNode[]): AtelierReferenceLink[] {
    const links = new Map<string, AtelierReferenceLink>();
    const byId = new Map(nodes.map(node => [node.id, node]));
    const addLink = (source: AtelierNode, target: AtelierNode) => {
        if (!canAttachAtelierReference(target, source)) return;
        const currentUrl = resolveAtelierReferenceMedia(source);
        if (!currentUrl) return;
        const knownUrls = new Set(getAtelierReferenceMediaUrls(source));
        const targetData = getAtelierCanvasNodeData(target);
        const boundUrls = ATELIER_REFERENCE_KINDS.flatMap(kind => strings(targetData[`reference_${kind}_urls` as keyof AtelierCanvasNodeData]));
        const url = boundUrls.find(value => knownUrls.has(value)) ?? currentUrl;
        const stale = boundUrls.length > 0 && !boundUrls.includes(currentUrl);
        const key = `${source.id}-${target.id}`;
        links.set(key, { key, from: source, to: target, url, ...(stale ? { stale: true } : {}) });
    };

    nodes.forEach((node) => {
        const parentNodeId = getAtelierCanvasNodeData(node).parent_node_id;
        const parent = parentNodeId ? byId.get(parentNodeId) : undefined;
        if (parent) addLink(node, parent);
    });

    nodes.forEach((node) => {
        getAtelierReferenceNodeIds(node).forEach((referenceNodeId) => {
            const source = byId.get(referenceNodeId);
            if (source) addLink(source, node);
        });
    });

    return Array.from(links.values());
}
