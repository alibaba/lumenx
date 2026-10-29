import { AtelierProjectApiError, hasOwnAtelierField, isAtelierRecord, requestAtelierJson, requireAtelierProjectIdInput, requireAtelierRevisionInput } from "@/lib/atelierApi";
import { parseAtelierNodeCommandAck } from "@/lib/atelierNodeApi";
import type { AtelierNode, AtelierNodeCommandAck, AtelierProject, JsonRecord } from "@/lib/atelierTypes";

export type BranchAtelierCandidateInput = { expected_revision: number };

const fail = (): never => {
    throw new AtelierProjectApiError("Invalid Atelier candidate branch request", "invalid_atelier_candidate_branch_request");
};

const text = (value: string, code: string): string => {
    if (typeof value !== "string" || value.length === 0) {
        throw new AtelierProjectApiError("Invalid Atelier candidate branch identity", code);
    }
    return value;
};

const projectPath = (projectId: string): string => `/atelier/projects/${encodeURIComponent(requireAtelierProjectIdInput(projectId))}`;
const nodePath = (projectId: string, nodeId: string): string => `${projectPath(projectId)}/nodes/${encodeURIComponent(text(nodeId, "invalid_atelier_node_id"))}`;
const canonical = (value: unknown): string => JSON.stringify(value, (_key, item) => isAtelierRecord(item) ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);

const candidateEntries = (node: AtelierNode, field: "candidates" | "deleted_candidates"): JsonRecord[] => {
    const values = node.data[field];
    if (values === undefined) return [];
    if (!Array.isArray(values)) return fail();
    return values.filter((item): item is JsonRecord => isAtelierRecord(item) && typeof item.id === "string");
};

const mediaKindFromCandidate = (node: AtelierNode, candidate: JsonRecord): "image" | "video" | "audio" => {
    if (candidate.execution_contract === "atelier-provider-media-v2" && (candidate.media_kind === "image" || candidate.media_kind === "audio")) return candidate.media_kind;
    if (candidate.media_kind === "video" || candidate.media_kind === undefined) return "video";
    if (node.type === "image" || node.type === "audio") return node.type;
    return fail();
};

const findSource = (project: AtelierProject, nodeId: string, candidateId: string): { node: AtelierNode; candidate: JsonRecord; mediaKind: "image" | "video" | "audio" } => {
    const node = project.nodes.find(item => item.id === nodeId);
    if (!node) return fail();
    const live = candidateEntries(node, "candidates").filter(item => item.id === candidateId);
    if (live.length !== 1 || candidateEntries(node, "deleted_candidates").some(item => item.id === candidateId)) return fail();
    const candidate = live[0];
    if (candidate.status !== "completed") return fail();
    const mediaKind = mediaKindFromCandidate(node, candidate);
    return { node, candidate, mediaKind };
};

const branchSource = (node: AtelierNode): JsonRecord => {
    const value = node.data.branch_source;
    if (!isAtelierRecord(value)) return fail();
    return value;
};

const projectWithoutVersionAndNodes = (project: AtelierProject): Record<string, unknown> => {
    const rest = { ...project } as Record<string, unknown>;
    delete rest.revision;
    delete rest.updated_at;
    delete rest.nodes;
    return rest;
};

const assertOnlyAckNodesAdded = (before: AtelierProject, after: AtelierProject, nodeIds: string[]): void => {
    if (canonical(projectWithoutVersionAndNodes(before)) !== canonical(projectWithoutVersionAndNodes(after))) fail();
    const beforeIds = new Set(before.nodes.map(node => node.id));
    const ackIds = new Set(nodeIds);
    if (ackIds.size !== nodeIds.length || after.nodes.length !== before.nodes.length + nodeIds.length) fail();
    for (const id of nodeIds) if (beforeIds.has(id)) fail();
    for (const beforeNode of before.nodes) {
        const afterNode = after.nodes.find(node => node.id === beforeNode.id);
        if (!afterNode || canonical(afterNode) !== canonical(beforeNode)) fail();
    }
    for (const afterNode of after.nodes) {
        if (!beforeIds.has(afterNode.id) && !ackIds.has(afterNode.id)) fail();
    }
};

const assertBranchAck = (
    ack: AtelierNodeCommandAck,
    before: AtelierProject,
    nodeId: string,
    candidateId: string,
    expectedRevision: number,
): AtelierNodeCommandAck => {
    if (ack.project.revision !== expectedRevision + 1 || ack.node_ids.length < 1 || ack.node_ids.length > 21) fail();
    const { mediaKind } = findSource(before, nodeId, candidateId);
    assertOnlyAckNodesAdded(before, ack.project, ack.node_ids);
    const draft = ack.project.nodes.find(item => item.id === ack.node_ids[0]) ?? fail();
    if (draft.project_id !== ack.project.id || draft.type !== mediaKind || draft.status !== "draft" || draft.media_urls.length !== 0) fail();
    const data = draft.data;
    if (Array.isArray(data.candidates) || Array.isArray(data.deleted_candidates) || typeof data.selected_candidate_id === "string") fail();
    const source = branchSource(draft);
    if (source.origin_project_id !== before.id || source.origin_node_id !== nodeId || source.origin_candidate_id !== candidateId
        || source.media_kind !== mediaKind) fail();
    if (source.recipe_status === "available" ? !hasOwnAtelierField(data, "generation_draft") : hasOwnAtelierField(data, "generation_draft")) fail();
    for (const referenceId of ack.node_ids.slice(1)) {
        const reference = ack.project.nodes.find(item => item.id === referenceId) ?? fail();
        if (reference.status !== "completed" || reference.media_urls.length === 0 || Array.isArray(reference.data.candidates)
            || Array.isArray(reference.data.deleted_candidates) || hasOwnAtelierField(reference.data, "branch_source")) fail();
    }
    return ack;
};

export const atelierCandidateBranchApi = {
    async branchCandidate(
        projectId: string,
        nodeId: string,
        candidateId: string,
        input: BranchAtelierCandidateInput,
        beforeProject: AtelierProject,
    ): Promise<AtelierNodeCommandAck> {
        if (!hasOwnAtelierField(input, "expected_revision") || Object.keys(input).length !== 1) fail();
        if (beforeProject.id !== projectId || beforeProject.revision !== input.expected_revision) fail();
        const expectedRevision = requireAtelierRevisionInput(input.expected_revision);
        findSource(beforeProject, nodeId, candidateId);
        const ack = parseAtelierNodeCommandAck(await requestAtelierJson(
            `${nodePath(projectId, nodeId)}/candidates/${encodeURIComponent(text(candidateId, "invalid_atelier_candidate_id"))}/branch`,
            { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) },
        ), { projectId, expectedRevision });
        return assertBranchAck(ack, beforeProject, nodeId, candidateId, expectedRevision);
    },
};
