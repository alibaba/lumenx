import { act, fireEvent, render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { beforeEach, describe, expect, it } from 'vitest';
import ParameterBar from './ParameterBar';
import { usePlaygroundStore } from './usePlaygroundStore';
import { buildPlaygroundRequestParameters } from './playgroundParameterContract';
import en from '../../../../messages/en.json';

beforeEach(() => usePlaygroundStore.setState({
  mode: 'i2v',
  modelId: 'kling-v3-i2v',
  parameters: { resolution: '720p', seed: 0, prompt_extend: true },
  batchSize: 1,
}));

describe('Kling parameter controls', () => {
  it('shows provider quality/audio controls without unsupported generic I2V fallbacks', () => {
    render(<NextIntlClientProvider locale="en" messages={en} timeZone="UTC"><ParameterBar /></NextIntlClientProvider>);
    expect(screen.getByText('Output quality')).toBeTruthy();
    expect(screen.queryByText('Aspect ratio')).toBeNull();
    expect(screen.queryByText('Resolution')).toBeNull();
    expect(screen.getByRole('button', { name: '1080P' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '1080P' }));
    fireEvent.click(screen.getByText('4K', { exact: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Advanced' }));
    expect(screen.getByText('Generate audio')).toBeTruthy();
    expect(screen.queryByPlaceholderText('Random')).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { name: 'ON' })[0]);
    expect(buildPlaygroundRequestParameters('kling-v3-i2v', usePlaygroundStore.getState().parameters)).toMatchObject({
      mode: '4k',
      audio: true,
      watermark: false,
    });
    expect(usePlaygroundStore.getState().parameters.seed).toBe(0);
    act(() => usePlaygroundStore.setState({ mode: 'r2v', modelId: 'kling/kling-v3-video#r2v' }));
    expect(screen.getByText('Aspect ratio')).toBeTruthy();
    expect(screen.getByRole('button', { name: '4K' })).toBeTruthy();
  });
});

describe('Vidu parameter controls', () => {
  it('shows R2V ratio, resolution, and generic audio controls from the catalog', () => {
    act(() => usePlaygroundStore.setState({
      mode: 'r2v',
      modelId: 'viduq3-pro-r2v',
      parameters: { resolution: '1080P', aspect_ratio: '9:16', audio: false, watermark: false, seed: 0 },
      batchSize: 1,
    }));

    render(<NextIntlClientProvider locale="en" messages={en} timeZone="UTC"><ParameterBar /></NextIntlClientProvider>);

    expect(screen.getByText('Aspect ratio')).toBeTruthy();
    expect(screen.getByText('Resolution')).toBeTruthy();
    expect(screen.getByRole('button', { name: '9:16' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '1080P' })).toBeTruthy();
    expect(screen.queryByText('Output quality')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Advanced' }));
    expect(screen.getByText('Generate audio')).toBeTruthy();
    expect(screen.getByText('Watermark')).toBeTruthy();
    expect(screen.queryByText('Prompt enhance')).toBeNull();
    expect(buildPlaygroundRequestParameters('viduq3-pro-r2v', usePlaygroundStore.getState().parameters)).toMatchObject({
      resolution: '1080P',
      aspect_ratio: '9:16',
      audio: false,
      watermark: false,
      seed: 0,
    });
  });
});
