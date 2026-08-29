import { useId, useState, type FormEvent } from 'react'

import '@/components/CanvasLauncher.css'
import type { CanvasLauncherProps } from '@/types'
import { isPaperLink } from '@/utils'

export function CanvasLauncher({
  canvas,
  papers,
  messages,
  selectedCanvasId,
  selectedPaper,
  activeAgent,
  isBusy,
  error,
  runStatus,
  runStatusLabel,
  onCreate,
  onAddPaperLink,
  onSendMessage,
  onClearPaper,
  onStop,
  onRetry,
  onUndo,
}: CanvasLauncherProps) {
  const [input, setInput] = useState('')
  const [validationError, setValidationError] = useState<string>()
  const mentionsId = useId()
  const mentionQuery = input.match(/(?:^|\s)@([^@]*)$/)?.[1].toLowerCase()
  const suggestions = mentionQuery === undefined
    ? []
    : papers
        .filter((paper) => paper.title.toLowerCase().includes(mentionQuery))
        .slice(0, 5)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const value = input.trim()

    if (!value) {
      setValidationError('Enter a research goal or paper link.')
      return
    }

    setValidationError(undefined)
    setInput('')
    try {
      if (selectedCanvasId && isPaperLink(value)) {
        await onAddPaperLink(value)
        return
      }

      if (selectedCanvasId) {
        const mentionedPaperIds = papers
          .filter((paper) => value.includes(`@${paper.title}`))
          .map((paper) => paper.id)
        const paperIds = Array.from(
          new Set([
            ...(selectedPaper ? [selectedPaper.id] : []),
            ...mentionedPaperIds,
          ]),
        )
        await onSendMessage(value, paperIds)
        return
      }

      await onCreate({ name: createCanvasName(value), research_goal: value })
    } catch {
      setInput((current) => current || value)
    }
  }

  return (
    <section aria-label="Research chat">
      <header>
        <strong>{canvas?.name ?? 'New canvas'}</strong>
        {activeAgent ? <span> · Agent: {activeAgent}</span> : null}
      </header>

      {messages.length > 0 ? (
        <ol
          className="canvas-launcher__transcript"
          aria-label="Conversation"
          aria-live="polite"
        >
          {messages.map((message) => (
            <li key={message.id}>
              <strong>
                {message.role === 'user' ? 'You' : message.agent_name}
              </strong>: {message.content}
              {message.status === 'queued' ? ' (queued)' : ''}
            </li>
          ))}
        </ol>
      ) : null}

      {selectedPaper ? (
        <p>
          Paper context: {selectedPaper.title}{' '}
          <button type="button" onClick={onClearPaper}>Remove</button>
        </p>
      ) : null}

      <form onSubmit={handleSubmit}>
        <fieldset disabled={isBusy}>
          <legend>
            {selectedCanvasId ? 'Message agents or add a paper' : 'Build a research canvas'}
          </legend>
          <label>
            {selectedCanvasId ? 'Message, @paper, or paper link' : 'Research goal'}
            <textarea
              name="researchInput"
              value={input}
              placeholder={
                selectedCanvasId
                  ? 'Ask the agent, mention @paper, or paste a paper link'
                  : 'Describe the research map to build'
              }
              aria-autocomplete="list"
              aria-controls={mentionsId}
              aria-expanded={suggestions.length > 0}
              onChange={(event) => {
                setInput(event.target.value)
                setValidationError(undefined)
              }}
              required
            />
          </label>
          <button type="submit">
            {selectedCanvasId ? 'Send' : 'Build canvas'}
          </button>
        </fieldset>
      </form>

      {suggestions.length > 0 ? (
        <ul id={mentionsId} aria-label="Paper mentions">
          {suggestions.map((paper) => (
            <li key={paper.id}>
              <button
                type="button"
                onClick={() => {
                  setInput((current) =>
                    current.replace(/@[^@]*$/, `@${paper.title} `),
                  )
                }}
              >
                {paper.title}
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {isBusy ? <p role="status">Submitting…</p> : null}
      {runStatus ? (
        <p role="status">{runStatusLabel ?? 'Run'}: {runStatus}</p>
      ) : null}
      {validationError || error ? (
        <p role="alert">{validationError ?? error}</p>
      ) : null}
      {onStop ? <button type="button" onClick={onStop}>Stop</button> : null}
      {onRetry ? <button type="button" onClick={onRetry}>Retry</button> : null}
      {onUndo ? <button type="button" onClick={onUndo}>Undo</button> : null}
    </section>
  )
}

function createCanvasName(researchGoal: string) {
  return researchGoal.slice(0, 80)
}
