import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  useInternalNode,
  type EdgeProps,
} from '@xyflow/react'

import type { RelationshipEdge as RelationshipEdgeType } from '@/types'
import { getEdgeParams } from '@/utils'
import { DEFAULT_RELATIONSHIP_LABEL } from '@constants'

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
  const visibleLabel = String(
    label ?? data?.relationship.label ?? DEFAULT_RELATIONSHIP_LABEL,
  )

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
      <EdgeLabelRenderer>
        <div
          aria-label={`Relationship: ${visibleLabel}`}
          className="nodrag nopan"
          style={{
            position: 'absolute',
            pointerEvents: 'none',
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
          }}
        >
          {visibleLabel}
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
