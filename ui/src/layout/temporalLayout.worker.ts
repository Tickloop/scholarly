/// <reference lib="webworker" />

import { computeTemporalLayout } from '@/layout/temporalLayout'
import type { LayoutRequest } from '@/types'

type WorkerRequest = {
  requestId: number
  input: LayoutRequest
}

self.onmessage = (event: MessageEvent<WorkerRequest>) => {
  self.postMessage({
    requestId: event.data.requestId,
    result: computeTemporalLayout(event.data.input),
  })
}
