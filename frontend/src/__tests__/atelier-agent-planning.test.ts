import { describe, expect, it } from 'vitest';
import type { AtelierAgentTurn } from '@/lib/api';
import {
    getAtelierAgentPlanContext,
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
});
