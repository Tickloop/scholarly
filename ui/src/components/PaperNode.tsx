import { Handle, Position, type NodeProps } from '@xyflow/react'
import {
  useId,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
  type MouseEvent,
} from 'react'

import '@/components/PaperNode.css'
import { PaperDetail } from '@/components/PaperDetail'
import { Badge } from '@/components/ui/badge'
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from '@/components/ui/hover-card'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { constants } from '@/constants'
import type { Paper, PaperCanvasNode } from '@/types'

const monthFormatter = new Intl.DateTimeFormat('en', {
  month: 'short',
  timeZone: 'UTC',
})

export function PaperNode({ data, selected }: NodeProps<PaperCanvasNode>) {
  const { paper } = data
  const titleId = useId()
  const previewId = useId()
  const nodeRef = useRef<HTMLElement>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const publication = getPublication(paper)
  const author = getAuthorLabel(paper.authors)
  const status = getPaperStatus(paper)
  const preview = getPaperPreview(paper)

  function activate() {
    setPreviewOpen(false)
    data.onActivate?.(nodeRef.current ?? undefined)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    delete event.currentTarget.dataset.restoringFocus
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      activate()
    }
  }

  function keepNodeClosed(event: MouseEvent<HTMLElement>) {
    event.stopPropagation()
  }

  function handleFocus(event: FocusEvent<HTMLElement>) {
    if (event.currentTarget.dataset.restoringFocus === 'true') return
    setPreviewOpen(true)
  }

  function handleBlur(event: FocusEvent<HTMLElement>) {
    delete event.currentTarget.dataset.restoringFocus
    setPreviewOpen(false)
  }

  return (
    <HoverCard
      open={previewOpen}
      onOpenChange={(open) => {
        if (open && nodeRef.current?.dataset.restoringFocus === 'true') return
        setPreviewOpen(open)
      }}
      openDelay={260}
    >
      <HoverCardTrigger asChild>
        <article
          ref={nodeRef}
          aria-labelledby={titleId}
          aria-describedby={previewOpen ? previewId : undefined}
          className="paper-node"
          data-selected={selected}
          tabIndex={0}
          style={{ width: constants.PAPER_NODE_DEFAULT_WIDTH }}
          onClick={activate}
          onBlur={handleBlur}
          onFocus={handleFocus}
          onKeyDown={handleKeyDown}
          onPointerMove={(event) => {
            if (event.currentTarget.dataset.restoringFocus !== 'true') return
            delete event.currentTarget.dataset.restoringFocus
            setPreviewOpen(true)
          }}
        >
          <Handle type="target" position={Position.Left} />

          <div className="paper-node__meta">
            {publication.dateTime ? (
              <time dateTime={publication.dateTime}>{publication.label}</time>
            ) : (
              <span>{publication.label}</span>
            )}
            <Badge data-status={status.tone} variant="outline">
              {status.label}
            </Badge>
          </div>

          <h2 id={titleId}>{paper.title}</h2>
          <p className="paper-node__author">{author}</p>

          <a
            aria-label={`Open ${paper.title} in a new tab`}
            className="paper-node__source nodrag nopan"
            href={paper.link}
            target="_blank"
            rel="noreferrer"
            onClick={keepNodeClosed}
            onPointerDown={keepNodeClosed}
          >
            Source <span aria-hidden="true">↗</span>
          </a>

          <Handle type="source" position={Position.Right} />
        </article>
      </HoverCardTrigger>
      <HoverCardContent
        id={previewId}
        align="start"
        className="paper-node__preview nodrag nopan"
        side="top"
        sideOffset={12}
      >
        <p className="paper-node__preview-label">About this paper</p>
        <p>{preview.text}</p>
        {preview.kind !== 'summary' ? (
          <Badge data-status={preview.kind} variant="outline">
            {preview.label}
          </Badge>
        ) : null}
      </HoverCardContent>
    </HoverCard>
  )
}

export function PaperInspector({
  paper,
  onClose,
}: {
  paper: Paper
  onClose?: () => void
}) {
  const publication = getPublication(paper)
  const status = getPaperStatus(paper)

  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open) onClose?.()
      }}
    >
      <SheetContent
        aria-describedby={`paper-sheet-description-${paper.id}`}
        className="paper-node__inspector"
        side="right"
      >
        <SheetHeader className="paper-node__inspector-header">
          <div className="paper-node__inspector-meta">
            <Badge data-status={status.tone} variant="outline">
              {status.label}
            </Badge>
            {publication.dateTime ? (
              <time dateTime={publication.dateTime}>{publication.label}</time>
            ) : (
              <span>{publication.label}</span>
            )}
          </div>
          <SheetTitle>{paper.title}</SheetTitle>
          <SheetDescription id={`paper-sheet-description-${paper.id}`}>
            {paper.authors.length > 0 ? paper.authors.join(', ') : 'Authors not listed'}
          </SheetDescription>
          <a
            className="paper-node__inspector-source"
            href={paper.link}
            rel="noreferrer"
            target="_blank"
          >
            Open paper <span aria-hidden="true">↗</span>
          </a>
        </SheetHeader>

        <PaperDetail paper={paper} review={paper.review} />
      </SheetContent>
    </Sheet>
  )
}

function getPublication(paper: Paper) {
  if (paper.year === null) {
    return { dateTime: undefined, label: 'Date unknown' }
  }
  if (paper.month === null) {
    return { dateTime: String(paper.year), label: String(paper.year) }
  }
  return {
    dateTime: `${paper.year}-${String(paper.month).padStart(2, '0')}`,
    label: `${monthFormatter.format(new Date(Date.UTC(paper.year, paper.month - 1)))} ${paper.year}`,
  }
}

function getAuthorLabel(authors: string[]) {
  if (authors.length === 0) return 'Authors not listed'
  if (authors.length === 1) return authors[0]
  return `${authors[0]} +${authors.length - 1}`
}

function getPaperStatus(paper: Paper) {
  const raw = paper.processing_status ?? (paper.review ? 'reviewed' : 'processing')
  if (raw === 'reviewed' || raw === 'completed') {
    return { label: 'Reviewed', tone: 'success' }
  }
  if (raw === 'failed' || raw === 'cancelled') {
    return { label: raw === 'failed' ? 'Failed' : 'Cancelled', tone: 'danger' }
  }
  if (raw === 'queued') return { label: 'Queued', tone: 'neutral' }
  return { label: 'Processing', tone: 'info' }
}

function getPaperPreview(paper: Paper): {
  kind: 'summary' | 'processing' | 'failed'
  label: string
  text: string
} {
  const summary = paper.plain_language_summary?.trim() || paper.review?.coreIdea.trim()
  if (summary) {
    return { kind: 'summary', label: 'Summary', text: summary }
  }

  const failed =
    paper.processing_status === 'failed' || paper.processing_status === 'cancelled'
  if (failed) {
    return {
      kind: 'failed',
      label: paper.processing_status === 'cancelled' ? 'Cancelled' : 'Failed',
      text: paper.error?.trim() || 'A plain-language summary could not be created.',
    }
  }

  return {
    kind: 'processing',
    label: 'Processing',
    text: 'The plain-language summary will appear when this paper has been reviewed.',
  }
}
