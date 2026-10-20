import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { makeDemo } from '../src/demo'
import type { Bundle, Detail, Page as ApiPage } from '../src/types'

test('classification proposal remains separate from apply, confirmation and sanity verdict', async ({ page }) => {
  const fallback = makeDemo()
  const sent: Record<string, unknown>[] = []
  let requested = false
  const head = { revision: 0, active_job: null as string | null, confirmed: false }
  const job = { job_id: 'cj-test', base_revision: 0, stale: false, references_current: true, instruction: '', state: {
    phase: 'READY', received_records: 1, received_parts: 1, stats: { records: 1, targets: 1, assigned: 1, groups: 1, unresolved: 0, candidate_records: 1 },
    header: { rules: [], explanation: 'UI 테스트용 제안', provenance: { adapter: 'TEST_DOUBLE', scanned_records: 1, sample_ids: ['r-test'] } }
  } }
  const candidate = { group_id: 'pg-fail', identity: { object: 'top/A' }, source_bundle: 'b-previous', source_unit: 'u-previous', source_job: 'cj-previous', fail_evidence_ids: ['r-previous'], matches: ['object'], differences: [], missing: [], state: 'POSSIBLE_FAIL_GROUP', needs_recheck: false, decision: null }
  await page.route('**/api/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const body = request.method() === 'POST' ? request.postDataJSON() : undefined
    if (!url.pathname.includes('/clustering')) return route.fulfill({ json: await fallback(url.pathname + url.search, body) })
    if (body && url.pathname.endsWith('/actions')) {
      sent.push(body); head.revision++
      if (body.action === 'APPLY') head.active_job = 'cj-test'
      if (body.action === 'CONFIRM') head.confirmed = true
      return route.fulfill({ json: { status: body.action, ...head } })
    }
    if (body) { sent.push(body); requested = true; return route.fulfill({ json: { status: 'REQUESTED', job_id: 'cj-test' } }) }
    if (url.pathname.endsWith('/members')) return route.fulfill({ json: { items: [{ record_id: 'r-test', group_id: 'pg-test', previous_group_id: null, membership_changed: true, check_status: 'FAIL', method: 'RULE', reason: 'IDENTITY_MATCH', fields: { object: 'top/A' }, candidates: [candidate, { ...candidate, group_id: 'pg-fail-2' }] }], total: 1, offset: 0, limit: 20 } })
    return route.fulfill({ json: { head, jobs: requested ? [job] : [], total: requested ? 1 : 0, saved_rules: [], input_blockers: [], history: [] } })
  })
  await page.goto('/')
  await page.getByLabel('접근 토큰').fill('synthetic-test-token')
  await page.getByRole('button', { name: '워크스페이스 연결' }).click()
  await page.getByRole('tab', { name: 'AI 분류', exact: true }).click()
  await page.getByLabel('분류 요청', { exact: true }).fill('객체별로 묶어줘')
  await page.getByRole('button', { name: 'VWP에 분류 요청', exact: true }).click()
  await expect(page.getByText('UI 테스트용 제안', { exact: true })).toBeVisible()
  expect(sent).toHaveLength(1)
  expect(sent[0].instruction).toBe('객체별로 묶어줘')
  await expect(page.getByRole('button', { name: '분류 확정', exact: true })).toBeDisabled()
  await expect(page.getByText('FAIL 그룹 연관 후보 · POSSIBLE_FAIL_GROUP', { exact: true })).toHaveCount(2)
  await page.getByLabel('분류 판단 사유').fill('전체 적용 결과와 미분류 건수 확인')
  await page.getByRole('button', { name: '제안을 현재 분류에 적용', exact: true }).click()
  await expect(page.getByText('분류 개정 1', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: '규칙 재사용 저장', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '분류 확정', exact: true }).click()
  await expect(page.getByText('분류 개정 2 · 사람 확정', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '규칙 재사용 저장', exact: true }).click()
  await expect.poll(() => sent.length).toBe(4)
  expect(sent.slice(1).map(v => v.action)).toEqual(['APPLY', 'CONFIRM', 'SAVE_RULES'])
  expect(sent.slice(1).map(v => v.expected_revision)).toEqual([0, 1, 2])
  await expect(page.getByText('개별 판정 FAIL', { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.screenshot({ path: 'artifacts/clustering-mobile.png', fullPage: true, animations: 'disabled' })
})

test('classification is explicitly unavailable in synthetic demo', async ({ page }) => {
  await demo(page)
  await page.getByRole('tab', { name: 'AI 분류', exact: true }).click()
  await expect(page.getByText('데모에서는 AI를 호출하지 않습니다.', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: 'VWP에 분류 요청', exact: true })).toHaveCount(0)
})

test('stale classification can be deferred and exclusions are visible without enabling approval', async ({ page }) => {
  const fallback = makeDemo()
  const sent: Record<string, unknown>[] = []
  const head = { revision: 3, active_job: 'cj-stale', confirmed: true }
  const job = { job_id: 'cj-stale', base_revision: 0, stale: false, references_current: false, instruction: '',
    catalog_exclusions: [{ group_id: 'pg-descendant', group_revision: 2, source_bundle: 'b-descendant',
      source_unit: 'u-other', source_job: 'cj-descendant', reason: 'SELF_DEPENDENCY' }],
    state: { phase: 'READY', received_records: 1, received_parts: 1 } }
  await page.route('**/api/**', async route => {
    const request = route.request(); const url = new URL(request.url())
    const body = request.method() === 'POST' ? request.postDataJSON() : undefined
    if (!url.pathname.includes('/clustering')) return route.fulfill({ json: await fallback(url.pathname + url.search, body) })
    if (body) {
      sent.push(body)
      expect(body.action).toBe('DEFER')
      head.confirmed = false; head.revision++
      return route.fulfill({ json: { status: 'DEFER', ...head } })
    }
    if (url.pathname.endsWith('/members')) return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 20 } })
    return route.fulfill({ json: { head, jobs: [job], total: 1, saved_rules: [], input_blockers: [], history: [] } })
  })
  await page.goto('/')
  await page.getByLabel('접근 토큰').fill('synthetic-test-token')
  await page.getByRole('button', { name: '워크스페이스 연결' }).click()
  await page.getByRole('tab', { name: 'AI 분류', exact: true }).click()
  await expect(page.getByText('비교 대상에서 제외한 과거 확정본 1개', { exact: true })).toBeVisible()
  await page.getByText('제외 사유 · 원본 개정', { exact: true }).click()
  await expect(page.getByText('현재 항목에 의존하는 그룹 · 순환 참조 방지', { exact: true })).toBeVisible()
  await expect(page.getByText('제외한 확정본까지 비교 완료했다는 뜻은 아닙니다.', { exact: false })).toBeVisible()
  await page.getByLabel('분류 판단 사유').fill('참조가 변경되어 재검토합니다.')
  for (const name of ['제안을 현재 분류에 적용', '분류 확정', '규칙 재사용 저장']) {
    await expect(page.getByRole('button', { name, exact: true })).toBeDisabled()
  }
  await expect(page.getByRole('button', { name: 'VWP에 분류 요청', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '분류 보류', exact: true }).click()
  await expect(page.getByText('분류 개정 4 · 미확정', { exact: false })).toBeVisible()
  expect(sent).toHaveLength(1)
  expect(sent[0].expected_revision).toBe(3)
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})

async function demo(page: Page) {
  await page.goto('/?demo=1&flow=legacy')
  await expect(page.getByRole('heading', { name: 'sanity-20261012-042' })).toBeVisible()
  await expect(page.getByText('CHECK-101', { exact: true })).toBeVisible()
}

test('explicit login, explicit demo, and memory-only token', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '검토 공간에 연결' })).toBeVisible()
  await expect(page.getByText('Atlas', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '데모 둘러보기' }).click()
  await expect(page.getByRole('heading', { name: 'Atlas 프로젝트', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '워크스페이스 데모 안내' })).toContainText('데모 모드')
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0])
  await page.getByRole('button', { name: '데모 종료', exact: true }).click()
  await expect(page.getByLabel('접근 토큰')).toHaveValue('')
})

test('all units, review FAIL guard, revision and history', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', err => errors.push(err.message))
  await demo(page)
  await expect(page.getByRole('row')).toHaveCount(9)
  await expect(page.getByText('대표 근거 2개 · 생략 16개.', { exact: false })).toBeVisible()
  await page.getByText('판정 기준 · 원본 정보', { exact: true }).click()
  await expect(page.getByText('reports/numeric_limits.csv', { exact: true })).toBeVisible()
  await expect(page.getByText('임계값', { exact: true })).toBeVisible()
  await expect(page.getByText('numeric_csv_v1', { exact: true })).toBeVisible()
  await expect(page.getByText('측정값', { exact: false }).first()).toBeVisible()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: 'artifacts/desktop.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '검토 의견 기록', exact: true }).click()
  await expect(page.getByRole('radio', { name: /PASS/ })).toBeDisabled()
  await page.getByLabel('판단 사유').fill('개별 측정값과 기준 초과를 확인했습니다.')
  await page.getByRole('button', { name: '검토 저장', exact: true }).click()
  await expect(page.getByText('검토 의견이 저장되었습니다.', { exact: false })).toBeVisible()
  await expect(page.getByText('개정 4', { exact: true }).first()).toBeVisible()
  await page.getByRole('tab', { name: /변경 이력/ }).click()
  await expect(page.getByText('개별 측정값과 기준 초과를 확인했습니다.')).toBeVisible()
  expect(errors).toEqual([])
})

test('evidence request, response receive, bounded evidence and report download', async ({ page }) => {
  await demo(page)
  await page.getByRole('button', { name: '근거 요청', exact: true }).click()
  await expect(page.getByLabel('요청할 기록 ID')).toHaveValue('r-demo-001\nr-demo-002')
  await page.getByLabel('요청 사유').fill('대표 기록의 전체 문맥을 확인합니다.')
  await page.getByRole('button', { name: '요청 등록', exact: true }).click()
  await expect(page.getByRole('button', { name: '수신 근거 보기', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '응답 수신', exact: true }).click()
  await expect(page.getByText('OA 응답: 수신 완료', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '수신 근거 보기', exact: true }).click()
  await expect(page.getByRole('dialog').getByText('FULFILLED', { exact: false })).toBeVisible()
  await expect(page.getByRole('dialog').getByText('[검사 근거 예시] r-demo-001:', { exact: false })).toBeVisible()
  await page.keyboard.press('Escape')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '보고서', exact: true }).click()
  expect((await download).suggestedFilename()).toBe('sanity-b-demo-20261012-chip-a.md')
})

test('API failure never becomes demo and token is not stored', async ({ page }) => {
  await page.route('**/api/**', route => {
    expect(route.request().headers().authorization).toBe('Bearer test-token-not-real')
    if (route.request().url().endsWith('/session')) return route.fulfill({ json: { reviewer: '실제 검토자', writable: true, access_mode: 'direct', notice: 'DB 연결 성공을 의미하지 않습니다.' } })
    return route.fulfill({ status: 503, json: { code: 'DATABASE_UNAVAILABLE' } })
  })
  await page.goto('/')
  await page.getByLabel('접근 토큰').fill('test-token-not-real')
  await page.getByRole('button', { name: '워크스페이스 연결' }).click()
  await expect(page.getByRole('alert')).toContainText('DATABASE_UNAVAILABLE')
  await expect(page.getByText('DB 연결 성공을 의미하지 않습니다.')).toBeVisible()
  await expect(page.getByText('데모 모드', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Atlas', { exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0])
  await page.reload()
  await expect(page.getByLabel('접근 토큰')).toHaveValue('')
})

test('failed command retry keeps UUID, edited command gets a new UUID, conflicts stay explicit', async ({ page }) => {
  const api = makeDemo(); const sent: Record<string, unknown>[] = []
  await page.route('**/api/**', async route => {
    const request = route.request(); const url = new URL(request.url())
    const body = request.method() === 'POST' ? request.postDataJSON() : undefined
    if (url.pathname.endsWith('/reviews')) {
      sent.push(body)
      return route.fulfill({ status: 409, json: { code: sent.length < 3 ? 'REVIEW_BLOCKED' : 'REVISION_CONFLICT' } })
    }
    await route.fulfill({ json: await api(url.pathname + url.search, body) })
  })
  await page.goto('/')
  await page.getByLabel('접근 토큰').fill('test-token-not-real')
  await page.getByRole('button', { name: '워크스페이스 연결' }).click()
  await expect(page.getByText('CHECK-101', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '검토 의견 기록', exact: true }).click()
  await page.getByLabel('판단 사유').fill('기준 초과 확인')
  await page.getByRole('button', { name: '검토 저장', exact: true }).click()
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('REVIEW_BLOCKED')
  await expect(page.getByRole('dialog').getByRole('alert')).not.toContainText('다른 검토자')
  await page.getByRole('button', { name: '검토 저장', exact: true }).click()
  await expect.poll(() => sent.length).toBe(2)
  expect(sent[0].request_id).toBe(sent[1].request_id)
  await page.getByLabel('판단 사유').fill('근거를 추가하여 확인')
  await page.getByRole('button', { name: '검토 저장', exact: true }).click()
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('새로고침')
  expect(sent[2].request_id).not.toBe(sent[1].request_id)
  expect(sent[2].expected_revision).toBe(3)
})

test('read-only session disables all writes', async ({ page }) => {
  const api = makeDemo()
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/session')) return route.fulfill({ json: { reviewer: '관찰자', writable: false, access_mode: 'readonly', notice: '읽기 전용 연결입니다.' } })
    return route.fulfill({ json: await api(url.pathname + url.search) })
  })
  await page.goto('/')
  await page.getByLabel('접근 토큰').fill('readonly-test-token')
  await page.getByRole('button', { name: '워크스페이스 연결' }).click()
  await expect(page.getByText('CHECK-101', { exact: true })).toBeVisible()
  for (const name of ['결과 수신', '검토 의견 기록', '근거 요청']) await expect(page.getByRole('button', { name, exact: true })).toBeDisabled()
})

test('mobile layout has no viewport overflow, menu and dialog work', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await demo(page)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: 'artifacts/mobile.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '메뉴 열기' }).click()
  await page.getByRole('button', { name: '활동 이력', exact: true }).click()
  await expect(page.getByRole('heading', { name: '검토 변경 이력' })).toBeVisible()
  await page.getByRole('tab', { name: /검토 대장/ }).click()
  await page.getByRole('button', { name: '검토 의견 기록', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  expect(await page.getByRole('dialog').boundingBox()).toMatchObject({ width: 358 })
})

test('bundle and failed unit-page changes never retain data from the old scope', async ({ page }) => {
  const api = makeDemo()
  let releaseRequests!: () => void
  const requestGate = new Promise<void>(resolve => { releaseRequests = resolve })
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/bundles') {
      const result = await api(url.pathname + url.search) as ApiPage<Bundle>
      result.items.push({ ...result.items[0], bundle_id: 'b-second', run_id: 'second-run' }); result.total = 2
      return route.fulfill({ json: result })
    }
    if (url.pathname.endsWith('/requests')) {
      if (url.pathname.includes('b-second')) { await requestGate; return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 20 } }) }
      return route.fulfill({ json: { items: [{ request_id: 'old-request', review_unit_id: 'u-numeric_limits', reason: '이전 번들에만 있는 요청', status: 'PENDING', record_ids: ['r-demo-001'], created_at: '2026-10-12T08:00:00Z', expires_at: '2026-10-12T09:00:00Z', result: null, response_status: null }], total: 1, offset: 0, limit: 20 } })
    }
    if (url.searchParams.get('unit_offset') === '50') return route.fulfill({ status: 503, json: { code: 'DATABASE_UNAVAILABLE' } })
    const result = await api(url.pathname + url.search)
    if (url.pathname === '/api/bundles/b-second') { const detail = result as Detail; detail.run.run_id = 'second-run'; detail.bundle_id = 'b-second'; detail.unit_total = 51 }
    return route.fulfill({ json: result })
  })
  await page.goto('/')
  await page.getByLabel('접근 토큰').fill('test-token-not-real')
  await page.getByRole('button', { name: '워크스페이스 연결' }).click()
  await page.getByRole('tab', { name: /추가 근거/ }).click()
  await expect(page.getByText('이전 번들에만 있는 요청')).toBeVisible()
  await page.locator('.run-card').nth(1).click()
  await expect(page.getByRole('heading', { name: 'second-run' })).toBeVisible()
  await expect(page.getByText('이전 번들에만 있는 요청')).toHaveCount(0)
  releaseRequests()
  await page.getByRole('tab', { name: /검토 대장/ }).click()
  await page.locator('.table-footer').getByRole('button', { name: '다음 페이지' }).click()
  await expect(page.getByRole('alert')).toContainText('DATABASE_UNAVAILABLE')
  await expect(page.getByRole('row')).toHaveCount(0)
})
