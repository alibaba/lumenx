// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

type Product = "studio" | "atelier";
const productOf = (file: string): Product | null => {
    if (file.startsWith("components/atelier/") || /^lib\/atelier[^/]*\.tsx?$/.test(file) || file === "store/atelierStore.ts") return "atelier";
    if (/^components\/(modules|project|canvas)\//.test(file) || /^(store\/(projectStore|editorStore|scriptDraftStore)|lib\/api)\.ts$/.test(file)) return "studio";
    return null;
};

function importsIn(file: string, source: string): string[] {
    const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
    const imports: string[] = [];
    const record = (node?: ts.Node) => { if (node && ts.isStringLiteralLike(node)) imports.push(node.text); };
    const visit = (node: ts.Node): void => {
        if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) record(node.moduleSpecifier);
        if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) record(node.moduleReference.expression);
        if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)) record(node.argument.literal);
        if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword || (ts.isIdentifier(node.expression) && node.expression.text === "require"))) record(node.arguments[0]);
        ts.forEachChild(node, visit);
    };
    visit(tree);
    return imports;
}

function dependencyGraph(sources: Map<string, string>): Map<string, string[]> {
    return new Map(Array.from(sources).map(([file, source]) => [file, importsIn(file, source).flatMap(specifier => {
        const base = specifier.startsWith("@/") ? specifier.slice(2)
            : specifier.startsWith(".") ? path.posix.normalize(path.posix.join(path.posix.dirname(file), specifier)) : null;
        if (base === null) return [];
        const resolved = [base, `${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`].find(candidate => sources.has(candidate));
        return resolved ? [resolved] : [];
    })]));
}

function crossProductPaths(sources: Map<string, string>): string[] {
    const graph = dependencyGraph(sources);
    const violations: string[] = [];
    for (const source of Array.from(graph.keys())) {
        const product = productOf(source);
        if (!product) continue;
        const seen = new Set([source]);
        const queue = [[source]];
        for (let index = 0; index < queue.length; index += 1) {
            const chain = queue[index];
            for (const target of graph.get(chain[chain.length - 1]) ?? []) {
                if (seen.has(target)) continue;
                seen.add(target);
                const targetProduct = productOf(target);
                if (targetProduct && targetProduct !== product) violations.push([...chain, target].join(" -> "));
                else queue.push([...chain, target]);
            }
        }
    }
    return violations;
}

function actualSources(): Map<string, string> {
    const root = path.resolve("src");
    const sources = new Map<string, string>();
    const visit = (directory: string) => {
        for (const item of readdirSync(directory, { withFileTypes: true })) {
            if (item.name === "__tests__") continue;
            const absolute = path.join(directory, item.name);
            if (item.isDirectory()) visit(absolute);
            else if (/\.tsx?$/.test(item.name) && !/\.(test|spec)\.tsx?$/.test(item.name)) sources.set(path.relative(root, absolute).split(path.sep).join("/"), readFileSync(absolute, "utf8"));
        }
    };
    visit(root);
    return sources;
}

describe("Studio and Atelier dependency boundaries", () => {
    it.each([
        'import type { Project } from "@/store/projectStore";',
        'export { value } from "../store/projectStore";',
        'const load = () => import("@/store/projectStore");',
        'type Project = import("@/store/projectStore").Project;',
        'import project = require("@/store/projectStore");',
    ])("finds indirect dependencies through neutral modules: %s", source => {
        const sources = new Map([
            ["components/atelier/Panel.tsx", 'import "../../lib/shared";'],
            ["lib/shared.ts", source],
            ["store/projectStore.ts", "export const value = 1;"],
        ]);
        expect(crossProductPaths(sources)).toContain("components/atelier/Panel.tsx -> lib/shared.ts -> store/projectStore.ts");
    });

    it("permits a neutral route composing both products and shared primitives", () => {
        const sources = new Map([
            ["app/page.tsx", 'import "@/components/atelier/Panel"; import "@/components/modules/Studio";'],
            ["components/atelier/Panel.tsx", 'import "@/lib/shared";'],
            ["components/modules/Studio.tsx", 'import "@/lib/shared";'],
            ["lib/shared.ts", "export const value = 1;"],
        ]);
        expect(crossProductPaths(sources)).toEqual([]);
    });

    it("keeps actual product code independent through every local import chain", () => {
        const sources = actualSources();
        expect(Array.from(sources.keys()).filter(file => productOf(file) === "atelier").length).toBeGreaterThan(20);
        expect(Array.from(sources.keys()).filter(file => productOf(file) === "studio").length).toBeGreaterThan(20);
        expect(crossProductPaths(sources)).toEqual([]);
    });
});
