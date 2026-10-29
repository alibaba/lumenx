import { screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import ShotCard, { type ShotNode } from '../storyboard-r2v/ShotCard';

vi.mock('@/components/shared/preview/PreviewVideo', () => ({
    default: ({
        src,
        alt,
        className,
        noLightbox,
        hoverPlay,
    }: {
        src?: string;
        alt?: string;
        className?: string;
        noLightbox?: boolean;
        hoverPlay?: boolean;
    }) => (
        <video
            data-testid="preview-video"
            src={src}
            aria-label={alt}
            className={className}
            data-no-lightbox={String(noLightbox)}
            data-hover-play={String(hoverPlay)}
        />
    ),
}));

const noop = vi.fn();

function renderCard(shot: ShotNode) {
    renderWithIntl(<ShotCard
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
        onUnpinVideo={noop}
    />);
}

describe('ShotCard recovered video task chrome', () => {
    it('lets needs-confirmation dominate pinned/final-take chrome even with a video URL', () => {
        renderCard({
            id: 'f1',
            prompt: 'move camera',
            tabMode: 'direct_r2v',
            videoStatus: 'needs_confirmation',
            videoTaskId: 'recover-task',
            videoUrl: '/historical.mp4',
            isVideoPinned: true,
            finalTakeId: 'recover-task',
        });

        expect(screen.getByText('结果待确认，未重新生成')).toBeInTheDocument();
        expect(screen.getByText('recover-task')).toBeInTheDocument();
        expect(screen.queryByText('已选 take')).not.toBeInTheDocument();
        expect(screen.queryByText('解锁')).not.toBeInTheDocument();
        expect(screen.queryByLabelText(/Generated video|已生成视频/i)).not.toBeInTheDocument();
    });

    it('renders recovered history video as passive background media', () => {
        renderCard({
            id: 'f1',
            prompt: 'move camera',
            tabMode: 'direct_r2v',
            videoStatus: 'needs_confirmation',
            videoTaskId: 'recover-task',
            videoUrl: '/historical.mp4',
        });

        const historicalVideo = screen.getByTestId('preview-video');
        expect(historicalVideo).toHaveAttribute('data-no-lightbox', 'true');
        expect(historicalVideo).toHaveAttribute('data-hover-play', 'false');
        expect(historicalVideo).toHaveClass('pointer-events-none');
    });

    it.each(['pending', 'processing'] as const)(
        'keeps needs-confirmation badge dominant when t2i is %s and old selected chrome exists',
        (t2iStatus) => {
            renderCard({
                id: 'f1',
                prompt: 'move camera',
                tabMode: 't2i_i2v',
                videoStatus: 'needs_confirmation',
                t2iStatus,
                videoTaskId: 'recover-task',
                videoUrl: '/historical.mp4',
                isVideoPinned: true,
                finalTakeId: 'recover-task',
            });

            expect(screen.getByText('结果待确认，未重新生成')).toBeInTheDocument();
            expect(screen.queryByText('已选 take')).not.toBeInTheDocument();
            expect(screen.queryByText('生成中')).not.toBeInTheDocument();
            expect(screen.queryByText('等待中')).not.toBeInTheDocument();
        },
    );
});
