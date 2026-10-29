import { AtelierProjectApiError, isAtelierRecord, requestAtelierJson, requireAtelierProjectIdInput, requireAtelierRevisionInput } from "@/lib/atelierApi";
import { parseAtelierNodeCommandAck } from "@/lib/atelierNodeApi";
import { ATELIER_REFERENCE_KINDS, canAttachAtelierReference, getAtelierCanvasNodeData, getAtelierPendingReferenceNodes, getAtelierReferenceMediaUrls, getAtelierReferenceNodeIds, resolveAtelierReferenceMedia } from "@/lib/atelierCanvas";
import type { AtelierNode, AtelierNodeCommandAck, AttachAtelierPendingReferenceInput, AttachAtelierReferenceInput, DetachAtelierReferenceInput } from "@/lib/atelierTypes";

const mismatch = () => new AtelierProjectApiError("Atelier reference acknowledgement does not match the saved relationship", "atelier_reference_ack_mismatch");
const requiredText = (value: string): string => {
    if (typeof value !== "string" || !value.trim()) throw new AtelierProjectApiError("Invalid Atelier reference input", "invalid_atelier_reference_input");
    return value;
};
const path = (projectId: string, targetId: string, action: "attach" | "detach" | "attach_pending") =>
    `/atelier/projects/${encodeURIComponent(requireAtelierProjectIdInput(projectId))}/nodes/${encodeURIComponent(requiredText(targetId))}/references/${action}`;
const targetOf = (ack: AtelierNodeCommandAck, targetId: string) => {
    if (ack.node_ids[0] !== targetId) throw mismatch();
    const target = ack.project.nodes.find(node => node.id === targetId)!;
    if (target.type !== "image" && target.type !== "video") throw mismatch();
    return target;
};
const pendingIdsOf = (target: AtelierNode): Set<string> =>
    new Set(getAtelierPendingReferenceNodes(target).map(reference => reference.source_node_id));
const urlsAccountedByResolvedSources = (ack: AtelierNodeCommandAck, target: AtelierNode): boolean => {
    const pendingIds = pendingIdsOf(target);
    const ids = getAtelierReferenceNodeIds(target);
    const resolvedUrls = new Set<string>();
    for (const id of ids) {
        if (pendingIds.has(id)) continue;
        const source = ack.project.nodes.find(node => node.id === id);
        if (source) getAtelierReferenceMediaUrls(source).forEach(url => resolvedUrls.add(url));
    }
    return ATELIER_REFERENCE_KINDS.every(kind =>
        (getAtelierCanvasNodeData(target)[`reference_${kind}_urls`] ?? []).every(url => resolvedUrls.has(url)));
};

export const atelierReferenceApi = {
    async attachReference(projectId: string, targetId: string, input: AttachAtelierReferenceInput): Promise<AtelierNodeCommandAck> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        const sourceId = requiredText(input.sourceNodeId);
        const candidateId = input.sourceCandidateId === undefined ? undefined : requiredText(input.sourceCandidateId);
        const raw = await requestAtelierJson(path(projectId, targetId, "attach"), { method: "POST", body: JSON.stringify({
            expected_revision: expectedRevision, source_node_id: sourceId,
            ...(candidateId === undefined ? {} : { source_candidate_id: candidateId }),
        }) });
        const ack = parseAtelierNodeCommandAck(raw, { projectId, expectedRevision });
        const target = targetOf(ack, targetId);
        if (ack.node_ids.length !== 2) throw mismatch();
        const source = ack.project.nodes.find(node => node.id === ack.node_ids[1])!;
        if (!canAttachAtelierReference(target, source)) throw mismatch();
        if (candidateId === undefined) {
            if (source.id !== sourceId) throw mismatch();
        } else {
            const origin = source.data.reference_origin;
            const parent = ack.project.nodes.find(node => node.id === sourceId);
            if (!isAtelierRecord(origin) || origin.node_id !== sourceId || origin.candidate_id !== candidateId
                || !parent || parent.type !== source.type || source.id === sourceId || source.media_urls.length !== 1) throw mismatch();
        }
        const url = resolveAtelierReferenceMedia(source);
        const urls = target.data[`reference_${source.type}_urls`];
        if (!url || !getAtelierReferenceNodeIds(target).includes(source.id) || !Array.isArray(urls) || !urls.includes(url)) throw mismatch();
        return ack;
    },

    async attachPendingReference(projectId: string, targetId: string, input: AttachAtelierPendingReferenceInput): Promise<AtelierNodeCommandAck> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        const sourceId = requiredText(input.sourceNodeId);
        const raw = await requestAtelierJson(path(projectId, targetId, "attach_pending"), { method: "POST", body: JSON.stringify({
            expected_revision: expectedRevision, source_node_id: sourceId,
        }) });
        const ack = parseAtelierNodeCommandAck(raw, { projectId, expectedRevision });
        const target = targetOf(ack, targetId);
        if (ack.node_ids.length !== 2 || ack.node_ids[1] !== sourceId) throw mismatch();
        const source = ack.project.nodes.find(node => node.id === sourceId)!;
        if (!canAttachAtelierReference(target, source) || getAtelierReferenceMediaUrls(source).length > 0) throw mismatch();
        const pending = getAtelierPendingReferenceNodes(target).find(reference => reference.source_node_id === sourceId);
        if (!pending || pending.source_kind !== source.type || pending.status !== "pending_media" || pending.schema_version !== 1) throw mismatch();
        if (!getAtelierReferenceNodeIds(target).includes(sourceId) || !urlsAccountedByResolvedSources(ack, target)) throw mismatch();
        return ack;
    },

    async detachReference(projectId: string, targetId: string, input: DetachAtelierReferenceInput): Promise<AtelierNodeCommandAck> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        const referenceUrl = input.referenceUrl === undefined ? undefined : requiredText(input.referenceUrl);
        const sourceId = input.sourceNodeId === undefined ? undefined : requiredText(input.sourceNodeId);
        if (sourceId === undefined && referenceUrl === undefined) throw new AtelierProjectApiError("A source node id or reference URL is required", "invalid_atelier_reference_input");
        const raw = await requestAtelierJson(path(projectId, targetId, "detach"), { method: "POST", body: JSON.stringify({
            expected_revision: expectedRevision,
            ...(referenceUrl === undefined ? {} : { reference_url: referenceUrl }),
            ...(sourceId === undefined ? {} : { source_node_id: sourceId }),
        }) });
        const ack = parseAtelierNodeCommandAck(raw, { projectId, expectedRevision });
        const target = targetOf(ack, targetId);
        if (sourceId === undefined && referenceUrl !== undefined) {
            const data = getAtelierCanvasNodeData(target);
            if (ATELIER_REFERENCE_KINDS.some(kind => data[`reference_${kind}_urls`]?.includes(referenceUrl))) throw mismatch();
        }
        if (sourceId !== undefined && (ack.node_ids.length > 2 || (ack.node_ids.length === 2 && ack.node_ids[1] !== sourceId))) throw mismatch();
        const removed = sourceId === undefined ? ack.node_ids.slice(1) : [sourceId];
        for (const id of removed) {
            const source = ack.project.nodes.find(node => node.id === id);
            if (!source || !canAttachAtelierReference(target, source) || getAtelierReferenceNodeIds(target).includes(id)
                || pendingIdsOf(target).has(id)
                || source.data.parent_node_id === targetId
                || (sourceId === undefined && (referenceUrl === undefined || !getAtelierReferenceMediaUrls(source).includes(referenceUrl)))) throw mismatch();
        }
        return ack;
    },
};
