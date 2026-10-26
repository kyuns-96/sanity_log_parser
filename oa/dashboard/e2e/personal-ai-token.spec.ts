import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const actor = { id: 'synthetic-self', name: '합성 사용자', username: 'synthetic-self', role: 'OWNER' }
const token = 'synthetic.personal.jwt-for-browser-test'

async function setup(page: Page, options: { registered?: boolean; expired?: boolean; soon?: boolean; disabled?: boolean; failSave?: boolean } = {}) {
  const state = { revision: options.registered ? 1 : 0, registered: !!options.registered, writes: [] as { path: string; body: Record<string, unknown> }[], unexpected: [] as string[] }
  const status = () => ({ enabled: !options.disabled, registered: state.registered, revision: state.revision,
    expires_at: state.registered ? new Date(Date.now() + (options.expired ? -1 : options.soon ? 10 : 365) * 86400000).toISOString() : null, expired: !!options.expired, expires_soon: !!options.soon })
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/workbench/session') return route.fulfill({ json: { user: actor, csrf: '', expires_at: '', environment: 'PRODUCTION_SSO' } })
    if (path === '/api/workbench/overview') return route.fulfill({ json: { user: actor, data_origin: 'PRODUCTION', projects: [] } })
    if (path.startsWith('/api/ai/credentials')) {
      if (request.method() === 'POST') {
        const body = request.postDataJSON() as Record<string, unknown>
        state.writes.push({ path, body })
        expect(request.headers()['x-sanity-request']).toBe('1')
        expect(request.headers()['authorization']).toBeUndefined()
        expect(body.expected_revision).toBe(state.revision)
        expect(Object.keys(body).sort()).toEqual(path.endsWith('/remove') ? ['expected_revision'] : ['expected_revision', 'token'])
        if (options.failSave) return route.fulfill({ status: 409, json: { code: 'REVISION_CONFLICT', private_detail: token } })
        state.revision++
        state.registered = !path.endsWith('/remove')
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
  const input = page.getByLabel('JWT 토큰', { exact: true })
  await expect(input).toHaveAttribute('type', 'password')
  await input.fill(token)
  await page.getByRole('button', { name: '토큰 저장', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('토큰을 저장했습니다')
  await expect(page.getByLabel('교체할 JWT 토큰')).toHaveValue('')
  expect(state.writes).toEqual([{ path: '/api/ai/credentials', body: { token, expected_revision: 0 } }])
  expect(await page.locator('body').innerText()).not.toContain(token)
  const stored = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))
  expect(stored).not.toContain(token)
  expect(state.unexpected).toEqual([])
})

test('replacement and removal use current revisions without returning saved tokens', async ({ page }) => {
  const state = await setup(page, { registered: true })
  await expect(page.getByLabel('교체할 JWT 토큰')).toHaveValue('')
  await page.getByLabel('교체할 JWT 토큰').fill(token)
  await page.getByRole('button', { name: '토큰 교체', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('토큰을 저장했습니다')
  await page.getByRole('button', { name: '연결 해제', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('연결을 해제했습니다')
  await expect(page.getByLabel('AI 토큰 등록 상태')).toContainText('미등록')
  await expect(page.getByLabel('JWT 토큰', { exact: true })).toHaveValue('')
  expect(state.writes.map(write => write.body.expected_revision)).toEqual([1, 2])
  expect(state.writes[1].body).not.toHaveProperty('token')
  expect(state.registered).toBe(false)
})

test('near-expiry and expired tokens give explicit renewal guidance', async ({ page }) => {
  await setup(page, { registered: true, expired: true })
  await expect(page.getByRole('alert')).toContainText('토큰이 만료됐습니다')
  await page.screenshot({ path: test.info().outputPath('personal-token-desktop.png') })
})

test('thirty-day expiry warning is shown for a registered personal token', async ({ page }) => {
  await setup(page, { registered: true, soon: true })
  await expect(page.getByRole('status')).toContainText('30일 안에 만료됩니다')
})

test('failed saves clear the token, do not echo server details, and do not auto-retry', async ({ page }) => {
  const state = await setup(page, { failSave: true })
  await page.getByLabel('JWT 토큰', { exact: true }).fill(token)
  await page.getByRole('button', { name: '토큰 저장', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('다른 창에서 토큰 설정이 변경됐습니다')
  await expect(page.getByLabel('JWT 토큰', { exact: true })).toHaveValue('')
  expect(await page.locator('body').innerText()).not.toContain(token)
  expect(state.writes).toHaveLength(1)
})

test('personal credentials are not entered when the service uses server credentials', async ({ page }) => {
  const state = await setup(page, { disabled: true })
  await expect(page.getByRole('dialog')).toContainText('활성화되어 있지 않습니다')
  await expect(page.locator('input[type=password]')).toHaveCount(0)
  expect(state.writes).toHaveLength(0)
})

test('personal token dialog fits a small mobile screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await setup(page)
  await expect(page.getByLabel('JWT 토큰', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '토큰 저장', exact: true })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
  expect(overflow).toBe(false)
  await page.screenshot({ path: test.info().outputPath('personal-token-mobile.png') })
})
