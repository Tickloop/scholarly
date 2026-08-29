import type { Meta, StoryObj } from '@storybook/react-vite'
import { useState } from 'react'
import { expect, fireEvent, fn, userEvent, waitFor, within } from 'storybook/test'

import { papers } from '@/_mock/researchData'
import { Canvas } from '@/components/Canvas'
import { CanvasProvider } from '@/context/CanvasContext'

const drawerPaper = {
  ...papers[0],
  processing_status: 'reviewed',
  plain_language_summary:
    'This paper replaces step-by-step sequence processing with a system that can compare all words at once.',
}

function CanvasWithPaper() {
  const [selectedPaperId, setSelectedPaperId] = useState<string>()
  return (
    <CanvasProvider papers={[drawerPaper]} relationships={[]}>
      <Canvas
        selectedPaperId={selectedPaperId}
        onPaperSelect={setSelectedPaperId}
      />
    </CanvasProvider>
  )
}

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

export const PaperDrawer: Story = {
  render: () => <CanvasWithPaper />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    const card = await waitFor(() =>
      canvas.getByRole('article', { name: drawerPaper.title }),
    )
    await userEvent.hover(card)
    await waitFor(() => {
      expect(
        canvasElement.ownerDocument.body.querySelector('.paper-node__preview'),
      ).toBeTruthy()
    })
    await userEvent.click(card)

    const page = within(canvasElement.ownerDocument.body)
    const drawer = await page.findByRole('dialog')
    await expect(within(drawer).getByText(drawerPaper.title)).toBeVisible()
    await expect(
      within(drawer).getByRole('tab', { name: 'Core idea' }),
    ).toBeVisible()
    await waitFor(() =>
      expect(
        canvasElement.ownerDocument.body.querySelector(
          '.paper-node__preview[data-state="open"]',
        ),
      ).toBeNull(),
    )

    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(page.queryByRole('dialog')).toBeNull())
    await waitFor(() => expect(card).toHaveFocus())
    await expect(canvasElement.ownerDocument.activeElement).toBe(card)
    await expect(
      canvasElement.ownerDocument.body.querySelector(
        '.paper-node__preview[data-state="open"]',
      ),
    ).toBeNull()
  },
}
