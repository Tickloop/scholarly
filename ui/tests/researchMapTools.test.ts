import assert from 'node:assert/strict'
import test from 'node:test'

import {
  modelContextIfSupported,
  registerResearchMapTools,
  type ModelContextTool,
  type ResearchMapActions,
} from '../src/webmcp/researchMapTools.ts'

function actions(calls: string[]): ResearchMapActions {
  const result = async (name: string) => {
    calls.push(name)
    return { ok: name }
  }
  return {
    getCanvas: () => result('get'),
    createCanvas: () => result('create'),
    startBuild: () => result('build'),
    addPaperUrl: () => result('url'),
    runAgent: (_canvas, input) => result(input.agent),
    updatePaper: () => result('paper'),
    updateRelationship: () => result('relationship'),
    relayout: () => result('relayout'),
  }
}

test('registers the exact ten research-map tools and executes through actions', async () => {
  const tools: ModelContextTool[] = []
  const signals: AbortSignal[] = []
  const calls: string[] = []
  const controller = registerResearchMapTools(
    {
      registerTool(tool, options) {
        tools.push(tool)
        if (options?.signal) signals.push(options.signal)
      },
    },
    'canvas-1',
    actions(calls),
  )

  assert.deepEqual(tools.map((tool) => tool.name), [
    'research_map_get_canvas',
    'research_map_create_canvas',
    'research_map_start_build',
    'research_map_add_paper_url',
    'research_map_run_discovery',
    'research_map_run_review',
    'research_map_run_connections',
    'research_map_update_paper',
    'research_map_update_relationship',
    'research_map_relayout',
  ])
  const result = await tools[0].execute({})
  assert.equal(result.content[0].text, '{"ok":"get"}')
  assert.deepEqual(calls, ['get'])
  controller.abort()
  assert.ok(signals.every((signal) => signal.aborted))
})

test('reports unsupported browsers and surfaces API failures', async () => {
  assert.equal(modelContextIfSupported({}), undefined)
  const tools: ModelContextTool[] = []
  const failing = actions([])
  failing.getCanvas = async () => { throw new Error('Canvas not found') }
  registerResearchMapTools(
    { registerTool(tool) { tools.push(tool) } },
    'missing',
    failing,
  )
  await assert.rejects(
    tools[0].execute({}),
    /research_map_get_canvas failed: Canvas not found/,
  )
})

test('passes invocation cancellation to fetch actions', async () => {
  const tools: ModelContextTool[] = []
  let received: AbortSignal | undefined
  const configured = actions([])
  configured.getCanvas = async (_canvas, signal) => {
    received = signal
    throw new DOMException('cancelled', 'AbortError')
  }
  registerResearchMapTools(
    { registerTool(tool) { tools.push(tool) } },
    'canvas-1',
    configured,
  )
  const invocation = new AbortController()
  invocation.abort()
  await assert.rejects(tools[0].execute({}, { signal: invocation.signal }), {
    name: 'AbortError',
  })
  assert.equal(received, invocation.signal)
})

test('rejects missing or blank canvas fields before calling the API', async () => {
  const tools: ModelContextTool[] = []
  const calls: string[] = []
  registerResearchMapTools(
    { registerTool(tool) { tools.push(tool) } },
    undefined,
    actions(calls),
  )
  const create = tool(tools, 'research_map_create_canvas')

  await assert.rejects(
    create.execute({ name: 'Research', research_goal: undefined }),
    /Research goal is required/,
  )
  await assert.rejects(
    create.execute({ name: '   ', research_goal: 'Map the field' }),
    /Canvas name is required/,
  )
  assert.deepEqual(calls, [])
})

test('rejects non-HTTPS paper URLs before calling the API', async () => {
  const tools: ModelContextTool[] = []
  const calls: string[] = []
  registerResearchMapTools(
    { registerTool(tool) { tools.push(tool) } },
    'canvas-1',
    actions(calls),
  )

  await assert.rejects(
    tool(tools, 'research_map_add_paper_url').execute({
      url: 'http://example.com/paper.pdf',
    }),
    /must use HTTPS/,
  )
  assert.deepEqual(calls, [])
})

test('accepts the API paper-reference forms and rejects unsupported bare input', async () => {
  const tools: ModelContextTool[] = []
  const received: unknown[] = []
  const configured = actions([])
  configured.addPaperUrl = async (_canvas, input) => {
    received.push(input.url)
    return input
  }
  registerResearchMapTools(
    { registerTool(tool) { tools.push(tool) } },
    'canvas-1',
    configured,
  )
  const add = tool(tools, 'research_map_add_paper_url')

  for (const url of [
    'https://publisher.example/paper.pdf',
    '10.1000/example',
    'doi:10.1000/example-two',
    '2005.11401v2',
    'arXiv:hep-th/9901001',
    'math.GT/0309136v1',
  ]) {
    await add.execute({ url })
  }
  for (const url of [
    'ftp://publisher.example/paper.pdf',
    'not a paper',
    'https://user:secret@publisher.example/paper.pdf',
  ]) {
    await assert.rejects(
      add.execute({ url }),
      /HTTPS URL|public hostname and no credentials/,
    )
  }
  await assert.rejects(
    add.execute({ url: '10.1000/example', unexpected: true }),
    /Unexpected input field/,
  )
  assert.deepEqual(received, [
    'https://publisher.example/paper.pdf',
    '10.1000/example',
    'doi:10.1000/example-two',
    '2005.11401v2',
    'arXiv:hep-th/9901001',
    'math.GT/0309136v1',
  ])
})

test('uses the same paper-reference contract for metadata updates', async () => {
  const tools: ModelContextTool[] = []
  const received: unknown[] = []
  const configured = actions([])
  configured.updatePaper = async (_canvas, _paper, input) => {
    received.push(input)
    return input
  }
  registerResearchMapTools(
    { registerTool(tool) { tools.push(tool) } },
    'canvas-1',
    configured,
  )
  const update = tool(tools, 'research_map_update_paper')
  await update.execute({ paper_id: 'paper-1', link: 'doi:10.1000/updated' })
  await update.execute({ paper_id: 'paper-1', link: 'arXiv:2005.11401' })
  await assert.rejects(
    update.execute({ paper_id: 'paper-1', link: 'ftp://example.org/paper' }),
    /must be an HTTPS URL, DOI, or arXiv ID/,
  )
  assert.deepEqual(received, [
    { link: 'doi:10.1000/updated' },
    { link: 'arXiv:2005.11401' },
  ])
})

test('rejects invalid year and coordinates before calling the API', async () => {
  const tools: ModelContextTool[] = []
  const calls: string[] = []
  registerResearchMapTools(
    { registerTool(tool) { tools.push(tool) } },
    'canvas-1',
    actions(calls),
  )

  await assert.rejects(
    tool(tools, 'research_map_update_paper').execute({
      paper_id: 'paper-1',
      year: 1599,
    }),
    /Paper year must be an integer from 1600 to 2200/,
  )
  await assert.rejects(
    tool(tools, 'research_map_add_paper_url').execute({
      url: 'https://example.com/paper.pdf',
      x: Number.POSITIVE_INFINITY,
    }),
    /Paper x must be a finite number/,
  )
  assert.deepEqual(calls, [])
})

test('allows paper dates to be cleared to the API unknown-date contract', async () => {
  const tools: ModelContextTool[] = []
  const updates: unknown[] = []
  const configured = actions([])
  configured.updatePaper = async (_canvas, paperId, input) => {
    updates.push({ paperId, input })
    return input
  }
  registerResearchMapTools(
    { registerTool(tool) { tools.push(tool) } },
    'canvas-1',
    configured,
  )

  await tool(tools, 'research_map_update_paper').execute({
    paper_id: 'paper-1',
    year: null,
    month: null,
  })

  assert.deepEqual(updates, [{
    paperId: 'paper-1',
    input: { year: null, month: null },
  }])
})

test('rejects insecure paper link updates before calling the API', async () => {
  const tools: ModelContextTool[] = []
  const calls: string[] = []
  registerResearchMapTools(
    { registerTool(tool) { tools.push(tool) } },
    'canvas-1',
    actions(calls),
  )

  await assert.rejects(
    tool(tools, 'research_map_update_paper').execute({
      paper_id: 'paper-1',
      link: 'http://example.com/paper.pdf',
    }),
    /must use HTTPS/,
  )
  assert.deepEqual(calls, [])
})

test('passes validated values without string-coercing missing input', async () => {
  const tools: ModelContextTool[] = []
  const received: unknown[] = []
  const configured = actions([])
  configured.createCanvas = async (input) => { received.push(input); return input }
  configured.addPaperUrl = async (_canvas, input) => { received.push(input); return input }
  configured.updatePaper = async (_canvas, paperId, input) => {
    received.push({ paperId, input })
    return input
  }
  configured.updateRelationship = async (_canvas, relationshipId, input) => {
    received.push({ relationshipId, input })
    return input
  }
  registerResearchMapTools(
    { registerTool(tool) { tools.push(tool) } },
    'canvas-1',
    configured,
  )

  await tool(tools, 'research_map_create_canvas').execute({
    name: ' Evidence map ',
    research_goal: ' Map trustworthy retrieval research ',
  })
  await tool(tools, 'research_map_add_paper_url').execute({
    url: 'https://arxiv.org/abs/2005.11401',
    x: -25.5,
    y: 120,
  })
  await tool(tools, 'research_map_update_paper').execute({
    paper_id: ' paper-1 ',
    year: 2020,
    month: 5,
    x: 10,
    pinned: true,
  })
  await tool(tools, 'research_map_update_relationship').execute({
    relationship_id: ' relationship-1 ',
    label: ' Uses retrieval ',
  })

  assert.deepEqual(received, [
    { name: 'Evidence map', research_goal: 'Map trustworthy retrieval research' },
    { url: 'https://arxiv.org/abs/2005.11401', x: -25.5, y: 120 },
    {
      paperId: 'paper-1',
      input: { year: 2020, month: 5, x: 10, pinned: true },
    },
    { relationshipId: 'relationship-1', input: { label: 'Uses retrieval' } },
  ])
})

function tool(tools: ModelContextTool[], name: string) {
  const match = tools.find((candidate) => candidate.name === name)
  assert.ok(match, `Missing tool ${name}`)
  return match
}
