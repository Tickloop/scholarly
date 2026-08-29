import '@/components/RelationshipDetail.css'
import type { RelationshipDetailProps } from '@/types'

export function RelationshipDetail({ relationship }: RelationshipDetailProps) {
  return (
    <aside aria-label={`Relationship detail: ${relationship.label}`}>
      <p className="relationship-detail">{relationship.explanation}</p>
    </aside>
  )
}
