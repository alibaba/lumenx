import {
    assertAtelierProjectIdentity,
    assertAtelierProjectRevisionNotBefore,
    AtelierProjectApiError,
    hasOwnAtelierField,
    isAtelierRecord,
    parseAtelierNodeRestoreSkip,
    parseAtelierProject,
    requestAtelierJson,
    requireAtelierProjectIdInput,
    requireAtelierRevisionInput,
} from "@/lib/atelierApi";
import type {
    AtelierNodeDeleteReceipt,
    DeleteAtelierNodesInput,
    DeleteAtelierNodesResult,
    RestoreAtelierNodesInput,
    RestoreAtelierNodesResult,
} from "@/lib/atelierTypes";

const ATELIER_PROJECTS_PATH = "/atelier/projects";

const body = (value: Record<string, unknown>): string => JSON.stringify(value);

const projectPath = (projectId: string): string => `${ATELIER_PROJECTS_PATH}/${encodeURIComponent(requireAtelierProjectIdInput(projectId))}`;

const requireNodeIdsInput = (nodeIds: string[]): string[] => {
    if (!Array.isArray(nodeIds) || nodeIds.length === 0) {
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

const requireReceiptIdInput = (receiptId: string): string => {
    if (typeof receiptId !== "string" || receiptId.length === 0) {
        throw new AtelierProjectApiError("Invalid Atelier delete receipt id", "invalid_atelier_delete_receipt_id");
    }
    return receiptId;
};

const assertExactIds = (actual: string[], expected: string[], code: string): void => {
    if (actual.length !== expected.length || actual.some((nodeId, index) => nodeId !== expected[index])) {
        throw new AtelierProjectApiError("Atelier node history acknowledgement did not match requested target", code);
    }
};

const findReceipt = (receipts: AtelierNodeDeleteReceipt[], receiptId: string): AtelierNodeDeleteReceipt | undefined => {
    return receipts.find(receipt => receipt.receipt_id === receiptId);
};

const restoreSkipsEqual = (left: RestoreAtelierNodesResult["skips"], right: RestoreAtelierNodesResult["skips"]): boolean => {
    return left.length === right.length && left.every((item, index) => {
        const other = right[index];
        return item.scope === other.scope
            && item.reason === other.reason
            && item.node_id === other.node_id
            && item.group === other.group;
    });
};

const parseStringArray = (value: unknown, code: string): string[] => {
    if (!Array.isArray(value) || !value.every(item => typeof item === "string" && item.length > 0)) {
        throw new AtelierProjectApiError("Invalid Atelier node history acknowledgement", code);
    }
    if (value.length !== new Set(value).size) {
        throw new AtelierProjectApiError("Invalid Atelier node history acknowledgement", code);
    }
    return [...value];
};

const parseDeleteAck = (value: unknown, projectId: string, expectedRevision: number, requestedIds: string[]): DeleteAtelierNodesResult => {
    if (!isAtelierRecord(value) || !hasOwnAtelierField(value, "project")) {
        throw new AtelierProjectApiError("Invalid Atelier delete acknowledgement", "invalid_atelier_delete_ack");
    }
    const project = assertAtelierProjectRevisionNotBefore(
        assertAtelierProjectIdentity(parseAtelierProject(value.project), projectId),
        expectedRevision,
    );
    const receiptId = typeof value.receipt_id === "string" && value.receipt_id.length > 0
        ? value.receipt_id
        : (() => { throw new AtelierProjectApiError("Invalid Atelier delete acknowledgement", "invalid_atelier_delete_ack"); })();
    const nodeIds = parseStringArray(value.node_ids, "invalid_atelier_delete_ack");
    assertExactIds(nodeIds, requestedIds, "atelier_delete_ack_target_mismatch");
    const receipt = findReceipt(project.node_delete_receipts, receiptId);
    if (!receipt) {
        throw new AtelierProjectApiError("Atelier delete receipt missing from acknowledgement project", "atelier_delete_receipt_missing");
    }
    if (receipt.project_id !== projectId) {
        throw new AtelierProjectApiError("Atelier delete receipt owner mismatch", "atelier_delete_receipt_owner_mismatch");
    }
    if (receipt.restored_at !== null) {
        throw new AtelierProjectApiError("Atelier delete receipt was already consumed", "atelier_delete_receipt_consumed");
    }
    if (receipt.base_revision !== expectedRevision) {
        throw new AtelierProjectApiError("Atelier delete receipt base revision did not match request", "atelier_delete_receipt_base_revision_mismatch");
    }
    assertExactIds(receipt.node_ids, requestedIds, "atelier_delete_ack_target_mismatch");
    return { project, receipt_id: receiptId, node_ids: nodeIds };
};

const parseRestoreAck = (value: unknown, projectId: string, expectedRevision: number, receiptId: string): RestoreAtelierNodesResult => {
    if (!isAtelierRecord(value) || !hasOwnAtelierField(value, "project")) {
        throw new AtelierProjectApiError("Invalid Atelier restore acknowledgement", "invalid_atelier_restore_ack");
    }
    const project = assertAtelierProjectRevisionNotBefore(
        assertAtelierProjectIdentity(parseAtelierProject(value.project), projectId),
        expectedRevision,
    );
    const ackReceiptId = typeof value.receipt_id === "string" && value.receipt_id.length > 0
        ? value.receipt_id
        : (() => { throw new AtelierProjectApiError("Invalid Atelier restore acknowledgement", "invalid_atelier_restore_ack"); })();
    if (ackReceiptId !== receiptId) {
        throw new AtelierProjectApiError("Atelier restore acknowledgement did not match requested receipt", "atelier_restore_ack_receipt_mismatch");
    }
    const receipt = findReceipt(project.node_delete_receipts, receiptId);
    if (!receipt) {
        throw new AtelierProjectApiError("Atelier delete receipt missing from acknowledgement project", "atelier_delete_receipt_missing");
    }
    if (receipt.project_id !== projectId) {
        throw new AtelierProjectApiError("Atelier delete receipt owner mismatch", "atelier_delete_receipt_owner_mismatch");
    }
    if (receipt.restored_at === null) {
        throw new AtelierProjectApiError("Atelier restore acknowledgement receipt was not consumed", "atelier_restore_receipt_unconsumed");
    }
    if (receipt.restore_base_revision !== expectedRevision) {
        throw new AtelierProjectApiError("Atelier restore receipt base revision did not match request", "atelier_restore_receipt_base_revision_mismatch");
    }
    const restoredNodeIds = parseStringArray(value.restored_node_ids, "invalid_atelier_restore_ack");
    assertExactIds(restoredNodeIds, receipt.node_ids, "atelier_restore_ack_target_mismatch");
    for (const nodeId of restoredNodeIds) {
        if (!project.nodes.some(node => node.id === nodeId)) {
            throw new AtelierProjectApiError("Atelier restore acknowledgement missing live restored node", "atelier_restore_ack_missing_live_node");
        }
    }
    if (!Array.isArray(value.skips)) {
        throw new AtelierProjectApiError("Invalid Atelier restore acknowledgement", "invalid_atelier_restore_ack");
    }
    const skips = value.skips.map(parseAtelierNodeRestoreSkip);
    if (!restoreSkipsEqual(skips, receipt.last_restore_skips)) {
        throw new AtelierProjectApiError("Atelier restore acknowledgement skips did not match committed receipt", "atelier_restore_ack_skips_mismatch");
    }
    return { project, receipt_id: receiptId, restored_node_ids: restoredNodeIds, skips };
};

export const atelierNodeHistoryApi = {
    async deleteNodes(projectId: string, input: DeleteAtelierNodesInput): Promise<DeleteAtelierNodesResult> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        const nodeIds = requireNodeIdsInput(input.nodeIds);
        return parseDeleteAck(await requestAtelierJson(`${projectPath(projectId)}/nodes/delete`, {
            method: "POST",
            body: body({ expected_revision: expectedRevision, node_ids: nodeIds }),
        }), projectId, expectedRevision, nodeIds);
    },

    async restoreNodes(projectId: string, input: RestoreAtelierNodesInput): Promise<RestoreAtelierNodesResult> {
        const expectedRevision = requireAtelierRevisionInput(input.expectedRevision);
        const receiptId = requireReceiptIdInput(input.receiptId);
        return parseRestoreAck(await requestAtelierJson(`${projectPath(projectId)}/nodes/restore`, {
            method: "POST",
            body: body({ expected_revision: expectedRevision, receipt_id: receiptId }),
        }), projectId, expectedRevision, receiptId);
    },
};
