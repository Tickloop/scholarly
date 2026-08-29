import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect, userEvent, within } from 'storybook/test'

import { papers } from '@/_mock/researchData'
import { PaperDetail } from '@/components/PaperDetail'

const paper = {
  ...papers[0],
  plain_language_summary:
    'This paper replaces step-by-step sequence processing with a system that can compare all words at once.',
}

const meta = {
  title: 'Research/PaperDetail',
  component: PaperDetail,
  args: {
    paper,
    review: paper.review!,
  },
  decorators: [
    (Story) => (
      <div style={{ width: 480, height: 720, display: 'flex' }}>
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof PaperDetail>

export default meta

type Story = StoryObj<typeof meta>

export const Default: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    await expect(canvas.getByText(paper.plain_language_summary)).toBeVisible()

    const coreTab = canvas.getByRole('tab', { name: 'Core idea' })
    coreTab.focus()
    await userEvent.keyboard('{ArrowRight}')

    await expect(canvas.getByRole('tab', { name: 'Results' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    await expect(canvas.getByText(paper.review!.data)).toBeVisible()
    await expect(canvas.getByText(paper.review!.limitations)).toBeVisible()
  },
}

export const EmptyReview: Story = {
  args: { review: undefined },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    await userEvent.click(canvas.getByRole('tab', { name: 'Results' }))
    await expect(
      canvas.getAllByText('The paper does not report this.').length,
    ).toBeGreaterThan(0)
  },
}
