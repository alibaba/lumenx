import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptPath = fileURLToPath(import.meta.url);
const frontendRoot = path.resolve(path.dirname(scriptPath), "..");
const baselinePath = path.join(path.dirname(scriptPath), "lint-baseline.json");

// Only this existing exhaustive-deps diagnostic embeds a source location.
// Keep variable names, hook kinds, numbers elsewhere, and all other wording.
const callbackLocation = /^The '([^']+)' logical expression could make the dependencies of useCallback Hook \(at line \d+\) change on every render\. To fix this, wrap the initialization of '\1' in its own useMemo\(\) Hook\.$/;

export function normalizeMessage(ruleId, message) {
  return ruleId === "react-hooks/exhaustive-deps" && callbackLocation.test(message)
    ? message.replace(/Hook \(at line \d+\)/, "Hook (at line <line>)")
    : message;
}

function key(row) {
  return JSON.stringify([row.file, row.ruleId, row.severity, row.message]);
}

function validateRow(row) {
  if (!row || typeof row.file !== "string" || !row.file ||
      path.isAbsolute(row.file) || row.file.split("/").some((part) => ["", ".", ".."].includes(part)) ||
      typeof row.ruleId !== "string" || !row.ruleId || ![1, 2].includes(row.severity) ||
      typeof row.message !== "string" || !row.message ||
      !Number.isSafeInteger(row.count) || row.count < 1) {
    throw new Error("Invalid lint diagnostic or baseline row");
  }
}

function countMap(rows) {
  if (!Array.isArray(rows)) throw new Error("Lint diagnostics must be an array");
  const result = new Map();
  for (const row of rows) {
    validateRow(row);
    const identity = key(row);
    if (result.has(identity)) throw new Error("Duplicate lint baseline identity");
    result.set(identity, row);
  }
  return result;
}

export function diagnosticCounts(report, root) {
  if (!Array.isArray(report)) throw new Error("ESLint report must be an array");
  const counts = new Map();
  for (const file of report) {
    if (!file || typeof file.filePath !== "string" || !Array.isArray(file.messages)) {
      throw new Error("Malformed ESLint file result");
    }
    const relative = path.relative(root, path.resolve(root, file.filePath)).split(path.sep).join("/");
    for (const diagnostic of file.messages) {
      if (diagnostic.fatal) throw new Error(`Fatal ESLint diagnostic in ${relative}`);
      const row = {
        file: relative,
        ruleId: diagnostic.ruleId,
        severity: diagnostic.severity,
        message: normalizeMessage(diagnostic.ruleId, diagnostic.message),
        count: 1,
      };
      validateRow(row);
      const identity = key(row);
      const existing = counts.get(identity);
      if (existing) existing.count += 1;
      else counts.set(identity, row);
    }
  }
  return [...counts.values()].sort((a, b) => key(a).localeCompare(key(b)));
}

export function compareDiagnostics(current, baseline) {
  const actual = countMap(current);
  const allowed = countMap(baseline);
  const added = [];
  const removed = [];
  for (const [identity, row] of actual) {
    const count = row.count - (allowed.get(identity)?.count ?? 0);
    if (count > 0) added.push({ ...row, count });
  }
  for (const [identity, row] of allowed) {
    const count = row.count - (actual.get(identity)?.count ?? 0);
    if (count > 0) removed.push({ ...row, count });
  }
  return {
    ok: added.length === 0,
    baselineDiagnostics: baseline.reduce((sum, row) => sum + row.count, 0),
    currentDiagnostics: current.reduce((sum, row) => sum + row.count, 0),
    added,
    removed,
  };
}

export function runLintGate({ root, outputDirectory, baselineFile, execute = spawnSync }) {
  fs.mkdirSync(outputDirectory, { recursive: true });
  const reportDirectory = fs.mkdtempSync(path.join(outputDirectory, "lint-"));
  const reportPath = path.join(reportDirectory, "eslint.json");
  const result = execute(process.execPath, [
    path.join(root, "node_modules", "next", "dist", "bin", "next"),
    "lint", "--no-cache", "--format", "json", "--output-file", reportPath,
  ], { cwd: root, encoding: "utf8", maxBuffer: 32 * 1024 * 1024 });
  fs.writeFileSync(path.join(reportDirectory, "lint.log"), `${result.stdout ?? ""}${result.stderr ?? ""}`);
  let summary;
  try {
    if (result.error || ![0, 1].includes(result.status)) {
      throw new Error(`ESLint failed to run: ${result.error?.message ?? result.signal ?? result.status}`);
    }
    const baseline = JSON.parse(fs.readFileSync(baselineFile, "utf8"));
    if (baseline.schemaVersion !== 1 || !baseline.source?.commit || !baseline.source?.reportSha256) {
      throw new Error("Lint baseline is missing its fixed-source provenance");
    }
    const current = diagnosticCounts(JSON.parse(fs.readFileSync(reportPath, "utf8")), root);
    const errors = current.reduce((sum, row) => sum + (row.severity === 2 ? row.count : 0), 0);
    if ((result.status === 1) !== (errors > 0)) {
      throw new Error("ESLint exit status and reported errors disagree");
    }
    summary = { ...compareDiagnostics(current, baseline.diagnostics), baselineSource: baseline.source };
  } catch (error) {
    summary = { ok: false, error: error.message };
  }
  summary.rawLintExitCode = result.status;
  summary.scope = "No new diagnostics by file, rule, severity, normalized message, and occurrence count";
  fs.writeFileSync(path.join(reportDirectory, "comparison.json"), `${JSON.stringify(summary, null, 2)}\n`);
  return { ...summary, reportDirectory };
}

if (process.argv[1] && path.resolve(process.argv[1]) === scriptPath) {
  const args = process.argv.slice(2);
  if (args.length !== 2 || args[0] !== "--output-directory" || !args[1]) {
    console.error("Usage: node scripts/check-lint-baseline.mjs --output-directory <reports>");
    process.exitCode = 2;
  } else {
    const result = runLintGate({ root: frontendRoot, outputDirectory: path.resolve(args[1]), baselineFile: baselinePath });
    console.log(JSON.stringify(result, null, 2));
    process.exitCode = result.ok ? 0 : 1;
  }
}
