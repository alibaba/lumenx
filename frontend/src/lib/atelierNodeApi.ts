import {
    assertAtelierProjectIdentity,
    assertAtelierProjectRevisionNotBefore,
    AtelierProjectApiError,
    hasOwnAtelierField,
    isAtelierRecord,
    parseAtelierProject,
    requestAtelierJson,
    requireAtelierProjectIdInput,
    requireAtelierRevisionInput,
} from "@/lib/atelierApi";
import type {
    AtelierNode,
    AtelierNodeCommandAck,
    CreateAtelierNodeInput,
    MoveAtelierNodesInput,
    SetAtelierRegionMembersInput,
    UpdateAtelierNodeBodyInput,
    UpdateAtelierNodeLayoutInput,
    UpdateAtelierNodesLayoutInput,
    UpdateAtelierNodeTextInput,
    UpdateAtelierRegionAppearanceInput,
    WrapAtelierRegionInput,
} from "@/lib/atelierTypes";

type AckExpectation = {
    expectedIds?: string[];
    expectedRevision: number;
    projectId: string;
    requireRegion?: boolean;
    createType?: string;
};

type LayoutLikeInput = {
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    regionId?: string | null;
};

const ATELIER_PROJECTS_PATH = "/atelier/projects";

const body = (value: Record<string, unknown>): string => JSON.stringify(value);

const requireNodeIdInput = (nodeId: string, code = "invalid_atelier_node_id"): string => {
    if (typeof nodeId !== "string" || nodeId.length === 0) {
        throw new AtelierProjectApiError("Invalid Atelier node id", code);
    }
    return nodeId;
};

const requireRegionIdInput = (regionId: string): string => requireNodeIdInput(regionId, "invalid_atelier_region_id");

const requireNodeIdsInput = (nodeIds: string[], options: { allowEmpty: boolean } = { allowEmpty: false }): string[] => {
    if (!Array.isArray(nodeIds) || (!options.allowEmpty && nodeIds.length === 0)) {
        throw new AtelierProjectApiError("Invalid Atelier node ids", "invalid_atelier_node_ids");
    }
    const seen = new Set<string>();
    for (const nodeId of nodeIds) {
        if (typeof nodeId !== "string" || nodeId.length === 0 || seen.has(nodeId)) {
            throw new AtelierProjectApiError("Invalid Atelier node ids", "invalid_atelier_node_ids");
        }
        seen.add(nodeId);
    }
    return [...nodeIds];
};

const requireLayoutUpdates = (updates: UpdateAtelierNodesLayoutInput["updates"]): UpdateAtelierNodesLayoutInput["updates"] => {
    if (!Array.isArray(updates) || updates.length === 0) {
        throw new AtelierProjectApiError("Invalid Atelier node ids", "invalid_atelier_node_ids");
    }
    requireNodeIdsInput(updates.map(update => update.nodeId));
    return updates;
};

const requireCreateNodeFields = (input: CreateAtelierNodeInput): void => {
    const requestedType = input.type ?? "idea";
    const hasRegionOnlyField = (hasOwnAtelierField(input, "color") && input.color !== undefined)
        || (hasOwnAtelierField(input, "collapsed") && input.collapsed !== undefined);
    if (requestedType !== "region" && hasRegionOnlyField) {
        throw new AtelierProjectApiError("Region appearance fields are only valid when creating region nodes", "invalid_atelier_node_create_fields");
    }
};


const projectPath = (projectId: string): string => `${ATELIER_PROJECTS_PATH}/${encodeURIComponent(requireAtelierProjectIdInput(projectId))}`;
const nodePath = (projectId: string, nodeId: string): string => `${projectPath(projectId)}/nodes/${encodeURIComponent(requireNodeIdInput(nodeId))}`;
const regionPath = (projectId: string, regionId: string): string => `${projectPath(projectId)}/regions/${encodeURIComponent(requireRegionIdInput(regionId))}`;

const appendLayoutFields = (payload: Record<string, unknown>, input: LayoutLikeInput): void => {
    if (input.x !== undefined) payload.x = input.x;
    if (input.y !== undefined) payload.y = input.y;
    if (input.width !== undefined) payload.width = input.width;
    if (input.height !== undefined) payload.height = input.height;
    if (hasOwnAtelierField(input, "regionId") && input.regionId !== undefined) payload.region_id = input.regionId;
};

const parseAckNodeIds = (value: unknown): string[] => {
    if (!Array.isArray(value)) {
        throw new AtelierProjectApiError("Invalid Atelier node command acknowledgement", "invalid_atelier_node_ack");
    }
    if (value.length === 0) {
        throw new AtelierProjectApiError("Atelier node command acknowledgement was empty", "atelier_node_ack_empty");
    }
    const nodeIds: string[] = [];
    const seen = new Set<string>();
    for (const item of value) {
        if (typeof item !== "string" || item.length === 0) {
            throw new AtelierProjectApiError("Invalid Atelier node command acknowledgement", "invalid_atelier_node_ack");
        }
        if (seen.has(item)) {
            throw new AtelierProjectApiError("Atelier node command acknowledgement contained duplicate node ids", "atelier_node_ack_duplicate");
        }
        seen.add(item);
        nodeIds.push(item);
    }
    return nodeIds;
};

const findNode = (nodes: AtelierNode[], nodeId: string): AtelierNode | undefined => nodes.find(node => node.id === nodeId);

const assertExactAckIds = (actual: string[], expected: string[]): void => {
    if (actual.length !== expected.length || actual.some((nodeId, index) => nodeId !== expected[index])) {
        throw new AtelierProjectApiError("Atelier node command acknowledgement did not match requested target", "atelier_node_ack_target_mismatch");
    }
};

const parseNodeCommandAck = (value: unknown, expectation: AckExpectation): AtelierNodeCommandAck => {
    if (!isAtelierRecord(value) || !hasOwnAtelierField(value, "project")) {
        throw new AtelierProjectApiError("Invalid Atelier node command acknowledgement", "invalid_atelier_node_ack");
    }
    const project = assertAtelierProjectRevisionNotBefore(
        assertAtelierProjectIdentity(parseAtelierProject(value.project), expectation.projectId),
        expectation.expectedRevision,
    );
    const nodeIds = parseAckNodeIds(value.node_ids);
    for (const nodeId of nodeIds) {
        if (!findNode(project.nodes, nodeId)) {
            throw new AtelierProjectApiError("Atelier node command acknowledgement referenced a missing target", "atelier_node_ack_missing_target");
        }
    }
    if (expectation.expectedIds) {
        assertExactAckIds(nodeIds, expectation.expectedIds);
    }
    if (expectation.requireRegion) {
        if (nodeIds.length !== 1 || findNode(project.nodes, nodeIds[0])?.type !== "region") {
            throw new AtelierProjectApiError("Atelier node command acknowledgement did not match requested target", "atelier_node_ack_target_mismatch");
        }
    }
    if (expectation.createType !== undefined) {
        if (nodeIds.length !== 1 || findNode(project.nodes, nodeIds[0])?.type !== expectation.createType) {
            throw new AtelierProjectApiError("Atelier node command acknowledgement did not match requested target", "atelier_node_ack_target_mismatch");
        }
    }
    return { project, node_ids: nodeIds };
};

export { parseNodeCommandAck as parseAtelierNodeCommandAck };

const requestNodeAck = async (path: string, init: RequestInit, expectation: AckExpectation): Promise<AtelierNodeCommandAck> => {
    return parseNodeCommandAck(await requestAtelierJson(path, init), expectation);
};

export const atelierNodeApi = {
    async createNode(projectId: string, input: CreateAtelierNodeInput): Promise<AtelierNodeCommandAck> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        requireCreateNodeFields(input);
        const payload: Record<string, unknown> = { expected_revision: expectedRevision };
        if (input.type !== undefined) payload.type = input.type;
        if (input.title !== undefined) payload.title = input.title;
        if (input.prompt !== undefined) payload.prompt = input.prompt;
        if (hasOwnAtelierField(input, "body") && input.body !== undefined) payload.body = input.body;
        appendLayoutFields(payload, input);
        if (input.color !== undefined) payload.color = input.color;
        if (input.collapsed !== undefined) payload.collapsed = input.collapsed;
        return requestNodeAck(`${projectPath(projectId)}/nodes`, {
            method: "POST",
            body: body(payload),
        }, {
            createType: input.type ?? "idea",
            expectedRevision,
            projectId,
        });
    },

    async updateNodeText(projectId: string, nodeId: string, input: UpdateAtelierNodeTextInput): Promise<AtelierNodeCommandAck> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        const payload: Record<string, unknown> = { expected_revision: expectedRevision };
        if (input.title !== undefined) payload.title = input.title;
        if (input.prompt !== undefined) payload.prompt = input.prompt;
        return requestNodeAck(`${nodePath(projectId, nodeId)}/text`, {
            method: "PUT",
            body: body(payload),
        }, {
            expectedIds: [nodeId],
            expectedRevision,
            projectId,
        });
    },

    async updateNodeBody(projectId: string, nodeId: string, input: UpdateAtelierNodeBodyInput): Promise<AtelierNodeCommandAck> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        return requestNodeAck(`${nodePath(projectId, nodeId)}/body`, {
            method: "PUT",
            body: body({ expected_revision: expectedRevision, body: input.body }),
        }, {
            expectedIds: [nodeId],
            expectedRevision,
            projectId,
        });
    },

    async updateNodeLayout(projectId: string, nodeId: string, input: UpdateAtelierNodeLayoutInput): Promise<AtelierNodeCommandAck> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        const payload: Record<string, unknown> = { expected_revision: expectedRevision };
        appendLayoutFields(payload, input);
        return requestNodeAck(`${nodePath(projectId, nodeId)}/layout`, {
            method: "PUT",
            body: body(payload),
        }, {
            expectedIds: [nodeId],
            expectedRevision,
            projectId,
        });
    },

    async updateNodesLayout(projectId: string, input: UpdateAtelierNodesLayoutInput): Promise<AtelierNodeCommandAck> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        const updates = requireLayoutUpdates(input.updates);
        const requestedIds = updates.map(update => update.nodeId);
        const payloadUpdates = updates.map(update => {
            const payload: Record<string, unknown> = { node_id: requireNodeIdInput(update.nodeId) };
            appendLayoutFields(payload, update);
            return payload;
        });
        return requestNodeAck(`${projectPath(projectId)}/nodes/layout`, {
            method: "PUT",
            body: body({ expected_revision: expectedRevision, updates: payloadUpdates }),
        }, {
            expectedIds: requestedIds,
            expectedRevision,
            projectId,
        });
    },

    async moveNodes(projectId: string, input: MoveAtelierNodesInput): Promise<AtelierNodeCommandAck> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        const nodeIds = requireNodeIdsInput(input.nodeIds);
        const payload: Record<string, unknown> = { expected_revision: expectedRevision, node_ids: nodeIds };
        if (input.dx !== undefined) payload.dx = input.dx;
        if (input.dy !== undefined) payload.dy = input.dy;
        return requestNodeAck(`${projectPath(projectId)}/nodes/move`, {
            method: "POST",
            body: body(payload),
        }, {
            expectedIds: nodeIds,
            expectedRevision,
            projectId,
        });
    },

    async wrapRegion(projectId: string, input: WrapAtelierRegionInput): Promise<AtelierNodeCommandAck> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        const payload: Record<string, unknown> = { expected_revision: expectedRevision };
        if (input.title !== undefined) payload.title = input.title;
        if (input.color !== undefined) payload.color = input.color;
        if (input.collapsed !== undefined) payload.collapsed = input.collapsed;
        if (input.memberNodeIds !== undefined) payload.member_node_ids = requireNodeIdsInput(input.memberNodeIds, { allowEmpty: true });
        appendLayoutFields(payload, input);
        return requestNodeAck(`${projectPath(projectId)}/regions/wrap`, {
            method: "POST",
            body: body(payload),
        }, {
            expectedRevision,
            projectId,
            requireRegion: true,
        });
    },

    async setRegionMembers(projectId: string, regionId: string, input: SetAtelierRegionMembersInput): Promise<AtelierNodeCommandAck> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        return requestNodeAck(`${regionPath(projectId, regionId)}/members`, {
            method: "POST",
            body: body({ expected_revision: expectedRevision, member_node_ids: requireNodeIdsInput(input.memberNodeIds, { allowEmpty: true }) }),
        }, {
            expectedIds: [regionId],
            expectedRevision,
            projectId,
            requireRegion: true,
        });
    },

    async updateRegionAppearance(projectId: string, regionId: string, input: UpdateAtelierRegionAppearanceInput): Promise<AtelierNodeCommandAck> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        const payload: Record<string, unknown> = { expected_revision: expectedRevision };
        if (input.color !== undefined) payload.color = input.color;
        if (input.collapsed !== undefined) payload.collapsed = input.collapsed;
        return requestNodeAck(`${regionPath(projectId, regionId)}/appearance`, {
            method: "PUT",
            body: body(payload),
        }, {
            expectedIds: [regionId],
            expectedRevision,
            projectId,
            requireRegion: true,
        });
    },
};
