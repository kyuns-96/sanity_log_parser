import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'

const route = (report: string) => `/workspace?project=project-1&block=project-1-cpu&version=project-1-cpu-post-1&tab=reports&eco=chip_finish&item=gca_constraints&review=${report}`

async function login(page: Page, report: string, user = 'user04') {
  await page.goto(route(report))
  await page.getByLabel('사용자 ID', { exact: true }).fill(user)
  await page.getByLabel('비밀번호', { exact: true }).fill('sanity-workspace')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Sanity item 순차 검토' })).toBeVisible()
}

test('actual model output shows the confirmed name, new pattern and DB case reference after reload', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await login(page, 'codex-mock-feedback')
  const panel = page.getByRole('region', { name: 'Sanity item 순차 검토' })
  await panel.getByText('설정과 이전 기록', { exact: true }).click()
  await panel.getByText('AI로 그룹 다시 만들기', { exact: true }).click()
  await expect(panel).toContainText('gpt-6-luna')
  await expect(panel.getByRole('button', { name: 'AI로 그룹 만들기', exact: true })).toBeEnabled()
  await expect(panel.locator('.ws-workbench-group-list > div')).toHaveCount(2)
  await expect(panel.locator('.ws-workbench-group-list')).toContainText('제약 설정 누락 통합 검토')
  await expect(panel.locator('.ws-review-progress > span').first()).toContainText('12건')
  await expect(panel.getByRole('button', { name: '2 그룹별 판정', exact: false })).toBeDisabled()
  await expect(panel.getByText('다음 보고서에서 이 분류 기준을 참고하도록 설정', { exact: true })).toHaveCount(0)
  await panel.getByText('현재 분류의 기준과 출처', { exact: true }).click()
  await expect(panel.locator('.ws-cluster-references')).toContainText('초기 분류 모델: gpt-6-luna')
  const view = await (await page.request.get('/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports/codex-mock-feedback/workbench')).json()
  expect(view.current.references).toHaveLength(1)
  expect(view.current.references[0].report_id).toBe('codex-mock-source')
  await expect(panel.locator('.ws-cluster-references')).toContainText(`codex-mock-source · 분류 개정 ${view.current.references[0].revision}`)
  await expect(panel.locator('.ws-cluster-references')).toContainText('입력 사례 1건')
  await page.reload()
  await expect(panel.locator('.ws-workbench-group-list > div')).toHaveCount(2)
  await expect(panel.locator('.ws-workbench-detail .ws-paged-record')).toHaveCount(8)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: 'artifacts/codex-feedback-desktop.png', fullPage: true, animations: 'disabled' })
  expect(errors).toEqual([])
})

test('control has three initial categories and scripted source confirmation retains its two-group AI history', async ({ page }) => {
  await login(page, 'codex-mock-control')
  await expect(page.locator('.ws-workbench-group-list > div')).toHaveCount(3)
  await page.goto(route('codex-mock-source'))
  await expect(page.locator('.ws-workbench-group-list > div')).toHaveCount(1)
  const view = await (await page.request.get('/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports/codex-mock-source/workbench')).json()
  expect(view.current.confirmed).toBe(true)
  if (view.summary.confirmed) await expect(page.getByRole('button', { name: /그룹별 판정/ })).toBeEnabled()
  else await expect(page.getByRole('button', { name: /그룹별 판정/ })).toBeDisabled()
  await page.getByText('설정과 이전 기록', { exact: true }).click()
  await page.getByText('분류 변경 이력', { exact: true }).click()
  const history = page.locator('.ws-cluster-history')
  await expect(history).toContainText('분류 초안 · 개정 1 · 2개 그룹')
  await expect(history).toContainText('구성 수정 · 개정 2 · 1개 그룹')
  await expect(history).toContainText('분류 확정 · 개정 3 · 1개 그룹')
  await expect(history).toContainText('테스트 담당자의 검토 기준')
})

test('TOP reads the same AI groups on a narrow screen without editing or overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await login(page, 'codex-mock-feedback', 'user01')
  await expect(page.locator('.ws-workbench-group-list > div')).toHaveCount(2)
  await expect(page.getByRole('button', { name: '그룹 초안 만들기', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'AI로 그룹 만들기', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: false })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1)).toBe(true)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: 'artifacts/codex-feedback-mobile.png', fullPage: true, animations: 'disabled' })
})
