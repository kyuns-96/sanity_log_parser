import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { makeDemo } from '../src/demo'
import type { Bundle, Detail, Page as ApiPage } from '../src/types'

async function demo(page: Page) {
  await page.goto('/?demo=1')
  await expect(page.getByRole('heading', { name: 'sanity-20261012-042' })).toBeVisible()
  await expect(page.getByText('CHECK-101', { exact: true })).toBeVisible()
}

test('explicit login, explicit demo, and memory-only token', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '검토 공간에 연결' })).toBeVisible()
  await expect(page.getByText('Atlas', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '데모 둘러보기' }).click()
  await expect(page.getByRole('heading', { name: 'sanity-20261012-042' })).toBeVisible()
  await expect(page.getByText('데모 모드', { exact: true })).toBeVisible()
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
  await expect(page.getByRole('dialog').getByText('[합성 근거] r-demo-001:', { exact: false })).toBeVisible()
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
