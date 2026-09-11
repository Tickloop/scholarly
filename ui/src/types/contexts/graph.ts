import type { Dispatch, SetStateAction } from "react"
import type { GraphEdge, GraphNode } from "@/types/components"

export type GraphContextValue = {
  nodes: GraphNode[]
  edges: GraphEdge[]
  setNodes: Dispatch<SetStateAction<GraphNode[]>>
  setEdges: Dispatch<SetStateAction<GraphEdge[]>>
}
