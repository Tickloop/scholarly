import type { PaperRelationship, RelationshipEdge } from '@/types'
import {
  RELATIONSHIP_EDGE_MARKER,
  RELATIONSHIP_EDGE_TYPE,
} from '@/constants'

export function relationshipToEdge(
  relationship: PaperRelationship,
): RelationshipEdge {
  return {
    id: relationship.id,
    // Direction contract: earlier/foundational source -> later/dependent target.
    source: relationship.source,
    target: relationship.target,
    type: RELATIONSHIP_EDGE_TYPE,
    ariaLabel: `Relationship: ${relationship.label}`,
    label: relationship.label,
    markerEnd: { type: RELATIONSHIP_EDGE_MARKER },
    data: { relationship },
  }
}
