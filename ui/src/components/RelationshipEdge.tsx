import {
  BaseEdge,
  EdgeText,
  EdgeToolbar,
  getBezierPath,
  useInternalNode,
  type EdgeProps,
} from '@xyflow/react'
import type { FormEvent } from 'react'

import { RelationshipDetail } from '@/components/RelationshipDetail'
import { useCanvasActions } from '@/context/CanvasContext'
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
  labelStyle,
  labelShowBg,
  labelBgStyle,
  labelBgPadding,
  labelBgBorderRadius,
  data,
  selected,
}: EdgeProps<RelationshipEdgeType>) {
  const actions = useCanvasActions()
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
      <EdgeText
        x={labelX}
        y={labelY}
        label={label}
        aria-label={`Relationship: ${String(label)}`}
        role="button"
        tabIndex={0}
        labelStyle={labelStyle}
        labelShowBg={labelShowBg}
        labelBgStyle={labelBgStyle}
        labelBgPadding={labelBgPadding}
        labelBgBorderRadius={labelBgBorderRadius}
        onClick={() => data?.onActivate?.()}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            data?.onActivate?.()
          }
        }}
      />
      {data?.relationship ? (
        <EdgeToolbar
          className="nodrag nopan"
          edgeId={id}
          isVisible={selected}
          x={labelX}
          y={labelY}
          alignY="bottom"
        >
          <RelationshipDetail relationship={data.relationship} />
          {actions ? (
            <RelationshipControls
              label={data.relationship.label}
              explanation={data.relationship.explanation}
              onSubmit={(event) => {
                event.preventDefault()
                const form = new FormData(event.currentTarget)
                actions.editRelationship?.(id, {
                  label: String(form.get('label')),
                  explanation: String(form.get('explanation')),
                })
              }}
              onDelete={() => actions.removeRelationship?.(id)}
            />
          ) : null}
        </EdgeToolbar>
      ) : null}
    </>
  )
}

function RelationshipControls({
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
