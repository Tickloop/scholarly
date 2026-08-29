import { constants } from '@/constants'
import type {
  LayoutNode,
  LayoutPosition,
  LayoutRequest,
  LayoutResult,
} from '@/types'

const DEFAULT_NODE_HEIGHT = 320
const VERTICAL_GAP = 64
const UNKNOWN_YEAR = Number.POSITIVE_INFINITY

type Band = {
  key: number
  nodes: LayoutNode[]
  x: number
}

type Bounds = {
  x: number
  y: number
  width: number
  height: number
}

export function computeTemporalLayout({
  nodes,
  relationships,
}: LayoutRequest): LayoutResult {
  const normalized = [...nodes]
    .map(normalizeNode)
    .sort(compareNodes)
  const nodeById = new Map(normalized.map((node) => [node.id, node]))
  const neighbors = createNeighborMap(normalized, relationships)
  const bands = createBands(normalized)

  runOrderingPass(bands, neighbors, nodeById, 'forward')
  runOrderingPass(bands, neighbors, nodeById, 'backward')

  const positions = new Map<string, LayoutPosition>()
  const occupied: Bounds[] = []
  for (const node of normalized.filter((item) => item.pinned)) {
    positions.set(node.id, toPosition(node.id, node.x, node.y, true))
    occupied.push(toBounds(node, node.x, node.y))
  }

  for (const band of bands) {
    band.nodes.forEach((node, index) => {
      if (node.pinned) return

      const neighborCenters = [...(neighbors.get(node.id) ?? [])]
        .map((id) => {
          const position = positions.get(id)
          const neighbor = nodeById.get(id)
          if (!position || !neighbor) return undefined
          return position.y + neighbor.height / 2 - node.height / 2
        })
        .filter((value): value is number => value !== undefined)
      const idealY = neighborCenters.length > 0
        ? median(neighborCenters)
        : index * (node.height + VERTICAL_GAP)
      const y = findFreeY(
        band.x,
        node.width,
        node.height,
        idealY,
        occupied,
      )
      positions.set(node.id, toPosition(node.id, band.x, y, false))
      occupied.push(toBounds(node, band.x, y))
    })
  }

  return {
    positions: normalized.map((node) => positions.get(node.id)!),
  }
}

function normalizeNode(node: LayoutNode): LayoutNode {
  return {
    ...node,
    width: positiveSize(node.width, constants.PAPER_NODE_DEFAULT_WIDTH),
    height: positiveSize(node.height, DEFAULT_NODE_HEIGHT),
    x: finiteCoordinate(node.x),
    y: finiteCoordinate(node.y),
  }
}

function createBands(nodes: LayoutNode[]) {
  const grouped = new Map<number, LayoutNode[]>()
  for (const node of nodes) {
    const year = publicationYear(node)
    grouped.set(year, [...(grouped.get(year) ?? []), node])
  }

  let x = 0
  return [...grouped.entries()]
    .sort(([left], [right]) => left - right)
    .map(([key, bandNodes]): Band => {
      const band = { key, nodes: bandNodes, x }
      x += Math.max(...bandNodes.map((node) => node.width)) +
        constants.PAPER_NODE_HORIZONTAL_GAP
      return band
    })
}

function createNeighborMap(
  nodes: LayoutNode[],
  relationships: LayoutRequest['relationships'],
) {
  const neighbors = new Map(nodes.map((node) => [node.id, new Set<string>()]))
  for (const relationship of relationships) {
    if (!neighbors.has(relationship.source) || !neighbors.has(relationship.target)) {
      continue
    }
    neighbors.get(relationship.source)!.add(relationship.target)
    neighbors.get(relationship.target)!.add(relationship.source)
  }
  return neighbors
}

function runOrderingPass(
  bands: Band[],
  neighbors: Map<string, Set<string>>,
  nodeById: Map<string, LayoutNode>,
  direction: 'forward' | 'backward',
) {
  const bandIndex = new Map<string, number>()
  bands.forEach((band, index) => {
    band.nodes.forEach((node) => bandIndex.set(node.id, index))
  })
  const rank = new Map<string, number>()
  const orderedBands = direction === 'forward' ? bands : [...bands].reverse()

  for (const band of orderedBands) {
    const currentBand = bandIndex.get(band.nodes[0]?.id ?? '') ?? 0
    const previousOrder = new Map(
      band.nodes.map((node, index) => [node.id, index]),
    )
    band.nodes.sort((left, right) => {
      const leftMedian = neighborRankMedian(
        left.id,
        currentBand,
        direction,
        neighbors,
        bandIndex,
        rank,
      )
      const rightMedian = neighborRankMedian(
        right.id,
        currentBand,
        direction,
        neighbors,
        bandIndex,
        rank,
      )
      if (leftMedian !== rightMedian) return leftMedian - rightMedian
      return (previousOrder.get(left.id)! - previousOrder.get(right.id)!) ||
        compareNodes(nodeById.get(left.id)!, nodeById.get(right.id)!)
    })
    band.nodes.forEach((node, index) => rank.set(node.id, index))
  }
}

function neighborRankMedian(
  id: string,
  currentBand: number,
  direction: 'forward' | 'backward',
  neighbors: Map<string, Set<string>>,
  bandIndex: Map<string, number>,
  rank: Map<string, number>,
) {
  const values = [...(neighbors.get(id) ?? [])]
    .filter((neighborId) => {
      const neighborBand = bandIndex.get(neighborId)
      return neighborBand !== undefined && (
        direction === 'forward'
          ? neighborBand < currentBand
          : neighborBand > currentBand
      )
    })
    .map((neighborId) => rank.get(neighborId))
    .filter((value): value is number => value !== undefined)
  return values.length > 0 ? median(values) : Number.POSITIVE_INFINITY
}

function findFreeY(
  x: number,
  width: number,
  height: number,
  idealY: number,
  occupied: Bounds[],
) {
  let y = Math.max(0, Math.round(idealY))
  const horizontalObstacles = occupied
    .filter((bounds) => rangesOverlap(x, x + width, bounds.x, bounds.x + bounds.width))
    .sort((left, right) => left.y - right.y || left.x - right.x)

  for (;;) {
    const collision = horizontalObstacles.find((bounds) =>
      rangesOverlap(
        y - VERTICAL_GAP,
        y + height + VERTICAL_GAP,
        bounds.y,
        bounds.y + bounds.height,
      ),
    )
    if (!collision) return y
    y = Math.round(collision.y + collision.height + VERTICAL_GAP)
  }
}

function publicationYear(node: LayoutNode) {
  return typeof node.year === 'number' && Number.isFinite(node.year) && node.year > 0
    ? node.year
    : UNKNOWN_YEAR
}

function compareNodes(left: LayoutNode, right: LayoutNode) {
  return publicationYear(left) - publicationYear(right) ||
    (left.month ?? 13) - (right.month ?? 13) ||
    left.id.localeCompare(right.id)
}

function median(values: number[]) {
  const sorted = [...values].sort((left, right) => left - right)
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 0
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[middle]
}

function rangesOverlap(
  firstStart: number,
  firstEnd: number,
  secondStart: number,
  secondEnd: number,
) {
  return firstStart < secondEnd && secondStart < firstEnd
}

function positiveSize(value: number, fallback: number) {
  return Number.isFinite(value) && value > 0 ? value : fallback
}

function finiteCoordinate(value: number) {
  return Number.isFinite(value) ? value : 0
}

function toBounds(node: LayoutNode, x: number, y: number): Bounds {
  return { x, y, width: node.width, height: node.height }
}

function toPosition(
  paper_id: string,
  x: number,
  y: number,
  pinned: boolean,
): LayoutPosition {
  return { paper_id, x, y, pinned }
}
