import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ShotCard from '../storyboard-r2v/ShotCard';
import { renderWithIntl } from '../../series/__tests__/test-utils';

describe('real ShotCard structural controls', () => {
    it('gives each icon an accessible name and disables structural callbacks while saving', () => {
        const changed = vi.fn();
        const props = {
            shot: { id: 'frame', prompt: 'A quiet room', tabMode: 'direct_r2v' as const },
            index: 1, totalShots: 3, characters: [], scenes: [], props: [], expanded: false,
            onUpdatePrompt: vi.fn(), onUpdateField: vi.fn(), onGenerateT2I: vi.fn(), onGenerateVideo: vi.fn(),
            onDelete: changed, onMoveUp: changed, onMoveDown: changed, onDuplicate: changed,
            onSetTabMode: vi.fn(), onOpenDrawer: vi.fn(), onInsertAsset: vi.fn(), onToggleExpanded: vi.fn(),
        };
        const view = renderWithIntl(<ShotCard {...props} structureDisabled />);
        for (const name of ['上移', '下移', '复制镜头', '删除镜头']) {
            const button = screen.getByRole('button', { name });
            expect(button).toBeDisabled(); fireEvent.click(button);
        }
        expect(changed).not.toHaveBeenCalled();
        view.rerender(<ShotCard {...props} structureDisabled={false} />);
        fireEvent.click(screen.getByRole('button', { name: '复制镜头' }));
        expect(changed).toHaveBeenCalledTimes(1);
    });
});
