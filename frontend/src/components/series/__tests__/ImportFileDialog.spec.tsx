import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

// Mock framer-motion
vi.mock('framer-motion', () => ({
    motion: {
        div: ({ children, ...props }: any) => {
            const { initial, animate, exit, whileHover, ...rest } = props;
            return <div {...rest}>{children}</div>;
        },
    },
    AnimatePresence: ({ children }: any) => <>{children}</>,
}));

// Mock lucide-react icons
vi.mock('lucide-react', () => ({
    X: (props: any) => <span data-testid="icon-x" {...props} />,
    Upload: (props: any) => <span data-testid="icon-upload" {...props} />,
    FileText: (props: any) => <span data-testid="icon-file-text" {...props} />,
    Loader2: (props: any) => <span data-testid="icon-loader" {...props} />,
    ChevronLeft: (props: any) => <span data-testid="icon-chevron-left" {...props} />,
    ChevronRight: (props: any) => <span data-testid="icon-chevron-right" {...props} />,
    Check: (props: any) => <span data-testid="icon-check" {...props} />,
    BookOpen: (props: any) => <span data-testid="icon-book-open" {...props} />,
}));

// Mock API
const mockImportFilePreview = vi.fn();
const mockImportFileConfirm = vi.fn();

vi.mock('@/lib/api', () => ({
    api: {
        importFilePreview: (...args: any[]) => mockImportFilePreview(...args),
        importFileConfirm: (...args: any[]) => mockImportFileConfirm(...args),
    },
}));

import ImportFileDialog from '../ImportFileDialog';

// ── Test data ──

const mockPreviewResult = {
    import_id: 'import-123',
    episodes: [
        { episode_number: 1, title: '第一章', summary: '故事开头', estimated_duration: '5min' },
        { episode_number: 2, title: '第二章', summary: '故事发展' },
    ],
};

const mockCreatedResult = {
    series_id: 'new-series-1',
    episodes: [{ id: 'ep1' }, { id: 'ep2' }],
};

function createMockFile(name: string = 'test.txt', size: number = 1024) {
    return new File(['mock content'], name, { type: 'text/plain' });
}

// ── Helpers ──

const defaultProps = {
    isOpen: true,
    onClose: vi.fn(),
    onSuccess: vi.fn(),
};

function renderDialog(props = {}) {
    return render(<ImportFileDialog {...defaultProps} {...props} />);
}

// ── Tests ──

describe('ImportFileDialog', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    // ── Rendering ──

    describe('Rendering', () => {
        it('does not render when isOpen is false', () => {
            renderDialog({ isOpen: false });
            expect(screen.queryByText('导入文件创建系列')).not.toBeInTheDocument();
        });

        it('renders dialog when isOpen is true', () => {
            renderDialog();
            expect(screen.getByText('导入文件创建系列')).toBeInTheDocument();
        });

        it('shows step indicator with 3 steps', () => {
            renderDialog();
            expect(screen.getByText('上传文件')).toBeInTheDocument();
            expect(screen.getByText('预览分集')).toBeInTheDocument();
            expect(screen.getByText('完成')).toBeInTheDocument();
        });
    });

    // ── Step 1: Upload ──

    describe('Step 1 - Upload', () => {
        it('shows file upload zone', () => {
            renderDialog();
            expect(screen.getByText('拖拽文件到此处，或点击选择')).toBeInTheDocument();
            expect(screen.getByText(/支持 .txt \/ .md 文件/)).toBeInTheDocument();
        });

        it('shows series title input', () => {
            renderDialog();
            expect(screen.getByPlaceholderText('输入系列标题...')).toBeInTheDocument();
        });

        it('shows suggested episodes input', () => {
            renderDialog();
            expect(screen.getByText('建议集数')).toBeInTheDocument();
        });

        it('disables analyze button when no file or title', () => {
            renderDialog();
            const analyzeBtn = screen.getByText('开始分析');
            expect(analyzeBtn).toBeDisabled();
        });

        it('shows file name after selecting a file', async () => {
            renderDialog();
            const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
            const file = createMockFile('my-story.txt');
            fireEvent.change(fileInput, { target: { files: [file] } });
            expect(screen.getByText('my-story.txt')).toBeInTheDocument();
        });

        it('auto-fills title from filename', async () => {
            renderDialog();
            const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
            const file = createMockFile('my-story.txt');
            fireEvent.change(fileInput, { target: { files: [file] } });
            const titleInput = screen.getByPlaceholderText('输入系列标题...') as HTMLInputElement;
            expect(titleInput.value).toBe('my-story');
        });

        it('shows error for unsupported file types', () => {
            renderDialog();
            const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
            const file = new File(['content'], 'doc.pdf', { type: 'application/pdf' });
            fireEvent.change(fileInput, { target: { files: [file] } });
            expect(screen.getByText('仅支持 .txt 和 .md 文件')).toBeInTheDocument();
        });
    });

    // ── Step 1 → Step 2 ──

    describe('Step 1 → Step 2 transition', () => {
        async function setupStep1WithFile() {
            renderDialog();
            const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
            const file = createMockFile('story.txt');
            fireEvent.change(fileInput, { target: { files: [file] } });
            // Title auto-fills from filename
        }

        it('calls API and transitions to step 2 on analyze', async () => {
            mockImportFilePreview.mockResolvedValue(mockPreviewResult);
            await setupStep1WithFile();

            fireEvent.click(screen.getByText('开始分析'));

            await waitFor(() => {
                expect(mockImportFilePreview).toHaveBeenCalled();
            });
            await waitFor(() => {
                expect(screen.getByText(/AI 已将文件分为 2 集/)).toBeInTheDocument();
            });
        });

        it('shows loading state during analysis', async () => {
            mockImportFilePreview.mockReturnValue(new Promise(() => {}));
            await setupStep1WithFile();

            fireEvent.click(screen.getByText('开始分析'));

            expect(screen.getByText('分析中...')).toBeInTheDocument();
        });

        it('shows error when API fails', async () => {
            mockImportFilePreview.mockRejectedValue({ message: '分析出错' });
            await setupStep1WithFile();

            fireEvent.click(screen.getByText('开始分析'));

            await waitFor(() => {
                expect(screen.getByText('分析出错')).toBeInTheDocument();
            });
        });

        it('shows preview episodes in step 2', async () => {
            mockImportFilePreview.mockResolvedValue(mockPreviewResult);
            await setupStep1WithFile();

            fireEvent.click(screen.getByText('开始分析'));

            await waitFor(() => {
                expect(screen.getByText('第一章')).toBeInTheDocument();
            });
            expect(screen.getByText('第二章')).toBeInTheDocument();
            expect(screen.getByText('故事开头')).toBeInTheDocument();
            expect(screen.getByText('EP1')).toBeInTheDocument();
            expect(screen.getByText('EP2')).toBeInTheDocument();
        });
    });

    // ── Step 2 → Step 3 ──

    describe('Step 2 → Step 3 transition', () => {
        async function goToStep2() {
            mockImportFilePreview.mockResolvedValue(mockPreviewResult);
            renderDialog();
            const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
            const file = createMockFile('story.txt');
            fireEvent.change(fileInput, { target: { files: [file] } });
            fireEvent.click(screen.getByText('开始分析'));
            await waitFor(() => {
                expect(screen.getByText('确认创建')).toBeInTheDocument();
            });
        }

        it('calls confirm API and shows success', async () => {
            mockImportFileConfirm.mockResolvedValue(mockCreatedResult);
            await goToStep2();

            fireEvent.click(screen.getByText('确认创建'));

            await waitFor(() => {
                expect(mockImportFileConfirm).toHaveBeenCalledWith({
                    title: 'story',
                    description: undefined,
                    import_id: 'import-123',
                    text: undefined,
                    episodes: mockPreviewResult.episodes,
                });
            });
            await waitFor(() => {
                expect(screen.getByText('系列创建成功')).toBeInTheDocument();
            });
        });

        it('shows loading state during creation', async () => {
            mockImportFileConfirm.mockReturnValue(new Promise(() => {}));
            await goToStep2();

            fireEvent.click(screen.getByText('确认创建'));

            expect(screen.getByText('创建中...')).toBeInTheDocument();
        });

        it('shows error when confirm API fails', async () => {
            mockImportFileConfirm.mockRejectedValue({ message: '创建出错' });
            await goToStep2();

            fireEvent.click(screen.getByText('确认创建'));

            await waitFor(() => {
                expect(screen.getByText('创建出错')).toBeInTheDocument();
            });
        });

        it('shows episode count in success message', async () => {
            mockImportFileConfirm.mockResolvedValue(mockCreatedResult);
            await goToStep2();

            fireEvent.click(screen.getByText('确认创建'));

            await waitFor(() => {
                expect(screen.getByText(/共 2 集/)).toBeInTheDocument();
            });
        });
    });

    // ── Navigation: Back button ──

    describe('Navigation', () => {
        it('goes back to step 1 when "返回修改" is clicked', async () => {
            mockImportFilePreview.mockResolvedValue(mockPreviewResult);
            renderDialog();
            const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
            const file = createMockFile('story.txt');
            fireEvent.change(fileInput, { target: { files: [file] } });
            fireEvent.click(screen.getByText('开始分析'));

            await waitFor(() => {
                expect(screen.getByText('返回修改')).toBeInTheDocument();
            });

            fireEvent.click(screen.getByText('返回修改'));

            // Should be back on step 1
            expect(screen.getByText('开始分析')).toBeInTheDocument();
            expect(screen.getByPlaceholderText('输入系列标题...')).toBeInTheDocument();
        });
    });

    // ── Close ──

    describe('Close', () => {
        it('calls onClose when close button is clicked', () => {
            const onClose = vi.fn();
            renderDialog({ onClose });

            // Click the X button in the header
            const closeButtons = screen.getAllByRole('button');
            // The close button contains the X icon
            const closeBtn = closeButtons.find(btn => btn.querySelector('[data-testid="icon-x"]'));
            fireEvent.click(closeBtn!);

            expect(onClose).toHaveBeenCalled();
        });

        it('calls onClose when cancel button is clicked', () => {
            const onClose = vi.fn();
            renderDialog({ onClose });

            fireEvent.click(screen.getByText('取消'));

            expect(onClose).toHaveBeenCalled();
        });
    });
});
