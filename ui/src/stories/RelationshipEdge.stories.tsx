import { useState } from 'react'
import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect, fn, userEvent, waitFor, within } from 'storybook/test'

import { papers, relationships } from '@/_mock/researchData'
import { Canvas } from '@/components/Canvas'
import { CanvasProvider } from '@/context/CanvasContext'

const editRelationship = fn()

function RelationshipEdgeFixture() {
  const [selectedRelationshipId, setSelectedRelationshipId] = useState<string>()

  return (
    <CanvasProvider
      papers={papers}
      relationships={relationships}
      editRelationship={editRelationship}
    >
      <Canvas
        selectedRelationshipId={selectedRelationshipId}
        onRelationshipSelect={setSelectedRelationshipId}
      />
    </CanvasProvider>
  )
}

const meta = {
  title: 'Research/RelationshipEdge',
  component: RelationshipEdgeFixture,
} satisfies Meta<typeof RelationshipEdgeFixture>

export default meta
type Story = StoryObj<typeof meta>

export const KeyboardEdit: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    const edge = await waitFor(() =>
      canvas.getByRole('button', {
        name: `Relationship: ${relationships[0].label}`,
      }),
    )
    edge.focus()
    await userEvent.keyboard('{Enter}')
    const form = await waitFor(() =>
      canvas.getByRole('form', {
        name: `Edit relationship: ${relationships[0].label}`,
      }),
    )
    const label = within(form).getByRole('textbox', { name: 'Label' })
    await userEvent.clear(label)
    await userEvent.type(label, 'Keyboard-edited relationship{Enter}')

    await expect(editRelationship).toHaveBeenCalledWith(
      relationships[0].id,
      expect.objectContaining({ label: 'Keyboard-edited relationship' }),
    )
    await expect(
      canvas.getByRole('complementary', {
        name: `Relationship detail: ${relationships[0].label}`,
      }),
    ).toBeVisible()
  },
}
