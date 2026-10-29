import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import LumenXBranding from '../LumenXBranding';
import { DEFAULT_THEME, useSettingsStore } from '@/store/settingsStore';

const originalBasePath = process.env.NEXT_PUBLIC_APP_BASE_PATH;

beforeEach(() => {
    process.env.NEXT_PUBLIC_APP_BASE_PATH = '/static';
    useSettingsStore.setState({ theme: DEFAULT_THEME, locale: 'zh', animations: true });
});

afterEach(() => {
    if (originalBasePath === undefined) delete process.env.NEXT_PUBLIC_APP_BASE_PATH;
    else process.env.NEXT_PUBLIC_APP_BASE_PATH = originalBasePath;
});

describe('LumenXBranding public logo assets', () => {
    it('renders the default dark logo under the configured app base path', () => {
        render(<LumenXBranding showSlogan={false} />);

        expect(screen.getByAltText('LumenX')).toHaveAttribute('src', '/static/logo-dark.png');
    });

    it('renders the selected light theme logo under the configured app base path after mount', async () => {
        useSettingsStore.setState({ theme: 'brand-light' });

        render(<LumenXBranding showSlogan={false} />);

        await waitFor(() => {
            expect(screen.getByAltText('LumenX')).toHaveAttribute('src', '/static/logo-light.png');
        });
    });
});
