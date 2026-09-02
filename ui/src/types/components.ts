import type { Edge, Node } from '@xyflow/react'

import type { Paper, PaperRelationship } from '@/types/research'

export type PaperCanvasNode = Node<{ paper: Paper }, 'paper'>

export type RelationshipEdge = Edge<
  { relationship: PaperRelationship },
  'relationship'
>
