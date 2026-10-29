import { afterEach, expect, it, vi } from "vitest";
import { parseAtelierProject } from "@/lib/atelierApi";
import { atelierCandidateJudgmentApi } from "@/lib/atelierCandidateJudgmentApi";
import wire from "./fixtures/atelier-candidate-judgment-wire.json";

afterEach(() => vi.unstubAllGlobals());

it("consumes actual HTTP judgment and no-op acknowledgements without changing generation facts", async () => {
    const before = parseAtelierProject(wire.before);
    const fetch = vi.fn()
        .mockResolvedValueOnce(new Response(JSON.stringify(wire.ack)))
        .mockResolvedValueOnce(new Response(JSON.stringify(wire.noop_ack)));
    vi.stubGlobal("fetch", fetch);
    const first = await atelierCandidateJudgmentApi.setCandidateJudgment("p1", "node-1", "candidate-1", {
        ...wire.request, verdict: "keep",
    });
    expect(first.project.nodes).toEqual(before.nodes);
    expect(first.project.generation_attempts).toEqual(before.generation_attempts);
    expect(first.project.generation_request_receipts).toEqual(before.generation_request_receipts);
    expect(first.project.sequence).toEqual(before.sequence);
    expect(first.project.candidate_judgments).toEqual(wire.ack.project.candidate_judgments);
    const noop = await atelierCandidateJudgmentApi.setCandidateJudgment("p1", "node-1", "candidate-1", {
        ...wire.request, verdict: "keep", expected_revision: 2,
    });
    expect(noop).toEqual(first);
});
