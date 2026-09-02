import type { XYPosition } from '@xyflow/react'

import type { Paper, PaperCanvasNode } from '@/types'
import { DEFAULT_NODE_POSITION, PAPER_NODE_TYPE } from '@constants'

export function paperToNode(
  paper: Paper,
  position: XYPosition = DEFAULT_NODE_POSITION,
): PaperCanvasNode {
  return {
    id: paper.id,
    type: PAPER_NODE_TYPE,
    ariaLabel: `Paper: ${paper.title}`,
    position,
    data: { paper },
  }
}
