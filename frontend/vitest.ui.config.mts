import { defineConfig } from 'vitest/config';
import path from 'path';

export default defineConfig({
    esbuild: {
        jsx: 'automatic',
    },
    test: {
        environment: 'happy-dom',
        fileParallelism: false,
        include: [
            'src/components/**/*.test.tsx',
            'src/components/**/*.spec.tsx',
        ],
        setupFiles: ['./vitest.setup.ts'],
    },
    resolve: {
        alias: {
            '@': path.resolve(__dirname, 'src'),
        },
    },
});
