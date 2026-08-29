import type { Paper, PaperCanvasNode } from '@/types'
import { paperToNode } from '@/utils/paperToNode'

export function papersToNodes(papers: Paper[]): PaperCanvasNode[] {
  return papers.map((paper) =>
    paperToNode(paper, { x: paper.x ?? 0, y: paper.y ?? 0 }),
  )
}
