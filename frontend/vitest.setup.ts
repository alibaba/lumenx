import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach } from 'vitest';
import { activateRuntimeEnvironment } from '@/lib/runtimeEnvironment';

const TEST_RUNTIME_STORAGE_ID = '11111111-1111-4111-8111-111111111111';

beforeEach(() => {
    activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_RUNTIME_STORAGE_ID });
});

afterEach(() => {
    cleanup();
});
