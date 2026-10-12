import { defineConfig } from '@playwright/test'

const target = process.env.E2E_TARGET ?? 'mock'
if (target !== 'mock' && target !== 'live') {
  throw new Error(`Invalid E2E_TARGET=${target}. Use mock or live.`)
}

export default defineConfig({
  testDir: './tests',
  testIgnore: target === 'mock' ? '**/news-live.spec.ts' : undefined,
  globalSetup: './tests/global-setup.ts',
  outputDir: `./test-results/artifacts/${target}`,
  fullyParallel: false,
  workers: 2,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [
    ['list'],
    ['html', { outputFolder: `test-results/report/${target}`, open: 'never' }],
  ],
  use: {
    baseURL: 'http://127.0.0.1:5187',
    browserName: 'chromium',
    colorScheme: 'dark',
    trace: 'on',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', testIgnore: '**/news-live.spec.ts', use: { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true } },
  ],
  webServer: {
    command: `bun run ${target === 'mock' ? 'dev:mock' : 'dev'} --host 127.0.0.1 --port 5187 --strictPort`,
    url: 'http://127.0.0.1:5187',
    reuseExistingServer: false,
    timeout: 30_000,
    env: { VITE_MOCK_API: target === 'mock' ? '1' : '0' },
  },
})
