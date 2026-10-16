import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'

const origin = 'http://127.0.0.1:8003'
const base = (suffix: string) => `/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports/qa-rationale-${suffix}`
const route = (suffix: string) => `/workspace?project=project-1&block=project-1-cpu&version=project-1-cpu-post-1&tab=reports&eco=chip_finish&item=gca_constraints&review=qa-rationale-${suffix}`
type Cluster = { revision: number; current: { confirmed: boolean; groups: { id: string; label: string; record_ids: string[] }[]; rationales?: Record<string, { status: string; content: { reason: string }; proposal: unknown }> } }
async function cluster(page: Page, suffix: string): Promise<Cluster> { return (await page.request.get(`${base(suffix)}/clustering`)).json() }
async function post(page: Page, suffix: string, path: string, body: object) {
  const session = await (await page.request.get('/workspace/api/session')).json()
  return page.request.post(`${base(suffix)}${path}`, { data: body, headers: { Origin: origin, 'X-CSRF-Token': session.csrf } })
}
async function open(page: Page, suffix: string, user = 'user04') {
  await page.goto(route(suffix))
  await page.getByLabel('사용자 ID', { exact: true }).fill(user)
  await page.getByLabel('비밀번호', { exact: true }).fill('sanity-workspace')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.locator('.ws-rationale[data-rationale-state]')).toBeVisible()
}
async function fresh(page: Page, suffix: string) {
  const before = await cluster(page, suffix)
  const response = await post(page, suffix, '/clustering/proposals', { expected_revision: before.revision, request_id: crypto.randomUUID() })
  expect(response.ok()).toBe(true)
  await page.reload()
  await expect(page.locator('.ws-rationale')).toHaveAttribute('data-rationale-state', 'DRAFT')
  return cluster(page, suffix)
}

test('human edits keep the original, persist on reload and bulk confirmation opens judgments', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message))
  await open(page, 'edit'); const before = await fresh(page, 'edit')
  const gid = before.current.groups[0].id
  const source = await (await page.request.get(`${base('edit')}/source`)).text()
  await page.getByRole('button', { name: '근거 수정', exact: true }).click()
  await page.getByLabel('분류 기준', { exact: true }).fill('해당 원본 경로의 같은 설정 누락을 묶습니다. 다른 경로의 위반과 구분하며 공통 원인은 확인되지 않았습니다.')
  await page.getByLabel('작성·수정 사유', { exact: true }).fill('합성 검토자가 원안의 불확실성 설명을 보완했습니다.')
  await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작' })).toBeDisabled()
  await page.getByRole('button', { name: '근거 저장·확인', exact: true }).click()
  await expect(page.locator('.ws-rationale')).toHaveAttribute('data-rationale-state', 'CONFIRMED')
  const saved = await cluster(page, 'edit')
  expect(saved.revision).toBe(before.revision+1)
  expect(saved.current.rationales![gid].proposal).toMatchObject(before.current.rationales![gid].proposal as Record<string, unknown>)
  await page.reload()
  await expect(page.locator('.ws-rationale-text').first()).toContainText('공통 원인은 확인되지 않았습니다')
  await page.getByText('원안·참고 사례·이력', { exact: true }).click()
  await page.getByText('이 그룹의 근거 변경 이력', { exact: false }).click()
  await expect(page.locator('.ws-rationale-disclosure[open]')).toContainText('불확실성 설명을 보완')
  await page.screenshot({ path: 'artifacts/rationale-human-review.png', fullPage: true })
  await page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
  await expect(page.getByRole('region', { name: '2단계 확정 그룹별 판정', exact: true })).toBeVisible()
  const confirmed = await cluster(page, 'edit')
  expect(Object.values(confirmed.current.rationales!).every(row => row.status === 'CONFIRMED')).toBe(true)
  expect(await (await page.request.get(`${base('edit')}/source`)).text()).toBe(source)
  expect(errors).toEqual([])
})

test('dragging marks only affected explanations stale and the current reason can be explicitly revised', async ({ page }) => {
  await open(page, 'move'); const draft = await fresh(page, 'move')
  expect((await post(page, 'move', '/clustering', { action: 'CONFIRM', expected_revision: draft.revision, request_id: crypto.randomUUID() })).ok()).toBe(true)
  await page.reload()
  const before = await cluster(page, 'move'), groups = before.current.groups
  const from = groups[0], to = groups[1], record = from.record_ids[0]
  const handle = page.getByRole('button', { name: `${record} 로그 이동`, exact: true })
  const target = page.locator(`button[data-log-drop="${to.id}"]`)
  await handle.scrollIntoViewIfNeeded(); const start = (await handle.boundingBox())!, end = (await target.boundingBox())!
  await page.mouse.move(start.x+start.width/2, start.y+start.height/2); await page.mouse.down()
  await page.mouse.move(end.x+end.width/2, end.y+end.height/2, { steps: 15 }); await page.mouse.up()
  await expect(page.locator('.ws-rationale')).toHaveAttribute('data-rationale-state', 'STALE')
  await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true })).toBeDisabled()
  const moved = await cluster(page, 'move')
  expect(moved.current.rationales![from.id].status).toBe('STALE')
  expect(moved.current.rationales![to.id].status).toBe('STALE')
  expect(moved.current.rationales![groups[2].id].status).toBe('CONFIRMED')
  const block = await post(page, 'move', '/clustering', { action: 'CONFIRM', expected_revision: moved.revision, request_id: crypto.randomUUID() })
  expect(block.status()).toBe(409)
  await page.getByRole('button', { name: '근거 수정', exact: true }).click()
  await page.getByLabel('분류 기준', { exact: true }).fill('이동된 로그를 포함해 함께 조사할 설정 누락입니다. 다른 그룹과 별도 조사하며 동일 원인은 미확인입니다.')
  await page.getByLabel('작성·수정 사유', { exact: true }).fill('이동 후 전체 구성에 맞게 근거 수정')
  await page.getByRole('button', { name: '근거 저장·확인', exact: true }).click()
  await expect(page.locator('.ws-rationale')).toHaveAttribute('data-rationale-state', 'CONFIRMED')
  await page.screenshot({ path: 'artifacts/rationale-after-drag.png', fullPage: true })
})

test('legacy confirmed history shows missing reasons and cannot enable new judgments', async ({ page }) => {
  await open(page, 'legacy')
  await expect(page.locator('.ws-rationale')).toHaveAttribute('data-rationale-state', 'MISSING')
  await expect(page.locator('.ws-rationale')).toContainText('저장된 분류 근거가 없습니다')
  await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: /그룹별 판정/ })).toBeDisabled()
  await expect(page.getByRole('button', { name: '근거 작성', exact: true })).toBeVisible()
  await page.screenshot({ path: 'artifacts/rationale-legacy-missing.png', fullPage: true })
})

test('TOP reads group reasons and history without editing controls', async ({ page }) => {
  await open(page, 'scope', 'user01')
  await expect(page.locator('.ws-rationale-text').first()).not.toBeEmpty()
  await expect(page.getByRole('button', { name: '근거 수정', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '근거 채택', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true })).toHaveCount(0)
})

test('selected original logs become this group’s representative evidence without invented line positions', async ({ page }) => {
  await open(page, 'scope'); const before = await fresh(page, 'scope')
  const group = before.current.groups[0], selected = group.record_ids.at(-1)!
  await page.getByRole('checkbox', { name: `${selected} 로그 선택`, exact: true }).check()
  await page.getByRole('button', { name: '근거 수정', exact: true }).click()
  await page.getByRole('button', { name: '선택한 로그를 대표 근거로 지정', exact: true }).click()
  await page.getByLabel('분류 기준', { exact: true }).fill('선택한 원본 메시지와 같은 설정 누락을 묶습니다. 다른 경로와 구분하며 원인은 미확인입니다.')
  await page.getByLabel('작성·수정 사유', { exact: true }).fill('대표 원본을 마지막 로그로 교체하는 합성 검증')
  await page.getByRole('button', { name: '초안 저장', exact: true }).click()
  await expect(page.locator('.ws-rationale')).toHaveAttribute('data-rationale-state', 'DRAFT')
  const current = await cluster(page, 'scope')
  const detail = await (await page.request.get(`${base('scope')}/groups/${group.id}/rationale?expected_revision=${current.revision}`)).json()
  expect(detail.rationale.content.evidence_ids).toEqual([selected])
  expect(detail.samples).toHaveLength(1)
  const original = await (await page.request.get(base('scope'))).json()
  const record = original.records.find((r: { id: string }) => r.id === selected)
  expect([detail.samples[0].raw, detail.samples[0].line_start, detail.samples[0].line_end]).toEqual([record.raw, record.line_start, record.line_end])
})
