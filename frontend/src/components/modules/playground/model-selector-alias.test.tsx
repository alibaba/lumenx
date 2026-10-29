import { render, screen, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { beforeEach, describe, expect, it } from 'vitest';

import ModelSelector from './ModelSelector';
import { resolvePlaygroundCatalogModelId } from './playgroundModelAliases';
import { usePlaygroundStore, type PlaygroundMode } from './usePlaygroundStore';
import en from '../../../../messages/en.json';

function renderSelector() {
  return render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <ModelSelector />
    </NextIntlClientProvider>,
  );
}

function resetSelector(mode: PlaygroundMode, modelId: string) {
  usePlaygroundStore.setState({
    mode,
    modelId,
    modelPreferences: {},
    parameters: {},
  });
}

describe('ModelSelector alias membership', () => {
  beforeEach(() => {
    resetSelector('i2v', 'wan/wan2.7-video#i2v');
  });

  it.each([
    ['t2v', 'seedance/seedance-2.0-video#t2v', 'seedance-2.0-t2v', /Seedance/i],
    ['i2v', 'seedance/seedance-2.0-video#i2v', 'seedance-2.0-i2v', /Seedance/i],
    ['r2v', 'seedance/seedance-2.0-video#r2v', 'seedance-2.0-r2v', /Seedance/i],
    ['i2v', 'wan/wan2.7-video#i2v', 'wan2.7-i2v', /Wan 2\.7/i],
    ['v2v', 'wan/wan2.7-video#videoedit', 'wan2.7-videoedit', /VideoEdit/i],
    ['t2v', 'happyhorse/happyhorse-1.1-t2v#i2v', 'happyhorse-1.1-t2v', /HappyHorse/i],
  ] as const)('does not fall back across providers for %s alias %s', async (mode, modelId, expectedModelId, label) => {
    resetSelector(mode, modelId);

    renderSelector();

    await waitFor(() => {
      expect(resolvePlaygroundCatalogModelId(usePlaygroundStore.getState().modelId)).toBe(expectedModelId);
    });
    expect(usePlaygroundStore.getState().modelId).not.toMatch(/^happyhorse-1\.1-i2v$/);
    expect(screen.getByRole('button', { name: label })).toBeTruthy();
  });
});
