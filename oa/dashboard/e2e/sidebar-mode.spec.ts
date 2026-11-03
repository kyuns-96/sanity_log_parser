import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { SidebarModePreview } from './support/sidebarModePreview'

async function setup(page: Page) {
  const state = new SidebarModePreview()
  await page.route('**/api/**', async route => {
    const request = route.request()
    try {
      const body = request.method() === 'POST' ? request.postDataJSON() as Record<string, unknown> : undefined
      return route.fulfill({ json: await state.request(new URL(request.url()).pathname + new URL(request.url()).search, request.method(), body) })
    } catch (error) {
      const code = error && typeof error === 'object' && 'code' in error ? String(error.code) : 'REQUEST_FAILED'
      return route.fulfill({ status: code === 'FORBIDDEN' ? 403 : 404, json: { code } })
    }
  })
  return state
}

test('expired authentication discards both cached modes and exposes only the SSO login', async ({ page }) => {
  await setup(page)
  await page.goto('/?page=workbench')
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await page.getByLabel('프로젝트 이름', { exact: true }).fill('세션이 끝나면 제거할 초안')
  await page.route('**/api/session', route => route.fulfill({ status: 401, json: { code: 'UNAUTHORIZED' } }))
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeVisible()
  await expect(page.locator('[data-dashboard-page="workbench"], [data-dashboard-page="admin"]')).toHaveCount(0)
  await expect(page.locator('.sidebar-mode-switch')).toHaveCount(0)
})
