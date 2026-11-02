import { defineConfig } from '@playwright/test'

// The retained synthetic QA server; these scenarios never call an external model.
export default defineConfig({
  testDir: './e2e', testMatch: 'workbench-guidance.spec.ts', workers: 1,
  timeout: 60000, expect: { timeout: 10000 },
  reporter: [['list']],
  outputDir: 'test-results/guidance',
  use: { baseURL: 'http://127.0.0.1:8003', viewport: { width: 1440, height: 1050 },
    trace: 'retain-on-failure', screenshot: 'only-on-failure' },
})
