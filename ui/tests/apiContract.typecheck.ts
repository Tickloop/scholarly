import type {
  AgentRun,
  CanvasSummary,
  DirectAgentInput,
  PaperUpdateInput,
} from '../src/types/index'

export const generatedContractExamples = {
  canvas: {
    id: 'canvas-1',
    name: 'Evidence map',
    research_goal: 'Map the field',
    research_brief: { status: 'stored' },
    build_status: 'completed',
    created_at: '2026-08-29T00:00:00Z',
    updated_at: '2026-08-29T00:00:00Z',
  } satisfies CanvasSummary,
  nullableRunPaper: {
    id: 'run-1',
    canvas_id: 'canvas-1',
    status: 'completed',
    paper_id: null,
  } satisfies AgentRun,
  mainAgent: {
    agent: 'main',
    prompt: 'Inspect the active canvas',
    paper_ids: [],
  } satisfies DirectAgentInput,
  unknownDate: {
    year: null,
    month: null,
  } satisfies PaperUpdateInput,
}
