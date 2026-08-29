import { useState } from 'react'
import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect, userEvent, waitFor, within } from 'storybook/test'

import { papers } from '@/_mock/researchData'
import { Canvas } from '@/components/Canvas'
import { CanvasProvider } from '@/context/CanvasContext'

function KeyboardCanvasFixture() {
  const [selectedPaperId, setSelectedPaperId] = useState<string>()

  return (
    <CanvasProvider papers={[papers[0]]} relationships={[]}>
      <Canvas
        selectedPaperId={selectedPaperId}
        onPaperSelect={setSelectedPaperId}
        onRelayout={() => undefined}
      />
    </CanvasProvider>
  )
}

const meta = {
  title: 'Research/CanvasKeyboard',
  component: KeyboardCanvasFixture,
} satisfies Meta<typeof KeyboardCanvasFixture>

export default meta
type Story = StoryObj<typeof meta>

export const SelectAndOpenPaperControls: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    const page = within(canvasElement.ownerDocument.body)
    const node = await waitFor(() =>
      canvas.getByRole('article', { name: papers[0].title }),
    )
    await expect(canvas.getByRole('button', { name: 'Relayout' })).toBeVisible()
    node.focus()
    await userEvent.keyboard('{Enter}')

    const drawer = await page.findByRole('dialog')
    await expect(within(drawer).getByText(papers[0].title)).toBeVisible()
    await expect(
      within(drawer).getByRole('tab', { name: 'Core idea' }),
    ).toBeVisible()
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(page.queryByRole('dialog')).toBeNull())
    await waitFor(() => expect(node).toHaveFocus())
    await expect(canvasElement.ownerDocument.activeElement).toBe(node)
    await expect(
      canvasElement.ownerDocument.body.querySelector(
        '.paper-node__preview[data-state="open"]',
      ),
    ).toBeNull()
  },
}
