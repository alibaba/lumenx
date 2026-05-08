const { spawn } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');
const os = require('os');
const { getApiHost, getApiPort } = require('./runtime-config');

const isWin = os.platform() === 'win32';
const venvPythonPath = isWin
  ? path.join(__dirname, '..', '.venv', 'Scripts', 'python')
  : path.join(__dirname, '..', '.venv', 'bin', 'python');
const pythonOverride = process.env.LUMENX_PYTHON || process.env.PYTHON;
const pythonPath = pythonOverride
  ? pythonOverride
  : fs.existsSync(venvPythonPath)
  ? venvPythonPath
  : 'python';

const root = path.join(__dirname, '..');
const runtimeDir = path.join(root, 'tmp');
const runtimeInfoPath = path.join(runtimeDir, 'lumenx-backend-dev.json');

const env = {
  ...process.env,
  NO_PROXY: '*.aliyuncs.com,localhost,127.0.0.1',
  no_proxy: '*.aliyuncs.com,localhost,127.0.0.1'
};

const backendHost = getApiHost();
const preferredPort = Number.parseInt(getApiPort(), 10);

function displayHost(host) {
  return host === '0.0.0.0' || host === '::' || host === '[::]' ? '127.0.0.1' : host;
}

function checkPortAvailable(port, host) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.once('listening', () => {
      server.close(() => resolve(true));
    });
    server.listen(port, host);
  });
}

async function findAvailablePort(startPort, host) {
  for (let port = startPort; port < startPort + 40; port += 1) {
    if (await checkPortAvailable(port, host)) return port;
  }
  throw new Error(`No available backend port found from ${startPort} to ${startPort + 39}`);
}

function writeRuntimeInfo(port) {
  const publicHost = displayHost(backendHost);
  const apiUrl = `http://${publicHost}:${port}`;

  fs.mkdirSync(runtimeDir, { recursive: true });
  fs.writeFileSync(
    runtimeInfoPath,
    JSON.stringify(
      {
        app: 'LumenX Studio API',
        url: apiUrl,
        host: backendHost,
        publicHost,
        port,
        requestedPort: preferredPort,
        launcherPid: process.pid,
        startedAt: new Date().toISOString(),
      },
      null,
      2,
    ),
    'utf-8',
  );

  return apiUrl;
}

function cleanupRuntimeFile() {
  try {
    const info = JSON.parse(fs.readFileSync(runtimeInfoPath, 'utf-8'));
    if (info.launcherPid === process.pid) {
      fs.rmSync(runtimeInfoPath, { force: true });
    }
  } catch {
    // Best-effort cleanup only.
  }
}

async function main() {
  fs.rmSync(runtimeInfoPath, { force: true });

  const backendPort = await findAvailablePort(preferredPort, backendHost);
  const apiUrl = writeRuntimeInfo(backendPort);

  if (backendPort !== preferredPort) {
    console.warn(`[backend] Port ${preferredPort} is in use. LumenX Studio API will run on ${apiUrl}`);
  } else {
    console.log(`[backend] LumenX Studio API will run on ${apiUrl}`);
  }
  console.log(`[backend] Python: ${pythonPath}`);

  const backend = spawn(pythonPath, [
    '-m', 'uvicorn', 'src.apps.comic_gen.api:app',
    '--reload', '--port', String(backendPort), '--host', backendHost
  ], {
    stdio: 'inherit',
    env: {
      ...env,
      LUMENX_API_HOST: backendHost,
      LUMENX_API_PORT: String(backendPort),
    }
  });

  const stopBackend = () => {
    if (!backend.killed) backend.kill();
  };

  process.on('SIGINT', stopBackend);
  process.on('SIGTERM', stopBackend);
  backend.on('exit', (code) => {
    cleanupRuntimeFile();
    process.exit(code || 0);
  });
}

main().catch((error) => {
  console.error(`[backend] Failed to start LumenX Studio API: ${error.message}`);
  process.exit(1);
});
