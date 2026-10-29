import type { ComponentProps } from 'react';
import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import ShotCard, { type ShotNode } from '../storyboard-r2v/ShotCard';

const shot: ShotNode = {
    id: 'f1',
    prompt: 'camera move',
    tabMode: 't2i_i2v',
    t2iImageUrl: 'uploads/source.png',
};

const noop = vi.fn();

function renderShotCard(overrides: Partial<ComponentProps<typeof ShotCard>> = {}) {
    const onGenerateBatch = vi.fn();
    const view = renderWithIntl(<ShotCard
        shot={shot}
        index={0}
        totalShots={1}
        characters={[]}
        scenes={[]}
        props={[]}
        onUpdatePrompt={noop}
        onUpdateField={noop}
        onGenerateT2I={noop}
        onGenerateVideo={noop}
        onDelete={noop}
        onMoveUp={noop}
        onMoveDown={noop}
        onDuplicate={noop}
        onSetTabMode={noop}
        onOpenDrawer={noop}
        onInsertAsset={noop}
        expanded={false}
        onToggleExpanded={noop}
        generateCount={2}
        genSummary="Wan · 5s"
        canGenerate
        onSetGenerateCount={noop}
        onGenerateBatch={onGenerateBatch}
        inFlightCount={0}
        {...overrides}
    />);
    return { ...view, onGenerateBatch };
}

describe('ShotCard generation pending feedback', () => {
    it('disables the real generate CTA with a saving-settings label while edits are pending', () => {
        const { container, onGenerateBatch } = renderShotCard({
            generationBlockedMessage: 'Saving generation settings…',
            generationBlockedBusy: true,
        });

        const button = screen.getByRole('button', { name: /saving generation settings/i });
        expect(button).toBeDisabled();
        expect(container.querySelector('.animate-spin')).not.toBeNull();
        fireEvent.click(button);
        expect(onGenerateBatch).not.toHaveBeenCalled();
    });

    it('disables the real generate CTA without a spinner after a save failure', () => {
        const { container, onGenerateBatch } = renderShotCard({
            generationBlockedMessage: 'Resolve unsaved shot edits before generating.',
            generationBlockedBusy: false,
        });

        const button = screen.getByRole('button', { name: /resolve unsaved shot edits/i });
        expect(button).toBeDisabled();
        expect(container.querySelector('.animate-spin')).toBeNull();
        fireEvent.click(button);
        expect(onGenerateBatch).not.toHaveBeenCalled();
    });
});
