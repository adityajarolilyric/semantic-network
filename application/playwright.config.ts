import { defineConfig } from '@playwright/test'

const apiPort = Number(process.env.METADATA_UI_TEST_API_PORT || 8798)
const uiPort = Number(process.env.METADATA_UI_TEST_PORT || 5184)
const apiUrl = `http://127.0.0.1:${apiPort}`

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${uiPort}`,
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: '../backend/.venv/bin/python ../backend/scripts/serve_sources_test.py',
      url: apiUrl + '/health/live',
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npm run dev -- --port ${uiPort}`,
      url: `http://127.0.0.1:${uiPort}`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { METADATA_API_TARGET: apiUrl },
    },
  ],
})
