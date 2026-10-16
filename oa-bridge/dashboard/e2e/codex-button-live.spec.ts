import { test, expect } from '@playwright/test'
import { writeFileSync } from 'node:fs'

const base = '/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports/codex-mock-feedback'
const route = '/workspace?project=project-1&block=project-1-cpu&version=project-1-cpu-post-1&tab=reports&eco=chip_finish&item=gca_constraints&review=codex-mock-feedback'

test('AI button calls the real model once and restores the saved request after page reload', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(route)
  await page.getByLabel('사용자 ID', { exact: true }).fill('user04')
  await page.getByLabel('비밀번호', { exact: true }).fill('sanity-workspace')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  async function revealModelButton() {
    const support = page.locator('.ws-workbench-support')
    if (await support.isVisible()) {
      await support.locator(':scope > summary').click()
      const rebuild = page.locator('.ws-rebuild-groups')
      if (await rebuild.isVisible()) await rebuild.locator(':scope > summary').click()
    }
  }
  await expect(page.locator('.ws-workbench h1')).toHaveText('로그 검토')
  await revealModelButton()
  const button = page.getByRole('button', { name: 'AI로 그룹 만들기', exact: true })
  await expect(button).toHaveText('AI로 그룹 만들기')
  await expect(button).toBeEnabled()
  const original = await (await page.request.get(base+'/workbench')).json()
  const por = await (await page.request.get('/workspace/api/blocks/project-1-cpu/versions')).json()
  const [sent] = await Promise.all([
    page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/clustering/ai')),
    button.click(),
  ])
  const command = sent.postDataJSON()
  await expect(button).toBeDisabled()
  await page.reload()
  await expect.poll(async () => (await (await page.request.get(base+'/clustering/ai')).json()).job?.status,
    { timeout: 200_000, intervals: [500, 1000, 2000] }).toBe('COMPLETED')
  await page.reload()
  await expect(page.locator('.ws-workbench h1')).toHaveText('로그 검토')
  await revealModelButton()
  await expect(button).toBeEnabled({ timeout: 10_000 })
  const after = await (await page.request.get(base+'/workbench')).json()
  expect(after.revision).toBe(original.revision+1)
  expect(after.current.model_called).toBe(true)
  expect(after.current.model_metadata.model).toBe('gpt-6-luna')
  expect(after.current.model_metadata.usage.output_tokens).toBeGreaterThan(0)
  expect(after.current.references).toHaveLength(1)
  expect(after.summary.confirmed).toBe(false)
  expect(after.source_sha256).toBe(original.source_sha256)
  expect((await (await page.request.get('/workspace/api/blocks/project-1-cpu/versions')).json()).por).toEqual(por.por)
  const session = await (await page.request.get('/workspace/api/session')).json()
  const replay = await page.request.post(base+'/clustering/ai', { data: command, headers: {
    Origin: 'http://127.0.0.1:8003', 'X-CSRF-Token': session.csrf,
  } })
  expect(replay.status()).toBe(202)
  expect((await replay.json()).event_id).toBe(after.current.id)
  const replayed = await (await page.request.get(base+'/workbench')).json()
  expect(replayed.revision).toBe(after.revision)
  expect(replayed.ai.remaining).toBe(original.ai.remaining-1)
  await page.getByText('현재 분류의 기준과 출처', { exact: true }).click()
  await expect(page.locator('.ws-cluster-references')).toContainText('초기 분류 모델: gpt-6-luna')
  await page.screenshot({ path: 'artifacts/codex-button-desktop.png', fullPage: true, animations: 'disabled' })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(button).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1)).toBe(true)
  await page.screenshot({ path: 'artifacts/codex-button-mobile.png', fullPage: true, animations: 'disabled' })
  expect(errors).toEqual([])
  writeFileSync('artifacts/codex-button-live-result.json', JSON.stringify({
    status: 'PASSED', real_model_called: true, model_calls: 1, reload_restored_request: true,
    repeated_uuid_no_new_call: true, source_and_por_unchanged: true, model: after.current.model_metadata,
    report_id: after.report.id, revision: after.revision, groups: after.summary.total,
    case_references: after.current.references.length,
  }, null, 2))
})
