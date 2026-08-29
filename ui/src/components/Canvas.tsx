import {
  Background,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  type ReactFlowInstance,
} from '@xyflow/react'
import { useEffect, useRef, useState } from 'react'
import '@xyflow/react/dist/style.css'

import '@/components/Canvas.css'
import { PaperInspector, PaperNode } from '@/components/PaperNode'
import { RelationshipEdge } from '@/components/RelationshipEdge'
import { useCanvas } from '@/context/CanvasContext'
import type {
  CanvasProps,
  PaperCanvasNode,
  RelationshipEdge as RelationshipEdgeType,
} from '@/types'
import { isPaperLink } from '@/utils'

const nodeTypes = { paper: PaperNode }
const edgeTypes = { relationship: RelationshipEdge }

export function Canvas({
  children,
  controls,
  selectedPaperId,
  selectedRelationshipId,
  onPaperSelect,
  onRelationshipSelect,
  onPaperLinkPaste,
  onPaperMove,
  onRelayout,
}: CanvasProps) {
  const { nodes, edges, onNodesChange, onEdgesChange } = useCanvas()
  const selectedPaper = nodes.find((node) => node.id === selectedPaperId)?.data.paper
  const [instance, setInstance] = useState<
    ReactFlowInstance<PaperCanvasNode, RelationshipEdgeType>
  >()
  const pointer = useRef({ x: 0, y: 0 })

  useEffect(() => {
    if (!instance || !onPaperLinkPaste) return

    function handlePaste(event: ClipboardEvent) {
      if (isEditableTarget(event.target)) return
      const value = event.clipboardData?.getData('text').trim()
      if (!value || !isPaperLink(value)) return

      event.preventDefault()
      onPaperLinkPaste?.(value, instance!.screenToFlowPosition(pointer.current))
    }

    document.addEventListener('paste', handlePaste)
    return () => document.removeEventListener('paste', handlePaste)
  }, [instance, onPaperLinkPaste])

  return (
    <main
      aria-label="Research canvas"
      data-ready={instance ? 'true' : 'false'}
      style={{ position: 'fixed', inset: 0 }}
      onPointerMove={(event) => {
        pointer.current = { x: event.clientX, y: event.clientY }
      }}
    >
      <ReactFlow
        nodes={nodes.map((node) => ({
          ...node,
          data: {
            ...node.data,
            onActivate: () => {
              onRelationshipSelect?.(undefined)
              onPaperSelect?.(node.id)
            },
          },
          selected: node.id === selectedPaperId,
        }))}
        edges={edges.map((edge) => ({
          ...edge,
          data: {
            relationship: edge.data!.relationship,
            onActivate: () => {
              onPaperSelect?.(undefined)
              onRelationshipSelect?.(edge.id)
            },
          },
          selected: edge.id === selectedRelationshipId,
        }))}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onInit={setInstance}
        onNodeClick={(_, node) => {
          onRelationshipSelect?.(undefined)
          onPaperSelect?.(node.id)
        }}
        onEdgeClick={(_, edge) => {
          onPaperSelect?.(undefined)
          onRelationshipSelect?.(edge.id)
        }}
        onPaneClick={() => {
          onPaperSelect?.(undefined)
          onRelationshipSelect?.(undefined)
        }}
        onNodeDragStop={(_, node) => onPaperMove?.(node.id, node.position)}
        multiSelectionKeyCode={null}
        nodesFocusable={false}
        edgesFocusable={false}
        fitView
      >
        {controls ? <Panel position="top-left">{controls}</Panel> : null}
        {onRelayout ? (
          <Panel position="top-right">
            <button type="button" onClick={onRelayout}>Relayout</button>
          </Panel>
        ) : null}
        <Background />
        <MiniMap />
        <Controls />
      </ReactFlow>
      {selectedPaper ? (
        <PaperInspector key={selectedPaper.id} paper={selectedPaper} />
      ) : null}
      {children ? (
        <div className="canvas__interaction">{children}</div>
      ) : null}
    </main>
  )
}

function isEditableTarget(target: EventTarget | null) {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  )
}
