import { expect, test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import type { Page } from '@playwright/test'
import type { PreservedSource } from '../src/PreservedSources'
import type { ClusterModelMetadata, CodexJob, ReportReview, ReviewCase, ReviewProposal } from '../src/workspaceApi'
import { makeWorkspaceDemo } from '../src/workspaceDemo'

// All identities, reports and model replies here are synthetic network fixtures.
const user = { id: 'synthetic-owner', name: '합성 검토자', username: 'synthetic-owner', role: 'OWNER' }
const version = { id: 'v1', project_id: 'p1', block_id: 'b1', net_ver: 'NET-1', revision: 'REV-1', layout: 'POST' as const, source_pre_id: null, ecos: ['ECO-2'] }
const report = { id: 'report-a', item_id: 'synthetic_check', project_id: 'p1', block_id: 'b1', version_id: 'v1', eco: 'ECO-2', family: 'GCA', rule: 'SYNTHETIC_RULE', check_status: 'FAIL', parse_status: 'COMPLETE', parser: 'synthetic-v1', record_count: 2, blockers: [], analysis_ready: true, source_storage: 'PARTS_V2' }
const makeRecords = (count: number) => Array.from({ length: count }, (_, position) => position + 1).map(index => ({ id: `log-${index}`, fields: { mode: 'test', object: `node-${index}` }, check_status: 'FAIL', raw: `Synthetic log ${index}: verification required\nFull source evidence for node-${index}`, line_start: index * 2 - 1, line_end: index * 2, reasons: [], raw_display_truncated: false }))
const model = { model: 'synthetic-oa-model', prompt_version: 'test-v1', input_sha256: 'a'.repeat(64), usage: { input_tokens: 100, output_tokens: 50 }, seconds: 0.1, input_case_count: 0, synthetic_only: false }
const at = '2026-10-22T00:00:00Z'
const base = '/api/workbench/blocks/b1/versions/v1/reports/report-a'
const url = '/?page=workbench&project=p1&block=b1&version=v1&eco=ECO-2&item=synthetic_check&tab=reports&review=report-a'
const emptyPage = { items: [], total: 0, offset: 0, limit: 25 }
const connectionId = '10000000-0000-4000-8000-000000000010'
const credentialPath = `/api/ai/connections/${connectionId}/credentials`

type MockOptions = { holdJob?: boolean; connections?: { id: string; label: string; model: string; models: string[]; available: boolean; enabled: boolean; credential_required?: boolean; credential_ready?: boolean }[]; models?: string[]; modelMetadata?: Partial<ClusterModelMetadata>; preserved?: PreservedSource[]; collectionOnly?: boolean; configured?: boolean; personalToken?: boolean; initialJob?: CodexJob; fail?: string; auth?: number; readOnly?: boolean; recordCount?: number; initialRevision?: number; confirmed?: boolean; omitFirstReasons?: boolean; longFields?: boolean; currentCase?: ReviewCase; cases?: ReviewCase[]; proposal?: ReviewProposal; currentStale?: boolean }

async function mockOA(page: Page, options: MockOptions = {}) {
  const actor = options.readOnly ? { ...user, role: 'TOP' } : user
  const sourceRecords = makeRecords(options.recordCount || 2).map((value, index) => {
    const { reasons, ...record } = value
    return { ...record, ...(options.collectionOnly ? { check_status: 'UNKNOWN', evaluation_status: 'NOT_PERFORMED' } : {}), fields: options.longFields && index === 0 ? { object: 'node-1/' + 'long-path/'.repeat(20), mode: 'MODE_' + 'LONG_NAME_'.repeat(12) } : record.fields,
      ...(!options.omitFirstReasons || index > 0 ? { reasons } : {}) }
  })
  const currentReport = { ...report, ...(options.preserved ? { preserved_sources: options.preserved } : {}), record_count: sourceRecords.length, ...(options.collectionOnly ? { check_status: 'UNKNOWN', evaluation_mode: 'none', not_evaluated_count: sourceRecords.length } : {}) }
  const state = { revision: options.initialRevision || 0, confirmed: options.confirmed || false, reviewRevision: 0, clusterJob: options.initialJob || null as CodexJob | null, reviewJob: null as CodexJob | null, clusterReads: 0, reviewReads: 0, current: null as ReviewCase | null, proposal: null as ReviewProposal | null, writes: [] as { path: string; body: Record<string, unknown>; marker: string | undefined }[], unexpected: [] as string[], calls: [] as string[], recordRequests: [] as { offset: number; limit: number; search: string; group: string | null; revision: string | null }[], recordPause: null as { offset: number; promise: Promise<void> } | null, recordFailure: null as { offset: number; remaining: number } | null }
  const reviewHistory = options.currentCase ? [options.currentCase] : []
  let credentialReady = false
  let credentialRevision = 0
  state.current = options.currentCase || null
  state.reviewRevision = options.currentCase?.revision || 0
  state.proposal = options.proposal || null
  const connections = () => options.configured === false ? [] : options.connections ?? [{ id: connectionId, label: '합성 AI 연결', model: model.model, models: options.models || [model.model], enabled: true, available: !options.personalToken || credentialReady, credential_required: true, credential_ready: !options.personalToken || credentialReady }]
  const capability = (job: CodexJob | null) => ({ available: options.configured !== false && (!options.personalToken || credentialReady), supported: true, enabled: options.configured !== false, remaining: null, provider: 'OA_OPENAI_COMPATIBLE', synthetic_only: false, job, connections: connections(), connection_id: connections()[0]?.id || '', model: connections()[0]?.model || '', models: connections()[0]?.models || [], ...(options.personalToken ? { credential_required: true, credential_ready: credentialReady, credential_error: credentialReady ? null : 'AI_USER_TOKEN_REQUIRED' } : {}) })
  const cluster = () => state.revision ? { id: `cluster-${state.revision}`, report_id: report.id, revision: state.revision, action: state.confirmed ? 'CONFIRM' : 'PROPOSE', confirmed: state.confirmed, reusable: false, compatible_versions: false, criteria: '', reason: '전체 로그의 검토 의도에 따라 묶었습니다.', identity_fields: ['mode'], group_count: 1, record_count: sourceRecords.length, at, actor, references: [], advisor: 'OA_OPENAI_COMPATIBLE', model_called: true, model_metadata: { ...model, model: state.clusterJob?.model || model.model, ...options.modelMetadata }, rationale_counts: { MISSING: 0, DRAFT: Number(!state.confirmed), CONFIRMED: Number(state.confirmed), STALE: 0 } } : null
  const group = () => ({ id: 'g1', label: '확인이 필요한 합성 대상', record_count: sourceRecords.length, check_status: options.collectionOnly ? 'UNKNOWN' : 'FAIL', status: state.current?.conclusion || 'PENDING', revision: state.reviewRevision, example: {}, rationale: { status: state.confirmed ? 'CONFIRMED' : 'DRAFT', reason: '같은 조건의 확인 대상입니다.', reason_truncated: false, reason_omitted_chars: 0, source: 'AI', evidence_count: 1 } })
  const finish = (job: CodexJob) => {
    job.status = options.fail ? 'FAILED' : 'COMPLETED'; job.error = options.fail || null
    if (!options.fail) state.revision = 1
  }
  await page.route('**/api/**', async route => {
    const req = route.request(); const path = new URL(req.url()).pathname; state.calls.push(`${req.method()} ${path}`)
    const body = req.method() === 'POST' ? req.postDataJSON() as Record<string, unknown> : undefined
    if (body) {
      state.writes.push({ path, body, marker: req.headers()['x-sanity-request'] })
      if (options.readOnly) return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } })
    }
    if (path.startsWith('/workspace/api/')) { state.unexpected.push(path); return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } }) }
    if (path === '/api/ai/connections') return route.fulfill({ json: { items: connections().map(value => ({ ...value, auth_mode: 'bearer', issuance_url: null, credential: { registered: credentialReady, revision: credentialRevision }, credential_error: null })) } })
    if (path === credentialPath && options.personalToken) {
      if (body) { expect(body.expected_revision).toBe(credentialRevision); credentialReady = true; credentialRevision++ }
      return route.fulfill({ json: { enabled: true, registered: credentialReady, revision: credentialRevision, expires_at: null, expired: false, expires_soon: false } })
    }
    if (path === '/api/workbench/session') return route.fulfill(options.auth ? { status: options.auth, json: { code: 'UNAUTHORIZED' } } : { json: { user: actor, csrf: '', expires_at: '', environment: 'PRODUCTION_SSO' } })
    if (path === '/api/workbench/overview') return route.fulfill({ json: { user: actor, data_origin: 'PRODUCTION', projects: [{ id: 'p1', name: '합성 프로젝트', description: '', tops: [], blocks: [{ id: 'b1', project_id: 'p1', name: '합성 블록', owners: options.readOnly ? [] : [actor], can_assign_por: !options.readOnly, por: { revision: 0, version: null, assigned_by: null, assigned_at: null }, report_version: version, counts: { FAIL: 1 }, total: 1 }] }] } })
    if (path === '/api/workbench/blocks/b1/versions') return route.fulfill({ json: { items: [version], can_assign_por: !options.readOnly, por: { revision: 0, version: null, assigned_by: null, assigned_at: null } } })
    if (path === '/api/workbench/blocks/b1/history') return route.fulfill({ json: { items: [] } })
    if (path === '/api/workbench/blocks/b1/intake' && req.method() === 'GET') {
      const params = new URL(req.url()).searchParams
      expect(params.get('offset')).toBe('0')
      expect(params.get('limit')).toBe('25')
      return route.fulfill({ json: { items: [], total: 0, can_confirm: !options.readOnly } })
    }
    if (path === '/api/workbench/blocks/b1/versions/v1/reports') return route.fulfill({ json: { items: [currentReport], total: 1, offset: 0, limit: 25, counts: { FAIL: 1 }, groups: [{ item_id: report.item_id, total: 1, counts: { FAIL: 1 }, parse_counts: { COMPLETE: 1 }, families: ['GCA'] }], version } })
    if (path === base) return route.fulfill({ json: currentReport })
    if (path === base+'/clustering/ai' && body) {
      expect(body.expected_revision).toBe(state.revision)
      state.clusterJob = { id: String(body.request_id), status: 'PENDING', error: null, model: String(body.model_id || model.model), ...(body.connection_id ? { connection_id: String(body.connection_id), connection_label: connections().find(value => value.id === body.connection_id)?.label } : {}), created_at: at, expected_revision: state.revision, input_case_count: 0 }
      state.clusterReads = 0
      return route.fulfill({ status: 202, json: state.clusterJob })
    }
    if (path === base+'/workbench') {
      if (!options.holdJob && state.clusterJob && ['PENDING', 'RUNNING'].includes(state.clusterJob.status) && ++state.clusterReads >= 2) finish(state.clusterJob)
      return route.fulfill({ json: { report: currentReport, version, current: cluster(), revision: state.revision, ready: true, can_edit: !options.readOnly, stale: false, task: null, ai: capability(state.clusterJob), record_count: sourceRecords.length, raw_line_count: sourceRecords.length * 2, source_sha256: 'a'.repeat(64), case_count: 0, reusable_count: 0, removed_count: 0, corporate_ai_connected: options.configured !== false, summary: { confirmed: state.confirmed, total: state.revision ? 1 : 0, reviewed: Number(!!state.current), pending: Number(!state.current), complete: !!state.current, rationale_counts: cluster()?.rationale_counts || { MISSING: 0, DRAFT: 0, CONFIRMED: 0, STALE: 0 }, counts: { PASS: Number(state.current?.conclusion === 'PASS'), FAIL: Number(state.current?.conclusion === 'FAIL'), DEFER: 0, STALE: 0 } } } })
    }
    if (path === base+'/clustering/groups') return route.fulfill({ json: { items: [group()], total: 1, offset: 0, limit: 25 } })
    if (path === base+'/clustering/records') {
      const params = new URL(req.url()).searchParams
      const offset = Number(params.get('offset') || 0); const limit = Number(params.get('limit') || 50); const search = params.get('search') || ''
      const filtered = sourceRecords.filter(record => `${record.raw} ${JSON.stringify(record.fields)}`.includes(search))
      state.recordRequests.push({ offset, limit, search, group: params.get('group_id'), revision: params.get('expected_revision') })
      const pause = state.recordPause
      if (pause?.offset === offset) await pause.promise
      if (state.recordFailure?.offset === offset && state.recordFailure.remaining > 0) {
        state.recordFailure.remaining--
        return route.fulfill({ status: 503, json: { code: 'DATABASE_UNAVAILABLE' } })
      }
      return route.fulfill({ json: { items: filtered.slice(offset, offset + limit), total: filtered.length, offset, limit, cluster_revision: state.revision, group: group() } })
    }
    if (path === base+'/groups/g1/rationale') return route.fulfill({ json: { group_id: 'g1', cluster_revision: state.revision, event_id: cluster()!.id, status: group().rationale.status, rationale: { content: { reason: group().rationale.reason, evidence_ids: ['log-1'], reference_groups: [] }, source: 'AI', status: group().rationale.status, binding: {}, reviewed_by: null, reviewed_at: null, change_reason: '', previous_event_id: null }, samples: [sourceRecords[0]], references: [], history: emptyPage } })
    if (path === base+'/clustering' && body) {
      expect(body.action).toBe('CONFIRM'); expect(body.expected_revision).toBe(state.revision)
      state.confirmed = true; state.revision++
      return route.fulfill({ json: { event: cluster() } })
    }
    if (path === base+'/groups/g1/review/ai' && body) {
      expect(body.expected_cluster_revision).toBe(state.revision)
      expect(body.expected_revision).toBe(state.reviewRevision)
      state.reviewJob = { id: String(body.request_id), status: 'PENDING', error: null, model: String(body.model_id || model.model), ...(body.connection_id ? { connection_id: String(body.connection_id), connection_label: connections().find(value => value.id === body.connection_id)?.label } : {}), created_at: at, expected_revision: state.reviewRevision, input_case_count: 0 }
      return route.fulfill({ status: 202, json: state.reviewJob })
    }
    if (path === base+'/groups/g1/review') {
      if (body) {
        state.reviewRevision++
        state.current = { ...body, id: `human-review-${state.reviewRevision}`, report_id: report.id, version_id: version.id, revision: state.reviewRevision, actor: user, at, source_sha256: 'a'.repeat(64), source_version: version, proposal_conclusion: state.proposal?.conclusion || null, proposal_agreed: false, group_id: 'g1', cluster_revision: state.revision } as ReviewCase
        reviewHistory.unshift(state.current)
        return route.fulfill({ json: { event: state.current } })
      }
      if (state.reviewJob && ['PENDING', 'RUNNING'].includes(state.reviewJob.status) && ++state.reviewReads >= 2) {
        state.reviewJob.status = 'COMPLETED'
        state.proposal = { id: 'proposal-1', conclusion: 'FAIL', reason: '현재 로그 두 건은 확인된 합성 점검 기준을 충족하지 않습니다.', references: [], advisor: 'OA_OPENAI_COMPATIBLE', model_called: true, at, schema_version: 2, evidence_ids: ['log-1', 'log-2'], criterion_ids: ['synthetic-check-1'], model_metadata: { ...model, model: state.reviewJob.model } }
      }
      return route.fulfill({ json: { current: state.current, current_stale: !!options.currentStale, revision: state.reviewRevision, history: reviewHistory, facts: { rule: report.rule, mode: 'test' }, ready: true, can_review: !options.readOnly, cases: options.cases || [], case_count: options.cases?.length || 0, reusable_count: 0, cases_displayed: options.cases?.length || 0, proposal: state.proposal, corporate_ai_connected: true, ai: capability(state.reviewJob) } })
    }
    state.unexpected.push(`${req.method()} ${path}`)
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } })
  })
  return state
}

test('batch progress distinguishes complete-record classification from group integration', async ({ page }) => {
  const job: CodexJob = { id: 'synthetic-batch', status: 'RUNNING', error: null, model: model.model,
    created_at: at, expected_revision: 0, input_case_count: 0,
    progress: { mode: 'BATCH', phase: 'CLASSIFY', completed_records: 1, total_records: 2, completed_calls: 1, round: 0, waiting: false } }
  const state = await mockOA(page, { initialJob: job })
  state.clusterReads = -100
  await page.goto(url)
  await expect(page.getByRole('status').filter({ hasText: '1 / 2건 완료' })).toBeVisible()
  await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /그룹별 판정/ })).toBeDisabled()
  job.progress = { ...job.progress!, phase: 'MERGE', completed_records: 2, round: 1 }
  await page.reload()
  await expect(page.getByRole('status').filter({ hasText: '통합과 전체 배정 검증이 남아 있습니다' })).toBeVisible()
  for (const [phase, message] of [
    ['TEXT', '보존 본문을 나누어 읽고'], ['TEXT_MERGE', '보존 본문을 나누어 읽고'],
    ['REVIEW', '확정 그룹의 전체 로그를 나누어 판정'], ['REVIEW_MERGE', '분할 판정을 비교하고'],
  ] as const) {
    job.progress = { ...job.progress!, phase }
    await page.reload()
    await expect(page.getByRole('status').filter({ hasText: message })).toBeVisible()
  }
  expect(state.writes).toHaveLength(0)
  expect(state.unexpected).toEqual([])
})

test('admin default model is selected and classification controls align with the action button', async ({ page }) => {
  const connections = [{ id: connectionId, label: '합성 AI 연결', model: 'synthetic-second-model', models: [model.model, 'synthetic-second-model'], enabled: true, available: true, credential_required: true, credential_ready: true }]
  const state = await mockOA(page, { connections })
  await page.goto(url)
  const modelSelect = page.getByRole('combobox', { name: '분류 AI 모델', exact: true })
  const connectionSelect = page.getByRole('combobox', { name: '분류 AI 연결', exact: true })
  const action = page.getByRole('button', { name: 'AI로 그룹 만들기', exact: true })
  await expect(modelSelect).toHaveValue('synthetic-second-model')
  await expect(modelSelect.locator('option:checked')).toHaveText('synthetic-second-model · 기본')
  await page.evaluate(() => document.fonts.ready)
  const button = (await action.boundingBox())!
  for (const control of [connectionSelect, modelSelect]) {
    const box = (await control.boundingBox())!
    expect(Math.abs(box.height - button.height)).toBeLessThan(1)
    expect(Math.abs(box.y - button.y)).toBeLessThan(1)
  }
  await page.screenshot({ path: test.info().outputPath('ai-classification-controls.png') })
  await action.click()
  await expect(page.locator('.ws-rationale-text').first()).toBeVisible()
  expect(state.writes.find(write => write.path.endsWith('/clustering/ai'))?.body).toMatchObject({ connection_id: connectionId, model_id: 'synthetic-second-model' })
})

test('connection selection is distinct from model ID and respects its personal credential readiness', async ({ page }) => {
  const connections = [
    { id: '10000000-0000-4000-8000-000000000001', label: '합성 Bearer 연결', model: model.model, models: [model.model], enabled: true, available: true, credential_required: true, credential_ready: true },
    { id: '10000000-0000-4000-8000-000000000002', label: '합성 x-client 연결', model: model.model, models: [model.model], enabled: true, available: false, credential_required: true, credential_ready: false },
  ]
  const state = await mockOA(page, { connections, models: [model.model] })
  await page.goto(url)
  const selector = page.getByRole('combobox', { name: '분류 AI 연결', exact: true })
  await selector.selectOption(connections[1].id)
  await expect(page.getByRole('button', { name: 'AI로 그룹 만들기', exact: true })).toBeDisabled()
  connections[1].available = true; connections[1].credential_ready = true
  await page.getByRole('button', { name: '최신 구성 다시 조회', exact: true }).click()
  await expect(selector).toHaveValue(connections[1].id)
  await page.getByRole('button', { name: 'AI로 그룹 만들기', exact: true }).click()
  await expect(page.locator('.ws-rationale-text').first()).toBeVisible()
  expect(state.writes.find(write => write.path.endsWith('/clustering/ai'))?.body).toMatchObject({ connection_id: connections[1].id, model_id: model.model })
  expect(state.unexpected).toEqual([])
})

test('users select the allowed model independently for classification and group judgment', async ({ page }) => {
  const state = await mockOA(page, { models: [model.model, 'synthetic-second-model'] })
  await page.goto(url)
  const classificationModel = page.getByRole('combobox', { name: '분류 AI 모델', exact: true })
  await expect(classificationModel).toHaveValue(model.model)
  await classificationModel.selectOption('synthetic-second-model')
  await page.getByRole('button', { name: 'AI로 그룹 만들기', exact: true }).click()
  await expect(page.locator('.ws-rationale-text').first()).toHaveText('같은 조건의 확인 대상입니다.')
  expect(state.writes.find(write => write.path.endsWith('/clustering/ai'))?.body.model_id).toBe('synthetic-second-model')
  expect(state.clusterJob?.model).toBe('synthetic-second-model')
  await page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
  const review = page.locator('.ws-judgment-workspace')
  const reviewModel = review.getByRole('combobox', { name: '판정 AI 모델', exact: true })
  await expect(reviewModel).toHaveValue(model.model)
  await reviewModel.selectOption('synthetic-second-model')
  await review.getByRole('button', { name: 'AI 판정 받기', exact: true }).click()
  await expect(review.locator('.ws-review-proposal')).toContainText('AI 모델 · synthetic-second-model')
  expect(state.writes.find(write => write.path.endsWith('/review/ai'))?.body.model_id).toBe('synthetic-second-model')
  expect(state.unexpected).toEqual([])
})

for (const removed of [false, true]) test(`an in-progress model is restored and locked when ${removed ? 'removed from' : 'present in'} the catalog`, async ({ page }) => {
  const job: CodexJob = { id: 'selected-job', status: 'RUNNING', error: null, model: 'synthetic-second-model',
    created_at: at, expected_revision: 0, input_case_count: 0 }
  await mockOA(page, { models: removed ? [model.model] : [model.model, job.model], initialJob: job, holdJob: true })
  await page.goto(url)
  const selector = page.getByRole('combobox', { name: '분류 AI 모델', exact: true })
  await expect(selector).toHaveValue('synthetic-second-model')
  await expect(selector).toBeDisabled()
})

test('production first classification and direct-evidence judgment retain human correction without a reuse opt-in', async ({ page }) => {
  const state = await mockOA(page)
  await page.goto(url)
  await expect(page.getByRole('heading', { name: '로그 검토', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '원본 내려받기' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '참고 엔진으로 초안 생성' })).toHaveCount(0)
  await page.getByRole('button', { name: 'AI로 그룹 만들기', exact: true }).click()
  await expect(page.getByRole('button', { name: /그룹별 판정/ })).toBeDisabled()
  await expect(page.locator('.ws-rationale-text').first()).toHaveText('같은 조건의 확인 대상입니다.')
  await page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
  const review = page.locator('.ws-judgment-workspace')
  await review.getByRole('button', { name: 'AI 판정 받기', exact: true }).click()
  await review.getByRole('textbox', { name: '판정 근거', exact: true }).fill('사람이 작성 중인 검토 근거')
  await expect(review.locator('.ws-review-proposal')).toContainText('AI 모델 · synthetic-oa-model')
  await expect(review.getByRole('textbox', { name: '판정 근거', exact: true })).toHaveValue('사람이 작성 중인 검토 근거')
  await review.getByText('이번 로그의 직접 근거 2건', { exact: true }).click()
  await expect(review.locator('.ws-review-proposal code').first()).toHaveText('log-1')
  await review.getByText('적용한 점검 기준 1건', { exact: true }).click()
  await expect(review.getByText('synthetic-check-1', { exact: true })).toBeVisible()
  await review.getByRole('button', { name: '제안 적용', exact: true }).click()
  await review.getByRole('radio', { name: 'PASS', exact: true }).check()
  await review.getByRole('textbox', { name: '판정 근거', exact: true }).fill('확인한 예외 조건에 해당하므로 사람 검토 결과 통과로 정정합니다.')
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


for (const width of [1440, 1280]) {
  test(`live workbench keeps all four functional panes and the final action visible at ${width}×${width === 1440 ? 900 : 800}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 800 })
    const state = await mockOA(page, { initialRevision: 1 })
    await page.goto(url)
    const columns = page.locator('.ws-workbench-columns.ws-studio-columns')
    await expect(columns.locator('.ws-record-original pre')).toContainText('Full source evidence for node-1')
    const panes = [columns.locator(':scope > .ws-studio-context'), columns.locator(':scope > .ws-studio-groups'), columns.locator(':scope > .ws-studio-evidence'), columns.locator(':scope > .ws-studio-decision')]
    const boxes = await Promise.all(panes.map(pane => pane.boundingBox()))
    expect(boxes.every(box => box !== null)).toBe(true)
    boxes.forEach((box, index) => {
      expect(box!.width).toBeGreaterThan(100)
      expect(box!.x).toBeGreaterThanOrEqual(0)
      expect(box!.x + box!.width).toBeLessThanOrEqual(width + 1)
      expect(Math.abs(box!.y - boxes[0]!.y)).toBeLessThanOrEqual(2)
      if (index) expect(box!.x).toBeGreaterThanOrEqual(boxes[index - 1]!.x + boxes[index - 1]!.width - 1)
    })
    await expect(panes[0]).toContainText('합성 프로젝트')
    await expect(panes[0]).toContainText('합성 블록')
    await expect(panes[2].getByRole('table', { name: '선택 그룹 로그 목록' })).toBeVisible()
    await expect(panes[2].locator('.ws-record-original pre')).toBeInViewport()
    await expect(panes[3].getByRole('heading', { name: '분류 근거', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: /그룹별 판정/ })).toBeDisabled()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`live-four-panes-${width}.png`), fullPage: true })

    await page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
    const review = page.locator('.ws-studio-decision .ws-judgment-workspace')
    const save = review.getByRole('button', { name: '판정 저장', exact: true })
    await expect(save).toBeInViewport()
    await expect(panes[2].locator('.ws-record-original pre')).toBeInViewport()
    await review.getByRole('radio', { name: 'PASS', exact: true }).check()
    await review.getByRole('textbox', { name: '판정 근거', exact: true }).fill('실제 원문과 예외 조건을 확인한 합성 검토 결과입니다.')
    await save.click()
    await expect(review.locator('.ws-judgment-summary > div').filter({ hasText: '사용자 판정' })).toContainText('PASS')
    await expect(review.locator('.ws-judgment-summary > div').filter({ hasText: '도구 판정' })).toContainText('FAIL')
    await expect(page.locator('.ws-review-complete')).toContainText('1개 그룹의 판정을 완료했습니다.')
    await expect(review.getByText('사용자 판정과 근거를 저장했습니다.', { exact: false })).toBeVisible()
    await expect(save).toBeInViewport({ ratio: 1 })
    const savedButton = await save.boundingBox(); const decisionPane = await panes[3].boundingBox()
    expect(savedButton).not.toBeNull(); expect(decisionPane).not.toBeNull()
    expect(savedButton!.y).toBeGreaterThanOrEqual(decisionPane!.y - 1)
    expect(savedButton!.y + savedButton!.height).toBeLessThanOrEqual(decisionPane!.y + decisionPane!.height + 1)
    await expect(panes[2].locator('.ws-record-original pre')).toBeInViewport()
    const firstLog = panes[2].getByRole('row', { name: 'log-1 원문 보기', exact: true })
    await expect(firstLog).toBeInViewport({ ratio: 1 })
    const logRow = await firstLog.boundingBox(); const tablePort = await panes[2].locator('.ws-paged-record-list').boundingBox(); const tableHead = await panes[2].locator('.ws-record-table thead').boundingBox()
    expect(logRow).not.toBeNull(); expect(tablePort).not.toBeNull(); expect(tableHead).not.toBeNull()
    expect(logRow!.height).toBeGreaterThanOrEqual(30)
    expect(logRow!.height).toBeLessThanOrEqual(36)
    expect(logRow!.y).toBeGreaterThanOrEqual(tablePort!.y + tableHead!.height - 1)
    expect(logRow!.y + logRow!.height).toBeLessThanOrEqual(tablePort!.y + tablePort!.height + 1)
    expect(state.current).toMatchObject({ conclusion: 'PASS', expected_revision: 0, expected_cluster_revision: 2, reusable: false })
    expect(state.writes).toHaveLength(2)
    expect(state.writes.every(write => write.marker === '1')).toBe(true)
    expect(state.unexpected).toEqual([])
    await page.screenshot({ path: testInfo.outputPath(`live-human-review-${width}.png`), fullPage: true })
  })
}

test('paged log table shows the chosen original by row or keyboard while preserving the group and selected records', async ({ page }, testInfo) => {
  test.setTimeout(60_000)
  await page.setViewportSize({ width: 1920, height: 1080 })
  const browserErrors: string[] = []
  page.on('pageerror', error => browserErrors.push(error.message))
  const state = await mockOA(page, { initialRevision: 1, recordCount: 52, omitFirstReasons: true, longFields: true })
  let releaseInitial!: () => void
  state.recordPause = { offset: 0, promise: new Promise<void>(resolve => { releaseInitial = resolve }) }
  const firstPage = page.waitForResponse(response => new URL(response.url()).pathname === base + '/clustering/records')
  await page.goto(url)
  const evidence = page.locator('.ws-studio-evidence')
  const pages = evidence.locator('.ws-record-pages')
  const original = evidence.getByRole('region', { name: '선택 로그 원문' })
  try {
    await expect.poll(() => state.recordRequests.some(request => request.offset === 0)).toBe(true)
    await expect(pages).toHaveAttribute('aria-busy', 'true')
    await expect(evidence.getByRole('status')).toHaveText('로그와 원문을 가져오는 중…')
    await expect(evidence.locator('.ws-record-loading-status svg')).toBeVisible()
    await expect(evidence.locator('.ws-record-loading-skeleton')).toHaveCount(6)
    await expect(evidence.locator('tr[data-record-id]')).toHaveCount(0)
    expect(state.writes).toEqual([])
    await page.screenshot({ path: testInfo.outputPath('initial-record-loading-1920.png'), fullPage: true })
  } finally { releaseInitial(); state.recordPause = null }
  const initialRecords = await (await firstPage).json()
  expect(initialRecords.items[0]).not.toHaveProperty('reasons')
  expect(initialRecords.items[1]).toHaveProperty('reasons', [])
  await expect(pages).toHaveAttribute('aria-busy', 'false')
  await expect(evidence.locator('tr[data-record-id]')).toHaveCount(50)

  const viewports = [
    { width: 1920, height: 1080, minimum: 12 },
    { width: 1536, height: 864, minimum: 8 },
    { width: 1440, height: 900, minimum: 8 },
    { width: 1280, height: 800, minimum: 6 },
  ]
  const densityMeasurements: object[] = []
  const measureRows = () => evidence.locator('tr[data-record-id]').evaluateAll(rows => {
    const first = rows[0]
    if (!first) return { visible: 0, total: 0, rowHeight: 0, portHeight: 0 }
    const port = first.closest('.ws-paged-record-list')!
    const header = first.closest('table')!.querySelector('thead')!.getBoundingClientRect()
    let top = Math.max(0, header.bottom); let bottom = innerHeight; let left = 0; let right = innerWidth
    for (let element = first.parentElement; element; element = element.parentElement) {
      const style = getComputedStyle(element); const rect = element.getBoundingClientRect()
      if (/auto|scroll|hidden|clip/.test(style.overflowY)) { top = Math.max(top, rect.top); bottom = Math.min(bottom, rect.bottom) }
      if (/auto|scroll|hidden|clip/.test(style.overflowX)) { left = Math.max(left, rect.left); right = Math.min(right, rect.right) }
    }
    const visible = rows.filter(row => { const rect = row.getBoundingClientRect(); return rect.top >= top - 1 && rect.bottom <= bottom + 1 && rect.left >= left - 1 && rect.right <= right + 1 }).length
    return { visible, total: rows.length, rowHeight: first.getBoundingClientRect().height, portHeight: port.getBoundingClientRect().height }
  })
  async function checkDensity(phase: string) {
    for (const viewport of viewports) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await expect(evidence.locator('tr[data-record-id]')).toHaveCount(50)
      await expect.poll(async () => (await measureRows()).visible, { message: `${phase} ${viewport.width}×${viewport.height}: at least ${viewport.minimum} complete real log rows must be readable` }).toBeGreaterThanOrEqual(viewport.minimum)
      densityMeasurements.push({ phase, ...viewport, ...await measureRows() })
      await expect(original.locator('pre')).toBeInViewport()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
      if (viewport.width >= 1536) await page.screenshot({ path: testInfo.outputPath(`log-density-${phase}-${viewport.width}.png`), fullPage: true })
    }
  }
  await checkDensity('classification')
  await page.setViewportSize({ width: 1920, height: 1080 })
  const tablePort = evidence.locator('.ws-paged-record-list')
  const tableHead = evidence.locator('.ws-record-table thead')
  const initialHeader = (await tableHead.boundingBox())!
  await tablePort.evaluate(element => { element.scrollTop = 300 })
  await expect.poll(() => tablePort.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  const stickyHeader = (await tableHead.boundingBox())!
  expect(Math.abs(stickyHeader.y - initialHeader.y)).toBeLessThanOrEqual(1)
  await expect(tableHead).toBeInViewport({ ratio: 1 })
  await tablePort.evaluate(element => { element.scrollTop = 0 })

  const compactRow = evidence.getByRole('row', { name: 'log-1 원문 보기', exact: true })
  const compactBounds = (await compactRow.boundingBox())!
  expect(compactBounds.height).toBeGreaterThanOrEqual(30)
  expect(compactBounds.height).toBeLessThanOrEqual(36)
  await expect(compactRow.locator('.ws-record-table-mode')).toHaveAttribute('title', initialRecords.items[0].fields.mode)
  await expect(original.locator('.ws-record-original-id')).toHaveText('log-1')
  await expect(original.locator('pre')).toHaveText('Synthetic log 1: verification required\nFull source evidence for node-1')
  await expect(compactRow.locator('.ws-status')).toHaveText('FAIL')
  await original.locator('.ws-record-original-meta > summary').click()
  await expect(original.locator('.ws-record-original-meta dl')).toContainText('node-1')
  await expect(original.locator('.ws-record-original-meta dl')).toContainText('mode')
  await expect(original.locator('.ws-record-original-meta dl')).toContainText(initialRecords.items[0].fields.mode)
  await original.locator('.ws-record-original-meta > summary').click()
  expect(browserErrors).toEqual([])
  await evidence.getByRole('row', { name: 'log-2 원문 보기', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(original.locator('.ws-record-original-id')).toHaveText('log-2')
  await expect(original.locator('pre')).toHaveText('Synthetic log 2: verification required\nFull source evidence for node-2')
  await evidence.getByRole('checkbox', { name: 'log-2 로그 선택', exact: true }).check()

  let releaseNextPage!: () => void
  state.recordPause = { offset: 50, promise: new Promise<void>(resolve => { releaseNextPage = resolve }) }
  state.recordFailure = { offset: 50, remaining: 1 }
  await evidence.getByRole('button', { name: '다음 그룹 원문 페이지', exact: true }).click()
  try {
    await expect.poll(() => state.recordRequests.some(request => request.offset === 50)).toBe(true)
    await expect(pages).toHaveAttribute('aria-busy', 'true')
    await expect(evidence.getByRole('status')).toHaveText('로그와 원문을 가져오는 중…')
    await expect(evidence.locator('.ws-record-loading-status svg')).toBeVisible()
    await expect(evidence.locator('.ws-record-table-pending svg')).toBeVisible()
    const previousRow = evidence.locator('tr[data-record-id="log-1"]')
    await expect(previousRow).toHaveAttribute('aria-disabled', 'true')
    await expect(previousRow).toHaveAttribute('tabindex', '-1')
    await expect(previousRow.locator('[role="checkbox"]')).toBeDisabled()
    await expect(previousRow.locator('.ws-log-grip')).toBeDisabled()
    await expect(evidence.locator('[aria-label="log-2 로그 선택"]')).toBeChecked()
    await expect(evidence.getByRole('button', { name: '다음 그룹 원문 페이지', exact: true })).toBeDisabled()
    await expect(original.getByRole('button', { name: '선택 로그 원문 복사', exact: true })).toBeDisabled()
    await previousRow.dispatchEvent('click')
    await previousRow.dispatchEvent('keydown', { key: 'Enter', code: 'Enter' })
    await expect(original.locator('.ws-record-original-id')).toHaveText('log-2')
    expect(state.writes).toEqual([])
    await page.screenshot({ path: testInfo.outputPath('next-record-page-loading-1920.png'), fullPage: true })
  } finally { releaseNextPage(); state.recordPause = null }
  await expect(evidence.getByRole('alert')).toContainText('로그와 원문을 가져오지 못했습니다.')
  await expect(pages).toHaveAttribute('aria-busy', 'false')
  await expect(evidence.locator('tr[data-record-id]')).toHaveCount(0)
  await expect(original.locator('pre')).toHaveCount(0)
  await expect(evidence.locator('[aria-label="현재 페이지 로그 50건 이하 선택"]')).toBeDisabled()
  await expect(evidence.getByRole('button', { name: '다음 그룹 원문 페이지', exact: true })).toBeDisabled()
  await expect(original.getByRole('button', { name: '선택 로그 원문 복사', exact: true })).toBeDisabled()
  expect(state.writes).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('failed-record-page-hides-stale-results-1920.png'), fullPage: true })
  let releaseRetry!: () => void
  state.recordPause = { offset: 50, promise: new Promise<void>(resolve => { releaseRetry = resolve }) }
  await evidence.getByRole('button', { name: '로그 다시 조회', exact: true }).click()
  try {
    await expect(pages).toHaveAttribute('aria-busy', 'true')
    await expect(evidence.getByRole('status')).toHaveText('로그와 원문을 가져오는 중…')
    await expect(evidence.locator('.ws-record-loading-skeleton')).toHaveCount(6)
    expect(state.writes).toEqual([])
  } finally { releaseRetry(); state.recordPause = null }
  await expect(pages).toHaveAttribute('aria-busy', 'false')
  await expect(evidence.getByRole('alert')).toHaveCount(0)
  await expect(evidence.getByRole('status')).toHaveCount(0)
  await expect(evidence.locator('tr[data-record-id]')).toHaveCount(2)
  expect(state.recordRequests.filter(request => request.offset === 50)).toHaveLength(2)
  await expect(original.locator('.ws-record-original-id')).toHaveText('log-51')
  await expect(original.locator('pre')).toContainText('Full source evidence for node-51')
  await evidence.getByRole('row', { name: 'log-52 원문 보기', exact: true }).click()
  await expect(original.locator('.ws-record-original-id')).toHaveText('log-52')
  await evidence.getByRole('button', { name: '이전 그룹 원문 페이지', exact: true }).click()
  await expect(evidence.locator('tr[data-record-id]')).toHaveCount(50)
  await expect(evidence.getByRole('checkbox', { name: 'log-2 로그 선택', exact: true })).toBeChecked()
  await evidence.getByLabel('그룹 원문 검색', { exact: true }).fill('node-52')
  await expect(evidence.locator('tr[data-record-id]')).toHaveCount(1)
  await expect(original.locator('.ws-record-original-id')).toHaveText('log-52')
  await expect(original.locator('pre')).toContainText('Full source evidence for node-52')
  await evidence.getByLabel('그룹 원문 검색', { exact: true }).fill('absent-synthetic-pattern')
  await expect(evidence.locator('tr[data-record-id]')).toHaveCount(0)
  await expect(original.locator('pre')).toHaveCount(0)
  await expect(original).toContainText('목록에서 로그를 선택하면 해당 원문을 확인할 수 있습니다.')
  await evidence.getByLabel('그룹 원문 검색', { exact: true }).fill('')
  await expect(evidence.locator('tr[data-record-id]')).toHaveCount(50)
  await expect(evidence.getByRole('checkbox', { name: 'log-2 로그 선택', exact: true })).toBeChecked()
  expect(state.recordRequests).toEqual(expect.arrayContaining([
    { offset: 50, limit: 50, search: '', group: 'g1', revision: '1' },
    { offset: 0, limit: 50, search: 'node-52', group: 'g1', revision: '1' },
    { offset: 0, limit: 50, search: 'absent-synthetic-pattern', group: 'g1', revision: '1' },
  ]))
  expect(state.recordRequests.every(request => request.group === 'g1' && request.revision === '1' && request.limit === 50)).toBe(true)
  expect(browserErrors).toEqual([])
  expect(state.writes).toEqual([])
  expect(state.unexpected).toEqual([])
  await page.getByRole('button', { name: '로그 선택 해제', exact: true }).click()
  await page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
  await expect(evidence.locator('tr[data-record-id]')).toHaveCount(50)
  await expect(page.locator('.ws-judgment-workspace')).toBeVisible()
  await checkDensity('confirmed')
  await expect(page.locator('.ws-judgment-workspace').getByRole('button', { name: '판정 저장', exact: true })).toBeInViewport({ ratio: 1 })
  expect(state.recordRequests.every(request => request.group === 'g1' && ['1', '2'].includes(request.revision || '') && request.limit === 50)).toBe(true)
  expect(state.writes).toHaveLength(1)
  expect(state.writes[0]).toMatchObject({ path: base + '/clustering', marker: '1', body: { action: 'CONFIRM', expected_revision: 1 } })
  expect(browserErrors).toEqual([])
  expect(state.unexpected).toEqual([])
  const measurementsPath = testInfo.outputPath('log-viewport-density.json')
  await writeFile(measurementsPath, JSON.stringify(densityMeasurements, null, 2))
  await testInfo.attach('log-viewport-density', { path: measurementsPath, contentType: 'application/json' })
})

test('mobile keeps original tool failure separate from a saved human pass and exposes each review action', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const state = await mockOA(page, { initialRevision: 1 })
  await page.goto(url)
  await expect(page.getByRole('region', { name: '선택 로그 원문' })).toContainText('Full source evidence for node-1')
  const mobileRow = (await page.getByRole('row', { name: 'log-1 원문 보기', exact: true }).boundingBox())!
  expect(mobileRow.height).toBeGreaterThanOrEqual(38)
  expect(mobileRow.height).toBeLessThanOrEqual(42)
  await page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
  const review = page.locator('.ws-judgment-workspace')
  const tool = review.locator('.ws-judgment-summary > div').filter({ hasText: '도구 판정' })
  await tool.scrollIntoViewIfNeeded()
  await expect(tool.getByText('FAIL', { exact: true })).toBeInViewport()
  await review.getByRole('radio', { name: 'PASS', exact: true }).check()
  await review.getByRole('textbox', { name: '판정 근거', exact: true }).fill('모바일에서 원본과 예외 조건을 확인하여 사람 판정을 통과로 저장합니다.')
  const save = review.getByRole('button', { name: '판정 저장', exact: true })
  await save.scrollIntoViewIfNeeded()
  await expect(save).toBeInViewport()
  await save.click()
  await expect(review.locator('.ws-judgment-summary > div').filter({ hasText: '사용자 판정' })).toContainText('PASS')
  await tool.scrollIntoViewIfNeeded()
  await expect(tool.getByText('FAIL', { exact: true })).toBeInViewport()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  expect(state.current).toMatchObject({ conclusion: 'PASS', expected_cluster_revision: 2, reusable: false })
  expect(state.writes).toHaveLength(2)
  expect(state.writes.every(write => write.marker === '1')).toBe(true)
  expect(state.unexpected).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('live-mobile-original-and-human-results.png'), fullPage: true })
})

test('TOP can inspect the live layout and confirmed judgment without gaining owner mutations', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  const state = await mockOA(page, { readOnly: true, initialRevision: 1, confirmed: true })
  await page.goto(url)
  await expect(page.locator('.ws-record-original pre')).toContainText('Full source evidence for node-1')
  await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'AI로 그룹 만들기', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '근거 수정', exact: true })).toHaveCount(0)
  await expect(page.getByRole('checkbox', { name: 'log-1 로그 선택', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '판정 보기', exact: true }).click()
  const review = page.locator('.ws-judgment-workspace')
  await expect(review.getByText('조회 전용입니다.', { exact: false })).toBeVisible()
  await expect(review.getByRole('button', { name: 'AI 판정 받기', exact: true })).toBeDisabled()
  await expect(review.getByRole('button', { name: '판정 저장', exact: true })).toHaveCount(0)
  await expect(review.getByRole('textbox', { name: '판정 근거', exact: true })).toHaveCount(0)
  await expect(review.locator('.ws-judgment-summary > div').filter({ hasText: '도구 판정' })).toContainText('FAIL')
  expect(state.writes).toEqual([])
  expect(state.unexpected).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('live-top-read-only.png'), fullPage: true })
})

const exceptionFields = { rule: 'SYNTHETIC_RULE', reason: '승인된 합성 구조의 특성을 확인했습니다.', conditions: '같은 블록에서 합성 모드와 검증된 구조가 모두 유지될 때만 참고합니다.' }
const savedReview = (overrides: Partial<ReviewCase> = {}): ReviewCase => ({ id: 'saved-review-1', report_id: report.id, version_id: version.id, conclusion: 'PASS', reason: '확정한 합성 검토 결과입니다.', criteria: '', conditions: {}, reusable: false, compatible_versions: false, exception: null, revision: 1, actor: user, at, source_sha256: 'a'.repeat(64), source_version: version, proposal_id: null, proposal_conclusion: null, proposal_agreed: null, group_id: 'g1', cluster_revision: 1, ...overrides })

async function openConfirmedReview(page: Page, options: MockOptions = {}) {
  const state = await mockOA(page, { initialRevision: 1, confirmed: true, ...options })
  await page.goto(url)
  await page.getByRole('button', { name: /그룹별 판정/ }).click()
  await expect(page.locator('.ws-judgment-workspace')).toBeVisible()
  return state
}

test('human exceptions require explicit complete bounds, persist and restore without enabling automatic rules', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  const state = await openConfirmedReview(page)
  const review = page.locator('.ws-judgment-workspace')
  const exception = review.getByRole('checkbox', { name: '이번 판정을 규칙 예외로 기록', exact: true })
  await expect(review.getByRole('checkbox', { name: '같은 블록의 호환되는 다른 버전에서도 AI 참고 허용', exact: true })).toHaveCount(0)
  const save = review.getByRole('button', { name: '판정 저장', exact: true })
  await expect(review.getByText('참고 엔진의 규칙 적용 설정 (선택)', { exact: true })).toHaveCount(0)
  await expect(review.getByRole('checkbox', { name: '동일 조건의 참고 엔진에 판정 규칙 적용 허용', exact: true })).toHaveCount(0)
  await expect(exception).not.toBeChecked()
  await review.getByRole('radio', { name: 'PASS', exact: true }).check()
  await review.getByRole('textbox', { name: '판정 근거', exact: true }).fill('도구 FAIL과 독립적으로 원문을 검토한 사람의 결론입니다.')
  await save.click()
  await expect(review.getByText('사용자 판정과 근거를 저장했습니다.', { exact: false })).toBeVisible()
  expect(state.current).toMatchObject({ conclusion: 'PASS', reusable: false, criteria: '', conditions: {}, compatible_versions: false, exception: null })
  await expect(exception).not.toBeChecked()
  await exception.check()
  await expect(review.getByRole('textbox', { name: '예외 대상 규칙', exact: true })).toHaveValue(report.rule)
  await expect(save).toBeDisabled()
  await review.getByRole('textbox', { name: '예외 사유', exact: true }).fill('   ')
  await review.getByRole('textbox', { name: '예외가 성립하는 조건', exact: true }).fill('   ')
  await expect(save).toBeDisabled()
  expect(state.writes).toHaveLength(1)
  await review.getByRole('textbox', { name: '예외 대상 규칙', exact: true }).fill(`  ${exceptionFields.rule}  `)
  await review.getByRole('textbox', { name: '예외 사유', exact: true }).fill(`  ${exceptionFields.reason}  `)
  await review.getByRole('textbox', { name: '예외가 성립하는 조건', exact: true }).fill(`  ${exceptionFields.conditions}  `)
  await expect(save).toBeEnabled()
  await save.click()
  await expect(review.locator('.ws-review-current-exception')).toContainText(exceptionFields.conditions)
  expect(state.current).toMatchObject({ conclusion: 'PASS', reusable: false, criteria: '', conditions: {}, compatible_versions: false, exception: exceptionFields })
  await expect(review.locator('.ws-judgment-summary > div').filter({ hasText: '도구 판정' })).toContainText('FAIL')
  await expect(save).toBeInViewport({ ratio: 1 })
  await page.reload()
  await page.getByRole('button', { name: /그룹별 판정/ }).click()
  await expect(exception).toBeChecked()
  await expect(review.getByRole('textbox', { name: '예외 사유', exact: true })).toHaveValue(exceptionFields.reason)
  await review.locator('.ws-review-records > summary').click()
  await expect(review.getByRole('region', { name: '사용자 판정 이력', exact: true }).locator('.ws-review-exception-evidence')).toContainText(exceptionFields.conditions)
  expect(state.writes).toHaveLength(2)
  expect(state.writes.every(write => write.marker === '1')).toBe(true)
  expect(state.unexpected).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('confirmed-human-exception-fullhd.png'), fullPage: true })
})

test('a deferred review clears exception intent and legacy compatibility while preserving exception history', async ({ page }) => {
  const state = await openConfirmedReview(page, { currentCase: savedReview({ exception: exceptionFields, compatible_versions: true }) })
  const review = page.locator('.ws-judgment-workspace')
  const exception = review.getByRole('checkbox', { name: '이번 판정을 규칙 예외로 기록', exact: true })
  await expect(exception).toBeChecked()
  await review.getByRole('radio', { name: '보류', exact: true }).check()
  await expect(exception).toBeDisabled()
  await expect(exception).not.toBeChecked()
  await expect(review.getByRole('textbox', { name: '예외 사유', exact: true })).toHaveCount(0)
  await review.getByRole('textbox', { name: '판정 근거', exact: true }).fill('필수 확인 자료가 부족하여 보류로 정정합니다.')
  await review.getByRole('button', { name: '판정 저장', exact: true }).click()
  await expect(review.getByText('사용자 판정과 근거를 저장했습니다.', { exact: false })).toBeVisible()
  expect(state.current).toMatchObject({ conclusion: 'DEFER', exception: null, compatible_versions: false, reusable: false })
  await expect(review.locator('.ws-review-current-exception')).toHaveCount(0)
  await review.locator('.ws-review-records > summary').click()
  await expect(review.getByRole('region', { name: '사용자 판정 이력', exact: true }).locator('.ws-review-exception-evidence')).toContainText(exceptionFields.conditions)
  expect(state.unexpected).toEqual([])
})

for (const change of ['ordinary', 'ordinary-compatible-true', 'exception', 'defer', 'stale'] as const) {
  const ordinaryEdit = change === 'ordinary' || change === 'ordinary-compatible-true'
  test(`legacy exact-condition approval is ${ordinaryEdit ? `preserved for ${change}` : `withdrawn on ${change}`} without relabeling history`, async ({ page }) => {
    const legacy = savedReview({ reusable: true, compatible_versions: change !== 'ordinary', criteria: '이전 합성 조건 승인 기준', conditions: { rule: report.rule, mode: 'test' } })
    const state = await openConfirmedReview(page, { currentCase: legacy, currentStale: change === 'stale' })
    const review = page.locator('.ws-judgment-workspace')
    await expect(review.getByRole('checkbox', { name: '이번 판정을 규칙 예외로 기록', exact: true })).not.toBeChecked()
    if (change === 'exception') {
      await review.getByRole('checkbox', { name: '이번 판정을 규칙 예외로 기록', exact: true }).check()
      await review.getByRole('textbox', { name: '예외 사유', exact: true }).fill(exceptionFields.reason)
      await review.getByRole('textbox', { name: '예외가 성립하는 조건', exact: true }).fill(exceptionFields.conditions)
    }
    if (change === 'defer') await review.getByRole('radio', { name: '보류', exact: true }).check()
    await expect(review.getByRole('checkbox', { name: '같은 블록의 호환되는 다른 버전에서도 AI 참고 허용', exact: true })).toHaveCount(0)
    await review.getByRole('textbox', { name: '판정 근거', exact: true }).fill('기존 이력을 보존하면서 현재 판정의 근거를 정정합니다.')
    await review.getByRole('button', { name: '판정 저장', exact: true }).click()
    await expect(review.getByText('사용자 판정과 근거를 저장했습니다.', { exact: false })).toBeVisible()
    expect(state.current).toMatchObject(ordinaryEdit ? { reusable: true, criteria: legacy.criteria, conditions: legacy.conditions, exception: null } : { reusable: false, criteria: '', conditions: {}, exception: change === 'exception' ? exceptionFields : null })
    expect(state.current?.compatible_versions).toBe(ordinaryEdit && legacy.compatible_versions)
    expect(state.writes).toHaveLength(1)
    expect(state.writes[0]).toMatchObject({ path: base + '/groups/g1/review', marker: '1', body: { reusable: ordinaryEdit, compatible_versions: ordinaryEdit && legacy.compatible_versions } })
    const stored = await page.evaluate(path => fetch(path).then(response => response.json()), base + '/groups/g1/review')
    expect(stored.history[1]).toEqual(legacy)
    await review.locator('.ws-review-records > summary').click()
    const history = review.getByRole('region', { name: '사용자 판정 이력', exact: true })
    await expect(history).toContainText(`이전 참고 엔진의 동일 조건 규칙 적용 승인: ${legacy.criteria}`)
    await expect(history.locator('.ws-review-exception-evidence')).toHaveCount(Number(change === 'exception'))
    expect(state.unexpected).toEqual([])
  })
}

test('AI cited and candidate cases show cross-project block version and ECO provenance without a compatibility opt-in', async ({ page }) => {
  const reference = savedReview({ id: 'reference-exception', report_id: 'previous-report', version_id: 'previous-version', evidence_mode: 'SHARED_CONFIRMED_KNOWLEDGE', example_complete: false, exception: exceptionFields, compatible_versions: false, source_version: { ...version, id: 'previous-version', project_id: 'previous-project', block_id: 'previous-block', net_ver: 'NET-0', revision: 'REV-0', layout: 'PRE', ecos: ['ECO-1'] }, scope: { project_id: 'previous-project', block_id: 'previous-block', eco: 'ECO-1' } })
  await openConfirmedReview(page, { cases: [reference], proposal: { id: 'proposal-with-exception-reference', conclusion: 'FAIL', reason: '이전 예외 조건은 이번 로그에서 확인되지 않았습니다.', references: [reference], advisor: 'OA_OPENAI_COMPATIBLE', model_called: true, at } })
  const review = page.locator('.ws-judgment-workspace')
  await review.getByText('AI가 인용한 공유 확정 사례 1건', { exact: true }).click()
  const cited = review.locator('.ws-review-proposal .ws-case-evidence')
  await expect(cited.locator('.ws-review-exception-evidence')).toContainText(exceptionFields.reason)
  await expect(cited).toContainText('프로젝트 previous-project · 블록 previous-block')
  await expect(cited).toContainText('버전 NET-0 · revision REV-0 · PRE · ECO ECO-1')
  await expect(cited).toContainText('이번 보고서와 다른 조건: 프로젝트, 블록, 버전, revision, layout, ECO')
  await expect(cited).not.toContainText('호환 버전 포함')
  await expect(cited).not.toContainText('원본 버전 한정')
  await expect(cited).toContainText('공유 확정 사례 · 이번 로그와 조건을 확인해 다시 판단')
  await review.locator('.ws-review-records > summary').click()
  await review.getByText('이번 조회의 관련 공유 확정 사례 후보 1건', { exact: true }).click()
  const candidate = review.locator('.ws-review-cases .ws-case-evidence')
  await expect(candidate.locator('.ws-review-exception-evidence')).toContainText(exceptionFields.conditions)
  await expect(candidate).toContainText('프로젝트 previous-project · 블록 previous-block')
  await expect(candidate).toContainText('버전 NET-0 · revision REV-0 · PRE · ECO ECO-1')
  await expect(candidate).toContainText('이번 보고서와 다른 조건: 프로젝트, 블록, 버전, revision, layout, ECO')
  await expect(candidate).not.toContainText('호환 버전 포함')
  await expect(candidate).not.toContainText('원본 버전 한정')
  await expect(candidate).toContainText('공유 확정 사례 · 이번 로그와 조건을 확인해 다시 판단')
  await expect(review.locator('.ws-review-cases')).toContainText('과거 확정 근거를 참고하며 원문은 기존 조회 권한을 따릅니다.')
  await expect(review.getByRole('checkbox', { name: '같은 블록의 호환되는 다른 버전에서도 AI 참고 허용', exact: true })).toHaveCount(0)
  await expect(review.getByRole('checkbox', { name: '이번 판정을 규칙 예외로 기록', exact: true })).not.toBeChecked()
})

test('memory review adapter validates and persists explicit exceptions with legacy compatibility metadata revisions and idempotency', async () => {
  const request = makeWorkspaceDemo()
  const reviewPath = '/blocks/demo-atlas-cpu/versions/demo-atlas-cpu-net2-post/reports/demo-atlas-cpu-net2-post-gca-001/review'
  const command = { conclusion: 'PASS', reason: '합성 원본과 예외 조건을 확인했습니다.', criteria: '', conditions: {}, reusable: false, compatible_versions: true, expected_revision: 0, request_id: 'explicit-exception' }
  for (const exception of [false, [], {}, { ...exceptionFields, reason: ' ' }, { ...exceptionFields, conditions: '' }, { ...exceptionFields, rule: 'x'.repeat(501) }, { ...exceptionFields, reason: 'x'.repeat(2001) }, { ...exceptionFields, conditions: 'x'.repeat(2001) }, { ...exceptionFields, ignored: true }]) {
    await expect(request(reviewPath, { body: { ...command, exception } })).rejects.toMatchObject({ code: 'INVALID_EXCEPTION' })
  }
  await expect(request(reviewPath, { body: { ...command, conclusion: 'DEFER', exception: exceptionFields } })).rejects.toMatchObject({ code: 'INVALID_EXCEPTION' })
  await expect(request(reviewPath, { body: { ...command, reusable: true, criteria: '이전 승인', exception: exceptionFields } })).rejects.toMatchObject({ code: 'INVALID_EXCEPTION' })
  const body = { ...command, exception: { rule: ` ${exceptionFields.rule} `, reason: ` ${exceptionFields.reason} `, conditions: ` ${exceptionFields.conditions} ` } }
  const result = await request<{ event: ReviewCase }>(reviewPath, { body })
  expect(result.event).toMatchObject({ exception: exceptionFields, compatible_versions: true, reusable: false, revision: 1 })
  expect(await request(reviewPath, { body })).toEqual(result)
  await expect(request(reviewPath, { body: { ...body, reason: '같은 요청 ID의 변경된 내용' } })).rejects.toMatchObject({ code: 'REQUEST_CONFLICT' })
  await expect(request(reviewPath, { body: { ...body, request_id: 'stale-exception' } })).rejects.toMatchObject({ code: 'REVISION_CONFLICT' })
  await request(reviewPath, { body: { ...command, expected_revision: 1, request_id: 'clear-exception', exception: null } })
  const view = await request<ReportReview>(reviewPath)
  expect(view.current).toMatchObject({ exception: null, compatible_versions: true, reusable: false, revision: 2 })
  expect(view.history[1]).toMatchObject({ exception: exceptionFields, revision: 1 })
})


test('personal token registration enables AI judgment without discarding the human draft', async ({ page }) => {
  const state = await mockOA(page, { personalToken: true, initialRevision: 1, confirmed: true })
  await page.goto(url)
  await page.getByRole('button', { name: '그룹 판정 계속', exact: true }).click()
  const review = page.locator('.ws-judgment-workspace')
  await expect(review.getByRole('button', { name: 'AI 판정 받기', exact: true })).toBeDisabled()
  const reason = review.getByRole('textbox', { name: '판정 근거', exact: true })
  await reason.fill('개인 토큰 등록 중에도 보존할 사람의 검토 근거')
  await page.getByRole('button', { name: '개인 AI 토큰', exact: true }).click()
  await page.getByLabel('합성 AI 연결 개인 Bearer 토큰', { exact: true }).fill('synthetic-personal-token')
  await page.getByRole('button', { name: '인증값 저장', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('개인 인증값을 저장했습니다')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(review.getByRole('button', { name: 'AI 판정 받기', exact: true })).toBeEnabled()
  await expect(reason).toHaveValue('개인 토큰 등록 중에도 보존할 사람의 검토 근거')
  await review.getByRole('button', { name: 'AI 판정 받기', exact: true }).click()
  await expect(review.locator('.ws-review-proposal')).toContainText('AI 모델 · synthetic-oa-model')
  await expect(reason).toHaveValue('개인 토큰 등록 중에도 보존할 사람의 검토 근거')
  const request = state.writes.find(write => write.path.endsWith('/review/ai'))!
  expect(request.body).not.toHaveProperty('token')
  expect(JSON.stringify(request.body)).not.toContain('synthetic-personal-token')
  expect(state.unexpected).toEqual([])
})


test('collection-only shows unperformed tool verdicts while keeping human group review available', async ({ page }) => {
  const state = await mockOA(page, { collectionOnly: true })
  await page.goto(url)
  await page.getByRole('button', { name: 'AI로 그룹 만들기', exact: true }).click()
  await expect(page.locator('.ws-context-file')).toContainText('판정 미실행')
  await expect(page.locator('.ws-record-table-result').filter({ hasText: '판정 미실행' })).toHaveCount(2)
  await expect(page.locator('.ws-selected-group-header')).toContainText('판정 미실행')
  await page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
  const review = page.locator('.ws-judgment-workspace')
  await expect(review.locator('.ws-judgment-summary')).toContainText('판정 미실행')
  await review.getByRole('radio', { name: 'PASS', exact: true }).check()
  await review.getByRole('textbox', { name: '판정 근거', exact: true }).fill('사람이 수집된 원문과 점검 기준을 확인했습니다.')
  await review.getByRole('button', { name: '판정 저장', exact: true }).click()
  await expect(review.locator('.ws-judgment-summary')).toContainText('PASS')
  await expect(review.locator('.ws-judgment-summary')).toContainText('판정 미실행')
  expect(state.current?.conclusion).toBe('PASS')
  expect(state.unexpected).toEqual([])
})

const preserved: PreservedSource[] = [
  { id: 'pf-synthetic-full', path: 'reports/companion/full.rpt', role: 'canonical_companion',
    binding: { kind: 'execution', anchor_unit_id: 'u-gca' }, size_bytes: 14000, sha256: 'b'.repeat(64),
    unit_key: 'su-synthetic', execution_id: 'sx-synthetic', storage_status: 'PRESERVED', content_available_to_ai: false },
  { id: 'pf-synthetic-sdc', path: 'reports/companion/input.sdc', role: 'constraint_input',
    binding: { kind: 'unit', review_unit_id: 'u-gca' }, size_bytes: 200, sha256: 'c'.repeat(64),
    unit_key: 'su-synthetic', execution_id: 'sx-synthetic', storage_status: 'PRESERVED', content_available_to_ai: false },
]

test('preserved source metadata stays visible without claiming AI body access', async ({ page }) => {
  const state = await mockOA(page, { preserved })
  await page.goto(url)
  await page.getByText('보존 근거 파일 2개', { exact: true }).click()
  const files = page.getByLabel('보존 근거 파일', { exact: true })
  await expect(files).toContainText('파일 본문은 아직 전달되지 않습니다.')
  await expect(files).toContainText('reports/companion/full.rpt')
  await expect(files).toContainText('분석 입력 제약')
  await files.getByText('SHA256 확인', { exact: true }).first().click()
  await expect(files.getByText('b'.repeat(64), { exact: true })).toBeVisible()
  await expect(files.getByRole('link')).toHaveCount(0)
  await page.screenshot({ path: test.info().outputPath('preserved-workbench.png'), fullPage: true })
  expect(state.unexpected).toEqual([])
})

test('OWNER confirms preserved roles and bindings with the exact submission digest', async ({ page }) => {
  const state = await mockOA(page)
  const entry = { id: 'synthetic-submission', revision: 0, scope: { project_key: 'p1', block_key: 'b1', net_ver: 'NET-1', submission_revision: 'REV-1', layout: 'POST', target_eco: 'ECO-2', pre_revision: null, profile_id: 'synthetic', profile_version: '1' }, reported_by: 'synthetic-owner', delivery_state: 'AWAITING_CONFIRMATION', created_at: at, blockers: [], decision: null }
  const detail = { ...entry, can_confirm: true, content_sha256: 'd'.repeat(64), scope_sha256: 'e'.repeat(64), manifest_sha256: 'f'.repeat(64), total_units: 1, offset: 0, limit: 50, units: [{ review_unit_id: 'u-gca', item_id: 'synthetic_check', report: 'reports/primary.csv', applicable: true, condition: {}, execution_status: 'COMPLETED', parse_status: 'COMPLETE', record_count: 2, blockers: [], source: { layout: 'POST', revision: 'REV-1', eco: 'ECO-2', mode: 'test', tool: 'synthetic', tool_version: '1', execution_ref: 'synthetic-execution', execution_order: 1 } }], preserved_files: preserved }
  let decision: Record<string, unknown> | undefined
  await page.route('**/api/workbench/blocks/b1/intake**', async route => {
    if (route.request().method() === 'POST') {
      decision = route.request().postDataJSON()
      return route.fulfill({ json: { id: entry.id, delivery_state: 'OA_STORED' } })
    }
    return route.fulfill({ json: new URL(route.request().url()).pathname.endsWith('/intake') ? { items: [entry], total: 1, can_confirm: true } : detail })
  })
  await page.goto(url.replace('&review=report-a', ''))
  await page.locator('.submission-intake > details > summary').click()
  await page.getByRole('button', { name: '범위 확인', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByText('보존 근거 파일 2개', { exact: true }).click()
  await expect(dialog).toContainText('원천 실행')
  await expect(dialog).toContainText('reports/companion/input.sdc')
  await page.screenshot({ path: test.info().outputPath('preserved-owner.png'), fullPage: true })
  await dialog.getByRole('checkbox', { name: /보존 파일 2개의 역할·귀속/ }).check()
  await dialog.getByRole('textbox').fill('전체 범위와 보존 근거의 귀속 확인')
  await dialog.getByRole('button', { name: '범위 확정 후 접수', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(decision).toMatchObject({ decision: 'CONFIRM', confirmed_scope: true, content_sha256: detail.content_sha256, scope_sha256: detail.scope_sha256, manifest_sha256: detail.manifest_sha256 })
  expect(state.unexpected).toEqual([])
})

test('preserved text viewer shows masked complete text pages and separate AI citations', async ({ page }) => {
  const file: PreservedSource = { ...preserved[1], content_available_to_ai: true,
    binding: { kind: 'units', review_unit_ids: ['u-gca', 'u-other-mode'] },
    extraction: { line_count: 11, text_sha256: 'd'.repeat(64), text_bytes: 300, redacted: true } }
  const state = await mockOA(page, { preserved: [file], initialRevision: 1, modelMetadata: {
    preserved_citations: [{ target_id: 'g1', evidence_ids: ['pe-synthetic-10'], reason: '제공된 클록 입력 제약을 직접 대조했습니다.' }],
    preserved_citation_sources: [{ id: 'pe-synthetic-10', source_id: file.id, line_start: 11, line_end: 11, source_sha256: 'e'.repeat(64), redacted: true }],
  } })
  const reads: number[] = []
  await page.route(`**${base}/preserved/${file.id}*`, route => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset') || 0); reads.push(offset)
    const items = Array.from({ length: Math.min(10, 11 - offset) }, (_, i) => ({ id: `pe-synthetic-${offset + i}`, source_id: file.id,
      line_start: offset + i + 1, line_end: offset + i + 1, source_sha256: 'e'.repeat(64), redacted: true,
      text_sha256: 'f'.repeat(64), text: `create_clock -period ${offset + i + 1} [get_ports clk] # TOKEN=[REDACTED]\n`, byte_start: offset + i, byte_end: offset + i + 1 }))
    return route.fulfill({ json: { source: file, items, total: 11, offset, limit: 10, complete: true, masked: true } })
  })
  await page.goto(url)
  await page.getByText('보존 근거 파일 1개', { exact: true }).click()
  await expect(page.getByLabel('보존 근거 파일')).toContainText('u-gca, u-other-mode')
  await page.getByText('AI가 인용한 보존 본문 근거', { exact: true }).click()
  await expect(page.locator('.ws-preserved-citations')).toContainText('제공된 클록 입력 제약을 직접 대조했습니다.')
  await expect(page.locator('.ws-preserved-citations')).toContainText('11–11행')
  await expect(page.locator('.ws-preserved-citations')).toContainText('pe-synthetic-10')
  await page.getByRole('button', { name: '마스킹 본문 보기', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: '보존 파일 마스킹 본문' })).toBeVisible()
  await expect(dialog.locator('pre').first()).toContainText('TOKEN=[REDACTED]')
  await expect(dialog.getByRole('link', { name: '마스킹 본문 내려받기' })).toHaveAttribute('href', `${base}/preserved/${file.id}?download=true`)
  await dialog.getByRole('button', { name: '다음 근거', exact: true }).click()
  await expect(dialog.locator('pre')).toHaveCount(1)
  await expect(dialog.locator('pre')).toContainText('period 11')
  await expect(dialog.getByRole('button', { name: '다음 근거', exact: true })).toBeDisabled()
  expect(reads).toEqual([0, 10])
  await page.screenshot({ path: test.info().outputPath('preserved-text.png'), fullPage: true })
  expect(state.unexpected).toEqual([])
})
