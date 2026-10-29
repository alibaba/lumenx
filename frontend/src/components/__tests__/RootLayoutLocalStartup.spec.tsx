import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import RootLayout from '@/app/layout';
import { api } from '@/lib/api';

vi.mock('@/components/Providers', () => ({ Providers: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('@/components/AppAccessGate', () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('@/components/layout/TauriDragBar', () => ({ default: () => null }));
vi.mock('@/lib/api', () => ({
  api: {
    getEnvConfig: vi.fn(),
  },
}));

describe('RootLayout local startup', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.mocked(api.getEnvConfig).mockResolvedValue({ DASHSCOPE_API_KEY: '' });
  });

  it('does not run the credential checker before local content is usable', async () => {
    render(<RootLayout><main>local studio shell</main></RootLayout>);

    await act(async () => undefined);

    expect(screen.getByText('local studio shell')).toBeTruthy();
    expect(api.getEnvConfig).not.toHaveBeenCalled();
  });
});
