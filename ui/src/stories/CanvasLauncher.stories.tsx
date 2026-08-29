import { expect, fn, userEvent, within } from 'storybook/test'
import type { Meta, StoryObj } from '@storybook/react-vite'

import { papers } from '@/_mock/researchData'
import { CanvasLauncher } from '@/components/CanvasLauncher'

const canvas = {
  id: 'canvas-1',
  name: 'Transformer history',
  research_goal: 'Map the papers that led to modern transformers.',
  build_status: 'completed',
  created_at: '2026-08-29T00:00:00Z',
  updated_at: '2026-08-29T00:00:00Z',
}

const meta = {
  title: 'Research/CanvasLauncher',
  component: CanvasLauncher,
  args: {
    canvas,
    papers,
    messages: [],
    selectedCanvasId: 'canvas-1',
    isBusy: false,
    onCreate: fn(),
    onAddPaperLink: fn(),
    onSendMessage: fn(),
    onClearPaper: fn(),
  },
} satisfies Meta<typeof CanvasLauncher>

export default meta

type Story = StoryObj<typeof meta>

export const Idle: Story = {
  args: {
    canvas: undefined,
    selectedCanvasId: undefined,
  },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement)
    await expect(
      canvas.getByRole('region', { name: 'Research chat' }),
    ).toBeVisible()
    await userEvent.type(
      canvas.getByRole('textbox', { name: 'Research goal' }),
      'Map retrieval augmented generation research',
    )
    await userEvent.click(canvas.getByRole('button', { name: 'Build canvas' }))
    await expect(args.onCreate).toHaveBeenCalledWith({
      name: 'Map retrieval augmented generation research',
      research_goal: 'Map retrieval augmented generation research',
    })
  },
}

export const Queued: Story = {
  args: {
    runStatus: 'queued',
    runStatusLabel: 'Paper link run',
  },
}

export const NewlyCreatedIdleCanvas: Story = {
  args: {
    canvas: { ...canvas, id: 'canvas-2', build_status: 'idle' },
    selectedCanvasId: 'canvas-2',
    runStatus: 'idle',
    runStatusLabel: 'Canvas build',
  },
  play: async ({ canvasElement }) => {
    const story = within(canvasElement)
    await expect(story.getByText('Canvas build: idle')).toBeVisible()
    await expect(story.queryByText('Canvas build: completed')).toBeNull()
  },
}

export const Processing: Story = {
  args: {
    messages: [
      {
        id: 'message-1',
        canvas_id: 'canvas-1',
        role: 'user',
        agent_name: 'main',
        content: 'Compare the strongest approaches.',
        status: 'queued',
        created_at: '2026-08-29T00:00:00Z',
      },
    ],
    runStatus: 'review.completed',
    runStatusLabel: 'Paper link run',
  },
}

export const FailedLink: Story = {
  args: {
    error: 'No verified academic metadata was found for the submitted link.',
    runStatus: 'run.failed',
    runStatusLabel: 'Paper link run',
  },
}

export const RecoverableCompletedBuild: Story = {
  args: {
    runStatus: 'run.completed',
    runStatusLabel: 'Canvas build run',
    onRetry: fn(),
  },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement)
    await userEvent.click(canvas.getByRole('button', { name: 'Retry' }))
    await expect(args.onRetry).toHaveBeenCalledOnce()
  },
}

export const PaperMention: Story = {
  args: {
    selectedPaper: papers[0],
    activeAgent: 'reviewer',
  },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement)
    const input = canvas.getByRole('textbox', {
      name: 'Message, @paper, or paper link',
    })
    await userEvent.type(input, '@BERT')
    await userEvent.click(canvas.getByRole('button', { name: papers[1].title }))
    await userEvent.click(canvas.getByRole('button', { name: 'Send' }))
    await expect(args.onSendMessage).toHaveBeenCalledWith(
      `@${papers[1].title}`,
      [papers[0].id, papers[1].id],
    )
  },
}
