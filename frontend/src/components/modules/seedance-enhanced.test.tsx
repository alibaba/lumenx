// @vitest-environment jsdom

import { type HTMLAttributes, type ReactNode, useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { VideoTask } from '@/lib/api';
import type { VideoParams } from '@/store/projectStore';
import VideoQueue from './VideoQueue';
import VideoSidebar from './VideoSidebar';

vi.mock('framer-motion', () => ({
    AnimatePresence: ({ children }: { children: ReactNode }) => <>{children}</>,
    motion: new Proxy({}, {
        get: () => ({
            children,
            layout,
            initial,
            animate,
            exit,
            transition,
            ...props
        }: HTMLAttributes<HTMLDivElement> & Record<string, unknown>) => <div {...props}>{children}</div>,
    }),
}));

const baseParams: VideoParams = {
    resolution: '720p',
    duration: 5,
    seed: 123,
    generateAudio: false,
    audioUrl: '',
    promptExtend: false,
    negativePrompt: '',
    batchSize: 1,
    cameraMovement: 'none',
    subjectMotion: 'still',
    model: 'doubao-seedance-2-0-260128',
    shotType: 'single',
    generationMode: 'i2v',
    referenceVideoUrls: [],
    aspectRatio: 'adaptive',
    watermark: true,
    cameraFixed: false,
    referenceAudioUrl: '',
    seedanceReferenceMode: 'image',
    seedanceWorkflow: 'standard',
    seedanceExtendMode: 'continue',
    seedanceEditMode: 'subject_replace',
    seedancePreviewOnly: false,
    mode: 'std',
    sound: false,
    cfgScale: 0.5,
    viduAudio: true,
    movementAmplitude: 'auto',
};

function SidebarHarness({ initialParams }: { initialParams?: Partial<VideoParams> }) {
    const [params, setParams] = useState<VideoParams>({
        ...baseParams,
        ...initialParams,
    });

    return (
        <VideoSidebar
            tasks={[]}
            onRemix={() => undefined}
            params={params}
            setParams={setParams}
        />
    );
}

describe('Seedance 增强交互', () => {
    it('开启仅预览模式后，状态卡切换为手动预览锁定', () => {
        render(<SidebarHarness />);

        fireEvent.click(screen.getByRole('button', { name: '仅预览 payload' }));

        expect(screen.getByTestId('seedance-submit-status')).toHaveTextContent(
            '当前由你手动开启了“仅预览 payload”',
        );
    });

    it('非标准工作流在缺少参考视频时会自动锁预览', () => {
        render(<SidebarHarness />);

        fireEvent.click(screen.getByRole('button', { name: '视频编辑' }));

        expect(screen.getByTestId('seedance-submit-status')).toHaveTextContent(
            '当前工作流缺少必需参考视频',
        );
    });

    it('任务卡片会回显 Seedance 标签组合', () => {
        const tasks: VideoTask[] = [
            {
                id: 'task-seedance-001',
                project_id: 'project-1',
                image_url: 'https://example.com/input.png',
                prompt: 'replace the umbrella with a neon parasol',
                status: 'completed',
                video_url: 'https://example.com/output.mp4',
                created_at: Date.now() / 1000,
                duration: 5,
                resolution: '480p',
                generate_audio: false,
                prompt_extend: false,
                model: 'doubao-seedance-2-0-260128',
                aspect_ratio: '9:16',
                watermark: false,
                camera_fixed: true,
                reference_audio_url: 'https://example.com/guide.wav',
                seedance_reference_mode: 'combo',
                seedance_workflow: 'edit',
                seedance_edit_mode: 'object_edit',
                reference_video_urls: ['https://example.com/source.mp4'],
            },
        ];

        render(<VideoQueue tasks={tasks} onRemix={() => undefined} />);

        expect(screen.getByText('视频编辑 · 对象增删改')).toBeInTheDocument();
        expect(screen.getByText('组合参考')).toBeInTheDocument();
        expect(screen.getByText('画幅 9:16')).toBeInTheDocument();
        expect(screen.getByText('无水印')).toBeInTheDocument();
        expect(screen.getByText('固定机位')).toBeInTheDocument();
        expect(screen.getByText('1 路参考视频')).toBeInTheDocument();
        expect(screen.getByText('外部音频')).toBeInTheDocument();
    });
});
