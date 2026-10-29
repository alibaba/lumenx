import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { compareDiagnostics, diagnosticCounts, normalizeMessage, runLintGate } from "./check-lint-baseline.mjs";

const root = path.resolve("/fixture/frontend");
const existing = { ruleId: "@typescript-eslint/no-explicit-any", severity: 2, message: "Unexpected any. Specify a different type.", line: 1, column: 1 };
const report = (messages = [existing], file = "src/example.ts") => [{ filePath: path.join(root, file), messages }];
const baseline = diagnosticCounts(report(), root);
const callback = (line, variable = "characters") => `The '${variable}' logical expression could make the dependencies of useCallback Hook (at line ${line}) change on every render. To fix this, wrap the initialization of '${variable}' in its own useMemo() Hook.`;

test("position-only moves keep existing debt and fixed diagnostics are reported", () => {
  assert.equal(compareDiagnostics(diagnosticCounts(report([{ ...existing, line: 900, column: 23 }]), root), baseline).ok, true);
  const fixed = compareDiagnostics([], baseline);
  assert.equal(fixed.ok, true);
  assert.equal(fixed.removed[0].count, 1);
});

test("additional occurrences cannot consume another file's allowance", () => {
  const duplicate = compareDiagnostics(diagnosticCounts(report([existing, existing]), root), baseline);
  assert.equal(duplicate.ok, false);
  assert.equal(duplicate.added[0].count, 1);
  assert.equal(compareDiagnostics(diagnosticCounts(report([existing], "src/new.ts"), root), baseline).ok, false);
});

test("changed rule, severity, and message fail independently", () => {
  for (const change of [{ ruleId: "new-rule" }, { severity: 1 }, { message: "Different error" }]) {
    assert.equal(compareDiagnostics(diagnosticCounts(report([{ ...existing, ...change }]), root), baseline).ok, false);
  }
});

test("only the known exhaustive-deps callback location is normalized", () => {
  const rule = "react-hooks/exhaustive-deps";
  assert.equal(normalizeMessage(rule, callback(1141)), normalizeMessage(rule, callback(1200)));
  assert.notEqual(normalizeMessage(rule, callback(1141)), normalizeMessage(rule, callback(1141, "props")));
  assert.notEqual(normalizeMessage("other-rule", callback(1141)), normalizeMessage("other-rule", callback(1200)));
  assert.notEqual(normalizeMessage(rule, "Expected 2 dependencies"), normalizeMessage(rule, "Expected 3 dependencies"));
  assert.notEqual(normalizeMessage(rule, callback(1141) + " Additional warning."), normalizeMessage(rule, callback(1200) + " Additional warning."));
});

test("malformed reports, escaped paths, fatal diagnostics and duplicate baseline rows fail", () => {
  assert.throws(() => diagnosticCounts({}, root));
  assert.throws(() => diagnosticCounts([{ filePath: "src/a.ts" }], root));
  assert.throws(() => diagnosticCounts(report([existing], "../outside.ts"), root));
  assert.throws(() => diagnosticCounts(report([{ ...existing, fatal: true }]), root));
  assert.throws(() => diagnosticCounts(report([{ ...existing, ruleId: null }]), root));
  assert.throws(() => compareDiagnostics(baseline, [...baseline, ...baseline]));
});

function run(t, { status = 1, messages = [existing], content, error } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "lumenx-lint-gate-test-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const baselineFile = path.join(directory, "baseline.json");
  fs.writeFileSync(baselineFile, JSON.stringify({ schemaVersion: 1, source: { commit: "fixture", reportSha256: "fixture" }, diagnostics: baseline }));
  return runLintGate({ root, outputDirectory: directory, baselineFile, execute: (_node, args) => {
    assert.ok(args.includes("--no-cache"));
    const target = args.at(-1);
    if (content !== null) fs.writeFileSync(target, content ?? JSON.stringify(report(messages)));
    return { status, stdout: "fixture stdout", stderr: "fixture stderr", error };
  } });
}

test("the gate preserves raw lint exit 1 while approving unchanged debt", (t) => {
  const result = run(t);
  assert.equal(result.ok, true);
  assert.equal(result.rawLintExitCode, 1);
  assert.equal(JSON.parse(fs.readFileSync(path.join(result.reportDirectory, "comparison.json"))).rawLintExitCode, 1);
  assert.match(fs.readFileSync(path.join(result.reportDirectory, "lint.log"), "utf8"), /fixture stdout/);
});

test("the executing gate rejects a new diagnostic", (t) => {
  assert.equal(run(t, { messages: [existing, existing] }).ok, false);
});

test("missing or corrupt output and runner failure cannot pass with a baseline", (t) => {
  for (const options of [{ content: null }, { content: "not json" }, { status: 2 }, { status: null, error: new Error("spawn failed") }]) {
    assert.equal(run(t, options).ok, false);
  }
});

test("exit code must agree with errors and a clean scan may retire all debt", (t) => {
  assert.equal(run(t, { status: 1, messages: [] }).ok, false);
  assert.equal(run(t, { status: 0 }).ok, false);
  assert.equal(run(t, { status: 0, messages: [] }).ok, true);
});
