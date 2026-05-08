const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const root = path.join(__dirname, '..');
const venv = path.join(root, '.venv');

console.log('[setup] Checking environment...');

if (process.env.CI || process.env.LUMENX_SKIP_DEV_SETUP === '1') {
  console.log('[setup] CI/skip flag detected; skipping interactive dev setup.');
  process.exit(0);
}

// 1. Setup Python venv if missing
if (!fs.existsSync(venv)) {
  console.log('[setup] Creating Python virtual environment...');
  try {
    execSync('python3 -m venv .venv || python -m venv .venv', { stdio: 'inherit', cwd: root });

    const pip = os.platform() === 'win32'
      ? path.join(venv, 'Scripts', 'pip')
      : path.join(venv, 'bin', 'pip');

    console.log('[setup] Installing Python dependencies...');
    execSync(`${pip} install -e .`, { stdio: 'inherit', cwd: root });
  } catch (e) {
    console.error('[setup] Failed to setup venv:', e.message);
  }
}

// 2. Setup Frontend dependencies if missing
const frontendModules = path.join(root, 'frontend', 'node_modules');
if (!fs.existsSync(frontendModules)) {
  console.log('[setup] Installing frontend dependencies...');
  execSync('npm install', { stdio: 'inherit', cwd: path.join(root, 'frontend') });
}

console.log('[setup] Done.');
