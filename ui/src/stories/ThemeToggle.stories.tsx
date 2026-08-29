import { expect, userEvent, within } from 'storybook/test'
import type { Meta, StoryObj } from '@storybook/react-vite'

import { ThemeToggle } from '@/components/ThemeToggle'
import { TooltipProvider } from '@/components/ui/tooltip'

const meta = {
  title: 'Research/ThemeToggle',
  component: ThemeToggle,
  decorators: [
    (Story) => (
      <TooltipProvider>
        <div className="bg-background p-8 text-foreground">
          <Story />
        </div>
      </TooltipProvider>
    ),
  ],
} satisfies Meta<typeof ThemeToggle>

export default meta
type Story = StoryObj<typeof meta>

export const SystemDefault: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    const toggle = await canvas.findByRole('button', { name: /Use (light|dark) theme/ })
    await expect(toggle).toBeVisible()
    await userEvent.click(toggle)
  },
}
