import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const actor = { id: 'synthetic-self', name: '합성 사용자', username: 'synthetic-self', role: 'OWNER' }
const token = 'synthetic.personal.jwt-for-browser-test'
const connectionId = '20000000-0000-4000-8000-000000000010'
const credentialPath = `/api/ai/connections/${connectionId}/credentials`
const tokenInput = '합성 AI 연결 개인 Bearer 토큰'

async function setup(page: Page) {
  const state = { revision: 0, registered: false, writes: [] as { path: string; body: Record<string, unknown> }[], unexpected: [] as string[] }
  const status = () => ({ enabled: true, registered: state.registered, revision: state.revision,
    expires_at: state.registered ? new Date(Date.now() + 365 * 86400000).toISOString() : null,
    expired: false, expires_soon: false, issuance_url: null })
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/ai/connections') return route.fulfill({ json: { items: [
      { id: connectionId, label: '합성 AI 연결', enabled: true, auth_mode: 'bearer', model: 'model-a', models: ['model-a'], issuance_url: null, credential: status(), credential_error: null },
    ] } })
    if (path === '/api/workbench/session') return route.fulfill({ json: { user: actor, csrf: '', expires_at: '', environment: 'PRODUCTION_SSO' } })
    if (path === '/api/workbench/overview') return route.fulfill({ json: { user: actor, data_origin: 'PRODUCTION', projects: [] } })
    if (path === credentialPath) {
      if (request.method() === 'POST') {
        const body = request.postDataJSON() as Record<string, unknown>
        state.writes.push({ path, body })
        expect(request.headers()['x-sanity-request']).toBe('1')
        expect(request.headers()['authorization']).toBeUndefined()
        expect(body.expected_revision).toBe(state.revision)
        expect(Object.keys(body).sort()).toEqual(['expected_revision', 'token'])
        state.revision++
        state.registered = true
      }
      return route.fulfill({ json: status(), headers: { 'Cache-Control': 'no-store' } })
    }
    state.unexpected.push(path)
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } })
  })
  await page.goto('/?page=workbench')
  await page.getByRole('button', { name: '개인 AI 토큰', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  return state
}

test('personal JWT is masked, saved only for self, and never retained in browser storage', async ({ page }) => {
  const state = await setup(page)
  const input = page.getByLabel(tokenInput, { exact: true })
  await expect(input).toHaveAttribute('type', 'password')
  await input.fill(token)
  await page.getByRole('button', { name: '인증값 저장', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('개인 인증값을 저장했습니다')
  await expect(page.getByLabel(tokenInput, { exact: true })).toHaveValue('')
  expect(state.writes).toEqual([{ path: credentialPath, body: { token, expected_revision: 0 } }])
  expect(await page.locator('body').innerText()).not.toContain(token)
  const stored = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))
  expect(stored).not.toContain(token)
  expect(state.unexpected).toEqual([])
})
