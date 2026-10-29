import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SettingsPage from './SettingsPage';
import { api, type EnvConfigPayload } from '@/lib/api';
import { toast } from '@/store/toastStore';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { renderWithIntl } from '../series/__tests__/test-utils';
import { NextIntlClientProvider } from 'next-intl';
import zhMessages from '../../../messages/zh.json';

vi.mock('@/components/common/GroupedModelGrid', () => ({ default: () => null }));
vi.mock('./UpdateChecker', () => ({ default: () => null }));
vi.mock('@/store/toastStore', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }));
vi.mock('@/lib/api', async importOriginal => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getEnvConfig: vi.fn(),
      triggerMulerunLogin: vi.fn(),
      fetchPromptDefaults: vi.fn(),
      healthCheck: vi.fn(),
      checkSystem: vi.fn(),
      saveEnvConfig: vi.fn(),
    },
  };
});

const STORAGE_A = '11111111-1111-4111-8111-111111111111';
const STORAGE_B = '22222222-2222-4222-8222-222222222222';

const envConfig = (overrides: Partial<EnvConfigPayload> = {}): EnvConfigPayload => ({
  DASHSCOPE_API_KEY: 'dashscope-key',
  ALIBABA_CLOUD_ACCESS_KEY_ID: '',
  ALIBABA_CLOUD_ACCESS_KEY_SECRET: '',
  OSS_ENABLE: true,
  OSS_BUCKET_NAME: '',
  OSS_ENDPOINT: '',
  OSS_BASE_PATH: '',
  KLING_PROVIDER_MODE: 'dashscope',
  VIDU_PROVIDER_MODE: 'dashscope',
  PIXVERSE_PROVIDER_MODE: 'dashscope',
  KLING_ACCESS_KEY: '',
  KLING_SECRET_KEY: '',
  VIDU_API_KEY: '',
  MULEROUTER_API_KEY: '',
  MULERUN_CLI_LOGGED_IN: false,
  endpoint_overrides: {},
  ...overrides,
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

async function renderApiKeys(initial: EnvConfigPayload = envConfig()) {
  vi.mocked(api.getEnvConfig).mockResolvedValueOnce(initial);
  const rendered = renderWithIntl(<SettingsPage />);
  fireEvent.click(await screen.findByRole('tab', { name: /API|密钥/ }));
  await screen.findByDisplayValue('dashscope-key');
  vi.useFakeTimers();
  return rendered;
}

function clickLoginButton() {
  const button = screen.getByRole('button', { name: '一键登录 MuleRun' });
  fireEvent.click(button);
  return button;
}

async function advance(ms: number) {
  act(() => { vi.advanceTimersByTime(ms); });
  await act(async () => {});
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getEnvConfig).mockReset();
  vi.mocked(api.triggerMulerunLogin).mockReset();
  vi.mocked(api.fetchPromptDefaults).mockReset();
  vi.mocked(api.healthCheck).mockReset();
  vi.mocked(api.checkSystem).mockReset();
  vi.mocked(api.saveEnvConfig).mockReset();
  localStorage.clear();
  activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
  vi.mocked(api.fetchPromptDefaults).mockResolvedValue({});
  vi.mocked(api.healthCheck).mockResolvedValue({ ok: true, time: 1, log_dir: '/tmp/lumenx/logs', log_file: '/tmp/lumenx/logs/app.log', studio_projects: 0 });
  vi.mocked(api.triggerMulerunLogin).mockResolvedValue({ status: 'ok' } as never);
});

afterEach(() => {
  vi.useRealTimers();
  clearRuntimeEnvironment();
});

describe('SettingsPage MuleRun login polling', () => {
  it('saves local configuration without a DashScope key', async () => {
    vi.mocked(api.getEnvConfig).mockResolvedValue(envConfig({ DASHSCOPE_API_KEY: '' }));
    vi.mocked(api.saveEnvConfig).mockResolvedValue(undefined);
    renderWithIntl(<SettingsPage />);
    fireEvent.click(await screen.findByRole('tab', { name: /API|密钥/ }));
    const save = await screen.findByRole('button', { name: '保存配置' });
    await waitFor(() => expect(save).toBeEnabled());
    await act(async () => fireEvent.click(save));
    expect(api.saveEnvConfig).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ DASHSCOPE_API_KEY: '' }));
    expect(toast.success).toHaveBeenCalledWith('设置保存成功');
  });

  it('still rejects missing credentials for explicitly selected vendor routing', async () => {
    vi.mocked(api.getEnvConfig).mockResolvedValue(envConfig({ DASHSCOPE_API_KEY: '', KLING_PROVIDER_MODE: 'vendor' }));
    renderWithIntl(<SettingsPage />);
    fireEvent.click(await screen.findByRole('tab', { name: /API|密钥/ }));
    const save = await screen.findByRole('button', { name: '保存配置' });
    await waitFor(() => expect(save).toBeEnabled());
    await act(async () => fireEvent.click(save));
    expect(api.saveEnvConfig).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ body: expect.stringContaining('Kling Access Key') }));
  });

  it('saves storage independently and suppresses a late error after changing environments', async () => {
    vi.mocked(api.getEnvConfig).mockResolvedValueOnce(envConfig({ DASHSCOPE_API_KEY: '', KLING_PROVIDER_MODE: 'vendor' })).mockResolvedValueOnce(envConfig());
    const pending = deferred<void>();
    vi.mocked(api.saveEnvConfig).mockReturnValueOnce(pending.promise);
    renderWithIntl(<SettingsPage />);
    fireEvent.click(await screen.findByRole('tab', { name: '存储' }));
    const save = await screen.findByRole('button', { name: '保存配置' });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    expect(api.saveEnvConfig).toHaveBeenCalledTimes(1);
    await act(async () => activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B }));
    await act(async () => pending.reject(new Error('late environment A failure')));
    expect(toast.error).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('ignores the old environment save result while the new environment is saving', async () => {
    vi.mocked(api.getEnvConfig).mockResolvedValueOnce(envConfig()).mockResolvedValueOnce(envConfig({ DASHSCOPE_API_KEY: 'b-key' }));
    const saveA = deferred<void>();
    const saveB = deferred<void>();
    vi.mocked(api.saveEnvConfig).mockReturnValueOnce(saveA.promise).mockReturnValueOnce(saveB.promise);
    renderWithIntl(<SettingsPage />);
    fireEvent.click(await screen.findByRole('tab', { name: /API|密钥/ }));
    await screen.findByDisplayValue('dashscope-key');
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
    await act(async () => activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B }));
    await screen.findByDisplayValue('b-key');
    const save = screen.getByRole('button', { name: '保存配置' });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await act(async () => saveA.resolve());
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /保存中/ })).toBeDisabled();
    await act(async () => saveB.resolve());
    expect(toast.success).toHaveBeenCalledExactlyOnceWith('设置保存成功');
  });

  it('serializes repeated save clicks and ignores completion after unmount', async () => {
    vi.mocked(api.getEnvConfig).mockResolvedValue(envConfig());
    const pending = deferred<void>();
    vi.mocked(api.saveEnvConfig).mockReturnValue(pending.promise);
    const rendered = renderWithIntl(<SettingsPage />);
    fireEvent.click(await screen.findByRole('tab', { name: /API|密钥/ }));
    await screen.findByDisplayValue('dashscope-key');
    const save = screen.getByRole('button', { name: '保存配置' });
    act(() => { fireEvent.click(save); fireEvent.click(save); });
    expect(api.saveEnvConfig).toHaveBeenCalledTimes(1);
    rendered.unmount();
    await act(async () => pending.resolve());
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('keeps the current environment config draft when the display locale changes', async () => {
    vi.mocked(api.getEnvConfig).mockResolvedValue(envConfig());
    const view = (locale: string) => (
      <NextIntlClientProvider locale={locale} messages={zhMessages} timeZone="Asia/Shanghai">
        <SettingsPage />
      </NextIntlClientProvider>
    );
    const rendered = renderWithIntl(view('zh'));
    fireEvent.click(await screen.findByRole('tab', { name: /API|密钥/ }));
    fireEvent.change(await screen.findByDisplayValue('dashscope-key'), { target: { value: 'unsaved-current-key' } });
    rendered.rerender(view('en'));
    await act(async () => {});
    expect(api.getEnvConfig).toHaveBeenCalledTimes(1);
    expect(screen.getByDisplayValue('unsaved-current-key')).toBeTruthy();
  });

  it('starts MuleRun login from the button and marks CLI login after a successful poll', async () => {
    await renderApiKeys();
    vi.mocked(api.getEnvConfig).mockResolvedValueOnce(envConfig({ MULERUN_CLI_LOGGED_IN: true }));

    await act(async () => { clickLoginButton(); });
    expect(api.triggerMulerunLogin).toHaveBeenCalledTimes(1);

    await advance(3000);

    expect(screen.getByText('MuleRun 已登录')).toBeInTheDocument();
    expect(api.getEnvConfig).toHaveBeenCalledTimes(2);
  });

  it('ignores repeated clicks while one login session is active', async () => {
    await renderApiKeys();
    const poll = deferred<EnvConfigPayload>();
    vi.mocked(api.getEnvConfig).mockReturnValueOnce(poll.promise);

    const button = screen.getByRole('button', { name: '一键登录 MuleRun' });
    await act(async () => {
      fireEvent.click(button);
      fireEvent.click(button);
    });

    expect(api.triggerMulerunLogin).toHaveBeenCalledTimes(1);
    await advance(3000);
    expect(api.getEnvConfig).toHaveBeenCalledTimes(2);
    await act(async () => { poll.resolve(envConfig({ MULERUN_CLI_LOGGED_IN: true })); });
    expect(screen.getByText('MuleRun 已登录')).toBeInTheDocument();
  });

  it('re-login starts a new poll and is not stopped by the previous login timeout', async () => {
    await renderApiKeys();
    vi.mocked(api.getEnvConfig).mockResolvedValueOnce(envConfig({ MULERUN_CLI_LOGGED_IN: true }));

    await act(async () => { clickLoginButton(); });
    await advance(3000);
    screen.getByText('MuleRun 已登录');

    await advance(116000);
    vi.mocked(api.getEnvConfig).mockResolvedValueOnce(envConfig({ MULERUN_CLI_LOGGED_IN: true }));
    fireEvent.click(screen.getByRole('button', { name: '重新登录' }));
    expect(api.triggerMulerunLogin).toHaveBeenCalledTimes(2);

    await advance(1000);
    await advance(3000);

    expect(api.getEnvConfig).toHaveBeenCalledTimes(3);
  });

  it('cleans up login polling after unmount', async () => {
    const rendered = await renderApiKeys();
    vi.mocked(api.getEnvConfig).mockResolvedValue(envConfig({ MULERUN_CLI_LOGGED_IN: false }));

    await act(async () => { clickLoginButton(); });
    const callsAfterInitialLoad = vi.mocked(api.getEnvConfig).mock.calls.length;
    screen.getByRole('button', { name: '一键登录 MuleRun' });

    await advance(3000);
    const callsBeforeUnmount = vi.mocked(api.getEnvConfig).mock.calls.length;
    expect(callsBeforeUnmount).toBeGreaterThan(callsAfterInitialLoad);

    rendered.unmount();
    await advance(120000);
    expect(api.getEnvConfig).toHaveBeenCalledTimes(callsBeforeUnmount);
  });

  it('does not apply a late MuleRun poll after the runtime environment switches', async () => {
    await renderApiKeys();
    const latePoll = deferred<EnvConfigPayload>();
    vi.mocked(api.getEnvConfig).mockReturnValueOnce(latePoll.promise);

    await act(async () => { clickLoginButton(); });
    await advance(3000);
    act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B }); });
    await act(async () => { latePoll.resolve(envConfig({ MULERUN_CLI_LOGGED_IN: true })); });

    expect(screen.queryByText('MuleRun 已登录')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '一键登录 MuleRun' })).toBeInTheDocument();
  });


  it('ignores a late successful poll after the login session has timed out', async () => {
    await renderApiKeys();
    const latePoll = deferred<EnvConfigPayload>();
    vi.mocked(api.getEnvConfig).mockReturnValue(latePoll.promise);

    await act(async () => { clickLoginButton(); });
    await advance(3000);
    await advance(117000);

    expect(toast.error).toHaveBeenCalledWith('登录失败');
    await act(async () => { latePoll.resolve(envConfig({ MULERUN_CLI_LOGGED_IN: true })); });

    expect(screen.queryByText('MuleRun 已登录')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '一键登录 MuleRun' })).toBeEnabled();
  });

  it('serializes slow MuleRun polls instead of overlapping backend reads', async () => {
    await renderApiKeys();
    const firstPoll = deferred<EnvConfigPayload>();
    vi.mocked(api.getEnvConfig)
      .mockReturnValueOnce(firstPoll.promise)
      .mockResolvedValueOnce(envConfig({ MULERUN_CLI_LOGGED_IN: true }));

    await act(async () => { clickLoginButton(); });
    await advance(3000);
    expect(api.getEnvConfig).toHaveBeenCalledTimes(2);

    await advance(3000);
    expect(api.getEnvConfig).toHaveBeenCalledTimes(2);

    await act(async () => { firstPoll.resolve(envConfig({ MULERUN_CLI_LOGGED_IN: false })); });
    await advance(3000);

    expect(api.getEnvConfig).toHaveBeenCalledTimes(3);
    expect(screen.getByText('MuleRun 已登录')).toBeInTheDocument();
  });

  it('cancels a pending MuleRun poll when the runtime environment switches', async () => {
    await renderApiKeys();
    const pendingPoll = deferred<EnvConfigPayload>();
    vi.mocked(api.getEnvConfig)
      .mockReturnValueOnce(pendingPoll.promise)
      .mockResolvedValue(envConfig({ MULERUN_CLI_LOGGED_IN: false }));

    await act(async () => { clickLoginButton(); });
    await advance(3000);
    act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B }); });
    await act(async () => {});

    expect(screen.getByRole('button', { name: '一键登录 MuleRun' })).toBeEnabled();
    await advance(120000);
    await act(async () => { pendingPoll.resolve(envConfig({ MULERUN_CLI_LOGGED_IN: true })); });

    expect(screen.queryByText('MuleRun 已登录')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '一键登录 MuleRun' })).toBeEnabled();
  });

  it('shows feedback when trigger or polling cannot complete login', async () => {
    vi.mocked(api.triggerMulerunLogin).mockRejectedValueOnce({ response: { data: { detail: 'browser blocked' } } });
    await renderApiKeys();

    await act(async () => { clickLoginButton(); });
    expect(toast.error).toHaveBeenCalledWith('browser blocked');

    vi.mocked(api.triggerMulerunLogin).mockResolvedValueOnce({ status: 'ok' } as never);
    vi.mocked(api.getEnvConfig).mockRejectedValue(new Error('poll failed'));
    await act(async () => { clickLoginButton(); });
    await advance(120000);

    expect(toast.error).toHaveBeenCalledWith('登录失败');
  });
});

describe('SettingsPage environment-scoped config loading', () => {
  it('rejects stale config replies across A→B→A and saves only the current environment values', async () => {
    const staleA = deferred<EnvConfigPayload>();
    const staleB = deferred<EnvConfigPayload>();
    const freshA = deferred<EnvConfigPayload>();
    vi.mocked(api.getEnvConfig)
      .mockReturnValueOnce(staleA.promise)
      .mockReturnValueOnce(staleB.promise)
      .mockReturnValueOnce(freshA.promise);

    renderWithIntl(<SettingsPage />);
    fireEvent.click(await screen.findByRole('tab', { name: /API|密钥/ }));
    expect(api.getEnvConfig).toHaveBeenCalledTimes(1);

    act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B }); });
    await waitFor(() => expect(api.getEnvConfig).toHaveBeenCalledTimes(2));

    act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A }); });
    await waitFor(() => expect(api.getEnvConfig).toHaveBeenCalledTimes(3));

    await act(async () => { freshA.resolve(envConfig({ DASHSCOPE_API_KEY: 'fresh-a-key' })); });
    await screen.findByDisplayValue('fresh-a-key');

    await act(async () => {
      staleA.resolve(envConfig({ DASHSCOPE_API_KEY: 'stale-a-key' }));
      staleB.resolve(envConfig({ DASHSCOPE_API_KEY: 'stale-b-key' }));
    });

    expect(screen.getByDisplayValue('fresh-a-key')).toBeInTheDocument();
    expect(screen.queryByDisplayValue('stale-a-key')).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue('stale-b-key')).not.toBeInTheDocument();

    vi.mocked(api.saveEnvConfig).mockResolvedValueOnce({ status: 'ok' } as never);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
    });

    expect(api.saveEnvConfig).toHaveBeenCalledWith(expect.objectContaining({ DASHSCOPE_API_KEY: 'fresh-a-key' }));
    expect(api.saveEnvConfig).not.toHaveBeenCalledWith(expect.objectContaining({ DASHSCOPE_API_KEY: 'stale-a-key' }));
    expect(api.saveEnvConfig).not.toHaveBeenCalledWith(expect.objectContaining({ DASHSCOPE_API_KEY: 'stale-b-key' }));
  });

  it('does not save stale A storage settings after B config load fails', async () => {
    vi.mocked(api.getEnvConfig).mockResolvedValueOnce(envConfig({
      DASHSCOPE_API_KEY: 'a-key',
      ALIBABA_CLOUD_ACCESS_KEY_ID: 'a-oss-ak',
      OSS_BUCKET_NAME: 'a-bucket',
    }));

    renderWithIntl(<SettingsPage />);
    fireEvent.click(await screen.findByRole('tab', { name: /API|密钥/ }));
    await screen.findByDisplayValue('a-key');

    vi.mocked(api.getEnvConfig).mockRejectedValueOnce(new Error('B unavailable'));
    act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B }); });
    fireEvent.click(await screen.findByRole('tab', { name: '存储' }));
    await screen.findByText('无法加载配置。后端是否已启动？');

    expect(screen.queryByDisplayValue('a-oss-ak')).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue('a-bucket')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '保存配置' })).not.toBeInTheDocument();
    expect(api.saveEnvConfig).not.toHaveBeenCalled();
  });

  it('loads partial B config from defaults instead of carrying A storage fields', async () => {
    vi.mocked(api.getEnvConfig)
      .mockResolvedValueOnce(envConfig({
        DASHSCOPE_API_KEY: 'a-key',
        ALIBABA_CLOUD_ACCESS_KEY_ID: 'a-oss-ak',
        OSS_BUCKET_NAME: 'a-bucket',
      }))
      .mockResolvedValueOnce({
        DASHSCOPE_API_KEY: 'b-key',
        endpoint_overrides: {},
      } as EnvConfigPayload);

    renderWithIntl(<SettingsPage />);
    fireEvent.click(await screen.findByRole('tab', { name: /API|密钥/ }));
    await screen.findByDisplayValue('a-key');

    act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B }); });
    await screen.findByDisplayValue('b-key');

    fireEvent.click(screen.getByRole('tab', { name: '存储' }));
    expect(screen.queryByDisplayValue('a-oss-ak')).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue('a-bucket')).not.toBeInTheDocument();
  });

  it('does not save A config into B after a synchronous runtime switch before effects flush', async () => {
    vi.mocked(api.getEnvConfig)
      .mockResolvedValueOnce(envConfig({ DASHSCOPE_API_KEY: 'a-key' }))
      .mockResolvedValueOnce(envConfig({ DASHSCOPE_API_KEY: 'b-key' }));

    renderWithIntl(<SettingsPage />);
    fireEvent.click(await screen.findByRole('tab', { name: /API|密钥/ }));
    await screen.findByDisplayValue('a-key');

    act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B }); });

    expect(screen.queryByRole('button', { name: '保存配置' })).not.toBeInTheDocument();
    expect(api.saveEnvConfig).not.toHaveBeenCalled();
    await screen.findByDisplayValue('b-key');
  });
});
