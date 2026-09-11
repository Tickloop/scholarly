import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  useInternalNode,
  type EdgeProps,
} from '@xyflow/react'

import type { PaperEdge as PaperEdgeType } from '@/types'
import { getEdgeParams } from '@/utils'

export function PaperEdge({
  id,
  source,
  target,
  markerStart,
  markerEnd,
  style,
  interactionWidth,
  data,
}: EdgeProps<PaperEdgeType>) {
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
  const visibleLabel = data?.relationship.label ?? 'Related'

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
