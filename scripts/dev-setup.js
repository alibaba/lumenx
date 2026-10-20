const { execSync, execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const root = path.join(__dirname, '..');
const venv = path.join(root, '.venv');

console.log('[setup] Checking environment...');

// 1. Setup Python venv if missing
if (!fs.existsSync(venv)) {
  console.log('[setup] Creating Python virtual environment...');
  try {
    execSync('python3 -m venv .venv || python -m venv .venv', { stdio: 'inherit', cwd: root });

    const pip = os.platform() === 'win32'
      ? path.join(venv, 'Scripts', 'pip')
      : path.join(venv, 'bin', 'pip');

    console.log('[setup] Installing Python dependencies...');
    execFileSync(pip, ['install', '-e', '.'], { stdio: 'inherit', cwd: root });
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

// 3. Pre-download Demucs model (required for dub workflow)
console.log('[setup] Checking Demucs model...');
try {
  execSync(
    'python -c "from demucs.pretrained import get_model; get_model(\'htdemucs\'); print(\'[setup] Demucs htdemucs model ready.\')"',
    { stdio: 'inherit', cwd: root, timeout: 180000 }
  );
} catch (e) {
  console.warn('[setup] ⚠️  Demucs model download failed. Dubbing feature will attempt download on first use.');
  console.warn('[setup]    If you are behind a firewall, manually run: python -c "from demucs.pretrained import get_model; get_model(\'htdemucs\')"');
}

console.log('[setup] Done.');
