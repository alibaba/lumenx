import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextIntlClientProvider } from 'next-intl';
import EnvConfigDialog from '@/components/project/EnvConfigDialog';
import { api } from '@/lib/api';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import zhMessages from '../../../../messages/zh.json';
import enMessages from '../../../../messages/en.json';

vi.mock('@/lib/api', () => ({
  api: {
    getEnvConfig: vi.fn(),
    saveEnvConfig: vi.fn(),
  },
}));


type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void; reject: (reason?: unknown) => void };

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const ENV_A = { schema_version: 1 as const, storage_id: '00000000-0000-4000-8000-00000000000a' };
const ENV_B = { schema_version: 1 as const, storage_id: '00000000-0000-4000-8000-00000000000b' };

const emptyConfig = {
  DASHSCOPE_API_KEY: '',
  ALIBABA_CLOUD_ACCESS_KEY_ID: '',
  ALIBABA_CLOUD_ACCESS_KEY_SECRET: '',
  OSS_BUCKET_NAME: '',
  OSS_ENDPOINT: '',
  OSS_BASE_PATH: '',
  KLING_PROVIDER_MODE: 'dashscope' as const,
  VIDU_PROVIDER_MODE: 'dashscope' as const,
  PIXVERSE_PROVIDER_MODE: 'dashscope' as const,
  KLING_ACCESS_KEY: '',
  KLING_SECRET_KEY: '',
  VIDU_API_KEY: '',
  MULEROUTER_API_KEY: '',
  endpoint_overrides: {},
};

describe('EnvConfigDialog optional local startup settings', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.mocked(api.getEnvConfig).mockResolvedValue({ ...emptyConfig });
    vi.mocked(api.saveEnvConfig).mockResolvedValue(undefined);
    vi.spyOn(window, 'alert').mockImplementation(() => undefined);
    activateRuntimeEnvironment(ENV_A);
  });

  afterEach(() => {
    clearRuntimeEnvironment();
  });

  it('can close with DashScope empty', async () => {
    const onClose = vi.fn();
    renderWithIntl(<EnvConfigDialog isOpen onClose={onClose} />);

    await screen.findByText('DashScope API Key');
    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    expect(onClose).toHaveBeenCalledExactlyOnceWith();
    expect(screen.queryByText(/必须配置|无法关闭/)).toBeNull();
  });

  it('saves an empty local-first config without requiring DashScope', async () => {
    const onClose = vi.fn();
    renderWithIntl(<EnvConfigDialog isOpen onClose={onClose} />);

    await screen.findByText('DashScope API Key');
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '保存配置' })));

    expect(api.saveEnvConfig).toHaveBeenCalledWith(expect.objectContaining({ DASHSCOPE_API_KEY: '' }));
    expect(window.alert).toHaveBeenCalledWith('配置保存成功！');
    expect(onClose).toHaveBeenCalledExactlyOnceWith();
  });

  it('does not save stale env A config after env B load replaces it', async () => {
    const loadA = deferred<typeof emptyConfig>();
    const loadB = deferred<typeof emptyConfig>();
    vi.mocked(api.getEnvConfig)
      .mockReturnValueOnce(loadA.promise)
      .mockReturnValueOnce(loadB.promise);
    renderWithIntl(<EnvConfigDialog isOpen onClose={vi.fn()} />);

    await act(async () => activateRuntimeEnvironment(ENV_B));
    await act(async () => loadB.resolve({ ...emptyConfig, DASHSCOPE_API_KEY: 'b-key' }));
    await act(async () => loadA.resolve({ ...emptyConfig, DASHSCOPE_API_KEY: 'a-key' }));

    await screen.findByDisplayValue('b-key');
    expect(screen.queryByDisplayValue('a-key')).toBeNull();
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '保存配置' })));

    expect(api.saveEnvConfig).toHaveBeenCalledWith(expect.objectContaining({ DASHSCOPE_API_KEY: 'b-key' }));
    expect(api.saveEnvConfig).not.toHaveBeenCalledWith(expect.objectContaining({ DASHSCOPE_API_KEY: 'a-key' }));
  });

  it('blocks saving stale env A config when env B config load fails', async () => {
    vi.mocked(api.getEnvConfig)
      .mockResolvedValueOnce({ ...emptyConfig, DASHSCOPE_API_KEY: 'a-key' })
      .mockRejectedValueOnce(new Error('env b unavailable'));
    renderWithIntl(<EnvConfigDialog isOpen onClose={vi.fn()} />);

    await screen.findByDisplayValue('a-key');
    await act(async () => activateRuntimeEnvironment(ENV_B));

    expect(await screen.findByText(/加载配置失败/)).toBeTruthy();
    expect(screen.queryByDisplayValue('a-key')).toBeNull();
    expect(screen.getByRole('button', { name: '保存配置' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
    expect(api.saveEnvConfig).not.toHaveBeenCalled();
  });

  it('does not let a late save from a closed session close a reopened dialog', async () => {
    const saveA = deferred<void>();
    vi.mocked(api.getEnvConfig)
      .mockResolvedValueOnce({ ...emptyConfig, DASHSCOPE_API_KEY: 'a-key' })
      .mockResolvedValueOnce({ ...emptyConfig, DASHSCOPE_API_KEY: 'reopened-key' });
    vi.mocked(api.saveEnvConfig).mockReturnValueOnce(saveA.promise);
    const onClose = vi.fn();
    const { rerender } = renderWithIntl(<EnvConfigDialog isOpen onClose={onClose} />);

    await screen.findByDisplayValue('a-key');
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
    rerender(<EnvConfigDialog isOpen={false} onClose={onClose} />);
    rerender(<EnvConfigDialog isOpen onClose={onClose} />);
    await screen.findByDisplayValue('reopened-key');

    await act(async () => saveA.resolve());

    expect(window.alert).not.toHaveBeenCalledWith('配置保存成功！');
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByDisplayValue('reopened-key')).toBeTruthy();
  });

  it('does not show stale save failure after switching environments', async () => {
    const saveA = deferred<void>();
    vi.mocked(api.getEnvConfig)
      .mockResolvedValueOnce({ ...emptyConfig, DASHSCOPE_API_KEY: 'a-key' })
      .mockResolvedValueOnce({ ...emptyConfig, DASHSCOPE_API_KEY: 'b-key' });
    vi.mocked(api.saveEnvConfig).mockReturnValueOnce(saveA.promise);
    renderWithIntl(<EnvConfigDialog isOpen onClose={vi.fn()} />);

    await screen.findByDisplayValue('a-key');
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
    await act(async () => activateRuntimeEnvironment(ENV_B));
    await screen.findByDisplayValue('b-key');

    await act(async () => saveA.reject(new Error('old env rejected')));

    expect(window.alert).not.toHaveBeenCalledWith('配置保存失败，请检查后重试。');
    expect(screen.getByDisplayValue('b-key')).toBeTruthy();
  });

  it('guards immediate double-click save as one operation', async () => {
    const saveA = deferred<void>();
    vi.mocked(api.getEnvConfig).mockResolvedValueOnce({ ...emptyConfig, DASHSCOPE_API_KEY: 'a-key' });
    vi.mocked(api.saveEnvConfig).mockReturnValue(saveA.promise);
    renderWithIntl(<EnvConfigDialog isOpen onClose={vi.fn()} />);

    await screen.findByDisplayValue('a-key');
    const save = screen.getByRole('button', { name: '保存配置' });
    fireEvent.click(save);
    fireEvent.click(save);

    expect(api.saveEnvConfig).toHaveBeenCalledTimes(1);
    await act(async () => saveA.resolve());
  });

  it('does not reload or drop a draft when locale changes', async () => {
    vi.mocked(api.getEnvConfig).mockResolvedValue({ ...emptyConfig, DASHSCOPE_API_KEY: 'loaded-key' });
    const renderDialog = (locale: 'zh' | 'en') => (
      <NextIntlClientProvider locale={locale} messages={locale === 'zh' ? zhMessages : enMessages} timeZone="Asia/Shanghai">
        <EnvConfigDialog isOpen onClose={vi.fn()} />
      </NextIntlClientProvider>
    );
    const { rerender } = render(renderDialog('zh'));

    const keyInput = await screen.findByDisplayValue('loaded-key');
    fireEvent.change(keyInput, { target: { value: 'draft-key' } });
    rerender(renderDialog('en'));

    expect(screen.getByDisplayValue('draft-key')).toBeTruthy();
    expect(api.getEnvConfig).toHaveBeenCalledTimes(1);
  });

  it('still rejects incomplete direct Kling vendor configuration', async () => {
    renderWithIntl(<EnvConfigDialog isOpen onClose={vi.fn()} />);

    await screen.findByText('Kling Provider');
    fireEvent.click(screen.getAllByRole('button', { name: 'Vendor Direct' })[0]);
    await act(async () => fireEvent.click(screen.getByRole('button', { name: '保存配置' })));

    expect(api.saveEnvConfig).not.toHaveBeenCalled();
    expect(window.alert).toHaveBeenCalledWith(expect.stringContaining('Kling Access Key (vendor mode)'));
  });
});
