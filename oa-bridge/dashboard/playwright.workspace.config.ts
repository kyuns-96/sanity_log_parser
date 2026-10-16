import { defineConfig } from '@playwright/test'

// The separate, initialized integration server uses its own disposable PostgreSQL.
export default defineConfig({
  testDir: './e2e', testMatch: 'workspace-live.spec.ts', fullyParallel: false, workers: 1,
  // Whole-partition validation and DB reads of the large fixtures can exceed 5s.
  expect: { timeout: 30000 },
  reporter: [['list'], ['html', { outputFolder: 'playwright-report/workspace', open: 'never' }], ['json', { outputFile: 'artifacts/workspace-browser-tests.json' }]],
  outputDir: 'test-results/workspace',
  use: { baseURL: 'http://127.0.0.1:8002', viewport: { width: 1440, height: 1050 },
    trace: 'retain-on-failure', screenshot: 'only-on-failure' },
})
