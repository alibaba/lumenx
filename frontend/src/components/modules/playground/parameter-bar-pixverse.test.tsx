import { act, fireEvent, render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { beforeEach, describe, expect, it } from 'vitest';
import ParameterBar from './ParameterBar';
import { usePlaygroundStore } from './usePlaygroundStore';
import { buildPlaygroundRequestParameters } from './playgroundParameterContract';
import en from '../../../../messages/en.json';
import zh from '../../../../messages/zh.json';

beforeEach(() => usePlaygroundStore.setState({
  mode: 'r2v', modelId: 'pixverse-v5.6-r2v', parameters: { resolution: '720P', duration: 10 }, batchSize: 1,
}));

function renderBar(locale: 'en' | 'zh' = 'en') {
  return render(<NextIntlClientProvider locale={locale} messages={locale === 'en' ? en : zh} timeZone="UTC"><ParameterBar /></NextIntlClientProvider>);
}

describe('PixVerse parameter controls', () => {
  it('changes valid duration choices with resolution without overwriting the saved draft', () => {
    renderBar();
    expect(screen.getByRole('button', { name: '10s' }).className).toContain('bg-primary');
    fireEvent.click(screen.getByRole('button', { name: '720P' }));
    fireEvent.click(screen.getByText('1080P', { exact: true }));
    expect(screen.queryByRole('button', { name: '10s' })).toBeNull();
    expect(screen.getByRole('button', { name: '5s' }).className).toContain('bg-primary');
    expect(usePlaygroundStore.getState().parameters.duration).toBe(10);
    expect(buildPlaygroundRequestParameters('pixverse-v5.6-r2v', usePlaygroundStore.getState().parameters)).toMatchObject({ resolution: '1080P', duration: 5 });
    fireEvent.click(screen.getByRole('button', { name: '1080P' }));
    fireEvent.click(screen.getByText('720P', { exact: true }));
    expect(screen.getByRole('button', { name: '10s' }).className).toContain('bg-primary');
    expect(buildPlaygroundRequestParameters('pixverse-v5.6-r2v', usePlaygroundStore.getState().parameters).duration).toBe(10);
  });

  it.each([
    ['en', 'Shot type', 'Single shot', 'Multi-shot'],
    ['zh', '镜头类型', '单镜头', '多镜头'],
  ] as const)('localizes V6 shot choices and keeps their draft when switching to C1 (%s)', (locale, label, single, multi) => {
    usePlaygroundStore.setState({ mode: 'i2v', modelId: 'pixverse/pixverse-v6-video', parameters: {} });
    renderBar(locale);
    expect(screen.getByText(label)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: single }));
    fireEvent.click(screen.getByText(multi, { exact: true }));
    expect(buildPlaygroundRequestParameters('pixverse/pixverse-v6-video', usePlaygroundStore.getState().parameters).shot_type).toBe('multi');
    act(() => usePlaygroundStore.setState({ modelId: 'pixverse-c1-i2v' }));
    expect(screen.queryByText(label)).toBeNull();
    expect(buildPlaygroundRequestParameters('pixverse-c1-i2v', usePlaygroundStore.getState().parameters)).not.toHaveProperty('shot_type');
    expect(usePlaygroundStore.getState().parameters.shot_type).toBe('multi');
    act(() => usePlaygroundStore.setState({ modelId: 'pixverse/pixverse-v6-video' }));
    expect(screen.getByRole('button', { name: multi })).toBeTruthy();
  });
});
