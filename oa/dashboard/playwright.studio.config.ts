import { defineConfig } from '@playwright/test'
import dashboardConfig from './playwright.config'

const port = Number(process.env.SANITY_STUDIO_TEST_PORT || '5199')
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid SANITY_STUDIO_TEST_PORT')
const baseURL = `http://127.0.0.1:${port}`

// Test the deployed HTML entries without the dev server's dependency prebundling.
// npm run test:studio builds both entries before starting this server.
export default defineConfig({
  ...dashboardConfig,
  testMatch: 'studio-workbench.spec.ts',
  use: { ...dashboardConfig.use, baseURL },
  webServer: {
    command: `npm run preview -- --port ${port} --strictPort`,
    url: `${baseURL}/studio.html`,
    reuseExistingServer: false,
  },
})
