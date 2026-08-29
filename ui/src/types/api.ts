import type { Paper, PaperRelationship } from '@/types/research'
import type { components } from '@/types/generated'

export type CanvasId = string
type ApiSchema = components['schemas']

export type CanvasSummary = ApiSchema['CanvasSummary']

export type CanvasSnapshot = Omit<ApiSchema['CanvasSnapshot'], 'papers' | 'relationships'> & {
  papers: Paper[]
  relationships: PaperRelationship[]
}

export type CreateCanvasInput = ApiSchema['CanvasCreate']

export type PaperLinkInput = ApiSchema['PaperLinkCreate']

export type AgentRun = ApiSchema['AgentRunRead']

export type AgentRunDetail = ApiSchema['AgentRunDetail']

export type DirectAgentInput = Omit<ApiSchema['DirectAgentCreate'], 'paper_ids'> & {
  paper_ids: string[]
}

export type CanvasUpdateInput = ApiSchema['CanvasUpdate']

export type ChatMessage = ApiSchema['ChatMessageRead']

export type ChatMessageInput = Omit<ApiSchema['ChatMessageCreate'], 'paper_ids'> & {
  paper_ids: string[]
}

export type ChatSubmission = ApiSchema['ChatSubmission']

export type PaperUpdateInput = ApiSchema['PaperUpdate']

export type ReviewUpdateInput = ApiSchema['ReviewEdit']

export type RelationshipUpdateInput = ApiSchema['RelationshipEdit']

export type UndoResult = ApiSchema['UndoRead']

export type LayoutUpdateInput = ApiSchema['CanvasLayoutUpdate']

export type RunEventType =
  | 'run.started'
  | 'agent.message.delta'
  | 'agent.thread.started'
  | 'tool.started'
  | 'tool.completed'
  | 'paper.added'
  | 'source.completed'
  | 'review.completed'
  | 'relationship.added'
  | 'run.completed'
  | 'run.failed'
  | 'run.cancelled'

export type RunEvent = Omit<ApiSchema['AgentRunEventRead'], 'type'> & {
  type: RunEventType
}

export type RunEventHandlers = {
  onEvent: (event: RunEvent) => void
  onError?: (error: Event) => void
}
