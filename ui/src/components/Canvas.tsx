import {
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type OnEdgesChange,
  type OnNodesChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useCallback } from 'react'

import { PaperNode } from '@/components/PaperNode'
import { PaperEdge } from '@/components/PaperEdge'
import { useGraph } from '@/contexts/GraphProvider'
import { PaperEdgeType, PaperNodeType } from '@/types'
import type { GraphEdge, GraphNode } from '@/types'

const nodeTypes = { [PaperNodeType]: PaperNode }
const edgeTypes = { [PaperEdgeType]: PaperEdge }

export function Canvas() {
  const { nodes, edges, setNodes, setEdges } = useGraph()

  const onNodesChange: OnNodesChange<GraphNode> = useCallback(
    (changes) => setNodes((nodesSnapshot) => applyNodeChanges(changes, nodesSnapshot)),
    [setNodes],
  )

  const onEdgesChange: OnEdgesChange<GraphEdge> = useCallback(
    (changes) => setEdges((edgesSnapshot) => applyEdgeChanges(changes, edgesSnapshot)),
    [setEdges],
  )

  return (
    <main aria-label="Research canvas" style={{ height: '100%' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
      >
        <Background />
        <MiniMap />
        <Controls />
      </ReactFlow>
    </main>
  )
}
