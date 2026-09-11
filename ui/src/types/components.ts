import type { Edge, Node } from '@xyflow/react'

import type { Paper, PaperRelationship } from '@/types/research'

export const PaperNodeType = 'paper' as const
export type PaperNodeData = { paper: Paper }
export type PaperNode = Node<PaperNodeData, typeof PaperNodeType>

export const PaperEdgeType = 'relationship' as const
export type PaperEdgeData = { relationship: PaperRelationship }
export type PaperEdge = Edge<PaperEdgeData, typeof PaperEdgeType>

export type GraphNode = PaperNode
export type GraphEdge = PaperEdge
