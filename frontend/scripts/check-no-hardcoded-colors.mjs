#!/usr/bin/env node
/**
 * 守卫：禁止已 token 化的模块出现硬编码主题色，防止 token 化成果回潮。
 *
 * 覆盖范围：整个 frontend/src/components 树（前端已 100% token 化，
 * 守卫防任何模块回潮）。
 *
 * 禁止（应改语义 token，否则浅色主题无法正确翻转）：
 *   - arbitrary hex 颜色：bg-[#646cff] / text-[#141416] / border-t-[#7a82ff] …
 *   - white + 透明度：text-white/40 / bg-white/[0.04] / border-white/15 …  → 用 foreground/alpha
 *
 * 允许（语义合理，不报）：
 *   - 纯 text-white（无透明度）：彩色底（bg-primary / bg-emerald 等）上的白字
 *   - black + 透明度：功能性遮罩（图片角标、modal backdrop、图片底部渐变）
 *   - rgba(...) in arbitrary（如 shadow / border 发光，不含 #，本就不匹配 hex 规则）
 *
 * 用法：node scripts/check-no-hardcoded-colors.mjs   （从 frontend/ 运行；或 npm run check:colors）
 * 退出码非 0 表示有违规，可接入 CI / pre-commit。
 */
import { readFileSync, readdirSync, existsSync, statSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const COMPONENTS = [
  path.resolve(__dirname, '../src/components'),
  path.resolve(process.cwd(), 'src/components'),
].find(existsSync);
if (!COMPONENTS) {
  console.error('✘ 找不到 components 目录');
  process.exit(2);
}

const BANNED = [
  { re: /\[#[0-9a-fA-F]{3,8}\]/, msg: 'arbitrary hex（改用语义 token：bg-primary / bg-surface / text-foreground …）' },
  { re: /\b(?:bg|text|border|from|to|via|ring|divide|placeholder|outline|caret|decoration|accent|ring-offset)-white\/(?:\[[0-9.]+\]|\d+)/, msg: 'white/alpha（改用 foreground/alpha，浅色主题才能正确翻转）' },
];

function collectTsx(dir) {
  if (!existsSync(dir)) return [];
  const out = [];
  for (const e of readdirSync(dir)) {
    const p = path.join(dir, e);
    if (statSync(p).isDirectory()) out.push(...collectTsx(p));
    else if (e.endsWith('.tsx')) out.push(p);
  }
  return out;
}

let violations = 0;
// 整个 components 树（全前端已 token 化；守卫防任何模块回潮）
for (const fp of collectTsx(COMPONENTS).sort()) {
    const rel = fp.replace(COMPONENTS + '/', '');
    const lines = readFileSync(fp, 'utf8').split('\n');
    lines.forEach((line, i) => {
      for (const { re, msg } of BANNED) {
        const g = new RegExp(re.source, 'g');
        const found = line.match(g);
        if (found) {
          violations += found.length;
          console.error(`  ${rel}:${i + 1}  ${found.join('  ')}  — ${msg}`);
        }
      }
    });
}

if (violations > 0) {
  console.error(`\n✘ 已 token 化模块存在 ${violations} 处硬编码颜色，请改用语义 token。`);
  process.exit(1);
}
console.log('✓ 全 components 树：无违规硬编码颜色（前端已 100% token 化）。');
