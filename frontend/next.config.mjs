/** @type {import('next').NextConfig} */
const isProd = process.env.NODE_ENV === 'production';
const isDocker = process.env.DOCKER_BUILD === 'true';

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:17177';

const nextConfig = {
    output: isProd ? 'export' : undefined,
    distDir: isProd ? (isDocker ? 'out' : '../static') : undefined,
    basePath: isProd && !isDocker ? '/static' : undefined,
    assetPrefix: isProd && !isDocker ? '/static' : undefined,
    // Dev-only: proxy /api-proxy/* to backend to avoid CORS issues (e.g. file downloads)
    async rewrites() {
        return isProd ? [] : [
            {
                source: '/api-proxy/:path*',
                destination: `${BACKEND_URL}/:path*`,
            },
        ];
    },
    eslint: {
        ignoreDuringBuilds: true,
    },
    typescript: {
        ignoreBuildErrors: true,
    },
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
                port: "17177",
            },
        ],
    },
};

export default nextConfig;
