import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'

// Public synthetic browser contract only. No company SDK, report or IdP call.
async function installSSO(page: Page) {
  const state = {
    signedIn: false, configStatus: 200, sessionStatus: null as number | null,
    sessionCode: 'UNAUTHORIZED', sessionMethod: 'sso', logoutStatus: 200,
    config: { mode: 'sso', login_url: '/auth/login', logout_url: '/auth/logout', logout_method: 'POST' } as Record<string, unknown>,
    calls: [] as string[], authorization: [] as (string | undefined)[], origins: [] as (string | undefined)[],
    businessCalls: [] as string[],
  }
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    state.calls.push(`${request.method()} ${path}`)
    state.authorization.push(request.headers().authorization)
    if (path === '/api/auth-config') return route.fulfill({ status: state.configStatus, json: state.configStatus === 200 ? state.config : { code: 'AUTH_CONFIG' } })
    if (path === '/api/session') {
      const status = state.sessionStatus ?? (state.signedIn ? 200 : 401)
      return route.fulfill({ status, json: status === 200 ? {
        user_id: 'synthetic-user-101', reviewer: '합성 SSO 사용자', auth_method: state.sessionMethod,
        authorization_ready: false, writable: false, access_mode: 'postgresql',
        notice: 'SSO 사용자 연결 확인 · 프로젝트·블록 권한 연동은 아직 준비되지 않았습니다.',
      } : { code: state.sessionCode } })
    }
    state.businessCalls.push(path)
    return route.fulfill({ status: 503, json: { code: 'AUTHORIZATION_NOT_IMPLEMENTED' } })
  })
  await page.route('**/auth/logout', async route => {
    const request = route.request()
    state.calls.push(`${request.method()} /auth/logout`)
    state.authorization.push(request.headers().authorization)
    state.origins.push(request.headers().origin)
    if (state.logoutStatus !== 200) return route.fulfill({ status: state.logoutStatus, json: { code: 'AUTH_PROVIDER_UNAVAILABLE' } })
    state.signedIn = false
    return route.fulfill({ json: { status: 'LOGGED_OUT' } })
  })
  await page.route('**/auth/login', route => route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<p>합성 로그인 경로</p>' }))
  return state
}

test('SSO discovery restores no local token and login uses top-level navigation', async ({ page }) => {
  const state = await installSSO(page)
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeEnabled()
  await expect(page.getByLabel('접근 토큰')).toHaveCount(0)
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(state.calls).toContain('GET /api/session')
  expect(state.businessCalls).toEqual([])
  expect(state.authorization.every(value => value === undefined)).toBeTruthy()
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0])
  await page.getByRole('button', { name: 'SSO로 로그인', exact: true }).click()
  await expect(page).toHaveURL(/\/auth\/login$/)
  await expect(page.getByText('합성 로그인 경로', { exact: true })).toBeVisible()
})

test('existing SSO session shows the permission boundary and POST logout survives reload', async ({ page, baseURL }) => {
  const state = await installSSO(page)
  state.signedIn = true
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'SSO 연결 확인', exact: true })).toBeVisible()
  await expect(page.getByText('합성 SSO 사용자 님의 인증이 확인되었습니다.', { exact: true })).toBeVisible()
  await expect(page.getByLabel('접근 토큰')).toHaveCount(0)
  expect(state.businessCalls).toEqual([])
  await page.screenshot({ path: 'artifacts/sso-connected-desktop.png', fullPage: true, animations: 'disabled' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.screenshot({ path: 'artifacts/sso-connected-mobile.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '로그아웃', exact: true }).click()
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeVisible()
  expect(state.calls.filter(value => value.endsWith('/auth/logout'))).toEqual(['POST /auth/logout'])
  expect(state.origins).toEqual([new URL(baseURL!).origin])
  expect(state.authorization.every(value => value === undefined)).toBeTruthy()
  await page.reload()
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeVisible()
  expect(state.businessCalls).toEqual([])
})

test('failed server logout stays explicit and is retried only by the user', async ({ page }) => {
  const state = await installSSO(page)
  state.signedIn = true; state.logoutStatus = 503
  await page.goto('/')
  await page.getByRole('button', { name: '로그아웃', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('서버 로그아웃에 실패했습니다.')
  await expect(page.getByRole('heading', { name: 'SSO 연결 확인', exact: true })).toBeVisible()
  expect(state.calls.filter(value => value.endsWith('/auth/logout'))).toHaveLength(1)
  state.logoutStatus = 200
  await page.getByRole('button', { name: '로그아웃', exact: true }).click()
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeVisible()
  expect(state.calls.filter(value => value.endsWith('/auth/logout'))).toHaveLength(2)
})

for (const [status, code] of [[403, 'ACCOUNT_NOT_LINKED'], [403, 'ACCOUNT_DISABLED'], [503, 'AUTH_SSO_NOT_CONFIGURED'], [503, 'AUTH_PROVIDER_UNAVAILABLE']] as const) {
  test(`${code} remains an explicit SSO error without token or business fallback`, async ({ page }) => {
    const state = await installSSO(page)
    state.sessionStatus = status; state.sessionCode = code
    await page.goto('/')
    await expect(page.getByRole('alert')).toContainText(code)
    await expect(page.getByLabel('접근 토큰')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'SSO 연결 확인', exact: true })).toHaveCount(0)
    expect(state.businessCalls).toEqual([])
    expect(state.authorization.every(value => value === undefined)).toBeTruthy()
  })
}

test('configuration failure cannot select token mode and explicit retry recovers', async ({ page }) => {
  const state = await installSSO(page)
  state.configStatus = 503
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('AUTH_CONFIG')
  await expect(page.getByLabel('접근 토큰')).toHaveCount(0)
  expect(state.calls).not.toContain('GET /api/session')
  state.configStatus = 200
  await page.getByRole('button', { name: '연결 상태 다시 확인', exact: true }).click()
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeEnabled()
})

test('unsafe login URLs and mismatched authentication responses stay blocked', async ({ page }) => {
  const state = await installSSO(page)
  state.config.login_url = '//unconfigured.example.invalid/login'
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('AUTH_CONFIG')
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toHaveCount(0)
  expect(state.calls).not.toContain('GET /api/session')
  state.config.login_url = '/auth/login'; state.signedIn = true; state.sessionMethod = 'local_token'
  await page.getByRole('button', { name: '연결 상태 다시 확인', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('AUTH_INVALID_RESULT')
  expect(state.businessCalls).toEqual([])
})

test('an SSO deployment without a login router never offers the local token form', async ({ page }) => {
  const state = await installSSO(page)
  state.config.login_url = null; state.config.logout_url = null; state.config.logout_method = null
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeDisabled()
  await expect(page.getByText('SSO 로그인 경로가 연결되지 않았습니다.', { exact: false })).toBeVisible()
  await expect(page.getByLabel('접근 토큰')).toHaveCount(0)
})
