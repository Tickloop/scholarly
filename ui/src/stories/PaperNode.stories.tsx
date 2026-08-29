import { ReactFlow, type NodeProps } from '@xyflow/react'
import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect, fn, userEvent, waitFor, within } from 'storybook/test'
import '@xyflow/react/dist/style.css'

import { papers } from '@/_mock/researchData'
import { PaperInspector, PaperNode } from '@/components/PaperNode'
import { CanvasProvider } from '@/context/CanvasContext'
import type { PaperCanvasNode } from '@/types'
import { paperToNode } from '@/utils'

const nodeTypes = { paper: PaperNode }
const editPaper = fn()
const retryPaperProcessing = fn()
const removePaper = fn()
const editReview = fn()
const regeneratePaperReview = fn()

const defaultArgs: NodeProps<PaperCanvasNode> = {
  id: papers[0].id,
  data: { paper: papers[0] },
  type: 'paper',
  dragging: false,
  zIndex: 0,
  selectable: true,
  deletable: true,
  selected: false,
  draggable: true,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
}

const meta = {
  title: 'Research/PaperNode',
  component: PaperNode,
  args: defaultArgs,
  render: ({ data, selected }) => {
    const node = { ...paperToNode(data.paper), selected }

    return (
      <CanvasProvider
        editPaper={editPaper}
        retryPaperProcessing={retryPaperProcessing}
        removePaper={removePaper}
        editReview={editReview}
        regeneratePaperReview={regeneratePaperReview}
      >
        <div style={{ width: '100vw', height: '100vh' }}>
          <ReactFlow
            key={`${data.paper.id}-${selected}`}
            defaultNodes={[node]}
            nodeTypes={nodeTypes}
            fitView
          />
          {selected ? <PaperInspector paper={data.paper} /> : null}
        </div>
      </CanvasProvider>
    )
  },
} satisfies Meta<typeof PaperNode>

export default meta

type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const UnknownDate: Story = {
  args: {
    data: {
      paper: { ...papers[0], year: null, month: null },
    },
  },
  play: async ({ canvasElement }) => {
    await waitFor(() =>
      expect(within(canvasElement).getByText('Date unknown')).toBeVisible(),
    )
  },
}

export const Editable: Story = {
  args: { selected: true },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    await expect(
      canvas.getByRole('form', {
        name: `Edit metadata for ${papers[0].title}`,
      }),
    ).toBeVisible()
    const title = canvas.getByRole('textbox', { name: 'Title' })
    await userEvent.clear(title)
    await userEvent.type(title, 'Edited paper title')
    await userEvent.click(canvas.getByRole('button', { name: 'Save paper' }))
    await expect(editPaper).toHaveBeenCalledWith(
      papers[0].id,
      expect.objectContaining({ title: 'Edited paper title' }),
    )
  },
}

export const Failed: Story = {
  args: {
    selected: true,
    data: {
      paper: {
        ...papers[0],
        review: undefined,
        processing_status: 'failed',
        error: 'No usable paper text was available.',
      },
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    await userEvent.click(canvas.getByRole('button', { name: 'Retry' }))
    await expect(retryPaperProcessing).toHaveBeenCalledWith(papers[0].id)
    await userEvent.click(canvas.getByRole('button', { name: 'Delete paper' }))
    await expect(removePaper).toHaveBeenCalledWith(papers[0].id)
  },
}
