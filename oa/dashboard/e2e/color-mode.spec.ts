import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'

const workspace = '/?demo=1'
const storageKey = 'sanity-color-mode'

async function chooseTheme(page: Page, name: string) {
  await page.getByRole('button', { name: '화면 테마', exact: true }).click()
  await page.getByRole('menuitemradio', { name, exact: true }).click()
}

test('system appearance follows OS changes without storing a preference; an explicit choice takes priority', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto(workspace)
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.getByRole('heading', { name: 'Atlas 프로젝트', exact: true })).toBeVisible()
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0])
  await page.emulateMedia({ colorScheme: 'light' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await chooseTheme(page, '다크 모드')
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.emulateMedia({ colorScheme: 'light' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await chooseTheme(page, '시스템 설정')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  expect(await page.evaluate(key => localStorage.getItem(key), storageKey)).toBeNull()
})

test('saved selection is applied before the application bundle loads', async ({ page }) => {
  await page.goto(workspace)
  await chooseTheme(page, '다크 모드')
  await page.route('**/src/main.tsx', route => route.abort())
  await page.reload()
  await expect(page.locator('#root')).toBeEmpty()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.locator('html')).toHaveClass(/dark/)
  expect(await page.evaluate(() => document.documentElement.style.colorScheme)).toBe('dark')
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', '#13161d')
})

test('theme persists across pages and synchronizes between open tabs', async ({ page, context }) => {
  await page.goto(workspace)
  await chooseTheme(page, '다크 모드')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  const second = await context.newPage()
  await second.goto('/?demo=1&flow=legacy')
  await expect(second.locator('html')).toHaveAttribute('data-theme', 'dark')
  await chooseTheme(second, '라이트 모드')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([storageKey])
  await chooseTheme(second, '시스템 설정')
  await expect.poll(() => page.evaluate(() => localStorage.length)).toBe(0)
  await second.close()
})

test('blocked storage does not prevent rendering or switching appearance', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.addInitScript(() => {
    for (const method of ['getItem', 'setItem', 'removeItem']) {
      Object.defineProperty(Storage.prototype, method, { value: () => { throw new DOMException('Blocked', 'SecurityError') } })
    }
  })
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto(workspace)
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await chooseTheme(page, '라이트 모드')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await chooseTheme(page, '시스템 설정')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  expect(errors).toEqual([])
})

test('unknown saved values fall back to the system preference', async ({ page }) => {
  await page.addInitScript(key => localStorage.setItem(key, 'unknown'), storageKey)
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto(workspace)
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByRole('button', { name: '화면 테마', exact: true }).click()
  await expect(page.getByRole('menuitemradio', { name: '시스템 설정', exact: true })).toBeChecked()
})

test('the review editor keeps unsaved text and selected logs while changing appearance', async ({ page }) => {
  await page.goto(workspace)
  await page.getByRole('button', { name: 'CPU 버전과 검사 보기' }).click()
  await page.getByRole('button', { name: /gca-001 검토 시작$/ }).click()
  await page.getByRole('button', { name: '그룹 초안 만들기', exact: true }).click()
  await page.getByRole('button', { name: '근거 수정', exact: true }).click()
  const draft = '원본 로그의 조건과 다른 그룹의 차이를 확인하는 중입니다.'
  await page.getByLabel('분류 기준', { exact: true }).fill(draft)
  await page.getByLabel('작성·수정 사유', { exact: true }).fill('다크 모드에서 근거를 작성합니다.')
  const selected = page.getByLabel('demo-atlas-cpu-net2-post-gca-001-log-1 로그 선택', { exact: true })
  await selected.check()
  await chooseTheme(page, '다크 모드')
  await expect(page.getByLabel('분류 기준', { exact: true })).toHaveValue(draft)
  await expect(selected).toBeChecked()
  await expect(page.getByLabel('분류 기준', { exact: true })).toHaveCSS('background-color', 'rgb(27, 32, 41)')
  // Check actual foreground/background pairs used by the editor and its actions.
  const ratios = await page.evaluate(() => {
    const luma = (color: string) => {
      const channels = color.match(/[\d.]+/g)!.slice(0, 3).map(Number).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
      return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722
    }
    return ['.ws-rationale-editor textarea', '.ws-studio-rationale-editor-actions button:last-child'].map(selector => {
      const style = getComputedStyle(document.querySelector(selector)!)
      const values = [luma(style.color), luma(style.backgroundColor)].sort((a, b) => b - a)
      return (values[0] + .05) / (values[1] + .05)
    })
  })
  expect(ratios.every(ratio => ratio >= 4.5)).toBe(true)
  await chooseTheme(page, '라이트 모드')
  await expect(page.getByLabel('분류 기준', { exact: true })).toHaveValue(draft)
  await expect(selected).toBeChecked()
})

test('phone theme menu is keyboard accessible and fits without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(workspace)
  const trigger = page.getByRole('button', { name: '화면 테마', exact: true })
  await trigger.focus()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('menuitemradio', { name: '시스템 설정', exact: true })).toBeFocused()
  await page.keyboard.press('End')
  await expect(page.getByRole('menuitemradio', { name: '다크 모드', exact: true })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(trigger).toBeFocused()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('button', { name: '메뉴 열기', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '워크스페이스 메뉴' })).toHaveCSS('background-color', 'rgb(23, 27, 35)')
  await page.keyboard.press('Escape')
})

test('login, SSO welcome and administration share the selected appearance', async ({ page }) => {
  let signedIn = false
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/auth-config') return route.fulfill({ json: { mode: 'sso', login_url: '/auth/login', logout_url: '/auth/logout', logout_method: 'POST' } })
    if (path === '/api/session') return signedIn
      ? route.fulfill({ json: { user_id: 'synthetic-user', reviewer: '합성 사용자', auth_method: 'sso', authorization_ready: true, is_admin: true, can_read_business: false, writable: false, can_receive: false, access_mode: 'postgresql', notice: '' } })
      : route.fulfill({ status: 401, json: { code: 'UNAUTHORIZED' } })
    if (path === '/api/access/catalog') return route.fulfill({ json: { projects: [], blocks: [] } })
    if (path === '/api/access/me') return route.fulfill({ json: { revision: 0, grants: [], requests: [] } })
    if (path === '/api/admin/access') return route.fulfill({ json: { revision: 0, grants: [], requests: [], users: [] } })
    return route.fulfill({ status: 404, json: { code: 'RESOURCE_NOT_FOUND' } })
  })
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeVisible()
  await chooseTheme(page, '다크 모드')
  await expect(page.locator('.login-main')).toHaveCSS('color', 'rgb(232, 236, 243)')
  signedIn = true
  await page.goto('/?page=welcome')
  await expect(page.getByRole('heading', { name: '권한 요청', exact: true })).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await expect(page.locator('.am-panel').first()).toHaveCSS('background-color', 'rgb(27, 32, 41)')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await chooseTheme(page, '라이트 모드')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
})

for (const [name, url, surface] of [
  ['workspace', workspace, '.ws-panel'],
  ['legacy', '/?demo=1&flow=legacy', '.detail-panel'],
  ['access demo', '/?demo=1&flow=access', '.af-panel'],
  ['versions', '/?demo=1&flow=versions', '.vf-panel'],
  ['reports', '/?demo=1&dataset=reports', '.rp-ledger'],
] as const) {
  test(`${name} uses the shared dark palette including mobile navigation`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' })
    await page.goto(url)
    await expect(page.locator(surface).first()).toBeVisible()
    await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(19, 22, 29)')
    await expect(page.getByRole('button', { name: '화면 테마', exact: true })).toBeVisible()
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await chooseTheme(page, '라이트 모드')
    await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(245, 246, 248)')
  })
}
