import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import PromptInput from './PromptInput';
import { usePlaygroundStore } from './usePlaygroundStore';
import en from '../../../../messages/en.json';

vi.mock('./PromptTemplateModal', () => ({ default: () => null }));
vi.mock('./PromptHistoryDrawer', () => ({ default: () => null }));

function renderPromptInput() {
  return render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <PromptInput />
    </NextIntlClientProvider>,
  );
}

describe('PromptInput migrated parameter contract hints', () => {
  beforeEach(() => {
    usePlaygroundStore.setState({
      mode: 'i2v',
      modelId: 'wan2.7-i2v',
      prompt: 'a bright frame',
      negativePrompt: '',
      parameters: {},
    });
  });

  it('keeps a Wan negative prompt visible but inactive on HappyHorse, then restores editing on Wan', async () => {
    renderPromptInput();

    fireEvent.click(screen.getByText('Negative prompt'));
    const wanTextarea = screen.getByPlaceholderText("What you don't want to see…") as HTMLTextAreaElement;
    expect(wanTextarea.disabled).toBe(false);
    fireEvent.change(wanTextarea, { target: { value: 'no blur' } });
    expect(usePlaygroundStore.getState().negativePrompt).toBe('no blur');
    fireEvent.click(screen.getByText('Negative prompt'));

    usePlaygroundStore.setState({ modelId: 'happyhorse-1.1-i2v' });
    await waitFor(() => expect(screen.getByText('Inactive')).toBeTruthy());
    expect(screen.getByText('Inactive')).toBeTruthy();
    expect(screen.getByText(/will not use it/)).toBeTruthy();
    expect(screen.queryByDisplayValue('no blur')).toBeNull();

    fireEvent.click(screen.getByText('Negative prompt'));
    const happyHorseTextarea = screen.getByDisplayValue('no blur') as HTMLTextAreaElement;
    expect(happyHorseTextarea.disabled).toBe(true);

    usePlaygroundStore.setState({ modelId: 'wan2.7-i2v' });
    await waitFor(() => expect(screen.queryByText('Inactive')).toBeNull());
    expect((screen.getByDisplayValue('no blur') as HTMLTextAreaElement).disabled).toBe(false);
  });
});
