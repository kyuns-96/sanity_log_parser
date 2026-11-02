import { defineConfig } from '@playwright/test'
import { availableParallelism } from 'node:os'

const port = Number(process.env.SANITY_DASHBOARD_TEST_PORT || '5173')
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid SANITY_DASHBOARD_TEST_PORT')
const baseURL = `http://127.0.0.1:${port}`
const workers = Number(process.env.SANITY_DASHBOARD_TEST_WORKERS || Math.min(4, availableParallelism()))
if (!Number.isInteger(workers) || workers < 1 || workers > 16) throw new Error('Invalid SANITY_DASHBOARD_TEST_WORKERS')

export default defineConfig({
  testDir: './e2e', fullyParallel: true, workers,
  testIgnore: ['**/workspace-live.spec.ts', '**/codex-*.spec.ts', '**/workbench-drag.spec.ts', '**/workbench-rationale.spec.ts', '**/workbench-guidance.spec.ts'],
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL, viewport: { width: 1440, height: 1100 }, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: { command: `npm run dev -- --port ${port}`, url: baseURL, reuseExistingServer: !process.env.SANITY_DASHBOARD_TEST_PORT },
})
