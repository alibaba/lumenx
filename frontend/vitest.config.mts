import { defineConfig } from 'vitest/config';
import path from 'path';

export default defineConfig({
    esbuild: {
        jsx: 'automatic',
    },
    test: {
        environment: 'node',
        include: [
            'src/__tests__/**/*.test.ts',
            'src/__tests__/**/*.spec.ts',
            'src/__tests__/**/*.test.tsx',
            'src/__tests__/**/*.spec.tsx',
            'src/components/modules/**/*.test.tsx',
            'src/components/modules/**/*.spec.tsx',
        ],
        setupFiles: ['./vitest.setup.ts'],
    },
    resolve: {
        alias: {
            '@': path.resolve(__dirname, 'src'),
        },
    },
});
