import type { Page, Route } from '@playwright/test'

type CanvasSummary = {
  id: string
  name: string
  research_goal: string
  research_brief?: Record<string, unknown> | null
  build_status: string
  created_at: string
  updated_at: string
}

export type MockPaper = {
  id: string
  title: string
  authors: string[]
  year: number | null
  month: number | null
  summary: string
  link: string
  processing_status: string
  error: string | null
  review: Record<string, string> | null
  x: number
  y: number
  pinned: boolean
}

type MockRelationship = {
  id: string
  source: string
  target: string
  type: 'extends' | 'related'
  label: string
  explanation: string
}

type MockMessage = {
  id: string
  canvas_id: string
  paper_id: string | null
  role: 'user' | 'assistant'
  agent_name: string
  content: string
  status: string
  created_at: string
}

type CanvasState = {
  canvas: CanvasSummary
  papers: MockPaper[]
  relationships: MockRelationship[]
}

type RunState = {
  id: string
  canvasId: string
  paperId?: string
  status: string
  agentName: string
  createdAt: string
  events: string[]
  finalize: () => void
  finalized: boolean
  release?: Promise<void>
  resolve?: () => void
}

const now = '2026-08-29T12:00:00Z'

export class MockResearchApi {
  readonly canvases = new Map<string, CanvasState>()
  readonly messages = new Map<string, MockMessage[]>()
  readonly canvasCreateRequests: Array<{ name: string; research_goal: string }> = []
  readonly chatRequests: Array<{ canvasId: string; content: string; paper_ids: string[] }> = []
  readonly paperLinkRequests: Array<{ canvasId: string; url: string; x?: number; y?: number }> = []
  readonly paperUpdates: Array<{ canvasId: string; paperId: string; input: Record<string, unknown> }> = []
  readonly paperRetryRequests: Array<{ canvasId: string; paperId: string }> = []
  readonly runCancelRequests: string[] = []
  readonly runRetryRequests: string[] = []
  layoutResetCount = 0
  layoutWriteCount = 0
  lastRunId?: string

  private sequence = 0
  private deferRun = false
  private nextBuildEmptyStopReason?: string
  private readonly failedSubmissions = new Set<'canvas' | 'chat' | 'link'>()
  private readonly runs = new Map<string, RunState>()
  private readonly undo = new Map<string, CanvasState>()

  async install(page: Page) {
    await page.route('**/api/v1/**', (route) => this.handle(route))
  }

  seedCanvas(
    name: string,
    options: {
      papers?: MockPaper[]
      relationships?: MockRelationship[]
      messages?: MockMessage[]
      buildStatus?: string
    } = {},
  ) {
    const id = `canvas-${++this.sequence}`
    const state: CanvasState = {
      canvas: {
        id,
        name,
        research_goal: `Research goal for ${name}`,
        build_status: options.buildStatus ?? 'completed',
        created_at: now,
        updated_at: now,
      },
      papers: clone(options.papers ?? []),
      relationships: clone(options.relationships ?? []),
    }
    this.canvases.set(id, state)
    this.messages.set(id, clone(options.messages ?? []))
    return state
  }

  seedCompletedBuild(
    name: string,
    summary: {
      stopReason: string
      acceptedPapers: number
      completedReviews: number
      relationships: number
    },
  ) {
    const papers = Array.from(
      { length: summary.acceptedPapers },
      (_, index) => mockPaper(
        `paper-${this.sequence + 1}-${index}`,
        `${name} paper ${index + 1}`,
        2020 + index,
      ),
    )
    const state = this.seedCanvas(name, { papers })
    state.canvas.research_brief = {
      pipeline_summary: {
        status: 'completed',
        accepted_papers: summary.acceptedPapers,
        completed_reviews: summary.completedReviews,
        relationships: summary.relationships,
        paper_failures: [],
        batches_started: 1,
        stop_reason: summary.stopReason,
        coverage: [],
      },
    }
    const run = this.makeRun(
      state.canvas.id,
      ['run.started', 'run.completed'],
      () => undefined,
      { agent: 'main' },
    )
    run.status = 'completed'
    run.finalized = true
    return { state, runId: run.id }
  }

  deferNextRun() {
    this.deferRun = true
  }

  completeNextBuildEmpty(
    stopReason = 'insufficient_relevant_candidates',
  ) {
    this.nextBuildEmptyStopReason = stopReason
  }

  failNextSubmission(kind: 'canvas' | 'chat' | 'link') {
    this.failedSubmissions.add(kind)
  }

  releaseRun(runId = this.lastRunId) {
    if (!runId) throw new Error('No run is waiting for release.')
    const run = this.runs.get(runId)
    if (!run?.resolve) throw new Error(`Run ${runId} is not deferred.`)
    run.resolve()
    run.resolve = undefined
  }

  paper(canvasId: string, paperId: string) {
    return this.requireCanvas(canvasId).papers.find((paper) => paper.id === paperId)
  }

  private async handle(route: Route) {
    const request = route.request()
    const method = request.method()
    const path = new URL(request.url()).pathname.replace('/api/v1', '')

    if (method === 'GET' && path === '/canvases') {
      return route.fulfill({ json: [...this.canvases.values()].map(({ canvas }) => canvas) })
    }
    if (method === 'GET' && path === '/runs') {
      const canvasId = new URL(request.url()).searchParams.get('canvas_id')
      const runs = [...this.runs.values()]
        .filter((run) => !canvasId || run.canvasId === canvasId)
        .reverse()
        .map(runDetail)
      return route.fulfill({ json: runs })
    }
    if (method === 'POST' && path === '/canvases') {
      if (this.consumeFailure('canvas')) {
        return route.fulfill({ status: 422, json: { detail: 'Canvas request failed.' } })
      }
      const input = request.postDataJSON() as { name: string; research_goal: string }
      this.canvasCreateRequests.push(input)
      const state = this.seedCanvas(input.name, { buildStatus: 'idle' })
      state.canvas.research_goal = input.research_goal
      return route.fulfill({ status: 201, json: state })
    }

    const canvasMatch = path.match(/^\/canvases\/([^/]+)$/)
    if (canvasMatch) {
      const canvasId = canvasMatch[1]
      if (method === 'GET') return route.fulfill({ json: this.requireCanvas(canvasId) })
      if (method === 'PATCH') {
        const input = request.postDataJSON() as { name: string }
        const state = this.requireCanvas(canvasId)
        state.canvas.name = input.name
        return route.fulfill({ json: state.canvas })
      }
      if (method === 'DELETE') {
        this.canvases.delete(canvasId)
        this.messages.delete(canvasId)
        return route.fulfill({ status: 204, body: '' })
      }
    }

    const buildMatch = path.match(/^\/canvases\/([^/]+)\/builds$/)
    if (method === 'POST' && buildMatch) {
      const canvasId = buildMatch[1]
      const state = this.requireCanvas(canvasId)
      state.canvas.build_status = 'running'
      const emptyStopReason = this.nextBuildEmptyStopReason
      this.nextBuildEmptyStopReason = undefined
      const run = this.makeRun(canvasId, ['run.started', 'paper.added', 'review.completed', 'run.completed'], () => {
        state.canvas.build_status = 'completed'
        if (emptyStopReason) {
          state.canvas.research_brief = {
            pipeline_summary: {
              status: 'completed',
              accepted_papers: 0,
              completed_reviews: 0,
              relationships: 0,
              paper_failures: [],
              batches_started: 0,
              stop_reason: emptyStopReason,
              coverage: [],
            },
          }
        } else if (state.papers.length === 0) {
          state.papers.push(mockPaper(`paper-${++this.sequence}`, `Evidence for ${state.canvas.name}`, 2020))
          state.canvas.research_brief = {
            pipeline_summary: {
              status: 'completed',
              accepted_papers: 1,
              completed_reviews: 1,
              relationships: 0,
              paper_failures: [],
              batches_started: 1,
              stop_reason: 'coverage_sufficient',
              coverage: [],
            },
          }
        }
      }, { agent: 'main' })
      return route.fulfill({ status: 202, json: runRead(run) })
    }

    const messagesMatch = path.match(/^\/canvases\/([^/]+)\/messages$/)
    if (messagesMatch) {
      const canvasId = messagesMatch[1]
      if (method === 'GET') return route.fulfill({ json: this.messages.get(canvasId) ?? [] })
      if (method === 'POST') {
        if (this.consumeFailure('chat')) {
          return route.fulfill({ status: 422, json: { detail: 'Chat request failed.' } })
        }
        const input = request.postDataJSON() as { content: string; paper_ids: string[] }
        this.chatRequests.push({ canvasId, ...input })
        const agent = input.paper_ids.length === 1 ? 'reviewer' : 'main'
        const queuedWithoutRun = input.content.includes('queued')
        const message = this.message(
          canvasId,
          'user',
          agent,
          input.content,
          queuedWithoutRun || this.deferRun ? 'queued' : 'completed',
        )
        const stored = this.messages.get(canvasId) ?? []
        stored.push(message)
        this.messages.set(canvasId, stored)
        if (queuedWithoutRun) {
          return route.fulfill({ status: 202, json: { message, run: null, queued: true } })
        }
        const answer = `Answer for: ${input.content}`
        const run = this.makeRun(canvasId, ['agent.message.delta', 'run.completed'], () => {
          message.status = 'completed'
          stored.push(this.message(canvasId, 'assistant', agent, answer, 'completed'))
        }, { content: answer, agent })
        run.events.unshift(JSON.stringify({
          type: 'run.started',
          payload: { agent: `research-map-${agent}` },
        }))
        return route.fulfill({ status: 202, json: { message, run: runRead(run), queued: false } })
      }
    }

    const linkMatch = path.match(/^\/canvases\/([^/]+)\/papers\/from-link$/)
    if (method === 'POST' && linkMatch) {
      if (this.consumeFailure('link')) {
        return route.fulfill({ status: 422, json: { detail: 'Paper link request failed.' } })
      }
      const canvasId = linkMatch[1]
      const input = request.postDataJSON() as { url: string; x?: number; y?: number }
      this.paperLinkRequests.push({ canvasId, ...input })
      const state = this.requireCanvas(canvasId)
      const paperId = `paper-${++this.sequence}`
      state.papers.push({
        ...mockPaper(paperId, `Queued paper: ${input.url.split('/').pop()}`, 2026),
        link: input.url,
        processing_status: 'queued',
        review: null,
        x: input.x ?? 0,
        y: input.y ?? 0,
      })
      const run = this.makeRun(
        canvasId,
        ['agent.message.delta', 'paper.added', 'review.completed', 'relationship.added', 'run.completed'],
        () => {
          const paper = this.paper(canvasId, paperId)!
          Object.assign(paper, {
            title: 'Resolved pasted paper',
            authors: ['Ada Researcher'],
            year: 2021,
            month: 4,
            summary: 'Verified paper metadata.',
            processing_status: 'reviewed',
            review: review('Resolved review'),
          })
          const neighbor = state.papers.find((candidate) => candidate.id !== paperId)
          if (neighbor) {
            state.relationships.push({
              id: `relationship-${++this.sequence}`,
              source: neighbor.id,
              target: paperId,
              type: 'extends',
              label: 'Extends the baseline',
              explanation: 'The pasted paper extends the existing method.',
            })
          }
        },
        { content: '{"internal":"link-json"}', agent: 'reviewer' },
        paperId,
      )
      return route.fulfill({ status: 202, json: runRead(run) })
    }

    const paperRetryMatch = path.match(/^\/canvases\/([^/]+)\/papers\/([^/]+)\/retry$/)
    if (method === 'POST' && paperRetryMatch) {
      const [, canvasId, paperId] = paperRetryMatch
      this.paperRetryRequests.push({ canvasId, paperId })
      const paper = this.requirePaper(this.requireCanvas(canvasId), paperId)
      paper.processing_status = 'queued'
      paper.error = null
      const run = this.makeRun(
        canvasId,
        ['paper.added', 'review.completed', 'run.completed'],
        () => {
          paper.processing_status = 'reviewed'
          paper.review = review('Retried review')
        },
        undefined,
        paperId,
      )
      return route.fulfill({ status: 202, json: runRead(run) })
    }

    const paperMatch = path.match(/^\/canvases\/([^/]+)\/papers\/([^/]+)$/)
    if (paperMatch) {
      const [, canvasId, paperId] = paperMatch
      const state = this.requireCanvas(canvasId)
      if (method === 'PATCH') {
        const input = request.postDataJSON() as Record<string, unknown>
        this.paperUpdates.push({ canvasId, paperId, input })
        Object.assign(this.requirePaper(state, paperId), input)
        return route.fulfill({ json: state })
      }
      if (method === 'DELETE') {
        const token = this.saveUndo(state)
        state.papers = state.papers.filter((paper) => paper.id !== paperId)
        state.relationships = state.relationships.filter(
          (relationship) => relationship.source !== paperId && relationship.target !== paperId,
        )
        return route.fulfill({ json: { undo_token: token } })
      }
    }

    const reviewMatch = path.match(/^\/canvases\/([^/]+)\/papers\/([^/]+)\/review$/)
    if (method === 'PATCH' && reviewMatch) {
      const [, canvasId, paperId] = reviewMatch
      const state = this.requireCanvas(canvasId)
      const input = request.postDataJSON() as { sections: Record<string, string> }
      const paper = this.requirePaper(state, paperId)
      paper.review = { ...(paper.review ?? {}), ...input.sections }
      return route.fulfill({ json: state })
    }

    const relationshipMatch = path.match(/^\/canvases\/([^/]+)\/relationships\/([^/]+)$/)
    if (relationshipMatch) {
      const [, canvasId, relationshipId] = relationshipMatch
      const state = this.requireCanvas(canvasId)
      if (method === 'PATCH') {
        const input = request.postDataJSON() as Partial<MockRelationship>
        Object.assign(this.requireRelationship(state, relationshipId), input)
        return route.fulfill({ json: state })
      }
      if (method === 'DELETE') {
        const token = this.saveUndo(state)
        state.relationships = state.relationships.filter(({ id }) => id !== relationshipId)
        return route.fulfill({ json: { undo_token: token } })
      }
    }

    const undoMatch = path.match(/^\/canvases\/([^/]+)\/undo$/)
    if (method === 'POST' && undoMatch) {
      const canvasId = undoMatch[1]
      const { undo_token: token } = request.postDataJSON() as { undo_token: string }
      const prior = this.undo.get(token)
      if (!prior || prior.canvas.id !== canvasId) return route.fulfill({ status: 404, body: 'Undo not found' })
      const restored = clone(prior)
      this.canvases.set(canvasId, restored)
      this.undo.delete(token)
      return route.fulfill({ json: restored })
    }

    const layoutMatch = path.match(/^\/canvases\/([^/]+)\/layout$/)
    if (method === 'PUT' && layoutMatch) {
      const state = this.requireCanvas(layoutMatch[1])
      const input = request.postDataJSON() as {
        positions: Array<{ paper_id: string; x: number; y: number; pinned: boolean }>
      }
      for (const position of input.positions) {
        Object.assign(this.requirePaper(state, position.paper_id), {
          x: position.x,
          y: position.y,
          pinned: position.pinned,
        })
      }
      this.layoutWriteCount += 1
      return route.fulfill({ json: state })
    }

    const resetMatch = path.match(/^\/canvases\/([^/]+)\/layout\/reset$/)
    if (method === 'POST' && resetMatch) {
      const state = this.requireCanvas(resetMatch[1])
      state.papers.forEach((paper) => { paper.pinned = false })
      this.layoutResetCount += 1
      return route.fulfill({ json: state })
    }

    const runCancelMatch = path.match(/^\/runs\/([^/]+)\/cancel$/)
    if (method === 'POST' && runCancelMatch) {
      const run = this.runs.get(runCancelMatch[1])
      if (!run) return route.fulfill({ status: 404, body: 'Run not found' })
      this.runCancelRequests.push(run.id)
      run.status = 'cancelled'
      run.events = [JSON.stringify({
        type: 'run.cancelled',
        payload: { message: 'Cancelled by user.', paper_id: run.paperId },
      })]
      run.finalized = true
      if (run.paperId) {
        const paper = this.paper(run.canvasId, run.paperId)!
        paper.processing_status = 'failed'
        paper.error = 'Cancelled by user.'
      }
      run.resolve?.()
      run.resolve = undefined
      return route.fulfill({ json: runRead(run) })
    }

    const runRetryMatch = path.match(/^\/runs\/([^/]+)\/retry$/)
    if (method === 'POST' && runRetryMatch) {
      const original = this.runs.get(runRetryMatch[1])
      if (!original) return route.fulfill({ status: 404, body: 'Run not found' })
      this.runRetryRequests.push(original.id)
      const state = this.requireCanvas(original.canvasId)
      state.canvas.build_status = 'running'
      const run = this.makeRun(
        original.canvasId,
        ['run.started', 'paper.added', 'review.completed', 'run.completed'],
        () => {
          state.canvas.build_status = 'completed'
          state.papers.push(mockPaper(
            `paper-${++this.sequence}`,
            `Recovered evidence for ${state.canvas.name}`,
            2021,
          ))
          state.canvas.research_brief = {
            pipeline_summary: {
              status: 'completed',
              accepted_papers: 1,
              completed_reviews: 1,
              relationships: 0,
              paper_failures: [],
              batches_started: 1,
              stop_reason: 'coverage_sufficient',
              coverage: [],
            },
          }
        },
        { agent: 'main' },
      )
      return route.fulfill({ status: 202, json: runRead(run) })
    }

    const runEventsMatch = path.match(/^\/runs\/([^/]+)\/events$/)
    if (method === 'GET' && runEventsMatch) {
      const run = this.runs.get(runEventsMatch[1])
      if (!run) return route.fulfill({ status: 404, body: 'Run not found' })
      if (run.release) await run.release
      if (!run.finalized) {
        run.finalize()
        run.finalized = true
        run.status = terminalStatus(run.events)
      }
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'Cache-Control': 'no-cache' },
        body: run.events.map((type, index) => this.event(run, type, index)).join(''),
      })
    }

    const runEventLogMatch = path.match(/^\/runs\/([^/]+)\/event-log$/)
    if (method === 'GET' && runEventLogMatch) {
      const run = this.runs.get(runEventLogMatch[1])
      if (!run) return route.fulfill({ status: 404, body: 'Run not found' })
      return route.fulfill({
        json: run.events.map((encoded, index) => {
          const { type, payload } = JSON.parse(encoded) as {
            type: string
            payload: Record<string, unknown>
          }
          return {
            id: `${run.id}-event-${index}`,
            run_id: run.id,
            canvas_id: run.canvasId,
            type,
            payload,
            created_at: now,
          }
        }),
      })
    }

    return route.fulfill({ status: 404, body: `${method} ${path} is not mocked` })
  }

  private makeRun(
    canvasId: string,
    events: string[],
    finalize: () => void,
    eventPayload?: Record<string, unknown>,
    paperId?: string,
  ) {
    const id = `run-${++this.sequence}`
    let resolve: (() => void) | undefined
    const release = this.deferRun
      ? new Promise<void>((done) => { resolve = done })
      : undefined
    this.deferRun = false
    const run: RunState = {
      id,
      canvasId,
      paperId,
      status: 'queued',
      agentName: 'research-map-main',
      createdAt: now,
      events: events.map((type) => JSON.stringify({ type, payload: eventPayload ?? {} })),
      finalize,
      finalized: false,
      release,
      resolve,
    }
    this.runs.set(id, run)
    this.lastRunId = id
    return run
  }

  private event(run: RunState, encoded: string, index: number) {
    const { type, payload } = JSON.parse(encoded) as { type: string; payload: Record<string, unknown> }
    const data = {
      id: `${run.id}-event-${index}`,
      run_id: run.id,
      canvas_id: run.canvasId,
      type,
      payload,
      created_at: now,
    }
    return `id: ${data.id}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`
  }

  private message(
    canvasId: string,
    role: 'user' | 'assistant',
    agent: string,
    content: string,
    status: string,
  ): MockMessage {
    return {
      id: `message-${++this.sequence}`,
      canvas_id: canvasId,
      paper_id: null,
      role,
      agent_name: agent,
      content,
      status,
      created_at: now,
    }
  }

  private saveUndo(state: CanvasState) {
    const token = `undo-${++this.sequence}`
    this.undo.set(token, clone(state))
    return token
  }

  private consumeFailure(kind: 'canvas' | 'chat' | 'link') {
    const failed = this.failedSubmissions.has(kind)
    this.failedSubmissions.delete(kind)
    return failed
  }

  private requireCanvas(canvasId: string) {
    const state = this.canvases.get(canvasId)
    if (!state) throw new Error(`Missing mock canvas ${canvasId}`)
    return state
  }

  private requirePaper(state: CanvasState, paperId: string) {
    const paper = state.papers.find(({ id }) => id === paperId)
    if (!paper) throw new Error(`Missing mock paper ${paperId}`)
    return paper
  }

  private requireRelationship(state: CanvasState, relationshipId: string) {
    const relationship = state.relationships.find(({ id }) => id === relationshipId)
    if (!relationship) throw new Error(`Missing mock relationship ${relationshipId}`)
    return relationship
  }
}

export function mockPaper(
  id: string,
  title: string,
  year: number | null,
  x = 0,
  y = 0,
): MockPaper {
  return {
    id,
    title,
    authors: ['Test Author'],
    year,
    month: year === null ? null : 1,
    summary: `Summary for ${title}.`,
    link: `https://example.com/${id}.pdf`,
    processing_status: 'reviewed',
    error: null,
    review: review(`Review for ${title}`),
    x,
    y,
    pinned: false,
  }
}

export function mockRelationship(
  id: string,
  source: string,
  target: string,
  label = 'Builds on',
): MockRelationship {
  return {
    id,
    source,
    target,
    type: 'extends',
    label,
    explanation: `${target} extends ${source}.`,
  }
}

export function mockMessage(
  id: string,
  canvasId: string,
  role: 'user' | 'assistant',
  content: string,
): MockMessage {
  return {
    id,
    canvas_id: canvasId,
    paper_id: null,
    role,
    agent_name: role === 'user' ? 'main' : 'reviewer',
    content,
    status: 'completed',
    created_at: now,
  }
}

function review(prefix: string) {
  return {
    coreIdea: `${prefix}: core idea`,
    problemSpace: `${prefix}: problem`,
    approach: `${prefix}: approach`,
    data: `${prefix}: data`,
    novelContribution: `${prefix}: novelty`,
    results: `${prefix}: results`,
    benchmarks: `${prefix}: benchmarks`,
    statisticalEvidence: `${prefix}: statistics`,
    limitations: `${prefix}: limitations`,
    citedIdeasAndDifferences: `${prefix}: citations`,
  }
}

function runRead(run: RunState) {
  return {
    id: run.id,
    canvas_id: run.canvasId,
    status: run.status,
    paper_id: run.paperId ?? null,
  }
}

function runDetail(run: RunState) {
  return {
    id: run.id,
    canvas_id: run.canvasId,
    agent_name: run.agentName,
    status: run.status,
    trueforge_session_id: null,
    trueforge_turn_id: null,
    error: null,
    created_at: run.createdAt,
    updated_at: now,
  }
}

function terminalStatus(events: string[]) {
  const terminal = [...events].reverse().map((encoded) => JSON.parse(encoded) as {
    type: string
  }).find(({ type }) => type.startsWith('run.'))
  if (terminal?.type === 'run.failed') return 'failed'
  if (terminal?.type === 'run.cancelled') return 'cancelled'
  return 'completed'
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}
