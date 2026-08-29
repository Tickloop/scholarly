import type { XYPosition } from '@xyflow/react'

import type { Paper, PaperCanvasNode } from '@/types'

export function paperToNode(
  paper: Paper,
  position: XYPosition = { x: 0, y: 0 },
): PaperCanvasNode {
  return {
    id: paper.id,
    type: 'paper',
    ariaLabel: `Paper: ${paper.title}`,
    position,
    data: { paper },
  }
}
