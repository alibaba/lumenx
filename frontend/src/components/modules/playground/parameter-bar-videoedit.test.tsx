import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { beforeEach, describe, expect, it } from 'vitest';

import ParameterBar from './ParameterBar';
import { usePlaygroundStore } from './usePlaygroundStore';
import en from '../../../../messages/en.json';
import zh from '../../../../messages/zh.json';

function renderBar(locale: 'en' | 'zh' = 'en') {
  const messages = locale === 'zh' ? zh : en;
  return render(
    <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
      <ParameterBar />
    </NextIntlClientProvider>,
  );
}

function resetVideoEdit(parameters: Record<string, string | number | boolean | undefined> = {}) {
  usePlaygroundStore.setState({
    mode: 'v2v',
    modelId: 'wan2.7-videoedit',
    parameters,
    batchSize: 1,
  });
}

describe('ParameterBar Wan 2.7 VideoEdit controls', () => {
  beforeEach(() => {
    resetVideoEdit({ duration: 0 });
  });

  it('renders only provider-valid VideoEdit duration options and preserves source duration', () => {
    renderBar('zh');

    expect(screen.getByRole('button', { name: /保留原时长/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '1s' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /保留原时长/ }));
    fireEvent.click(screen.getByText('2s'));
    expect(usePlaygroundStore.getState().parameters.duration).toBe(2);

    fireEvent.click(screen.getByRole('button', { name: '2s' }));
    fireEvent.click(screen.getByText('保留原时长'));
    expect(usePlaygroundStore.getState().parameters.duration).toBe(0);
  });

  it('localizes VideoEdit audio setting labels in English and Chinese', () => {
    const { unmount } = renderBar('zh');

    expect(screen.getByText('音频')).toBeTruthy();
    expect(screen.getByRole('button', { name: /自动/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /自动/ }));
    fireEvent.click(screen.getByText('保留原声'));
    expect(usePlaygroundStore.getState().parameters.audio_setting).toBe('origin');

    unmount();
    resetVideoEdit({ duration: 0 });
    renderBar('en');

    expect(screen.getByText('Audio')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Auto/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Auto/ }));
    fireEvent.click(screen.getByText('Keep source audio'));
    expect(usePlaygroundStore.getState().parameters.audio_setting).toBe('origin');
  });

  it('keeps explicit seed zero instead of treating it as random', async () => {
    renderBar('en');

    fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
    const seed = await screen.findByPlaceholderText('Random');
    fireEvent.change(seed, { target: { value: '0' } });

    await waitFor(() => expect(usePlaygroundStore.getState().parameters.seed).toBe(0));
  });
});

describe('ParameterBar migrated provider defaults', () => {
  beforeEach(() => {
    usePlaygroundStore.setState({
      mode: 'i2v',
      modelId: 'happyhorse-1.1-i2v',
      parameters: {
        aspect_ratio: '16:9',
        audio_setting: 'origin',
        prompt_extend: false,
      },
      batchSize: 1,
    });
  });

  it('shows HappyHorse watermark ON from the catalog default and preserves explicit OFF', async () => {
    renderBar('en');

    expect(screen.getByText(/Some saved settings are kept/)).toBeTruthy();
    expect(usePlaygroundStore.getState().parameters).toMatchObject({
      aspect_ratio: '16:9',
      audio_setting: 'origin',
      prompt_extend: false,
    });

    fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
    expect(screen.getByText('Watermark')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'ON' }).className).toContain('bg-primary');

    fireEvent.click(screen.getByRole('button', { name: 'OFF' }));
    await waitFor(() => expect(usePlaygroundStore.getState().parameters.watermark).toBe(false));
    expect(screen.getByRole('button', { name: 'OFF' }).className).toContain('bg-primary');
  });
});

describe('ParameterBar HappyHorse 1.0 VideoEdit controls', () => {
  beforeEach(() => {
    usePlaygroundStore.setState({
      mode: 'v2v',
      modelId: 'happyhorse-1.0-video-edit',
      parameters: {
        duration: 0,
        aspect_ratio: '16:9',
        prompt_extend: false,
      },
      batchSize: 1,
    });
  });

  it('hides duration controls, explains source duration, and keeps inactive drafts', async () => {
    renderBar('en');

    expect(screen.queryByRole('button', { name: /Keep source duration/ })).toBeNull();
    expect(screen.queryByText('5s')).toBeNull();
    expect(screen.getByText(/Source video duration/)).toBeTruthy();
    expect(screen.getByText(/first 15 seconds/)).toBeTruthy();
    expect(screen.getByText(/Some saved settings are kept/)).toBeTruthy();
    expect(usePlaygroundStore.getState().parameters).toMatchObject({
      duration: 0,
      aspect_ratio: '16:9',
      prompt_extend: false,
    });

    expect(screen.getByText('Audio')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Auto/ })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /Advanced/ }));
    expect(screen.getByText('Watermark')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'ON' }).className).toContain('bg-primary');

    fireEvent.click(screen.getByRole('button', { name: 'OFF' }));
    await waitFor(() => expect(usePlaygroundStore.getState().parameters.watermark).toBe(false));

    usePlaygroundStore.setState({ modelId: 'wan2.7-videoedit' });
    await waitFor(() => expect(screen.getByRole('button', { name: /Keep source duration/ })).toBeTruthy());
    expect(usePlaygroundStore.getState().parameters.duration).toBe(0);
    expect(usePlaygroundStore.getState().parameters.prompt_extend).toBe(false);
  });
});
