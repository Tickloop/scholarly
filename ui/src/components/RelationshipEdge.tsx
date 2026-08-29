import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  useInternalNode,
  type EdgeProps,
} from '@xyflow/react'
import { useId, useState, type FormEvent } from 'react'

import { RelationshipDetail } from '@/components/RelationshipDetail'
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from '@/components/ui/hover-card'
import type { RelationshipEdge as RelationshipEdgeType } from '@/types'
import { getEdgeParams } from '@/utils'

export function RelationshipEdge({
  id,
  source,
  target,
  markerStart,
  markerEnd,
  style,
  interactionWidth,
  label,
  data,
}: EdgeProps<RelationshipEdgeType>) {
  const [previewOpen, setPreviewOpen] = useState(false)
  const previewId = useId()
  const sourceNode = useInternalNode(source)
  const targetNode = useInternalNode(target)

  if (!sourceNode || !targetNode) return null

  const {
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  } = getEdgeParams(sourceNode, targetNode)
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })
  const relationship = data?.relationship
  const visibleLabel = String(label ?? relationship?.label ?? 'Related')

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerStart={markerStart}
        markerEnd={markerEnd}
        style={style}
        interactionWidth={interactionWidth}
      />
      {relationship ? (
        <EdgeLabelRenderer>
          <div
            className="relationship-edge__label-wrapper nodrag nopan"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            }}
          >
            <HoverCard
              open={previewOpen}
              onOpenChange={setPreviewOpen}
              openDelay={220}
            >
              <HoverCardTrigger asChild>
                <button
                  aria-label={`Relationship: ${visibleLabel}`}
                  aria-describedby={previewOpen ? previewId : undefined}
                  className="relationship-edge__label"
                  type="button"
                  onBlur={() => setPreviewOpen(false)}
                  onClick={(event) => event.stopPropagation()}
                  onFocus={() => setPreviewOpen(true)}
                  onPointerDown={(event) => event.stopPropagation()}
                >
                  {visibleLabel}
                </button>
              </HoverCardTrigger>
              <HoverCardContent
                id={previewId}
                align="center"
                className="relationship-edge__preview nodrag nopan"
                sideOffset={10}
              >
                <RelationshipDetail relationship={relationship} />
              </HoverCardContent>
            </HoverCard>
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  )
}

// Kept for the later editing pass. Relationship controls are intentionally not
// mounted in the read-only canvas experience.
export function RelationshipControls({
  label,
  explanation,
  onSubmit,
  onDelete,
}: {
  label: string
  explanation: string
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  onDelete: () => void
}) {
  return (
    <form aria-label={`Edit relationship: ${label}`} onSubmit={onSubmit}>
      <label>Label<input name="label" defaultValue={label} required /></label>
      <label>
        Explanation
        <textarea name="explanation" defaultValue={explanation} required />
      </label>
      <button type="submit">Save relationship</button>
      <button type="button" onClick={onDelete}>Delete relationship</button>
    </form>
  )
}
