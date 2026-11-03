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
  expect(errors).toEqual([])
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
