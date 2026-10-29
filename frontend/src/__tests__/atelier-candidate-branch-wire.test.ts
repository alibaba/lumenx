import { afterEach, describe, expect, it, vi } from "vitest";
import { parseAtelierProject } from "@/lib/atelierApi";
import { atelierCandidateBranchApi } from "@/lib/atelierCandidateBranchApi";
import wire from "./fixtures/atelier-candidate-branch-wire.json";

afterEach(() => vi.unstubAllGlobals());

describe("candidate branch actual application wire", () => {
    for (const [label, fixture] of Object.entries(wire)) {
        it(`accepts the actual ${label} response without dispatching generation`, async () => {
            const before = parseAtelierProject(structuredClone(fixture.before));
            const after = parseAtelierProject(structuredClone(fixture.ack.project));
            const branch = after.nodes.find(node => node.id === fixture.ack.node_ids[0])!;
            const provenance = branch.data.branch_source as { origin_node_id: string; origin_candidate_id: string; recipe_status: string };
            const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(fixture.ack), {
                status: 200, headers: { "Content-Type": "application/json" },
            }));
            vi.stubGlobal("fetch", fetch);
            const result = await atelierCandidateBranchApi.branchCandidate(before.id, provenance.origin_node_id,
                provenance.origin_candidate_id, fixture.request, before);
            expect(result.project).toEqual(after);
            expect(fetch).toHaveBeenCalledTimes(1);
            expect(fetch.mock.calls[0][0]).toMatch(/\/candidates\/[^/]+\/branch$/);
            expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(fixture.request);
            expect(result.project.generation_attempts).toEqual(before.generation_attempts);
            expect(branch.media_urls).toEqual([]);
            if (label === "provider") {
                expect(provenance.recipe_status).toBe("available");
                expect(branch.prompt).toBe("Original frozen image prompt");
                expect(branch.data.generation_draft).toMatchObject({ schema_version: 3, params: { seed: 0, watermark: false } });
            } else {
                expect(provenance.recipe_status).toBe("unavailable_imported");
                expect(branch.prompt).toBe("");
                expect(branch.data.generation_draft).toBeUndefined();
            }
        });
    }
});
