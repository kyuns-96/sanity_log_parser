import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { makeDemo } from '../src/demo'

// Synthetic UI contracts: no company accounts, models or database are used.
const profile = { id: 'synthetic-vwp', label: '합성 프로필', api: 'chat_completions',
  models: ['vwp-fast', 'vwp-review'], auth_mode: 'bearer', timeout: 120,
  max_input_bytes: 1048576, max_output_bytes: 2097152, sha256: 'a'.repeat(64) }

type Entry = { id: string; revision: number; config: Record<string, any>; client_key_registered: boolean }

async function adminFixture(page: Page, initial: Entry[] = [], profiles = [profile]) {
  const fallback = makeDemo()
  const state = { entries: initial, profiles, getFailures: 0, failNextRefresh: false,
    credentialsReady: true, vwpReady: true, deleteFailure: '' as '' | 'conflict' | 'lost',
    deleted: new Map<string, number>(), deletePaths: [] as string[],
    holdGet: null as null | { started: () => void; release: Promise<void> },
    writes: [] as Record<string, any>[], taskWrites: [] as Record<string, any>[],
    taskDefaults: { revision: 0, defaults: { CLUSTER: null, REVIEW: null } } as { revision: number; defaults: { CLUSTER: {connection_id: string; model_id: string} | null; REVIEW: {connection_id: string; model_id: string} | null } } }
  await page.route('**/api/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname
    if (path === '/api/auth-config') return route.fulfill({ json: { mode: 'sso', login_url: '/auth/login', logout_url: '/auth/logout', logout_method: 'POST' } })
    if (path === '/api/session') return route.fulfill({ json: { user_id: 'synthetic-user', reviewer: '합성 사용자', auth_method: 'sso', authorization_ready: true, is_admin: true, can_read_business: false, can_receive: false, writable: false, access_mode: 'postgresql', notice: '' } })
    if (path === '/api/access/catalog') return route.fulfill({ json: { projects: [], blocks: [] } })
    if (path === '/api/access/me') return route.fulfill({ json: { user_id: 'synthetic-user', is_admin: true, revision: 0, grants: [], requests: [] } })
    if (path === '/api/admin/access') return route.fulfill({ json: { revision: 0, requests: [], grants: [], users: [] } })
    if (path === '/api/admin/ai/task-defaults') {
      if (request.method() === 'GET') return route.fulfill({ json: state.taskDefaults })
      const body = request.postDataJSON()
      expect(request.headers()['x-sanity-request']).toBe('1')
      expect(body.expected_revision).toBe(state.taskDefaults.revision)
      state.taskWrites.push(body)
      state.taskDefaults = { revision: state.taskDefaults.revision + 1, defaults: body.defaults }
      return route.fulfill({ json: state.taskDefaults })
    }
    if (path.startsWith('/api/admin/ai/connections')) {
      if (request.method() === 'GET') {
        if (state.getFailures > 0) { state.getFailures--; return route.fulfill({ status: 503, json: { code: 'UNAVAILABLE' } }) }
        if (state.holdGet) {
          const pending = state.holdGet; state.holdGet = null
          const snapshot = structuredClone(state.entries)
          pending.started(); await pending.release
          return route.fulfill({ json: { items: snapshot, credentials_ready: state.credentialsReady, vwp_ready: state.vwpReady, vwp_profiles: state.profiles } })
        }
        return route.fulfill({ json: { items: state.entries, credentials_ready: state.credentialsReady, vwp_ready: state.vwpReady, vwp_profiles: state.profiles } })
      }
      const body = request.postDataJSON()
      if (path.endsWith('/delete')) {
        const id = path.split('/').at(-2)!
        const old = state.entries.find(entry => entry.id === id)
        expect(Object.keys(body)).toEqual(['expected_revision'])
        expect(request.headers()['x-sanity-request']).toBe('1')
        state.writes.push(body); state.deletePaths.push(path)
        if (state.deleteFailure === 'conflict') return route.fulfill({ status: 409, json: { code: 'REVISION_CONFLICT' } })
        const revision = old?.revision ?? state.deleted.get(id)
        expect(body.expected_revision).toBe(revision)
        if (old) { state.entries.splice(state.entries.indexOf(old), 1); state.deleted.set(id, old.revision) }
        if (state.failNextRefresh) { state.getFailures++; state.failNextRefresh = false }
        if (state.deleteFailure === 'lost') { state.deleteFailure = ''; return route.abort('failed') }
        return route.fulfill({ json: { id, revision: revision! + 1, deleted: true } })
      }
      const id = path.split('/').at(-1)!
      const old = state.entries.find(entry => entry.id === id)
      expect(body.expected_revision).toBe(old?.revision || 0)
      expect(request.headers()['x-sanity-request']).toBe('1')
      state.writes.push(body)
      const value = { id, revision: (old?.revision || 0) + 1, config: body.config,
        client_key_registered: !!body.client_api_key || !!old?.client_key_registered }
      if (old) state.entries[state.entries.indexOf(old)] = value; else state.entries.push(value)
      if (state.failNextRefresh) { state.getFailures++; state.failNextRefresh = false }
      return route.fulfill({ json: value })
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

async function fillOa(panel: ReturnType<Page['getByRole']>) {
  await panel.getByRole('button', { name: 'OA AI 연결 추가', exact: true }).click()
  await panel.getByLabel('AI 연결 이름', { exact: true }).fill('합성 OA 연결')
  await panel.getByLabel('AI API URL', { exact: true }).fill('http://synthetic.example.test/v1/chat/completions')
  await panel.getByLabel('허용 AI 모델 목록').fill('model-a\nmodel-b')
  await panel.getByLabel('기본 AI 모델', { exact: true }).selectOption('model-b')
}

async function fillVwp(panel: ReturnType<Page['getByRole']>) {
  await panel.getByRole('button', { name: 'VWP AI 연결 추가', exact: true }).click()
  const form = panel.getByRole('form', { name: 'VWP AI 연결 등록' })
  await form.getByLabel('VWP AI 연결 이름', { exact: true }).fill('합성 VWP 연결')
  await form.getByLabel('VWP AI 실행 프로필', { exact: true }).selectOption(profile.id)
  await form.getByLabel('VWP 기본 AI 모델', { exact: true }).selectOption('vwp-fast')
  return form
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

test('uncertain deletion can be confirmed with the same revision without losing the row first', async ({ page }) => {
  const state = await adminFixture(page, [deletionEntry('OA')]); state.deleteFailure = 'lost'
  const panel = await openPanel(page)
  await panel.getByRole('listitem').getByRole('button', { name: '삭제', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: '연결 삭제', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('삭제 요청 결과를 확인하지 못했습니다')
  await expect(page.locator('.ai-connections-admin .ai-connection-row')).toHaveCount(1)
  await dialog.getByRole('button', { name: '취소', exact: true }).click()
  await expect(panel.getByRole('listitem')).toHaveCount(1)
  await panel.getByRole('listitem').getByRole('button', { name: '삭제', exact: true }).click()
  await dialog.getByRole('button', { name: '연결 삭제', exact: true }).click()
  await expect(panel.getByRole('listitem')).toHaveCount(0)
  expect(state.writes).toEqual([{ expected_revision: 3 }, { expected_revision: 3 }])
})

test('a rejected deletion leaves the connection visible without claiming success', async ({ page }) => {
  const state = await adminFixture(page, [deletionEntry('OA')]); state.deleteFailure = 'conflict'
  const panel = await openPanel(page)
  await panel.getByRole('listitem').getByRole('button', { name: '삭제', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: '연결 삭제', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('삭제하지 못했습니다')
  await expect(page.locator('.ai-connections-admin .ai-connection-row')).toHaveCount(1)
  await dialog.getByRole('button', { name: '취소', exact: true }).click()
  await expect(panel.getByRole('listitem')).toHaveCount(1)
  await expect(panel.getByRole('status').filter({ hasText: '연결을 삭제했습니다' })).toHaveCount(0)
})

test('deletion remains available offline and an old list response cannot restore the removed connection', async ({ page }) => {
  const state = await adminFixture(page, [deletionEntry('VWP')], [])
  state.credentialsReady = false; state.vwpReady = false
  const panel = await openPanel(page)
  let start!: () => void, release!: () => void
  const started = new Promise<void>(resolve => { start = resolve })
  const gate = new Promise<void>(resolve => { release = resolve })
  state.holdGet = { started: start, release: gate }
  await panel.getByRole('button', { name: '새로고침', exact: true }).click()
  await started
  state.failNextRefresh = true
  await panel.getByRole('listitem').getByRole('button', { name: '삭제', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '연결 삭제', exact: true }).click()
  await expect(panel.getByRole('listitem')).toHaveCount(0)
  await expect(panel.getByRole('status').filter({ hasText: '연결을 삭제했습니다' })).toBeVisible()
  await expect(panel.getByRole('alert').filter({ hasText: '목록을 불러오지 못했습니다' })).toBeVisible()
  release()
  await expect(panel.getByRole('listitem')).toHaveCount(0)
  expect(state.writes).toEqual([{ expected_revision: 3 }])
})

test('batch settings expose the effective output-bound cap before saving', async ({ page }) => {
  const state = await adminFixture(page); const panel = await openPanel(page)
  await fillOa(panel)
  await panel.locator('summary').filter({ hasText: '처리 한도와 전체 로그 배치 설정' }).click()
  await panel.getByLabel('전체 로그·그룹 판정·보존 본문 배치 사용').check()
  const estimate = panel.getByRole('status').filter({ hasText: '현재 설정의 분류 배치 상한' })
  await expect(estimate).toContainText('21건 / 호출')
  await expect(estimate).toContainText('최소 2,381회')
  await panel.getByLabel('조각별 최대 로그', { exact: true }).fill('1000')
  await expect(estimate).toContainText('21건 / 호출')
  await panel.getByLabel('출력 예약', { exact: true }).fill('8192')
  await expect(estimate).toContainText('42건 / 호출')
  await expect(estimate).toContainText('최소 1,191회')
  await panel.getByLabel('조각별 최대 로그', { exact: true }).fill('10')
  await expect(estimate).toContainText('10건 / 호출')
  expect(state.writes).toHaveLength(0)
})

test('parallel call setting preserves an untouched legacy config and enforces its range', async ({ page }) => {
  const state = await adminFixture(page); const panel = await openPanel(page)
  await fillOa(panel)
  await panel.locator('summary').filter({ hasText: '처리 한도와 전체 로그 배치 설정' }).click()
  await panel.getByLabel('전체 로그·그룹 판정·보존 본문 배치 사용').check()
  const limit = panel.getByLabel('작업당 동시 호출 수', { exact: true })
  await expect(limit).toHaveValue('1')
  await expect(panel).toContainText('다른 작업이나 이전 원격 호출은 포함하지 않습니다')
  await panel.getByRole('button', { name: 'AI 연결 저장', exact: true }).click()
  await expect(panel.getByRole('listitem').getByText('합성 OA 연결', { exact: true })).toBeVisible()
  expect(state.writes[0].config.batch).not.toHaveProperty('max_concurrency')
  await panel.getByRole('listitem').filter({ hasText: '합성 OA 연결' }).getByRole('button', { name: '수정', exact: true }).click()
  await panel.locator('summary').filter({ hasText: '처리 한도와 전체 로그 배치 설정' }).click()
  await limit.fill('9')
  expect(await limit.evaluate((input: HTMLInputElement) => input.checkValidity())).toBe(false)
  await panel.getByRole('button', { name: 'AI 연결 저장', exact: true }).click()
  expect(state.writes).toHaveLength(1)
  await limit.fill('2')
  await panel.getByRole('button', { name: 'AI 연결 저장', exact: true }).click()
  await expect.poll(() => state.writes.length).toBe(2)
  expect(state.writes[1].config.batch.max_concurrency).toBe(2)
})

for (const site of ['OA', 'VWP'] as const) {
  test(`${site} save succeeds even when the subsequent list refresh fails`, async ({ page }) => {
    const state = await adminFixture(page); const panel = await openPanel(page)
    state.failNextRefresh = true
    if (site === 'OA') {
      await fillOa(panel)
      await panel.getByRole('button', { name: 'AI 연결 저장', exact: true }).click()
    } else {
      const form = await fillVwp(panel)
      await form.getByRole('button', { name: 'VWP AI 연결 저장', exact: true }).click()
    }
    await expect(panel.getByRole('status').filter({ hasText: /저장/ })).toBeVisible()
    await expect(panel.getByRole('alert').filter({ hasText: /목록|새로고침|불러오/ })).toBeVisible()
    expect(state.writes).toHaveLength(1)
    await panel.getByRole('button', { name: '새로고침', exact: true }).click()
    await expect(panel.getByRole('listitem').getByText(`합성 ${site} 연결`, { exact: true })).toBeVisible()
    expect(state.writes).toHaveLength(1)
  })
}

test('initial list failure is not an empty list and retry loads entries', async ({ page }) => {
  const initial: Entry[] = [{ id: 'existing', revision: 1, client_key_registered: false,
    config: { label: '기존 합성 연결', enabled: true, execution_site: 'vwp', profile_id: profile.id,
      api: 'chat_completions', model: 'vwp-fast', models: ['vwp-fast'] } }]
  const state = await adminFixture(page, initial); state.getFailures = 1
  const panel = await openPanel(page)
  await expect(panel.getByRole('alert').filter({ hasText: /목록|불러오/ })).toBeVisible()
  await expect(panel.getByText('등록된 AI 연결이 없습니다', { exact: true })).toHaveCount(0)
  await panel.getByRole('button', { name: '새로고침', exact: true }).click()
  await expect(panel.getByRole('listitem').getByText('기존 합성 연결', { exact: true })).toBeVisible()
  expect(state.writes).toHaveLength(0)
})

test('an unavailable previously selected VWP model can be removed and the default reselected', async ({ page }) => {
  const batch = { enabled: false, tokenizer_mode: 'utf8_bytes', tokenizer_sha256: '', context_tokens: 32768,
    input_tokens: 16000, output_tokens: 4096, overhead_tokens: 1024, output_parameter: 'max_tokens',
    max_records: 200, merge_groups: 20, max_calls: 20000, max_rounds: 8, deadline_seconds: 86400 }
  const initial: Entry[] = [{ id: 'existing-vwp', revision: 3, client_key_registered: false,
    config: { label: '기존 VWP 연결', enabled: true, execution_site: 'vwp', profile_id: profile.id,
      api: 'chat_completions', model: 'vwp-removed', models: ['vwp-fast', 'vwp-removed'],
      json_mode: false, timeout: 120, max_input_bytes: 1048576, max_output_bytes: 2097152, batch } }]
  const state = await adminFixture(page, initial); const panel = await openPanel(page)
  await panel.getByRole('listitem').filter({ hasText: '기존 VWP 연결' }).getByRole('button', { name: '수정', exact: true }).click()
  const form = panel.getByRole('form', { name: 'VWP AI 연결 등록' })
  const removed = form.getByRole('checkbox', { name: /vwp-removed/ })
  await expect(removed).toBeChecked()
  await removed.click()
  await expect(removed).toHaveCount(0)
  await expect(form.getByLabel('VWP 기본 AI 모델', { exact: true })).toHaveValue('')
  await form.getByLabel('VWP 기본 AI 모델', { exact: true }).selectOption('vwp-fast')
  await form.getByRole('button', { name: 'VWP AI 연결 저장', exact: true }).click()
  await expect(panel.getByRole('listitem').getByText('기존 VWP 연결', { exact: true })).toBeVisible()
  expect(state.writes).toHaveLength(1)
  expect(state.writes[0]).toMatchObject({ expected_revision: 3, config: { models: ['vwp-fast'], model: 'vwp-fast' } })
})

test('removing the OA default requires choosing another allowed model before saving', async ({ page }) => {
  const state = await adminFixture(page); const panel = await openPanel(page)
  await fillOa(panel)
  await panel.getByLabel('허용 AI 모델 목록').fill('model-a')
  await expect(panel.getByLabel('기본 AI 모델', { exact: true })).toHaveValue('')
  await expect(panel.getByRole('button', { name: 'AI 연결 저장', exact: true })).toBeDisabled()
  expect(state.writes).toHaveLength(0)
  await panel.getByLabel('기본 AI 모델', { exact: true }).selectOption('model-a')
  await panel.getByRole('button', { name: 'AI 연결 저장', exact: true }).click()
  await expect(panel.getByRole('listitem').getByText('합성 OA 연결', { exact: true })).toBeVisible()
  expect(state.writes[0].config).toMatchObject({ models: ['model-a'], model: 'model-a' })
})

test('an older refresh cannot overwrite the list loaded after a successful save', async ({ page }) => {
  const state = await adminFixture(page); const panel = await openPanel(page)
  await fillOa(panel)
  let started!: () => void; let release!: () => void
  const pendingStarted = new Promise<void>(resolve => { started = resolve })
  const pendingRelease = new Promise<void>(resolve => { release = resolve })
  state.holdGet = { started, release: pendingRelease }
  try {
    await panel.getByRole('button', { name: '새로고침', exact: true }).click()
    await pendingStarted  // The old GET captured the empty list before the POST.
    await panel.getByRole('button', { name: 'AI 연결 저장', exact: true }).click()
    await expect(panel.getByRole('listitem').getByText('합성 OA 연결', { exact: true })).toBeVisible()
    expect(state.writes).toHaveLength(1)
    const oldResponse = page.waitForResponse(response => response.request().method() === 'GET' &&
      new URL(response.url()).pathname === '/api/admin/ai/connections')
    release(); await oldResponse
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
    await expect(panel.getByRole('listitem').getByText('합성 OA 연결', { exact: true })).toBeVisible()
    await expect(panel.getByText('등록된 AI 연결이 없습니다', { exact: true })).toHaveCount(0)
    expect(state.writes).toHaveLength(1)
  } finally { release() }
})

for (const width of [1280, 1440]) {
  test(`desktop ${width}: long model names and expanded limits do not overlap or overflow`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1100 })
    const longModel = `synthetic-${'long-model-name-'.repeat(9)}end`
    await adminFixture(page, [], [{ ...profile, models: ['vwp-fast', longModel] }])
    const panel = await openPanel(page)
    await fillOa(panel)
    await panel.getByLabel('허용 AI 모델 목록').fill(`model-a\n${longModel}`)
    for (const site of ['OA', 'VWP'] as const) {
      const form = site === 'OA' ? panel.locator('form.ai-connection-form') : await fillVwp(panel)
      await form.locator('summary').filter({ hasText: '처리 한도와 전체 로그 배치 설정' }).click()
      await form.getByRole('checkbox', { name: '전체 로그·그룹 판정·보존 본문 배치 사용', exact: true }).check()
      if (site === 'VWP') await expect(form.getByRole('group', { name: 'VWP 허용 AI 모델 목록' }).getByText(longModel, { exact: true })).toBeVisible()
      const layout = await form.evaluate(root => {
      const labels = Array.from(root.querySelectorAll('label')).filter(label => {
        const control = label.querySelector('input,select,textarea')
        return control && label.getClientRects().length && control.getClientRects().length
      })
      const rects = labels.map(label => ({ text: label.textContent?.trim(), rect: label.getBoundingClientRect(),
        control: label.querySelector('input,select,textarea')!.getBoundingClientRect() }))
      const overlaps: string[] = []
      for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
        const a = rects[i].rect, b = rects[j].rect
        if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
            Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1)
          overlaps.push(`${rects[i].text} / ${rects[j].text}`)
      }
      const formRect = root.getBoundingClientRect()
      return { overlaps, badControls: rects.filter(({ rect, control }) => control.width <= 0 || control.height <= 0 ||
        control.left < rect.left - 1 || control.right > rect.right + 1 || control.top < rect.top - 1 ||
        control.bottom > rect.bottom + 1 || control.right > formRect.right + 1).map(item => item.text),
        overflow: document.documentElement.scrollWidth > window.innerWidth + 1 }
      })
      expect(layout.overlaps, `${site}: labels`).toEqual([])
      expect(layout.badControls, `${site}: controls`).toEqual([])
      expect(layout.overflow, `${site}: horizontal overflow`).toBe(false)
      if (site === 'OA') await form.getByRole('button', { name: '취소', exact: true }).click()
    }
  })
}

test('reasoning settings persist for OA and return to omitted server defaults', async ({ page }) => {
  const state = await adminFixture(page); const panel = await openPanel(page)
  await fillOa(panel)
  await panel.getByRole('button', { name: 'AI 연결 저장', exact: true }).click()
  await expect(panel.getByRole('listitem').getByText('합성 OA 연결', { exact: true })).toBeVisible()
  expect(state.writes[0].config).not.toHaveProperty('reasoning')
  const edit = () => panel.getByRole('listitem').filter({ hasText: '합성 OA 연결' }).getByRole('button', { name: '수정', exact: true }).click()
  const openLimits = () => panel.locator('summary').filter({ hasText: '처리 한도와 전체 로그 배치 설정' }).click()
  await edit(); await openLimits()
  await panel.getByLabel('AI 추론 동작').selectOption('on')
  await panel.getByLabel('AI 추론 강도').selectOption('high')
  await panel.getByRole('button', { name: 'AI 연결 저장', exact: true }).click()
  await expect.poll(() => state.writes.length).toBe(2)
  expect(state.writes[1].config.reasoning).toEqual({ mode: 'on', protocol: 'openai', effort: 'high' })
  await edit(); await openLimits()
  await expect(panel.getByLabel('AI 추론 강도')).toHaveValue('high')
  await panel.getByLabel('AI 추론 동작').selectOption('off')
  await expect(panel.getByText('추론 끄기는 none 값을 전달합니다. none을 지원하는 모델에서만 사용하세요.')).toBeVisible()
  await panel.getByLabel('AI 추론 요청 방식').selectOption('chat_template')
  await panel.getByRole('button', { name: 'AI 연결 저장', exact: true }).click()
  await expect.poll(() => state.writes.length).toBe(3)
  expect(state.writes[2].config.reasoning).toEqual({ mode: 'off', protocol: 'chat_template', effort: 'default' })
  await edit(); await openLimits()
  await expect(panel.getByLabel('AI 추론 요청 방식')).toHaveValue('chat_template')
  await panel.getByLabel('AI API 종류').selectOption('responses')
  await expect(panel.getByText(/Responses는 Chat Template을 지원하지 않아/)).toBeVisible()
  await expect(panel.getByLabel('AI 추론 요청 방식')).toHaveValue('openai')
  await panel.getByRole('button', { name: 'AI 연결 저장', exact: true }).click()
  await expect.poll(() => state.writes.length).toBe(4)
  expect(state.writes[3].config.reasoning).toEqual({ mode: 'off', protocol: 'openai', effort: 'default' })
  await edit(); await openLimits()
  await panel.getByLabel('AI 추론 동작').selectOption('default')
  await panel.getByRole('button', { name: 'AI 연결 저장', exact: true }).click()
  await expect.poll(() => state.writes.length).toBe(5)
  expect(state.writes[4].config).not.toHaveProperty('reasoning')
})

test('VWP reasoning is persisted using the selected profile', async ({ page }) => {
  const state = await adminFixture(page); const panel = await openPanel(page); const form = await fillVwp(panel)
  await form.locator('summary').filter({ hasText: '처리 한도와 전체 로그 배치 설정' }).click()
  await form.getByLabel('AI 추론 동작').selectOption('on')
  await form.getByLabel('AI 추론 요청 방식').selectOption('chat_template')
  await form.getByRole('button', { name: 'VWP AI 연결 저장', exact: true }).click()
  await expect.poll(() => state.writes.length).toBe(1)
  expect(state.writes[0].config.reasoning).toEqual({ mode: 'on', protocol: 'chat_template', effort: 'default' })
  await panel.getByRole('listitem').filter({ hasText: '합성 VWP 연결' }).getByRole('button', { name: '수정', exact: true }).click()
  const reopened = panel.getByRole('form', { name: 'VWP AI 연결 등록' })
  await reopened.locator('summary').filter({ hasText: '처리 한도와 전체 로그 배치 설정' }).click()
  await expect(reopened.getByLabel('AI 추론 요청 방식')).toHaveValue('chat_template')
})

test('admin saves distinct classification and judgment defaults', async ({ page }) => {
  const oa = deletionEntry('OA'), vwp = deletionEntry('VWP')
  oa.config.models = ['synthetic-model', 'classify']; vwp.config.models = ['synthetic-model', 'judge']
  const state = await adminFixture(page, [oa, vwp]); const panel = await openPanel(page)
  const defaults = panel.getByRole('region', { name: '작업별 기본 AI 모델' })
  await defaults.getByLabel('로그 분류 기본 연결').selectOption(oa.id)
  await defaults.getByLabel('로그 분류 기본 모델').selectOption('classify')
  await defaults.getByLabel('그룹 판정 기본 연결').selectOption(vwp.id)
  await defaults.getByLabel('그룹 판정 기본 모델').selectOption('judge')
  await defaults.getByRole('button', { name: '작업별 기본값 저장' }).click()
  await expect.poll(() => state.taskWrites.length).toBe(1)
  expect(state.taskWrites[0]).toEqual({ expected_revision: 0, defaults: { CLUSTER: { connection_id: oa.id, model_id: 'classify' }, REVIEW: { connection_id: vwp.id, model_id: 'judge' } } })
  await defaults.getByRole('button', { name: '다시 조회' }).click()
  await expect(defaults.getByLabel('로그 분류 기본 모델')).toHaveValue('classify')
  await expect(defaults.getByLabel('그룹 판정 기본 모델')).toHaveValue('judge')
})
