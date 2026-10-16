import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'

const origin = 'http://127.0.0.1:8003'
const base = '/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports/qa-rationale-scope'
const route = '/workspace?project=project-1&block=project-1-cpu&version=project-1-cpu-post-1&tab=reports&eco=chip_finish&item=gca_constraints&review=qa-rationale-scope'
async function login(page: Page, url = route, user = 'user04') {
  await page.goto(url)
  await page.getByLabel('사용자 ID', { exact: true }).fill(user)
  await page.getByLabel('비밀번호', { exact: true }).fill('sanity-workspace')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.locator('.ws-workbench h1')).toHaveText('로그 검토')
}
async function fresh(page: Page) {
  const view = await (await page.request.get(base+'/workbench')).json()
  const session = await (await page.request.get('/workspace/api/session')).json()
  const result = await page.request.post(base+'/clustering/proposals', { headers: {
    Origin: origin, 'X-CSRF-Token': session.csrf,
  }, data: { expected_revision: view.revision, request_id: crypto.randomUUID() } })
  expect(result.ok()).toBe(true)
  await page.reload()
  await expect(page.locator('.ws-rationale')).toHaveAttribute('data-rationale-state', 'DRAFT')
}

test('arrival aligns groups, original logs and professional rationale in one viewport', async ({ page }) => {
  const external: string[] = []
  page.on('request', req => { if (req.url().endsWith('/clustering/ai') && req.method() === 'POST') external.push(req.url()) })
  await login(page); await fresh(page)
  await expect(page.getByRole('button', { name: /로그 분류/ })).toHaveAttribute('aria-current', 'step')
  await expect(page.locator('.ws-review-progress > span').first()).toHaveText('로그 12건3개 그룹')
  await expect(page.getByRole('button', { name: /그룹별 판정/ })).toBeDisabled()
  await expect(page.locator('.ws-bulk-confirm-note')).toContainText('전체 3개 그룹의 유효한 근거 초안을 일괄 채택')
  await expect(page.locator('.ws-workbench-support')).not.toHaveAttribute('open')
  await expect(page.getByLabel('전체 분류 메모 (선택)', { exact: true })).toBeHidden()
  await expect(page.locator('.ws-rationale-more')).not.toHaveAttribute('open')
  await expect(page.locator('.ws-rationale-samples')).not.toHaveAttribute('open')
  const panes = await Promise.all(['.ws-workbench-groups', '.ws-record-pages', '.ws-group-inspector'].map(selector => page.locator(selector).boundingBox()))
  expect(panes.every(box => box && box.y < 500)).toBe(true)
  expect(panes[0]!.x + panes[0]!.width).toBeLessThan(panes[1]!.x)
  expect(panes[1]!.x + panes[1]!.width).toBeLessThan(panes[2]!.x)
  const firstLog = await page.locator('.ws-paged-record').first().boundingBox()
  expect(firstLog!.y + firstLog!.height).toBeLessThan(900)
  await expect(page.getByRole('heading', { name: '분류 근거', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true })).toBeEnabled()
  const current = await (await page.request.get(base+'/clustering')).json()
  const first = current.current.groups[0]
  const reason = current.current.rationales[first.id].content.reason
  if (reason.length > 240) {
    await expect(page.locator('.ws-rationale-text').first()).toHaveText(reason.slice(0, 240)+'…')
    await page.getByRole('button', { name: /근거 전문 보기/ }).click()
  }
  await expect(page.locator('.ws-rationale-text').first()).toHaveText(reason)
  await page.getByRole('button', { name: '병합', exact: true }).click()
  await expect(page.getByText('병합할 그룹을 2개 이상 선택하세요.')).toBeVisible()
  await page.getByRole('checkbox', { name: `${first.label} 그룹 전체 선택`, exact: true }).check()
  await page.locator('.ws-workbench-group-heading').getByRole('button', { name: '취소', exact: true }).click()
  expect((await (await page.request.get(base+'/clustering')).json()).revision).toBe(current.revision)
  expect(external).toEqual([])
  await page.screenshot({ path: 'artifacts/guidance-first-visit.png', fullPage: true, animations: 'disabled' })
  // Follow the guide until only the current group remains. Asking for the next
  // explanation must keep its log context and editing controls available.
  for (let index = 0; index < 2; index++) {
    await page.getByRole('button', { name: '다음 미검토 근거', exact: true }).click()
    await expect(page.locator('.ws-rationale')).toHaveAttribute('data-rationale-state', 'DRAFT')
    await page.getByRole('button', { name: '근거 채택', exact: true }).click()
    await expect(page.locator('.ws-rationale')).toHaveAttribute('data-rationale-state', 'CONFIRMED')
  }
  await page.getByRole('button', { name: '다음 미검토 근거', exact: true }).click()
  await expect(page.locator('.ws-rationale')).toHaveAttribute('data-rationale-state', 'DRAFT')
  await expect(page.locator('.ws-workbench-group-list > div > button')).toHaveCount(1)
  await expect(page.getByRole('button', { name: '이름 변경', exact: true })).toBeEnabled()
  const lastGroup = await page.locator('.ws-selected-group-header h2').innerText()
  await page.getByRole('button', { name: '다음 미검토 근거', exact: true }).click()
  await expect(page.locator('.ws-notice')).toContainText('현재 그룹이 마지막 미검토 대상')
  await expect(page.getByRole('button', { name: '이름 변경', exact: true })).toBeEnabled()
  await expect(page.locator('.ws-selected-group-header h2')).toHaveText(lastGroup)
})

test('a person can follow the two stages through PASS, FAIL, hold, correction and completion', async ({ page }) => {
  await login(page); await fresh(page)
  const source = await (await page.request.get(base+'/source')).text()
  await page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
  await expect(page.getByRole('button', { name: /그룹별 판정/ })).toHaveAttribute('aria-current', 'step')
  await expect(page.locator('.ws-judgment-evidence')).not.toHaveAttribute('open')
  await expect(page.locator('.ws-review-advice')).toContainText('사내 AI 미연결')
  const review = page.locator('.ws-judgment-workspace')
  await review.getByRole('button', { name: '판정 제안 받기', exact: true }).click()
  await expect(review.locator('.ws-review-proposal')).toBeVisible()
  await expect(review.getByRole('button', { name: '판정 저장', exact: true })).toBeDisabled()
  for (const [index, outcome] of ['PASS', 'FAIL', '보류'].entries()) {
    await review.getByRole('radio', { name: new RegExp(outcome) }).check()
    await review.getByLabel('판정 근거', { exact: true }).fill(`합성 로그 전체를 확인한 뒤 ${outcome}로 정했습니다. 그룹 ${index+1}`)
    await review.getByRole('button', { name: '판정 저장', exact: true }).click()
    await expect(review.getByRole('status')).toContainText('사용자 판정과 근거를 저장')
    if (index < 2) await page.getByRole('button', { name: '다음 미검토 그룹', exact: true }).click()
  }
  let view = await (await page.request.get(base+'/workbench')).json()
  expect(view.summary.complete).toBe(false)
  expect(view.summary.counts).toMatchObject({ PASS: 1, FAIL: 1, DEFER: 1 })
  await review.getByRole('radio', { name: /PASS/ }).check()
  await review.getByLabel('판정 근거', { exact: true }).fill('보류했던 내용을 추가 확인해 통과로 정했습니다.')
  await review.getByRole('button', { name: '판정 저장', exact: true }).click()
  await expect(page.locator('.ws-review-complete')).toContainText('3개 그룹의 판정을 완료했습니다.')
  view = await (await page.request.get(base+'/workbench')).json()
  expect(view.summary.counts).toMatchObject({ PASS: 2, FAIL: 1, DEFER: 0 })
  expect(view.summary.complete).toBe(true)
  expect(await (await page.request.get(base+'/source')).text()).toBe(source)
  await page.screenshot({ path: 'artifacts/guidance-completed.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '보고서 목록으로', exact: true }).click()
  await expect(page.getByRole('heading', { name: '보고서 검토' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: '검사 규칙', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '상세 열 보기', exact: true }).click()
  await expect(page.getByRole('columnheader', { name: '검사 규칙', exact: true })).toBeVisible()
})

test('mobile explains a blocked next step and keeps an unfinished editor safe', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message))
  await login(page); await fresh(page)
  await page.getByRole('button', { name: '근거 수정', exact: true }).click()
  await page.getByLabel('분류 기준', { exact: true }).fill('아직 저장하지 않은 설명')
  await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true })).toBeDisabled()
  await expect(page.locator('.ws-step-hint')).toContainText('작성 중인 내용을 저장하거나 취소')
  const before = await page.locator('.ws-workbench-group-list button[aria-pressed=true]').textContent()
  await page.locator('.ws-workbench-group-list > div > button').nth(1).click()
  await expect(page.getByRole('alert')).toContainText('저장하거나 취소')
  expect(await page.locator('.ws-workbench-group-list button[aria-pressed=true]').textContent()).toBe(before)
  await expect(page.getByLabel('분류 기준', { exact: true })).toHaveValue('아직 저장하지 않은 설명')
  await page.locator('.ws-rationale-editor').getByRole('button', { name: '취소', exact: true }).click()
  await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true })).toBeEnabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1)).toBe(true)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: 'artifacts/guidance-mobile.png', fullPage: true, animations: 'disabled' })
  expect(errors).toEqual([])
})

test('a reader can inspect the same explanations without seeing controls to change them', async ({ page }) => {
  await login(page); await fresh(page)
  await page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
  await expect(page.locator('.ws-judgment-workspace')).toBeVisible()
  await page.getByRole('button', { name: '로그아웃', exact: true }).click()
  await login(page, route, 'user01')
  await expect(page.getByRole('button', { name: /로그 분류/ })).toContainText('확정')
  await expect(page.getByRole('button', { name: '판정 보기', exact: true })).toBeEnabled()
  await expect(page.locator('.ws-rationale-text').first()).not.toBeEmpty()
  await expect(page.getByText('조회 전용 ·', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: '근거 채택', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: /그룹별 판정/ }).click()
  await expect(page.getByRole('button', { name: '판정 저장', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '판정 제안 받기', exact: true })).toBeDisabled()
})
