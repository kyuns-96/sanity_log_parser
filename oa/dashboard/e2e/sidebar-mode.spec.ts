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
  const sidebar = page.locator('aside.sidebar:visible')
  await expect(sidebar.getByRole('button', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await expect(sidebar.getByRole('button', { name: 'AI 분류·판정', exact: true })).toHaveCount(0)
  await expect(control.getByRole('button', { name: '관리자 모드', exact: true })).toHaveAttribute('aria-pressed', 'true')
  const transform = await page.locator('.sidebar-mode-thumb:visible').evaluate(element => getComputedStyle(element).transform)
  expect(Number(transform.match(/matrix\([^,]+,[^,]+,[^,]+,[^,]+,\s*([^,]+)/)?.[1])).toBeGreaterThan(60)
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page).toHaveURL(/page=welcome/)
  await expect(page.getByRole('heading', { name: '권한 요청', exact: true })).toBeVisible()
  await expect(page.locator('.sidebar-mode-thumb:visible')).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)')
  expect(state.writes).toEqual([])
})

test('user project block version ECO and selected item survive a visit to administration', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/?page=workbench')
  await page.getByRole('button', { name: 'CPU 버전과 검사 보기' }).click()
  await expect(page.getByRole('button', { name: /gca-001 검토 시작$/ })).toBeVisible()
  const before = new URL(page.url())
  const userSwitch = (await page.locator('.sidebar-mode-switch:visible').boundingBox())!
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  const adminSwitch = (await page.locator('.sidebar-mode-switch:visible').boundingBox())!
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
  const sidebarWidth = (await page.getByRole('complementary').boundingBox())!.width
  expect(target.width).toBeGreaterThanOrEqual(44)
  expect(target.height).toBeGreaterThanOrEqual(44)
  await trigger.focus(); await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '메뉴 펼치기', exact: true })).toBeVisible()
  const back = page.getByRole('button', { name: '사용자 모드로 전환', exact: true })
  await expect(back).toBeVisible()
  expect((await page.getByRole('complementary').boundingBox())!.width).toBe(sidebarWidth)
  const destination = (await back.boundingBox())!
  for (const key of ['x', 'y', 'width'] as const) expect(Math.abs(destination[key] - target[key])).toBeLessThan(1)
  await back.click()
  await expect(page.getByRole('button', { name: '관리자 모드로 전환', exact: true })).toBeVisible()
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

function documentRequests(page: Page) {
  const requests: string[] = []
  page.on('request', request => { if (request.isNavigationRequest() && request.frame() === page.mainFrame()) requests.push(request.url()) })
  return requests
}

test('both modes keep the same document and retain unsaved administrator and permission-request drafts', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/?page=welcome')
  await page.getByLabel('요청 사유', { exact: true }).fill('전환 뒤에도 남아 있어야 하는 신청 초안')
  await page.getByRole('combobox', { name: '프로젝트', exact: true }).selectOption('demo-atlas')
  await page.getByRole('combobox', { name: '블록', exact: true }).selectOption('demo-atlas-cpu')
  const origin = await page.evaluate(() => { document.body.dataset.navigationProbe = 'same-document'; return performance.timeOrigin })
  const documents = documentRequests(page)
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await page.getByLabel('프로젝트 ID', { exact: true }).fill('draft-project')
  await page.getByLabel('프로젝트 이름', { exact: true }).fill('저장하지 않은 프로젝트')
  await page.getByLabel('설명', { exact: true }).fill('관리자 초안 보존')
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page.getByLabel('요청 사유', { exact: true })).toHaveValue('전환 뒤에도 남아 있어야 하는 신청 초안')
  await expect(page.getByRole('combobox', { name: '프로젝트', exact: true })).toHaveValue('demo-atlas')
  await expect(page.getByRole('combobox', { name: '블록', exact: true })).toHaveValue('demo-atlas-cpu')
  await expect(page.locator('[data-dashboard-page="welcome"] main')).toBeFocused()
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await expect(page.getByLabel('프로젝트 ID', { exact: true })).toHaveValue('draft-project')
  await expect(page.getByLabel('프로젝트 이름', { exact: true })).toHaveValue('저장하지 않은 프로젝트')
  await expect(page.getByLabel('설명', { exact: true })).toHaveValue('관리자 초안 보존')
  expect(await page.evaluate(() => ({ origin: performance.timeOrigin, probe: document.body.dataset.navigationProbe }))).toEqual({ origin, probe: 'same-document' })
  expect(documents).toEqual([])
  expect(state.writes).toEqual([])
})

test('workbench search filters details and document survive both directions without test API requests', async ({ page }) => {
  const state = await setup(page)
  const testApi: string[] = []
  await page.route('**/workspace/api/**', route => { testApi.push(route.request().url()); return route.fulfill({ status: 500, json: { code: 'WRONG_ENVIRONMENT' } }) })
  await page.goto('/?page=workbench')
  await page.getByRole('button', { name: 'CPU 버전과 검사 보기' }).click()
  await page.getByRole('textbox', { name: '검사 검색', exact: true }).fill('gca-001')
  await page.getByRole('combobox', { name: '결과 필터', exact: true }).selectOption('FAIL')
  await page.getByRole('button', { name: '상세 열 보기', exact: true }).click()
  await expect(page.getByRole('button', { name: /gca-001 검토 시작$/ })).toBeVisible()
  const before = page.url()
  const documents = documentRequests(page)
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await page.getByLabel('프로젝트 이름', { exact: true }).fill('워크스페이스에서 잠시 이동한 초안')
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '검사 검색', exact: true })).toHaveValue('gca-001')
  await expect(page.getByRole('combobox', { name: '결과 필터', exact: true })).toHaveValue('FAIL')
  await expect(page.getByRole('button', { name: '기본 열 보기', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /gca-001 검토 시작$/ })).toBeVisible()
  await expect(page).toHaveURL(before)
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await expect(page.getByLabel('프로젝트 이름', { exact: true })).toHaveValue('워크스페이스에서 잠시 이동한 초안')
  expect(documents).toEqual([])
  expect(testApi).toEqual([])
  expect(state.writes).toEqual([])
})

test('browser Back and Forward restore each mode and drafts without document navigation', async ({ page }) => {
  await setup(page)
  await page.goto('/?page=welcome')
  await page.getByLabel('요청 사유', { exact: true }).fill('뒤로 가기에서 복원할 초안')
  const documents = documentRequests(page)
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await page.getByLabel('프로젝트 이름', { exact: true }).fill('앞으로 가기에서 복원할 초안')
  await page.goBack()
  await expect(page.getByLabel('요청 사유', { exact: true })).toHaveValue('뒤로 가기에서 복원할 초안')
  await expect(page.getByRole('button', { name: '사용자 모드', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.goForward()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await expect(page.getByLabel('프로젝트 이름', { exact: true })).toHaveValue('앞으로 가기에서 복원할 초안')
  await expect(page.getByRole('button', { name: '관리자 모드', exact: true })).toHaveAttribute('aria-pressed', 'true')
  expect(documents).toEqual([])
})

test('administrator revocation is checked before reopening retained administration', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/?page=admin')
  await page.getByLabel('프로젝트 이름', { exact: true }).fill('회수 후에는 닫혀야 하는 초안')
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Atlas 프로젝트', exact: true })).toBeVisible()
  state.admin = false
  const count = state.calls.filter(call => call.includes('/api/admin/')).length
  await page.goBack()
  await expect(page.getByRole('heading', { name: '권한 요청', exact: true })).toBeVisible()
  await expect(page.locator('.sidebar-mode-switch')).toHaveCount(0)
  await expect(page.locator('[data-dashboard-page="admin"]')).toHaveCount(0)
  expect(state.calls.filter(call => call.includes('/api/admin/'))).toHaveLength(count)
})

test('business revocation removes retained workbench data before returning to user mode', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/?page=workbench')
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  state.readable = false
  const count = state.calls.filter(call => call.includes('/api/workbench/')).length
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '권한 요청', exact: true })).toBeVisible()
  await expect(page.locator('[data-dashboard-page="workbench"]')).toHaveCount(0)
  expect(state.calls.filter(call => call.includes('/api/workbench/'))).toHaveLength(count)
})

test('a failed session check stays in place with a usable retry and preserves the draft', async ({ page }) => {
  await setup(page)
  await page.goto('/?page=admin')
  await page.getByLabel('프로젝트 이름', { exact: true }).fill('통신 실패에도 유지할 초안')
  let failing = true
  await page.route('**/api/session', route => failing ? route.fulfill({ status: 503, json: { code: 'AUTH_PROVIDER_UNAVAILABLE' } }) : route.fallback())
  const documents = documentRequests(page)
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('현재 로그인과 권한을 확인하지 못했습니다')
  await expect(page).toHaveURL(/page=admin/)
  await expect(page.getByLabel('프로젝트 이름', { exact: true })).toHaveValue('통신 실패에도 유지할 초안')
  await expect(page.getByRole('button', { name: '관리자 모드', exact: true })).toHaveAttribute('aria-pressed', 'true')
  failing = false
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page).toHaveURL(/page=workbench/)
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await expect(page.getByLabel('프로젝트 이름', { exact: true })).toHaveValue('통신 실패에도 유지할 초안')
  expect(documents).toEqual([])
})

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

test('logout clears retained mode drafts before displaying the login screen', async ({ page }) => {
  await setup(page)
  await page.goto('/?page=workbench')
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await page.getByLabel('프로젝트 이름', { exact: true }).fill('로그아웃 시 제거할 초안')
  await page.route('**/auth/logout', async route => {
    expect(route.request().method()).toBe('POST')
    await page.route('**/api/session', route => route.fulfill({ status: 401, json: { code: 'UNAUTHORIZED' } }))
    return route.fulfill({ json: { status: 'LOGGED_OUT' } })
  })
  await page.getByRole('button', { name: '로그아웃', exact: true }).click()
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeVisible()
  await expect(page.locator('[data-dashboard-page="workbench"], [data-dashboard-page="admin"]')).toHaveCount(0)
  await expect(page.locator('input[aria-label="프로젝트 이름"]')).toHaveCount(0)
})

test('each mode restores its own scroll position when returning', async ({ page }) => {
  await setup(page)
  await page.setViewportSize({ width: 1440, height: 720 })
  await page.goto('/?page=workbench')
  await page.getByRole('button', { name: 'CPU 버전과 검사 보기' }).click()
  await expect(page.getByRole('button', { name: /gca-001 검토 시작$/ })).toBeVisible()
  await page.evaluate(() => window.scrollTo(0, 300))
  const userScroll = await page.evaluate(() => scrollY)
  expect(userScroll).toBeGreaterThan(200)
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  expect(await page.evaluate(() => scrollY)).toBe(0)
  await page.evaluate(() => window.scrollTo(0, 250))
  const adminScroll = await page.evaluate(() => scrollY)
  expect(adminScroll).toBeGreaterThan(200)
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '검사 검색', exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(userScroll)
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(adminScroll)
})

test('Back and Forward restore the version encoded in each workbench history entry', async ({ page }) => {
  await setup(page)
  await page.goto('/?page=workbench')
  await page.getByRole('button', { name: 'CPU 버전과 검사 보기' }).click()
  const versions = page.getByRole('combobox', { name: '조회 버전', exact: true })
  const first = await versions.inputValue()
  const second = await versions.locator('option').evaluateAll((options, first) => options.map(option => (option as HTMLOptionElement).value).find(value => value !== first), first)
  expect(second).toBeTruthy()
  const documents = documentRequests(page)
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await versions.selectOption(second!)
  await expect.poll(() => new URL(page.url()).searchParams.get('version')).toBe(second)
  await page.goBack()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await page.goBack()
  await expect(versions).toHaveValue(first)
  await expect.poll(() => new URL(page.url()).searchParams.get('version')).toBe(first)
  await page.goForward()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await page.goForward()
  await expect(versions).toHaveValue(second!)
  await expect.poll(() => new URL(page.url()).searchParams.get('version')).toBe(second)
  expect(documents).toEqual([])
})

for (const initial of ['workbench', 'admin'] as const) test(`first mode visits from ${initial} prepare the authenticated shell without a login flash`, async ({ page }) => {
  await setup(page)
  await page.goto(`/?page=${initial}`)
  const firstTitle = initial === 'workbench' ? 'Atlas 프로젝트' : '프로젝트·권한 관리'
  const secondTitle = initial === 'workbench' ? '프로젝트·권한 관리' : 'Atlas 프로젝트'
  await expect(page.getByRole('heading', { name: firstTitle, exact: true })).toBeVisible()
  await page.evaluate(() => {
    document.body.dataset.loginFlashes = '0'
    const observer = new MutationObserver(() => {
      if (Array.from(document.querySelectorAll('.login-page, .ws-session-loading')).some(element => element.getClientRects().length > 0)) {
        document.body.dataset.loginFlashes = String(Number(document.body.dataset.loginFlashes) + 1)
      }
    })
    observer.observe(document.body, { childList: true, subtree: true })
  })
  await page.getByRole('button', { name: initial === 'workbench' ? '관리자 모드' : '사용자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: secondTitle, exact: true })).toBeVisible()
  await page.getByRole('button', { name: initial === 'workbench' ? '사용자 모드' : '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: firstTitle, exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.body.dataset.loginFlashes)).toBe('0')
})

test('a cached desktop screen reconnects its mobile drawer correctly after resizing while hidden', async ({ page }) => {
  await setup(page)
  await page.goto('/?page=workbench')
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Atlas 프로젝트', exact: true })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: '메뉴 열기', exact: true }).click()
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  const trigger = page.getByRole('button', { name: '메뉴 열기', exact: true })
  await trigger.click()
  const drawer = page.getByRole('dialog', { name: '워크스페이스 메뉴' })
  await expect(drawer).toBeVisible()
  await expect(page.locator('body')).toHaveCSS('overflow', 'hidden')
  await page.keyboard.press('Escape')
  await expect(trigger).toBeFocused()
  await expect(trigger).toHaveAttribute('aria-expanded', 'false')
  expect(await page.evaluate(() => document.body.style.overflow === 'hidden')).toBe(false)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Atlas 프로젝트', exact: true })).toBeVisible()
  await expect(page.getByRole('complementary', { name: '워크스페이스 메뉴' })).toBeVisible()
})
