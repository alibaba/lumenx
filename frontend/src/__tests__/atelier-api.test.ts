import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('axios', () => ({
    default: {
        get: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
        delete: vi.fn(),
    },
}));

const mockedAxios = vi.mocked(axios, true);

describe('atelier API client', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('creates canvas projects through the shared Atelier Core endpoint', async () => {
        mockedAxios.post.mockResolvedValueOnce({
            data: { id: 'atelier-1', title: 'Board', nodes: [] },
        });
        const { api } = await import('@/lib/api');

        const result = await api.createAtelierProject('Board', 'Explore branches', 'studio-1');

        expect(mockedAxios.post).toHaveBeenCalledWith('http://localhost:17177/atelier/projects', {
            title: 'Board',
            description: 'Explore branches',
            source_project_id: 'studio-1',
        });
        expect(result.id).toBe('atelier-1');
    });

    it('updates Codex-style agent approval policy', async () => {
        mockedAxios.put.mockResolvedValueOnce({
            data: {
                id: 'atelier-1',
                agent_policy: {
                    approval_mode: 'never',
                    allowed_tools: ['canvas.createNode'],
                    max_nodes_per_action: 6,
                },
            },
        });
        const { api } = await import('@/lib/api');

        const result = await api.updateAtelierAgentPolicy('atelier-1', {
            approval_mode: 'never',
            allowed_tools: ['canvas.createNode'],
            max_nodes_per_action: 6,
        });

        expect(mockedAxios.put).toHaveBeenCalledWith(
            'http://localhost:17177/atelier/projects/atelier-1/agent_policy',
            {
                approval_mode: 'never',
                allowed_tools: ['canvas.createNode'],
                max_nodes_per_action: 6,
            }
        );
        expect(result.agent_policy.approval_mode).toBe('never');
    });

    it('submits denied agent approvals with the pending turn id', async () => {
        mockedAxios.post.mockResolvedValueOnce({
            data: {
                id: 'turn-1',
                status: 'failed',
                tool_calls: [],
            },
        });
        const { api } = await import('@/lib/api');

        await api.runAtelierAgentTurn('atelier-1', {
            user_message: 'Reject this plan',
            tool_calls: [],
            deny: true,
            turn_id: 'turn-1',
        });

        expect(mockedAxios.post).toHaveBeenCalledWith(
            'http://localhost:17177/atelier/projects/atelier-1/agent/turns',
            {
                user_message: 'Reject this plan',
                tool_calls: [],
                deny: true,
                turn_id: 'turn-1',
            }
        );
    });

    it('asks Atelier Core to plan agent tool calls from user intent', async () => {
        mockedAxios.post.mockResolvedValueOnce({
            data: {
                project_id: 'atelier-1',
                user_message: 'Create a moonlit chase shot',
                planner: 'model_adapter',
                skill_name: 'idea-to-canvas',
                status: 'ready',
                reason: 'Model planner produced a validated Atelier tool-call plan.',
                tool_calls: [
                    {
                        tool_name: 'canvas.createVideoNode',
                        arguments: { prompt: 'Create a moonlit chase shot' },
                    },
                ],
                context: {
                    selected_node_id: null,
                    planner_schema_version: 'atelier.agent.planner.v1',
                    planner_adapter_name: 'unit-test-adapter',
                    tool_schema_version: 'atelier.tools.v1',
                    model_trace_id: 'trace-1',
                    planner_input: {
                        schema_version: 'atelier.agent.planner.v1',
                        adapter_name: 'unit-test-adapter',
                        tool_schema_version: 'atelier.tools.v1',
                        model_trace_id: 'trace-1',
                        skill_name: 'idea-to-canvas',
                        tool_calls: [
                            {
                                tool_name: 'canvas.createVideoNode',
                                arguments: { title: 'Moonlit chase' },
                            },
                        ],
                    },
                },
                created_at: 2,
            },
        });
        const { api } = await import('@/lib/api');

        const plan = await api.planAtelierAgentTurn('atelier-1', {
            user_message: 'Create a moonlit chase shot',
            selected_node_id: null,
            planner: 'model_adapter',
            planner_input: {
                schema_version: 'atelier.agent.planner.v1',
                adapter_name: 'unit-test-adapter',
                tool_schema_version: 'atelier.tools.v1',
                model_trace_id: 'trace-1',
                skill_name: 'idea-to-canvas',
                tool_calls: [
                    {
                        tool_name: 'canvas.createVideoNode',
                        arguments: { title: 'Moonlit chase' },
                    },
                ],
            },
        });

        expect(mockedAxios.post).toHaveBeenCalledWith(
            'http://localhost:17177/atelier/projects/atelier-1/agent/plan',
            {
                user_message: 'Create a moonlit chase shot',
                selected_node_id: null,
                planner: 'model_adapter',
                planner_input: {
                    schema_version: 'atelier.agent.planner.v1',
                    adapter_name: 'unit-test-adapter',
                    tool_schema_version: 'atelier.tools.v1',
                    model_trace_id: 'trace-1',
                    skill_name: 'idea-to-canvas',
                    tool_calls: [
                        {
                            tool_name: 'canvas.createVideoNode',
                            arguments: { title: 'Moonlit chase' },
                        },
                    ],
                },
            }
        );
        expect(plan.planner).toBe('model_adapter');
        expect(plan.tool_calls[0].tool_name).toBe('canvas.createVideoNode');
    });

    it('creates nodes with Studio resource references but without depending on Studio UI', async () => {
        mockedAxios.post.mockResolvedValueOnce({
            data: {
                id: 'node-1',
                project_id: 'atelier-1',
                type: 'video',
                video_task_id: 'task-1',
            },
        });
        const { api } = await import('@/lib/api');

        await api.createAtelierNode('atelier-1', {
            type: 'video',
            title: 'Branch A',
            source_project_id: 'studio-1',
            frame_id: 'frame-1',
            asset_id: 'asset-1',
            video_task_id: 'task-1',
            media_urls: ['output/video/branch-a.mp4'],
        });

        expect(mockedAxios.post).toHaveBeenCalledWith(
            'http://localhost:17177/atelier/projects/atelier-1/nodes',
            {
                type: 'video',
                title: 'Branch A',
                source_project_id: 'studio-1',
                frame_id: 'frame-1',
                asset_id: 'asset-1',
                video_task_id: 'task-1',
                media_urls: ['output/video/branch-a.mp4'],
            }
        );
    });

    it('lists agent tools and runs a bounded agent turn through the harness endpoint', async () => {
        mockedAxios.get.mockResolvedValueOnce({
            data: [
                {
                    name: 'canvas.createVideoNode',
                    description: 'Create a video node',
                    input_schema: { type: 'object' },
                    required_permission: 'canvas_write',
                    mutates_canvas: true,
                    max_count_cost: 1,
                    requires_approval: false,
                },
            ],
        });
        mockedAxios.post.mockResolvedValueOnce({
            data: {
                id: 'turn-1',
                project_id: 'atelier-1',
                user_message: 'Create a shot',
                preview: false,
                status: 'waiting_approval',
                tool_calls: [
                    {
                        call_id: 'call-1',
                        tool_name: 'canvas.createVideoNode',
                        arguments: { prompt: 'A chase' },
                        status: 'approval_required',
                        approval_required: true,
                        approval_granted: false,
                        created_at: 1,
                    },
                ],
                created_at: 1,
            },
        });
        const { api } = await import('@/lib/api');

        const tools = await api.listAtelierAgentTools('atelier-1');
        const turn = await api.runAtelierAgentTurn('atelier-1', {
            user_message: 'Create a shot',
            tool_calls: [
                {
                    tool_name: 'canvas.createVideoNode',
                    arguments: { prompt: 'A chase' },
                },
            ],
            preview: false,
            approve: false,
        });

        expect(mockedAxios.get).toHaveBeenCalledWith('http://localhost:17177/atelier/projects/atelier-1/agent/tools');
        expect(mockedAxios.post).toHaveBeenCalledWith('http://localhost:17177/atelier/projects/atelier-1/agent/turns', {
            user_message: 'Create a shot',
            tool_calls: [
                {
                    tool_name: 'canvas.createVideoNode',
                    arguments: { prompt: 'A chase' },
                },
            ],
            preview: false,
            approve: false,
        });
        expect(tools[0].name).toBe('canvas.createVideoNode');
        expect(turn.tool_calls[0].status).toBe('approval_required');
    });

    it('includes the target turn id when approving an existing pending agent turn', async () => {
        mockedAxios.post.mockResolvedValueOnce({
            data: {
                id: 'turn-approval',
                project_id: 'atelier-1',
                user_message: 'Approve this turn',
                preview: false,
                status: 'completed',
                tool_calls: [],
                created_at: 1,
            },
        });
        const { api } = await import('@/lib/api');

        await api.runAtelierAgentTurn('atelier-1', {
            user_message: 'Approve this turn',
            tool_calls: [{ tool_name: 'canvas.createVideoNode', arguments: { prompt: 'A chase' } }],
            approve: true,
            turn_id: 'turn-approval',
        });

        expect(mockedAxios.post).toHaveBeenCalledWith('http://localhost:17177/atelier/projects/atelier-1/agent/turns', {
            user_message: 'Approve this turn',
            tool_calls: [{ tool_name: 'canvas.createVideoNode', arguments: { prompt: 'A chase' } }],
            approve: true,
            turn_id: 'turn-approval',
        });
    });

    it('creates and manages video candidates for a canvas node', async () => {
        mockedAxios.post
            .mockResolvedValueOnce({
                data: {
                    id: 'node-1',
                    data: { candidates: [{ id: 'candidate-1', status: 'pending' }] },
                },
            })
            .mockResolvedValueOnce({
                data: {
                    id: 'node-1',
                    data: { selected_candidate_id: 'candidate-1' },
                },
            });
        mockedAxios.delete.mockResolvedValueOnce({
            data: { id: 'node-1', data: { candidates: [] } },
        });
        const { api } = await import('@/lib/api');

        await api.createAtelierVideoCandidates('atelier-1', 'node-1', {
            prompt: 'A camera pushes toward the city skyline',
            model: 'wan2.7-i2v',
            reference_image_urls: ['uploads/ref.png'],
            batch_size: 3,
            params: { duration: 5, resolution: '720p' },
        });
        await api.selectAtelierVideoCandidate('atelier-1', 'node-1', 'candidate-1');
        await api.deleteAtelierVideoCandidate('atelier-1', 'node-1', 'candidate-2');

        expect(mockedAxios.post).toHaveBeenNthCalledWith(
            1,
            'http://localhost:17177/atelier/projects/atelier-1/nodes/node-1/video_candidates',
            {
                prompt: 'A camera pushes toward the city skyline',
                model: 'wan2.7-i2v',
                reference_image_urls: ['uploads/ref.png'],
                batch_size: 3,
                params: { duration: 5, resolution: '720p' },
            }
        );
        expect(mockedAxios.post).toHaveBeenNthCalledWith(
            2,
            'http://localhost:17177/atelier/projects/atelier-1/nodes/node-1/video_candidates/select',
            { candidate_id: 'candidate-1' }
        );
        expect(mockedAxios.delete).toHaveBeenCalledWith(
            'http://localhost:17177/atelier/projects/atelier-1/nodes/node-1/video_candidates/candidate-2'
        );
    });

    it('retries one candidate and regenerates the whole candidate round', async () => {
        mockedAxios.post
            .mockResolvedValueOnce({
                data: {
                    id: 'node-1',
                    status: 'processing',
                    data: { candidates: [{ id: 'candidate-1', status: 'pending', retry_count: 1 }] },
                },
            })
            .mockResolvedValueOnce({
                data: {
                    id: 'node-1',
                    status: 'processing',
                    data: { candidates: [{ id: 'candidate-2', status: 'pending' }] },
                },
            });
        const { api } = await import('@/lib/api');

        await api.retryAtelierVideoCandidate('atelier-1', 'node-1', 'candidate-1');
        await api.regenerateAtelierVideoCandidates('atelier-1', 'node-1', {
            prompt: 'Fresh take',
            model: 'wan2.7-i2v',
            reference_image_urls: ['uploads/ref.png'],
            batch_size: 1,
            params: { duration: 8, resolution: '1080p' },
        });

        expect(mockedAxios.post).toHaveBeenNthCalledWith(
            1,
            'http://localhost:17177/atelier/projects/atelier-1/nodes/node-1/video_candidates/candidate-1/retry'
        );
        expect(mockedAxios.post).toHaveBeenNthCalledWith(
            2,
            'http://localhost:17177/atelier/projects/atelier-1/nodes/node-1/video_candidates/regenerate',
            {
                prompt: 'Fresh take',
                model: 'wan2.7-i2v',
                reference_image_urls: ['uploads/ref.png'],
                batch_size: 1,
                params: { duration: 8, resolution: '1080p' },
            }
        );
    });
});
