import { describe, expect, it } from 'vitest';
import type { AtelierAgentTurn } from '@/lib/api';
import {
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
});
