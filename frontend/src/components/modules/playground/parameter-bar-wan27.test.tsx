import { act, fireEvent, render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ParameterBar from './ParameterBar';
import ModelSelector from './ModelSelector';
import PromptInput from './PromptInput';
import ResultCard from './ResultCard';
import DetailPanel from './DetailPanel';
import GalleryView from './GalleryView';
import { usePlaygroundStore, type PlaygroundGeneration, type PlaygroundParameters } from './usePlaygroundStore';
import { wan27ImageDraftIssues } from './playgroundImageContract';
import { resolvePlaygroundCatalogModelId } from './playgroundModelAliases';
import en from '../../../../messages/en.json';
import zh from '../../../../messages/zh.json';

vi.mock('./PromptTemplateModal', () => ({ default: () => null }));
vi.mock('./PromptHistoryDrawer', () => ({ default: () => null }));
beforeEach(() => usePlaygroundStore.setState({ mode: 't2i', modelId: 'wan/wan2.7-image#image', parameters: {}, inputMedia: [], negativePrompt: '', prompt: 'a painting', batchSize: 1, history: [], activeGenerationIds: [] }));
function controls(locale: 'en' | 'zh' = 'en') {
  return render(<NextIntlClientProvider locale={locale} messages={locale === 'en' ? en : zh} timeZone="UTC"><ParameterBar /><PromptInput /></NextIntlClientProvider>);
}
describe('Wan2.7 real controls', () => {
  it.each(['en', 'zh'] as const)('explains the supported controls in %s without inserting defaults', (locale) => {
    controls(locale);
    const messages = locale === 'en' ? en : zh;
    expect(screen.getByText(messages.playground.imageContract.supported)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: messages.playground.parameters.advanced }));
    expect(screen.queryByText(messages.playground.parameters.promptExtend)).toBeNull();
    expect(screen.getByText(messages.playground.parameters.watermark)).toBeTruthy();
    expect(usePlaygroundStore.getState().parameters).toEqual({});
  });
  it.each(['wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'])('keeps the canonical selection %s without provider fallback', (modelId) => {
    usePlaygroundStore.setState({ modelId, parameters: { seed: 0, watermark: false } });
    render(<NextIntlClientProvider locale="en" messages={en} timeZone="UTC"><ModelSelector /></NextIntlClientProvider>);
    expect(usePlaygroundStore.getState().modelId).toBe(resolvePlaygroundCatalogModelId(modelId));
    expect(usePlaygroundStore.getState().parameters).toEqual({ seed: 0, watermark: false });
    expect(screen.getByRole('button', { name: /Wan 2.7 Image/ })).toBeTruthy();
  });
  it('retains fractional seed as invalid until edited and sends explicit zero and false', () => {
    controls(); fireEvent.click(screen.getByRole('button', { name: 'Advanced' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Seed' }), { target: { value: '0.5' } });
    expect(usePlaygroundStore.getState().parameters.seed).toBe(0.5);
    expect(wan27ImageDraftIssues(usePlaygroundStore.getState())).toHaveLength(1);
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Seed' }), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'ON' }));
    fireEvent.click(screen.getByRole('button', { name: 'OFF' }));
    expect(usePlaygroundStore.getState().parameters).toEqual({ seed: 0, watermark: false });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Seed' }), { target: { value: '' } });
    expect(usePlaygroundStore.getState().parameters.seed).toBeUndefined();
    expect(wan27ImageDraftIssues(usePlaygroundStore.getState())).toEqual([]);
  });
  it('keeps invalid size and negative text through image-model switches, enables the old controls with a reference', () => {
    usePlaygroundStore.setState({ parameters: { size: 'old-size', prompt_extend: false }, negativePrompt: 'old negative' });
    controls();
    expect(screen.getByRole('button', { name: 'old-size' })).toBeTruthy();
    expect(screen.getByText(en.playground.imageContract.negativeUnsupported)).toBeTruthy();
    fireEvent.click(screen.getByText('Negative prompt'));
    expect(screen.getByDisplayValue('old negative')).toBeDisabled();
    act(() => usePlaygroundStore.setState({ modelId: 'wan2.7-image-pro' }));
    expect(usePlaygroundStore.getState().parameters.size).toBe('old-size');
    act(() => usePlaygroundStore.setState({ inputMedia: ['uploads/reference.png'] }));
    expect(screen.getByDisplayValue('old negative')).toBeEnabled();
    expect(usePlaygroundStore.getState().parameters.size).toBe('old-size');
    fireEvent.click(screen.getByRole('button', { name: 'Advanced' }));
    expect(screen.getByText('Prompt extend')).toBeTruthy();
    expect(usePlaygroundStore.getState().parameters.prompt_extend).toBe(false);
  });
  it('displays a malformed size safely even when the user adds a reference', () => {
    const size = { old: 1024 };
    usePlaygroundStore.setState({ parameters: { size } as unknown as PlaygroundParameters });
    controls();
    act(() => usePlaygroundStore.setState({ inputMedia: ['uploads/reference.png'] }));
    expect(usePlaygroundStore.getState().parameters.size).toEqual(size);
    expect(screen.getByRole('button', { name: JSON.stringify(size) })).toBeTruthy();
  });
  it.each(['card', 'details', 'gallery'])('renders retained malformed historical values safely in the real %s', (view) => {
    const generation: PlaygroundGeneration = { id: 'old', model_id: 'wan2.7-image', mode: 't2i', prompt: 'old image', parameters: { size: { width: 1024, toString: null }, resolution: ['old'], aspect_ratio: { toString: null }, duration: { toString: null }, prompt_extend: null, custom: { toString: null } } as unknown as PlaygroundParameters, input_media: [], batch_size: 1, outputs: [{ id: 'out', media_path: 'uploads/image.png', media_type: 'image', saved_to_library: false }], created_at: '2026-10-14T00:00:00Z', status: 'completed' };
    usePlaygroundStore.setState({ history: [generation] });
    render(<NextIntlClientProvider locale="en" messages={en} timeZone="UTC">{view === 'card' ? <ResultCard generation={generation} /> : view === 'details' ? <DetailPanel generation={generation} allGenerations={[generation]} onClose={vi.fn()} onNavigate={vi.fn()} /> : <GalleryView generations={[generation]} onOpenDetail={vi.fn()} />}</NextIntlClientProvider>);
    expect(screen.getAllByRole('img', { name: 'old image' }).length).toBeGreaterThan(0);
  });
});
