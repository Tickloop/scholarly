import { afterEach, expect, test } from 'vitest'

import viteConfig from '../vite.config'

const originalTarget = process.env.VITE_API_PROXY_TARGET

afterEach(() => {
  if (originalTarget === undefined) delete process.env.VITE_API_PROXY_TARGET
  else process.env.VITE_API_PROXY_TARGET = originalTarget
})

test('uses the API proxy target supplied by the development launcher', () => {
  process.env.VITE_API_PROXY_TARGET = 'http://127.0.0.1:18100'
  const config =
    typeof viteConfig === 'function'
      ? viteConfig({ command: 'serve', mode: 'test', isSsrBuild: false, isPreview: false })
      : viteConfig

  expect(config.server?.proxy?.['/api']).toBe('http://127.0.0.1:18100')
})
