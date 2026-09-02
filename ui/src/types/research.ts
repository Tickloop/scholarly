export type Paper = {
  id: string
  title: string
  authors: string[]
  year: number | null
  month: number | null
  summary: string
  link: string
  x?: number
  y?: number
}

export type PaperRelationship = {
  id: string
  source: string
  target: string
  label: string
}
