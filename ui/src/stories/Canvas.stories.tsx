import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect, fireEvent, fn, waitFor } from 'storybook/test'

import { Canvas } from '@/components/Canvas'
import { CanvasProvider } from '@/context/CanvasContext'

const meta = {
  title: 'Research/Canvas',
  component: Canvas,
  args: {
    onPaperLinkPaste: fn(),
  },
  render: (args) => (
    <CanvasProvider papers={[]} relationships={[]}>
      <Canvas {...args} />
    </CanvasProvider>
  ),
} satisfies Meta<typeof Canvas>

export default meta
type Story = StoryObj<typeof meta>

export const PastePaperAtPointer: Story = {
  play: async ({ args, canvasElement }) => {
    const flow = await waitFor(() => {
      const element = canvasElement.querySelector('.react-flow')
      expect(element).toBeTruthy()
      return element as HTMLElement
    })
    await waitFor(() => {
      expect(flow.querySelector('.react-flow__viewport')).toBeTruthy()
    })
    await waitFor(() => {
      expect(canvasElement.querySelector('[data-ready="true"]')).toBeTruthy()
    })
    fireEvent.pointerMove(flow, { clientX: 240, clientY: 180 })
    const clipboardData = new DataTransfer()
    clipboardData.setData('text/plain', 'https://arxiv.org/abs/2005.11401')
    canvasElement.ownerDocument.dispatchEvent(
      new ClipboardEvent('paste', {
        bubbles: true,
        cancelable: true,
        clipboardData,
      }),
    )

    await waitFor(() =>
      expect(args.onPaperLinkPaste).toHaveBeenCalledWith(
        'https://arxiv.org/abs/2005.11401',
        expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }),
      ),
    )
  },
}
