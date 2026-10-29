import { afterEach, describe, expect, it, vi } from "vitest";
import { parseAtelierProject } from "@/lib/atelierApi";
import { atelierCandidateJudgmentApi } from "@/lib/atelierCandidateJudgmentApi";
import selectedFixture from "./fixtures/atelier-generation-selected.json";
import packageFixture from "./fixtures/atelier-project-package-wire.json";

const PROJECT_ID = "project-1";
const NODE_ID = "video";
const CANDIDATE_ID = "candidate-1";

const response = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });

function ack(patch: Record<string, unknown> = {}) {
    const project = structuredClone(selectedFixture.project) as Record<string, unknown>;
    return {
        project: { ...project, candidate_judgments: [], ...patch },
        node_ids: [NODE_ID],
    };
}

afterEach(() => {
    vi.unstubAllGlobals();
});

describe("Atelier candidate judgment API", () => {
    it("defaults legacy missing history to empty but rejects null history", () => {
        const legacy = structuredClone(selectedFixture.project) as Record<string, unknown>;
        delete legacy.candidate_judgments;
        expect(parseAtelierProject(legacy).candidate_judgments).toEqual([]);
        expect(() => parseAtelierProject({ ...legacy, candidate_judgments: null })).toThrow();
    });

    it("parses imported inert video candidate judgment history and accepts legal emoji reason length", () => {
        const project = structuredClone(packageFixture.prepared_project) as Record<string, unknown>;
        project.candidate_judgments = [{
            schema_version: 1,
            id: "judgment-imported",
            project_id: project.id,
            node_id: "fixture-node-3",
            candidate_id: "fixture-candidate",
            media_kind: "video",
            verdict: "keep",
            reason: "🔥".repeat(2000),
            created_at: 10,
        }];

        expect(parseAtelierProject(project).candidate_judgments?.[0].reason).toBe("🔥".repeat(2000));
    });

    it("rejects invalid judgment chains, media kind drift, and negative timestamps", () => {
        const base = structuredClone(selectedFixture.project) as Record<string, unknown>;
        const judgment = {
            schema_version: 1,
            id: "judgment-1",
            project_id: PROJECT_ID,
            node_id: NODE_ID,
            candidate_id: CANDIDATE_ID,
            media_kind: "video",
            verdict: "keep",
            reason: "",
            created_at: 10,
        };
        expect(() => parseAtelierProject({ ...base, candidate_judgments: [{ ...judgment, media_kind: "audio" }] })).toThrow();
        expect(() => parseAtelierProject({ ...base, candidate_judgments: [{ ...judgment, created_at: -1 }] })).toThrow();
        expect(() => parseAtelierProject({ ...base, candidate_judgments: [{ ...judgment, supersedes_judgment_id: "missing" }] })).toThrow();
    });

    it("sends the exact review body and verifies appended latest judgment", async () => {
        const project = structuredClone(selectedFixture.project) as Record<string, unknown>;
        const judgment = {
            schema_version: 1,
            id: "judgment-1",
            project_id: PROJECT_ID,
            node_id: NODE_ID,
            candidate_id: CANDIDATE_ID,
            media_kind: "video",
            verdict: "keep",
            reason: "Best emotion",
            created_at: 10,
        };
        const fetch = vi.fn().mockResolvedValue(response(ack({ ...project, revision: 3, candidate_judgments: [judgment] })));
        vi.stubGlobal("fetch", fetch);

        const result = await atelierCandidateJudgmentApi.setCandidateJudgment(PROJECT_ID, NODE_ID, CANDIDATE_ID, {
            expected_revision: 2,
            verdict: "keep",
            reason: "Best emotion",
        });

        expect(result.project.candidate_judgments?.at(-1)).toEqual(judgment);
        expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/atelier/projects/project-1/nodes/video/candidates/candidate-1/judgment"), expect.objectContaining({
            method: "PUT",
            body: JSON.stringify({ expected_revision: 2, verdict: "keep", reason: "Best emotion" }),
        }));
    });

    it("allows first unreviewed empty reason as a same-revision no-op", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(ack())));

        const result = await atelierCandidateJudgmentApi.setCandidateJudgment(PROJECT_ID, NODE_ID, CANDIDATE_ID, {
            expected_revision: 2,
            verdict: "unreviewed",
            reason: "",
        });

        expect(result.project.revision).toBe(2);
        expect(result.project.candidate_judgments).toEqual([]);
    });

    it("rejects ACKs whose latest judgment does not match the submitted verdict and reason", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(ack({ revision: 3, candidate_judgments: [{
            schema_version: 1,
            id: "judgment-1",
            project_id: PROJECT_ID,
            node_id: NODE_ID,
            candidate_id: CANDIDATE_ID,
            media_kind: "video",
            verdict: "reject",
            reason: "Wrong payload",
            created_at: 10,
        }] }))));

        await expect(atelierCandidateJudgmentApi.setCandidateJudgment(PROJECT_ID, NODE_ID, CANDIDATE_ID, {
            expected_revision: 2,
            verdict: "keep",
            reason: "Best emotion",
        })).rejects.toMatchObject({ code: "invalid_atelier_candidate_judgment_request" });
    });

    it("accepts a 2000-code-point emoji reason in the request body", async () => {
        const reason = "🔥".repeat(2000);
        const fetch = vi.fn().mockResolvedValue(response(ack({ revision: 3, candidate_judgments: [{
            schema_version: 1,
            id: "judgment-1",
            project_id: PROJECT_ID,
            node_id: NODE_ID,
            candidate_id: CANDIDATE_ID,
            media_kind: "video",
            verdict: "keep",
            reason,
            created_at: 10,
        }] })));
        vi.stubGlobal("fetch", fetch);

        await atelierCandidateJudgmentApi.setCandidateJudgment(PROJECT_ID, NODE_ID, CANDIDATE_ID, {
            expected_revision: 2,
            verdict: "keep",
            reason,
        });

        expect(JSON.parse((fetch.mock.calls[0][1] as RequestInit).body as string).reason).toBe(reason);
    });
});
