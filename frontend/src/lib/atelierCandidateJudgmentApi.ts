import { AtelierProjectApiError, hasOwnAtelierField, requestAtelierJson, requireAtelierProjectIdInput, requireAtelierRevisionInput } from "@/lib/atelierApi";
import { parseAtelierNodeCommandAck } from "@/lib/atelierNodeApi";
import type { AtelierCandidateJudgment, AtelierNodeCommandAck, SetAtelierCandidateJudgmentInput } from "@/lib/atelierTypes";

const fail = (): never => {
    throw new AtelierProjectApiError("Invalid Atelier candidate judgment request", "invalid_atelier_candidate_judgment_request");
};

const text = (value: string, code: string): string => {
    if (typeof value !== "string" || value.length === 0) {
        throw new AtelierProjectApiError("Invalid Atelier candidate judgment identity", code);
    }
    return value;
};

const projectPath = (projectId: string): string => `/atelier/projects/${encodeURIComponent(requireAtelierProjectIdInput(projectId))}`;
const nodePath = (projectId: string, nodeId: string): string => `${projectPath(projectId)}/nodes/${encodeURIComponent(text(nodeId, "invalid_atelier_node_id"))}`;

const reasonLength = (value: string): number => Array.from(value).length;

const candidateJudgments = (ack: AtelierNodeCommandAck): AtelierCandidateJudgment[] => ack.project.candidate_judgments ?? [];

const assertJudgmentAck = (
    ack: AtelierNodeCommandAck,
    nodeId: string,
    candidateId: string,
    expectedRevision: number,
    input: SetAtelierCandidateJudgmentInput,
): AtelierNodeCommandAck => {
    const history = candidateJudgments(ack).filter(item => item.node_id === nodeId && item.candidate_id === candidateId);
    const latest = history[history.length - 1] ?? null;
    if (input.verdict === "unreviewed" && input.reason === "" && latest === null) {
        if (ack.project.revision !== expectedRevision) fail();
        return ack;
    }
    if (!latest || latest.verdict !== input.verdict || latest.reason !== input.reason || latest.project_id !== ack.project.id
        || latest.node_id !== nodeId || latest.candidate_id !== candidateId) fail();
    if (history.length > 1) {
        const previous = history[history.length - 2];
        if (latest.supersedes_judgment_id !== previous.id) fail();
    } else if (latest.supersedes_judgment_id !== undefined) {
        fail();
    }
    if (ack.project.revision !== expectedRevision && ack.project.revision !== expectedRevision + 1) fail();
    return ack;
};

export const atelierCandidateJudgmentApi = {
    async setCandidateJudgment(
        projectId: string,
        nodeId: string,
        candidateId: string,
        input: SetAtelierCandidateJudgmentInput,
    ): Promise<AtelierNodeCommandAck> {
        if (!hasOwnAtelierField(input, "expected_revision") || !hasOwnAtelierField(input, "verdict") || !hasOwnAtelierField(input, "reason")
            || Object.keys(input).length !== 3 || (input.verdict !== "unreviewed" && input.verdict !== "keep" && input.verdict !== "reject")
            || typeof input.reason !== "string" || reasonLength(input.reason) > 2000) fail();
        const expectedRevision = requireAtelierRevisionInput(input.expected_revision);
        const value = await requestAtelierJson(
            `${nodePath(projectId, nodeId)}/candidates/${encodeURIComponent(text(candidateId, "invalid_atelier_candidate_id"))}/judgment`,
            {
                method: "PUT",
                body: JSON.stringify({
                    expected_revision: expectedRevision,
                    verdict: input.verdict,
                    reason: input.reason,
                }),
            },
        );
        return assertJudgmentAck(
            parseAtelierNodeCommandAck(value, { projectId, expectedRevision, expectedIds: [nodeId] }),
            nodeId,
            candidateId,
            expectedRevision,
            input,
        );
    },
};
