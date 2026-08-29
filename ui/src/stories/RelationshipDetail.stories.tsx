import type { Meta, StoryObj } from '@storybook/react-vite'

import { relationships } from '@/_mock/researchData'
import { RelationshipDetail } from '@/components/RelationshipDetail'

const meta = {
  title: 'Research/RelationshipDetail',
  component: RelationshipDetail,
  args: {
    relationship: relationships[0],
  },
  argTypes: {
    relationship: {
      control: 'object',
    },
  },
} satisfies Meta<typeof RelationshipDetail>

export default meta

type Story = StoryObj<typeof meta>

export const Default: Story = {}
