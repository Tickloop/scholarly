import { Handle, Position, type NodeProps } from '@xyflow/react'
import { useId, useState, type FormEvent } from 'react'

import '@/components/PaperNode.css'
import { constants } from '@/constants'
import { PaperDetail } from '@/components/PaperDetail'
import { useCanvasActions } from '@/context/CanvasContext'
import type { Paper, PaperCanvasNode, PaperUpdateInput } from '@/types'

const monthFormatter = new Intl.DateTimeFormat('en', {
  month: 'short',
  timeZone: 'UTC',
})

export function PaperNode({ data, selected }: NodeProps<PaperCanvasNode>) {
  const { paper } = data
  const titleId = useId()
  const publicationDate = paper.year === null
    ? undefined
    : paper.month === null
      ? String(paper.year)
      : `${paper.year}-${String(paper.month).padStart(2, '0')}`
  const publicationLabel = paper.year === null
    ? 'Date unknown'
    : paper.month === null
      ? String(paper.year)
      : `${monthFormatter.format(new Date(Date.UTC(paper.year, paper.month - 1)))} ${paper.year}`

  return (
    <article
      aria-labelledby={titleId}
      className="paper-node"
      data-selected={selected}
      tabIndex={0}
      style={{ width: constants.PAPER_NODE_DEFAULT_WIDTH }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          data.onActivate?.()
        }
      }}
    >
      <Handle type="target" position={Position.Left} />
      <h2 id={titleId}>{paper.title}</h2>
      {publicationDate ? (
        <time dateTime={publicationDate}>{publicationLabel}</time>
      ) : (
        <span>Date unknown</span>
      )}
      <p>{paper.authors.join(', ')}</p>
      <p>{paper.summary}</p>
      {paper.processing_status && paper.processing_status !== 'reviewed' ? (
        <p role="status">Paper: {paper.processing_status}</p>
      ) : null}
      {paper.error ? <p role="alert">{paper.error}</p> : null}
      <a className="nodrag" href={paper.link} target="_blank" rel="noreferrer">
        Open paper
      </a>
      <Handle type="source" position={Position.Right} />
    </article>
  )
}

export function PaperInspector({ paper }: { paper: Paper }) {
  const actions = useCanvasActions()
  if (!actions) return null

  return (
    <aside
      aria-label={`Selected paper: ${paper.title}`}
      className="paper-node__inspector nodrag nopan"
    >
      {paper.review ? <PaperDetail paper={paper} review={paper.review} /> : null}
      <PaperControls
        paper={paper}
        onEdit={(input) => actions.editPaper?.(paper.id, input)}
        onReviewEdit={(event) => {
          event.preventDefault()
          const form = new FormData(event.currentTarget)
          const sections = Object.fromEntries(
            Object.keys(paper.review ?? {}).map((key) => [
              key,
              String(form.get(key)),
            ]),
          )
          actions.editReview?.(paper.id, { sections })
        }}
        onRetry={() => actions.retryPaperProcessing?.(paper.id)}
        onRegenerate={() => actions.regeneratePaperReview?.(paper.id)}
        onDelete={() => actions.removePaper?.(paper.id)}
      />
    </aside>
  )
}

type DraftKey = 'title' | 'authors' | 'year' | 'month' | 'summary' | 'link'
type Draft = Record<DraftKey, string>

type PaperControlsProps = {
  paper: Paper
  onEdit: (input: PaperUpdateInput) => void
  onReviewEdit: (event: FormEvent<HTMLFormElement>) => void
  onRetry: () => void
  onRegenerate: () => void
  onDelete: () => void
}

function PaperControls({
  paper,
  onEdit,
  onReviewEdit,
  onRetry,
  onRegenerate,
  onDelete,
}: PaperControlsProps) {
  const [edits, setEdits] = useState<Partial<Draft>>({})
  const values = { ...paperDraft(paper), ...edits }

  function updateDraft(key: DraftKey, value: string) {
    setEdits((current) => ({ ...current, [key]: value }))
  }

  function submitMetadata(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const input: PaperUpdateInput = {}
    for (const key of Object.keys(edits) as DraftKey[]) {
      if (key === 'authors') {
        input.authors = values.authors
          .split(',')
          .map((author) => author.trim())
          .filter(Boolean)
      } else if (key === 'year' || key === 'month') {
        input[key] = optionalNumber(values[key])
      } else {
        input[key] = values[key]
      }
    }
    if (Object.keys(input).length > 0) {
      onEdit(input)
      setEdits({})
    }
  }

  return (
    <section aria-label={`Controls for ${paper.title}`}>
      <form aria-label={`Edit metadata for ${paper.title}`} onSubmit={submitMetadata}>
        <label>Title<input name="title" value={values.title} onChange={(event) => updateDraft('title', event.target.value)} required /></label>
        <label>Authors<input name="authors" value={values.authors} onChange={(event) => updateDraft('authors', event.target.value)} /></label>
        <label>Year<input name="year" type="number" min="1600" max="2200" value={values.year} onChange={(event) => updateDraft('year', event.target.value)} /></label>
        <label>Month<input name="month" type="number" min="1" max="12" value={values.month} onChange={(event) => updateDraft('month', event.target.value)} /></label>
        <label>Summary<textarea name="summary" value={values.summary} onChange={(event) => updateDraft('summary', event.target.value)} /></label>
        <label>Link<input name="link" type="url" value={values.link} onChange={(event) => updateDraft('link', event.target.value)} required /></label>
        <button type="submit">Save paper</button>
      </form>
      {paper.review ? (
        <form aria-label={`Edit review for ${paper.title}`} onSubmit={onReviewEdit}>
          {Object.entries(paper.review).map(([key, value]) => (
            <label key={key}>
              {key}
              <textarea name={key} defaultValue={value} />
            </label>
          ))}
          <button type="submit">Save review</button>
        </form>
      ) : null}
      {paper.processing_status === 'failed' || paper.processing_status === 'cancelled' ? (
        <button type="button" onClick={onRetry}>Retry</button>
      ) : null}
      {paper.review ? (
        <button type="button" onClick={onRegenerate}>Regenerate review</button>
      ) : null}
      <button type="button" onClick={onDelete}>Delete paper</button>
    </section>
  )
}

function paperDraft(paper: Paper): Draft {
  return {
    title: paper.title,
    authors: paper.authors.join(', '),
    year: paper.year === null ? '' : String(paper.year),
    month: paper.month === null ? '' : String(paper.month),
    summary: paper.summary,
    link: paper.link,
  }
}

function optionalNumber(value: string) {
  const normalized = value.trim()
  return normalized ? Number(normalized) : null
}
