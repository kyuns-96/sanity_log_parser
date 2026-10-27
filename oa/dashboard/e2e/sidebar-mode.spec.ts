import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { SidebarModePreview } from './support/sidebarModePreview'

async function setup(page: Page, options: { admin?: boolean; readable?: boolean } = {}) {
  const state = new SidebarModePreview()
  state.admin = options.admin ?? true
  state.readable = options.readable ?? true
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

test('dual-role sidebar switches menus and slides the selected background in both directions', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/?page=welcome')
  const control = page.getByRole('group', { name: '작업 모드' })
  await expect(control.getByRole('button', { name: '사용자 모드', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page).toHaveURL(/page=admin/)
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  const sidebar = page.locator('aside.sidebar')
  await expect(sidebar.getByRole('button', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await expect(sidebar.getByRole('button', { name: 'AI 분류·판정', exact: true })).toHaveCount(0)
  await expect(control.getByRole('button', { name: '관리자 모드', exact: true })).toHaveAttribute('aria-pressed', 'true')
  const transform = await page.locator('.sidebar-mode-thumb').evaluate(element => getComputedStyle(element).transform)
  expect(Number(transform.match(/matrix\([^,]+,[^,]+,[^,]+,[^,]+,\s*([^,]+)/)?.[1])).toBeGreaterThan(60)
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page).toHaveURL(/page=welcome/)
  await expect(page.getByRole('heading', { name: '권한 요청', exact: true })).toBeVisible()
  await expect(page.locator('.sidebar-mode-thumb')).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)')
  expect(state.writes).toEqual([])
})

test('user project block version ECO and selected item survive a visit to administration', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/?page=workbench')
  await page.getByRole('button', { name: 'CPU 버전과 검사 보기' }).click()
  await expect(page.getByRole('button', { name: /gca-001 검토 시작$/ })).toBeVisible()
  const before = new URL(page.url())
  const userSwitch = (await page.locator('.sidebar-mode-switch').boundingBox())!
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  const adminSwitch = (await page.locator('.sidebar-mode-switch').boundingBox())!
  for (const key of ['x', 'y', 'width'] as const) expect(Math.abs(adminSwitch[key] - userSwitch[key])).toBeLessThan(1)
  await page.reload()
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page.getByRole('button', { name: /gca-001 검토 시작$/ })).toBeVisible()
  const after = new URL(page.url())
  for (const key of ['page', 'project', 'block', 'version', 'tab', 'eco', 'item']) expect(after.searchParams.get(key)).toBe(before.searchParams.get(key))
  expect(state.writes).toEqual([])
})

test('the thumb has a real horizontal transition before the page changes', async ({ page }) => {
  await setup(page)
  await page.goto('/?page=welcome')
  await page.getByRole('button', { name: '관리자 모드', exact: true }).waitFor()
  const motion = await page.locator('.sidebar-mode-thumb').evaluate(async element => {
    element.parentElement!.querySelector<HTMLButtonElement>('[aria-label="관리자 모드"]')!.click()
    await new Promise(requestAnimationFrame)
    await new Promise(requestAnimationFrame)
    const animation = element.getAnimations()[0]
    if (!animation) return null
    animation.currentTime = 110
    return { duration: animation.effect!.getTiming().duration, x: new DOMMatrix(getComputedStyle(element).transform).m41, width: element.getBoundingClientRect().width }
  })
  expect(motion).not.toBeNull()
  expect(motion!.duration).toBe(220)
  expect(motion!.x).toBeGreaterThan(0)
  expect(motion!.x).toBeLessThan(motion!.width)
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
})

test('regular users have no switch in the portal or workbench and direct admin URLs stay denied', async ({ page }) => {
  const state = await setup(page, { admin: false })
  for (const url of ['/?page=welcome', '/?page=workbench', '/?page=admin&is_admin=true']) {
    await page.goto(url)
    await expect(page.locator('.sidebar-mode-switch')).toHaveCount(0)
  }
  await expect(page.getByRole('heading', { name: '권한 요청', exact: true })).toBeVisible()
  expect(state.calls.filter(call => call.includes('/api/admin/'))).toEqual([])
})

test('an administrator without business grants returns to the permission page without gaining access', async ({ page }) => {
  const state = await setup(page, { readable: false })
  await page.goto('/?page=admin')
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page).toHaveURL(/page=welcome/)
  await expect(page.getByRole('heading', { name: '권한 요청', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'AI 분류·판정', exact: true })).toHaveCount(0)
  expect(state.calls.some(call => call.startsWith('GET /api/workbench/'))).toBe(false)
  expect(state.writes).toEqual([])
})

test('the mobile drawer offers the switch with usable targets and closes after navigation', async ({ page }) => {
  await setup(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/?page=workbench')
  await page.getByRole('button', { name: '메뉴 열기', exact: true }).click()
  const admin = page.getByRole('button', { name: '관리자 모드', exact: true })
  await expect(admin).toBeVisible()
  expect((await admin.boundingBox())!.height).toBeGreaterThanOrEqual(38)
  await admin.focus(); await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '메뉴 열기', exact: true })).toHaveAttribute('aria-expanded', 'false')
  await page.getByRole('button', { name: '메뉴 열기', exact: true }).click()
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page).toHaveURL(/page=workbench/)
  await expect(page.getByRole('button', { name: '메뉴 열기', exact: true })).toHaveAttribute('aria-expanded', 'false')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('the collapsed rail retains an accessible mode switch', async ({ page }) => {
  await setup(page)
  await page.goto('/?page=workbench')
  await page.getByRole('button', { name: '메뉴 접기', exact: true }).click()
  const trigger = page.getByRole('button', { name: '관리자 모드로 전환', exact: true })
  await expect(trigger).toBeVisible()
  const target = (await trigger.boundingBox())!
  expect(target.width).toBeGreaterThanOrEqual(44)
  expect(target.height).toBeGreaterThanOrEqual(44)
  await trigger.focus(); await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
})

test('reduced motion suppresses sliding while preserving mode navigation', async ({ page }) => {
  await setup(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/?page=welcome')
  await expect(page.locator('.sidebar-mode-thumb')).toHaveCSS('transition-duration', '0s')
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '서비스 메뉴' })).toHaveCSS('animation-name', 'none')
})

test('a revoked administrator loses the switch on the next authenticated load', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/?page=admin')
  await expect(page.getByRole('button', { name: '관리자 모드', exact: true })).toBeVisible()
  state.admin = false
  const count = state.calls.filter(call => call.includes('/api/admin/')).length
  await page.reload()
  await expect(page.getByRole('heading', { name: '권한 요청', exact: true })).toBeVisible()
  await expect(page.locator('.sidebar-mode-switch')).toHaveCount(0)
  expect(state.calls.filter(call => call.includes('/api/admin/'))).toHaveLength(count)
})

for (const value of ['https://other.example.test/', '//other.example.test/', '/?page=admin', '/?demo=1', '/?page=workbench\\other']) {
  test(`return navigation rejects ${value}`, async ({ page }) => {
    await setup(page)
    await page.goto(`/?page=admin&user_view=${encodeURIComponent(value)}`)
    await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
    await expect(page).toHaveURL(/page=workbench/)
    await expect(page.getByRole('heading', { name: 'Atlas 프로젝트', exact: true })).toBeVisible()
  })
}
