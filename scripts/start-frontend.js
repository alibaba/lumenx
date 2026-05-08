const { spawn } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');
const { getApiPort, getFrontendPort } = require('./runtime-config');

const root = path.join(__dirname, '..');
const frontendDir = path.join(root, 'frontend');
const runtimeDir = path.join(root, 'tmp');
const runtimeInfoPath = path.join(runtimeDir, 'lumenx-frontend-dev.json');
const backendRuntimeInfoPath = path.join(runtimeDir, 'lumenx-backend-dev.json');
const host = '127.0.0.1';
const preferredPort = Number.parseInt(getFrontendPort(), 10);
const runtimeMaxAgeMs = 2 * 60 * 1000;
const frontendStartedAt = Date.now();
const backendRuntimeWaitMs = process.env.LUMENX_WAIT_FOR_BACKEND_RUNTIME === '1' ? 10000 : 1000;

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
  if (startedAt < frontendStartedAt - runtimeMaxAgeMs) return false;
  if (Date.now() - startedAt > runtimeMaxAgeMs) return false;
  if (Number.isInteger(info.launcherPid) && !isProcessAlive(info.launcherPid)) return false;
  return true;
}

async function readBackendInfo(maxWaitMs = backendRuntimeWaitMs) {
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    try {
      const info = JSON.parse(fs.readFileSync(backendRuntimeInfoPath, 'utf-8'));
      if (info && info.app === 'LumenX Studio API' && info.url && info.port && isFreshRuntime(info)) {
        return info;
      }
    } catch {
      // The backend process may not have written its runtime file yet.
    }
    await sleep(500);
  }
  return null;
}

function checkPortAvailable(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.once('listening', () => {
      server.close(() => resolve(true));
    });
    server.listen(port, host);
  });
}

async function findAvailablePort(startPort) {
  for (let port = startPort; port < startPort + 40; port += 1) {
    if (await checkPortAvailable(port)) return port;
  }
  throw new Error(`No available frontend port found from ${startPort} to ${startPort + 39}`);
}

async function main() {
  fs.rmSync(runtimeInfoPath, { force: true });

  const backendInfo = await readBackendInfo();
  const backendPort = String(backendInfo?.port || getApiPort());
  const backendUrl = backendInfo?.url || `http://127.0.0.1:${backendPort}`;
  const port = await findAvailablePort(preferredPort);
  const frontendUrl = `http://${host}:${port}`;

  fs.mkdirSync(runtimeDir, { recursive: true });
  fs.writeFileSync(
    runtimeInfoPath,
    JSON.stringify(
      {
        app: 'LumenX Studio',
        url: frontendUrl,
        host,
        port,
        requestedPort: preferredPort,
        backendUrl,
        backendPort,
        launcherPid: process.pid,
        startedAt: new Date().toISOString(),
      },
      null,
      2,
    ),
    'utf-8',
  );

  if (port !== preferredPort) {
    console.warn(`[frontend] Port ${preferredPort} is in use. LumenX Studio will run on ${frontendUrl}`);
  } else {
    console.log(`[frontend] LumenX Studio will run on ${frontendUrl}`);
  }
  console.log(`[frontend] Backend API: ${backendUrl}`);

  const nextBin = path.join(frontendDir, 'node_modules', 'next', 'dist', 'bin', 'next');
  const child = spawn(
    process.execPath,
    [nextBin, 'dev', '-H', host, '-p', String(port)],
    {
      cwd: frontendDir,
      stdio: 'inherit',
      env: {
        ...process.env,
        LUMENX_API_PORT: backendPort,
        NEXT_PUBLIC_LUMENX_API_PORT: backendPort,
        LUMENX_FRONTEND_PORT: String(port),
        NEXT_PUBLIC_LUMENX_FRONTEND_PORT: String(port),
        NO_PROXY: '*.aliyuncs.com,localhost,127.0.0.1',
        no_proxy: '*.aliyuncs.com,localhost,127.0.0.1',
      },
    },
  );

  const cleanupRuntimeFile = () => {
    try {
      const info = JSON.parse(fs.readFileSync(runtimeInfoPath, 'utf-8'));
      if (info.launcherPid === process.pid) {
        fs.rmSync(runtimeInfoPath, { force: true });
      }
    } catch {
      // Best-effort cleanup only.
    }
  };

  const stopChild = () => {
    if (!child.killed) child.kill();
  };

  process.on('SIGINT', stopChild);
  process.on('SIGTERM', stopChild);
  child.on('exit', (code) => {
    cleanupRuntimeFile();
    process.exit(code || 0);
  });
}

main().catch((error) => {
  console.error(`[frontend] Failed to start LumenX Studio: ${error.message}`);
  process.exit(1);
});
