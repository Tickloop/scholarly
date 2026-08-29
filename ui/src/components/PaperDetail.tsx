import { useState } from 'react'

import '@/components/PaperDetail.css'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { PaperDetailProps, PaperReviewSectionProps } from '@/types'

type DetailTab = 'core' | 'results'

export function PaperDetail({ paper, review }: PaperDetailProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>('core')

  return (
    <Tabs
      className="paper-detail"
      value={activeTab}
      onValueChange={(value) => setActiveTab(value as DetailTab)}
    >
      <TabsList aria-label="Paper review sections" className="paper-detail__tabs">
        <span
          aria-hidden="true"
          className="paper-detail__tab-indicator"
          data-active={activeTab}
        />
        <TabsTrigger value="core">Core idea</TabsTrigger>
        <TabsTrigger value="results">Results</TabsTrigger>
      </TabsList>

      <ScrollArea className="paper-detail__scroll">
        <TabsContent className="paper-detail__content" value="core">
          <ReviewSection
            title="Overview"
            content={paper.plain_language_summary || review?.coreIdea}
          />
          <ReviewSection title="The problem" content={review?.problemSpace} />
          <ReviewSection title="The solution" content={review?.coreIdea} />
          <ReviewSection
            title="What is new"
            content={review?.novelContribution}
          />
          <ReviewSection
            title="How it builds on earlier work"
            content={review?.citedIdeasAndDifferences}
          />
        </TabsContent>

        <TabsContent className="paper-detail__content" value="results">
          <ReviewSection title="Data" content={review?.data} />
          <ReviewSection title="Method" content={review?.approach} />
          <ReviewSection title="Results" content={review?.results} />
          <ReviewSection title="Benchmarks" content={review?.benchmarks} />
          <ReviewSection
            title="Statistical significance"
            content={review?.statisticalEvidence}
          />
          <ReviewSection title="Limitations" content={review?.limitations} />
        </TabsContent>
      </ScrollArea>
    </Tabs>
  )
}

function ReviewSection({ title, content }: PaperReviewSectionProps) {
  return (
    <section className="paper-detail__section">
      <h3>{title}</h3>
      <p>{content?.trim() || 'The paper does not report this.'}</p>
    </section>
  )
}
