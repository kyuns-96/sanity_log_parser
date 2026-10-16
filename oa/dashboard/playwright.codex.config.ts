import { defineConfig } from '@playwright/test'

// Uses saved model outputs in an isolated DB. Browser tests never call Codex.
export default defineConfig({
  testDir: './e2e', testMatch: 'codex-mock.spec.ts', workers: 1,
  reporter: [['list'], ['json', { outputFile: 'artifacts/codex-browser-tests.json' }]],
  outputDir: 'test-results/codex',
  use: { baseURL: 'http://127.0.0.1:8003', viewport: { width: 1440, height: 1050 },
    trace: 'retain-on-failure', screenshot: 'only-on-failure' },
})
