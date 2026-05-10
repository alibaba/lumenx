import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AtelierAgentTurn, AtelierProject } from '@/lib/api';

vi.mock('@/lib/api', () => ({
    api: {
        getAtelierProject: vi.fn(),
        listAtelierAgentTools: vi.fn(),
        planAtelierAgentTurn: vi.fn(),
        runAtelierAgentTurn: vi.fn(),
        updateAtelierAgentPolicy: vi.fn(),
        updateAtelierNode: vi.fn(),
    },
}));

const project: AtelierProject = {
    id: 'atelier-1',
    title: 'Board',
    description: '',
    nodes: [
        {
            id: 'node-1',
            project_id: 'atelier-1',
            type: 'video',
            title: 'Shot',
            prompt: '',
            status: 'draft',
            x: 120,
            y: 160,
            width: 420,
            height: 560,
            media_urls: [],
            data: {},
            created_by: 'user',
            created_at: 1,
            updated_at: 1,
        },
        {
            id: 'image-1',
            project_id: 'atelier-1',
            type: 'image',
            title: 'Reference A',
            prompt: '',
            status: 'completed',
            x: 20,
            y: 160,
            width: 220,
            height: 136,
            media_urls: ['uploads/ref-a.png'],
            data: {},
            created_by: 'user',
            created_at: 1,
            updated_at: 1,
        },
    ],
    agent_policy: {
        approval_mode: 'untrusted',
        allowed_tools: [],
        max_nodes_per_action: 8,
        updated_at: 1,
    },
    created_at: 1,
    updated_at: 1,
    agent_turns: [],
};

function cloneProject(): AtelierProject {
    return JSON.parse(JSON.stringify(project)) as AtelierProject;
}

describe('atelier store canvas interactions', () => {
    beforeEach(async () => {
        vi.clearAllMocks();
        const { useAtelierStore } = await import('@/store/atelierStore');
        const freshProject = cloneProject();
        useAtelierStore.setState({
            projects: [freshProject],
            currentProject: freshProject,
            selectedNodeId: null,
            agentTools: [],
            agentTurns: [],
            pendingAgentTurn: null,
            isLoading: false,
            isAgentRunning: false,
            error: null,
        });
    });

    it('loads available agent tools for the current Atelier project', async () => {
        const { api } = await import('@/lib/api');
        const { useAtelierStore } = await import('@/store/atelierStore');
        vi.mocked(api.listAtelierAgentTools).mockResolvedValueOnce([
            {
                name: 'canvas.createVideoNode',
                description: 'Create video node',
                input_schema: { type: 'object' },
                required_permission: 'canvas_write',
                mutates_canvas: true,
                max_count_cost: 1,
                requires_approval: true,
            },
        ]);

        await useAtelierStore.getState().loadAgentTools();

        expect(api.listAtelierAgentTools).toHaveBeenCalledWith('atelier-1');
        expect(useAtelierStore.getState().agentTools.map((tool) => tool.name)).toEqual(['canvas.createVideoNode']);
    });

    it('requests agent plans from Atelier Core instead of local-only planning', async () => {
        const { api } = await import('@/lib/api');
        const { useAtelierStore } = await import('@/store/atelierStore');
        vi.mocked(api.planAtelierAgentTurn).mockResolvedValueOnce({
            project_id: 'atelier-1',
            user_message: 'Create a moonlit chase shot',
            planner: 'model_adapter',
            skill_name: 'idea-to-canvas',
            status: 'ready',
            reason: 'Model planner produced a validated Atelier tool-call plan.',
            tool_calls: [
                {
                    tool_name: 'canvas.createVideoNode',
                    arguments: { title: 'Create a moonlit chase shot', prompt: 'Create a moonlit chase shot' },
                },
            ],
            context: {
                selected_node_id: 'node-1',
                planner_schema_version: 'atelier.agent.planner.v1',
                planner_adapter_name: 'unit-test-adapter',
                tool_schema_version: 'atelier.tools.v1',
                model_trace_id: 'trace-1',
            },
            created_at: 2,
        });

        const plan = await useAtelierStore.getState().planAgentTurn({
            user_message: 'Create a moonlit chase shot',
            selected_node_id: 'node-1',
            planner: 'model_adapter',
            planner_input: {
                schema_version: 'atelier.agent.planner.v1',
                adapter_name: 'unit-test-adapter',
                tool_schema_version: 'atelier.tools.v1',
                model_trace_id: 'trace-1',
                tool_calls: [
                    {
                        tool_name: 'canvas.createVideoNode',
                        arguments: { title: 'Create a moonlit chase shot' },
                    },
                ],
            },
        });

        expect(api.planAtelierAgentTurn).toHaveBeenCalledWith('atelier-1', {
            user_message: 'Create a moonlit chase shot',
            selected_node_id: 'node-1',
            planner: 'model_adapter',
            planner_input: {
                schema_version: 'atelier.agent.planner.v1',
                adapter_name: 'unit-test-adapter',
                tool_schema_version: 'atelier.tools.v1',
                model_trace_id: 'trace-1',
                tool_calls: [
                    {
                        tool_name: 'canvas.createVideoNode',
                        arguments: { title: 'Create a moonlit chase shot' },
                    },
                ],
            },
        });
        expect(plan.tool_calls[0].tool_name).toBe('canvas.createVideoNode');
    });

    it('updates agent policy through the shared Atelier project state', async () => {
        const { api } = await import('@/lib/api');
        const { useAtelierStore } = await import('@/store/atelierStore');
        const updatedProject = {
            ...cloneProject(),
            agent_policy: {
                approval_mode: 'never' as const,
                allowed_tools: ['canvas.readProject'],
                max_nodes_per_action: 4,
                updated_at: 2,
            },
        };
        vi.mocked(api.updateAtelierAgentPolicy).mockResolvedValueOnce(updatedProject);

        await useAtelierStore.getState().updateAgentPolicy({
            approval_mode: 'never',
            allowed_tools: ['canvas.readProject'],
            max_nodes_per_action: 4,
        });

        expect(api.updateAtelierAgentPolicy).toHaveBeenCalledWith('atelier-1', {
            approval_mode: 'never',
            allowed_tools: ['canvas.readProject'],
            max_nodes_per_action: 4,
        });
        expect(useAtelierStore.getState().currentProject?.agent_policy.approval_mode).toBe('never');
        expect(useAtelierStore.getState().currentProject?.agent_policy.allowed_tools).toEqual(['canvas.readProject']);
    });

    it('runs preview agent turns without mutating canvas nodes locally', async () => {
        const { api } = await import('@/lib/api');
        const { useAtelierStore } = await import('@/store/atelierStore');
        const turn: AtelierAgentTurn = {
            id: 'turn-preview',
            project_id: 'atelier-1',
            user_message: 'Create a moonlit chase shot',
            preview: true,
            status: 'completed',
            tool_calls: [
                {
                    call_id: 'call-1',
                    tool_name: 'canvas.createVideoNode',
                    arguments: { title: 'Moonlit chase', prompt: 'Create a moonlit chase shot' },
                    status: 'proposed',
                    approval_required: false,
                    approval_granted: false,
                    created_at: 2,
                },
            ],
            created_at: 2,
        };
        const refreshed = {
            ...cloneProject(),
            agent_turns: [turn],
        };
        vi.mocked(api.runAtelierAgentTurn).mockResolvedValueOnce(turn);
        vi.mocked(api.getAtelierProject).mockResolvedValueOnce(refreshed);

        await useAtelierStore.getState().runAgentTurn({
            user_message: 'Create a moonlit chase shot',
            preview: true,
            tool_calls: [{ tool_name: 'canvas.createVideoNode', arguments: { title: 'Moonlit chase' } }],
        });

        expect(api.runAtelierAgentTurn).toHaveBeenCalledWith('atelier-1', {
            user_message: 'Create a moonlit chase shot',
            preview: true,
            tool_calls: [{ tool_name: 'canvas.createVideoNode', arguments: { title: 'Moonlit chase' } }],
        });
        expect(useAtelierStore.getState().currentProject?.nodes).toHaveLength(2);
        expect(useAtelierStore.getState().agentTurns[0].id).toBe('turn-preview');
        expect(useAtelierStore.getState().pendingAgentTurn).toBeNull();
    });

    it('tracks an agent turn that is waiting for approval', async () => {
        const { api } = await import('@/lib/api');
        const { useAtelierStore } = await import('@/store/atelierStore');
        const turn: AtelierAgentTurn = {
            id: 'turn-approval',
            project_id: 'atelier-1',
            user_message: 'Add a video node',
            preview: false,
            status: 'waiting_approval',
            tool_calls: [
                {
                    call_id: 'call-approval',
                    tool_name: 'canvas.createVideoNode',
                    arguments: { title: 'Needs approval' },
                    status: 'approval_required',
                    approval_required: true,
                    approval_granted: false,
                    created_at: 2,
                },
            ],
            created_at: 2,
        };
        vi.mocked(api.runAtelierAgentTurn).mockResolvedValueOnce(turn);
        vi.mocked(api.getAtelierProject).mockResolvedValueOnce({
            ...cloneProject(),
            agent_turns: [turn],
        });

        await useAtelierStore.getState().runAgentTurn({
            user_message: 'Add a video node',
            tool_calls: [{ tool_name: 'canvas.createVideoNode', arguments: { title: 'Needs approval' } }],
        });

        expect(useAtelierStore.getState().pendingAgentTurn?.id).toBe('turn-approval');
        expect(useAtelierStore.getState().agentTurns).toHaveLength(1);
    });

    it('syncs canvas state after an approved agent turn creates a node', async () => {
        const { api } = await import('@/lib/api');
        const { useAtelierStore } = await import('@/store/atelierStore');
        const waitingTurn: AtelierAgentTurn = {
            id: 'turn-approval',
            project_id: 'atelier-1',
            user_message: 'A neon rooftop reveal',
            preview: false,
            status: 'waiting_approval',
            tool_calls: [
                {
                    call_id: 'call-pending',
                    tool_name: 'canvas.createVideoNode',
                    arguments: { title: 'Agent Shot' },
                    status: 'approval_required',
                    approval_required: true,
                    approval_granted: false,
                    created_at: 2,
                },
            ],
            created_at: 2,
        };
        const createdNode = {
            ...project.nodes[0],
            id: 'agent-video-1',
            title: 'Agent Shot',
            prompt: 'A neon rooftop reveal',
            created_by: 'agent',
        };
        const turn: AtelierAgentTurn = {
            id: 'turn-approval',
            project_id: 'atelier-1',
            user_message: 'A neon rooftop reveal',
            preview: false,
            status: 'completed',
            tool_calls: [
                {
                    call_id: 'call-completed',
                    tool_name: 'canvas.createVideoNode',
                    arguments: { title: 'Agent Shot' },
                    status: 'completed',
                    approval_required: true,
                    approval_granted: true,
                    result_snapshot: { node: createdNode },
                    created_at: 2,
                },
            ],
            created_at: 2,
        };
        vi.mocked(api.runAtelierAgentTurn).mockResolvedValueOnce(turn);
        useAtelierStore.setState({
            projects: [cloneProject()],
            currentProject: {
                ...cloneProject(),
                agent_turns: [waitingTurn],
            },
            selectedNodeId: null,
            agentTools: [],
            agentTurns: [waitingTurn],
            pendingAgentTurn: waitingTurn,
            isLoading: false,
            isAgentRunning: false,
            error: null,
        });
        vi.mocked(api.getAtelierProject).mockResolvedValueOnce({
            ...cloneProject(),
            nodes: [...project.nodes, createdNode],
            agent_turns: [turn],
        });

        await useAtelierStore.getState().runAgentTurn({
            user_message: 'A neon rooftop reveal',
            approve: true,
            turn_id: 'turn-approval',
            tool_calls: [{ tool_name: 'canvas.createVideoNode', arguments: { title: 'Agent Shot' } }],
        });

        expect(useAtelierStore.getState().currentProject?.nodes.map((node) => node.id)).toContain('agent-video-1');
        expect(useAtelierStore.getState().selectedNodeId).toBe('agent-video-1');
        expect(useAtelierStore.getState().pendingAgentTurn).toBeNull();
        expect(api.runAtelierAgentTurn).toHaveBeenCalledWith('atelier-1', {
            user_message: 'A neon rooftop reveal',
            approve: true,
            turn_id: 'turn-approval',
            tool_calls: [{ tool_name: 'canvas.createVideoNode', arguments: { title: 'Agent Shot' } }],
        });
    });

    it('clears a pending agent turn after denial without selecting a new node', async () => {
        const { api } = await import('@/lib/api');
        const { useAtelierStore } = await import('@/store/atelierStore');
        const waitingTurn: AtelierAgentTurn = {
            id: 'turn-approval',
            project_id: 'atelier-1',
            user_message: 'A neon rooftop reveal',
            preview: false,
            status: 'waiting_approval',
            tool_calls: [
                {
                    call_id: 'call-pending',
                    tool_name: 'canvas.createVideoNode',
                    arguments: { title: 'Agent Shot' },
                    status: 'approval_required',
                    approval_required: true,
                    approval_granted: false,
                    created_at: 2,
                },
            ],
            created_at: 2,
        };
        const deniedTurn: AtelierAgentTurn = {
            ...waitingTurn,
            status: 'failed',
            tool_calls: [
                {
                    ...waitingTurn.tool_calls[0],
                    status: 'denied',
                    error: 'User denied approval',
                    completed_at: 3,
                },
            ],
            completed_at: 3,
        };
        const refreshed = {
            ...cloneProject(),
            agent_turns: [deniedTurn],
        };
        useAtelierStore.setState({
            projects: [cloneProject()],
            currentProject: {
                ...cloneProject(),
                agent_turns: [waitingTurn],
            },
            selectedNodeId: 'node-1',
            agentTurns: [waitingTurn],
            pendingAgentTurn: waitingTurn,
        });
        vi.mocked(api.runAtelierAgentTurn).mockResolvedValueOnce(deniedTurn);
        vi.mocked(api.getAtelierProject).mockResolvedValueOnce(refreshed);

        await useAtelierStore.getState().runAgentTurn({
            user_message: 'A neon rooftop reveal',
            deny: true,
            turn_id: 'turn-approval',
            tool_calls: [],
        });

        expect(api.runAtelierAgentTurn).toHaveBeenCalledWith('atelier-1', {
            user_message: 'A neon rooftop reveal',
            deny: true,
            turn_id: 'turn-approval',
            tool_calls: [],
        });
        expect(useAtelierStore.getState().pendingAgentTurn).toBeNull();
        expect(useAtelierStore.getState().selectedNodeId).toBe('node-1');
        expect(useAtelierStore.getState().agentTurns[0].tool_calls[0].status).toBe('denied');
    });

    it('moves a node locally before persistence completes', async () => {
        const { useAtelierStore } = await import('@/store/atelierStore');

        useAtelierStore.getState().moveNodeLocal('node-1', 220, 260);

        const node = useAtelierStore.getState().currentProject?.nodes[0];
        expect(node?.x).toBe(220);
        expect(node?.y).toBe(260);
    });

    it('persists node position through the shared node update API', async () => {
        const { api } = await import('@/lib/api');
        const { useAtelierStore } = await import('@/store/atelierStore');
        vi.mocked(api.updateAtelierNode).mockResolvedValueOnce({
            ...project.nodes[0],
            x: 320,
            y: 360,
        });

        await useAtelierStore.getState().commitNodePosition('node-1', 320, 360);

        expect(api.updateAtelierNode).toHaveBeenCalledWith('atelier-1', 'node-1', { x: 320, y: 360 });
        const node = useAtelierStore.getState().currentProject?.nodes[0];
        expect(node?.x).toBe(320);
        expect(node?.y).toBe(360);
    });

    it('attaches an existing image node as a video reference', async () => {
        const { api } = await import('@/lib/api');
        const { useAtelierStore } = await import('@/store/atelierStore');
        vi.mocked(api.updateAtelierNode)
            .mockResolvedValueOnce({
                ...project.nodes[0],
                data: {
                    reference_image_urls: ['uploads/ref-a.png'],
                    reference_node_ids: ['image-1'],
                },
            })
            .mockResolvedValueOnce({
                ...project.nodes[1],
                data: {
                    parent_node_id: 'node-1',
                    reference_role: 'video_reference_image',
                },
            });

        await useAtelierStore.getState().attachReferenceNode('node-1', 'image-1');

        expect(api.updateAtelierNode).toHaveBeenNthCalledWith(1, 'atelier-1', 'node-1', {
            data: {
                reference_image_urls: ['uploads/ref-a.png'],
                reference_node_ids: ['image-1'],
            },
        });
        expect(api.updateAtelierNode).toHaveBeenNthCalledWith(2, 'atelier-1', 'image-1', {
            data: {
                parent_node_id: 'node-1',
                reference_role: 'video_reference_image',
            },
        });
        const nodes = useAtelierStore.getState().currentProject?.nodes ?? [];
        expect(nodes[0].data.reference_node_ids).toEqual(['image-1']);
        expect(nodes[1].data.parent_node_id).toBe('node-1');
    });

    it('keeps repeated reference attachment idempotent', async () => {
        const { api } = await import('@/lib/api');
        const { useAtelierStore } = await import('@/store/atelierStore');
        const linkedProject = cloneProject();
        linkedProject.nodes[0].data = {
            reference_image_urls: ['uploads/ref-a.png'],
            reference_node_ids: ['image-1'],
        };
        linkedProject.nodes[1].data = {
            parent_node_id: 'node-1',
            reference_role: 'video_reference_image',
        };
        useAtelierStore.setState({
            projects: [linkedProject],
            currentProject: linkedProject,
        });
        vi.mocked(api.updateAtelierNode)
            .mockResolvedValueOnce({
                ...linkedProject.nodes[0],
                data: {
                    reference_image_urls: ['uploads/ref-a.png'],
                    reference_node_ids: ['image-1'],
                },
            })
            .mockResolvedValueOnce(linkedProject.nodes[1]);

        await useAtelierStore.getState().attachReferenceNode('node-1', 'image-1');

        expect(api.updateAtelierNode).toHaveBeenNthCalledWith(1, 'atelier-1', 'node-1', {
            data: {
                reference_image_urls: ['uploads/ref-a.png'],
                reference_node_ids: ['image-1'],
            },
        });
        const nodes = useAtelierStore.getState().currentProject?.nodes ?? [];
        expect(nodes[0].data.reference_image_urls).toEqual(['uploads/ref-a.png']);
        expect(nodes[0].data.reference_node_ids).toEqual(['image-1']);
    });

    it('rejects attaching one image node to a different video while already bound', async () => {
        const { api } = await import('@/lib/api');
        const { useAtelierStore } = await import('@/store/atelierStore');
        const linkedProject = cloneProject();
        linkedProject.nodes.push({
            ...linkedProject.nodes[0],
            id: 'node-2',
            title: 'Second Shot',
            data: {},
        });
        linkedProject.nodes[1].data = {
            parent_node_id: 'node-1',
            reference_role: 'video_reference_image',
        };
        useAtelierStore.setState({
            projects: [linkedProject],
            currentProject: linkedProject,
        });

        await expect(useAtelierStore.getState().attachReferenceNode('node-2', 'image-1')).rejects.toThrow(
            'Reference node is already attached to another video node'
        );
        expect(api.updateAtelierNode).not.toHaveBeenCalled();
    });

    it('detaches a reference without deleting the image node', async () => {
        const { api } = await import('@/lib/api');
        const { useAtelierStore } = await import('@/store/atelierStore');
        const linkedProject = cloneProject();
        linkedProject.nodes[0].data = {
            reference_image_urls: ['uploads/ref-a.png'],
            reference_node_ids: ['image-1'],
        };
        linkedProject.nodes[1].data = {
            parent_node_id: 'node-1',
            reference_role: 'video_reference_image',
        };
        useAtelierStore.setState({
            projects: [linkedProject],
            currentProject: linkedProject,
        });
        vi.mocked(api.updateAtelierNode)
            .mockResolvedValueOnce({
                ...linkedProject.nodes[0],
                data: {
                    reference_image_urls: [],
                    reference_node_ids: [],
                },
            })
            .mockResolvedValueOnce({
                ...linkedProject.nodes[1],
                data: {},
            });

        await useAtelierStore.getState().detachReferenceNode('node-1', 'uploads/ref-a.png', 'image-1');

        expect(api.updateAtelierNode).toHaveBeenNthCalledWith(1, 'atelier-1', 'node-1', {
            data: {
                reference_image_urls: [],
                reference_node_ids: [],
            },
        });
        expect(api.updateAtelierNode).toHaveBeenNthCalledWith(2, 'atelier-1', 'image-1', {
            data: {},
        });
        const nodes = useAtelierStore.getState().currentProject?.nodes ?? [];
        expect(nodes).toHaveLength(2);
        expect(nodes[0].data.reference_image_urls).toEqual([]);
        expect(nodes[1].data.parent_node_id).toBeUndefined();
    });
});
