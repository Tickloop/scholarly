import type { Edge, Node, XYPosition } from '@xyflow/react'
import type { ReactNode } from 'react'

import type {
  Paper,
  PaperRelationship,
  PaperReview,
} from '@/types/research'
import type {
  CanvasId,
  CanvasSummary,
  ChatMessage,
  CreateCanvasInput,
} from '@/types/api'

export type PaperCanvasNode = Node<{
  paper: Paper
  onActivate?: (origin?: HTMLElement) => void
}, 'paper'>

export type RelationshipEdge = Edge<
  {
    relationship: PaperRelationship
    onActivate?: () => void
  },
  'relationship'
>

export type PaperDetailProps = {
  paper: Paper
  review?: PaperReview
}

export type PaperReviewSectionProps = {
  title: string
  content?: string | null
}

export type RelationshipDetailProps = {
  relationship: PaperRelationship
}

export type CanvasProps = {
  children?: ReactNode
  controls?: ReactNode
  selectedPaperId?: string
  selectedRelationshipId?: string
  onPaperSelect?: (paperId?: string) => void
  onRelationshipSelect?: (relationshipId?: string) => void
  onPaperLinkPaste?: (url: string, position: XYPosition) => void
  onPaperMove?: (paperId: string, position: XYPosition) => void
  onRelayout?: () => void
}

export type CanvasLauncherProps = {
  canvas?: CanvasSummary
  papers: Paper[]
  messages: ChatMessage[]
  selectedCanvasId?: CanvasId
  selectedPaper?: Paper
  activeAgent?: string
  isBusy: boolean
  error?: string
  runStatus?: string
  runStatusLabel?: string
  onCreate: (input: CreateCanvasInput) => void | Promise<void>
  onAddPaperLink: (url: string) => void | Promise<void>
  onSendMessage: (content: string, paperIds: string[]) => void | Promise<void>
  onClearPaper: () => void
  onStop?: () => void
  onRetry?: () => void
  onUndo?: () => void
}

export type CanvasDrawerProps = {
  canvases: CanvasSummary[]
  selectedCanvasId?: CanvasId
  isBusy: boolean
  onSelect: (canvasId?: CanvasId) => void
  onRename: (canvasId: CanvasId, name: string) => void | Promise<void>
  onDelete: (canvasId: CanvasId) => void | Promise<void>
  onRefresh?: () => void | Promise<void>
}
