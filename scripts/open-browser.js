const { exec } = require('child_process');
const fs = require('fs');
const path = require('path');
const { getApiPort } = require('./runtime-config');

const root = path.join(__dirname, '..');
const runtimeInfoPath = path.join(root, 'tmp', 'lumenx-frontend-dev.json');
const backendPort = getApiPort();
const runtimeMaxAgeMs = 2 * 60 * 1000;
const openBrowserStartedAt = Date.now();

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isProcessAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function isFreshRuntime(info) {
  const startedAt = Date.parse(info.startedAt || '');
  if (!Number.isFinite(startedAt)) return false;
  if (startedAt < openBrowserStartedAt - runtimeMaxAgeMs) return false;
  if (Date.now() - startedAt > runtimeMaxAgeMs) return false;
  if (Number.isInteger(info.launcherPid) && !isProcessAlive(info.launcherPid)) return false;
  return true;
}

async function readFrontendInfo(maxWaitMs = 60000) {
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    try {
      const info = JSON.parse(fs.readFileSync(runtimeInfoPath, 'utf-8'));
      if (info && info.app === 'LumenX Studio' && info.url && isFreshRuntime(info)) return info;
    } catch {
      // The frontend process may not have written its runtime file yet.
    }
    await sleep(500);
  }
  return null;
}

async function openReadyBrowser() {
  const info = await readFrontendInfo();
  if (!info) {
    console.warn('\n  LumenX Studio frontend did not publish a fresh runtime URL.');
    console.warn('  Browser was not opened to avoid showing another app on a reused port.');
    console.warn('  Check the frontend terminal for the confirmed LumenX URL.\n');
    return;
  }

  const frontendUrl = info.url;
  const backendUrl = info.backendUrl || `http://127.0.0.1:${backendPort}`;

  console.log('\n  LumenX Studio Ready');
  console.log(`  Frontend: ${frontendUrl}`);
  console.log(`  Backend:  ${backendUrl}`);
  console.log('  Press Ctrl+C to stop all services.\n');

  if (process.env.CI || process.env.LUMENX_SKIP_BROWSER_OPEN === '1') {
    console.log('  Browser launch skipped for CI/non-interactive mode.\n');
    return;
  }

  const cmd = process.platform === 'win32' ? `start "" "${frontendUrl}"`
    : process.platform === 'darwin' ? `open "${frontendUrl}"`
    : `xdg-open "${frontendUrl}"`;
  exec(cmd);
}

setTimeout(() => {
  openReadyBrowser().catch((error) => {
    console.error(`[open] Failed to open LumenX Studio: ${error.message}`);
  });
}, 1000);
