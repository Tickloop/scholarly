import { constants } from '@/constants'
import type {
  AgentRun,
  AgentRunDetail,
  CanvasId,
  CanvasSnapshot,
  CanvasSummary,
  CanvasUpdateInput,
  ChatMessage,
  ChatMessageInput,
  ChatSubmission,
  CreateCanvasInput,
  DirectAgentInput,
  LayoutUpdateInput,
  PaperLinkInput,
  PaperUpdateInput,
  RelationshipUpdateInput,
  ReviewUpdateInput,
  RunEvent,
  RunEventHandlers,
  UndoResult,
} from '@/types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${constants.API_BASE_URL}/api/v1${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(message || `Request failed with status ${response.status}`)
  }

  if (response.status === 204) return undefined as T

  return response.json() as Promise<T>
}

export function listCanvases() {
  return request<CanvasSummary[]>('/canvases')
}

export function createCanvas(input: CreateCanvasInput, signal?: AbortSignal) {
  return request<CanvasSnapshot>('/canvases', {
    method: 'POST',
    body: JSON.stringify(input),
    signal,
  })
}

export function getCanvas(canvasId: CanvasId, signal?: AbortSignal) {
  return request<CanvasSnapshot>(`/canvases/${canvasId}`, { signal })
}

export function renameCanvas(canvasId: CanvasId, input: CanvasUpdateInput) {
  return request<CanvasSummary>(`/canvases/${canvasId}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

export function deleteCanvas(canvasId: CanvasId) {
  return request<void>(`/canvases/${canvasId}`, { method: 'DELETE' })
}

export function listMessages(canvasId: CanvasId) {
  return request<ChatMessage[]>(`/canvases/${canvasId}/messages`)
}

export function sendMessage(canvasId: CanvasId, input: ChatMessageInput) {
  return request<ChatSubmission>(`/canvases/${canvasId}/messages`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export function startCanvasBuild(canvasId: CanvasId, signal?: AbortSignal) {
  return request<AgentRun>(`/canvases/${canvasId}/builds`, {
    method: 'POST',
    signal,
  })
}

export function submitPaperLink(
  canvasId: CanvasId,
  input: PaperLinkInput,
  signal?: AbortSignal,
) {
  return request<AgentRun>(`/canvases/${canvasId}/papers/from-link`, {
    method: 'POST',
    body: JSON.stringify(input),
    signal,
  })
}

export function startDirectAgent(
  canvasId: CanvasId,
  input: DirectAgentInput,
  signal?: AbortSignal,
) {
  return request<AgentRun>(`/canvases/${canvasId}/agents/runs`, {
    method: 'POST',
    body: JSON.stringify(input),
    signal,
  })
}

export function listRuns(canvasId: CanvasId, signal?: AbortSignal) {
  return request<AgentRunDetail[]>(
    `/runs?canvas_id=${encodeURIComponent(canvasId)}`,
    { signal },
  )
}

export function listRunEvents(runId: string, signal?: AbortSignal) {
  return request<RunEvent[]>(`/runs/${runId}/event-log`, { signal })
}

export function cancelRun(runId: string) {
  return request<AgentRun>(`/runs/${runId}/cancel`, { method: 'POST' })
}

export function retryRun(runId: string) {
  return request<AgentRun>(`/runs/${runId}/retry`, { method: 'POST' })
}

export function updatePaper(
  canvasId: CanvasId,
  paperId: string,
  input: PaperUpdateInput,
  signal?: AbortSignal,
) {
  return request<CanvasSnapshot>(`/canvases/${canvasId}/papers/${paperId}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
    signal,
  })
}

export function retryPaper(canvasId: CanvasId, paperId: string) {
  return request<AgentRun>(`/canvases/${canvasId}/papers/${paperId}/retry`, {
    method: 'POST',
  })
}

export function deletePaper(canvasId: CanvasId, paperId: string) {
  return request<UndoResult>(`/canvases/${canvasId}/papers/${paperId}`, {
    method: 'DELETE',
  })
}

export function updateReview(
  canvasId: CanvasId,
  paperId: string,
  input: ReviewUpdateInput,
) {
  return request<CanvasSnapshot>(
    `/canvases/${canvasId}/papers/${paperId}/review`,
    { method: 'PATCH', body: JSON.stringify(input) },
  )
}

export function regenerateReview(canvasId: CanvasId, paperId: string) {
  return request<AgentRun>(
    `/canvases/${canvasId}/papers/${paperId}/review/regenerate`,
    { method: 'POST' },
  )
}

export function updateRelationship(
  canvasId: CanvasId,
  relationshipId: string,
  input: RelationshipUpdateInput,
  signal?: AbortSignal,
) {
  return request<CanvasSnapshot>(
    `/canvases/${canvasId}/relationships/${relationshipId}`,
    { method: 'PATCH', body: JSON.stringify(input), signal },
  )
}

export function deleteRelationship(
  canvasId: CanvasId,
  relationshipId: string,
) {
  return request<UndoResult>(
    `/canvases/${canvasId}/relationships/${relationshipId}`,
    { method: 'DELETE' },
  )
}

export function undoCanvasChange(canvasId: CanvasId, undoToken: string) {
  return request<CanvasSnapshot>(`/canvases/${canvasId}/undo`, {
    method: 'POST',
    body: JSON.stringify({ undo_token: undoToken }),
  })
}

export function updateCanvasLayout(canvasId: CanvasId, input: LayoutUpdateInput) {
  return request<CanvasSnapshot>(`/canvases/${canvasId}/layout`, {
    method: 'PUT',
    body: JSON.stringify(input),
  })
}

export function resetCanvasLayout(canvasId: CanvasId, signal?: AbortSignal) {
  return request<CanvasSnapshot>(`/canvases/${canvasId}/layout/reset`, {
    method: 'POST',
    signal,
  })
}

export function subscribeToRunEvents(
  runId: string,
  { onEvent, onError }: RunEventHandlers,
) {
  const source = new EventSource(
    `${constants.API_BASE_URL}/api/v1/runs/${runId}/events`,
  )

  const receiveEvent = (message: MessageEvent<string>) => {
    onEvent(JSON.parse(message.data) as RunEvent)
  }

  source.onmessage = receiveEvent
  constants.RUN_EVENT_TYPES.forEach((eventType) => {
    source.addEventListener(eventType, receiveEvent as EventListener)
  })
  if (onError) source.onerror = onError

  return () => source.close()
}
