import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'

const studioUrl = '/studio.html'
const groupIds = ['generated', 'divider', 'boundary', 'unused', 'missing']

async function openStudio(page: Page, query = '') {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const response = await page.goto(studioUrl + query)
  expect(response?.status()).toBe(200)
  await expect(page.getByRole('heading', { name: 'CLK_0003', exact: true })).toBeVisible()
  await page.evaluate(() => document.fonts.ready)
}

async function chooseVerdict(page: Page, name: 'PASS' | 'FAIL' | '보류') {
  await page.locator('.human-decision').getByText(name, { exact: true }).click()
  await expect(page.getByRole('radio', { name, exact: true })).toBeChecked()
}

test.describe('independent Studio workbench preview', () => {
  test.describe.configure({ mode: 'parallel' })

  test('stable group ordinals, full-group search, paging and checkbox selection retain the matching original', async ({ page }) => {
    await openStudio(page)
    await expect(page.locator('.group-ordinal')).toHaveText(['01', '02', '03', '04', '05'])
    await expect(page.locator('.group-list svg')).toHaveCount(0)
    await page.getByRole('searchbox', { name: '그룹 검색' }).fill('분주')
    await expect(page.locator('.group-list .group-item')).toHaveCount(1)
    await expect(page.getByTestId('group-divider').locator('.group-ordinal')).toHaveText('02')
    await page.getByTestId('group-divider').click()
    await expect(page.locator('.evidence-heading h2')).toHaveText('분주 관계 확인')
    await expect(page.locator('.source-id')).toContainText('SYN-0049')
    await page.getByRole('searchbox', { name: '그룹 검색' }).fill('')

    await page.getByRole('checkbox', { name: 'SYN-0050 선택', exact: true }).click()
    await expect(page.locator('.record-count')).toContainText('1건 선택')
    await expect(page.locator('.source-id')).toContainText('SYN-0049')
    const secondRow = page.getByRole('row', { name: 'SYN-0050 원문 보기', exact: true })
    await secondRow.focus()
    await secondRow.press('Enter')
    await expect(page.locator('.source-id')).toContainText('SYN-0050')
    await page.getByRole('checkbox', { name: '현재 페이지 전체 선택' }).click()
    await expect(page.locator('.record-count')).toContainText('6건 선택')
    await page.getByRole('button', { name: '로그 선택 해제', exact: true }).click()
    await expect(page.locator('.record-count')).toHaveText('29건')
    await page.getByRole('button', { name: '다음 로그 페이지', exact: true }).click()
    await expect(page.locator('.source-id')).toContainText('SYN-0055')
    await expect(page.locator('.record-pagination')).toContainText('7–12')

    await page.getByRole('searchbox', { name: '로그 검색' }).fill('u_div_02')
    await expect(page.locator('.target-path')).toHaveCount(1)
    await expect(page.locator('.source-id')).toContainText('SYN-0050')
    await expect(page.getByRole('button', { name: '이전 로그 페이지', exact: true })).toBeDisabled()
    await page.getByRole('region', { name: 'AI 판정 제안' }).getByRole('button', { name: '원문 근거 29건', exact: true }).click()
    await expect(page.getByRole('searchbox', { name: '로그 검색' })).toHaveValue('')
    await expect(page.locator('.target-path')).toHaveCount(6)
    await expect(page.locator('.source-id')).toContainText('SYN-0049')
    await page.getByRole('searchbox', { name: '로그 검색' }).fill('no-such-synthetic-target')
    await expect(page.getByText('검색 결과가 없습니다.', { exact: true })).toBeVisible()
    await page.getByRole('searchbox', { name: '로그 검색' }).fill('')
    await expect(page.locator('.target-path')).toHaveCount(6)
    await expect(page.locator('.source-id')).toContainText('SYN-0049')
  })

  test('human verdicts require reasons, preserve the tool FAIL and leave deferred groups incomplete', async ({ page }) => {
    await openStudio(page)
    const reason = page.getByRole('textbox', { name: '판정 근거', exact: true })
    const confirm = page.getByTestId('confirm-verdict')
    await reason.fill('')
    await expect(confirm).toBeDisabled()
    await reason.fill('짧은 근거')
    await expect(confirm).toBeDisabled()
    await chooseVerdict(page, 'PASS')
    await reason.fill('합성 원문과 적용 조건을 확인하여 도구 결과와 별도로 PASS로 판단합니다.')
    await confirm.click()
    await expect(page.getByTestId('group-generated')).toContainText('PASS · 확정')
    await expect(page.locator('.original-result')).toContainText('FAIL')
    await expect(page.getByRole('region', { name: 'AI 판정 제안' })).toContainText('보류')

    for (const id of groupIds.slice(1)) {
      await page.getByTestId('group-' + id).click()
      await chooseVerdict(page, '보류')
      await reason.fill('필수 원본 근거가 충분하지 않아 추가 확인 전까지 판정을 보류합니다.')
      await confirm.click()
    }
    await expect(page.locator('.progress-label')).toContainText('5 / 5')
    await expect(page.locator('.completion-note')).toContainText('보류 4')
    await expect(page.locator('.completion-note')).toContainText('미확정 0')
    await expect(page.locator('.completion-note')).toContainText('검토 진행 중')
    const filter = page.getByRole('combobox', { name: '그룹 확정 상태' })
    await filter.click()
    await page.getByRole('option', { name: '미확정 · 0', exact: true }).click()
    await expect(page.getByText('조건에 맞는 그룹이 없습니다.', { exact: true })).toBeVisible()
    await filter.click()
    await page.getByRole('option', { name: '확정 · 5', exact: true }).click()
    await expect(page.locator('.group-list .group-item')).toHaveCount(5)
    await expect(page.getByTestId('group-generated').locator('.group-ordinal')).toHaveText('01')
  })

  test('tabs and history Sheet support keyboard navigation and theme changes preserve drafts', async ({ page }) => {
    await openStudio(page)
    const basisTab = page.getByRole('tab', { name: '분류 근거', exact: true })
    await basisTab.click()
    await expect(basisTab).toHaveAttribute('aria-controls', 'pane-basis')
    await expect(page.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'tab-basis')
    await expect(page.getByRole('tabpanel')).toContainText('왜 함께 묶었는가')
    await basisTab.press('ArrowRight')
    await expect(page.getByRole('tab', { name: '이력', exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('tabpanel')).toContainText('로그 분류 확정')

    const history = page.getByRole('button', { name: '검토 이력', exact: true })
    await history.click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('실제 모델 호출 없는 합성 예시')
    await dialog.getByRole('button').last().focus()
    await page.keyboard.press('Tab')
    expect(await dialog.evaluate(element => element.contains(document.activeElement))).toBe(true)
    await page.keyboard.press('Escape')
    await expect(dialog).not.toBeVisible()
    await expect(history).toBeFocused()

    const draft = '테마를 바꾸어도 작성 중인 사람의 판단 근거가 유지되어야 합니다.'
    await page.getByRole('textbox', { name: '판정 근거', exact: true }).fill(draft)
    await chooseVerdict(page, 'PASS')
    await page.getByRole('button', { name: '어두운 화면으로 전환', exact: true }).click()
    await expect(page.locator('html')).toHaveClass(/dark/)
    await expect(page.getByRole('textbox', { name: '판정 근거', exact: true })).toHaveValue(draft)
    await expect(page.getByRole('radio', { name: 'PASS', exact: true })).toBeChecked()
    await page.getByRole('button', { name: '밝은 화면으로 전환', exact: true }).click()
    await expect(page.locator('html')).not.toHaveClass(/dark/)
    await expect(page.getByRole('textbox', { name: '판정 근거', exact: true })).toHaveValue(draft)
    await expect(page.getByRole('tab', { name: '이력', exact: true })).toHaveAttribute('aria-selected', 'true')
  })

  test('desktop and phone layouts have no horizontal overflow and desktop confirmation stays visible', async ({ page }, testInfo) => {
    for (const [width, height] of [[1440, 900], [1280, 800], [390, 844]]) {
      await page.setViewportSize({ width, height })
      await openStudio(page)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      const confirm = page.getByTestId('confirm-verdict')
      if (width > 720) {
        const box = await confirm.boundingBox()
        expect(box).not.toBeNull()
        expect(box!.y).toBeGreaterThanOrEqual(0)
        expect(box!.y + box!.height).toBeLessThanOrEqual(height)
      } else {
        await expect(page.locator('.original-result')).toBeVisible()
        await expect(page.locator('.original-result')).toContainText('FAIL')
        await confirm.scrollIntoViewIfNeeded()
        await expect(confirm).toBeVisible()
        await page.getByRole('textbox', { name: '판정 근거', exact: true }).fill('모바일 화면에서도 최종 근거를 작성하고 확정할 수 있습니다.')
        await confirm.click()
        await expect(page.locator('.progress-label')).toContainText('1 / 5')
      }
      await page.screenshot({ path: testInfo.outputPath(`studio-${width}.png`), fullPage: true, animations: 'disabled' })
    }
  })

  test('synthetic review state resets on reload without API calls, external assets or browser errors', async ({ page, baseURL }) => {
    const apiRequests: string[] = [], externalRequests: string[] = [], errors: string[] = [], consoleErrors: string[] = []
    page.on('request', request => {
      const url = new URL(request.url())
      if (!['data:', 'blob:'].includes(url.protocol) && url.host !== new URL(baseURL!).host) externalRequests.push(url.origin)
      if (/(^|\/)api(?:\/|$)/.test(url.pathname)) apiRequests.push(url.pathname)
    })
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
    await openStudio(page)
    await expect(page.locator('.workspace-footer')).toContainText('실제 AI·DB 연결 없음 · 새로고침 시 초기화')
    await chooseVerdict(page, 'PASS')
    await page.getByTestId('confirm-verdict').click()
    await expect(page.locator('.progress-label')).toContainText('1 / 5')
    await page.reload()
    await expect(page.locator('.progress-label')).toContainText('0 / 5')
    await expect(page.getByTestId('group-generated')).toContainText('최종 판정 전')
    await expect(page.getByRole('radio', { name: '보류', exact: true })).toBeChecked()
    expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0])
    expect(await page.context().cookies()).toEqual([])
    expect(apiRequests).toEqual([])
    expect(externalRequests).toEqual([])
    expect(errors).toEqual([])
    expect(consoleErrors).toEqual([])
  })

  test('opening the real workspace after Studio uses its shared neutral theme and preserves the entry point', async ({ page }) => {
    await openStudio(page, '?theme=dark')
    await expect(page.locator('html')).toHaveClass(/dark/)
    const requests: string[] = []
    page.on('request', request => requests.push(new URL(request.url()).pathname))
    await page.goto('/workspace?demo=1')
    await expect(page.getByRole('heading', { name: 'Atlas 프로젝트', exact: true })).toBeVisible()
    await expect(page.locator('.ws-connected')).toHaveText('데모 환경')
    await expect(page.locator('.comparison')).toHaveCount(0)
    await expect(page.locator('html')).not.toHaveClass(/dark/)
    await expect(page.locator('html')).toHaveClass(/studio-system/)
    expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--accent').trim())).toBe('#4f46e5')
    expect(requests.filter(path => path.includes('/studio-preview/'))).toEqual([])
    await page.getByRole('button', { name: 'CPU 버전과 검사 보기', exact: true }).click()
    await expect(page.locator('.ws-report-table tbody tr')).toHaveCount(25)
  })
})
