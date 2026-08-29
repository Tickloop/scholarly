import { MarkerType } from '@xyflow/react'

import type { PaperRelationship, RelationshipEdge } from '@/types'

export function relationshipToEdge(
  relationship: PaperRelationship,
): RelationshipEdge {
  return {
    id: relationship.id,
    // Direction contract: earlier/foundational source -> later/dependent target.
    source: relationship.source,
    target: relationship.target,
    type: 'relationship',
    ariaLabel: `Relationship: ${relationship.label}`,
    label: relationship.label,
    markerEnd: { type: MarkerType.Arrow },
    data: { relationship },
  }
}
