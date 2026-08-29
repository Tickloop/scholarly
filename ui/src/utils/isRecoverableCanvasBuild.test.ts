import { describe, expect, it } from 'vitest'

import type { CanvasSnapshot } from '@/types'
import { isRecoverableCanvasBuild } from '@/utils/isRecoverableCanvasBuild'

describe('recoverable canvas-build summaries', () => {
  it.each([
    ['causal legacy checkpoint', 4, 4, 1],
    ['protein legacy checkpoint', 2, 2, 0],
  ])('allows %s with persisted work', (_, accepted, reviewed, relationships) => {
    expect(isRecoverableCanvasBuild(snapshot(
      'academic_search_unavailable', accepted, reviewed, relationships,
    ))).toBe(true)
  })

  it.each([
    [0, 0],
    [4, 0],
    [0, 4],
  ])('rejects a legacy checkpoint without both positive counts', (accepted, reviewed) => {
    expect(isRecoverableCanvasBuild(snapshot(
      'academic_search_unavailable', accepted, reviewed, 0,
    ))).toBe(false)
  })

  it('preserves the current empty-build and allowlisted stop behavior', () => {
    expect(isRecoverableCanvasBuild(snapshot(
      'insufficient_relevant_candidates', 0, 0, 0,
    ))).toBe(true)
    expect(isRecoverableCanvasBuild(snapshot('coverage_sufficient', 0, 0, 0)))
      .toBe(true)
    expect(isRecoverableCanvasBuild(snapshot('coverage_sufficient', 4, 4, 1)))
      .toBe(false)
  })
})

function snapshot(
  stopReason: string,
  acceptedPapers: number,
  completedReviews: number,
  relationships: number,
): CanvasSnapshot {
  return {
    canvas: {
      id: 'canvas-legacy',
      name: 'Legacy canvas',
      research_goal: 'Recover persisted research.',
      research_brief: {
        pipeline_summary: {
          stop_reason: stopReason,
          accepted_papers: acceptedPapers,
          completed_reviews: completedReviews,
          relationships,
        },
      },
      build_status: 'completed',
      created_at: '2026-08-29T00:00:00Z',
      updated_at: '2026-08-29T00:00:00Z',
    },
    papers: [],
    relationships: [],
  }
}
