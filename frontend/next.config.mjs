/** @type {import('next').NextConfig} */
const isProd = process.env.NODE_ENV === 'production';
const isDocker = process.env.DOCKER_BUILD === 'true';
const API_PORT = process.env.LUMENX_API_PORT || process.env.NEXT_PUBLIC_LUMENX_API_PORT || '18177';

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || `http://127.0.0.1:${API_PORT}`;

const nextConfig = {
    output: isProd ? 'export' : undefined,
    distDir: isProd ? (isDocker ? 'out' : '../static') : undefined,
    basePath: isProd && !isDocker ? '/static' : undefined,
    assetPrefix: isProd && !isDocker ? '/static' : undefined,
    env: {
        NEXT_PUBLIC_LUMENX_API_PORT: API_PORT,
    },
    // Dev-only: proxy /api-proxy/* to backend to avoid CORS issues (e.g. file downloads)
    ...(!isProd ? {
        async rewrites() {
            return [
            {
                source: '/api-proxy/:path*',
                destination: `${BACKEND_URL}/:path*`,
            },
            ];
        },
    } : {}),
    images: {
        unoptimized: true,
        remotePatterns: [
            {
                protocol: "https",
                hostname: "placehold.co",
            },
            {
                protocol: "http",
                hostname: "localhost",
                port: API_PORT,
            },
            {
                protocol: "http",
                hostname: "127.0.0.1",
                port: API_PORT,
            },
        ],
    },
};

export default nextConfig;
