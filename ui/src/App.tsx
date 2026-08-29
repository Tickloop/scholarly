import { useCallback, useEffect, useRef, useState } from 'react'
import type { XYPosition } from '@xyflow/react'

import {
  cancelRun,
  createCanvas,
  deleteCanvas,
  deletePaper,
  deleteRelationship,
  getCanvas,
  listMessages,
  listRunEvents,
  listRuns,
  listCanvases,
  regenerateReview,
  renameCanvas,
  resetCanvasLayout,
  retryPaper,
  retryRun,
  sendMessage,
  startCanvasBuild,
  submitPaperLink,
  subscribeToCanvasEvents,
  subscribeToRunEvents,
  undoCanvasChange,
  updateCanvasLayout,
  updatePaper,
  updateRelationship,
  updateReview,
} from '@/api/client'
import { Canvas } from '@/components/Canvas'
import { CanvasDrawer } from '@/components/CanvasDrawer'
import { CanvasLauncher } from '@/components/CanvasLauncher'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from '@/components/ui/sidebar'
import { constants } from '@/constants'
import { CanvasProvider } from '@/context/CanvasContext'
import type {
  AgentRun,
  CanvasId,
  CanvasSnapshot,
  CanvasSummary,
  ChatMessage,
  CreateCanvasInput,
  LayoutPosition,
  RunEvent,
} from '@/types'
import { isRecoverableCanvasBuild } from '@/utils'
import { useResearchMapWebMCP } from '@/webmcp/useResearchMapWebMCP'

type RunPurpose = 'build' | 'link' | 'chat' | 'direct' | 'review'

type RunSubscription = {
  run: AgentRun
  label: string
  purpose: RunPurpose
  terminal: boolean
  close: () => void
}

function App() {
  const [canvases, setCanvases] = useState<CanvasSummary[]>([])
  const [snapshot, setSnapshot] = useState<CanvasSnapshot>()
  const [isBusy, setIsBusy] = useState(true)
  const [error, setError] = useState<string>()
  const [runStatus, setRunStatus] = useState<string>()
  const [runStatusLabel, setRunStatusLabel] = useState<string>()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [selectedPaperId, setSelectedPaperId] = useState<string>()
  const [selectedRelationshipId, setSelectedRelationshipId] = useState<string>()
  const [activeAgent, setActiveAgent] = useState<string>()
  const [activeRun, setActiveRun] = useState<AgentRun>()
  const [undoToken, setUndoToken] = useState<string>()
  const [sidebarOpen, setSidebarOpen] = useState(
    () => localStorage.getItem(constants.SIDEBAR_OPEN_STORAGE_KEY) !== 'false',
  )
  const runSubscriptions = useRef(new Map<string, RunSubscription>())
  const activeRunKey = useRef<string | undefined>(undefined)
  const activeRunPurpose = useRef<RunPurpose | undefined>(undefined)
  const activeCanvasId = useRef<string | undefined>(undefined)
  const layoutMutationQueue = useRef<Promise<void>>(Promise.resolve())
  activeCanvasId.current = snapshot?.canvas.id

  const closeAllRunStreams = useCallback(() => {
    runSubscriptions.current.forEach((subscription) => subscription.close())
    runSubscriptions.current.clear()
    activeRunKey.current = undefined
    activeRunPurpose.current = undefined
  }, [])

  const loadCanvas = useCallback(async (canvasId: CanvasId) => {
    closeAllRunStreams()
    setRunStatus(undefined)
    setIsBusy(true)
    setError(undefined)
    setActiveAgent(undefined)
    setActiveRun(undefined)
    setUndoToken(undefined)
    try {
      const nextSnapshot = await getCanvas(canvasId)
      const persistedBuildRun = isRecoverableCanvasBuild(nextSnapshot)
        ? await findLatestCanvasBuildRun(canvasId)
        : undefined
      setSnapshot(nextSnapshot)
      setRunStatus(nextSnapshot.canvas.build_status)
      setRunStatusLabel('Canvas build')
      setActiveRun(persistedBuildRun)
      activeRunPurpose.current = persistedBuildRun ? 'build' : undefined
      setMessages(
        constants.CANVAS_CHAT_ENABLED ? await listMessages(canvasId) : [],
      )
      setSelectedPaperId(undefined)
      setSelectedRelationshipId(undefined)
      localStorage.setItem(constants.LAST_CANVAS_ID_STORAGE_KEY, canvasId)
    } catch (loadError) {
      setError(getErrorMessage(loadError))
    } finally {
      setIsBusy(false)
    }
  }, [closeAllRunStreams])

  useEffect(() => {
    let isCurrent = true

    async function loadInitialCanvas() {
      try {
        const nextCanvases = await listCanvases()
        if (!isCurrent) return

        setCanvases(nextCanvases)
        const storedCanvasId = localStorage.getItem(
          constants.LAST_CANVAS_ID_STORAGE_KEY,
        )
        const selectedCanvas =
          nextCanvases.find((canvas) => canvas.id === storedCanvasId) ??
          nextCanvases[0]

        if (selectedCanvas) await loadCanvas(selectedCanvas.id)
      } catch (loadError) {
        if (isCurrent) setError(getErrorMessage(loadError))
      } finally {
        if (isCurrent) setIsBusy(false)
      }
    }

    void loadInitialCanvas()
    return () => {
      isCurrent = false
      closeAllRunStreams()
    }
  }, [closeAllRunStreams, loadCanvas])

  const refreshWorkspace = useCallback(async (changedCanvasId?: string) => {
    try {
      const nextCanvases = await listCanvases()
      setCanvases(nextCanvases)

      const currentCanvasId = activeCanvasId.current
      if (currentCanvasId) {
        const currentStillExists = nextCanvases.some(
          (canvas) => canvas.id === currentCanvasId,
        )
        if (!currentStillExists) {
          if (nextCanvases[0]) await loadCanvas(nextCanvases[0].id)
          else {
            closeAllRunStreams()
            setSnapshot(undefined)
            setMessages([])
            localStorage.removeItem(constants.LAST_CANVAS_ID_STORAGE_KEY)
          }
          return
        }

        if (!changedCanvasId || changedCanvasId === currentCanvasId) {
          setSnapshot(await getCanvas(currentCanvasId))
        }
        return
      }

      if (nextCanvases[0]) await loadCanvas(nextCanvases[0].id)
      setError(undefined)
    } catch (refreshError) {
      setError(getErrorMessage(refreshError))
    }
  }, [closeAllRunStreams, loadCanvas])

  useEffect(() => {
    return subscribeToCanvasEvents({
      onEvent: (event) => void refreshWorkspace(event.canvas_id),
      onOpen: () => void refreshWorkspace(),
    })
  }, [refreshWorkspace])

  const refreshCanvasForEvent = useCallback(
    async (event: RunEvent, key: string, purpose: RunPurpose) => {
      const subscription = runSubscriptions.current.get(key)
      if (!subscription) return
      const isTerminal =
        event.type === 'run.completed' ||
        event.type === 'run.failed' ||
        event.type === 'run.cancelled'
      if (activeRunKey.current === key) {
        setRunStatus(event.type)
        setActiveRun((current) => current ? { ...current, status: event.type } : current)
        if (typeof event.payload.agent === 'string') {
          setActiveAgent(event.payload.agent)
        }
      }
      if (
        purpose === 'chat' &&
        event.type === 'agent.message.delta' &&
        typeof event.payload.content === 'string' &&
        activeCanvasId.current === event.canvas_id
      ) {
        const streamingId = `stream:${event.run_id}`
        setMessages((current) => {
          const existing = current.find((message) => message.id === streamingId)
          if (!existing) {
            return [
              ...current,
              {
                id: streamingId,
                canvas_id: event.canvas_id,
                role: 'assistant',
                agent_name:
                  typeof event.payload.agent === 'string'
                    ? event.payload.agent
                    : 'agent',
                content: event.payload.content as string,
                status: 'streaming',
                created_at: event.created_at,
              },
            ]
          }
          return current.map((message) =>
            message.id === streamingId
              ? {
                  ...message,
                  content: message.content + event.payload.content,
                }
              : message,
          )
        })
      }
      if (event.type === 'run.failed' && activeCanvasId.current === event.canvas_id) {
        setError(getRunFailureMessage(event))
      }
      if (isTerminal) {
        subscription.terminal = true
        subscription.close()
        runSubscriptions.current.delete(key)
      }
      if (
        event.type === 'paper.added' ||
        event.type === 'source.completed' ||
        event.type === 'tool.completed' ||
        event.type === 'review.completed' ||
        event.type === 'relationship.added' ||
        event.type === 'run.completed' ||
        event.type === 'run.failed' ||
        event.type === 'run.cancelled'
      ) {
        const nextSnapshot = await getCanvas(event.canvas_id)
        if (activeCanvasId.current === event.canvas_id) setSnapshot(nextSnapshot)
      }
      if (isTerminal && purpose === 'chat') {
        const nextMessages = await listMessages(event.canvas_id)
        if (activeCanvasId.current === event.canvas_id) setMessages(nextMessages)
      }
    },
    [],
  )

  function watchRun(
    run: AgentRun,
    label: string,
    purpose: RunPurpose = inferRunPurpose(label),
  ) {
    const key = runSubscriptionKey(run.id, purpose)
    runSubscriptions.current.get(key)?.close()
    setRunStatus(run.status)
    setRunStatusLabel(label)
    setActiveRun(run)
    activeRunKey.current = key
    activeRunPurpose.current = purpose
    const subscription: RunSubscription = {
      run,
      label,
      purpose,
      terminal: false,
      close: () => undefined,
    }
    runSubscriptions.current.set(key, subscription)
    subscription.close = subscribeToRunEvents(run.id, {
      onEvent: (event) => void refreshCanvasForEvent(event, key, purpose),
      onError: () => {
        const current = runSubscriptions.current.get(key)
        if (current && !current.terminal) {
          current.close()
          runSubscriptions.current.delete(key)
          setError('The run event stream was interrupted.')
        }
      },
    })
  }

  async function handleCreate(input: CreateCanvasInput) {
    setIsBusy(true)
    setError(undefined)
    setRunStatus(undefined)
    setRunStatusLabel(undefined)
    setActiveAgent(undefined)
    setActiveRun(undefined)
    closeAllRunStreams()

    try {
      const nextSnapshot = await createCanvas(input)
      setSnapshot(nextSnapshot)
      setRunStatus(nextSnapshot.canvas.build_status)
      setRunStatusLabel('Canvas build')
      setCanvases((current) => [...current, nextSnapshot.canvas])
      setMessages([])
      localStorage.setItem(
        constants.LAST_CANVAS_ID_STORAGE_KEY,
        nextSnapshot.canvas.id,
      )

      const run = await startCanvasBuild(nextSnapshot.canvas.id)
      watchRun(run, 'Canvas build run', 'build')
    } catch (createError) {
      setError(getErrorMessage(createError))
      throw createError
    } finally {
      setIsBusy(false)
    }
  }

  async function handlePaperLink(url: string, position?: XYPosition) {
    if (!snapshot) return

    const canvasId = snapshot.canvas.id
    setIsBusy(true)
    setError(undefined)

    try {
      const run = await submitPaperLink(canvasId, {
        url,
        x: position?.x,
        y: position?.y,
      })
      setRunStatus(run.status)
      setRunStatusLabel('Paper link run')
      setSnapshot(await getCanvas(canvasId))
      if (run.paper_id) {
        setSelectedRelationshipId(undefined)
        setSelectedPaperId(run.paper_id)
      }
      watchRun(run, 'Paper link run', 'link')
    } catch (linkError) {
      setError(getErrorMessage(linkError))
      throw linkError
    } finally {
      setIsBusy(false)
    }
  }

  async function handleSendMessage(content: string, paperIds: string[]) {
    if (!snapshot) return
    setError(undefined)
    try {
      const submission = await sendMessage(snapshot.canvas.id, {
        content,
        paper_ids: paperIds,
      })
      setMessages((current) => [...current, submission.message])
      setActiveAgent(submission.message.agent_name)
      if (submission.run) {
        watchRun(
          submission.run,
          submission.queued ? 'Queued chat' : 'Chat run',
          'chat',
        )
      } else if (submission.queued) {
        setRunStatus('queued')
        setRunStatusLabel('Queued chat')
      }
    } catch (messageError) {
      setError(getErrorMessage(messageError))
      throw messageError
    }
  }

  async function handleRenameCanvas(canvasId: CanvasId, name: string) {
    try {
      const canvas = await renameCanvas(canvasId, { name })
      setCanvases((current) =>
        current.map((item) => item.id === canvas.id ? canvas : item),
      )
      setSnapshot((current) =>
        current?.canvas.id === canvas.id ? { ...current, canvas } : current,
      )
    } catch (renameError) {
      setError(getErrorMessage(renameError))
    }
  }

  async function handleDeleteCanvas(canvasId: CanvasId) {
    try {
      await deleteCanvas(canvasId)
      const remaining = canvases.filter((canvas) => canvas.id !== canvasId)
      setCanvases(remaining)
      if (snapshot?.canvas.id === canvasId) {
        if (remaining[0]) await loadCanvas(remaining[0].id)
        else handleSelectCanvas(undefined)
      }
    } catch (deleteError) {
      setError(getErrorMessage(deleteError))
    }
  }

  async function handleStop() {
    if (!activeRun) return
    try {
      const run = await cancelRun(activeRun.id)
      const key = activeRunKey.current
      const subscription = key ? runSubscriptions.current.get(key) : undefined
      if (subscription) {
        subscription.terminal = true
        subscription.close()
        runSubscriptions.current.delete(key!)
      }
      setActiveRun(run)
      setRunStatus(run.status)
      const nextSnapshot = await getCanvas(run.canvas_id)
      if (activeCanvasId.current === run.canvas_id) setSnapshot(nextSnapshot)
      if (subscription?.purpose === 'chat') {
        const nextMessages = await listMessages(run.canvas_id)
        if (activeCanvasId.current === run.canvas_id) setMessages(nextMessages)
      }
    } catch (stopError) {
      setError(getErrorMessage(stopError))
    }
  }

  async function handleRetryRun() {
    if (!activeRun) return
    try {
      watchRun(
        await retryRun(activeRun.id),
        runStatusLabel ?? 'Run',
        activeRunPurpose.current ?? inferRunPurpose(runStatusLabel ?? 'Run'),
      )
    } catch (retryError) {
      setError(getErrorMessage(retryError))
    }
  }

  async function applySnapshotChange(change: Promise<CanvasSnapshot>) {
    try {
      setSnapshot(await change)
      setError(undefined)
    } catch (changeError) {
      setError(getErrorMessage(changeError))
    }
  }

  async function handleDeletePaper(paperId: string) {
    if (!snapshot) return
    try {
      const result = await deletePaper(snapshot.canvas.id, paperId)
      setUndoToken(result.undo_token)
      setSelectedPaperId(undefined)
      setSnapshot(await getCanvas(snapshot.canvas.id))
    } catch (deleteError) {
      setError(getErrorMessage(deleteError))
    }
  }

  async function handleDeleteRelationship(relationshipId: string) {
    if (!snapshot) return
    try {
      const result = await deleteRelationship(snapshot.canvas.id, relationshipId)
      setUndoToken(result.undo_token)
      setSelectedRelationshipId(undefined)
      setSnapshot(await getCanvas(snapshot.canvas.id))
    } catch (deleteError) {
      setError(getErrorMessage(deleteError))
    }
  }

  async function handleUndo() {
    if (!snapshot || !undoToken) return
    await applySnapshotChange(undoCanvasChange(snapshot.canvas.id, undoToken))
    setUndoToken(undefined)
  }

  function enqueueLayoutMutation(
    canvasId: string,
    mutation: () => Promise<CanvasSnapshot>,
  ) {
    layoutMutationQueue.current = layoutMutationQueue.current.then(async () => {
      try {
        const nextSnapshot = await mutation()
        if (activeCanvasId.current === canvasId) setSnapshot(nextSnapshot)
      } catch (layoutError) {
        if (activeCanvasId.current === canvasId) {
          setError(getErrorMessage(layoutError))
        }
      }
    })
    return layoutMutationQueue.current
  }

  function handlePersistLayout(positions: LayoutPosition[]) {
    const canvasId = activeCanvasId.current
    if (!canvasId || positions.length === 0) return
    return enqueueLayoutMutation(
      canvasId,
      () => updateCanvasLayout(canvasId, { positions }),
    )
  }

  function handleRelayout() {
    const canvasId = activeCanvasId.current
    if (!canvasId) return
    return enqueueLayoutMutation(canvasId, () => resetCanvasLayout(canvasId))
  }

  function handlePaperMove(paperId: string, position: XYPosition) {
    const canvasId = activeCanvasId.current
    if (!canvasId) return
    setSnapshot((current) => current?.canvas.id === canvasId
      ? {
          ...current,
          papers: current.papers.map((paper) => paper.id === paperId
            ? { ...paper, ...position, pinned: true }
            : paper),
        }
      : current)
    void enqueueLayoutMutation(
      canvasId,
      () => updatePaper(canvasId, paperId, {
        x: position.x,
        y: position.y,
        pinned: true,
      }),
    )
  }

  function handleSelectCanvas(canvasId?: CanvasId) {
    if (canvasId) {
      void loadCanvas(canvasId)
      return
    }

    closeAllRunStreams()
    setSnapshot(undefined)
    setRunStatus(undefined)
    setRunStatusLabel(undefined)
    setError(undefined)
    setMessages([])
    setSelectedPaperId(undefined)
    setSelectedRelationshipId(undefined)
    setActiveAgent(undefined)
    setActiveRun(undefined)
    localStorage.removeItem(constants.LAST_CANVAS_ID_STORAGE_KEY)
  }

  useResearchMapWebMCP(snapshot?.canvas.id, {
    activateCanvas: (nextSnapshot) => {
      closeAllRunStreams()
      setSnapshot(nextSnapshot)
      setRunStatus(nextSnapshot.canvas.build_status)
      setRunStatusLabel('Canvas build')
      setActiveAgent(undefined)
      setActiveRun(undefined)
      setCanvases((current) => [
        nextSnapshot.canvas,
        ...current.filter((canvas) => canvas.id !== nextSnapshot.canvas.id),
      ])
      setMessages([])
      setSelectedPaperId(undefined)
      setSelectedRelationshipId(undefined)
      localStorage.setItem(
        constants.LAST_CANVAS_ID_STORAGE_KEY,
        nextSnapshot.canvas.id,
      )
    },
    applySnapshot: setSnapshot,
    watchRun,
  })

  return (
    <CanvasProvider
      key={snapshot?.canvas.id ?? 'empty-canvas'}
      papers={snapshot?.papers ?? []}
      relationships={snapshot?.relationships ?? []}
      editPaper={(paperId, input) => {
        if (snapshot) void applySnapshotChange(updatePaper(snapshot.canvas.id, paperId, input))
      }}
      retryPaperProcessing={(paperId) => {
        if (!snapshot) return
        const canvasId = snapshot.canvas.id
        void retryPaper(canvasId, paperId).then(async (run) => {
          setSnapshot(await getCanvas(canvasId))
          setSelectedRelationshipId(undefined)
          setSelectedPaperId(paperId)
          watchRun(run, 'Paper link run', 'link')
        }).catch((retryError) => setError(getErrorMessage(retryError)))
      }}
      removePaper={(paperId) => void handleDeletePaper(paperId)}
      editReview={(paperId, input) => {
        if (snapshot) void applySnapshotChange(updateReview(snapshot.canvas.id, paperId, input))
      }}
      regeneratePaperReview={(paperId) => {
        if (!snapshot) return
        void regenerateReview(snapshot.canvas.id, paperId).then((run) => watchRun(run, 'Review run'))
      }}
      editRelationship={(relationshipId, input) => {
        if (snapshot) void applySnapshotChange(updateRelationship(snapshot.canvas.id, relationshipId, input))
      }}
      removeRelationship={(relationshipId) => void handleDeleteRelationship(relationshipId)}
      persistLayout={handlePersistLayout}
    >
      <SidebarProvider
        open={sidebarOpen}
        onOpenChange={(open) => {
          setSidebarOpen(open)
          localStorage.setItem(constants.SIDEBAR_OPEN_STORAGE_KEY, String(open))
        }}
      >
        <CanvasDrawer
          key={snapshot?.canvas.id ?? 'new-canvas'}
          canvases={canvases}
          selectedCanvasId={snapshot?.canvas.id}
          isBusy={isBusy}
          onSelect={handleSelectCanvas}
          onRename={handleRenameCanvas}
          onDelete={handleDeleteCanvas}
          onRefresh={() => refreshWorkspace()}
        />
        <SidebarInset className="relative min-h-svh min-w-0 overflow-hidden">
          <SidebarTrigger className="absolute top-3 left-3 z-40 border bg-background shadow-sm md:hidden" />
          {error ? (
            <Alert variant="destructive" className="absolute top-3 left-1/2 z-40 w-[min(32rem,calc(100%-6rem))] -translate-x-1/2 bg-background shadow-md">
              <AlertTitle>Canvas update failed</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          {snapshot ? (
            <Canvas
              selectedPaperId={selectedPaperId}
              selectedRelationshipId={selectedRelationshipId}
              onPaperSelect={(paperId) => {
                setSelectedRelationshipId(undefined)
                setSelectedPaperId(paperId)
              }}
              onRelationshipSelect={(relationshipId) => {
                setSelectedPaperId(undefined)
                setSelectedRelationshipId(relationshipId)
              }}
              onPaperLinkPaste={(url, position) => {
                void handlePaperLink(url, position).catch(() => undefined)
              }}
              onPaperMove={handlePaperMove}
              onRelayout={() => void handleRelayout()}
            >
              {constants.CANVAS_CHAT_ENABLED ? (
                <CanvasLauncher
                  canvas={snapshot.canvas}
                  papers={snapshot.papers}
                  messages={messages}
                  selectedCanvasId={snapshot.canvas.id}
                  selectedPaper={snapshot.papers.find((paper) => paper.id === selectedPaperId)}
                  activeAgent={activeAgent}
                  isBusy={isBusy}
                  error={error}
                  runStatus={runStatus}
                  runStatusLabel={runStatusLabel}
                  onCreate={handleCreate}
                  onAddPaperLink={(url) => handlePaperLink(url)}
                  onSendMessage={handleSendMessage}
                  onClearPaper={() => setSelectedPaperId(undefined)}
                  onStop={activeRun && !isTerminalStatus(activeRun.status) ? handleStop : undefined}
                  onRetry={canRetryRun(activeRun, activeRunPurpose.current, snapshot)
                    ? handleRetryRun
                    : undefined}
                  onUndo={undoToken ? handleUndo : undefined}
                />
              ) : null}
            </Canvas>
          ) : (
            <EmptyCanvas isBusy={isBusy} />
          )}
        </SidebarInset>
      </SidebarProvider>
    </CanvasProvider>
  )
}

function EmptyCanvas({ isBusy }: { isBusy: boolean }) {
  return (
    <main className="flex min-h-svh items-center justify-center p-8" aria-label="Research canvas">
      <div className="max-w-sm text-center">
        <p className="text-base font-semibold">
          {isBusy ? 'Loading canvases…' : 'No canvases yet.'}
        </p>
        {!isBusy ? (
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            Create one in TrueForge.
          </p>
        ) : null}
      </div>
    </main>
  )
}

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong.'
}

function getRunFailureMessage(event: RunEvent) {
  const message = event.payload.message
  return typeof message === 'string' && message.length > 0
    ? message
    : 'The autonomous build failed.'
}

function isTerminalStatus(status: string) {
  return [
    'completed',
    'completed_with_errors',
    'failed',
    'cancelled',
    'run.completed',
    'run.failed',
    'run.cancelled',
  ].includes(status)
}

function isFailureStatus(status: string) {
  return status === 'failed' || status === 'run.failed'
}

function canRetryRun(
  run: AgentRun | undefined,
  purpose: RunPurpose | undefined,
  currentSnapshot: CanvasSnapshot | undefined,
) {
  if (!run) return false
  if (isFailureStatus(run.status)) return true
  if (purpose !== 'build' || !isTerminalStatus(run.status) || !currentSnapshot) {
    return false
  }

  return isRecoverableCanvasBuild(currentSnapshot)
}

async function findLatestCanvasBuildRun(canvasId: CanvasId) {
  const runs = await listRuns(canvasId)
  for (const run of runs) {
    if (run.agent_name !== 'research-map-main') continue
    const events = await listRunEvents(run.id)
    const isCanvasBuild = events.some(
      (event) => event.type === 'run.started' && event.payload.agent === 'main',
    )
    if (isCanvasBuild) return run
  }
  return undefined
}

function runSubscriptionKey(runId: string, purpose: RunPurpose) {
  return `${purpose}:${runId}`
}

function inferRunPurpose(label: string): RunPurpose {
  if (label.includes('Chat') || label.includes('chat')) return 'chat'
  if (label.includes('Paper link')) return 'link'
  if (label.includes('Review')) return 'review'
  if (label.includes('Direct')) return 'direct'
  return 'build'
}

export default App
