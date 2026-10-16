import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'

const origin = 'http://127.0.0.1:8003'
const block = '/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1'
const report = (suffix: string) => `qa-log-drag-${suffix}`
const api = (suffix: string) => `${block}/reports/${report(suffix)}`
const route = (suffix: string) => `/workspace?project=project-1&block=project-1-cpu&version=project-1-cpu-post-1&tab=reports&eco=chip_finish&item=gca_constraints&review=${report(suffix)}`
type Cluster = { revision: number; current: { groups: { id: string; label: string; record_ids: string[] }[]; reason: string; confirmed: boolean } }

async function getCluster(page: Page, suffix: string): Promise<Cluster> {
  const response = await page.request.get(`${api(suffix)}/clustering`)
  expect(response.ok()).toBe(true)
  return response.json()
}
async function post(page: Page, suffix: string, path: string, body: object) {
  const session = await (await page.request.get('/workspace/api/session')).json()
  return page.request.post(`${api(suffix)}${path}`, { data: body,
    headers: { Origin: origin, 'X-CSRF-Token': session.csrf } })
}
async function open(page: Page, suffix: string, reset = true) {
  await page.goto(route(suffix))
  await page.getByLabel('사용자 ID', { exact: true }).fill('user04')
  await page.getByLabel('비밀번호', { exact: true }).fill('sanity-workspace')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.locator('.ws-workbench-group-list > div').first()).toBeVisible()
  if (reset) {
    const before = await getCluster(page, suffix)
    const ranges = [[1, 100], [101, 115], [116, 120]]
    const groups = ['clock', 'delay', 'width'].map((key, index) => ({ id: `qa-${key}`,
      label: ['클럭 설정 누락', '입출력 지연 누락', '데이터 폭 불일치'][index],
      record_ids: Array.from({ length: ranges[index][1]-ranges[index][0]+1 }, (_, i) => `${report(suffix)}-e-${String(i+ranges[index][0]).padStart(3, '0')}`) }))
    const response = await post(page, suffix, '/clustering', { action: 'EDIT', groups,
      expected_revision: before.revision, request_id: crypto.randomUUID(), reason: '합성 UI 시나리오 시작' })
    expect(response.ok()).toBe(true)
    await page.reload()
  }
  await expect(page.locator('.ws-workbench-detail .ws-paged-record')).toHaveCount(50)
}
const grip = (page: Page, suffix: string, index = 1) => page.getByRole('button', { name: `${report(suffix)}-e-${String(index).padStart(3, '0')} 로그 이동`, exact: true })
const target = (page: Page, key = 'delay') => page.locator(`button[data-log-drop="qa-${key}"]`)
async function pointerDrop(page: Page, suffix: string, to: ReturnType<typeof target>, index = 1) {
  const handle = grip(page, suffix, index)
  // Keep the destination away from the edge before a direct mouse drop. Edge
  // holding intentionally scrolls the page; the touch scenario tests that path.
  await to.evaluate(element => element.scrollIntoView({ block: 'center' }))
  await handle.scrollIntoViewIfNeeded()
  const from = (await handle.boundingBox())!
  const dest = (await to.boundingBox())!
  await page.mouse.move(from.x+from.width/2, from.y+from.height/2)
  await page.mouse.down()
  await page.mouse.move(from.x+from.width/2-10, from.y+from.height/2, { steps: 2 })
  await page.mouse.move(dest.x+dest.width/2, dest.y+dest.height/2, { steps: 10 })
  await expect.poll(() => to.evaluate(element => element.classList.contains('ws-log-drop-active') || !!element.parentElement?.classList.contains('ws-log-drop-active'))).toBe(true)
  await page.mouse.up()
}
async function assertPartition(page: Page, suffix: string) {
  const value = await getCluster(page, suffix)
  const ids = value.current.groups.flatMap(group => group.record_ids)
  expect(ids).toHaveLength(120)
  expect(new Set(ids).size).toBe(120)
  expect(value.current.confirmed).toBe(false)
  return value
}

test('single-log drag saves a real MOVE, keeps source/PoR, and reloads in the destination', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await open(page, 'single')
  const before = await getCluster(page, 'single')
  const source = await (await page.request.get(`${api('single')}/source`)).text()
  const por = (await (await page.request.get('/workspace/api/blocks/project-1-cpu/versions')).json()).por
  await expect(page.locator('.ws-heading, .ws-version-bar, .ws-por-summary, .ws-item-catalog')).toHaveCount(0)
  await expect(page.locator('.ws-workbench-support details[open]')).toHaveCount(0)
  await pointerDrop(page, 'single', target(page))
  await expect(page.locator('.ws-notice')).toContainText('변경 이력을 저장')
  const after = await assertPartition(page, 'single')
  expect(after.revision).toBe(before.revision+1)
  expect(after.current.groups.find(group => group.id === 'qa-delay')?.record_ids).toContain(`${report('single')}-e-001`)
  expect(after.current.reason).toContain('사용자가 로그 1건')
  expect(await (await page.request.get(`${api('single')}/source`)).text()).toBe(source)
  expect((await (await page.request.get('/workspace/api/blocks/project-1-cpu/versions')).json()).por).toEqual(por)
  await expect(target(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(grip(page, 'single')).toBeVisible()
  await page.reload()
  expect((await getCluster(page, 'single')).revision).toBe(after.revision)
  await expect(page.locator('.ws-workbench-detail .ws-paged-record')).toHaveCount(50)
  await expect(page.getByRole('button', { name: '2 그룹별 판정', exact: false })).toBeDisabled()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: 'artifacts/workbench-drag-desktop.png', fullPage: true, animations: 'disabled' })
  expect(errors).toEqual([])
})

test('51 selected logs across record pages move together with bounded page requests', async ({ page }) => {
  const limits: string[] = []
  page.on('request', request => { if (request.url().includes('/clustering/records?')) limits.push(request.url()) })
  await open(page, 'multi')
  await page.getByLabel('현재 페이지 로그 50건 이하 선택').check()
  await page.getByRole('button', { name: '다음 그룹 원문 페이지' }).click()
  await expect(grip(page, 'multi', 51)).toBeVisible()
  await page.getByLabel(`${report('multi')}-e-051 로그 선택`, { exact: true }).check()
  await expect(page.locator('.ws-workbench-edit')).toContainText('51건')
  await pointerDrop(page, 'multi', target(page), 51)
  await expect(page.locator('.ws-notice')).toContainText('변경 이력을 저장')
  const value = await assertPartition(page, 'multi')
  expect(value.current.groups.find(group => group.id === 'qa-delay')?.record_ids).toHaveLength(66)
  expect(value.current.groups.find(group => group.id === 'qa-clock')?.record_ids).toHaveLength(49)
  expect(limits.length).toBeGreaterThan(2)
  expect(limits.every(url => new URL(url).searchParams.get('limit') === '50')).toBe(true)
  await expect(page.locator('.ws-workbench-detail .ws-paged-record')).toHaveCount(50)
})

test('drop onto new group asks for a name; cancelling changes nothing, and saving preserves all IDs', async ({ page }) => {
  await open(page, 'new')
  const before = await getCluster(page, 'new')
  const newGroup = page.getByRole('button', { name: '새 그룹으로 분리', exact: true })
  await pointerDrop(page, 'new', newGroup)
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('button', { name: '선택 로그를 새 그룹으로 분리' })).toBeDisabled()
  await dialog.getByRole('button', { name: '취소', exact: true }).click()
  expect((await getCluster(page, 'new')).revision).toBe(before.revision)
  await pointerDrop(page, 'new', newGroup)
  await page.getByLabel('분리할 새 그룹 이름').fill('별도 확인할 클럭')
  await page.getByLabel('이번 변경 사유').fill('사람이 다른 원인으로 확인한 합성 사례')
  await dialog.getByRole('button', { name: '선택 로그를 새 그룹으로 분리' }).click()
  await expect(dialog).toBeHidden()
  const value = await assertPartition(page, 'new')
  expect(value.current.groups).toHaveLength(4)
  expect(value.current.groups.find(group => group.label === '별도 확인할 클럭')?.record_ids).toEqual([`${report('new')}-e-001`])
  expect(value.current.reason).toBe('사람이 다른 원인으로 확인한 합성 사례')
  await expect(page.locator('.ws-selected-group-header h2').first()).toHaveText('별도 확인할 클럭')
})

test('keyboard pickup, cancellation and move work; rename and final confirmation keep the two stages', async ({ page }) => {
  await open(page, 'keyboard')
  const before = await getCluster(page, 'keyboard')
  await grip(page, 'keyboard').focus()
  await page.keyboard.press('Space')
  await expect(target(page).locator('..')).toHaveClass(/ws-log-drop-active/)
  await page.keyboard.press('Escape')
  expect((await getCluster(page, 'keyboard')).revision).toBe(before.revision)
  await grip(page, 'keyboard').focus()
  await page.keyboard.press('Enter')
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(page.locator('.ws-notice')).toContainText('변경 이력을 저장')
  const moved = await assertPartition(page, 'keyboard')
  expect(moved.current.groups.find(group => group.id === 'qa-width')?.record_ids).toContain(`${report('keyboard')}-e-001`)
  await page.getByRole('button', { name: '이름 변경', exact: true }).click()
  await page.getByLabel('선택 그룹 이름').fill('사람이 검토할 폭 불일치')
  await page.getByRole('button', { name: '그룹 이름 저장', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeHidden()
  await expect(page.locator('.ws-selected-group-header h2').first()).toHaveText('사람이 검토할 폭 불일치')
  let review = await getCluster(page, 'keyboard')
  for (const group of review.current.groups) {
    const response = await post(page, 'keyboard', `/groups/${group.id}/rationale`, { action: 'SAVE',
      expected_revision: review.revision, request_id: crypto.randomUUID(), reviewed: true,
      reason: '키보드 이동·이름 변경 후 합성 검토자가 그룹 전체의 근거를 확인', content: {
        reason: `${group.label}: 구성된 로그의 설정 문제를 함께 조사하며 다른 그룹과 별도 검토합니다. 공통 원인은 미확인입니다.`,
        evidence_ids: group.record_ids.slice(0, 2), reference_groups: [] } })
    expect(response.ok()).toBe(true)
    review = await getCluster(page, 'keyboard')
  }
  await page.reload()
  await page.getByText('설정과 이전 기록', { exact: true }).click()
  await page.getByText('전체 메모와 재사용 설정 (선택)', { exact: true }).click()
  await page.getByLabel('전체 분류 메모 (선택)', { exact: true }).fill('전체 120건의 그룹 배정을 확인했습니다.')
  await page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: false }).click()
  await expect(page.getByRole('region', { name: '2단계 확정 그룹별 판정' })).toBeVisible()
  await expect(page.locator('.ws-log-grip')).toHaveCount(0)
  expect((await getCluster(page, 'keyboard')).current.confirmed).toBe(true)
})

test('touch drag on mobile saves one log without horizontal overflow', async ({ browser }) => {
  const context = await browser.newContext({ baseURL: origin, viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })
  const page = await context.newPage()
  try {
    await open(page, 'touch')
    const handle = grip(page, 'touch')
    await handle.scrollIntoViewIfNeeded()
    const from = (await handle.boundingBox())!
    const session = await context.newCDPSession(page)
    const start = { x: from.x+from.width/2, y: from.y+from.height/2 }
    await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [start] })
    // The group rail is above the log pane on narrow screens. Holding near the
    // viewport edge scrolls it into view without releasing the picked-up log.
    await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: start.x, y: 40 }] })
    await expect.poll(async () => (await target(page).boundingBox())!.y, { timeout: 5000 }).toBeGreaterThan(100)
    const dest = (await target(page).boundingBox())!
    await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: dest.x+dest.width/2, y: dest.y+dest.height/2 }] })
    await expect(target(page).locator('..')).toHaveClass(/ws-log-drop-active/)
    await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
    await expect(page.locator('.ws-notice')).toContainText('변경 이력을 저장')
    const value = await assertPartition(page, 'touch')
    expect(value.current.groups.find(group => group.id === 'qa-delay')?.record_ids).toHaveLength(16)
    await expect(target(page)).toHaveAttribute('aria-pressed', 'true')
    await expect(grip(page, 'touch')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1)).toBe(true)
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: 'artifacts/workbench-drag-mobile.png', fullPage: true, animations: 'disabled' })
  } finally { await context.close() }
})

test('a concurrent revision rejects a stale drop and reloads the latest partition', async ({ page }) => {
  await open(page, 'conflict')
  const before = await getCluster(page, 'conflict')
  await grip(page, 'conflict').scrollIntoViewIfNeeded()
  const from = (await grip(page, 'conflict').boundingBox())!
  const dest = (await target(page).boundingBox())!
  await page.mouse.move(from.x+10, from.y+10); await page.mouse.down()
  await page.mouse.move(from.x-10, from.y+10)
  const response = await post(page, 'conflict', '/clustering/operations', { expected_revision: before.revision,
    request_id: crypto.randomUUID(), reason: '다른 창의 합성 변경', operation: { kind: 'RENAME', destination_id: 'qa-clock', label: '다른 창에서 변경한 클럭' } })
  expect(response.ok()).toBe(true)
  await page.mouse.move(dest.x+dest.width/2, dest.y+dest.height/2); await page.mouse.up()
  await expect(page.getByRole('alert')).toContainText('분류 개정이 변경되었습니다')
  await expect(page.locator('.ws-workbench-group-list')).toContainText('다른 창에서 변경한 클럭')
  const value = await assertPartition(page, 'conflict')
  expect(value.revision).toBe(before.revision+1)
  expect(value.current.groups.find(group => group.id === 'qa-delay')?.record_ids).toHaveLength(15)
})

test('a saved response lost in transit can be retried with the same UUID without another revision', async ({ page }) => {
  await open(page, 'retry')
  const before = await getCluster(page, 'retry')
  const ids: string[] = []
  await page.route(`**/reports/${report('retry')}/clustering/operations`, async route => {
    ids.push(route.request().postDataJSON().request_id)
    if (ids.length === 1) { const saved = await route.fetch(); expect(saved.ok()).toBe(true); await route.abort('failed') }
    else await route.continue()
  })
  await pointerDrop(page, 'retry', target(page))
  await expect(page.getByRole('alert')).toBeVisible()
  await pointerDrop(page, 'retry', target(page))
  await expect(page.locator('.ws-notice')).toContainText('변경 이력을 저장')
  expect(ids).toHaveLength(2)
  expect(ids[0]).toBe(ids[1])
  expect((await assertPartition(page, 'retry')).revision).toBe(before.revision+1)
})

test('same-group, outside and cancelled drops do not write; a read-only TOP has no drag handles', async ({ page, browser }) => {
  await open(page, 'cancel')
  const before = await getCluster(page, 'cancel')
  const writes: string[] = []
  page.on('request', request => { if (request.method() === 'POST' && request.url().includes('/clustering/operations')) writes.push(request.url()) })
  await pointerDrop(page, 'cancel', target(page, 'clock'))
  const from = (await grip(page, 'cancel').boundingBox())!
  await page.mouse.move(from.x+10, from.y+10); await page.mouse.down()
  await page.mouse.move(400, 330, { steps: 10 }); await page.mouse.up()
  await page.mouse.move(from.x+10, from.y+10); await page.mouse.down()
  await page.mouse.move(from.x-20, from.y+10); await page.keyboard.press('Escape'); await page.mouse.up()
  expect((await getCluster(page, 'cancel')).revision).toBe(before.revision)
  expect(writes).toEqual([])
  const context = await browser.newContext({ baseURL: origin })
  const top = await context.newPage()
  try {
    await top.goto(route('cancel'))
    await top.getByLabel('사용자 ID', { exact: true }).fill('user01')
    await top.getByLabel('비밀번호', { exact: true }).fill('sanity-workspace')
    await top.getByRole('button', { name: '로그인', exact: true }).click()
    await expect(top.locator('.ws-workbench-group-list > div')).toHaveCount(3)
    await expect(top.locator('.ws-log-grip, [data-log-drop]')).toHaveCount(0)
    await expect(top.getByRole('button', { name: '이름 변경', exact: true })).toHaveCount(0)
    await top.getByRole('button', { name: '보고서 목록', exact: true }).click()
    await expect(top.getByLabel('조회 버전')).toBeVisible()
    await expect(top.getByRole('region', { name: '블록의 현재 PoR' })).toBeVisible()
  } finally { await context.close() }
})
