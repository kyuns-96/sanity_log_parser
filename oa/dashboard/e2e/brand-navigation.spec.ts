import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'
import { SidebarModePreview } from './support/sidebarModePreview'

// Only synthetic browser fixtures are used; no company authentication or data.
async function setup(page: Page, options: { admin?: boolean; readable?: boolean; signedOut?: boolean } = {}) {
  const state = new SidebarModePreview()
  state.admin = options.admin ?? true; state.readable = options.readable ?? true
  await page.route('**/api/**', async route => {
    const request = route.request(); const url = new URL(request.url())
    if (options.signedOut && url.pathname === '/api/session') {
      state.calls.push('GET /api/session')
      return route.fulfill({ status: 401, json: { code: 'UNAUTHORIZED' } })
    }
    try {
      const body = request.method() === 'POST' ? request.postDataJSON() as Record<string, unknown> : undefined
      return route.fulfill({ json: await state.request(url.pathname + url.search, request.method(), body) })
    } catch (error) {
      const code = error && typeof error === 'object' && 'code' in error ? String(error.code) : 'REQUEST_FAILED'
      return route.fulfill({ status: code === 'FORBIDDEN' ? 403 : 404, json: { code } })
    }
  })
  return state
}

const brand = (page: Page) => page.getByRole('link', { name: 'Sanity Lens AI 홈', exact: true }).filter({ visible: true })

async function assertBrand(link: Locator, showName = true) {
  await expect(link).toBeVisible()
  if (showName) {
    await expect(link.locator('strong.console-brand-name')).toContainText('Sanity Lens')
    await expect(link.locator('strong.console-brand-name span.console-brand-ai')).toHaveText('AI')
    const metrics = await link.locator('strong.console-brand-name').evaluate(node => {
      const style = getComputedStyle(node); const rect = node.getBoundingClientRect()
      return { width: rect.width, height: rect.height, scrollWidth: node.scrollWidth, clientWidth: node.clientWidth,
        clipped: style.textOverflow === 'ellipsis' }
    })
    expect(metrics.width).toBeGreaterThan(0); expect(metrics.height).toBeGreaterThan(0)
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1)
    expect(metrics.clipped).toBe(false)
  }
  await expect(link).toHaveAttribute('href', /\//)
}

for (const start of ['admin', 'review', 'workbench'] as const) {
  test(`production ${start} logo returns to initial authenticated route`, async ({ page }) => {
    const state = await setup(page)
    await page.goto(`/?page=${start}`)
    if (start === 'admin') await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
    if (start === 'workbench') {
      await page.getByRole('button', { name: 'CPU 버전과 검사 보기' }).click()
      await page.getByRole('button', { name: /gca-001 검토 시작$/ }).click()
      await expect(page.locator('.ws-studio-workbench-topbar')).toBeVisible()
      expect(new URL(page.url()).searchParams.get('review')).toBeTruthy()
    }
    const link = brand(page); await assertBrand(link)
    await expect(link).toHaveAttribute('href', '/')
    const callsBefore = state.calls.filter(call => call === 'GET /api/session').length
    await link.focus(); await expect(link).toBeFocused(); await page.keyboard.press('Enter')
    await expect.poll(() => state.calls.filter(call => call === 'GET /api/session').length).toBeGreaterThan(callsBefore)
    await expect.poll(() => {
      const url = new URL(page.url())
      return url.pathname === '/' && !url.searchParams.has('review') && !url.searchParams.has('project') &&
        !url.searchParams.has('demo') && [null, 'welcome', 'workbench', 'review'].includes(url.searchParams.get('page'))
    }).toBe(true)
    expect(state.writes).toEqual([])
  })
}

test('an administrator without business access returns to the permission screen', async ({ page }) => {
  const state = await setup(page, { readable: false })
  await page.goto('/?page=admin')
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await brand(page).click()
  await expect(page.getByRole('heading', { name: '권한 요청', exact: true })).toBeVisible()
  expect(state.calls.some(call => call.startsWith('GET /api/workbench/'))).toBe(false)
  expect(state.writes).toEqual([])
})

test('signed-out brand is a keyboard-accessible home link and retains authentication boundaries', async ({ page }) => {
  const state = await setup(page, { signedOut: true })
  await page.goto('/?page=admin')
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeVisible()
  const link = brand(page); await assertBrand(link)
  await expect(link).toHaveAttribute('href', '/')
  await expect(link).not.toHaveAttribute('target', '_blank')
  await link.focus(); await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeVisible()
  await expect(page).toHaveURL(/\/$/)
  expect(state.writes).toEqual([])
})

for (const url of ['/?demo=1&flow=access', '/?demo=1&flow=versions']) {
  test(`explicit demo brand keeps its destination ${url}`, async ({ page }) => {
    await page.goto(url)
    const link = brand(page); await assertBrand(link)
    await expect(link).toHaveAttribute('href', url)
    await link.click()
    await expect(page).toHaveURL(new RegExp(url.replace(/[?]/g, '\\?') + '$'))
  })
}

for (const url of ['/?demo=1', '/workspace?demo=1']) {
  test(`workspace demo home stays synthetic for ${url}`, async ({ page }) => {
    const apiCalls: string[] = []
    page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) apiCalls.push(request.url()) })
    await page.goto(url)
    await expect(page.getByRole('heading', { name: 'Atlas 프로젝트', exact: true })).toBeVisible()
    const link = brand(page); await assertBrand(link)
    await link.click()
    await expect(page.getByRole('heading', { name: 'Atlas 프로젝트', exact: true })).toBeVisible()
    expect(new URL(page.url()).searchParams.get('demo')).toBe('1')
    expect(apiCalls).toEqual([])
  })
}

for (const width of [1280, 1440]) {
  test(`desktop ${width}: logo fits expanded/collapsed sidebar, report header and login`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1100 }); await setup(page)
    await page.goto('/?page=workbench')
    await expect(page.getByRole('button', { name: 'CPU 버전과 검사 보기' })).toBeVisible()
    await assertBrand(brand(page))
    await page.getByRole('button', { name: '메뉴 접기', exact: true }).click()
    await assertBrand(brand(page), false)
    await page.getByRole('button', { name: '메뉴 펼치기', exact: true }).click()
    await assertBrand(brand(page))
    await page.getByRole('button', { name: 'CPU 버전과 검사 보기' }).click()
    await page.getByRole('button', { name: /gca-001 검토 시작$/ }).click()
    await expect(page.locator('.ws-studio-workbench-topbar')).toBeVisible()
    await assertBrand(brand(page))
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.route('**/api/session', route => route.fulfill({ status: 401, json: { code: 'UNAUTHORIZED' } }))
    await page.goto('/')
    await expect(page.getByRole('button', { name: 'SSO로 로그인', exact: true })).toBeVisible()
    await assertBrand(brand(page))
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  })
}

for (const theme of ['light', 'dark'] as const) {
  test(`${theme}: primary foreground contrast and semantic PASS/FAIL colors remain usable`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme })
    await page.goto('/?demo=1')
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await page.getByRole('button', { name: 'CPU 버전과 검사 보기' }).click()
    const pass = page.locator('.ws-status-pass').first(), fail = page.locator('.ws-status-fail').first()
    await expect(pass).toBeVisible(); await expect(fail).toBeVisible()
    const colors = await page.evaluate(() => {
      const parse = (color: string) => (color.match(/[\d.]+/g) || []).slice(0, 3).map(Number)
      const palette = document.createElement('span')
      palette.style.color = 'var(--primary-foreground)'
      palette.style.backgroundColor = 'var(--primary)'
      document.body.append(palette)
      const pass = document.querySelector('.ws-status-pass')!, fail = document.querySelector('.ws-status-fail')!
      const colors = { fg: parse(getComputedStyle(palette).color), bg: parse(getComputedStyle(palette).backgroundColor),
        pass: parse(getComputedStyle(pass).color), fail: parse(getComputedStyle(fail).color) }
      palette.remove()
      return colors
    })
    const lum = (rgb: number[]) => rgb.map(value => { const v = value / 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 })
      .reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0)
    const luminances = [lum(colors.fg), lum(colors.bg)].sort((a, b) => b - a)
    expect((luminances[0] + 0.05) / (luminances[1] + 0.05)).toBeGreaterThanOrEqual(4.5)
    expect(colors.pass[1]).toBeGreaterThan(colors.pass[0])
    expect(colors.fail[0]).toBeGreaterThan(colors.fail[1])
  })
}
