const { exec } = require('child_process');

const URL = 'http://localhost:3000';
const backendPort = process.env.BACKEND_PORT || '18177';

setTimeout(() => {
  console.log('\n  ╔══════════════════════════════════════════╗');
  console.log('  ║                                          ║');
  console.log('  ║   LumenX AI Comic Platform Ready!        ║');
  console.log('  ║                                          ║');
  console.log('  ║   Frontend:  http://localhost:3000       ║');
  console.log(`  ║   Backend:   http://127.0.0.1:${backendPort}     ║`);
  console.log('  ║                                          ║');
  console.log('  ║   Press Ctrl+C to stop all services.     ║');
  console.log('  ║                                          ║');
  console.log('  ╚══════════════════════════════════════════╝\n');

  const cmd = process.platform === 'win32' ? `start "" "${URL}"`
    : process.platform === 'darwin' ? `open "${URL}"`
    : `xdg-open "${URL}"`;
  exec(cmd);
}, 5000);
