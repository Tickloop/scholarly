import { useState } from 'react'
import type { Meta, StoryObj } from '@storybook/react-vite'

import { papers as mockPapers, relationships } from '@/_mock/researchData'
import { Canvas } from '@/components/Canvas'
import { CanvasProvider } from '@/context/CanvasContext'
import type { LayoutPosition, Paper } from '@/types'

const initialPapers: Paper[] = mockPapers.slice(0, 2).map((paper) => ({
  ...paper,
  x: 0,
  y: 0,
  pinned: false,
}))

function CanvasLayoutFixture() {
  const [papers, setPapers] = useState(initialPapers)
  const [persistBatches, setPersistBatches] = useState(0)

  function persistLayout(positions: LayoutPosition[]) {
    setPersistBatches((count) => count + 1)
    setPapers((current) => current.map((paper) => {
      const position = positions.find((item) => item.paper_id === paper.id)
      return position
        ? { ...paper, x: position.x, y: position.y, pinned: position.pinned }
        : paper
    }))
  }

  return (
    <CanvasProvider
      papers={papers}
      relationships={relationships.filter((relationship) =>
        papers.some((paper) => paper.id === relationship.source) &&
        papers.some((paper) => paper.id === relationship.target),
      )}
      persistLayout={persistLayout}
    >
      <Canvas
        controls={(
          <button
            type="button"
            onClick={() => setPapers((current) => [
              ...current,
              {
                ...mockPapers[1],
                id: 'inserted-paper',
                year: 2020,
                x: 0,
                y: 0,
                pinned: false,
              },
            ])}
          >
            Insert paper
          </button>
        )}
        onPaperMove={(paperId, position) => {
          setPapers((current) => current.map((paper) =>
            paper.id === paperId
              ? { ...paper, ...position, pinned: true }
              : paper,
          ))
        }}
        onRelayout={() => {
          setPapers((current) => current.map((paper) => ({
            ...paper,
            pinned: false,
          })))
        }}
      />
      <output aria-label="Layout state">
        Papers: {papers.length}; pinned: {papers.filter((paper) => paper.pinned).length}; persisted: {persistBatches}; positions: {papers.map((paper) => `${paper.id}:${paper.x},${paper.y}`).join('|')}
      </output>
    </CanvasProvider>
  )
}

const meta = {
  title: 'Research/CanvasLayout',
  component: CanvasLayoutFixture,
} satisfies Meta<typeof CanvasLayoutFixture>

export default meta
type Story = StoryObj<typeof meta>

export const MoveInsertPersistRelayout: Story = {}
