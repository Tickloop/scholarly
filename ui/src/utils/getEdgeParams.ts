import { Position, type InternalNode, type XYPosition } from '@xyflow/react'

function getNodeIntersection(
  intersectionNode: InternalNode,
  targetNode: InternalNode,
): XYPosition {
  const sourceWidth = intersectionNode.measured.width ?? 0
  const sourceHeight = intersectionNode.measured.height ?? 0
  const targetWidth = targetNode.measured.width ?? 0
  const targetHeight = targetNode.measured.height ?? 0

  const sourcePosition = intersectionNode.internals.positionAbsolute
  const targetPosition = targetNode.internals.positionAbsolute
  
  // if node's width and height have not been measured, we fall-back to 
  // mid-point of node
  if (!sourceWidth || !sourceHeight) {
    return { x: sourcePosition.x, y: sourcePosition.y}
  }


  const halfWidth = sourceWidth / 2
  const halfHeight = sourceHeight / 2
  const centerX = sourcePosition.x + halfWidth
  const centerY = sourcePosition.y + halfHeight


  const targetCenterX = targetPosition.x + targetWidth / 2
  const targetCenterY = targetPosition.y + targetHeight / 2
  const directionX = targetCenterX - centerX
  const directionY = targetCenterY - centerY
  const largestRatio = Math.max(
    Math.abs(directionX) / halfWidth,
    Math.abs(directionY) / halfHeight,
  )

  if (!largestRatio) {
    return { x: centerX, y: centerY }
  }

  const scale = 1 / largestRatio

  return {
    x: centerX + directionX * scale,
    y: centerY + directionY * scale,
  }
}

function getEdgePosition(node: InternalNode, point: XYPosition): Position {
  const position = node.internals.positionAbsolute
  const width = node.measured.width ?? 0

  if (Math.round(point.x) <= Math.round(position.x) + 1) return Position.Left
  if (Math.round(point.x) >= Math.round(position.x + width) - 1) return Position.Right
  if (Math.round(point.y) <= Math.round(position.y) + 1) return Position.Top

  return Position.Bottom
}

export function getEdgeParams(source: InternalNode, target: InternalNode) {
  const sourcePoint = getNodeIntersection(source, target)
  const targetPoint = getNodeIntersection(target, source)

  return {
    sourceX: sourcePoint.x,
    sourceY: sourcePoint.y,
    targetX: targetPoint.x,
    targetY: targetPoint.y,
    sourcePosition: getEdgePosition(source, sourcePoint),
    targetPosition: getEdgePosition(target, targetPoint),
  }
}
