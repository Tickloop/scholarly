import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import '@/components/Canvas.css'
import { PaperNode } from '@/components/PaperNode'
import { RelationshipEdge } from '@/components/RelationshipEdge'
import { papersToNodes, relationshipToEdge } from '@/utils'
import { PAPERS, RELATIONSHIPS } from '@constants'

const nodeTypes = { paper: PaperNode }
const edgeTypes = { relationship: RelationshipEdge }
const initialNodes = papersToNodes(PAPERS)
const initialEdges = RELATIONSHIPS.map(relationshipToEdge)

export function Canvas() {
  const [nodes, , onNodesChange] = useNodesState(initialNodes)
  const [edges, , onEdgesChange] = useEdgesState(initialEdges)

  return (
    <main aria-label="Research canvas" className="research-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        fitView
      >
        <Background />
        <MiniMap />
        <Controls />
      </ReactFlow>
    </main>
  )
}
