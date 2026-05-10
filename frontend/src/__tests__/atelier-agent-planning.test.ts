import { describe, expect, it } from 'vitest';
import type { AtelierAgentTurn } from '@/lib/api';
import {
    getAtelierAgentTurnSummaries,
    getAtelierPlanContextRows,
    getAtelierAgentPlanContext,
    getAtelierPlannerPackageRows,
    isAgentTurnBlocked,
    isAtelierAgentPlanStale,
    validateAtelierAgentIntent,
} from '@/lib/atelierAgentPlanning';

describe('atelier agent planning', () => {
    it('keeps local planning helpers limited to client-side intent validation', () => {
        const emptyPlan = validateAtelierAgentIntent('   ');
        const readyPlan = validateAtelierAgentIntent('生成 3 个候选视频');

        expect(emptyPlan.error).toContain('Enter an intent');
        expect(emptyPlan.toolCalls).toHaveLength(0);
        expect(readyPlan.error).toBeNull();
        expect(readyPlan.toolCalls).toHaveLength(0);
    });

    it('blocks new turns while a pending approval exists', () => {
        const pendingTurn = {
            id: 'turn-1',
            project_id: 'atelier-1',
            user_message: 'Add one shot',
            preview: false,
            status: 'waiting_approval',
            tool_calls: [],
            created_at: 1,
        } as AtelierAgentTurn;

        expect(isAgentTurnBlocked(pendingTurn)).toBe(true);
        expect(isAgentTurnBlocked(null)).toBe(false);
    });

    it('invalidates a planned turn when the selected node context changes', () => {
        const previous = getAtelierAgentPlanContext('atelier-1', 1, 2, 'video-1', 1);
        const current = getAtelierAgentPlanContext('atelier-1', 1, 2, 'video-2', 1);

        expect(isAtelierAgentPlanStale(previous, current)).toBe(true);
    });

    it('invalidates a planned turn when the project context changes', () => {
        const previous = getAtelierAgentPlanContext('atelier-1', 1, 2, 'video-1', 1);
        const updatedProject = getAtelierAgentPlanContext('atelier-1', 2, 2, 'video-1', 1);
        const addedNode = getAtelierAgentPlanContext('atelier-1', 1, 3, 'video-1', 1);

        expect(isAtelierAgentPlanStale(previous, updatedProject)).toBe(true);
        expect(isAtelierAgentPlanStale(previous, addedNode)).toBe(true);
    });

    it('summarizes planner packages without exposing raw canvas payloads', () => {
        const rows = getAtelierPlannerPackageRows({
            project_id: 'atelier-1',
            user_message: 'Generate candidates',
            selected_node_id: 'video-1',
            planner_schema_version: 'atelier.agent.planner.v1',
            tool_schema_version: 'atelier.tools.v1',
            output_contract: {},
            tool_schemas: [
                {
                    name: 'create_video_node',
                    description: 'Create a video node',
                    input_schema: {},
                    required_permission: 'canvas_write',
                    mutates_canvas: true,
                    max_count_cost: 1,
                    requires_approval: true,
                },
            ],
            project_snapshot: {
                id: 'atelier-1',
                title: 'Atelier Test',
                description: '',
                node_count: 1,
                nodes: [],
            },
            selected_node_snapshot: {
                id: 'video-1',
                project_id: 'atelier-1',
                type: 'video',
                title: 'Shot A',
                prompt: 'A long prompt should not appear in rows',
                status: 'idle',
                x: 0,
                y: 0,
                width: 320,
                height: 220,
                media_urls: [],
                data: {},
                created_by: 'user',
                created_at: 1,
                updated_at: 1,
            },
            policy_snapshot: {
                approval_mode: 'untrusted',
                allowed_tools: [],
                max_nodes_per_action: 4,
                updated_at: 1,
            },
            created_at: 1,
        });

        expect(rows).toContainEqual({ label: 'Tool scope', value: '1 registered' });
        expect(rows).toContainEqual({ label: 'Selected', value: 'Shot A · video' });
        expect(rows.map((row) => row.value).join(' ')).not.toContain('long prompt');
    });

    it('summarizes redacted planner context keys instead of raw model input', () => {
        const rows = getAtelierPlanContextRows({
            selected_node_id: 'video-1',
            planner_schema_version: 'atelier.agent.planner.v1',
            planner_adapter_name: 'unit-test-adapter',
            tool_schema_version: 'atelier.tools.v1',
            model_trace_id: 'trace-1',
            planner_input: {
                adapter_name: 'unit-test-adapter',
                schema_version: 'atelier.agent.planner.v1',
                tool_schema_version: 'atelier.tools.v1',
            },
        });

        expect(rows).toContainEqual({ label: 'Adapter', value: 'unit-test-adapter' });
        expect(rows).toContainEqual({ label: 'Model trace', value: 'trace-1' });
        expect(rows).toContainEqual({ label: 'Context keys', value: 'adapter_name, schema_version, tool_schema_version' });
    });

    it('summarizes turn history without exposing raw tool arguments or snapshots', () => {
        const summaries = getAtelierAgentTurnSummaries([
            {
                id: 'turn-1',
                project_id: 'atelier-1',
                user_message: 'Create a rooftop shot',
                preview: false,
                status: 'completed',
                tool_calls: [
                    {
                        call_id: 'call-1',
                        tool_name: 'canvas.createVideoNode',
                        arguments: {
                            prompt: 'Raw prompt should not be copied into history summary',
                        },
                        status: 'completed',
                        approval_required: true,
                        approval_granted: true,
                        result_snapshot: {
                            node: {
                                id: 'node-1',
                                title: 'Rooftop shot',
                                prompt: 'Raw result prompt should not be copied into history summary',
                            },
                        },
                        created_at: 1,
                    },
                    {
                        call_id: 'call-2',
                        tool_name: 'generation.createVideoCandidates',
                        arguments: {
                            reference_image_urls: ['raw-ref.png'],
                        },
                        status: 'failed',
                        approval_required: false,
                        approval_granted: false,
                        error: 'Missing reference image',
                        result_snapshot: null,
                        created_at: 2,
                    },
                ],
                created_at: 1,
            },
        ]);

        expect(summaries).toHaveLength(1);
        expect(summaries[0]).toMatchObject({
            title: 'Create a rooftop shot',
            mode: 'execute',
            callCount: 2,
            completedCount: 1,
            failedCount: 1,
            waitingApprovalCount: 0,
            resultSummary: 'Node: Rooftop shot',
        });
        expect(summaries[0].toolCalls).toEqual([
            {
                callId: 'call-1',
                toolName: 'canvas.createVideoNode',
                status: 'completed',
                result: 'Node: Rooftop shot',
            },
            {
                callId: 'call-2',
                toolName: 'generation.createVideoCandidates',
                status: 'failed',
                result: 'Missing reference image',
            },
        ]);
        expect(JSON.stringify(summaries)).not.toContain('Raw prompt');
        expect(JSON.stringify(summaries)).not.toContain('raw-ref.png');
    });
});
