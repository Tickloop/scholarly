import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect, userEvent, waitFor, within } from 'storybook/test'

import { papers, relationships } from '@/_mock/researchData'
import { Canvas } from '@/components/Canvas'
import { CanvasProvider } from '@/context/CanvasContext'

function RelationshipEdgeFixture() {
  return (
    <CanvasProvider papers={papers} relationships={relationships}>
      <Canvas />
    </CanvasProvider>
  )
}

const meta = {
  title: 'Research/RelationshipEdge',
  component: RelationshipEdgeFixture,
} satisfies Meta<typeof RelationshipEdgeFixture>

export default meta
type Story = StoryObj<typeof meta>

export const HoverExplanation: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    const edge = await waitFor(() =>
      canvas.getByRole('button', {
        name: `Relationship: ${relationships[0].label}`,
      }),
    )
    await userEvent.hover(edge)
    await waitFor(() =>
      expect(
        within(canvasElement.ownerDocument.body).getByText(
          relationships[0].explanation,
        ),
      ).toBeVisible(),
    )
  },
}

export const KeyboardExplanation: Story = {
  play: async ({ canvasElement }) => {
    const edge = await waitFor(() =>
      within(canvasElement).getByRole('button', {
        name: `Relationship: ${relationships[0].label}`,
      }),
    )
    edge.focus()
    await waitFor(() =>
      expect(
        within(canvasElement.ownerDocument.body).getByText(
          relationships[0].explanation,
        ),
      ).toBeVisible(),
    )

    await userEvent.keyboard('{Enter}')
    await expect(edge).toHaveFocus()
    await expect(
      within(canvasElement).queryByRole('form', {
        name: `Edit relationship: ${relationships[0].label}`,
      }),
    ).toBeNull()
  },
}
