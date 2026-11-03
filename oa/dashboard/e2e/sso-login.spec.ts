import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'

// Public synthetic browser contract only. No company SDK, report or IdP call.
async function installSSO(page: Page) {
  const state = {
    signedIn: false, sessionMethod: 'sso',
    config: { mode: 'sso', login_url: '/auth/login', logout_url: '/auth/logout', logout_method: 'POST' } as Record<string, unknown>,
    calls: [] as string[], authorization: [] as (string | undefined)[], origins: [] as (string | undefined)[],
    businessCalls: [] as string[],
  }
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    state.calls.push(`${request.method()} ${path}`)
    state.authorization.push(request.headers().authorization)
    if (path === '/api/auth-config') return route.fulfill({ json: state.config })
    if (path === '/api/session') {
      const status = state.signedIn ? 200 : 401
      return route.fulfill({ status, json: status === 200 ? {
        user_id: 'synthetic-user-101', reviewer: '합성 SSO 사용자', auth_method: state.sessionMethod,
        authorization_ready: false, writable: false, access_mode: 'postgresql',
        notice: 'SSO 사용자 연결 확인 · 프로젝트·블록 권한 연동은 아직 준비되지 않았습니다.',
      } : { code: 'UNAUTHORIZED' } })
    }
    state.businessCalls.push(path)
    return route.fulfill({ status: 503, json: { code: 'AUTHORIZATION_NOT_IMPLEMENTED' } })
  })
  await page.route('**/auth/logout', async route => {
    const request = route.request()
    state.calls.push(`${request.method()} /auth/logout`)
    state.authorization.push(request.headers().authorization)
    state.origins.push(request.headers().origin)
    state.signedIn = false
    return route.fulfill({ json: { status: 'LOGGED_OUT' } })
  })
  return state
}

test('existing SSO session shows the permission boundary and POST logout survives reload', async ({ page, baseURL }) => {
  const state = await installSSO(page)
  state.signedIn = true
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'SSO 연결 확인', exact: true })).toBeVisible()
  await expect(page.getByText('합성 SSO 사용자 님의 인증이 확인되었습니다.', { exact: true })).toBeVisible()
  await expect(page.getByLabel('접근 토큰')).toHaveCount(0)
  expect(state.businessCalls).toEqual([])
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.getByRole('button', { name: '로그아웃', exact: true }).click()
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeVisible()
  expect(state.calls.filter(value => value.endsWith('/auth/logout'))).toEqual(['POST /auth/logout'])
  expect(state.origins).toEqual([new URL(baseURL!).origin])
  expect(state.authorization.every(value => value === undefined)).toBeTruthy()
  await page.reload()
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeVisible()
  expect(state.businessCalls).toEqual([])
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
