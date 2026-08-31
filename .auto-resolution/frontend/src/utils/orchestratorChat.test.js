import { describe, it, expect } from 'vitest';
import { mergeActivePlanIntoMessages, hydrateOrchestratorFields } from './orchestratorChat';

describe('orchestratorChat helpers', () => {
  it('hydrates orchestrator fields from extra_data', () => {
    const msg = hydrateOrchestratorFields({
      id: '1',
      role: 'assistant',
      content: 'Orchestration plan',
      extra_data: {
        messageType: 'orchestrator_plan',
        orchestratorPlan: { status: 'planning', steps: [{ id: 1 }] },
        orchestratorPlanId: 'plan_abc',
      },
    });

    expect(msg.messageType).toBe('orchestrator_plan');
    expect(msg.orchestratorPlanId).toBe('plan_abc');
    expect(msg.orchestratorPlan.steps).toHaveLength(1);
  });

  it('inserts active plan after the last /plan user message', () => {
    const messages = [
      { id: 'u1', role: 'user', content: '/plan analyze repo', timestamp: '2026-01-01T10:00:00Z' },
      { id: 'u2', role: 'user', content: 'follow up', timestamp: '2026-01-01T10:05:00Z' },
    ];
    const activePlan = { status: 'planning', steps: [{ id: 1, description: 'step' }] };

    const merged = mergeActivePlanIntoMessages(messages, activePlan, 'plan_xyz');

    expect(merged).toHaveLength(3);
    expect(merged[1].orchestratorPlanId).toBe('plan_xyz');
    expect(merged[1].role).toBe('assistant');
    expect(merged[2].id).toBe('u2');
  });

  it('updates existing plan message when plan id already present', () => {
    const messages = [
      {
        id: 'a1',
        role: 'assistant',
        orchestratorPlanId: 'plan_xyz',
        orchestratorPlan: { status: 'planning', steps: [] },
      },
    ];
    const activePlan = { status: 'executing', steps: [{ id: 1 }] };

    const merged = mergeActivePlanIntoMessages(messages, activePlan, 'plan_xyz');

    expect(merged).toHaveLength(1);
    expect(merged[0].orchestratorPlan.status).toBe('executing');
  });
});
