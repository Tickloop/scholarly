import type { LayoutRequest, LayoutResult } from '@/types'

type WorkerResponse = {
  requestId: number
  result: LayoutResult
}

type PendingRequest = {
  resolve: (result: LayoutResult) => void
  reject: (error: Error) => void
}

let worker: Worker | undefined
let requestId = 0
const pending = new Map<number, PendingRequest>()

export function layoutInWorker(input: LayoutRequest) {
  const activeWorker = getWorker()
  const nextRequestId = ++requestId

  return new Promise<LayoutResult>((resolve, reject) => {
    pending.set(nextRequestId, { resolve, reject })
    activeWorker.postMessage({ requestId: nextRequestId, input })
  })
}

function getWorker() {
  if (worker) return worker

  worker = new Worker(new URL('./temporalLayout.worker.ts', import.meta.url), {
    type: 'module',
  })
  worker.onmessage = (event: MessageEvent<WorkerResponse>) => {
    const request = pending.get(event.data.requestId)
    if (!request) return
    pending.delete(event.data.requestId)
    request.resolve(event.data.result)
  }
  worker.onerror = () => {
    const error = new Error('Temporal layout worker failed.')
    pending.forEach((request) => request.reject(error))
    pending.clear()
    worker?.terminate()
    worker = undefined
  }
  return worker
}
