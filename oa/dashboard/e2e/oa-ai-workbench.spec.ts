import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import type { CodexJob, ReviewCase, ReviewProposal } from '../src/workspaceApi'

// All identities, reports and model replies here are synthetic network fixtures.
const user = { id: 'synthetic-owner', name: '합성 검토자', username: 'synthetic-owner', role: 'OWNER' }
const version = { id: 'v1', project_id: 'p1', block_id: 'b1', net_ver: 'NET-1', revision: 'REV-1', layout: 'POST', source_pre_id: null, ecos: ['ECO-2'] }
const report = { id: 'report-a', item_id: 'synthetic_check', project_id: 'p1', block_id: 'b1', version_id: 'v1', eco: 'ECO-2', family: 'GCA', rule: 'SYNTHETIC_RULE', check_status: 'FAIL', parse_status: 'COMPLETE', parser: 'synthetic-v1', record_count: 2, blockers: [], analysis_ready: true, source_storage: 'PARTS_V2' }
const records = [1, 2].map(index => ({ id: `log-${index}`, fields: { mode: 'test', object: `node-${index}` }, check_status: 'FAIL', raw: `Synthetic log ${index}: verification required`, line_start: index, line_end: index, reasons: [], raw_display_truncated: false }))
const model = { model: 'synthetic-oa-model', prompt_version: 'test-v1', input_sha256: 'a'.repeat(64), usage: { input_tokens: 100, output_tokens: 50 }, seconds: 0.1, input_case_count: 0, synthetic_only: false }
const at = '2026-10-22T00:00:00Z'
const base = '/api/workbench/blocks/b1/versions/v1/reports/report-a'
const url = '/?page=workbench&project=p1&block=b1&version=v1&eco=ECO-2&item=synthetic_check&tab=reports&review=report-a'
const emptyPage = { items: [], total: 0, offset: 0, limit: 25 }

async function mockOA(page: Page, options: { configured?: boolean; initialJob?: CodexJob; fail?: string; auth?: number } = {}) {
  const state = { revision: 0, confirmed: false, reviewRevision: 0, clusterJob: options.initialJob || null as CodexJob | null, reviewJob: null as CodexJob | null, clusterReads: 0, reviewReads: 0, current: null as ReviewCase | null, proposal: null as ReviewProposal | null, writes: [] as { path: string; body: Record<string, unknown>; marker: string | undefined }[], unexpected: [] as string[], calls: [] as string[] }
  const capability = (job: CodexJob | null) => ({ available: options.configured !== false, supported: true, enabled: options.configured !== false, model: model.model, remaining: null, provider: 'OA_OPENAI_COMPATIBLE', synthetic_only: false, job })
  const cluster = () => state.revision ? { id: `cluster-${state.revision}`, report_id: report.id, revision: state.revision, action: state.confirmed ? 'CONFIRM' : 'PROPOSE', confirmed: state.confirmed, reusable: false, compatible_versions: false, criteria: '', reason: '전체 로그의 검토 의도에 따라 묶었습니다.', identity_fields: ['mode'], group_count: 1, record_count: 2, at, actor: user, references: [], advisor: 'OA_OPENAI_COMPATIBLE', model_called: true, model_metadata: model, rationale_counts: { MISSING: 0, DRAFT: Number(!state.confirmed), CONFIRMED: Number(state.confirmed), STALE: 0 } } : null
  const group = () => ({ id: 'g1', label: '확인이 필요한 합성 대상', record_count: 2, check_status: 'FAIL', status: state.current?.conclusion || 'PENDING', revision: state.reviewRevision, example: {}, rationale: { status: state.confirmed ? 'CONFIRMED' : 'DRAFT', reason: '같은 조건의 확인 대상입니다.', reason_truncated: false, reason_omitted_chars: 0, source: 'AI', evidence_count: 1 } })
  const finish = (job: CodexJob) => {
    job.status = options.fail ? 'FAILED' : 'COMPLETED'; job.error = options.fail || null
    if (!options.fail) state.revision = 1
  }
  await page.route('**/api/**', async route => {
    const req = route.request(); const path = new URL(req.url()).pathname; state.calls.push(`${req.method()} ${path}`)
    const body = req.method() === 'POST' ? req.postDataJSON() as Record<string, unknown> : undefined
    if (body) state.writes.push({ path, body, marker: req.headers()['x-sanity-request'] })
    if (path.startsWith('/workspace/api/')) { state.unexpected.push(path); return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } }) }
    if (path === '/api/workbench/session') return route.fulfill(options.auth ? { status: options.auth, json: { code: 'UNAUTHORIZED' } } : { json: { user, csrf: '', expires_at: '', environment: 'PRODUCTION_SSO' } })
    if (path === '/api/workbench/overview') return route.fulfill({ json: { user, data_origin: 'PRODUCTION', projects: [{ id: 'p1', name: '합성 프로젝트', description: '', tops: [], blocks: [{ id: 'b1', project_id: 'p1', name: '합성 블록', owners: [user], can_assign_por: true, por: { revision: 0, version: null, assigned_by: null, assigned_at: null }, report_version: version, counts: { FAIL: 1 }, total: 1 }] }] } })
    if (path === '/api/workbench/blocks/b1/versions') return route.fulfill({ json: { items: [version], can_assign_por: true, por: { revision: 0, version: null, assigned_by: null, assigned_at: null } } })
    if (path === '/api/workbench/blocks/b1/history') return route.fulfill({ json: { items: [] } })
    if (path === '/api/workbench/blocks/b1/versions/v1/reports') return route.fulfill({ json: { items: [report], total: 1, offset: 0, limit: 25, counts: { FAIL: 1 }, groups: [{ item_id: report.item_id, total: 1, counts: { FAIL: 1 }, parse_counts: { COMPLETE: 1 }, families: ['GCA'] }], version } })
    if (path === base) return route.fulfill({ json: report })
    if (path === base+'/clustering/ai' && body) {
      expect(body.expected_revision).toBe(state.revision)
      state.clusterJob = { id: String(body.request_id), status: 'PENDING', error: null, model: model.model, created_at: at, expected_revision: state.revision, input_case_count: 0 }
      state.clusterReads = 0
      return route.fulfill({ status: 202, json: state.clusterJob })
    }
    if (path === base+'/workbench') {
      if (state.clusterJob && ['PENDING', 'RUNNING'].includes(state.clusterJob.status) && ++state.clusterReads >= 2) finish(state.clusterJob)
      return route.fulfill({ json: { report, version, current: cluster(), revision: state.revision, ready: true, can_edit: true, stale: false, task: null, ai: capability(state.clusterJob), record_count: 2, raw_line_count: 2, source_sha256: 'a'.repeat(64), case_count: 0, reusable_count: 0, removed_count: 0, corporate_ai_connected: options.configured !== false, summary: { confirmed: state.confirmed, total: state.revision ? 1 : 0, reviewed: Number(!!state.current), pending: Number(!state.current), complete: !!state.current, rationale_counts: cluster()?.rationale_counts || { MISSING: 0, DRAFT: 0, CONFIRMED: 0, STALE: 0 }, counts: { PASS: Number(state.current?.conclusion === 'PASS'), FAIL: Number(state.current?.conclusion === 'FAIL'), DEFER: 0, STALE: 0 } } } })
    }
    if (path === base+'/clustering/groups') return route.fulfill({ json: { items: [group()], total: 1, offset: 0, limit: 25 } })
    if (path === base+'/clustering/records') return route.fulfill({ json: { items: records, total: 2, offset: 0, limit: 50, cluster_revision: state.revision, group: group() } })
    if (path === base+'/groups/g1/rationale') return route.fulfill({ json: { group_id: 'g1', cluster_revision: state.revision, event_id: cluster()!.id, status: group().rationale.status, rationale: { content: { reason: group().rationale.reason, evidence_ids: ['log-1'], reference_groups: [] }, source: 'AI', status: group().rationale.status, binding: {}, reviewed_by: null, reviewed_at: null, change_reason: '', previous_event_id: null }, samples: [records[0]], references: [], history: emptyPage } })
    if (path === base+'/clustering' && body) {
      expect(body.action).toBe('CONFIRM'); expect(body.expected_revision).toBe(state.revision)
      state.confirmed = true; state.revision++
      return route.fulfill({ json: { event: cluster() } })
    }
    if (path === base+'/groups/g1/review/ai' && body) {
      expect(body.expected_cluster_revision).toBe(state.revision)
      expect(body.expected_revision).toBe(state.reviewRevision)
      state.reviewJob = { id: String(body.request_id), status: 'PENDING', error: null, model: model.model, created_at: at, expected_revision: state.reviewRevision, input_case_count: 0 }
      return route.fulfill({ status: 202, json: state.reviewJob })
    }
    if (path === base+'/groups/g1/review') {
      if (body) {
        state.reviewRevision++
        state.current = { ...body, id: 'human-review-1', report_id: report.id, version_id: version.id, revision: state.reviewRevision, actor: user, at, source_sha256: 'a'.repeat(64), source_version: version, proposal_conclusion: state.proposal?.conclusion || null, proposal_agreed: false, group_id: 'g1', cluster_revision: state.revision } as ReviewCase
        return route.fulfill({ json: { event: state.current } })
      }
      if (state.reviewJob && ['PENDING', 'RUNNING'].includes(state.reviewJob.status) && ++state.reviewReads >= 2) {
        state.reviewJob.status = 'COMPLETED'
        state.proposal = { id: 'proposal-1', conclusion: 'FAIL', reason: '현재 로그 두 건은 확인된 합성 점검 기준을 충족하지 않습니다.', references: [], advisor: 'OA_OPENAI_COMPATIBLE', model_called: true, at, schema_version: 2, evidence_ids: ['log-1', 'log-2'], criterion_ids: ['synthetic-check-1'], model_metadata: model }
      }
      return route.fulfill({ json: { current: state.current, revision: state.reviewRevision, history: state.current ? [state.current] : [], facts: { rule: report.rule, mode: 'test' }, ready: true, can_review: true, cases: [], case_count: 0, reusable_count: 0, cases_displayed: 0, proposal: state.proposal, corporate_ai_connected: true, ai: capability(state.reviewJob) } })
    }
    state.unexpected.push(`${req.method()} ${path}`)
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } })
  })
  return state
}

test('production first classification and direct-evidence judgment retain human correction without a reuse opt-in', async ({ page }) => {
  const state = await mockOA(page)
  await page.goto(url)
  await expect(page.getByRole('heading', { name: '로그 검토', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '원본 내려받기' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '참고 엔진으로 초안 생성' })).toHaveCount(0)
  await page.getByRole('button', { name: 'AI로 그룹 만들기', exact: true }).click()
  await expect(page.locator('.ws-rationale-text').first()).toHaveText('같은 조건의 확인 대상입니다.')
  await page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
  const review = page.locator('.ws-judgment-workspace')
  await review.getByRole('button', { name: 'AI 판정 받기', exact: true }).click()
  await review.getByLabel('판정 근거', { exact: true }).fill('사람이 작성 중인 검토 근거')
  await expect(review.locator('.ws-review-proposal')).toContainText('AI 모델 · synthetic-oa-model')
  await expect(review.getByLabel('판정 근거', { exact: true })).toHaveValue('사람이 작성 중인 검토 근거')
  await review.getByText('이번 로그의 직접 근거 2건', { exact: true }).click()
  await expect(review.locator('.ws-review-proposal code').first()).toHaveText('log-1')
  await review.getByText('적용한 점검 기준 1건', { exact: true }).click()
  await expect(review.getByText('synthetic-check-1', { exact: true })).toBeVisible()
  await review.getByRole('button', { name: '제안 적용', exact: true }).click()
  await review.getByRole('radio', { name: 'PASS', exact: true }).check()
  await review.getByLabel('판정 근거', { exact: true }).fill('확인한 예외 조건에 해당하므로 사람 검토 결과 통과로 정정합니다.')
  await review.getByRole('button', { name: '판정 저장', exact: true }).click()
  await expect(review.getByText('다음 AI 판단의 참고 사례 후보로 축적됩니다.', { exact: false })).toBeVisible()
  expect(state.current).toMatchObject({ conclusion: 'PASS', reusable: false, proposal_id: 'proposal-1' })
  expect(state.writes).toHaveLength(4)
  expect(state.writes.every(write => write.marker === '1')).toBe(true)
  expect(state.unexpected).toEqual([])
  expect(new URL(page.url()).searchParams.get('page')).toBe('workbench')
  expect(new URL(page.url()).searchParams.get('eco')).toBe('ECO-2')
  expect(state.calls.some(call => call.includes('/workspace/'))).toBe(false)
})

test('production session failure returns to SSO portal without synthetic login even with demo query', async ({ page }) => {
  await mockOA(page, { auth: 401 })
  await page.goto(url+'&demo=1')
  await expect(page.getByRole('link', { name: '포털에서 로그인·권한 확인' })).toHaveAttribute('href', '/')
  await expect(page.getByLabel('사용자 ID', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('비밀번호', { exact: true })).toHaveCount(0)
  await expect(page.getByText('공용 테스트 비밀번호', { exact: false })).toHaveCount(0)
})

test('unconfigured OA AI does not offer a reference-engine substitute', async ({ page }) => {
  const state = await mockOA(page, { configured: false })
  await page.goto(url)
  await expect(page.getByRole('button', { name: 'AI로 그룹 만들기', exact: true })).toBeDisabled()
  await expect(page.getByText('OA AI가 아직 설정되지 않았습니다.', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: '그룹 초안 만들기', exact: true })).toHaveCount(0)
  expect(state.writes).toEqual([])
})

test('pending jobs resume with GET only and unknown outcomes never auto-submit', async ({ page }) => {
  const state = await mockOA(page, { initialJob: { id: 'existing-job', status: 'PENDING', error: null, model: model.model, created_at: at, expected_revision: 0, input_case_count: 0 } })
  await page.goto(url)
  await expect(page.locator('.ws-rationale-text').first()).toBeVisible()
  expect(state.writes).toEqual([])
  state.revision = 0
  state.clusterJob = { ...state.clusterJob!, status: 'UNKNOWN', error: 'AI_RESULT_UNKNOWN' }
  await page.reload()
  await expect(page.getByText('AI 호출 결과가 불명확합니다.', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '최신 구성 다시 조회', exact: true }).click()
  expect(state.writes).toEqual([])
})

test('model limit failure shows rejected whole-input work and preserves manual request boundary', async ({ page }) => {
  const state = await mockOA(page, { fail: 'AI_INPUT_LIMIT' })
  await page.goto(url)
  await page.getByRole('button', { name: 'AI로 그룹 만들기', exact: true }).click()
  await expect(page.getByText('로그를 생략하지 않았습니다.', { exact: false })).toBeVisible()
  expect(state.revision).toBe(0)
  expect(state.writes).toHaveLength(1)
  await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true })).toHaveCount(0)
})


test('production report preview opens group workflow without unsupported full-report judgment requests', async ({ page }) => {
  const state = await mockOA(page)
  await page.goto(url.replace('&review=report-a', ''))
  await page.getByRole('button', { name: 'report-a 보고서 보기', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('원본 report 파일은 VWP에 보관됩니다.')
  await expect(page.getByRole('tab', { name: '사용자 판정·AI review', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '전체 로그·검토 열기', exact: true }).click()
  await expect(page.getByRole('heading', { name: '로그 검토', exact: true })).toBeVisible()
  expect(state.calls).not.toContain(`GET ${base}/review`)
  expect(state.unexpected).toEqual([])
})
