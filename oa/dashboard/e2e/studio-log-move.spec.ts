import { expect, test } from '@playwright/test'

for (const method of ['pointer', 'keyboard'] as const) {
  test(`Studio log table preserves ${method} group moves and invalidates the affected rationales`, async ({ page }) => {
    await page.goto('/workspace?demo=1')
    await page.getByRole('button', { name: 'CPU 버전과 검사 보기', exact: true }).click()
    await page.getByRole('button', { name: /gca-001 검토 시작$/ }).click()
    await page.getByRole('button', { name: '그룹 초안 만들기', exact: true }).click()
    const targets = page.locator('.ws-workbench-group-list button[data-log-drop]')
    await expect(targets).toHaveCount(3)
    const firstGroup = await targets.nth(0).getAttribute('data-log-drop')
    const secondGroup = await targets.nth(1).getAttribute('data-log-drop')
    const log = 'demo-atlas-cpu-net2-post-gca-001-log-1'
    const handle = page.getByRole('button', { name: `${log} 로그 이동`, exact: true })
    await expect(page.locator('.ws-record-table tbody tr')).toHaveCount(6)
    if (method === 'pointer') {
      await handle.scrollIntoViewIfNeeded()
      const from = (await handle.boundingBox())!
      const to = (await targets.nth(1).boundingBox())!
      await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2)
      await page.mouse.down()
      await page.mouse.move(from.x + from.width / 2 - 10, from.y + from.height / 2, { steps: 2 })
      await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 8 })
      await expect(page.locator(`.ws-workbench-group-list > div[data-log-drop="${secondGroup}"]`)).toHaveClass(/ws-log-drop-active/)
      await page.mouse.up()
    } else {
      await handle.focus()
      await page.keyboard.press('Enter')
      await expect(page.locator(`button[data-log-drop="${secondGroup}"]`)).toBeFocused()
      await page.keyboard.press('Enter')
    }
    await expect(page.locator(`button[data-log-drop="${secondGroup}"]`)).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator('.ws-record-table tbody tr')).toHaveCount(7)
    await expect(page.getByRole('row', { name: `${log} 원문 보기`, exact: true })).toBeVisible()
    await expect(page.getByRole('region', { name: '그룹별 분류 근거' }).getByText('재검토', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true })).toBeDisabled()
    await expect(page.locator('.ws-review-progress')).toContainText('로그 18건')
    await expect(page.locator('.ws-review-progress')).toContainText('3개 그룹')
    await page.locator(`button[data-log-drop="${firstGroup}"]`).click()
    await expect(page.locator('.ws-record-table tbody tr')).toHaveCount(5)
    await expect(page.getByRole('row', { name: `${log} 원문 보기`, exact: true })).toHaveCount(0)
    await targets.nth(2).click()
    await expect(page.locator('.ws-record-table tbody tr')).toHaveCount(6)
    await expect(page.getByRole('region', { name: '그룹별 분류 근거' })).toContainText('검토 전')
  })
}
