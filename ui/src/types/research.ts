export type PaperId = string

export type Paper = {
  id: PaperId
  title: string
  authors: string[]
  year: number | null
  month: number | null
  summary: string
  link: string
  processing_status?: string
  error?: string | null
  x?: number
  y?: number
  pinned?: boolean
  review?: PaperReview
}

export type CreatePaperInput = Omit<Paper, 'id'>

export type PaperRelationship = {
  id: string
  source: PaperId
  target: PaperId
  type: 'extends' | 'contradicts' | 'same_benchmark' | 'uses_method' | 'cites' | 'related'
  label: string
  explanation: string
}

export type CreateRelationshipInput = Omit<PaperRelationship, 'id'>

export type PaperReview = {
  coreIdea: string
  problemSpace: string
  approach: string
  data: string
  novelContribution: string
  results: string
  benchmarks: string
  statisticalEvidence: string
  limitations: string
  citedIdeasAndDifferences: string
}
