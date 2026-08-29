import type { Preview } from '@storybook/react-vite'
import { ThemeProvider } from '../src/components/ThemeProvider'
import '../src/styles/globals.css'

const preview: Preview = {
  decorators: [
    (Story) => (
      <ThemeProvider>
        <Story />
      </ThemeProvider>
    ),
  ],
  parameters: {
    controls: {
      expanded: true,
    },
  },
}

export default preview
