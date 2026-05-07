import { render, screen, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

vi.mock('next/dynamic', () => ({
    default: (loader: unknown) => {
        void loader;
        return function DynamicMock() {
            return <div data-testid="creative-canvas" />;
        };
    },
}));

vi.mock('@/components/layout/PipelineSidebar', () => ({
    default: () => <div data-testid="pipeline-sidebar">PipelineSidebar</div>,
}));

vi.mock('@/components/modules/PropertiesPanel', () => ({
    default: () => <div data-testid="properties-panel">PropertiesPanel</div>,
}));

vi.mock('@/components/modules/ScriptProcessor', () => ({
    default: () => <div data-testid="script-processor">ScriptProcessor</div>,
}));

vi.mock('@/components/modules/VideoGenerator', () => ({
    default: () => <div data-testid="video-generator">VideoGenerator</div>,
}));

vi.mock('@/components/modules/VideoAssembly', () => ({
    default: () => <div data-testid="video-assembly">VideoAssembly</div>,
}));

vi.mock('@/components/modules/ConsistencyVault', () => ({
    default: () => <div data-testid="consistency-vault">ConsistencyVault</div>,
}));

vi.mock('@/components/modules/ArtDirection', () => ({
    default: () => <div data-testid="art-direction">ArtDirection</div>,
}));

vi.mock('@/components/modules/StoryboardComposer', () => ({
    default: () => <div data-testid="storyboard-composer">StoryboardComposer</div>,
}));

vi.mock('@/components/modules/VoiceActingStudio', () => ({
    default: () => <div data-testid="voice-acting-studio">VoiceActingStudio</div>,
}));

vi.mock('@/components/modules/FinalMixStudio', () => ({
    default: () => <div data-testid="final-mix-studio">FinalMixStudio</div>,
}));

vi.mock('@/components/modules/ExportStudio', () => ({
    default: () => <div data-testid="export-studio">ExportStudio</div>,
}));

vi.mock('@/components/common/ModelSettingsModal', () => ({
    default: () => null,
}));

vi.mock('@/components/project/EnvConfigDialog', () => ({
    default: () => null,
}));

vi.mock('@/components/project/PromptConfigModal', () => ({
    default: () => null,
}));

type MockProject = { id: string; title: string } | null;

const mockState: {
    currentProject: MockProject;
    selectProject: ReturnType<typeof vi.fn>;
} = {
    currentProject: null,
    selectProject: vi.fn(),
};

vi.mock('@/store/projectStore', () => ({
    useProjectStore: (selector: (state: typeof mockState) => unknown) => selector(mockState),
}));

import ProjectClient from '../ProjectClient';

describe('ProjectClient', () => {
    beforeEach(() => {
        mockState.currentProject = null;
        mockState.selectProject = vi.fn().mockResolvedValue(undefined);
        vi.clearAllMocks();
    });

    it('resolves project on mount and renders loading first', async () => {
        let resolveSelection: (() => void) | null = null;
        mockState.selectProject = vi.fn().mockImplementation(
            () =>
                new Promise<void>((resolve) => {
                    resolveSelection = () => {
                        mockState.currentProject = { id: 'ep-1', title: '第一集' };
                        resolve();
                    };
                })
        );

        render(<ProjectClient id="ep-1" />);

        expect(screen.getByText('正在加载项目...')).toBeInTheDocument();
        expect(mockState.selectProject).toHaveBeenCalledWith('ep-1');

        if (resolveSelection) {
            (resolveSelection as () => void)();
        }

        await waitFor(() => {
            expect(screen.getByTestId('script-processor')).toBeInTheDocument();
        });
    });

    it('shows not found only after resolving and still missing target project', async () => {
        mockState.selectProject = vi.fn().mockResolvedValue(undefined);

        render(<ProjectClient id="missing-project" />);

        expect(screen.getByText('正在加载项目...')).toBeInTheDocument();

        await waitFor(() => {
            expect(screen.getByText('项目未找到')).toBeInTheDocument();
        });
    });

    it('does not render a stale project with a different id', async () => {
        mockState.currentProject = { id: 'old-project', title: '旧项目' };
        mockState.selectProject = vi.fn().mockResolvedValue(undefined);

        render(<ProjectClient id="new-project" />);

        expect(screen.getByText('正在加载项目...')).toBeInTheDocument();
        expect(screen.queryByText('旧项目')).not.toBeInTheDocument();

        await waitFor(() => {
            expect(screen.getByText('项目未找到')).toBeInTheDocument();
        });
    });
});
