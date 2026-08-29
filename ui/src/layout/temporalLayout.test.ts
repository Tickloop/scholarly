import { describe, expect, it } from 'vitest'

import { computeTemporalLayout } from '@/layout/temporalLayout'
import type { LayoutNode, LayoutRequest } from '@/types'

const WIDTH = 320
const HEIGHT = 100

describe('computeTemporalLayout', () => {
  it('orders year bands oldest to newest and puts unknown dates last', () => {
    const result = layout([
      node('new', 2024),
      node('unknown', null),
      node('old', 2019),
    ])

    expect(x(result, 'old')).toBeLessThan(x(result, 'new'))
    expect(x(result, 'new')).toBeLessThan(x(result, 'unknown'))
    expect(x(result, 'new') - x(result, 'old')).toBeGreaterThanOrEqual(
      WIDTH + 160,
    )
  })

  it('keeps measured bounds at least 64 pixels apart', () => {
    const result = layout([
      { ...node('a', 2020), height: 220 },
      { ...node('b', 2020), height: 80 },
      { ...node('c', 2020), height: 140 },
    ])

    expect(y(result, 'b') - (y(result, 'a') + 220)).toBeGreaterThanOrEqual(64)
    expect(y(result, 'c') - (y(result, 'b') + 80)).toBeGreaterThanOrEqual(64)
  })

  it('is deterministic for the same graph and pins', () => {
    const input = request(
      [node('b', 2020), node('a', 2019), node('c', 2021, true, 900, 77)],
      [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }],
    )

    expect(computeTemporalLayout(input)).toEqual(computeTemporalLayout(input))
  })

  it('preserves pinned nodes and treats them as fixed obstacles', () => {
    const result = layout([
      node('fixed', 2020, true, 0, 0),
      node('free', 2020),
    ])

    expect(position(result, 'fixed')).toEqual({
      paper_id: 'fixed',
      x: 0,
      y: 0,
      pinned: true,
    })
    expect(y(result, 'free')).toBeGreaterThanOrEqual(HEIGHT + 64)
  })

  it('uses connected-neighbor medians in forward and backward ordering passes', () => {
    const result = computeTemporalLayout(request(
      [node('old-a', 2020), node('old-b', 2020), node('new-c', 2021), node('new-d', 2021)],
      [{ source: 'old-a', target: 'new-d' }, { source: 'old-b', target: 'new-c' }],
    ))

    expect(y(result, 'old-a')).toBe(y(result, 'new-d'))
    expect(y(result, 'old-b')).toBe(y(result, 'new-c'))
  })

  it.each([
    ['older', node('inserted', 2018)],
    ['newer', node('inserted', 2026)],
    ['same year', node('inserted', 2020)],
    ['unknown date', node('inserted', null)],
  ])('places an inserted %s paper without moving a pin', (_, inserted) => {
    const result = layout([
      node('fixed', 2020, true, 41, 73),
      node('existing', 2022),
      inserted,
    ])

    expect(position(result, 'fixed')).toMatchObject({ x: 41, y: 73, pinned: true })
    if (inserted.year === 2018) expect(x(result, 'inserted')).toBeLessThan(x(result, 'existing'))
    if (inserted.year === 2026) expect(x(result, 'inserted')).toBeGreaterThan(x(result, 'existing'))
    if (inserted.year === 2020) expect(x(result, 'inserted')).toBe(0)
    if (inserted.year === null) expect(x(result, 'inserted')).toBeGreaterThan(x(result, 'existing'))
  })
})

function node(
  id: string,
  year: number | null,
  pinned = false,
  x = 0,
  y = 0,
): LayoutNode {
  return {
    id,
    year,
    month: year === null ? null : 1,
    x,
    y,
    width: WIDTH,
    height: HEIGHT,
    pinned,
  }
}

function request(
  nodes: LayoutNode[],
  relationships: LayoutRequest['relationships'] = [],
): LayoutRequest {
  return { nodes, relationships }
}

function layout(nodes: LayoutNode[]) {
  return computeTemporalLayout(request(nodes))
}

function position(result: ReturnType<typeof layout>, id: string) {
  return result.positions.find((item) => item.paper_id === id)!
}

function x(result: ReturnType<typeof layout>, id: string) {
  return position(result, id).x
}

function y(result: ReturnType<typeof layout>, id: string) {
  return position(result, id).y
}
