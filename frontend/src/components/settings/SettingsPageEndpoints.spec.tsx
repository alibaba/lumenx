import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SettingsPage from './SettingsPage';
import { api, type EnvConfigPayload } from '@/lib/api';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { toast } from '@/store/toastStore';
import { renderWithIntl } from '../series/__tests__/test-utils';

vi.mock('@/store/toastStore', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }));
vi.mock('@/lib/api', async importOriginal => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getEnvConfig: vi.fn(),
      saveEnvConfig: vi.fn(),
      fetchPromptDefaults: vi.fn(),
      healthCheck: vi.fn(),
    },
  };
});

const STORAGE_A = '11111111-1111-4111-8111-111111111111';
const STORAGE_B = '22222222-2222-4222-8222-222222222222';
const loadedEndpoints = Object.freeze({
  DASHSCOPE_BASE_URL: 'https://dashscope.loaded.example.test',
  KLING_BASE_URL: 'https://kling.loaded.example.test',
  VIDU_BASE_URL: 'https://vidu.loaded.example.test',
  MULEROUTER_BASE_URL: 'https://mulerouter.loaded.example.test',
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

async function renderApiKeys(initial: EnvConfigPayload = { DASHSCOPE_API_KEY: 'loaded-key' }) {
  vi.mocked(api.getEnvConfig).mockResolvedValueOnce(initial);
  const rendered = renderWithIntl(<SettingsPage />);
  fireEvent.click(await screen.findByRole('tab', { name: /API|密钥/ }));
  await waitFor(() => expect(screen.getByRole('button', { name: '保存配置' })).toBeEnabled());
  return rendered;
}

function endpoint(provider: string) {
  // The current visible FieldLabel is adjacent to, rather than linked to, its input.
  return within(screen.getByText(`${provider} BASE URL`).parentElement!).getByRole('textbox');
}

function expandEndpoints() {
  fireEvent.click(screen.getByRole('button', { name: '展开端点配置' }));
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getEnvConfig).mockReset();
  vi.mocked(api.saveEnvConfig).mockReset().mockResolvedValue(undefined);
  vi.mocked(api.fetchPromptDefaults).mockReset().mockResolvedValue({});
  vi.mocked(api.healthCheck).mockReset().mockResolvedValue({
    ok: true, time: 1, log_dir: '/tmp/lumenx/logs', log_file: '/tmp/lumenx/logs/app.log', studio_projects: 0,
  });
  localStorage.clear();
  activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
});

afterEach(() => {
  clearRuntimeEnvironment();
});

describe('SettingsPage endpoint configuration', () => {
  it('reads all four provider endpoints and keeps a draft when the controls are collapsed', async () => {
    await renderApiKeys({ endpoint_overrides: loadedEndpoints });
    expect(screen.queryByText('MuleRouter BASE URL')).not.toBeInTheDocument();
    expandEndpoints();
    expect(endpoint('DashScope')).toHaveValue(loadedEndpoints.DASHSCOPE_BASE_URL);
    expect(endpoint('Kling')).toHaveValue(loadedEndpoints.KLING_BASE_URL);
    expect(endpoint('Vidu')).toHaveValue(loadedEndpoints.VIDU_BASE_URL);
    expect(endpoint('MuleRouter')).toHaveValue(loadedEndpoints.MULEROUTER_BASE_URL);

    fireEvent.change(endpoint('MuleRouter'), { target: { value: 'https://mulerouter.draft.example.test' } });
    fireEvent.click(screen.getByRole('button', { name: '收起端点配置' }));
    expect(screen.queryByText('MuleRouter BASE URL')).not.toBeInTheDocument();
    expandEndpoints();
    expect(endpoint('MuleRouter')).toHaveValue('https://mulerouter.draft.example.test');
    expect(api.saveEnvConfig).not.toHaveBeenCalled();
  });

  it('submits all four edited endpoints with the existing credentials and routes, then waits for acknowledgement', async () => {
    const initial = Object.freeze({
      DASHSCOPE_API_KEY: 'loaded-key',
      KLING_PROVIDER_MODE: 'vendor' as const,
      VIDU_PROVIDER_MODE: 'vendor' as const,
      PIXVERSE_PROVIDER_MODE: 'dashscope' as const,
      KLING_ACCESS_KEY: 'kling-access', KLING_SECRET_KEY: 'kling-secret', VIDU_API_KEY: 'vidu-key',
      OSS_BUCKET_NAME: 'existing-bucket',
      endpoint_overrides: Object.freeze({}),
    });
    const pending = deferred<void>();
    vi.mocked(api.saveEnvConfig).mockReturnValueOnce(pending.promise);
    await renderApiKeys(initial);
    expandEndpoints();
    fireEvent.change(endpoint('DashScope'), { target: { value: 'https://dashscope.edited.example.test' } });
    fireEvent.change(endpoint('Kling'), { target: { value: 'https://kling.edited.example.test' } });
    fireEvent.change(endpoint('Vidu'), { target: { value: 'https://vidu.edited.example.test' } });
    fireEvent.change(endpoint('MuleRouter'), { target: { value: 'https://mulerouter.edited.example.test' } });
    fireEvent.change(screen.getByDisplayValue('loaded-key'), { target: { value: 'edited-key' } });
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }));

    expect(api.saveEnvConfig).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      ...initial,
      DASHSCOPE_API_KEY: 'edited-key',
      endpoint_overrides: {
        DASHSCOPE_BASE_URL: 'https://dashscope.edited.example.test',
        KLING_BASE_URL: 'https://kling.edited.example.test',
        VIDU_BASE_URL: 'https://vidu.edited.example.test',
        MULEROUTER_BASE_URL: 'https://mulerouter.edited.example.test',
      },
    }));
    expect(initial.DASHSCOPE_API_KEY).toBe('loaded-key');
    expect(initial.endpoint_overrides).toEqual({});
    expect(toast.success).not.toHaveBeenCalled();
    const saving = screen.getByRole('button', { name: /保存中/ });
    expect(saving).toBeDisabled();
    fireEvent.click(saving);
    expect(api.saveEnvConfig).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve());
    expect(toast.success).toHaveBeenCalledExactlyOnceWith('设置保存成功');
    expect(screen.getByRole('button', { name: '保存配置' })).toBeEnabled();
  });

  it('sends an explicit empty override when clearing an endpoint without changing the other providers', async () => {
    await renderApiKeys({ endpoint_overrides: loadedEndpoints });
    expandEndpoints();
    fireEvent.change(endpoint('MuleRouter'), { target: { value: '' } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '保存配置' })));

    expect(endpoint('MuleRouter')).toHaveValue('');
    expect(api.saveEnvConfig).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      endpoint_overrides: { ...loadedEndpoints, MULEROUTER_BASE_URL: '' },
    }));
    expect(loadedEndpoints.MULEROUTER_BASE_URL).toBe('https://mulerouter.loaded.example.test');
    expect(toast.success).toHaveBeenCalledExactlyOnceWith('设置保存成功');
  });

  it('retains the endpoint draft after a failed save and retries the same values', async () => {
    const pending = deferred<void>();
    vi.mocked(api.saveEnvConfig).mockReturnValueOnce(pending.promise);
    await renderApiKeys({ endpoint_overrides: loadedEndpoints });
    expandEndpoints();
    fireEvent.change(endpoint('Vidu'), { target: { value: 'https://vidu.retry.example.test' } });
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
    await act(async () => pending.reject(new Error('save unavailable')));

    expect(toast.error).toHaveBeenCalledExactlyOnceWith('保存配置失败。');
    expect(toast.success).not.toHaveBeenCalled();
    expect(endpoint('Vidu')).toHaveValue('https://vidu.retry.example.test');
    expect(screen.getByRole('button', { name: '保存配置' })).toBeEnabled();
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '保存配置' })));
    expect(api.saveEnvConfig).toHaveBeenCalledTimes(2);
    expect(api.saveEnvConfig).toHaveBeenNthCalledWith(2, expect.objectContaining({
      endpoint_overrides: { ...loadedEndpoints, VIDU_BASE_URL: 'https://vidu.retry.example.test' },
    }));
    expect(toast.success).toHaveBeenCalledExactlyOnceWith('设置保存成功');
  });

  it('uses default routing and empty endpoints when the server omits optional fields or returns an unknown route', async () => {
    await renderApiKeys({ KLING_PROVIDER_MODE: 'unexpected-value' } as unknown as EnvConfigPayload);
    expandEndpoints();
    expect(endpoint('DashScope')).toHaveValue('');
    expect(endpoint('Kling')).toHaveValue('');
    expect(endpoint('Vidu')).toHaveValue('');
    expect(endpoint('MuleRouter')).toHaveValue('');
    expect(screen.queryByPlaceholderText('Kling Access Key')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('Vidu API Key')).not.toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '保存配置' })));
    expect(api.saveEnvConfig).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      KLING_PROVIDER_MODE: 'dashscope', VIDU_PROVIDER_MODE: 'dashscope', PIXVERSE_PROVIDER_MODE: 'dashscope',
      endpoint_overrides: {},
    }));
  });

  it('ignores old A success and B failure after returning to A and editing its fresh endpoints', async () => {
    const oldA = deferred<EnvConfigPayload>();
    const oldB = deferred<EnvConfigPayload>();
    vi.mocked(api.getEnvConfig)
      .mockReturnValueOnce(oldA.promise)
      .mockReturnValueOnce(oldB.promise)
      .mockResolvedValueOnce({ endpoint_overrides: loadedEndpoints });
    renderWithIntl(<SettingsPage />);
    fireEvent.click(screen.getByRole('tab', { name: /API|密钥/ }));
    expect(api.getEnvConfig).toHaveBeenCalledTimes(1);
    await act(async () => activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B }));
    expect(api.getEnvConfig).toHaveBeenCalledTimes(2);
    await act(async () => activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A }));
    expect(api.getEnvConfig).toHaveBeenCalledTimes(3);
    expandEndpoints();
    fireEvent.change(endpoint('MuleRouter'), { target: { value: 'https://fresh-a.example.test' } });
    await act(async () => oldA.resolve({ endpoint_overrides: { MULEROUTER_BASE_URL: 'https://old-a.example.test' } }));
    await act(async () => oldB.reject(new Error('late B load failure')));

    expect(endpoint('MuleRouter')).toHaveValue('https://fresh-a.example.test');
    expect(endpoint('DashScope')).toHaveValue(loadedEndpoints.DASHSCOPE_BASE_URL);
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '保存配置' })));
    expect(api.saveEnvConfig).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      endpoint_overrides: { ...loadedEndpoints, MULEROUTER_BASE_URL: 'https://fresh-a.example.test' },
    }));
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('does not carry endpoint drafts into a new environment that omits endpoint overrides', async () => {
    await renderApiKeys({ endpoint_overrides: loadedEndpoints });
    expandEndpoints();
    fireEvent.change(endpoint('DashScope'), { target: { value: 'https://unsaved-a.example.test' } });
    const loadB = deferred<EnvConfigPayload>();
    vi.mocked(api.getEnvConfig).mockReturnValueOnce(loadB.promise);
    await act(async () => activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B }));
    expect(screen.queryByText('DashScope BASE URL')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '保存配置' })).not.toBeInTheDocument();
    await act(async () => loadB.resolve({ DASHSCOPE_API_KEY: 'b-key' }));

    expect(endpoint('DashScope')).toHaveValue('');
    expect(endpoint('Kling')).toHaveValue('');
    expect(endpoint('Vidu')).toHaveValue('');
    expect(endpoint('MuleRouter')).toHaveValue('');
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '保存配置' })));
    expect(api.saveEnvConfig).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      DASHSCOPE_API_KEY: 'b-key', endpoint_overrides: {},
    }));
  });

  it.each(['success', 'failure'] as const)('ignores an old endpoint save %s while the new environment is saving', async outcome => {
    const saveA = deferred<void>();
    const saveB = deferred<void>();
    vi.mocked(api.saveEnvConfig).mockReturnValueOnce(saveA.promise).mockReturnValueOnce(saveB.promise);
    await renderApiKeys({ endpoint_overrides: loadedEndpoints });
    expandEndpoints();
    fireEvent.change(endpoint('MuleRouter'), { target: { value: 'https://a-saved.example.test' } });
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
    vi.mocked(api.getEnvConfig).mockResolvedValueOnce({ endpoint_overrides: {} });
    await act(async () => activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B }));
    fireEvent.change(endpoint('MuleRouter'), { target: { value: 'https://b-saved.example.test' } });
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
    await act(async () => {
      if (outcome === 'success') saveA.resolve();
      else saveA.reject(new Error('late A save failure'));
    });

    expect(api.saveEnvConfig).toHaveBeenCalledTimes(2);
    expect(api.saveEnvConfig).toHaveBeenNthCalledWith(1, expect.objectContaining({
      endpoint_overrides: { ...loadedEndpoints, MULEROUTER_BASE_URL: 'https://a-saved.example.test' },
    }));
    expect(api.saveEnvConfig).toHaveBeenNthCalledWith(2, expect.objectContaining({
      endpoint_overrides: { MULEROUTER_BASE_URL: 'https://b-saved.example.test' },
    }));
    expect(endpoint('MuleRouter')).toHaveValue('https://b-saved.example.test');
    expect(screen.getByRole('button', { name: /保存中/ })).toBeDisabled();
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
    await act(async () => saveB.resolve());
    expect(toast.success).toHaveBeenCalledExactlyOnceWith('设置保存成功');
    expect(screen.getByRole('button', { name: '保存配置' })).toBeEnabled();
  });

  it('rejects an old endpoint form event in the same turn that the active environment changes', async () => {
    await renderApiKeys({ endpoint_overrides: loadedEndpoints });
    expandEndpoints();
    const oldInput = endpoint('MuleRouter');
    const oldSave = screen.getByRole('button', { name: '保存配置' });
    const loadB = deferred<EnvConfigPayload>();
    vi.mocked(api.getEnvConfig).mockReturnValueOnce(loadB.promise);
    act(() => {
      activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
      fireEvent.change(oldInput, { target: { value: 'https://stale-form.example.test' } });
      fireEvent.click(oldSave);
    });
    expect(api.saveEnvConfig).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledExactlyOnceWith('保存配置失败。');
    await act(async () => loadB.resolve({ endpoint_overrides: {} }));
    expect(endpoint('MuleRouter')).toHaveValue('');
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '保存配置' })));
    expect(api.saveEnvConfig).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ endpoint_overrides: {} }));
  });
});
