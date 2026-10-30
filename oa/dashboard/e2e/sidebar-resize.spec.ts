import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'
import { SidebarModePreview } from './support/sidebarModePreview'

// Independent synthetic desktop fixtures: no company DB, SSO SDK or model.
async function setupAdmin(page: Page) {
  const state = new SidebarModePreview()
  await page.route('**/api/**', async route => {
    const request = route.request(); const url = new URL(request.url())
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

const handle = (page: Page) => page.getByRole('separator', { name: '사이드바 너비 조절', exact: true })
const layout = (page: Page) => page.locator('.sidebar-layout:visible')
const sidebar = (page: Page) => layout(page).locator('aside:visible')
const main = (page: Page) => layout(page).locator('.main-shell:visible, .ws-main-shell:visible')

async function widthIs(control: Locator, width: number) {
  await expect(control).toHaveAttribute('aria-valuenow', String(width))
}

async function dragTo(page: Page, control: Locator, width: number) {
  const box = (await control.boundingBox())!
  const start = Number(await control.getAttribute('aria-valuenow'))
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + width - start, box.y + box.height / 2, { steps: 5 })
  await page.mouse.up()
  await widthIs(control, width)
}

async function dragBy(page: Page, control: Locator, delta: number) {
  const box = (await control.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + delta, box.y + box.height / 2, { steps: 5 })
  await page.mouse.up()
}

async function geometry(page: Page, width: number) {
  await expect.poll(async () => (await sidebar(page).boundingBox())?.width).toBeCloseTo(width, 0)
  const a = (await sidebar(page).boundingBox())!, b = (await main(page).boundingBox())!
  expect(Math.abs(a.x + a.width - b.x)).toBeLessThanOrEqual(1)
  expect(Math.abs(b.width - (1280 - width))).toBeLessThanOrEqual(1)
}

const cases = [
  { name: 'workspace demo', url: '/?demo=1', ready: 'Atlas 프로젝트', admin: false },
  { name: 'legacy demo', url: '/?demo=1&flow=legacy', ready: 'CHECK-101', admin: false },
  { name: 'production admin', url: '/?page=admin', ready: '프로젝트·권한 관리', admin: true },
] as const

for (const item of cases) {
  test(`${item.name}: pointer resizing moves sidebar and content together and persists`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 1000 })
    if (item.admin) await setupAdmin(page)
    await page.goto(item.url)
    await expect(page.getByText(item.ready, { exact: true }).first()).toBeVisible()
    const control = handle(page)
    await expect(control).toHaveAttribute('aria-valuemin', '216')
    await expect(control).toHaveAttribute('aria-valuemax', '360')
    await widthIs(control, 220)
    await dragBy(page, control, -300); await widthIs(control, 216)
    await dragBy(page, control, 500); await widthIs(control, 360)
    await dragTo(page, control, 300)
    await geometry(page, 300)
    await expect.poll(() => page.evaluate(() => Number(localStorage.getItem('sanity-sidebar-width')))).toBe(300)
    await page.getByRole('button', { name: '메뉴 접기', exact: true }).click()
    await expect(handle(page)).toHaveCount(0)
    await page.getByRole('button', { name: '메뉴 펼치기', exact: true }).click()
    await widthIs(handle(page), 300)
    await geometry(page, 300)
    await page.reload()
    await widthIs(handle(page), 300)
    await geometry(page, 300)
  })

  test(`${item.name}: keyboard range, shift step and double-click reset`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 })
    if (item.admin) await setupAdmin(page)
    await page.goto(item.url)
    await expect(page.getByText(item.ready, { exact: true }).first()).toBeVisible()
    const control = handle(page)
    await control.focus(); await expect(control).toBeFocused()
    await page.keyboard.press('Home'); await widthIs(control, 216)
    await page.keyboard.press('ArrowLeft'); await widthIs(control, 216)
    await page.keyboard.press('ArrowRight'); await widthIs(control, 224)
    await page.keyboard.press('Shift+ArrowRight'); await widthIs(control, 248)
    await page.keyboard.press('Shift+ArrowLeft'); await widthIs(control, 224)
    await page.keyboard.press('End'); await widthIs(control, 360)
    await page.keyboard.press('ArrowRight'); await widthIs(control, 360)
    await control.dblclick(); await widthIs(control, 220)
  })
}

test('pointer cancellation ends the drag and a later drag still works', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 })
  await page.goto('/?demo=1')
  const control = handle(page); await widthIs(control, 220)
  const box = (await control.boundingBox())!
  await control.evaluate(element => element.addEventListener('pointerdown', event => {
    ;(element as HTMLElement).dataset.testPointerId = String((event as PointerEvent).pointerId)
  }, { once: true }))
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + 55, box.y + box.height / 2)
  await widthIs(control, 275)
  await control.evaluate(element => element.dispatchEvent(new PointerEvent('pointercancel', {
    bubbles: true, pointerId: Number((element as HTMLElement).dataset.testPointerId), pointerType: 'mouse', isPrimary: true,
  })))
  await expect(page.locator('html')).not.toHaveClass(/sidebar-pointer-resizing/)
  const afterCancel = await control.getAttribute('aria-valuenow')
  await page.mouse.move(box.x + box.width / 2 + 110, box.y + box.height / 2)
  await page.mouse.up()
  await expect(control).toHaveAttribute('aria-valuenow', afterCancel!)
  await dragTo(page, control, 300)
})

test('viewport clamps effective width while retaining the saved preference', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/?demo=1')
  const control = handle(page)
  await control.focus(); await page.keyboard.press('End')
  await widthIs(control, 360)
  await expect.poll(() => page.evaluate(() => Number(localStorage.getItem('sanity-sidebar-width')))).toBe(360)
  await page.setViewportSize({ width: 1000, height: 1000 })
  await expect(control).toHaveAttribute('aria-valuemax', '280')
  await widthIs(control, 280)
  await expect.poll(async () => (await sidebar(page).boundingBox())?.width).toBeCloseTo(280, 0)
  await expect.poll(async () => (await main(page).boundingBox())?.width).toBeGreaterThanOrEqual(719)
  expect(Number(await page.evaluate(() => localStorage.getItem('sanity-sidebar-width')))).toBe(360)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await expect(control).toHaveAttribute('aria-valuemax', '360')
  await widthIs(control, 360)
  await expect.poll(async () => (await sidebar(page).boundingBox())?.width).toBeCloseTo(360, 0)
  expect(Number(await page.evaluate(() => localStorage.getItem('sanity-sidebar-width')))).toBe(360)
})

test('Escape cancels a real drag, restores its starting width and removes drag state', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 })
  await page.goto('/?demo=1')
  const control = handle(page); await widthIs(control, 220)
  const box = (await control.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + 55, box.y + box.height / 2)
  await widthIs(control, 275)
  await expect(page.locator('html')).toHaveClass(/sidebar-pointer-resizing/)
  await page.keyboard.press('Escape')
  await widthIs(control, 220)
  await expect(page.locator('html')).not.toHaveClass(/sidebar-pointer-resizing/)
  await page.mouse.move(box.x + box.width / 2 + 110, box.y + box.height / 2)
  await page.mouse.up()
  await widthIs(control, 220)
  await dragTo(page, control, 300)
})

test('authenticated mode switches and reload retain one expanded width', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 }); const state = await setupAdmin(page)
  await page.goto('/?page=workbench')
  await dragTo(page, handle(page), 300)
  await page.getByRole('button', { name: '관리자 모드', exact: true }).click()
  await expect(page.getByRole('heading', { name: '프로젝트·권한 관리', exact: true })).toBeVisible()
  await widthIs(handle(page), 300); await geometry(page, 300)
  await page.reload(); await widthIs(handle(page), 300)
  await page.getByRole('button', { name: '사용자 모드', exact: true }).click()
  await widthIs(handle(page), 300); await geometry(page, 300)
  expect(state.writes).toEqual([])
})

test('collapse animates sidebar and content in sync; reduced motion finishes without a transition', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1000 })
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.goto('/?demo=1')
  await dragTo(page, handle(page), 300)
  const observed = await layout(page).evaluate(async root => {
    const side = root.querySelector('aside')!, content = root.querySelector('.ws-main-shell')!
    root.querySelector<HTMLButtonElement>('[aria-label="메뉴 접기"]')!.click()
    await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame)
    const animations = [...side.getAnimations(), ...content.getAnimations()]
      .filter(animation => animation.effect?.getTiming().duration !== 0)
    if (!animations.length) return null
    for (const animation of animations) {
      const duration = Number(animation.effect!.getTiming().duration)
      animation.pause(); animation.currentTime = duration / 2
    }
    const a = side.getBoundingClientRect(), b = content.getBoundingClientRect()
    return { width: a.width, gap: Math.abs(a.right - b.left), count: animations.length }
  })
  expect(observed).not.toBeNull()
  expect(observed!.width).toBeGreaterThan(80); expect(observed!.width).toBeLessThan(300)
  expect(observed!.gap).toBeLessThanOrEqual(1)

  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.reload(); await widthIs(handle(page), 300)
  await page.getByRole('button', { name: '메뉴 접기', exact: true }).click()
  await expect(handle(page)).toHaveCount(0)
  const reduced = await layout(page).evaluate(root => {
    const side = root.querySelector('aside')!, content = root.querySelector('.ws-main-shell')!
    const a = side.getBoundingClientRect(), b = content.getBoundingClientRect()
    return { width: a.width, gap: Math.abs(a.right - b.left), animations: [...side.getAnimations(), ...content.getAnimations()].length }
  })
  expect(reduced.width).toBeLessThan(100); expect(reduced.gap).toBeLessThanOrEqual(1)
  expect(reduced.animations).toBe(0)
})
