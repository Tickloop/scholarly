import { expect, test, type Locator, type Page } from '@playwright/test'

import {
  MockResearchApi,
  mockMessage,
  mockPaper,
  mockRelationship,
} from './fixtures/mockResearchApi'

const consoleErrors = new WeakMap<Page, string[]>()

test.beforeEach(({ page }) => {
  const errors: string[] = []
  consoleErrors.set(page, errors)
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
})

test.afterEach(({ page }) => {
  expect(consoleErrors.get(page)).toEqual([])
})

test('keeps the empty-canvas submit control stable for one ordinary pointer click', async ({ page }) => {
  const api = new MockResearchApi()
  await api.install(page)
  await page.setViewportSize({ width: 1600, height: 1000 })
  await page.goto('/')

  await page.getByRole('textbox', { name: 'Research goal' })
    .fill('Stable pointer submission')
  const button = page.getByRole('button', { name: 'Build canvas' })
  await button.evaluate((element) => {
    element.setAttribute('data-instance-token', 'original-submit')
  })
  const firstBox = await button.boundingBox()
  await page.waitForTimeout(150)
  const secondBox = await button.boundingBox()

  expect(await button.getAttribute('data-instance-token')).toBe('original-submit')
  expect(firstBox).not.toBeNull()
  expect(secondBox).toEqual(firstBox)
  await button.click()

  await expect.poll(() => api.canvasCreateRequests).toEqual([{
    name: 'Stable pointer submission',
    research_goal: 'Stable pointer submission',
  }])
  await expect(page.getByRole('button', { name: 'Build canvas' })).toHaveCount(0)
})

test('clears the composer on submission and restores it only when the request fails', async ({ page }) => {
  const api = new MockResearchApi()
  api.seedCanvas('Composer canvas')
  api.failNextSubmission('chat')
  await api.install(page)
  await page.goto('/')

  const composer = page.getByRole('textbox', { name: 'Message, @paper, or paper link' })
  await composer.fill('Restore this failed message')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(composer).toHaveValue('Restore this failed message')
  await expect(page.getByRole('alert')).toContainText('Chat request failed.')
  expect(consoleErrors.get(page)).toEqual([
    expect.stringContaining('Failed to load resource'),
  ])
  consoleErrors.set(page, [])

  await composer.fill('Clear this successful message')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(composer).toHaveValue('')
  await expect.poll(() => api.chatRequests).toHaveLength(1)
})

test('retries a recoverable completed empty build but not a successful completed build', async ({ page }) => {
  const api = new MockResearchApi()
  api.completeNextBuildEmpty('insufficient_relevant_candidates')
  await api.install(page)
  await page.goto('/')

  const goal = 'Map CRISPR delivery research from verified papers'
  await page.getByRole('textbox', { name: 'Research goal' }).fill(goal)
  await page.getByRole('button', { name: 'Build canvas' }).click()
  await expect(page.getByText('Canvas build run: run.completed')).toBeVisible()
  const emptyRunId = api.lastRunId!
  await expect(page.getByRole('article')).toHaveCount(0)

  const retry = page.getByRole('button', { name: 'Retry' })
  await expect(retry).toBeVisible()

  const composer = page.getByRole('textbox', {
    name: 'Message, @paper, or paper link',
  })
  await composer.fill('Explain why this build stopped')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText(
    'Answer for: Explain why this build stopped',
    { exact: false },
  )).toBeVisible()
  await expect(page.getByRole('button', { name: 'Retry' })).toHaveCount(0)

  await page.reload()
  const restoredRetry = page.getByRole('button', { name: 'Retry' })
  await expect(restoredRetry).toBeVisible()
  api.deferNextRun()
  await restoredRetry.click()
  await expect.poll(() => api.runRetryRequests).toEqual([emptyRunId])
  await expect(page.getByText('Canvas build: queued')).toBeVisible()
  const retryRunId = api.lastRunId!
  api.releaseRun(retryRunId)
  await expect(page.getByRole('article', {
    name: `Recovered evidence for ${goal}`,
  })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Retry' })).toHaveCount(0)

  await selectCanvas(page, '')
  await page.getByRole('textbox', { name: 'Research goal' })
    .fill('Successful nonempty build')
  await page.getByRole('button', { name: 'Build canvas' }).click()
  await expect(page.getByRole('article', {
    name: 'Evidence for Successful nonempty build',
  })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Retry' })).toHaveCount(0)
})

test('restores legacy checkpoint Retry only when accepted papers and reviews exist', async ({ page }) => {
  const api = new MockResearchApi()
  const causal = api.seedCompletedBuild('Causal legacy checkpoint', {
    stopReason: 'academic_search_unavailable',
    acceptedPapers: 4,
    completedReviews: 4,
    relationships: 1,
  })
  const protein = api.seedCompletedBuild('Protein legacy checkpoint', {
    stopReason: 'academic_search_unavailable',
    acceptedPapers: 2,
    completedReviews: 2,
    relationships: 0,
  })
  const empty = api.seedCompletedBuild('Empty legacy checkpoint', {
    stopReason: 'academic_search_unavailable',
    acceptedPapers: 0,
    completedReviews: 0,
    relationships: 0,
  })
  await api.install(page)
  await page.goto('/')

  for (const checkpoint of [causal, protein]) {
    await selectCanvas(page, checkpoint.state.canvas.id)
    await page.reload()
    const retry = page.getByRole('button', { name: 'Retry' })
    await expect(retry).toBeVisible()
    api.deferNextRun()
    await retry.click()
    await expect.poll(() => api.runRetryRequests.filter(
      (runId) => runId === checkpoint.runId,
    )).toHaveLength(1)
    const retryRunId = api.lastRunId!
    api.releaseRun(retryRunId)
    await expect(page.getByRole('button', { name: 'Retry' })).toHaveCount(0)
  }

  await selectCanvas(page, empty.state.canvas.id)
  await page.reload()
  await expect(page.getByRole('button', { name: 'Retry' })).toHaveCount(0)
  expect(api.runRetryRequests).not.toContain(empty.runId)
})

test('keeps concurrent chat and link streams isolated through both terminals', async ({ page }) => {
  const api = new MockResearchApi()
  const canvas = api.seedCanvas('Concurrent runs')
  api.deferNextRun()
  await api.install(page)
  await page.goto('/')

  const composer = page.getByRole('textbox', { name: 'Message, @paper, or paper link' })
  await composer.fill('Concurrent chat question')
  await page.getByRole('button', { name: 'Send' }).click()
  const chatRunId = api.lastRunId!
  await expect(page.getByText('Concurrent chat question (queued)', { exact: false }))
    .toBeVisible()

  api.deferNextRun()
  await composer.fill('https://doi.org/10.1000/concurrent-link')
  await page.getByRole('button', { name: 'Send' }).click()
  const linkRunId = api.lastRunId!
  expect(linkRunId).not.toBe(chatRunId)
  await expect(page.getByText('Paper: queued')).toBeVisible()

  api.releaseRun(linkRunId)
  await expect(page.getByRole('article', { name: 'Resolved pasted paper' })).toBeVisible()
  await expect(page.getByText('{"internal":"link-json"}', { exact: false })).toHaveCount(0)
  await expect(page.getByText('The run event stream was interrupted.')).toHaveCount(0)

  api.releaseRun(chatRunId)
  await expect(page.getByText('Answer for: Concurrent chat question', { exact: false }))
    .toBeVisible()
  await expect(page.getByText('Concurrent chat question (queued)', { exact: false }))
    .toHaveCount(0)
  expect(api.messages.get(canvas.canvas.id)?.at(0)?.status).toBe('completed')
  await expect(page.getByText('The run event stream was interrupted.')).toHaveCount(0)
})

test('routes the shared paper-reference contract through composer and canvas paste', async ({ page }) => {
  const api = new MockResearchApi()
  api.seedCanvas('Paper reference canvas')
  await api.install(page)
  await page.goto('/')
  await expect(page.locator('main[data-ready="true"]')).toBeVisible()

  const accepted = [
    'https://publisher.example/paper.pdf',
    '10.1000/bare-doi',
    'doi:10.1000/prefixed-doi',
    'arXiv:2005.11401v2',
    '2005.11401v3',
    'hep-th/9901001',
    'math.GT/0309136v1',
  ]
  const rejected = [
    'http://publisher.example/paper.pdf',
    'ftp://publisher.example/paper.pdf',
    'not a paper',
    'https://user:secret@publisher.example/paper.pdf',
  ]
  const composer = page.getByRole('textbox', { name: 'Message, @paper, or paper link' })

  for (const reference of accepted) {
    await composer.fill(reference)
    await page.getByRole('button', { name: 'Send' }).click()
    await expect.poll(() => api.paperLinkRequests.at(-1)?.url).toBe(reference)
  }
  const composerAcceptedCount = api.paperLinkRequests.length
  for (const reference of rejected) {
    await composer.fill(reference)
    await page.getByRole('button', { name: 'Send' }).click()
    await expect.poll(() => api.chatRequests.at(-1)?.content).toBe(reference)
    expect(api.paperLinkRequests).toHaveLength(composerAcceptedCount)
  }

  await page.mouse.move(700, 350)
  for (const reference of accepted) {
    await dispatchCanvasPaste(page, reference)
    await expect.poll(() => api.paperLinkRequests.at(-1)?.url).toBe(reference)
  }
  const pasteAcceptedCount = api.paperLinkRequests.length
  for (const reference of rejected) {
    await dispatchCanvasPaste(page, reference)
    await page.waitForTimeout(25)
    expect(api.paperLinkRequests).toHaveLength(pasteAcceptedCount)
  }
})

test('syncs resolved metadata without replacing an actively edited field', async ({ page }) => {
  const api = new MockResearchApi()
  const canvas = api.seedCanvas('Metadata canvas')
  api.deferNextRun()
  await api.install(page)
  await page.goto('/')

  const composer = page.getByRole('textbox', { name: 'Message, @paper, or paper link' })
  await composer.fill('https://doi.org/10.1000/metadata-sync')
  await page.getByRole('button', { name: 'Send' }).click()
  const placeholder = canvas.papers[0]
  const form = page.getByRole('form', { name: `Edit metadata for ${placeholder.title}` })
  await expect(form.getByRole('textbox', { name: 'Title' })).toHaveValue(placeholder.title)
  await form.getByRole('textbox', { name: 'Summary' }).fill('Keep this user draft')

  api.releaseRun()
  await expect(page.getByRole('article', { name: 'Resolved pasted paper' })).toBeVisible()
  const resolvedForm = page.getByRole('form', { name: 'Edit metadata for Resolved pasted paper' })
  await expect(resolvedForm.getByRole('textbox', { name: 'Title' }))
    .toHaveValue('Resolved pasted paper')
  await expect(resolvedForm.getByRole('textbox', { name: 'Summary' }))
    .toHaveValue('Keep this user draft')
  await expect(page.locator(`.react-flow__node[data-id="${placeholder.id}"]`))
    .toHaveAttribute('aria-label', 'Paper: Resolved pasted paper')

  await resolvedForm.getByRole('button', { name: 'Save paper' }).click()
  await expect.poll(() => api.paperUpdates).toEqual([{
    canvasId: canvas.canvas.id,
    paperId: placeholder.id,
    input: { summary: 'Keep this user draft' },
  }])
  expect(api.paper(canvas.canvas.id, placeholder.id)?.title).toBe('Resolved pasted paper')
})

test('deletes a bottom graph paper by ordinary pointer and restores it with Undo', async ({ page }) => {
  const api = new MockResearchApi()
  const top = { ...mockPaper('paper-top', 'Top paper', 2018, 0, 0), pinned: true }
  const bottom = { ...mockPaper('paper-bottom', 'Bottom minimap paper', 2020, 900, 640), pinned: true }
  api.seedCanvas('Pointer controls', { papers: [top, bottom] })
  await api.install(page)
  await page.goto('/')

  await page.getByRole('article', { name: bottom.title }).click()
  const inspector = page.getByRole('complementary', { name: `Selected paper: ${bottom.title}` })
  await inspector.getByRole('button', { name: 'Delete paper' }).click()
  await expect(page.getByRole('article', { name: bottom.title })).toHaveCount(0)
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByRole('article', { name: bottom.title })).toBeVisible()
})

test('refreshes a cancelled link node and retries the same paper ID by pointer', async ({ page }) => {
  const api = new MockResearchApi()
  const canvas = api.seedCanvas('Cancel and retry')
  api.deferNextRun()
  await api.install(page)
  await page.goto('/')

  const composer = page.getByRole('textbox', { name: 'Message, @paper, or paper link' })
  await composer.fill('https://arxiv.org/abs/1234.9876')
  await page.getByRole('button', { name: 'Send' }).click()
  const paperId = canvas.papers[0].id
  await page.getByRole('button', { name: 'Stop' }).click()

  await expect(page.getByText('Paper: failed')).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('Cancelled by user.')
  const inspector = page.getByRole('complementary', {
    name: `Selected paper: ${canvas.papers[0].title}`,
  })
  await expect(inspector.getByRole('button', { name: 'Retry' })).toBeVisible()
  await expect(inspector.getByRole('button', { name: 'Delete paper' })).toBeVisible()
  await inspector.getByRole('button', { name: 'Retry' }).click()

  await expect.poll(() => api.paperRetryRequests).toEqual([{
    canvasId: canvas.canvas.id,
    paperId,
  }])
  expect(api.paper(canvas.canvas.id, paperId)?.id).toBe(paperId)
  await expect(page.getByText('Paper: failed')).toHaveCount(0)
})

for (const viewport of [
  { width: 1280, height: 720 },
  { width: 1200, height: 800 },
  { width: 1024, height: 768 },
]) {
  test(`keeps selected paper editor controls above chat at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    const api = new MockResearchApi()
    const paper = mockPaper('paper-editor', 'ColBERT', 2020)
    const canvas = api.seedCanvas('Pointer editor canvas', {
      papers: [paper],
      messages: [mockMessage(
        'message-wide',
        'canvas-1',
        'assistant',
        'A deliberately long conversation entry keeps the fixed chat layer wide enough to reproduce transparent hit interception over the selected paper editor controls.',
      )],
    })
    await api.install(page)
    await page.setViewportSize(viewport)
    await page.goto('/')

    await page.getByRole('article', { name: paper.title }).click()
    const removeContext = page.getByRole('button', { name: 'Remove' })
    await expectPointerTarget(removeContext)
    await removeContext.click()
    await expect(page.getByRole('complementary', {
      name: `Selected paper: ${paper.title}`,
    })).toHaveCount(0)
    await page.getByRole('article', { name: paper.title }).click()

    const metadataForm = page.getByRole('form', { name: `Edit metadata for ${paper.title}` })
    const updatedTitle = `ColBERT edited ${viewport.width}`
    await metadataForm.getByRole('textbox', { name: 'Title' }).fill(updatedTitle)
    const savePaper = metadataForm.getByRole('button', { name: 'Save paper' })
    await expectPointerTarget(savePaper)
    await savePaper.click()
    await expect.poll(() => api.paperUpdates.at(-1)).toEqual({
      canvasId: canvas.canvas.id,
      paperId: paper.id,
      input: { title: updatedTitle },
    })

    const reviewForm = page.getByRole('form', { name: `Edit review for ${updatedTitle}` })
    const updatedCoreIdea = `Pointer-saved review ${viewport.width}`
    await reviewForm.locator('textarea[name="coreIdea"]').fill(updatedCoreIdea)
    const saveReview = reviewForm.getByRole('button', { name: 'Save review' })
    await expectPointerTarget(saveReview)
    await saveReview.click()
    await expect.poll(() => api.paper(canvas.canvas.id, paper.id)?.review?.coreIdea)
      .toBe(updatedCoreIdea)

    const inspector = page.getByRole('complementary', {
      name: `Selected paper: ${updatedTitle}`,
    })
    const deletePaper = inspector.getByRole('button', { name: 'Delete paper' })
    await expectPointerTarget(deletePaper)
    await deletePaper.click()
    await expect(page.getByRole('article', { name: updatedTitle })).toHaveCount(0)
    await page.getByRole('button', { name: 'Undo' }).click()
    await expect(page.getByRole('article', { name: updatedTitle })).toBeVisible()
  })
}

test('creates three canvases, switches without leaks, and restores the last selection', async ({ page }) => {
  const api = new MockResearchApi()
  await api.install(page)
  await page.goto('/')

  const goals = ['Alpha evidence', 'Beta evidence', 'Gamma evidence']
  const canvasIds: string[] = []
  for (const [index, goal] of goals.entries()) {
    await page.getByRole('textbox', { name: 'Research goal' }).fill(goal)
    await page.getByRole('button', { name: 'Build canvas' }).click()
    await expect(page.getByRole('article', { name: `Evidence for ${goal}` })).toBeVisible()
    canvasIds.push([...api.canvases.keys()].at(-1)!)

    const message = `${goal} queued note`
    await page.getByRole('textbox', { name: 'Message, @paper, or paper link' }).fill(message)
    await page.getByRole('button', { name: 'Send' }).click()
    await expect(page.getByText(`${message} (queued)`, { exact: false })).toBeVisible()

    if (index < goals.length - 1) {
      await selectCanvas(page, '')
      await expect(page.getByRole('textbox', { name: 'Research goal' })).toBeVisible()
    }
  }

  for (const [index, canvasId] of canvasIds.entries()) {
    await selectCanvas(page, canvasId)
    await expect(page.getByRole('article', { name: `Evidence for ${goals[index]}` })).toBeVisible()
    await expect(page.getByText(`${goals[index]} queued note (queued)`, { exact: false })).toBeVisible()
    for (const other of goals.filter((_, otherIndex) => otherIndex !== index)) {
      await expect(page.getByText(`${other} queued note (queued)`, { exact: false })).toHaveCount(0)
    }
  }

  await page.reload()
  await expect(page.getByRole('article', { name: 'Evidence for Gamma evidence' })).toBeVisible()
  await expect(page.getByText('Gamma evidence queued note (queued)', { exact: false })).toBeVisible()
})

test('refresh replaces streamed chat with durable messages without duplicates', async ({ page }) => {
  const api = new MockResearchApi()
  const canvas = api.seedCanvas('Chat canvas', {
    messages: [
      mockMessage('message-seed-1', 'canvas-1', 'user', 'Earlier question'),
      mockMessage('message-seed-2', 'canvas-1', 'assistant', 'Earlier answer'),
    ],
  })
  await api.install(page)
  await page.goto('/')

  await expect(conversationItems(page)).toHaveCount(2)
  const question = 'Explain this result'
  await page.getByRole('textbox', { name: 'Message, @paper, or paper link' }).fill(question)
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText(`Answer for: ${question}`, { exact: false })).toBeVisible()
  await expect(conversationItems(page)).toHaveCount(4)
  await expect(conversationItems(page).filter({ hasText: `You: ${question}` })).toHaveCount(1)

  await page.reload()
  await expect(conversationItems(page)).toHaveCount(4)
  await expect(page.getByText(`Answer for: ${question}`, { exact: false })).toHaveCount(1)
  expect(api.messages.get(canvas.canvas.id)).toHaveLength(4)
})

test('routes selected and mentioned papers while accepting queued messages', async ({ page }) => {
  const api = new MockResearchApi()
  const first = mockPaper('paper-first', 'First Paper', 2018)
  const second = mockPaper('paper-second', 'Second Paper', 2020)
  const canvas = api.seedCanvas('Routing canvas', { papers: [first, second] })
  await api.install(page)
  await page.goto('/')

  await page.getByRole('article', { name: first.title }).click()
  const input = page.getByRole('textbox', { name: 'Message, @paper, or paper link' })
  await input.fill('queued comparison @Second')
  await page.getByRole('button', { name: second.title }).click()
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText('(queued)', { exact: false })).toBeVisible()
  await expect.poll(() => api.chatRequests.at(-1)).toEqual({
    canvasId: canvas.canvas.id,
    content: `queued comparison @${second.title}`,
    paper_ids: [first.id, second.id],
  })

  await page.getByRole('button', { name: 'Remove' }).click()
  await input.fill('queued review @First')
  await page.getByRole('button', { name: first.title }).click()
  await page.getByRole('button', { name: 'Send' }).click()
  await expect.poll(() => api.chatRequests.at(-1)).toEqual({
    canvasId: canvas.canvas.id,
    content: `queued review @${first.title}`,
    paper_ids: [first.id],
  })
  expect(api.messages.get(canvas.canvas.id)?.at(-1)?.agent_name).toBe('reviewer')
})

test('pastes on the canvas and progresses from queued paper to review and relationship', async ({ page }) => {
  const api = new MockResearchApi()
  const base = mockPaper('paper-base', 'Baseline Paper', 2018)
  const canvas = api.seedCanvas('Paste canvas', { papers: [base] })
  api.deferNextRun()
  await api.install(page)
  await page.goto('/')
  await expect(page.getByRole('article', { name: base.title })).toBeVisible()

  await page.mouse.move(700, 350)
  await page.evaluate((url) => {
    const clipboard = new DataTransfer()
    clipboard.setData('text/plain', url)
    document.dispatchEvent(new ClipboardEvent('paste', { clipboardData: clipboard, bubbles: true }))
  }, 'https://arxiv.org/abs/1234.5678')

  await expect(page.getByText('Paper: queued')).toBeVisible()
  await expect.poll(() => api.paperLinkRequests.length).toBe(1)
  const submitted = api.paperLinkRequests[0]
  expect(submitted.canvasId).toBe(canvas.canvas.id)
  expect(Number.isFinite(submitted.x)).toBe(true)
  expect(Number.isFinite(submitted.y)).toBe(true)

  api.releaseRun()
  await expect(page.getByRole('article', { name: 'Resolved pasted paper' })).toBeVisible()
  await expect(page.getByRole('complementary', {
    name: 'Resolved pasted paper',
    exact: true,
  })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Relationship: Extends the baseline' })).toBeVisible()
  expect(api.canvases.get(canvas.canvas.id)?.relationships).toHaveLength(1)
})

test('edits paper, review, and relationship then deletes and undoes each resource', async ({ page }) => {
  const api = new MockResearchApi()
  const first = mockPaper('paper-edit', 'Editable Paper', 2018)
  const second = mockPaper('paper-neighbor', 'Neighbor Paper', 2020)
  const relationship = mockRelationship('relationship-edit', first.id, second.id)
  const canvas = api.seedCanvas('Editing canvas', {
    papers: [first, second],
    relationships: [relationship],
  })
  await api.install(page)
  await page.goto('/')

  await page.getByRole('article', { name: first.title }).click()
  const metadataForm = page.getByRole('form', { name: `Edit metadata for ${first.title}` })
  await metadataForm.getByRole('textbox', { name: 'Title' }).fill('Edited Paper')
  await metadataForm.getByRole('button', { name: 'Save paper' }).click()
  await expect(page.getByRole('article', { name: 'Edited Paper' })).toBeVisible()

  const reviewForm = page.getByRole('form', { name: 'Edit review for Edited Paper' })
  await reviewForm.locator('textarea[name="coreIdea"]').fill('User-protected core idea')
  await reviewForm.getByRole('button', { name: 'Save review' }).click()
  await expect.poll(() => api.paper(canvas.canvas.id, first.id)?.review?.coreIdea)
    .toBe('User-protected core idea')

  await clickCanvasPane(page)
  await page.getByRole('button', { name: `Relationship: ${relationship.label}` }).click()
  const relationshipForm = page.getByRole('form', { name: `Edit relationship: ${relationship.label}` })
  await relationshipForm.getByRole('textbox', { name: 'Label' }).fill('Edited relationship')
  await relationshipForm.getByRole('button', { name: 'Save relationship' }).click()
  await expect(page.getByRole('button', { name: 'Relationship: Edited relationship' })).toBeVisible()

  await page.getByRole('button', { name: 'Relationship: Edited relationship' }).click()
  await page.getByRole('button', { name: 'Delete relationship' }).click()
  await expect(page.getByRole('button', { name: 'Relationship: Edited relationship' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByRole('button', { name: 'Relationship: Edited relationship' })).toBeVisible()

  await page.getByRole('article', { name: 'Edited Paper' }).click()
  await page.getByRole('button', { name: 'Delete paper' }).click()
  await expect(page.getByRole('article', { name: 'Edited Paper' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByRole('article', { name: 'Edited Paper' })).toBeVisible()
})

test('keeps a dragged pin through insertion and reload, then Relayout clears it', async ({ page }) => {
  const api = new MockResearchApi()
  const pinnedCandidate = mockPaper('paper-drag', 'Draggable Paper', 2017, 40, 60)
  const neighbor = mockPaper('paper-later', 'Later Paper', 2019, 520, 60)
  const canvas = api.seedCanvas('Layout canvas', {
    papers: [pinnedCandidate, neighbor],
    relationships: [mockRelationship('relationship-layout', pinnedCandidate.id, neighbor.id)],
  })
  await api.install(page)
  await page.goto('/')

  const node = page.locator(`.react-flow__node[data-id="${pinnedCandidate.id}"]`)
  await expect(node).toBeVisible()
  const box = await node.boundingBox()
  expect(box).not.toBeNull()
  await page.mouse.move(box!.x + box!.width - 8, box!.y + 8)
  await page.mouse.down()
  await page.mouse.move(box!.x + box!.width + 112, box!.y + 78, { steps: 8 })
  await page.mouse.up()
  await expect.poll(() => api.paper(canvas.canvas.id, pinnedCandidate.id)?.pinned).toBe(true)
  const dragged = { ...api.paper(canvas.canvas.id, pinnedCandidate.id)! }

  api.deferNextRun()
  await page.getByRole('textbox', { name: 'Message, @paper, or paper link' })
    .fill('https://doi.org/10.1000/layout-paper')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText('Paper: queued')).toBeVisible()
  expect(api.paper(canvas.canvas.id, pinnedCandidate.id)).toMatchObject({
    x: dragged.x,
    y: dragged.y,
    pinned: true,
  })
  api.releaseRun()
  await expect(page.getByRole('article', { name: 'Resolved pasted paper' })).toBeVisible()

  await page.reload()
  await expect(page.getByRole('article', { name: pinnedCandidate.title })).toBeVisible()
  expect(api.paper(canvas.canvas.id, pinnedCandidate.id)).toMatchObject({
    x: dragged.x,
    y: dragged.y,
    pinned: true,
  })

  await page.getByRole('button', { name: 'Relayout' }).click()
  await expect.poll(() => api.layoutResetCount).toBe(1)
  await expect.poll(() => api.paper(canvas.canvas.id, pinnedCandidate.id)?.pinned).toBe(false)
})

test('falls back safely when native WebMCP is unsupported', async ({ page }) => {
  const api = new MockResearchApi()
  api.seedCanvas('Fallback canvas')
  await api.install(page)
  await page.goto('/')

  expect(await page.evaluate(
    () => (document as Document & { modelContext?: unknown }).modelContext,
  )).toBeUndefined()
  await expect(page.getByRole('region', { name: 'Research chat' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Canvas switcher' })).toBeVisible()
})

test('renders and persists a real null-date snapshot in the final non-overlapping band', async ({ page }) => {
  const api = new MockResearchApi()
  const oldest = mockPaper('paper-oldest', 'Oldest dated paper', 2018)
  const newest = mockPaper('paper-newest', 'Newest dated paper', 2022)
  const unknown = mockPaper('paper-unknown', 'Undated paper', null)
  const canvas = api.seedCanvas('Unknown date canvas', {
    papers: [unknown, newest, oldest],
  })
  await api.install(page)
  await page.goto('/')

  const unknownNode = page.getByRole('article', { name: unknown.title })
  await expect(unknownNode.getByText('Date unknown')).toBeVisible()
  await expect.poll(() => api.layoutWriteCount).toBeGreaterThan(0)
  await expect.poll(() => api.paper(canvas.canvas.id, unknown.id)?.x).toBeGreaterThan(
    api.paper(canvas.canvas.id, newest.id)!.x,
  )
  await expectNonOverlapping(page, [oldest.id, newest.id, unknown.id])
  const persisted = { ...api.paper(canvas.canvas.id, unknown.id)! }

  await page.reload()
  await expect(page.getByRole('article', { name: unknown.title }).getByText('Date unknown'))
    .toBeVisible()
  expect(api.paper(canvas.canvas.id, unknown.id)).toMatchObject({
    year: null,
    month: null,
    x: persisted.x,
    y: persisted.y,
  })
  await expectNonOverlapping(page, [oldest.id, newest.id, unknown.id])
})

async function selectCanvas(page: Page, canvasId: string) {
  const details = page.locator('details')
  if (!(await details.getAttribute('open'))) {
    await page.getByText('Canvases', { exact: true }).click()
  }
  await page.getByLabel('Open canvas').selectOption(canvasId)
  await page.waitForFunction((selectedCanvasId) => (
    window.localStorage.getItem('research-map:last-canvas-id') ===
      (selectedCanvasId || null)
  ), canvasId)
}

async function dispatchCanvasPaste(page: Page, reference: string) {
  await page.evaluate((value) => {
    const clipboard = new DataTransfer()
    clipboard.setData('text/plain', value)
    document.dispatchEvent(new ClipboardEvent('paste', {
      clipboardData: clipboard,
      bubbles: true,
    }))
  }, reference)
}

function conversationItems(page: Page) {
  return page.getByRole('list', { name: 'Conversation' }).getByRole('listitem')
}

async function clickCanvasPane(page: Page) {
  const point = await page.evaluate(() => {
    for (let y = 40; y < window.innerHeight - 40; y += 40) {
      for (let x = 40; x < window.innerWidth - 40; x += 40) {
        if (document.elementFromPoint(x, y)?.classList.contains('react-flow__pane')) {
          return { x, y }
        }
      }
    }
    return null
  })
  expect(point).not.toBeNull()
  await page.mouse.click(point!.x, point!.y)
}

async function expectNonOverlapping(page: Page, paperIds: string[]) {
  const boxes = await Promise.all(
    paperIds.map((paperId) =>
      page.locator(`.react-flow__node[data-id="${paperId}"]`).boundingBox(),
    ),
  )
  expect(boxes.every(Boolean)).toBe(true)
  for (let left = 0; left < boxes.length; left += 1) {
    for (let right = left + 1; right < boxes.length; right += 1) {
      const a = boxes[left]!
      const b = boxes[right]!
      const separated =
        a.x + a.width <= b.x || b.x + b.width <= a.x ||
        a.y + a.height <= b.y || b.y + b.height <= a.y
      expect(separated).toBe(true)
    }
  }
}

async function expectPointerTarget(target: Locator) {
  await target.scrollIntoViewIfNeeded()
  const hit = await target.evaluate((element) => {
    const bounds = element.getBoundingClientRect()
    const pointTarget = document.elementFromPoint(
      bounds.left + bounds.width / 2,
      bounds.top + bounds.height / 2,
    )
    return {
      matches: pointTarget === element || element.contains(pointTarget),
      target: pointTarget instanceof HTMLElement
        ? `${pointTarget.tagName}.${pointTarget.className}`
        : String(pointTarget),
    }
  })
  expect(hit.matches, `pointer hit ${hit.target}`).toBe(true)
}
