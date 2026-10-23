import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { makeDemo } from '../src/demo'
import type { AccessCatalog, AccessGrant, AccessRequest } from '../src/accessApi'
import type { Detail } from '../src/types'

// Browser contracts use synthetic fixtures only; no company account, DB or SDK.
async function accessServer(page: Page, admin = false, empty = false) {
  const fallback = makeDemo()
  const state = {
    admin, adminForbidden: false, readable: false, resourceWritable: false, denyResource: false, denialStatus: 404, denialCode: 'RESOURCE_NOT_FOUND', revision: 0,
    catalog: (empty ? { projects: [], blocks: [] } : {
      projects: [{ id: 'project-alpha', name: 'Alpha 프로젝트', description: '합성 프로젝트' }],
      blocks: [{ id: 'block-cpu', project_id: 'project-alpha', name: 'CPU 블록' }],
    }) as AccessCatalog,
    grants: [] as AccessGrant[], requests: [] as AccessRequest[], calls: [] as string[],
    writes: [] as { path: string; body: Record<string, unknown>; marker?: string; authorization?: string }[],
  }
  await page.route('**/api/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const body = request.method() === 'POST' ? request.postDataJSON() as Record<string, unknown> : undefined
    state.calls.push(`${request.method()} ${path}`)
    if (body) state.writes.push({ path, body, marker: request.headers()['x-sanity-request'], authorization: request.headers().authorization })
    if (path === '/api/auth-config') return route.fulfill({ json: { mode: 'sso', login_url: '/auth/login', logout_url: '/auth/logout', logout_method: 'POST' } })
    if (path === '/api/session') return route.fulfill({ json: {
      user_id: 'synthetic-user', reviewer: '합성 사용자', auth_method: 'sso', authorization_ready: true,
      is_admin: state.admin, can_read_business: state.readable, can_receive: false,
      writable: state.grants.some(grant => grant.role === 'OWNER'), access_mode: 'postgresql', notice: '',
    } })
    if (path === '/api/access/catalog') return route.fulfill({ json: state.catalog })
    if (path === '/api/access/me') return route.fulfill({ json: { user_id: 'synthetic-user', is_admin: state.admin, revision: state.revision, grants: state.grants, requests: state.requests } })
    if (path === '/api/admin/access') return state.adminForbidden
      ? route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } })
      : route.fulfill({ json: { revision: state.revision, requests: state.requests, grants: state.grants, users: [{ user_id: 'synthetic-user', display_name: '합성 사용자' }] } })
    if (body && path === '/api/admin/projects') {
      state.catalog.projects.push({ id: String(body.project_id), name: String(body.name), description: String(body.description) })
      state.revision++
      return route.fulfill({ json: { status: 'CREATED' } })
    }
    if (body && /^\/api\/admin\/projects\/[^/]+\/blocks$/.test(path)) {
      state.catalog.blocks.push({ id: String(body.block_id), project_id: path.split('/')[4], name: String(body.name) })
      state.revision++
      return route.fulfill({ json: { status: 'CREATED' } })
    }
    if (body && path === '/api/access/requests') {
      state.requests.push({ request_id: String(body.request_id), user_id: 'synthetic-user', role: body.role as 'TOP' | 'OWNER', project_id: String(body.project_id), block_id: body.block_id as string | null, reason: String(body.reason), status: 'PENDING', created_at: '2026-10-22T00:00:00Z' })
      state.revision++
      return route.fulfill({ json: { status: 'REQUESTED' } })
    }
    if (body && path.endsWith('/decision')) {
      const target = state.requests.find(item => item.request_id === path.split('/')[4])!
      expect(body.expected_revision).toBe(state.revision)
      target.status = body.decision === 'APPROVE' ? 'APPROVED' : 'REJECTED'
      target.decision_reason = String(body.reason)
      target.decided_by = 'synthetic-user'
      if (body.decision === 'APPROVE') {
        state.grants.push({ grant_id: 'grant-approved', user_id: target.user_id, role: target.role, project_id: target.project_id, block_id: target.block_id })
        state.readable = true
      }
      state.revision++
      return route.fulfill({ json: { status: target.status } })
    }
    if (body && path.endsWith('/revoke')) {
      expect(body.expected_revision).toBe(state.revision)
      state.grants = state.grants.filter(grant => grant.grant_id !== path.split('/')[4])
      state.readable = !!state.grants.length
      state.revision++
      return route.fulfill({ json: { status: 'REVOKED' } })
    }
    if (state.denyResource && (path.startsWith('/api/bundles/') || path.startsWith('/api/evidence/'))) return route.fulfill({ status: state.denialStatus, json: { code: state.denialCode } })
    const value = await fallback(path + url.search, body)
    if (/^\/api\/bundles\/[^/]+$/.test(path)) {
      const detail = value as Detail
      detail.permissions = { can_review: state.resourceWritable, can_cluster: state.resourceWritable, can_request_evidence: state.resourceWritable, can_receive_evidence: state.resourceWritable }
    }
    return route.fulfill({ json: value })
  })
  return state
}

test('new SSO account requests its block without fetching business data or borrowing demo access', async ({ page }) => {
  const state = await accessServer(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '내 프로젝트·권한', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '관리자', exact: true })).toHaveCount(0)
  await page.getByLabel('프로젝트', { exact: true }).selectOption('project-alpha')
  await page.getByLabel('블록', { exact: true }).selectOption('block-cpu')
  await page.getByLabel('요청 사유', { exact: true }).fill('합성 CPU 블록 검토 담당')
  await page.getByRole('button', { name: '권한 요청 보내기', exact: true }).click()
  await expect(page.getByText('승인 대기', { exact: true })).toBeVisible()
  expect(state.writes[0]).toMatchObject({ path: '/api/access/requests', marker: '1', authorization: undefined, body: { role: 'OWNER', project_id: 'project-alpha', block_id: 'block-cpu' } })
  expect(state.calls.some(call => call.includes('/api/bundles'))).toBeFalsy()
  expect(state.calls.some(call => call.includes('/workspace'))).toBeFalsy()
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0])
  await page.goto('/?page=admin')
  await expect(page.getByRole('heading', { name: '내 프로젝트·권한', exact: true })).toBeVisible()
  expect(state.calls).not.toContain('GET /api/admin/access')
})

test('administrator creates project and block, approves own request, and revokes the separate business grant', async ({ page }) => {
  const state = await accessServer(page, true, true)
  await page.goto('/?page=admin')
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '검토 대장', exact: true })).toHaveCount(0)
  await page.getByLabel('프로젝트 ID', { exact: true }).fill('project-alpha')
  await page.getByLabel('프로젝트 이름', { exact: true }).fill('Alpha 프로젝트')
  await page.getByRole('button', { name: '프로젝트 등록', exact: true }).click()
  await expect(page.getByText('프로젝트를 등록했습니다.', { exact: false })).toBeVisible()
  await page.getByLabel('프로젝트', { exact: true }).selectOption('project-alpha')
  await page.getByLabel('블록 ID', { exact: true }).fill('block-cpu')
  await page.getByLabel('블록 이름', { exact: true }).fill('CPU 블록')
  await page.getByRole('button', { name: '블록 등록', exact: true }).click()
  await expect(page.getByText('블록을 등록했습니다.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '내 프로젝트·권한', exact: true }).click()
  await expect(page).toHaveURL(/\?page=welcome$/)
  await page.getByLabel('프로젝트', { exact: true }).selectOption('project-alpha')
  await page.getByLabel('블록', { exact: true }).selectOption('block-cpu')
  await page.getByLabel('요청 사유', { exact: true }).fill('본인 CPU 담당 권한 요청')
  await page.getByRole('button', { name: '권한 요청 보내기', exact: true }).click()
  await expect(page.getByText('승인 대기', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '관리자', exact: true }).click()
  await expect(page.getByText('내 요청', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '승인', exact: true }).click()
  await page.getByLabel('처리 사유', { exact: true }).fill('담당 범위 확인 후 승인')
  await page.getByRole('button', { name: '승인 확정', exact: true }).click()
  await expect(page.getByText('권한 변경을 저장했습니다.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '검토 대장', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '권한 회수', exact: true }).click()
  await page.getByLabel('처리 사유', { exact: true }).fill('담당 해제')
  await page.getByRole('button', { name: '회수 확정', exact: true }).click()
  await expect(page.getByRole('button', { name: '검토 대장', exact: true })).toHaveCount(0)
  expect(state.grants).toEqual([])
  expect(state.writes.every(write => write.marker === '1' && write.authorization === undefined)).toBeTruthy()
  expect(state.calls.some(call => call.includes('/api/bundles'))).toBeFalsy()
  await page.reload()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
})

test('an OWNER elsewhere cannot edit a TOP-only bundle or receive the global inbox', async ({ page }) => {
  const state = await accessServer(page)
  state.readable = true
  state.grants = [{ grant_id: 'grant-other-owner', user_id: 'synthetic-user', role: 'OWNER', project_id: 'project-other', block_id: 'block-other' }]
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'sanity-20261012-042', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '검토 의견 기록', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '근거 요청', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '결과 수신', exact: true })).toHaveCount(0)
  state.resourceWritable = true
  await page.getByRole('button', { name: '새로고침', exact: true }).click()
  await expect(page.getByRole('button', { name: '검토 의견 기록', exact: true })).toBeEnabled()
  await expect(page.getByRole('button', { name: '근거 요청', exact: true })).toBeEnabled()
})

for (const denialStatus of [404, 403]) test(`scope revocation removes displayed business evidence and preserves the SSO session on ${denialStatus}`, async ({ page }) => {
  const state = await accessServer(page)
  state.readable = true
  state.resourceWritable = true
  state.grants = [{ grant_id: 'grant-owner', user_id: 'synthetic-user', role: 'OWNER', project_id: 'project-alpha', block_id: 'block-cpu' }]
  await page.goto('/')
  await expect(page.getByText('CHECK-101', { exact: true })).toBeVisible()
  state.readable = false; state.grants = []; state.denyResource = true
  state.denialStatus = denialStatus; state.denialCode = denialStatus === 403 ? 'FORBIDDEN' : 'RESOURCE_NOT_FOUND'
  await page.getByRole('button', { name: '새로고침', exact: true }).click()
  await expect(page.getByRole('heading', { name: '내 프로젝트·권한', exact: true })).toBeVisible()
  await expect(page.getByText('CHECK-101', { exact: true })).toHaveCount(0)
  await expect(page.getByText('SSO 로그인됨', { exact: true })).toBeVisible()
  await expect(page.getByLabel('접근 토큰')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '권한 요청 보내기', exact: true })).toBeVisible()
})

test('permission denial on administration keeps a usable authenticated welcome page', async ({ page }) => {
  const state = await accessServer(page, true)
  state.adminForbidden = true
  await page.goto('/?page=admin')
  await expect(page.getByRole('alert')).toContainText('FORBIDDEN')
  await page.getByRole('button', { name: '내 프로젝트·권한', exact: true }).click()
  await expect(page.getByRole('heading', { name: '내 프로젝트·권한', exact: true })).toBeVisible()
  await expect(page.getByText('SSO 로그인됨', { exact: true })).toBeVisible()
})

test('welcome and administrator share the Studio neutral theme without mobile overflow', async ({ page }) => {
  await accessServer(page, true)
  await page.goto('/?page=welcome')
  await expect(page.getByRole('heading', { name: '권한 요청', exact: true })).toBeVisible()
  await page.screenshot({ path: 'artifacts/rbac-welcome-desktop.png', fullPage: true, animations: 'disabled' })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole('heading', { name: '권한 요청', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--accent').trim())).toBe('#272b35')
  await page.screenshot({ path: 'artifacts/rbac-welcome-mobile.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '관리자', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.screenshot({ path: 'artifacts/rbac-admin-mobile.png', fullPage: true, animations: 'disabled' })
})
