import { fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import ParamsSection, { type ParamsState } from '../storyboard-r2v/shot-panel/ParamsSection';
import type { I2VModelConfig } from '@/lib/modelCatalog';

const models: I2VModelConfig[] = Array.from({ length: 12 }, (_, index) => ({
    id: `model-${index + 1}`,
    name: `Model ${index + 1}`,
    description: `Model ${index + 1} description`,
    duration: { type: 'fixed', value: 5 },
    params: {
        resolution: { options: ['720P', '1080P'], default: '1080P' },
        ratio: { options: ['16:9', '9:16'], default: '16:9' },
    },
}));

const params: ParamsState = {
    model: 'model-1',
    duration: 5,
    count: 1,
    resolution: '1080P',
    ratio: '16:9',
};

function setViewport(width: number, height: number) {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
}

function mockTriggerRect(rect: Partial<DOMRect>) {
    const original = HTMLElement.prototype.getBoundingClientRect;
    const spy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function getBoundingClientRect(this: HTMLElement) {
        if (this.getAttribute('aria-haspopup') === 'listbox') {
            return {
                x: rect.left ?? 0,
                y: rect.top ?? 0,
                left: rect.left ?? 0,
                top: rect.top ?? 0,
                right: rect.right ?? ((rect.left ?? 0) + (rect.width ?? 160)),
                bottom: rect.bottom ?? ((rect.top ?? 0) + (rect.height ?? 28)),
                width: rect.width ?? 160,
                height: rect.height ?? 28,
                toJSON: () => ({}),
            } as DOMRect;
        }
        return original.call(this);
    });
    return () => { spy.mockRestore(); };
}

function modelTrigger() {
    return screen.getAllByRole('button', { name: /model 1/i }).at(-1) as HTMLElement;
}

function renderParams(onChange = vi.fn()) {
    return {
        onChange,
        ...renderWithIntl(<ParamsSection
            shotId="shot-1"
            title="Generate video"
            modelList={models}
            params={params}
            onChange={onChange}
        />),
    };
}

afterEach(() => {
    vi.restoreAllMocks();
});

describe('ParamsSection model menu viewport behavior', () => {
    it('flips the portaled model menu above a bottom-edge trigger and keeps choices clickable', () => {
        setViewport(1280, 800);
        const restoreRect = mockTriggerRect({ left: 900, top: 766, bottom: 794, width: 220, right: 1120 });
        const { onChange } = renderParams();

        fireEvent.click(modelTrigger());

        const listbox = screen.getByRole('listbox', { name: /model/i });
        expect(Number.parseFloat(listbox.style.top)).toBeLessThan(766);
        expect(Number.parseFloat(listbox.style.top)).toBeGreaterThanOrEqual(8);
        expect(Number.parseFloat(listbox.style.maxHeight)).toBeLessThanOrEqual(784);

        fireEvent.click(screen.getByRole('option', { name: 'Model 12' }));
        expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ model: 'model-12' }));
        expect(screen.queryByRole('listbox', { name: /model/i })).toBeNull();
        restoreRect();
    });

    it('clamps right-edge and short-viewport placement without closing on internal scroll', () => {
        setViewport(320, 180);
        const restoreRect = mockTriggerRect({ left: 285, top: 70, bottom: 98, width: 120, right: 405 });
        renderParams();

        fireEvent.click(modelTrigger());
        const listbox = screen.getByRole('listbox', { name: /model/i });

        expect(Number.parseFloat(listbox.style.left)).toBeGreaterThanOrEqual(8);
        expect(Number.parseFloat(listbox.style.left) + Number.parseFloat(listbox.style.width)).toBeLessThanOrEqual(312);
        expect(Number.parseFloat(listbox.style.maxHeight)).toBeLessThanOrEqual(164);

        fireEvent.scroll(listbox);
        expect(screen.getByRole('listbox', { name: /model/i })).toBeInTheDocument();

        fireEvent.scroll(window);
        expect(screen.queryByRole('listbox', { name: /model/i })).toBeNull();
        restoreRect();
    });

    it('closes the model menu on Escape', () => {
        setViewport(1280, 800);
        const restoreRect = mockTriggerRect({ left: 120, top: 120, bottom: 148, width: 200, right: 320 });
        renderParams();

        fireEvent.click(modelTrigger());
        expect(screen.getByRole('listbox', { name: /model/i })).toBeInTheDocument();

        fireEvent.keyDown(window, { key: 'Escape' });
        expect(screen.queryByRole('listbox', { name: /model/i })).toBeNull();
        restoreRect();
    });
});
