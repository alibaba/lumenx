import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import {
    AgentPanelHistoryList,
    AgentPanelPlannerReadiness,
    AgentPanelSessionOverview,
} from '@/components/atelier/AgentPanelTrace';
import type {
    AtelierAgentPlannerReadiness,
    AtelierAgentSessionSummary,
    AtelierAgentTurnSummary,
} from '@/lib/atelierAgentPlanning';

describe('atelier agent panel components', () => {
    it('renders the session overview from a redacted session summary', () => {
        const summary: AtelierAgentSessionSummary = {
            status: 'waiting_approval',
            focus: {
                label: 'Approval',
                detail: 'Needs review',
                turnId: 'turn-1',
            },
            turnCount: 2,
            plannedCallCount: 1,
            waitingApprovalCount: 1,
            completedCallCount: 3,
            failedCallCount: 0,
            previewTurnCount: 1,
            executeTurnCount: 1,
        };

        const html = renderToStaticMarkup(<AgentPanelSessionOverview summary={summary} />);

        expect(html).toContain('Session');
        expect(html).toContain('waiting_approval');
        expect(html).toContain('Needs review');
        expect(html).toContain('3');
        expect(html).not.toContain('arguments');
        expect(html).not.toContain('result_snapshot');
    });

    it('renders planner readiness as a live-planner contract status', () => {
        const readiness: AtelierAgentPlannerReadiness = {
            status: 'ready',
            label: 'Live planner contract ready',
            detail: 'Adapter unit-test returned a schema-compatible plan context.',
            plannerSchemaVersion: 'atelier.agent.planner.v1',
            toolSchemaVersion: 'atelier.tools.v1',
            adapterName: 'unit-test',
            modelTraceId: 'trace-1',
        };

        const html = renderToStaticMarkup(<AgentPanelPlannerReadiness readiness={readiness} />);

        expect(html).toContain('Readiness');
        expect(html).toContain('Live planner contract ready');
        expect(html).toContain('Planner atelier.agent.planner.v1');
        expect(html).toContain('Tools atelier.tools.v1');
        expect(html).toContain('Trace trace-1');
    });

    it('highlights and expands the focused history turn without raw tool payloads', () => {
        const summaries: AtelierAgentTurnSummary[] = [
            {
                id: 'turn-1',
                title: 'Create a rooftop shot',
                status: 'waiting_approval',
                mode: 'execute',
                callCount: 1,
                completedCount: 0,
                failedCount: 0,
                waitingApprovalCount: 1,
                resultSummary: null,
                toolCalls: [
                    {
                        callId: 'call-1',
                        toolName: 'canvas.createVideoNode',
                        status: 'approval_required',
                        result: 'Node: Rooftop shot',
                    },
                ],
            },
        ];

        const html = renderToStaticMarkup(
            <AgentPanelHistoryList
                summaries={summaries}
                focusedTurnId="turn-1"
                expandedTurnId="turn-1"
                onToggleTurn={vi.fn()}
            />
        );

        expect(html).toContain('History');
        expect(html).toContain('Focus');
        expect(html).toContain('Create a rooftop shot');
        expect(html).toContain('1 waiting approval');
        expect(html).toContain('Node: Rooftop shot');
        expect(html).toContain('border-primary/40');
        expect(html).not.toContain('prompt');
        expect(html).not.toContain('reference_image_urls');
    });

    it('renders nothing when history is empty', () => {
        const html = renderToStaticMarkup(
            <AgentPanelHistoryList
                summaries={[]}
                focusedTurnId={null}
                expandedTurnId={null}
                onToggleTurn={vi.fn()}
            />
        );

        expect(html).toBe('');
    });
});
