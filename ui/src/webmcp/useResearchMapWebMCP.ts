import { useEffect, useRef } from 'react'

import {
  createCanvas,
  getCanvas,
  resetCanvasLayout,
  startCanvasBuild,
  startDirectAgent,
  submitPaperLink,
  updatePaper,
  updateRelationship,
} from '@/api/client'
import type { AgentRun, CanvasSnapshot } from '@/types'
import {
  modelContextIfSupported,
  registerResearchMapTools,
  type ModelContextLike,
  type ResearchMapActions,
} from '@/webmcp/researchMapTools'

type WebMCPCallbacks = {
  activateCanvas: (snapshot: CanvasSnapshot) => void
  applySnapshot: (snapshot: CanvasSnapshot) => void
  watchRun: (run: AgentRun, label: string) => void
}

declare global {
  interface Document {
    modelContext?: ModelContextLike
  }
}

export function useResearchMapWebMCP(
  canvasId: string | undefined,
  callbacks: WebMCPCallbacks,
) {
  const callbacksRef = useRef(callbacks)
  useEffect(() => {
    callbacksRef.current = callbacks
  }, [callbacks])

  useEffect(() => {
    const modelContext = modelContextIfSupported(document)
    if (!modelContext) return

    const withResult = <T,>(
      operation: Promise<T>,
      callback: (value: T) => void,
    ) => operation.then((value) => {
      callback(value)
      return value
    })
    const actions: ResearchMapActions = {
      getCanvas: (id, signal) =>
        withResult(getCanvas(id, signal), callbacksRef.current.applySnapshot),
      createCanvas: (input, signal) =>
        withResult(createCanvas(input, signal), callbacksRef.current.activateCanvas),
      startBuild: (id, signal) =>
        withResult(startCanvasBuild(id, signal), (run) =>
          callbacksRef.current.watchRun(run, 'Canvas build run'),
        ),
      addPaperUrl: (id, input, signal) =>
        withResult(submitPaperLink(id, input, signal), (run) =>
          callbacksRef.current.watchRun(run, 'Paper link run'),
        ),
      runAgent: (id, input, signal) =>
        withResult(startDirectAgent(id, input, signal), (run) =>
          callbacksRef.current.watchRun(run, `Direct ${input.agent} run`),
        ),
      updatePaper: (id, paperId, input, signal) =>
        withResult(
          updatePaper(id, paperId, input, signal),
          callbacksRef.current.applySnapshot,
        ),
      updateRelationship: (id, relationshipId, input, signal) =>
        withResult(
          updateRelationship(id, relationshipId, input, signal),
          callbacksRef.current.applySnapshot,
        ),
      relayout: (id, signal) =>
        withResult(resetCanvasLayout(id, signal), callbacksRef.current.applySnapshot),
    }
    const registration = registerResearchMapTools(modelContext, canvasId, actions)
    return () => registration.abort()
  }, [canvasId])
}
