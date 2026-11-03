import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { makeDemo } from '../src/demo'

// Synthetic UI contracts: no company accounts, models or database are used.
const profile = { id: 'synthetic-vwp', label: '합성 프로필', api: 'chat_completions',
  models: ['vwp-fast', 'vwp-review'], auth_mode: 'bearer', timeout: 120,
  max_input_bytes: 1048576, max_output_bytes: 2097152, sha256: 'a'.repeat(64) }

type Entry = { id: string; revision: number; config: Record<string, any>; client_key_registered: boolean }

async function adminFixture(page: Page, initial: Entry[]) {
  const fallback = makeDemo()
  const state = { entries: initial, writes: [] as Record<string, any>[], deletePaths: [] as string[] }
  await page.route('**/api/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname
    if (path === '/api/auth-config') return route.fulfill({ json: { mode: 'sso', login_url: '/auth/login', logout_url: '/auth/logout', logout_method: 'POST' } })
    if (path === '/api/session') return route.fulfill({ json: { user_id: 'synthetic-user', reviewer: '합성 사용자', auth_method: 'sso', authorization_ready: true, is_admin: true, can_read_business: false, can_receive: false, writable: false, access_mode: 'postgresql', notice: '' } })
    if (path === '/api/access/catalog') return route.fulfill({ json: { projects: [], blocks: [] } })
    if (path === '/api/access/me') return route.fulfill({ json: { user_id: 'synthetic-user', is_admin: true, revision: 0, grants: [], requests: [] } })
    if (path === '/api/admin/access') return route.fulfill({ json: { revision: 0, requests: [], grants: [], users: [] } })
    if (path === '/api/admin/ai/task-defaults' && request.method() === 'GET') {
      return route.fulfill({ json: { revision: 0, defaults: { CLUSTER: null, REVIEW: null } } })
    }
    if (path === '/api/admin/ai/scheduler' && request.method() === 'GET') {
      return route.fulfill({ json: { revision: 0, limits: { max_concurrency: 8, max_jobs: 4 } } })
    }
    if (path === '/api/admin/ai/connections' && request.method() === 'GET') {
      return route.fulfill({ json: { items: state.entries, credentials_ready: true, vwp_ready: true, vwp_profiles: [profile] } })
    }
    if (path.startsWith('/api/admin/ai/connections/') && path.endsWith('/delete')) {
      const body = request.postDataJSON()
      const id = path.split('/').at(-2)!
      const old = state.entries.find(entry => entry.id === id)!
      expect(Object.keys(body)).toEqual(['expected_revision'])
      expect(request.headers()['x-sanity-request']).toBe('1')
      expect(body.expected_revision).toBe(old.revision)
      state.writes.push(body); state.deletePaths.push(path)
      state.entries.splice(state.entries.indexOf(old), 1)
      return route.fulfill({ json: { id, revision: old.revision + 1, deleted: true } })
    }
    const body = request.method() === 'POST' ? request.postDataJSON() : undefined
    return route.fulfill({ json: await fallback(path + url.search, body) })
  })
  return state
}

async function openPanel(page: Page) {
  await page.goto('/?page=admin')
  await page.getByRole('button', { name: 'AI 연결·모델 관리', exact: true }).click()
  return page.getByRole('region', { name: 'AI 연결·모델 관리', exact: true })
}

const deletionEntry = (site: 'OA' | 'VWP'): Entry => ({ id: site === 'OA' ? '10000000-0000-4000-8000-000000000011' : '10000000-0000-4000-8000-000000000012', revision: 3, client_key_registered: false,
  config: { label: `삭제 대상 ${site}`, enabled: true, model: 'synthetic-model', models: ['synthetic-model'],
    ...(site === 'VWP' ? { execution_site: 'vwp', profile_id: 'expired-synthetic-profile' } : { auth_mode: 'bearer' }) } })

test('administrators can cancel and explicitly delete OA and VWP connections', async ({ page }) => {
  const state = await adminFixture(page, [deletionEntry('OA'), deletionEntry('VWP')]); const panel = await openPanel(page)
  const row = (site: string) => panel.getByRole('listitem').filter({ hasText: `삭제 대상 ${site}` })
  await row('OA').getByRole('button', { name: '삭제', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('삭제 대상 OA')
  await expect(dialog).toContainText('synthetic-model')
  await expect(dialog).toContainText('판정 이력은 보존')
  await expect(dialog).toContainText('진행 중인 작업은 중단될 수 있습니다')
  await dialog.getByRole('button', { name: '취소', exact: true }).click()
  expect(state.writes).toHaveLength(0)
  await row('OA').getByRole('button', { name: '삭제', exact: true }).click()
  await dialog.getByRole('button', { name: '연결 삭제', exact: true }).click()
  await expect(row('OA')).toHaveCount(0)
  await expect(panel.getByRole('status').filter({ hasText: '연결을 삭제했습니다' })).toBeVisible()
  await row('VWP').getByRole('button', { name: '삭제', exact: true }).click()
  await dialog.getByRole('button', { name: '연결 삭제', exact: true }).click()
  await expect(row('VWP')).toHaveCount(0)
  expect(state.deletePaths).toEqual(['/api/admin/ai/connections/10000000-0000-4000-8000-000000000011/delete', '/api/admin/ai/connections/10000000-0000-4000-8000-000000000012/delete'])
  expect(state.writes).toEqual([{ expected_revision: 3 }, { expected_revision: 3 }])
})
