import type { CanvasSnapshot } from '@/types'

const RECOVERABLE_COMPLETED_BUILD_REASONS = new Set([
  'insufficient_unique_candidates',
  'insufficient_relevant_candidates',
  'academic_search_unavailable_after_checkpoint',
])

export function isRecoverableCanvasBuild(snapshot: CanvasSnapshot) {
  const buildStatus = snapshot.canvas.build_status
  if (buildStatus === 'failed' || buildStatus === 'completed_with_errors') {
    return true
  }
  if (buildStatus !== 'completed') return false

  const brief = snapshot.canvas.research_brief
  if (!brief || typeof brief !== 'object') return false
  const summary = brief.pipeline_summary
  if (!summary || typeof summary !== 'object' || Array.isArray(summary)) return false
  const values = summary as Record<string, unknown>
  const stopReason = values.stop_reason

  if (stopReason === 'academic_search_unavailable') {
    return positiveCount(values.accepted_papers) &&
      positiveCount(values.completed_reviews)
  }
  if (
    typeof stopReason === 'string' &&
    RECOVERABLE_COMPLETED_BUILD_REASONS.has(stopReason)
  ) {
    return true
  }
  return values.accepted_papers === 0 || values.completed_reviews === 0
}

function positiveCount(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}
