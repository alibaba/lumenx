import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { beforeEach, describe, expect, it } from 'vitest';
import ParameterBar from './ParameterBar';
import { usePlaygroundStore } from './usePlaygroundStore';
import { buildPlaygroundRequestParameters } from './playgroundParameterContract';
import en from '../../../../messages/en.json';
import zh from '../../../../messages/zh.json';

beforeEach(() => usePlaygroundStore.setState({ mode: 't2v', modelId: 'seedance-2.0-t2v', parameters: {}, batchSize: 1 }));
describe('Seedance parameter controls', () => {
  it.each(['en', 'zh'] as const)('shows product defaults, preserves hidden watermark, and emits the current mode contract (%s)', locale => {
    const messages = locale === 'en' ? en : zh;
    render(<NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC"><ParameterBar /></NextIntlClientProvider>);
    expect(screen.getByRole('button', { name: '1080p' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '16:9' })).toBeTruthy();
    expect(screen.getByRole('textbox')).toHaveValue('5');
    fireEvent.click(screen.getByRole('button', { name: messages.playground.parameters.advanced }));
    const audio = screen.getByText(messages.playground.parameters.generateAudio).parentElement!;
    expect(within(audio).getByRole('button', { name: 'ON' }).className).toContain('bg-primary');
    fireEvent.click(within(audio).getByRole('button', { name: 'OFF' }));
    const watermark = screen.getByText(messages.playground.parameters.watermark).parentElement!;
    fireEvent.click(within(watermark).getByRole('button', { name: 'ON' }));
    act(() => usePlaygroundStore.setState({ mode: 'i2v', modelId: 'seedance-2.0-i2v' }));
    expect(screen.queryByText(messages.playground.parameters.watermark)).toBeNull();
    expect(usePlaygroundStore.getState().parameters.watermark).toBe(true);
    expect(buildPlaygroundRequestParameters('seedance-2.0-i2v', usePlaygroundStore.getState().parameters)).toEqual({ duration: 5, resolution: '1080p', aspect_ratio: '16:9', audio: false });
    act(() => usePlaygroundStore.setState({ mode: 't2v', modelId: 'seedance-2.0-t2v' }));
    expect(buildPlaygroundRequestParameters('seedance-2.0-t2v', usePlaygroundStore.getState().parameters).watermark).toBe(true);
  });
});
