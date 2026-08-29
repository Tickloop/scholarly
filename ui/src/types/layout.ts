export type LayoutNode = {
  id: string
  year?: number | null
  month?: number | null
  x: number
  y: number
  width: number
  height: number
  pinned: boolean
}

export type LayoutRelationship = {
  source: string
  target: string
}

export type LayoutRequest = {
  nodes: LayoutNode[]
  relationships: LayoutRelationship[]
}

export type LayoutPosition = {
  paper_id: string
  x: number
  y: number
  pinned: boolean
}

export type LayoutResult = {
  positions: LayoutPosition[]
}
