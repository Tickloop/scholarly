import type { Paper, PaperCanvasNode } from '@/types'
import { paperToNode } from '@/utils/paperToNode'
import { DEFAULT_NODE_POSITION } from '@/constants'

export function papersToNodes(papers: Paper[]): PaperCanvasNode[] {
  return papers.map((paper) =>
    paperToNode(paper, {
      x: paper.x ?? DEFAULT_NODE_POSITION.x,
      y: paper.y ?? DEFAULT_NODE_POSITION.y,
    }),
  )
}
