import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect, within } from 'storybook/test'

import { papers } from '@/_mock/researchData'
import { PaperDetail } from '@/components/PaperDetail'

const meta = {
  title: 'Research/PaperDetail',
  component: PaperDetail,
  args: {
    paper: papers[0],
    review: papers[0].review!,
  },
  argTypes: {
    paper: {
      control: 'object',
    },
    review: {
      control: 'object',
    },
  },
} satisfies Meta<typeof PaperDetail>

export default meta

type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const UnknownDate: Story = {
  args: {
    paper: { ...papers[0], year: null, month: null },
  },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText('Date unknown')).toBeVisible()
  },
}
