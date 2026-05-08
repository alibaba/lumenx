// Runtime manifests live under tmp/lumenx-*.json and are ephemeral launch hints.
// They must not contain user/project data; see docs/runtime-files.md.
const DEFAULT_LUMENX_API_HOST = '127.0.0.1';
const DEFAULT_LUMENX_API_PORT = '18177';
const DEFAULT_LUMENX_FRONTEND_PORT = '3000';

function getApiHost(defaultHost = DEFAULT_LUMENX_API_HOST) {
  return (process.env.LUMENX_API_HOST || defaultHost).trim() || defaultHost;
}

function getApiPort(defaultPort = DEFAULT_LUMENX_API_PORT) {
  const rawPort = (process.env.LUMENX_API_PORT || process.env.BACKEND_PORT || defaultPort).trim();
  const parsed = Number.parseInt(rawPort, 10);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 65535
    ? String(parsed)
    : defaultPort;
}

function getFrontendPort(defaultPort = DEFAULT_LUMENX_FRONTEND_PORT) {
  const rawPort = (
    process.env.LUMENX_FRONTEND_PORT ||
    process.env.NEXT_PUBLIC_LUMENX_FRONTEND_PORT ||
    defaultPort
  ).trim();
  const parsed = Number.parseInt(rawPort, 10);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 65535
    ? String(parsed)
    : defaultPort;
}

module.exports = {
  DEFAULT_LUMENX_API_HOST,
  DEFAULT_LUMENX_API_PORT,
  DEFAULT_LUMENX_FRONTEND_PORT,
  getApiHost,
  getApiPort,
  getFrontendPort,
};
