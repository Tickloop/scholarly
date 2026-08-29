import {
  PAPER_REFERENCE_PATTERN,
  paperReferenceValidationError,
} from '../utils/isPaperLink.ts'

export type ToolResult = {
  content: Array<{ type: 'text'; text: string }>
  structuredContent?: unknown
}

export type ToolExecution = { signal?: AbortSignal }

export type ModelContextTool = {
  name: string
  description: string
  inputSchema: Record<string, unknown>
  execute: (input: Record<string, unknown>, execution?: ToolExecution) => Promise<ToolResult>
}

export type ModelContextLike = {
  registerTool: (
    tool: ModelContextTool,
    options?: { signal?: AbortSignal },
  ) => Promise<void> | void
}

export type ResearchMapActions = {
  getCanvas: (canvasId: string, signal?: AbortSignal) => Promise<unknown>
  createCanvas: (
    input: { name: string; research_goal: string },
    signal?: AbortSignal,
  ) => Promise<unknown>
  startBuild: (canvasId: string, signal?: AbortSignal) => Promise<unknown>
  addPaperUrl: (
    canvasId: string,
    input: { url: string; x?: number; y?: number },
    signal?: AbortSignal,
  ) => Promise<unknown>
  runAgent: (
    canvasId: string,
    input: {
      agent: 'discovery' | 'reviewer' | 'connection'
      prompt: string
      paper_ids: string[]
    },
    signal?: AbortSignal,
  ) => Promise<unknown>
  updatePaper: (
    canvasId: string,
    paperId: string,
    input: Record<string, unknown>,
    signal?: AbortSignal,
  ) => Promise<unknown>
  updateRelationship: (
    canvasId: string,
    relationshipId: string,
    input: Record<string, unknown>,
    signal?: AbortSignal,
  ) => Promise<unknown>
  relayout: (canvasId: string, signal?: AbortSignal) => Promise<unknown>
  onResult?: (toolName: string, result: unknown) => void
}

const stringProperty = (description: string, maxLength?: number) => ({
  type: 'string',
  description,
  minLength: 1,
  ...(maxLength ? { maxLength } : {}),
})

export function researchMapToolDefinitions(
  canvasId: string | undefined,
  actions: ResearchMapActions,
): ModelContextTool[] {
  const activeCanvas = () => {
    if (!canvasId) throw new Error('No research canvas is active.')
    return canvasId
  }
  const execute =
    (name: string, operation: (input: Record<string, unknown>, signal?: AbortSignal) => Promise<unknown>) =>
    async (input: Record<string, unknown>, execution?: ToolExecution) => {
      try {
        const result = await operation(input, execution?.signal)
        actions.onResult?.(name, result)
        return toolResult(result)
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') throw error
        throw new Error(`${name} failed: ${errorMessage(error)}`, { cause: error })
      }
    }

  return [
    {
      name: 'research_map_get_canvas',
      description: 'Read the active research canvas, including papers and relationships.',
      inputSchema: { type: 'object', properties: {}, additionalProperties: false },
      execute: execute('research_map_get_canvas', (input, signal) => {
        assertOnlyKeys(input, [])
        return actions.getCanvas(activeCanvas(), signal)
      }),
    },
    {
      name: 'research_map_create_canvas',
      description: 'Create a research canvas from a name and autonomous research goal.',
      inputSchema: {
        type: 'object',
        properties: {
          name: stringProperty('Short canvas name.', 200),
          research_goal: stringProperty('Research goal for the autonomous build.'),
        },
        required: ['name', 'research_goal'],
        additionalProperties: false,
      },
      execute: execute('research_map_create_canvas', (input, signal) => {
        assertOnlyKeys(input, ['name', 'research_goal'])
        return actions.createCanvas(
          {
            name: requiredString(input, 'name', 'Canvas name', 200),
            research_goal: requiredString(input, 'research_goal', 'Research goal'),
          },
          signal,
        )
      }),
    },
    {
      name: 'research_map_start_build',
      description: 'Start the full autonomous build for the active canvas.',
      inputSchema: { type: 'object', properties: {}, additionalProperties: false },
      execute: execute('research_map_start_build', (input, signal) => {
        assertOnlyKeys(input, [])
        return actions.startBuild(activeCanvas(), signal)
      }),
    },
    {
      name: 'research_map_add_paper_url',
      description: 'Queue one HTTPS paper URL on the active canvas for autonomous processing.',
      inputSchema: {
        type: 'object',
        properties: {
          url: {
            ...stringProperty('HTTPS paper URL or bare DOI/arXiv identifier.', 2048),
            pattern: PAPER_REFERENCE_PATTERN,
          },
          x: { type: 'number' },
          y: { type: 'number' },
        },
        required: ['url'],
        additionalProperties: false,
      },
      execute: execute('research_map_add_paper_url', (input, signal) => {
        assertOnlyKeys(input, ['url', 'x', 'y'])
        return actions.addPaperUrl(
          activeCanvas(),
          {
            url: requiredPaperReference(input, 'url', 'Paper reference'),
            ...optionalCoordinate(input, 'x'),
            ...optionalCoordinate(input, 'y'),
          },
          signal,
        )
      }),
    },
    directAgentTool('discovery', execute, activeCanvas, actions),
    directAgentTool('reviewer', execute, activeCanvas, actions),
    directAgentTool('connection', execute, activeCanvas, actions),
    {
      name: 'research_map_update_paper',
      description: 'Update user-editable metadata or position for one active-canvas paper.',
      inputSchema: {
        type: 'object',
        properties: {
          paper_id: stringProperty('Paper ID on the active canvas.'),
          title: { type: 'string', minLength: 1 },
          authors: { type: 'array', items: { type: 'string' } },
          year: {
            anyOf: [
              { type: 'integer', minimum: 1600, maximum: 2200 },
              { type: 'null' },
            ],
          },
          month: {
            anyOf: [
              { type: 'integer', minimum: 1, maximum: 12 },
              { type: 'null' },
            ],
          },
          summary: { type: 'string' },
          link: { type: 'string', minLength: 1 },
          x: { type: 'number' },
          y: { type: 'number' },
          pinned: { type: 'boolean' },
        },
        required: ['paper_id'],
        additionalProperties: false,
      },
      execute: execute('research_map_update_paper', (input, signal) => {
        assertOnlyKeys(input, [
          'paper_id', 'title', 'authors', 'year', 'month', 'summary', 'link',
          'x', 'y', 'pinned',
        ])
        const paperId = requiredString(input, 'paper_id', 'Paper ID')
        const update = paperUpdate(input)
        if (Object.keys(update).length === 0) {
          throw new Error('Paper update must include at least one field.')
        }
        return actions.updatePaper(activeCanvas(), paperId, update, signal)
      }),
    },
    {
      name: 'research_map_update_relationship',
      description: 'Update the label or explanation of one active-canvas relationship.',
      inputSchema: {
        type: 'object',
        properties: {
          relationship_id: stringProperty('Relationship ID on the active canvas.'),
          label: { type: 'string', minLength: 1, maxLength: 120 },
          explanation: { type: 'string', minLength: 1 },
        },
        required: ['relationship_id'],
        additionalProperties: false,
      },
      execute: execute('research_map_update_relationship', (input, signal) => {
        assertOnlyKeys(input, ['relationship_id', 'label', 'explanation'])
        const relationshipId = requiredString(
          input,
          'relationship_id',
          'Relationship ID',
        )
        const update: Record<string, unknown> = {}
        if (has(input, 'label')) {
          update.label = requiredString(input, 'label', 'Relationship label', 120)
        }
        if (has(input, 'explanation')) {
          update.explanation = requiredString(
            input,
            'explanation',
            'Relationship explanation',
          )
        }
        if (Object.keys(update).length === 0) {
          throw new Error('Relationship update must include a label or explanation.')
        }
        return actions.updateRelationship(
          activeCanvas(),
          relationshipId,
          update,
          signal,
        )
      }),
    },
    {
      name: 'research_map_relayout',
      description: 'Unpin the active canvas and request its deterministic full relayout.',
      inputSchema: { type: 'object', properties: {}, additionalProperties: false },
      execute: execute('research_map_relayout', (input, signal) => {
        assertOnlyKeys(input, [])
        return actions.relayout(activeCanvas(), signal)
      }),
    },
  ]
}

export function registerResearchMapTools(
  modelContext: ModelContextLike,
  canvasId: string | undefined,
  actions: ResearchMapActions,
): AbortController {
  const controller = new AbortController()
  for (const tool of researchMapToolDefinitions(canvasId, actions)) {
    void Promise.resolve(
      modelContext.registerTool(tool, { signal: controller.signal }),
    ).catch((error: unknown) => {
      if (!controller.signal.aborted) console.warn('WebMCP tool registration failed.', error)
    })
  }
  return controller
}

export function modelContextIfSupported(value: unknown): ModelContextLike | undefined {
  if (!value || typeof value !== 'object') return undefined
  const modelContext = (value as { modelContext?: unknown }).modelContext
  if (!modelContext || typeof modelContext !== 'object') return undefined
  return typeof (modelContext as ModelContextLike).registerTool === 'function'
    ? (modelContext as ModelContextLike)
    : undefined
}

function directAgentTool(
  agent: 'discovery' | 'reviewer' | 'connection',
  execute: (
    name: string,
    operation: (input: Record<string, unknown>, signal?: AbortSignal) => Promise<unknown>,
  ) => ModelContextTool['execute'],
  activeCanvas: () => string,
  actions: ResearchMapActions,
): ModelContextTool {
  const suffix = agent === 'connection' ? 'connections' : agent === 'reviewer' ? 'review' : agent
  const name = `research_map_run_${suffix}`
  const paperRule = agent === 'reviewer' ? 'exactly one paper ID' : agent === 'connection' ? 'exactly two paper IDs' : 'no paper IDs'
  return {
    name,
    description: `Run the ${agent} agent directly for debugging, using ${paperRule}.`,
    inputSchema: {
      type: 'object',
      properties: {
        prompt: stringProperty('Scoped instruction for this direct agent run.'),
        paper_ids: {
          type: 'array',
          items: { type: 'string', minLength: 1 },
          minItems: agent === 'reviewer' ? 1 : agent === 'connection' ? 2 : 0,
          maxItems: agent === 'reviewer' ? 1 : agent === 'connection' ? 2 : 0,
        },
      },
      required: ['prompt', 'paper_ids'],
      additionalProperties: false,
    },
    execute: execute(name, (input, signal) => {
      assertOnlyKeys(input, ['prompt', 'paper_ids'])
      const prompt = requiredString(input, 'prompt', 'Agent prompt')
      const expectedCount = agent === 'reviewer' ? 1 : agent === 'connection' ? 2 : 0
      const paperIds = requiredStringArray(
        input,
        'paper_ids',
        'Paper IDs',
        expectedCount === 0,
      )
      if (paperIds.length !== expectedCount) {
        throw new Error(
          `${agent} agent requires ${expectedCount === 0 ? 'no' : `exactly ${expectedCount}`} paper ID${expectedCount === 1 ? '' : 's'}.`,
        )
      }
      return actions.runAgent(
        activeCanvas(),
        { agent, prompt, paper_ids: paperIds },
        signal,
      )
    }),
  }
}

function paperUpdate(input: Record<string, unknown>) {
  const update: Record<string, unknown> = {}
  if (has(input, 'title')) update.title = requiredString(input, 'title', 'Paper title')
  if (has(input, 'authors')) {
    update.authors = requiredStringArray(input, 'authors', 'Paper authors', true)
  }
  if (has(input, 'year')) {
    update.year = input.year === null
      ? null
      : boundedInteger(input, 'year', 'Paper year', 1600, 2200)
  }
  if (has(input, 'month')) {
    update.month = input.month === null
      ? null
      : boundedInteger(input, 'month', 'Paper month', 1, 12)
  }
  if (has(input, 'summary')) update.summary = stringValue(input, 'summary', 'Paper summary')
  if (has(input, 'link')) {
    update.link = requiredPaperReference(input, 'link', 'Paper reference')
  }
  Object.assign(update, optionalCoordinate(input, 'x'), optionalCoordinate(input, 'y'))
  if (has(input, 'pinned')) {
    if (typeof input.pinned !== 'boolean') throw new Error('Paper pinned must be a boolean.')
    update.pinned = input.pinned
  }
  return update
}

function has(input: Record<string, unknown>, key: string) {
  return Object.prototype.hasOwnProperty.call(input, key)
}

function assertOnlyKeys(input: Record<string, unknown>, allowed: string[]) {
  const extra = Object.keys(input).find((key) => !allowed.includes(key))
  if (extra) throw new Error(`Unexpected input field: ${extra}.`)
}

function requiredString(
  input: Record<string, unknown>,
  key: string,
  label: string,
  maxLength?: number,
) {
  const value = input[key]
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new Error(`${label} is required.`)
  }
  const trimmed = value.trim()
  if (maxLength && trimmed.length > maxLength) {
    throw new Error(`${label} must be at most ${maxLength} characters.`)
  }
  return trimmed
}

function stringValue(input: Record<string, unknown>, key: string, label: string) {
  const value = input[key]
  if (typeof value !== 'string') throw new Error(`${label} must be a string.`)
  return value
}

function requiredStringArray(
  input: Record<string, unknown>,
  key: string,
  label: string,
  allowEmpty = false,
) {
  const value = input[key]
  if (!Array.isArray(value) || (!allowEmpty && value.length === 0)) {
    throw new Error(`${label} must be ${allowEmpty ? 'an array' : 'a non-empty array'}.`)
  }
  if (value.some((item) => typeof item !== 'string' || item.trim().length === 0)) {
    throw new Error(`${label} must contain only nonblank strings.`)
  }
  return value.map((item) => (item as string).trim())
}

function boundedInteger(
  input: Record<string, unknown>,
  key: string,
  label: string,
  minimum: number,
  maximum: number,
) {
  const value = input[key]
  if (!Number.isInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    throw new Error(`${label} must be an integer from ${minimum} to ${maximum}.`)
  }
  return value as number
}

function optionalCoordinate(input: Record<string, unknown>, key: 'x' | 'y') {
  if (!has(input, key)) return {}
  const value = input[key]
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`Paper ${key} must be a finite number.`)
  }
  return { [key]: value }
}

function requiredPaperReference(
  input: Record<string, unknown>,
  key: string,
  label: string,
) {
  const value = requiredString(input, key, label, 2048)
  const error = paperReferenceValidationError(value)
  if (error) throw new Error(error)
  return value
}

function toolResult(value: unknown): ToolResult {
  return {
    content: [{ type: 'text', text: JSON.stringify(value) }],
    structuredContent: value,
  }
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Unknown error.'
}
