import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { subscribeToCanvasEvents } from '@/api/client'

class FakeEventSource {
  static instances: FakeEventSource[] = []

  readonly url: string
  closed = false
  onopen: ((event: Event) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  private listeners = new Map<string, Set<EventListener>>()

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListener) {
    const listeners = this.listeners.get(type) ?? new Set<EventListener>()
    listeners.add(listener)
    this.listeners.set(type, listeners)
  }

  close() {
    this.closed = true
  }

  emitOpen() {
    this.onopen?.(new Event('open'))
  }

  emitError() {
    this.onerror?.(new Event('error'))
  }

  emitMessage(data: string) {
    const event = { data } as MessageEvent<string>
    this.listeners.get('canvas.changed')?.forEach((listener) => {
      listener(event)
    })
  }
}

describe('subscribeToCanvasEvents', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.useFakeTimers()
    vi.spyOn(Math, 'random').mockReturnValue(0)
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('reconnects after a disconnect and reconciles again on open', () => {
    const onOpen = vi.fn()
    const onEvent = vi.fn()
    const unsubscribe = subscribeToCanvasEvents({ onOpen, onEvent })
    const first = FakeEventSource.instances[0]!

    first.emitOpen()
    expect(onOpen).toHaveBeenCalledTimes(1)
    first.emitError()
    expect(first.closed).toBe(true)

    vi.advanceTimersByTime(499)
    expect(FakeEventSource.instances).toHaveLength(1)
    vi.advanceTimersByTime(1)
    const second = FakeEventSource.instances[1]!
    second.emitOpen()

    expect(onOpen).toHaveBeenCalledTimes(2)
    second.emitMessage(JSON.stringify({
      canvas_id: 'canvas-1',
      change_type: 'paper.added',
      updated_at: '2026-08-29T00:00:00Z',
    }))
    expect(onEvent).toHaveBeenCalledWith({
      canvas_id: 'canvas-1',
      change_type: 'paper.added',
      updated_at: '2026-08-29T00:00:00Z',
    })

    unsubscribe()
  })

  it('deduplicates repeated errors from the same stale source', () => {
    const unsubscribe = subscribeToCanvasEvents({ onEvent: vi.fn() })
    const first = FakeEventSource.instances[0]!

    first.emitError()
    first.emitError()
    vi.advanceTimersByTime(500)

    expect(FakeEventSource.instances).toHaveLength(2)
    unsubscribe()
  })

  it('cancels the pending reconnect and ignores stale updates on cleanup', () => {
    const onOpen = vi.fn()
    const onEvent = vi.fn()
    const unsubscribe = subscribeToCanvasEvents({ onOpen, onEvent })
    const first = FakeEventSource.instances[0]!

    first.emitError()
    unsubscribe()
    vi.advanceTimersByTime(10_000)
    first.emitOpen()
    first.emitMessage(JSON.stringify({
      canvas_id: 'canvas-1',
      change_type: 'canvas.updated',
      updated_at: '2026-08-29T00:00:00Z',
    }))

    expect(FakeEventSource.instances).toHaveLength(1)
    expect(onOpen).not.toHaveBeenCalled()
    expect(onEvent).not.toHaveBeenCalled()
  })

  it('recovers after several failures with bounded exponential delays', () => {
    const onOpen = vi.fn()
    const unsubscribe = subscribeToCanvasEvents({ onOpen, onEvent: vi.fn() })

    FakeEventSource.instances[0]!.emitError()
    vi.advanceTimersByTime(500)
    FakeEventSource.instances[1]!.emitError()
    vi.advanceTimersByTime(999)
    expect(FakeEventSource.instances).toHaveLength(2)
    vi.advanceTimersByTime(1)
    FakeEventSource.instances[2]!.emitError()
    vi.advanceTimersByTime(2_000)
    const recovered = FakeEventSource.instances[3]!
    recovered.emitOpen()

    expect(onOpen).toHaveBeenCalledTimes(1)

    recovered.emitError()
    vi.advanceTimersByTime(500)
    expect(FakeEventSource.instances).toHaveLength(5)
    unsubscribe()
  })

  it('ignores malformed frames without interrupting later events', () => {
    const onEvent = vi.fn()
    const unsubscribe = subscribeToCanvasEvents({ onEvent })
    const source = FakeEventSource.instances[0]!

    source.emitMessage('{not json')
    source.emitMessage(JSON.stringify({ canvas_id: 'missing-fields' }))
    source.emitMessage(JSON.stringify({
      canvas_id: 'canvas-2',
      change_type: 'review.completed',
      updated_at: '2026-08-29T00:00:00Z',
    }))

    expect(onEvent).toHaveBeenCalledTimes(1)
    unsubscribe()
  })
})
