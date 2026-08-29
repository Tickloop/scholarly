import type { OnEdgesChange, OnNodesChange } from '@xyflow/react'
import type { ReactNode } from 'react'

import type {
  CreatePaperInput,
  CreateRelationshipInput,
  Paper,
  PaperId,
  PaperRelationship,
  PaperReview,
} from '@/types/research'
import type {
  PaperUpdateInput,
  RelationshipUpdateInput,
  ReviewUpdateInput,
} from '@/types/api'
import type { LayoutPosition } from '@/types/layout'
import type {
  PaperCanvasNode,
  RelationshipEdge,
} from '@/types/components'

export type CanvasContextValue = {
  nodes: PaperCanvasNode[]
  edges: RelationshipEdge[]
  onNodesChange: OnNodesChange<PaperCanvasNode>
  onEdgesChange: OnEdgesChange<RelationshipEdge>
  createPaperNode: (input: CreatePaperInput) => Paper
  deletePaperNode: (paperId: PaperId) => void
  assignPaperReview: (paperId: PaperId, review: PaperReview) => void
  createRelationshipEdge: (
    input: CreateRelationshipInput,
  ) => PaperRelationship
  deleteRelationshipEdge: (relationshipId: string) => void
  editPaper?: (paperId: string, input: PaperUpdateInput) => void
  retryPaperProcessing?: (paperId: string) => void
  removePaper?: (paperId: string) => void
  editReview?: (paperId: string, input: ReviewUpdateInput) => void
  regeneratePaperReview?: (paperId: string) => void
  editRelationship?: (
    relationshipId: string,
    input: RelationshipUpdateInput,
  ) => void
  removeRelationship?: (relationshipId: string) => void
  persistLayout?: (positions: LayoutPosition[]) => void | Promise<void>
}

export type CanvasProviderProps = {
  children: ReactNode
  papers?: Paper[]
  relationships?: PaperRelationship[]
  editPaper?: CanvasContextValue['editPaper']
  retryPaperProcessing?: CanvasContextValue['retryPaperProcessing']
  removePaper?: CanvasContextValue['removePaper']
  editReview?: CanvasContextValue['editReview']
  regeneratePaperReview?: CanvasContextValue['regeneratePaperReview']
  editRelationship?: CanvasContextValue['editRelationship']
  removeRelationship?: CanvasContextValue['removeRelationship']
  persistLayout?: CanvasContextValue['persistLayout']
}
