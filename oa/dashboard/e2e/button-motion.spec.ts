import { expect, test, type Locator, type Page } from '@playwright/test'

async function openWorkbench(page: Page) {
  await page.goto('/workspace?demo=1')
  await page.getByRole('button', { name: 'CPU 버전과 검사 보기', exact: true }).click()
  await page.getByRole('button', { name: /gca-001 검토 시작$/ }).click()
  return page.getByRole('button', { name: '그룹 초안 만들기', exact: true })
}

async function verticalShift(button: Locator) {
  return button.evaluate(element => {
    const transform = getComputedStyle(element).transform
    return transform === 'none' ? 0 : new DOMMatrixReadOnly(transform).m42
  })
}

test('workbench actions respond to hover and press while table controls stay anchored', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  const primary = await openWorkbench(page)
  await primary.hover()
  await expect.poll(() => verticalShift(primary)).toBe(-1)
  const transition = await primary.evaluate(element => {
    const style = getComputedStyle(element)
    return { properties: style.transitionProperty, duration: style.transitionDuration, shadow: style.boxShadow }
  })
  expect(transition.properties).toContain('transform')
  expect(transition.properties).not.toContain('all')
  expect(transition.duration).toBe('0.16s')
  expect(transition.shadow).not.toBe('none')
  await page.mouse.down()
  await expect.poll(() => verticalShift(primary)).toBe(0)
  await page.mouse.move(1, 1)
  await page.mouse.up()
  await primary.click()

  const rows = page.locator('.ws-record-table tbody tr[data-record-id]')
  await expect(rows).toHaveCount(6)
  const checkbox = rows.first().getByRole('checkbox')
  await checkbox.hover()
  expect(await verticalShift(checkbox)).toBe(0)
  await page.mouse.down()
  expect(await verticalShift(checkbox)).toBe(0)
  await page.mouse.up()
  await expect(checkbox).toBeChecked()

  const outline = page.getByRole('button', { name: '새 그룹', exact: true })
  await outline.hover()
  await expect.poll(() => verticalShift(outline)).toBe(-1)
  await outline.focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Shift+Tab')
  await expect(outline).toBeFocused()
  expect(await outline.evaluate(element => element.matches(':focus-visible'))).toBe(true)
  expect(await outline.evaluate(element => getComputedStyle(element).outlineStyle)).toBe('solid')

  for (const control of [
    page.locator('.ws-pipeline-steps > button').first(),
    page.locator('.ws-workbench-group-list > div > button').first(),
    rows.first().getByRole('button', { name: /로그 이동$/ }),
  ]) {
    await control.hover()
    await expect.poll(() => verticalShift(control)).toBe(0)
    await page.mouse.down()
    expect(await verticalShift(control)).toBe(0)
    await page.mouse.move(1, 1)
    await page.mouse.up()
  }
  const disabled = page.getByRole('button', { name: '다음 그룹 원문 페이지', exact: true })
  await expect(disabled).toBeDisabled()
  const disabledShadow = await disabled.evaluate(element => getComputedStyle(element).boxShadow)
  const bounds = (await disabled.boundingBox())!
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)
  expect(await verticalShift(disabled)).toBe(0)
  expect(await disabled.evaluate(element => getComputedStyle(element).boxShadow)).toBe(disabledShadow)
  await outline.hover()
  await expect.poll(() => verticalShift(outline)).toBe(-1)
  await page.screenshot({ path: testInfo.outputPath('workbench-button-hover.png') })
})

test('reduced motion keeps action buttons still with visible keyboard focus', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const primary = await openWorkbench(page)
  await primary.hover()
  expect(await verticalShift(primary)).toBe(0)
  expect(await primary.evaluate(element => getComputedStyle(element).transitionDuration)).toBe('0s')
  await primary.focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Shift+Tab')
  await expect(primary).toBeFocused()
  expect(await primary.evaluate(element => element.matches(':focus-visible'))).toBe(true)
  expect(await primary.evaluate(element => getComputedStyle(element).outlineStyle)).toBe('solid')
  await page.mouse.down()
  expect(await verticalShift(primary)).toBe(0)
  await page.mouse.move(1, 1)
  await page.mouse.up()
})

test('touch devices do not retain a raised hover state', async ({ browser }, testInfo) => {
  const context = await browser.newContext({
    baseURL: testInfo.project.use.baseURL,
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  })
  const page = await context.newPage()
  const primary = await openWorkbench(page)
  expect(await page.evaluate(() => matchMedia('(hover: hover) and (pointer: fine)').matches)).toBe(false)
  await primary.hover()
  expect(await verticalShift(primary)).toBe(0)
  await primary.tap()
  await expect(page.locator('.ws-record-table tbody tr[data-record-id]')).toHaveCount(6)
  const refresh = page.getByRole('button', { name: '최신 구성 다시 조회', exact: true })
  await refresh.hover()
  expect(await verticalShift(refresh)).toBe(0)
  await page.screenshot({ path: testInfo.outputPath('touch-actions.png'), fullPage: true })
  await context.close()
})
