import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const frontendDir = path.resolve(__dirname, "..");
const rootDir = path.resolve(frontendDir, "..");
const outputPath = path.join(frontendDir, "src", "lib", "generated", "openapi-types.ts");
const python = process.env.PYTHON || process.env.LUMENX_PYTHON || "python";

const dumpOpenApiScript = `
import json
from src.apps.comic_gen.api import app
print(json.dumps(app.openapi(), ensure_ascii=False))
`;

function readOpenApiSchema() {
  const result = spawnSync(python, ["-c", dumpOpenApiScript], {
    cwd: rootDir,
    encoding: "utf-8",
    env: {
      ...process.env,
      LUMENX_SKIP_BROWSER_OPEN: "1",
    },
  });

  if (result.status !== 0) {
    throw new Error(
      [
        "Failed to load FastAPI OpenAPI schema.",
        result.stderr.trim(),
        result.stdout.trim(),
      ].filter(Boolean).join("\n"),
    );
  }

  const stdout = result.stdout.trim();
  const jsonStart = stdout.indexOf("{");
  if (jsonStart < 0) {
    throw new Error("OpenAPI generator did not receive JSON output from Python.");
  }

  return JSON.parse(stdout.slice(jsonStart));
}

function toTypeName(ref) {
  const rawName = String(ref).split("/").pop() || "Unknown";
  return rawName.replace(/[^a-zA-Z0-9_$]/g, "_");
}

function isIdentifier(value) {
  return /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(value);
}

function propertyName(value) {
  return isIdentifier(value) ? value : JSON.stringify(value);
}

function literal(value) {
  return JSON.stringify(value);
}

function primitiveType(type) {
  switch (type) {
    case "string":
      return "string";
    case "integer":
    case "number":
      return "number";
    case "boolean":
      return "boolean";
    case "null":
      return "null";
    default:
      return "unknown";
  }
}

function schemaToType(schema) {
  if (!schema || typeof schema !== "object") return "unknown";

  if (schema.$ref) return toTypeName(schema.$ref);

  if (Array.isArray(schema.enum)) {
    return schema.enum.map((item) => literal(item)).join(" | ") || "never";
  }

  if (Array.isArray(schema.anyOf)) {
    return [...new Set(schema.anyOf.map((item) => schemaToType(item)))].join(" | ");
  }

  if (Array.isArray(schema.oneOf)) {
    return [...new Set(schema.oneOf.map((item) => schemaToType(item)))].join(" | ");
  }

  if (Array.isArray(schema.allOf)) {
    return schema.allOf.map((item) => schemaToType(item)).join(" & ");
  }

  if (Array.isArray(schema.type)) {
    return [...new Set(schema.type.map((item) => primitiveType(item)))].join(" | ");
  }

  if (schema.type === "array") {
    return `Array<${schemaToType(schema.items)}>`;
  }

  if (schema.type === "object" || schema.properties) {
    const properties = schema.properties || {};
    const propertyEntries = Object.entries(properties);
    const additional = schema.additionalProperties;

    if (!propertyEntries.length) {
      if (additional && additional !== true) {
        return `Record<string, ${schemaToType(additional)}>`;
      }
      return "Record<string, unknown>";
    }

    const required = new Set(schema.required || []);
    const lines = propertyEntries.map(([key, value]) => {
      const optional = required.has(key) ? "" : "?";
      return `${propertyName(key)}${optional}: ${schemaToType(value)};`;
    });
    return `{\n${lines.map((line) => `  ${line}`).join("\n")}\n}`;
  }

  return primitiveType(schema.type);
}

function renderSchema(name, schema) {
  const typeName = name.replace(/[^a-zA-Z0-9_$]/g, "_");
  const properties = schema?.properties;

  if ((schema?.type === "object" || properties) && properties) {
    const required = new Set(schema.required || []);
    const lines = Object.entries(properties).map(([key, value]) => {
      const optional = required.has(key) ? "" : "?";
      return `  ${propertyName(key)}${optional}: ${schemaToType(value)};`;
    });
    return `export interface ${typeName} {\n${lines.join("\n")}\n}`;
  }

  return `export type ${typeName} = ${schemaToType(schema)};`;
}

function main() {
  const schema = readOpenApiSchema();
  const schemas = schema.components?.schemas || {};
  const rendered = Object.keys(schemas)
    .sort((left, right) => left.localeCompare(right))
    .map((name) => renderSchema(name, schemas[name]))
    .join("\n\n");

  const content = [
    "/* eslint-disable */",
    "// This file is generated from FastAPI OpenAPI. Do not edit by hand.",
    "// Run `npm -C frontend run generate:api-types` after backend schema changes.",
    "",
    rendered,
    "",
  ].join("\n");

  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, content, "utf-8");
  console.log(`Generated ${path.relative(rootDir, outputPath).replaceAll(path.sep, "/")}`);
}

main();
