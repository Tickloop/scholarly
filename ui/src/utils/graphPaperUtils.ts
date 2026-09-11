import { MarkerType, type XYPosition } from '@xyflow/react'

import {
  PaperEdgeType,
  PaperNodeType,
  type Paper,
  type PaperEdge,
  type PaperNode,
  type PaperRelationship,
} from '@/types'

export function formatPaperDate(year: number, month: number): string {
  const formatter = new Intl.DateTimeFormat('en', {
    month: 'short',
    timeZone: 'UTC',
  })

  return `${formatter.format(new Date(Date.UTC(year, month - 1)))} ${year}`
}

export function paperToNode(
  paper: Paper,
  position: XYPosition = { x: 0, y: 0 },
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
    markerEnd: { type: MarkerType.Arrow },
    data: { relationship },
  }
}

export function papersToNodes(papers: Paper[]): PaperNode[] {
  return papers.map((paper) =>
    paperToNode(paper, {
      x: paper.x ?? 0,
      y: paper.y ?? 0,
    }),
  )
}
