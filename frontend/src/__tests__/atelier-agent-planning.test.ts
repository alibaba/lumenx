import { describe, expect, it } from 'vitest';
import type { AtelierAgentTurn, AtelierNode } from '@/lib/api';
import {
    getAtelierAgentPlanContext,
    isAgentTurnBlocked,
    isAtelierAgentPlanStale,
    planAtelierAgentTurn,
} from '@/lib/atelierAgentPlanning';

function createVideoNode(overrides: Partial<AtelierNode> = {}): AtelierNode {
    return {
        id: 'video-1',
        project_id: 'atelier-1',
        type: 'video',
        title: 'Shot',
        prompt: 'A rooftop chase',
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
        ...overrides,
    };
}

describe('atelier agent planning', () => {
    it('rejects candidate generation intent when no video node is selected', () => {
        const plan = planAtelierAgentTurn('生成 3 个候选视频', 2, null);

        expect(plan.toolCalls).toHaveLength(0);
        expect(plan.error).toContain('selected video node');
    });

    it('rejects candidate generation intent when the selected video has no references', () => {
        const plan = planAtelierAgentTurn('生成 3 个候选视频', 2, createVideoNode());

        expect(plan.toolCalls).toHaveLength(0);
        expect(plan.error).toContain('reference image');
    });

    it('plans candidate generation when the selected video already has references', () => {
        const plan = planAtelierAgentTurn(
            '生成 3 个候选视频',
            2,
            createVideoNode({
                data: {
                    model: 'wan2.7-i2v',
                    reference_image_urls: ['uploads/ref-a.png'],
                },
            })
        );

        expect(plan.error).toBeNull();
        expect(plan.toolCalls[0].tool_name).toBe('generation.createVideoCandidates');
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
});
