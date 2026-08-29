import '@/components/RelationshipDetail.css'
import type { RelationshipDetailProps } from '@/types'

export function RelationshipDetail({ relationship }: RelationshipDetailProps) {
  return (
    <aside
      aria-label={`Relationship detail: ${relationship.label}`}
      className="relationship-detail"
    >
      <p className="relationship-detail__label">{relationship.label}</p>
      <p className="relationship-detail__explanation">
        {relationship.explanation}
      </p>
    </aside>
  )
}
