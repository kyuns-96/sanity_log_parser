import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const vwpId = '20000000-0000-4000-8000-000000000091'
const oaId = '20000000-0000-4000-8000-000000000092'
const vwpPath = `/api/ai/connections/${vwpId}/credentials`
const syntheticKey = 'synthetic-vwp-personal-key'
const issueUrl = 'https://identity.example.test/vwp-keys'

async function setup(page: Page) {
  let revision = 0
  let registered = false
  const writes: { path: string; body: Record<string, unknown> }[] = []
  const status = () => ({ registered, revision, expired: false, connection_changed: false })
  const remote = () => ({ id: vwpId, label: '합성 VWP', enabled: true, execution_site: 'vwp', auth_mode: 'bearer',
    model: 'vwp-model', models: ['vwp-model'], issuance_url: issueUrl, credential: status(), credential_required: true,
    credential_ready: registered, credential_error: registered ? null : 'AI_USER_TOKEN_REQUIRED' })
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
