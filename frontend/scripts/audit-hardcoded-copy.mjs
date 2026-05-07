import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const strict = process.argv.includes("--strict");
const rootDir = path.resolve(process.cwd(), "src");

const COPY_ATTRS = new Set([
  "title",
  "placeholder",
  "alt",
  "aria-label",
  "aria-placeholder",
  "label",
  "helperText",
  "emptyText",
  "tooltip",
]);

const IGNORE_ATTRS = new Set([
  "className",
  "class",
  "id",
  "htmlFor",
  "type",
  "accept",
  "src",
  "href",
  "key",
  "rel",
  "target",
  "role",
  "name",
  "value",
  "mode",
  "variant",
  "size",
  "kind",
  "color",
  "icon",
  "method",
  "preload",
]);

const IGNORE_PROPERTY_KEYS = new Set([
  ...IGNORE_ATTRS,
  "backgroundImage",
  "maskImage",
  "gridTemplateColumns",
  "gridTemplateRows",
]);

const FILE_IGNORE_SEGMENTS = [
  `${path.sep}__tests__${path.sep}`,
  `${path.sep}lib${path.sep}i18n${path.sep}`,
];

const FILE_IGNORE_PATTERNS = [
  /\.test\.(ts|tsx)$/i,
  /\.spec\.(ts|tsx)$/i,
];

const STRING_ALLOWLIST = [
  /^use client$/,
  /^use server$/,
  /^https?:\/\//i,
  /^\/[A-Za-z0-9/_:.-]*$/,
  /^([A-Za-z]:)?[\\/]/,
  /^[A-Za-z0-9_.-]+$/,
  /^[A-Z0-9]{2,8}$/,
  /^[a-z]+(?:,[a-z]+)+$/i,
  /^#[A-Za-z0-9_-]+$/,
  /^@[A-Za-z0-9_-]+$/,
  /^(?:&[a-z]+;)+$/i,
  /^\.{3,}$/,
  /^[a-z0-9_-]+=$/i,
];

const PROMPT_ALLOWLIST = [
  /STRICTLY MAINTAIN/i,
  /^Full body character design of /,
  /^Character Reference Sheet for /,
  /^Close-up portrait of the SAME character /,
  /^Full-body character reference video\./,
  /^High-fidelity portrait video reference\./,
  /^Cinematic shot of /,
  /^Dialogue context:/,
  /^\(camera:/,
  /^\[character\d+:/,
];

const findings = [];

function collectFiles(dir) {
  const files = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectFiles(fullPath));
      continue;
    }

    if (!/\.(ts|tsx)$/.test(entry.name) || /\.d\.ts$/.test(entry.name)) {
      continue;
    }

    if (FILE_IGNORE_PATTERNS.some((pattern) => pattern.test(entry.name))) {
      continue;
    }

    if (FILE_IGNORE_SEGMENTS.some((segment) => fullPath.includes(segment))) {
      continue;
    }

    files.push(fullPath);
  }
  return files;
}

function normalizeText(value) {
  return value.replace(/\s+/g, " ").trim();
}

function truncate(value, max = 90) {
  return value.length <= max ? value : `${value.slice(0, max - 1)}...`;
}

function getPropertyNameText(name) {
  if (!name) return "";
  if (ts.isIdentifier(name) || ts.isStringLiteral(name) || ts.isNumericLiteral(name)) {
    return name.text;
  }
  return "";
}

function getCallExpressionName(expression) {
  if (ts.isIdentifier(expression)) {
    return expression.text;
  }

  if (ts.isPropertyAccessExpression(expression)) {
    return `${getCallExpressionName(expression.expression)}.${expression.name.text}`;
  }

  return "";
}

function extractTemplateText(node) {
  if (ts.isTemplateExpression(node)) {
    return normalizeText([
      node.head.text,
      ...node.templateSpans.map((span) => span.literal.text),
    ].join(" "));
  }

  if (ts.isNoSubstitutionTemplateLiteral(node)) {
    return normalizeText(node.text);
  }

  return "";
}

function containsLanguageText(value) {
  return /[\u4e00-\u9fff]/.test(value) || /[A-Za-z]{2,}/.test(value);
}

function looksLikeCopy(value) {
  const text = normalizeText(value);
  if (!text || !containsLanguageText(text)) {
    return false;
  }

  if (PROMPT_ALLOWLIST.some((pattern) => pattern.test(text))) {
    return false;
  }

  if (STRING_ALLOWLIST.some((pattern) => pattern.test(text))) {
    return false;
  }

  if (/^[A-Za-z0-9_:-]+$/.test(text) && !/[\u4e00-\u9fff]/.test(text)) {
    return false;
  }

  if (!/[\u4e00-\u9fff]/.test(text)) {
    const tokens = text.split(/\s+/);
    if (tokens.length > 1 && tokens.every((token) => /^[!a-z0-9:[\]()./%#,_-]+$/i.test(token))) {
      return false;
    }

    if (/(linear-gradient|radial-gradient|conic-gradient|rgba?\(|hsla?\(|var\(--)/i.test(text)) {
      return false;
    }
  }

  if (/\/|\\/.test(text) && !/[\u4e00-\u9fff]/.test(text)) {
    return false;
  }

  return /[\u4e00-\u9fff]/.test(text) || /[A-Za-z]{2,}.*[A-Za-z]{2,}/.test(text);
}

function shouldSkipByContext(sourceFile, node, text) {
  const sourceText = node.getText(sourceFile);
  const parent = node.parent;

  if (/\$\{\s*(copy|messages)\./.test(sourceText) || /\bt\(/.test(sourceText)) {
    return true;
  }

  let current = parent;
  while (current && current !== sourceFile) {
    if (ts.isJsxAttribute(current)) {
      const attrName = getPropertyNameText(current.name);
      if (IGNORE_ATTRS.has(attrName)) {
        return true;
      }
      if (COPY_ATTRS.has(attrName)) {
        return !looksLikeCopy(text);
      }
    }

    if (ts.isPropertyAssignment(current)) {
      const key = getPropertyNameText(current.name);
      if (IGNORE_PROPERTY_KEYS.has(key)) {
        return true;
      }
    }

    current = current.parent;
  }

  if (
    ts.isImportDeclaration(parent) ||
    ts.isExportDeclaration(parent) ||
    ts.isExternalModuleReference(parent) ||
    ts.isLiteralTypeNode(parent)
  ) {
    return true;
  }

  if (ts.isJsxAttribute(parent)) {
    const attrName = getPropertyNameText(parent.name);
    if (IGNORE_ATTRS.has(attrName)) {
      return true;
    }
    if (COPY_ATTRS.has(attrName)) {
      return !looksLikeCopy(text);
    }
  }

  if (ts.isPropertyAssignment(parent)) {
    const key = getPropertyNameText(parent.name);
    if (IGNORE_ATTRS.has(key)) {
      return true;
    }
  }

  if (ts.isCallExpression(parent)) {
    const callee = getCallExpressionName(parent.expression);
    if (callee === "alert" || callee === "confirm" || callee === "prompt") {
      return false;
    }
    if (callee.startsWith("console.")) {
      return true;
    }
  }

  if (ts.isJsxExpression(parent) && ts.isJsxAttribute(parent.parent)) {
    const attrName = getPropertyNameText(parent.parent.name);
    if (IGNORE_ATTRS.has(attrName)) {
      return true;
    }
    if (COPY_ATTRS.has(attrName)) {
      return !looksLikeCopy(text);
    }
  }

  return !looksLikeCopy(text);
}

function pushFinding(sourceFile, node, text, kind) {
  const start = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
  findings.push({
    file: path.relative(process.cwd(), sourceFile.fileName),
    line: start.line + 1,
    column: start.character + 1,
    kind,
    text: truncate(normalizeText(text)),
  });
}

function inspectSourceFile(filePath) {
  const text = fs.readFileSync(filePath, "utf8");
  const sourceFile = ts.createSourceFile(
    filePath,
    text,
    ts.ScriptTarget.Latest,
    true,
    filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );

  function visit(node) {
    if (ts.isJsxText(node)) {
      const value = normalizeText(node.text);
      if (value && looksLikeCopy(value)) {
        pushFinding(sourceFile, node, value, "jsx-text");
      }
    }

    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      const value = normalizeText(node.text);
      if (value && !shouldSkipByContext(sourceFile, node, value)) {
        pushFinding(sourceFile, node, value, "string");
      }
    }

    if (ts.isTemplateExpression(node)) {
      const value = extractTemplateText(node);
      if (value && !shouldSkipByContext(sourceFile, node, value)) {
        pushFinding(sourceFile, node, value, "template");
      }
    }

    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
}

for (const file of collectFiles(rootDir)) {
  inspectSourceFile(file);
}

if (findings.length === 0) {
  console.log("未发现疑似裸文案。");
  process.exit(0);
}

console.log(`发现 ${findings.length} 条疑似裸文案：`);
for (const finding of findings) {
  console.log(`- ${finding.file}:${finding.line}:${finding.column} [${finding.kind}] ${finding.text}`);
}

if (!strict) {
  console.log("\n提示：默认只巡检不阻断。如需在 CI 中阻断，请追加 --strict。");
}

process.exit(strict ? 1 : 0);
