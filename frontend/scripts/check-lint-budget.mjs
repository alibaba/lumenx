import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";

const budget = JSON.parse(readFileSync("lint-warning-budget.json", "utf8"));
const nextCli = path.resolve("node_modules", "next", "dist", "bin", "next");

const result = spawnSync(process.execPath, [nextCli, "lint"], {
  cwd: process.cwd(),
  encoding: "utf8",
  shell: false,
});

const output = `${result.stdout || ""}${result.stderr || ""}`;

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

if (result.status !== 0) {
  process.stdout.write(output);
  process.exit(result.status || 1);
}

const warningCount = (output.match(/Warning:/g) || []).length;
const maxWarnings = Number(budget.maxWarnings);

console.log(`ESLint warning budget: ${warningCount}/${maxWarnings}`);

if (!Number.isFinite(maxWarnings)) {
  console.error("Invalid lint-warning-budget.json: maxWarnings must be a number.");
  process.exit(1);
}

if (warningCount > maxWarnings) {
  console.error(
    `ESLint warning budget exceeded by ${warningCount - maxWarnings}. Fix the new warning(s) or explicitly lower/refresh the reviewed budget.`,
  );
  process.exit(1);
}
