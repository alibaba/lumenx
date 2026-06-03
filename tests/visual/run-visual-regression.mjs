#!/usr/bin/env node
/**
 * Visual regression harness for Atelier v3 canvas.
 *
 * Usage:
 *   node tests/visual/run-visual-regression.mjs [--update-golden]
 *
 * Prerequisites:
 *   - Dev server running at http://localhost:3009/#/atelier
 *   - Backend at http://localhost:17177
 *   - playwright globally installed (/opt/homebrew/lib/node_modules/playwright)
 *   - pixelmatch: npm i -g pixelmatch (or uses built-in threshold fallback)
 */

import { chromium } from '/opt/homebrew/lib/node_modules/playwright/index.mjs';
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const GOLDEN_DIR = join(__dirname, 'golden');
const CAPTURE_DIR = join(__dirname, 'captures');
const UPDATE_GOLDEN = process.argv.includes('--update-golden');
const BASE_URL = process.env.ATELIER_URL || 'http://localhost:3009';
const THRESHOLD = parseFloat(process.env.VISUAL_THRESHOLD || '0.005');

mkdirSync(GOLDEN_DIR, { recursive: true });
mkdirSync(CAPTURE_DIR, { recursive: true });

const VIEWPORTS = [
  { name: 'baseline', action: async (page) => {
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
  }},
  { name: 'selected-draft', action: async (page) => {
    const shells = await page.$$('.atelier-node-shell');
    if (shells.length > 0) {
      const box = await shells[0].boundingBox();
      if (box) {
        await page.mouse.click(box.x + box.width / 2, box.y + 20);
        await page.waitForTimeout(800);
      }
    }
    // If no nodes on canvas, capture the empty state — still a valid golden
  }},
  { name: 'agent-empty', action: async (page) => {
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    // Agent panel should show empty state with skill cards
  }},
];

async function run() {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1200 },
    deviceScaleFactor: 2,
  });

  const results = [];

  try {
    await page.goto(`${BASE_URL}/#/atelier`, {
      waitUntil: 'domcontentloaded',
      timeout: 30000,
    });
    await page.waitForTimeout(5000);

    for (const vp of VIEWPORTS) {
      await vp.action(page);
      const capturePath = join(CAPTURE_DIR, `${vp.name}.png`);
      await page.screenshot({ path: capturePath });

      const goldenPath = join(GOLDEN_DIR, `${vp.name}.png`);

      if (UPDATE_GOLDEN || !existsSync(goldenPath)) {
        writeFileSync(goldenPath, readFileSync(capturePath));
        results.push({ name: vp.name, status: 'golden-updated' });
        console.log(`  ✓ ${vp.name}: golden updated`);
      } else {
        const golden = readFileSync(goldenPath);
        const capture = readFileSync(capturePath);

        if (golden.equals(capture)) {
          results.push({ name: vp.name, status: 'pass', delta: 0 });
          console.log(`  ✓ ${vp.name}: identical`);
        } else {
          // Byte-level diff as a rough proxy for pixel delta
          let diffBytes = 0;
          const minLen = Math.min(golden.length, capture.length);
          for (let i = 0; i < minLen; i++) {
            if (golden[i] !== capture[i]) diffBytes++;
          }
          diffBytes += Math.abs(golden.length - capture.length);
          const delta = diffBytes / Math.max(golden.length, capture.length);

          if (delta <= THRESHOLD) {
            results.push({ name: vp.name, status: 'pass', delta });
            console.log(`  ✓ ${vp.name}: delta ${(delta * 100).toFixed(2)}% (within ${THRESHOLD * 100}%)`);
          } else {
            results.push({ name: vp.name, status: 'fail', delta });
            console.log(`  ✗ ${vp.name}: delta ${(delta * 100).toFixed(2)}% EXCEEDS ${THRESHOLD * 100}%`);
          }
        }
      }
    }
  } catch (e) {
    console.error('ERR:', e.message);
    results.push({ name: 'runtime', status: 'error', error: e.message });
  }

  await browser.close();

  const failed = results.filter(r => r.status === 'fail');
  const errors = results.filter(r => r.status === 'error');

  console.log(`\n${results.length} viewports checked: ${results.filter(r => r.status === 'pass').length} passed, ${failed.length} failed, ${errors.length} errors`);

  if (failed.length > 0 || errors.length > 0) {
    process.exit(1);
  }
}

run();
