import { describe, expect, it } from 'vitest'

import { computeTemporalLayout } from '@/layout/temporalLayout'
import type { LayoutNode, LayoutRelationship } from '@/types'

describe('temporal layout performance', () => {
  it.each([50, 100])('lays out %i nodes within the focused budget', (count) => {
    const nodes: LayoutNode[] = Array.from({ length: count }, (_, index) => ({
      id: `paper-${String(index).padStart(3, '0')}`,
      year: 2000 + (index % 20),
      month: index % 12 + 1,
      x: 0,
      y: 0,
      width: 320 + index % 3 * 20,
      height: 180 + index % 5 * 24,
      pinned: index % 17 === 0,
    }))
    const relationships: LayoutRelationship[] = nodes.slice(1).map((node, index) => ({
      source: nodes[index].id,
      target: node.id,
    }))

    const started = performance.now()
    const first = computeTemporalLayout({ nodes, relationships })
    const elapsed = performance.now() - started

    expect(first.positions).toHaveLength(count)
    expect(elapsed).toBeLessThan(250)
    expect(computeTemporalLayout({ nodes, relationships })).toEqual(first)
  })
})
