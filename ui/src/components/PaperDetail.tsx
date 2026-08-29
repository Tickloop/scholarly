import { useId } from 'react'

import '@/components/PaperDetail.css'
import type { PaperDetailProps, PaperReviewSectionProps } from '@/types'

export function PaperDetail({ paper, review }: PaperDetailProps) {
  const titleId = useId()
  const publicationDate = paper.year === null
    ? undefined
    : paper.month === null
      ? String(paper.year)
      : `${paper.year}-${String(paper.month).padStart(2, '0')}`
  const authorByLine = paper.authors.join(', ') + " · "

  return (
    <aside aria-labelledby={titleId} className="paper-detail">
      <header className="paper-details__header">
        <div>
          <p className="paper-details__eyebrow">Paper review</p>
          <h2 id={titleId}>{paper.title}</h2>
          <p className="paper-details__byline">
            {authorByLine}
            {publicationDate ? (
              <time dateTime={publicationDate}>{paper.year}</time>
            ) : (
              <span>Date unknown</span>
            )}
          </p>
        </div>
      </header>

      <div className="paper-details__body">
        <section className="paper-details__core">
          <h3>Core idea</h3>
          <p>{review.coreIdea}</p>
        </section>

        <ReviewSection title="Problem space" content={review.problemSpace} />
        <ReviewSection title="Approach" content={review.approach} />
        <ReviewSection title="Data" content={review.data} />
        <ReviewSection
          title="Novel contribution"
          content={review.novelContribution}
        />
        <ReviewSection title="Results" content={review.results} />
        <ReviewSection title="Benchmarks" content={review.benchmarks} />
        <ReviewSection
          title="Statistical evidence"
          content={review.statisticalEvidence}
        />
        <ReviewSection title="Limitations" content={review.limitations} />
        <ReviewSection
          title="Cited ideas and differences from earlier work"
          content={review.citedIdeasAndDifferences}
        />

        <a
          className="paper-details__source"
          href={paper.link}
          rel="noreferrer"
          target="_blank"
        >
          Read the source paper <span aria-hidden="true">↗</span>
        </a>
      </div>
    </aside>
  )
}

function ReviewSection({ title, content }: PaperReviewSectionProps) {
  return (
    <section className="paper-details__section">
      <h3>{title}</h3>
      <p>{content}</p>
    </section>
  )
}
