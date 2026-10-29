import { describe, expect, it } from "vitest";
import { parseAtelierProject } from "@/lib/atelierApi";
import fixture from "./fixtures/atelier-project-package-wire.json";
import legacy from "./fixtures/atelier-project.json";

describe("Atelier independent package wire", () => {
    it("preserves backend copy provenance and target media proof without execution rights", () => {
        const project = parseAtelierProject(fixture.prepared_project);
        expect(project.import_provenance).toEqual(fixture.prepared_project.import_provenance);
        expect(project.exports[0].imported_proof).toEqual(fixture.prepared_project.exports[0].imported_proof);
        expect(project.agent_turns).toEqual([]);
        expect(project.generation_attempts).toEqual([]);
        expect(project.sequence_export_jobs).toEqual([]);
        expect(project.exports[0].publication_proof).toBeNull();
        expect(project.import_provenance?.agent_history[0].messages).toContainEqual(expect.objectContaining({
            role: "tool", status: "completed", error_code: "none",
        }));
    });

    it("preserves absent fields in legacy wire", () => {
        const project = parseAtelierProject(legacy);
        expect(project).not.toHaveProperty("import_provenance");
        project.exports.forEach(record => expect(record).not.toHaveProperty("imported_proof"));
    });

    it.each([
        { project_id: "other" }, { export_id: "other" }, { relative_path: "video/source.mp4" },
        { relative_path: "/tmp/private.mp4" }, { relative_path: "video/imported/../other.mp4" },
        { sha256: "invalid" }, { size_bytes: -1 }, { size_bytes: 1.5 },
        { imported_at: Number.NaN }, { schema_version: 2 }, { credential_ref: "hidden" },
    ])("rejects mismatched or malformed target proof %j", patch => {
        const raw = structuredClone(fixture.prepared_project);
        Object.assign(raw.exports[0].imported_proof, patch);
        expect(() => parseAtelierProject(raw)).toThrow();
    });

    it("rejects imported proof combined with native render identity", () => {
        const raw = structuredClone(fixture.prepared_project);
        Object.assign(raw.exports[0], { job_id: "forged", snapshot_digest: "a".repeat(64) });
        expect(() => parseAtelierProject(raw)).toThrow();
    });

    it.each([
        { schema_version: 2 }, { source_project_id: "" }, { imported_at: Infinity }, { agent_history: null },
        { request_id: "hidden" },
    ])("rejects malformed provenance %j", patch => {
        const raw = structuredClone(fixture.prepared_project);
        Object.assign(raw.import_provenance, patch);
        expect(() => parseAtelierProject(raw)).toThrow();
    });

    it("rejects history containing executable fields instead of silently discarding them", () => {
        const raw = structuredClone(fixture.prepared_project);
        Object.assign(raw.import_provenance.agent_history[0], { approval_record: { decision: "approved" } });
        expect(() => parseAtelierProject(raw)).toThrow();
    });

    it("rejects imported history carrying Product Skill execution authority", () => {
        const raw = structuredClone(fixture.prepared_project);
        Object.assign(raw.import_provenance.agent_history[0], {
            skill_name: "story-scout",
            skill_snapshot: { name: "story-scout", prompt_template: "execute this" },
            expected_tools: ["canvas.readProject"],
        });
        expect(() => parseAtelierProject(raw)).toThrow();
    });
});
