import { Handle, Position, type NodeProps } from '@xyflow/react'
import { useId, type MouseEvent } from 'react'

import type { Paper, PaperNode } from '@/types'
import { PAPER_DATE_FORMATTER, PAPER_NODE_WIDTH } from '@/constants'

function getPublication(paper: Paper) {
  if (paper.year === null) {
    return { dateTime: undefined, label: 'Date unknown' }
  }
  if (paper.month === null) {
    return { dateTime: String(paper.year), label: String(paper.year) }
  }
  return {
    dateTime: `${paper.year}-${String(paper.month).padStart(2, '0')}`,
    label: `${PAPER_DATE_FORMATTER.format(new Date(Date.UTC(paper.year, paper.month - 1)))} ${paper.year}`,
  }
}

function getAuthorLabel(authors: string[]) {
  if (authors.length === 0) return 'Authors not listed'
  if (authors.length === 1) return authors[0]
  return `${authors[0]} +${authors.length - 1}`
}

export function PaperNode({ data }: NodeProps<PaperNode>) {
  const { paper } = data
  const titleId = useId()
  const publication = getPublication(paper)

  function keepNodeOpen(event: MouseEvent<HTMLAnchorElement>) {
    event.stopPropagation()
  }

  return (
    <article
      aria-labelledby={titleId}
      style={{ width: PAPER_NODE_WIDTH, border: "1px solid black", padding: "0.25rem 0.5rem", borderRadius: "0.5rem" }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }}/>

      {publication.dateTime ? (
        <time dateTime={publication.dateTime}>{publication.label}</time>
      ) : (
        <span>{publication.label}</span>
      )}
      <h2 id={titleId}>{paper.title}</h2>
      <p>{getAuthorLabel(paper.authors)}</p>
      <p>{paper.summary}</p>
      <a
        aria-label={`Open ${paper.title} in a new tab`}
        className="nodrag nopan"
        href={paper.link}
        target="_blank"
        rel="noreferrer"
        onClick={keepNodeOpen}
        onPointerDown={keepNodeOpen}
      >
        Source <span aria-hidden="true">↗</span>
      </a>

      <Handle type="source" position={Position.Right} style={{ opacity: 0 }}/>
    </article>
  )
}
