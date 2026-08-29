import { expect, fn, userEvent, within } from 'storybook/test'
import type { Meta, StoryObj } from '@storybook/react-vite'

import { CanvasDrawer } from '@/components/CanvasDrawer'

const canvases = [
  {
    id: 'canvas-1',
    name: 'Transformer history',
    research_goal: 'Map transformer research.',
    build_status: 'completed',
    created_at: '2026-08-29T00:00:00Z',
    updated_at: '2026-08-29T00:00:00Z',
  },
  {
    id: 'canvas-2',
    name: 'Diffusion models',
    research_goal: 'Map diffusion research.',
    build_status: 'running',
    created_at: '2026-08-29T00:00:00Z',
    updated_at: '2026-08-29T00:00:00Z',
  },
]

const meta = {
  title: 'Research/CanvasDrawer',
  component: CanvasDrawer,
  args: {
    canvases,
    selectedCanvasId: 'canvas-1',
    isBusy: false,
    onSelect: fn(),
    onRename: fn(),
    onDelete: fn(),
  },
} satisfies Meta<typeof CanvasDrawer>

export default meta
type Story = StoryObj<typeof meta>

export const Selected: Story = {
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement)
    await expect(
      canvas.getByRole('navigation', { name: 'Canvas switcher' }),
    ).toBeVisible()
    await userEvent.click(canvas.getByText('Canvases'))
    const input = canvas.getByRole('textbox', { name: 'Canvas name' })
    await userEvent.clear(input)
    await userEvent.type(input, 'Renamed canvas')
    await userEvent.click(canvas.getByRole('button', { name: 'Rename' }))
    await expect(args.onRename).toHaveBeenCalledWith(
      'canvas-1',
      'Renamed canvas',
    )
  },
}

export const NewCanvas: Story = {
  args: { selectedCanvasId: undefined },
}
