import { expect, test } from '@playwright/test'

type StoryIndex = {
  entries: Record<string, {
    id: string
    type: string
    tags?: string[]
  }>
}

type StoryResult = {
  storyId: string
  status: string
}

test('executes every Storybook play function successfully', async ({ page, request }) => {
  const response = await request.get('/index.json')
  expect(response.ok()).toBe(true)
  const index = await response.json() as StoryIndex
  const storyIds = Object.values(index.entries)
    .filter((entry) => entry.type === 'story' && entry.tags?.includes('play-fn'))
    .map((entry) => entry.id)
    .sort()
  expect(storyIds.length).toBeGreaterThan(0)

  await page.addInitScript(() => {
    const target = window as Window & {
      __researchMapStoryEvents?: StoryResult[]
      __researchMapStoryHooked?: boolean
      __STORYBOOK_PREVIEW__?: {
        channel?: { on: (event: string, listener: (result: StoryResult) => void) => void }
      }
    }
    target.__researchMapStoryEvents = []
    const timer = window.setInterval(() => {
      const channel = target.__STORYBOOK_PREVIEW__?.channel
      if (!channel || target.__researchMapStoryHooked) return
      target.__researchMapStoryHooked = true
      window.clearInterval(timer)
      channel.on('storyFinished', (result) => {
        target.__researchMapStoryEvents?.push(result)
      })
    }, 0)
  })

  for (const storyId of storyIds) {
    const browserErrors: string[] = []
    const onPageError = (error: Error) => browserErrors.push(error.message)
    const onConsole = (message: { type: () => string; text: () => string }) => {
      if (message.type() === 'error') browserErrors.push(message.text())
    }
    page.on('pageerror', onPageError)
    page.on('console', onConsole)

    await page.goto(`/iframe.html?id=${storyId}&viewMode=story`)
    const result = await page.waitForFunction((expectedStoryId) => {
      const target = window as Window & { __researchMapStoryEvents?: StoryResult[] }
      return target.__researchMapStoryEvents?.find((event) => event.storyId === expectedStoryId)
    }, storyId).then((handle) => handle.jsonValue() as Promise<StoryResult>)

    expect(result.status, `${storyId} play function`).toBe('success')
    expect(browserErrors, `${storyId} browser errors`).toEqual([])
    page.off('pageerror', onPageError)
    page.off('console', onConsole)
  }
})
