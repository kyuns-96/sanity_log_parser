import { defineConfig } from '@playwright/test'

// Dedicated retained synthetic QA workspace. No external AI calls.
export default defineConfig({
  testDir: './e2e', testMatch: 'workbench-drag.spec.ts', workers: 1,
  reporter: [['list'], ['json', { outputFile: 'artifacts/drag-browser-tests.json' }]],
  outputDir: 'test-results/drag',
  use: { baseURL: 'http://127.0.0.1:8003', viewport: { width: 1440, height: 1050 },
    trace: 'retain-on-failure', screenshot: 'only-on-failure' },
})
