import { test, expect } from '@playwright/test'

const url = '/?demo=1&dataset=reports'

test('parsed library presents the requested sample size without live API or authentication', async ({ page }) => {
  const apiCalls: string[] = []
  const errors: string[] = []
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) apiCalls.push(request.url()) })
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(url)
  await expect(page.getByRole('heading', { name: '검사 결과 워크스페이스' })).toBeVisible()
  await expect(page.locator('.rp-library-total')).toContainText('1,000')
  await expect(page.getByLabel('전체 예시 구성')).toContainText('사용자 10명')
  await expect(page.getByLabel('전체 예시 구성')).toContainText('프로젝트 3개')
  await expect(page.getByLabel('전체 예시 구성')).toContainText('블록 9개')
  await expect(page.getByLabel('전체 예시 구성')).toContainText('분석 묶음 27개')
  await expect(page.getByLabel('가상 사용자').locator('option')).toHaveCount(10)
  await expect(page.getByRole('navigation', { name: '예시 프로젝트' }).getByRole('button')).toHaveCount(3)
  await expect(page.getByLabel('담당 블록 선택').getByRole('button')).toHaveCount(4)
  await expect(page.locator('.rp-stat-total')).toContainText('334')
  await expect(page.locator('tbody tr')).toHaveCount(25)
  expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 })
  expect(apiCalls).toEqual([])
  expect(errors).toEqual([])
  await page.screenshot({ path: 'artifacts/reports-library-desktop.png', fullPage: true, animations: 'disabled' })
})

test('virtual owners see their assigned blocks across projects and each TOP sees three blocks', async ({ page }) => {
  await page.goto(url)
  await page.getByLabel('가상 사용자').selectOption('user-04')
  await expect(page.getByRole('button', { name: 'Boreal 프로젝트', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'CPU 블록', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'GPU 블록', exact: true })).toHaveCount(0)
  await expect(page.locator('.rp-stat-total')).toContainText('112')
  await page.getByRole('button', { name: 'Cygnus 프로젝트', exact: true }).click()
  await expect(page.getByRole('button', { name: 'GPU 블록', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'CPU 블록', exact: true })).toHaveCount(0)
  await expect(page.locator('tbody tr td:nth-child(3)')).toHaveText(Array(25).fill('GPU'))
  await page.getByLabel('가상 사용자').selectOption('user-10')
  await expect(page.getByRole('button', { name: 'Atlas 프로젝트', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'CPU 블록', exact: true })).toBeVisible()
  for (const user of ['user-01', 'user-02', 'user-03']) {
    await page.getByLabel('가상 사용자').selectOption(user)
    await expect(page.getByLabel('담당 블록 선택').getByRole('button')).toHaveCount(4)
  }
})

test('tool filters, pagination and search cover the entire selected project', async ({ page }) => {
  await page.goto(url)
  for (const family of ['GCA', 'VCLP', 'SPYGLASS']) {
    await page.getByLabel('도구 필터').selectOption(family)
    const title = { GCA: 'GCA', VCLP: 'VC LP', SPYGLASS: 'SpyGlass' }[family]!
    await expect(page.locator('tbody tr td:nth-child(2)')).toHaveText(Array(25).fill(title))
  }
  await page.getByLabel('도구 필터').selectOption('all')
  await page.getByRole('button', { name: '다음 검사 페이지' }).click()
  await expect(page.locator('.pagination')).toContainText('26–50 / 334')
  await page.getByLabel('검사 검색').fill('sample-0991')
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await expect(page.getByRole('button', { name: 'sample-0991 보고서 보기' })).toBeVisible()
  await expect(page.getByRole('button', { name: '다음 검사 페이지' })).toBeDisabled()
  await page.getByLabel('검사 검색').fill('no-such-synthetic-check')
  await expect(page.locator('.rp-empty')).toContainText('조건에 맞는 검사 항목이 없습니다')
})

test('native-inspired reports and original line positions are shown for all three initial parsers', async ({ page }) => {
  await page.goto(url)
  for (const [id, parser, native] of [
    ['sample-0028', 'gca_public_text_v1', 'Report : report_constraint_analysis'],
    ['sample-0002', 'vclp_public_text_v1', 'ViolationSource :'],
    ['sample-0003', 'spyglass_moresimple_v1', 'rtl/ddr_sample.sv'],
  ]) {
    await page.getByLabel('검사 검색').fill(id)
    await page.getByRole('button', { name: `${id} 보고서 보기` }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toContainText(parser)
    await expect(dialog.locator('.rp-original')).toContainText(native)
    await expect(dialog.locator('.rp-record').first()).toContainText('원문')
    await expect(dialog.locator('.rp-record').first()).toContainText('observed')
    if (id === 'sample-0002') await expect(dialog).toContainText('UNVERIFIED_REPORT_COMPLETION')
    if (id === 'sample-0003') {
      await expect(dialog).toContainText('UNRECOGNIZED_SPYGLASS_CONTENT')
      await expect(dialog.locator('.rp-record')).toHaveCount(2)
    }
    await dialog.getByRole('button', { name: '닫기', exact: true }).click()
  }
})

test('missing reports cannot be downloaded and a received sample downloads its displayed bytes', async ({ page }) => {
  await page.goto(url)
  await page.getByLabel('결과 필터').selectOption('MISSING')
  await expect(page.locator('tbody tr td:nth-child(6)')).toHaveText(Array(await page.locator('tbody tr').count()).fill('미수신'))
  await page.getByRole('button', { name: 'sample-0001 보고서 보기' }).click()
  await expect(page.getByRole('dialog')).toContainText('원본 report 미수신')
  await expect(page.getByRole('button', { name: '합성 보고서 다운로드' })).toBeDisabled()
  await page.getByRole('button', { name: '닫기', exact: true }).click()
  await page.getByLabel('결과 필터').selectOption('all')
  await page.getByLabel('검사 검색').fill('sample-0012')
  await page.getByRole('button', { name: 'sample-0012 보고서 보기' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.locator('.rp-report-meta')).toContainText('PASS')
  await expect(dialog.locator('.rp-record')).toContainText('sample_zero_proof')
  const promise = page.waitForEvent('download')
  await dialog.getByRole('button', { name: '합성 보고서 다운로드' }).click()
  const download = await promise
  expect(download.suggestedFilename()).toBe('sample-0012-gca-synthetic.rpt')
  const stream = await download.createReadStream()
  let content = ''
  for await (const chunk of stream!) content += chunk.toString()
  expect(content).toBe(await dialog.locator('.rp-original').textContent())
})

test('390px mobile layout supports scoped filtering and the full report dialog without page overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(url)
  await expect(page.getByRole('heading', { name: '검사 결과 워크스페이스' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByLabel('가상 사용자').selectOption('user-04')
  await page.getByRole('button', { name: 'Cygnus 프로젝트', exact: true }).click()
  await page.getByLabel('검사 검색').fill('sample-0008')
  await page.getByRole('button', { name: 'sample-0008 보고서 보기' }).click()
  await expect(page.getByRole('dialog')).toContainText('Cygnus / GPU')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: 'artifacts/reports-library-mobile-dialog.png', animations: 'disabled' })
  await page.getByRole('button', { name: '닫기', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.screenshot({ path: 'artifacts/reports-library-mobile.png', fullPage: true, animations: 'disabled' })
})

test('preview requires explicit demo mode and a missing dataset remains an error', async ({ page }) => {
  await page.goto('/?dataset=reports')
  await expect(page.getByLabel('접근 토큰')).toBeVisible()
  await expect(page.getByLabel('가상 사용자')).toHaveCount(0)
  await page.route('**/sample-data/reports-library.json', route => route.fulfill({ status: 404, body: 'Missing synthetic snapshot' }))
  await page.goto(url)
  await expect(page.getByRole('alert')).toContainText('예시 데이터를 불러오지 못했습니다')
  await expect(page.locator('tbody tr')).toHaveCount(0)
  await expect(page.getByLabel('가상 사용자')).toHaveCount(0)
})
