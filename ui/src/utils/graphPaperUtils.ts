import {
  PaperEdgeType,
  PaperNodeType,
  type Paper,
  type PaperEdge,
  type PaperNode,
  type PaperRelationship,
} from '@/types'
import {
  DEFAULT_NODE_POSITION,
  RELATIONSHIP_EDGE_MARKER,
} from '@/constants'

import type { XYPosition } from '@xyflow/react'

export function paperToNode(
  paper: Paper,
  position: XYPosition = DEFAULT_NODE_POSITION,
): PaperNode {
  return {
    id: paper.id,
    type: PaperNodeType,
    ariaLabel: `Paper: ${paper.title}`,
    position,
    data: { paper },
  }
}

export function relationshipToEdge(
  relationship: PaperRelationship,
): PaperEdge {
  return {
    id: relationship.id,
    source: relationship.source,
    target: relationship.target,
    type: PaperEdgeType,
    ariaLabel: `Relationship: ${relationship.label}`,
    label: relationship.label,
    markerEnd: { type: RELATIONSHIP_EDGE_MARKER },
    data: { relationship },
  }
}

export function papersToNodes(papers: Paper[]): PaperNode[] {
  return papers.map((paper) =>
    paperToNode(paper, {
      x: paper.x ?? DEFAULT_NODE_POSITION.x,
      y: paper.y ?? DEFAULT_NODE_POSITION.y,
    }),
  )
}
