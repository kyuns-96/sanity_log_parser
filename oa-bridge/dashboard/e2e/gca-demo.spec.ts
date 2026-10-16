import { test, expect } from '@playwright/test'
import { makeGcaDemo } from '../src/gcaDemo'

test('GCA examples keep project, block and run reviews separate', async ({ page }) => {
  const requests: string[] = []
  const errors: string[] = []
  page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/api/')) requests.push(r.url()) })
  page.on('pageerror', e => errors.push(e.message))
  await page.goto('/?demo=1&dataset=gca')
  await expect(page.getByRole('heading', { name: 'gca-atlas-cpu-1013', exact: true })).toBeVisible()
  await expect(page.getByText('CLK_0003', { exact: true })).toBeVisible()
  await page.getByLabel('조회 실행', { exact: true }).selectOption({ label: 'gca-atlas-cpu-1013' })
  await expect(page.getByRole('heading', { name: 'gca-atlas-cpu-1013', exact: true })).toBeVisible()
  await expect(page.getByText('2개 프로젝트 · 4개 블록 · 5개 실행', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '검토 의견 기록', exact: true }).click()
  await page.getByLabel('판단 사유').fill('Atlas CPU 최신 실행에서 소스 경로 확인 필요')
  await page.getByRole('button', { name: '검토 저장', exact: true }).click()
  await expect(page.getByText('검토 의견이 저장되었습니다.', { exact: false })).toBeVisible()
  await page.getByLabel('조회 실행', { exact: true }).selectOption({ label: 'gca-atlas-cpu-1012' })
  await expect(page.getByRole('heading', { name: 'gca-atlas-cpu-1012', exact: true })).toBeVisible()
  await expect(page.getByText('Atlas CPU 최신 실행에서 소스 경로 확인 필요', { exact: true })).toHaveCount(0)
  await page.getByLabel('GCA 블록').selectOption('GPU')
  await expect(page.getByRole('heading', { name: 'gca-atlas-gpu-1013', exact: true })).toBeVisible()
  await page.getByLabel('GCA 프로젝트').selectOption('Boreal')
  await expect(page.getByRole('heading', { name: 'gca-boreal-cpu-1013', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '검토 의견 기록', exact: true })).toBeVisible()
  await expect(page.getByText('Atlas CPU 최신 실행에서 소스 경로 확인 필요', { exact: true })).toHaveCount(0)
  await page.getByLabel('GCA 프로젝트').selectOption('Atlas')
  await expect(page.getByText('Atlas CPU 최신 실행에서 소스 경로 확인 필요', { exact: true })).toBeVisible()
  expect(requests).toEqual([])
  expect(errors).toEqual([])
})

test('GCA report provenance, raw records and downloads agree', async ({ page }) => {
  await page.goto('/?demo=1&dataset=gca')
  const source = page.getByRole('region', { name: 'GCA 예시와 출처' })
  await expect(source.getByRole('link')).toHaveCount(2)
  await expect(source.getByRole('link', { name: /Synopsys GCA 백서/ })).toHaveAttribute('href', /synopsys\.com/)
  await source.getByText('예시 보고서 전체 보기', { exact: true }).click()
  await expect(source.locator('pre')).toContainText('r-atlas-cpu-1013-clock_source-4')
  await expect(source.locator('pre')).toContainText('not native PrimeTime output')
  const download = page.waitForEvent('download')
  await source.getByRole('button', { name: '예시 보고서 다운로드 (.rpt)' }).click()
  const file = await download
  expect(file.suggestedFilename()).toBe('example-u-atlas-cpu-1013-clock_source.rpt')
  const stream = await file.createReadStream()
  let text = ''
  for await (const chunk of stream!) text += chunk.toString()
  expect(text).toBe(await source.locator('pre').textContent())
  await page.getByRole('button', { name: '근거 요청', exact: true }).click()
  await page.getByLabel('요청할 기록 ID').fill('r-atlas-cpu-1013-clock_source-4')
  await page.getByLabel('요청 사유').fill('대표 예시 밖의 기록 확인')
  await page.getByRole('button', { name: '요청 등록', exact: true }).click()
  await page.getByRole('button', { name: '응답 수신', exact: true }).click()
  await page.getByRole('button', { name: '수신 근거 보기', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('soc/cpu/u_clk_3/div2/CP')
  await expect(page.getByRole('dialog')).toContainText('GCA 검사 근거 예시')
})

test('mobile GCA scope selection preserves a missing report as incomplete', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/?demo=1&dataset=gca')
  await page.getByLabel('GCA 프로젝트').selectOption('Boreal')
  await page.getByLabel('GCA 블록').selectOption('DDR')
  await expect(page.getByRole('heading', { name: 'gca-boreal-ddr-1013', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'GCA · Block-to-top case 상세 보기', exact: true }).click()
  await expect(page.getByText('report가 아직 도착하지 않은 상황', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: '검토 의견 기록', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '예시 보고서 다운로드 (.rpt)' })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('GCA evidence adapter rejects a record from another project or item', async () => {
  const api = makeGcaDemo()
  const path = '/api/bundles/b-gca-atlas-cpu-1013/evidence'
  const request = { request_id: 'scope-test', unit_id: 'u-atlas-cpu-1013-clock_source', reason: 'test' }
  await expect(api(path, { ...request, record_ids: ['r-boreal-cpu-1013-clock_source-1'] })).rejects.toThrow('INVALID_RECORD_SCOPE')
  await expect(api(path, { ...request, record_ids: ['r-atlas-cpu-1013-case_conflict-1'] })).rejects.toThrow('INVALID_RECORD_SCOPE')
  await expect(api(path, { ...request, record_ids: ['r-atlas-cpu-1013-clock_source-4'] })).resolves.toEqual({ status: 'REQUESTED' })
})
