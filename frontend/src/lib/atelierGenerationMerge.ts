import type { AtelierGenerationAttempt, AtelierProject, JsonRecord } from "@/lib/atelierTypes";

const canonical = (value: unknown): string => JSON.stringify(value, (_key, item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return item;
    return Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]]));
});

function receiptIdentity(receipt: AtelierProject["generation_request_receipts"][number]): string {
    return `${receipt.entrypoint}:${receipt.client_request_id}`;
}

function isMediaAttempt(attempt: AtelierGenerationAttempt): attempt is Extract<AtelierGenerationAttempt, { execution_contract: "atelier-provider-media-v2" }> {
    return "execution_contract" in attempt && attempt.execution_contract === "atelier-provider-media-v2";
}

function identity(attempt: AtelierGenerationAttempt): string {
    const record = attempt.provider_execution;
    const input = isMediaAttempt(attempt)
        ? { execution_contract: attempt.execution_contract, media_kind: attempt.media_kind }
        : "input_contract_version" in attempt
            ? { input_contract_version: attempt.input_contract_version, input_media: attempt.input_media }
            : { first_frame_source: attempt.first_frame_source };
    return canonical({
        schema_version: attempt.schema_version, project_id: attempt.project_id, node_id: attempt.node_id,
        candidate_id: attempt.candidate_id, attempt_id: attempt.attempt_id, created_at: attempt.created_at,
        ...input,
        record: { schema_version: record.schema_version, attempt_id: record.attempt_id,
            ...("execution_kind" in record ? { execution_kind: record.execution_kind, media_kind: record.media_kind, output_ref: record.output_ref } : {}),
            provider_model_id: record.provider_model_id, intent: record.intent, media: record.media,
            scope: record.scope, credential_ref: record.credential_ref, fingerprint: record.fingerprint },
    });
}

/** The caller chooses the canvas snapshot; execution has its own revision clock. */
export function mergeAtelierGenerationState(base: AtelierProject, other: AtelierProject): AtelierProject {
    if (base.id !== other.id) throw new Error("Atelier generation project mismatch");
    const attempts = new Map((base.generation_attempts ?? []).map(item => [item.attempt_id, item]));
    const receipts = new Map((base.generation_request_receipts ?? []).map(item => [receiptIdentity(item), item]));
    for (const receipt of other.generation_request_receipts ?? []) {
        if (canonical(receipts.get(receiptIdentity(receipt))) !== canonical(receipt)) {
            throw new Error("Atelier generation receipt changed or missing");
        }
    }
    let changed = false;
    for (const incoming of other.generation_attempts ?? []) {
        const current = attempts.get(incoming.attempt_id);
        if (!current) throw new Error("Atelier generation attempt missing from current canvas");
        if (identity(current) !== identity(incoming)) throw new Error("Atelier generation execution identity changed");
        if (current.attempt_revision === incoming.attempt_revision && canonical(current) !== canonical(incoming)) {
            throw new Error("Atelier generation attempt has different content at the same revision");
        }
        if (incoming.attempt_revision > current.attempt_revision) {
            attempts.set(incoming.attempt_id, incoming);
            changed = true;
        }
    }
    if (!changed) return base;
    const result = structuredClone(base);
    result.generation_attempts = (base.generation_attempts ?? []).map(item => structuredClone(attempts.get(item.attempt_id)!));
    for (const node of result.nodes) {
        for (const field of ["candidates", "deleted_candidates"]) {
            const candidates = node.data[field];
            if (!Array.isArray(candidates)) continue;
            for (const value of candidates) {
                if (!value || typeof value !== "object" || Array.isArray(value)
                    || (value.execution_contract !== "atelier-provider-video-v1" && value.execution_contract !== "atelier-provider-media-v2")) continue;
                const candidate = value as JsonRecord;
                const attemptId = typeof candidate.attempt_id === "string" ? candidate.attempt_id : null;
                if (attemptId === null) continue;
                const attempt = attempts.get(attemptId);
                if (!attempt || attempt.node_id !== node.id || attempt.candidate_id !== candidate.id) {
                    throw new Error("Atelier generation candidate owner mismatch");
                }
                if (candidate.execution_contract === "atelier-provider-media-v2") {
                    if (!isMediaAttempt(attempt) || candidate.media_kind !== attempt.media_kind) {
                        throw new Error("Atelier generation candidate owner mismatch");
                    }
                    Object.assign(candidate, { status: attempt.status, error_code: attempt.error_code,
                        media_url: attempt.output_ref, execution_fingerprint: attempt.provider_execution.fingerprint });
                } else {
                    Object.assign(candidate, { status: attempt.status, error_code: attempt.error_code,
                        video_url: attempt.output_ref, updated_at: attempt.updated_at });
                }
            }
        }
    }
    return result;
}
