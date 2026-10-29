import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import VideoQueue from '../VideoQueue';
import type { VideoTask } from '@/lib/api';

function task(status: VideoTask['status']): VideoTask {
    return {
        id: 'task-recover',
        project_id: 'a',
        frame_id: 'f1',
        image_url: '/first.png',
        prompt: 'move camera',
        status,
        duration: 5,
        resolution: '720p',
        generate_audio: false,
        prompt_extend: true,
        created_at: 1,
        provider_name: 'dashscope',
        provider_task_id: 'provider-task-1',
        provider_request_id: 'request-1',
    };
}

describe('VideoQueue recovered task state', () => {
    it('shows needs-confirmation as read-only recovery, not failed retry', () => {
        const onRemix = vi.fn();
        renderWithIntl(<VideoQueue tasks={[task('needs_confirmation')]} onRemix={onRemix} />);

        expect(screen.getByText('结果待确认，未重新生成')).toBeInTheDocument();
        expect(screen.getByText('provider-task-1')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /重试任务|Retry Task/i })).not.toBeInTheDocument();
        fireEvent.click(screen.getByText('结果待确认，未重新生成'));
        expect(onRemix).not.toHaveBeenCalled();
    });
});
