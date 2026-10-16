import { defineConfig } from '@playwright/test'
import base from './playwright.codex.config'

if (process.env.SANITY_CODEX_LIVE_TEST !== '1') throw new Error('Explicit SANITY_CODEX_LIVE_TEST=1 is required for a real model call')

export default defineConfig({
  ...base, testMatch: 'codex-button-live.spec.ts', timeout: 240_000, retries: 0,
  reporter: [['list'], ['json', { outputFile: 'artifacts/codex-button-live-browser.json' }]],
  outputDir: 'test-results/codex-button-live',
})
