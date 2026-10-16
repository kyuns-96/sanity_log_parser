import { defineConfig } from '@playwright/test'

// Retained synthetic QA only. These scenarios never invoke an external model.
export default defineConfig({
  testDir: './e2e', testMatch: 'workbench-rationale.spec.ts', workers: 1,
  timeout: 60000, expect: { timeout: 10000 },
  reporter: [['list'], ['json', { outputFile: 'artifacts/rationale-browser-tests.json' }]],
  outputDir: 'test-results/rationale',
  use: { baseURL: 'http://127.0.0.1:8003', viewport: { width: 1440, height: 1050 },
    trace: 'retain-on-failure', screenshot: 'only-on-failure' },
})
