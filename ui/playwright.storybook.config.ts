import { defineConfig, devices } from '@playwright/test'

const port = 43118

export default defineConfig({
  testDir: './storybook-e2e',
  timeout: 120_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['line']],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: {
    command: `pnpm exec storybook dev --host 127.0.0.1 --ci -p ${port}`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
})
