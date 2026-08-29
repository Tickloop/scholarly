import { ReactFlow, type NodeProps } from '@xyflow/react'
import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect, fn, userEvent, waitFor, within } from 'storybook/test'
import '@xyflow/react/dist/style.css'

import { papers } from '@/_mock/researchData'
import { PaperNode } from '@/components/PaperNode'
import type { Paper, PaperCanvasNode } from '@/types'
import { paperToNode } from '@/utils'

const nodeTypes = { paper: PaperNode }
const activate = fn()
const plainLanguageSummary =
  'This paper introduces a model that lets every word look directly at the other words that matter. It trains faster than older sequence models and improves machine translation results.'
const defaultPaper: Paper = {
  ...papers[0],
  plain_language_summary: plainLanguageSummary,
  processing_status: 'reviewed',
}

const defaultArgs: NodeProps<PaperCanvasNode> = {
  id: defaultPaper.id,
  data: { paper: defaultPaper, onActivate: activate },
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
    const node = {
      ...paperToNode(data.paper),
      data,
      selected,
    }

    return (
      <div style={{ width: '100vw', height: '100vh' }}>
        <ReactFlow
          key={`${data.paper.id}-${selected}`}
          defaultNodes={[node]}
          nodeTypes={nodeTypes}
          fitView
        />
      </div>
    )
  },
} satisfies Meta<typeof PaperNode>

export default meta

type Story = StoryObj<typeof meta>

export const Default: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    const card = await waitFor(() =>
      canvas.getByRole('article', { name: defaultPaper.title }),
    )
    await expect(within(card).queryByText(plainLanguageSummary)).toBeNull()
    await expect(within(card).queryByText(defaultPaper.summary)).toBeNull()
    await expect(within(card).getByText('Ashish Vaswani +3')).toBeVisible()
    await expect(within(card).getByText('Reviewed')).toBeVisible()

    await userEvent.hover(card)
    await waitFor(() =>
      expect(
        within(canvasElement.ownerDocument.body).getByText(plainLanguageSummary),
      ).toBeVisible(),
    )
  },
}

export const KeyboardPreviewAndActivate: Story = {
  play: async ({ canvasElement }) => {
    const card = await waitFor(() =>
      within(canvasElement).getByRole('article', { name: defaultPaper.title }),
    )
    card.focus()
    await waitFor(() =>
      expect(
        within(canvasElement.ownerDocument.body).getByText(plainLanguageSummary),
      ).toBeVisible(),
    )
    await userEvent.keyboard('{Enter}')
    await expect(activate).toHaveBeenCalled()
  },
}

export const LegacyReviewFallback: Story = {
  args: {
    data: {
      paper: {
        ...defaultPaper,
        plain_language_summary: null,
      },
    },
  },
  play: async ({ canvasElement }) => {
    const card = await waitFor(() =>
      within(canvasElement).getByRole('article', { name: defaultPaper.title }),
    )
    await userEvent.hover(card)
    await waitFor(() =>
      expect(
        within(canvasElement.ownerDocument.body).getByText(
          defaultPaper.review!.coreIdea,
        ),
      ).toBeVisible(),
    )
    await expect(
      within(canvasElement.ownerDocument.body).queryByText(defaultPaper.summary),
    ).toBeNull()
  },
}

export const Processing: Story = {
  args: {
    data: {
      paper: {
        ...defaultPaper,
        plain_language_summary: null,
        review: undefined,
        processing_status: 'processing',
      },
    },
  },
  play: async ({ canvasElement }) => {
    const card = await waitFor(() =>
      within(canvasElement).getByRole('article', { name: defaultPaper.title }),
    )
    await userEvent.hover(card)
    await waitFor(() =>
      expect(
        within(canvasElement.ownerDocument.body).getByText(
          'The plain-language summary will appear when this paper has been reviewed.',
        ),
      ).toBeVisible(),
    )
  },
}

export const Failed: Story = {
  args: {
    data: {
      paper: {
        ...defaultPaper,
        plain_language_summary: null,
        review: undefined,
        processing_status: 'failed',
        error: 'No usable paper text was available.',
      },
    },
  },
}

export const UnknownDate: Story = {
  args: {
    data: {
      paper: { ...defaultPaper, year: null, month: null },
    },
  },
  play: async ({ canvasElement }) => {
    await waitFor(() =>
      expect(within(canvasElement).getByText('Date unknown')).toBeVisible(),
    )
  },
}
