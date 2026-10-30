import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const vwpId = '20000000-0000-4000-8000-000000000091'
const oaId = '20000000-0000-4000-8000-000000000092'
const vwpPath = `/api/ai/connections/${vwpId}/credentials`
const syntheticKey = 'synthetic-vwp-personal-key'
const issueUrl = 'https://identity.example.test/vwp-keys'

async function setup(page: Page, options: { authMode?: string; error?: string; registered?: boolean; disabled?: boolean; expired?: boolean; scopeChanged?: boolean; fail?: boolean } = {}) {
  let revision = options.registered ? 1 : 0
  let registered = !!options.registered
  const writes: { path: string; body: Record<string, unknown> }[] = []
  const status = () => ({ registered, revision, expired: !!options.expired && registered, connection_changed: !!options.scopeChanged && registered })
  const remote = () => ({ id: vwpId, label: '합성 VWP', enabled: !options.disabled, execution_site: 'vwp', auth_mode: options.authMode || 'bearer',
    model: 'vwp-model', models: ['vwp-model'], issuance_url: issueUrl, credential: status(), credential_required: true, credential_ready: registered && !options.error,
    credential_error: options.error || (registered ? null : 'AI_USER_TOKEN_REQUIRED') })
  await page.route('**/api/**', route => {
    const req = route.request(); const path = new URL(req.url()).pathname
    if (path === '/api/workbench/session') return route.fulfill({ json: { user: { id: 'synthetic-self', name: '합성 사용자', username: 'synthetic-self', role: 'OWNER' }, csrf: '', expires_at: '', environment: 'PRODUCTION_SSO' } })
    if (path === '/api/workbench/overview') return route.fulfill({ json: { user: { id: 'synthetic-self', name: '합성 사용자', username: 'synthetic-self', role: 'OWNER' }, data_origin: 'PRODUCTION', projects: [] } })
    if (path === '/api/ai/connections') return route.fulfill({ json: { items: [remote(), { id: oaId, label: '합성 OA', enabled: true, auth_mode: 'bearer', model: 'oa-model', models: ['oa-model'], issuance_url: null, credential: { registered: false, revision: 0 }, credential_error: null }] } })
    if (path === vwpPath || path === vwpPath + '/remove') {
      const body = req.postDataJSON() as Record<string, unknown>
      expect(req.method()).toBe('POST')
      expect(req.headers()['x-sanity-request']).toBe('1')
      expect(req.headers().authorization).toBeUndefined()
      expect(body.expected_revision).toBe(revision)
      expect(Object.keys(body).sort()).toEqual(path.endsWith('/remove') ? ['expected_revision'] : ['expected_revision', 'token'])
      writes.push({ path, body })
      if (options.fail) return route.fulfill({ status: 409, json: { code: 'REVISION_CONFLICT', private_detail: syntheticKey } })
      revision++; registered = !path.endsWith('/remove')
      return route.fulfill({ json: status() })
    }
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } })
  })
  await page.goto('/?page=workbench')
  await page.getByRole('button', { name: '개인 AI 토큰', exact: true }).click()
  const remoteArticle = page.getByRole('article', { name: '합성 VWP 개인 인증값', exact: true })
  return { writes, remoteArticle }
}

test('VWP personal Bearer key is masked, registered, replaced and removed with independent OA credentials', async ({ page }) => {
  const { writes, remoteArticle } = await setup(page)
  const input = remoteArticle.getByLabel('합성 VWP 개인 Bearer 토큰', { exact: true })
  await expect(input).toHaveAttribute('type', 'password')
  await expect(remoteArticle).toContainText('개인 인증값이 필요합니다')
  const link = remoteArticle.getByRole('link', { name: /인증값 발급받기/ })
  await expect(link).toHaveAttribute('href', issueUrl)
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  await expect(link).toHaveAttribute('referrerpolicy', 'no-referrer')
  await expect(page.getByRole('article', { name: '합성 OA 개인 인증값' }).getByLabel('합성 OA 개인 Bearer 토큰')).toBeVisible()
  await input.fill(syntheticKey)
  await remoteArticle.getByRole('button', { name: '인증값 저장' }).click()
  await expect(input).toHaveValue('')
  await expect(remoteArticle).toContainText('등록됨')
  await input.fill('synthetic-vwp-replacement')
  await remoteArticle.getByRole('button', { name: '인증값 교체' }).click()
  await expect(input).toHaveValue('')
  await expect.poll(() => writes.length).toBe(2)
  await remoteArticle.getByRole('button', { name: '연결 해제' }).click()
  await expect(remoteArticle).toContainText('미등록')
  expect(writes.map(write => write.body.expected_revision)).toEqual([0, 1, 2])
  expect(writes[2].body).not.toHaveProperty('token')
  await expect(page.locator('body')).not.toContainText(syntheticKey)
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain(syntheticKey)
})

test('VWP x-client mode asks for an API key, and failed saves never echo or retain it', async ({ page }) => {
  const { writes, remoteArticle } = await setup(page, { authMode: 'x_client_api_key', fail: true })
  const input = remoteArticle.getByLabel('합성 VWP 개인 API 키')
  await input.fill(syntheticKey)
  await remoteArticle.getByRole('button', { name: '인증값 저장' }).click()
  await expect(input).toHaveValue('')
  await expect(page.getByRole('alert')).toContainText('다른 창에서 설정이 바뀌었습니다')
  await expect(page.locator('body')).not.toContainText(syntheticKey)
  expect(writes).toHaveLength(1)
})

for (const scenario of [
  { label: 'expired', options: { registered: true, expired: true }, message: '인증값이 만료됐습니다' },
  { label: 'changed scope', options: { registered: true, scopeChanged: true }, message: '인증값을 다시 등록해야 합니다' },
] as const) test(`VWP ${scenario.label} gives personal credential guidance`, async ({ page }) => {
  const { remoteArticle } = await setup(page, scenario.options)
  await expect(remoteArticle).toContainText(scenario.message)
})

test('VWP permission denial blocks registration but permits deleting the existing personal key', async ({ page }) => {
  const { writes, remoteArticle } = await setup(page, { registered: true, error: 'FORBIDDEN' })
  await expect(remoteArticle.getByLabel('합성 VWP 개인 Bearer 토큰')).toBeDisabled()
  await expect(remoteArticle).toContainText('현재 사용자에게 VWP 모델 사용 권한이 없습니다')
  await remoteArticle.getByRole('button', { name: '연결 해제' }).click()
  await expect(remoteArticle).toContainText('미등록')
  expect(writes[0].path).toBe(vwpPath + '/remove')
  await expect(remoteArticle.getByLabel('합성 VWP 개인 Bearer 토큰')).toBeDisabled()
  await expect(remoteArticle).toContainText('현재 사용자에게 VWP 모델 사용 권한이 없습니다')
})

test('VWP profile failure remains visible and blocks registration after removing the stored key', async ({ page }) => {
  const { writes, remoteArticle } = await setup(page, { registered: true, error: 'AI_VWP_PROFILE_UNAVAILABLE', expired: true })
  const input = remoteArticle.getByLabel('합성 VWP 개인 Bearer 토큰')
  await expect(input).toBeDisabled()
  await expect(remoteArticle).toContainText('VWP AI 실행 프로필을 사용할 수 없습니다')
  await expect(remoteArticle).not.toContainText('인증값이 만료됐습니다')
  await remoteArticle.getByRole('button', { name: '연결 해제' }).click()
  await expect(remoteArticle).toContainText('미등록')
  await expect(remoteArticle).toContainText('VWP AI 실행 프로필을 사용할 수 없습니다')
  await expect(input).toBeDisabled()
  expect(writes[0].path).toBe(vwpPath + '/remove')
})

test('disabled VWP connection permits removing a stored personal key', async ({ page }) => {
  const { writes, remoteArticle } = await setup(page, { registered: true, disabled: true })
  await expect(remoteArticle.getByLabel('합성 VWP 개인 Bearer 토큰')).toBeDisabled()
  await remoteArticle.getByRole('button', { name: '연결 해제' }).click()
  await expect(remoteArticle).toContainText('사용 중지')
  expect(writes[0].path).toBe(vwpPath + '/remove')
})

test('legacy VWP metadata asks for configuration review instead of claiming shared credentials', async ({ page }) => {
  const { writes } = await setup(page, { authMode: 'vwp_managed', registered: true })
  const article = page.getByRole('article', { name: '합성 VWP VWP 연결' })
  await expect(article).toContainText('인증 방식을 확인할 수 없습니다')
  await expect(article).not.toContainText('VWP에서 인증 관리')
  await expect(article.locator('input')).toHaveCount(0)
  await article.getByRole('button', { name: '연결 해제' }).click()
  await expect.poll(() => writes.length).toBe(1)
  expect(writes[0].path).toBe(vwpPath + '/remove')
})
