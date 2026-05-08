const { execFileSync, spawn } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const root = path.join(__dirname, '..');
const backendRuntimePath = path.join(root, 'tmp', 'lumenx-backend-dev.json');
const frontendRuntimePath = path.join(root, 'tmp', 'lumenx-frontend-dev.json');
const managedE2eOutputDir = path.join(root, 'tmp', `e2e-output-${process.pid}`);
const e2eOutputDir = process.env.LUMENX_E2E_OUTPUT_DIR
  ? path.resolve(process.env.LUMENX_E2E_OUTPUT_DIR)
  : managedE2eOutputDir;
const keepE2eOutputAlways = process.env.LUMENX_KEEP_E2E_OUTPUT === '1';
const keepE2eOutputOnFailure = process.env.LUMENX_KEEP_E2E_OUTPUT_ON_FAILURE === '1';
const browserSmokeScenario = process.env.LUMENX_BROWSER_SMOKE_SCENARIO || 'full';
const browserSmokeSummaryPath = process.env.LUMENX_E2E_SUMMARY_PATH
  ? path.resolve(process.env.LUMENX_E2E_SUMMARY_PATH)
  : path.join(e2eOutputDir, `browser-smoke-${browserSmokeScenario}.json`);
const npmCmd = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const devCommand = process.platform === 'win32' ? 'cmd.exe' : npmCmd;
const devArgs = process.platform === 'win32'
  ? ['/d', '/s', '/c', 'npm run dev']
  : ['run', 'dev'];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function occupyPort(port) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', (error) => {
      if (error.code === 'EADDRINUSE') {
        console.log(`[ci-smoke] Port ${port} was already occupied; using it as the conflict.`);
        resolve(null);
        return;
      }
      reject(error);
    });
    server.listen(port, '127.0.0.1', () => resolve(server));
  });
}

function readRuntime(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  } catch {
    return null;
  }
}

async function waitForRuntime(filePath, expectedApp, devProcess, timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (devProcess.exitCode !== null) {
      throw new Error(`npm run dev exited early with code ${devProcess.exitCode}`);
    }

    const info = readRuntime(filePath);
    if (info && info.app === expectedApp && info.url) {
      return info;
    }
    await sleep(500);
  }

  throw new Error(`Timed out waiting for ${expectedApp} runtime file: ${filePath}`);
}

async function fetchText(url, timeoutMs = 15000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) {
      throw new Error(`${url} returned HTTP ${response.status}`);
    }
    return await response.text();
  } finally {
    clearTimeout(timeout);
  }
}

async function waitForUrlText(
  url,
  expectedSubstring,
  devProcess,
  timeoutMs = 120000,
) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;

  while (Date.now() < deadline) {
    if (devProcess.exitCode !== null) {
      throw new Error(`npm run dev exited early with code ${devProcess.exitCode}`);
    }

    try {
      const text = await fetchText(url, 5000);
      if (text.includes(expectedSubstring)) {
        return text;
      }
      lastError = new Error(`Response from ${url} did not contain ${expectedSubstring}`);
    } catch (error) {
      lastError = error;
    }

    await sleep(1000);
  }

  throw lastError || new Error(`Timed out waiting for ${url}`);
}

function killProcessTree(child) {
  if (!child || child.exitCode !== null) return;
  try {
    if (process.platform === 'win32') {
      execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      process.kill(-child.pid, 'SIGTERM');
    }
  } catch {
    try {
      child.kill('SIGTERM');
    } catch {
      // Best effort.
    }
  }
}

function runBrowserSmoke(frontendInfo, backendInfo) {
  if (process.env.LUMENX_SKIP_BROWSER_E2E === '1') {
    console.log('[ci-smoke] Browser E2E smoke skipped by LUMENX_SKIP_BROWSER_E2E=1.');
    return;
  }

  execFileSync(process.execPath, [path.join(root, 'frontend', 'scripts', 'browser-smoke.mjs')], {
    cwd: root,
    stdio: 'inherit',
    env: {
      ...process.env,
      LUMENX_E2E_FRONTEND_URL: frontendInfo.url,
      LUMENX_E2E_BACKEND_URL: backendInfo.url,
      LUMENX_E2E_HEADLESS: process.env.LUMENX_E2E_HEADLESS || '1',
      LUMENX_BROWSER_SMOKE_SCENARIO: browserSmokeScenario,
      LUMENX_E2E_SUMMARY_PATH: browserSmokeSummaryPath,
      LUMENX_OUTPUT_DIR: e2eOutputDir,
      LUMENX_E2E_ARTIFACT_DIR: e2eOutputDir,
      LUMENX_LOCAL_VIDEO_SMOKE: process.env.LUMENX_LOCAL_VIDEO_SMOKE || '1',
      LUMENX_E2E_OUTPUT_DIR: e2eOutputDir,
    },
  });
}

function appendLog(logs, prefix, chunk) {
  logs.push(`${prefix}${chunk.toString()}`);
  while (logs.join('').length > 12000) {
    logs.shift();
  }
}

async function main() {
  fs.rmSync(backendRuntimePath, { force: true });
  fs.rmSync(frontendRuntimePath, { force: true });
  if (!process.env.LUMENX_E2E_OUTPUT_DIR && !keepE2eOutputAlways) {
    fs.rmSync(e2eOutputDir, { recursive: true, force: true });
  }
  fs.mkdirSync(e2eOutputDir, { recursive: true });

  const blockers = [
    await occupyPort(18177),
    await occupyPort(3000),
  ].filter(Boolean);
  const logs = [];

  const devProcess = spawn(devCommand, devArgs, {
    cwd: root,
    detached: process.platform !== 'win32',
    env: {
      ...process.env,
      CI: '1',
      LUMENX_SKIP_DEV_SETUP: '1',
      LUMENX_SKIP_BROWSER_OPEN: '1',
      LUMENX_OUTPUT_DIR: e2eOutputDir,
      LUMENX_LOCAL_VIDEO_SMOKE: process.env.LUMENX_LOCAL_VIDEO_SMOKE || '1',
      PYTHON: process.env.PYTHON || 'python',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  devProcess.stdout.on('data', (chunk) => appendLog(logs, '[dev] ', chunk));
  devProcess.stderr.on('data', (chunk) => appendLog(logs, '[dev:err] ', chunk));

  let failed = false;
  try {
    const backendInfo = await waitForRuntime(
      backendRuntimePath,
      'LumenX Studio API',
      devProcess,
    );
    const frontendInfo = await waitForRuntime(
      frontendRuntimePath,
      'LumenX Studio',
      devProcess,
    );

    if (Number(backendInfo.port) === Number(backendInfo.requestedPort)) {
      throw new Error(`Backend did not avoid occupied port ${backendInfo.requestedPort}`);
    }
    if (Number(frontendInfo.port) === Number(frontendInfo.requestedPort)) {
      throw new Error(`Frontend did not avoid occupied port ${frontendInfo.requestedPort}`);
    }
    if (String(frontendInfo.backendPort) !== String(backendInfo.port)) {
      throw new Error(
        `Frontend backend port ${frontendInfo.backendPort} did not match backend ${backendInfo.port}`,
      );
    }

    await waitForUrlText(`${backendInfo.url}/openapi.json`, 'AI Comic Gen API', devProcess);
    await waitForUrlText(frontendInfo.url, 'LumenX Studio', devProcess);
    runBrowserSmoke(frontendInfo, backendInfo);

    console.log(`[ci-smoke] Backend:  ${backendInfo.url}`);
    console.log(`[ci-smoke] Frontend: ${frontendInfo.url}`);
    console.log('[ci-smoke] Port conflict + root npm run dev + frontend browser smoke passed.');
  } catch (error) {
    failed = true;
    console.error(`[ci-smoke] ${error.message}`);
    console.error(logs.join(''));
    throw error;
  } finally {
    killProcessTree(devProcess);
    await Promise.all(
      blockers.map(
        (server) => new Promise((resolve) => server.close(resolve)),
      ),
    );
    fs.rmSync(backendRuntimePath, { force: true });
    fs.rmSync(frontendRuntimePath, { force: true });
    const shouldKeepE2eOutput = keepE2eOutputAlways || (failed && keepE2eOutputOnFailure);
    if (shouldKeepE2eOutput) {
      console.log(`[ci-smoke] Preserving E2E output at ${e2eOutputDir}`);
    } else if (!process.env.LUMENX_E2E_OUTPUT_DIR) {
      fs.rmSync(e2eOutputDir, { recursive: true, force: true });
    }
  }
}

main().catch((error) => {
  console.error('[ci-smoke] Startup smoke failed:', error);
  process.exit(1);
});
