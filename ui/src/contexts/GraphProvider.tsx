import { PAPERS, RELATIONSHIPS } from "@/constants";
import { papersToNodes, relationshipToEdge } from "@/utils";
import { createContext, useContext, useState } from "react";
import type { GraphContextValue } from "@/types";
import type { ReactNode } from "react";

const GraphContext = createContext<GraphContextValue | null>(null)

export function GraphProvider({ children }: { children: ReactNode }) {
  const [ nodes, setNodes ] = useState(() => papersToNodes(PAPERS))
  const [ edges, setEdges ] = useState(() => RELATIONSHIPS.map(relationshipToEdge))

  return (
    <GraphContext.Provider value={{ nodes, edges, setNodes, setEdges }}>
      {children}
    </GraphContext.Provider>
  )
}

export function useGraph() {
  const context = useContext(GraphContext)

  if (!context) {
    throw new Error("outside GraphProvider")
  }

  return context
}
