import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
} from 'react'
import { useEdgesState, useNodesState } from '@xyflow/react'

import {
  papers as mockPapers,
  relationships as mockRelationships,
} from '@/_mock/researchData'
import { constants } from '@/constants'
import type {
  CanvasContextValue,
  CanvasProviderProps,
  CreatePaperInput,
  CreateRelationshipInput,
  PaperId,
  PaperReview,
} from '@/types'
import { layoutInWorker } from '@/layout/layoutWorkerClient'
import {
  createPaper,
  createRelationship,
  paperToNode,
  papersToNodes,
  relationshipToEdge,
} from '@/utils'

const CanvasContext = createContext<CanvasContextValue | null>(null)

export function CanvasProvider({
  children,
  papers = mockPapers,
  relationships = mockRelationships,
  editPaper,
  retryPaperProcessing,
  removePaper,
  editReview,
  regeneratePaperReview,
  editRelationship,
  removeRelationship,
  persistLayout,
}: CanvasProviderProps) {
  const initialNodes = papersToNodes(papers)
  const initialEdges = relationships.map((relationship) =>
    relationshipToEdge(relationship),
  )
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges)
  const nodesRef = useRef(nodes)
  const layoutRequestId = useRef(0)
  const persistLayoutRef = useRef(persistLayout)
  const papersRef = useRef(papers)
  const relationshipsRef = useRef(relationships)

  useEffect(() => {
    nodesRef.current = nodes
    persistLayoutRef.current = persistLayout
    papersRef.current = papers
    relationshipsRef.current = relationships
  }, [nodes, papers, persistLayout, relationships])

  const graphSignature = useMemo(
    () => JSON.stringify({
      papers: [...papers]
        .sort((left, right) => left.id.localeCompare(right.id))
        .map((paper) => [
          paper.id,
          paper.year,
          paper.month,
          Boolean(paper.pinned),
          paper.pinned ? paper.x ?? 0 : null,
          paper.pinned ? paper.y ?? 0 : null,
        ]),
      relationships: [...relationships]
        .sort((left, right) => left.id.localeCompare(right.id))
        .map((relationship) => [
          relationship.id,
          relationship.source,
          relationship.target,
        ]),
    }),
    [papers, relationships],
  )
  const measurementSignature = nodes
    .map((node) => `${node.id}:${node.measured?.width ?? 0}:${node.measured?.height ?? 0}`)
    .sort()
    .join('|')

  useEffect(() => {
    setNodes((current) => {
      const currentById = new Map(current.map((node) => [node.id, node]))
      return papers.map((paper) => {
        const existing = currentById.get(paper.id)
        if (!existing) {
          return paperToNode(paper, { x: paper.x ?? 0, y: paper.y ?? 0 })
        }
        return {
          ...existing,
          data: { paper },
          ariaLabel: `Paper: ${paper.title}`,
          position: paper.pinned
            ? { x: paper.x ?? 0, y: paper.y ?? 0 }
            : existing.position,
        }
      })
    })
    setEdges(
      relationships.map((relationship) => relationshipToEdge(relationship)),
    )
  }, [papers, relationships, setEdges, setNodes])

  useEffect(() => {
    const requestId = ++layoutRequestId.current
    const currentPapers = papersRef.current
    const currentRelationships = relationshipsRef.current
    const currentById = new Map(nodesRef.current.map((node) => [node.id, node]))
    if (currentPapers.some((paper) => {
      const measured = currentById.get(paper.id)?.measured
      return !measured?.width || !measured.height
    })) {
      return
    }

    void layoutInWorker({
      nodes: currentPapers.map((paper) => {
        const current = currentById.get(paper.id)
        return {
          id: paper.id,
          year: paper.year,
          month: paper.month,
          x: paper.pinned ? paper.x ?? 0 : current?.position.x ?? paper.x ?? 0,
          y: paper.pinned ? paper.y ?? 0 : current?.position.y ?? paper.y ?? 0,
          width: current?.measured?.width ?? constants.PAPER_NODE_DEFAULT_WIDTH,
          height: current?.measured?.height ?? 320,
          pinned: Boolean(paper.pinned),
        }
      }),
      relationships: currentRelationships.map(({ source, target }) => ({ source, target })),
    }).then((result) => {
      if (layoutRequestId.current !== requestId) return
      const positionById = new Map(
        result.positions.map((position) => [position.paper_id, position]),
      )
      setNodes((current) => current.map((node) => {
        const position = positionById.get(node.id)
        return position
          ? { ...node, position: { x: position.x, y: position.y } }
          : node
      }))

      const changedUnpinned = result.positions.filter((position) => {
        const paper = currentPapers.find((item) => item.id === position.paper_id)
        return paper && !paper.pinned && (
          paper.x !== position.x || paper.y !== position.y
        )
      })
      if (changedUnpinned.length > 0) {
        void persistLayoutRef.current?.(changedUnpinned)
      }
    }).catch(() => undefined)
  }, [graphSignature, measurementSignature, setNodes])

  const createPaperNode = useCallback(
    (input: CreatePaperInput) => {
      const paper = createPaper(input)

      setNodes((currentNodes) => [...currentNodes, paperToNode(paper)])

      return paper
    },
    [setNodes],
  )

  const deletePaperNode = useCallback(
    (paperId: PaperId) => {
      setNodes((currentNodes) =>
        currentNodes.filter((node) => node.id !== paperId),
      )
      setEdges((currentEdges) =>
        currentEdges.filter(
          (edge) => edge.source !== paperId && edge.target !== paperId,
        ),
      )
    },
    [setEdges, setNodes],
  )

  const assignPaperReview = useCallback(
    (paperId: PaperId, review: PaperReview) => {
      setNodes((currentNodes) =>
        currentNodes.map((node) =>
          node.id === paperId
            ? {
                ...node,
                data: {
                  paper: { ...node.data.paper, review },
                },
              }
            : node,
        ),
      )
    },
    [setNodes],
  )

  const createRelationshipEdge = useCallback(
    (input: CreateRelationshipInput) => {
      const relationship = createRelationship(input)

      setEdges((currentEdges) => [
        ...currentEdges,
        relationshipToEdge(relationship),
      ])

      return relationship
    },
    [setEdges],
  )

  const deleteRelationshipEdge = useCallback(
    (relationshipId: string) => {
      setEdges((currentEdges) =>
        currentEdges.filter((edge) => edge.id !== relationshipId),
      )
    },
    [setEdges],
  )

  const value = useMemo<CanvasContextValue>(
    () => ({
      nodes,
      edges,
      onNodesChange,
      onEdgesChange,
      createPaperNode,
      deletePaperNode,
      assignPaperReview,
      createRelationshipEdge,
      deleteRelationshipEdge,
      editPaper,
      retryPaperProcessing,
      removePaper,
      editReview,
      regeneratePaperReview,
      editRelationship,
      removeRelationship,
      persistLayout,
    }),
    [
      nodes,
      edges,
      onNodesChange,
      onEdgesChange,
      createPaperNode,
      deletePaperNode,
      assignPaperReview,
      createRelationshipEdge,
      deleteRelationshipEdge,
      editPaper,
      retryPaperProcessing,
      removePaper,
      editReview,
      regeneratePaperReview,
      editRelationship,
      removeRelationship,
      persistLayout,
    ],
  )

  return (
    <CanvasContext.Provider value={value}>
      {children}
    </CanvasContext.Provider>
  )
}

export function useCanvas() {
  const context = useContext(CanvasContext)

  if (!context) {
    throw new Error('useCanvas must be used within CanvasProvider')
  }

  return context
}

export function useCanvasActions() {
  return useContext(CanvasContext)
}
