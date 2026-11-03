import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import type { CodexJob, RationaleContent, ReviewCase, ReviewProposal } from '../src/workspaceApi';

// All identities, reports and model replies here are synthetic network fixtures.
const user = { id: 'synthetic-owner', name: '합성 검토자', username: 'synthetic-owner', role: 'MEMBER' }
const version = { id: 'v1', project_id: 'p1', block_id: 'b1', net_ver: 'NET-1', revision: 'REV-1', layout: 'POST' as const, source_pre_id: null, ecos: ['ECO-2'] }
const report = { id: 'report-a', item_id: 'synthetic_check', project_id: 'p1', block_id: 'b1', version_id: 'v1', eco: 'ECO-2', family: 'GCA', rule: 'SYNTHETIC_RULE', check_status: 'FAIL', parse_status: 'COMPLETE', parser: 'synthetic-v1', record_count: 2, blockers: [], analysis_ready: true, source_storage: 'PARTS_V2' }
const makeRecords = (count: number) => Array.from({ length: count }, (_, position) => position + 1).map(index => ({ id: `log-${index}`, fields: { mode: 'test', object: `node-${index}` }, check_status: 'FAIL', raw: `Synthetic log ${index}: verification required\nFull source evidence for node-${index}`, line_start: index * 2 - 1, line_end: index * 2, reasons: [], raw_display_truncated: false }))
const model = { model: 'synthetic-oa-model', prompt_version: 'test-v1', input_sha256: 'a'.repeat(64), usage: { input_tokens: 100, output_tokens: 50 }, seconds: 0.1, input_case_count: 0, synthetic_only: false }
const at = '2026-10-22T00:00:00Z'
const base = '/api/workbench/blocks/b1/versions/v1/reports/report-a'
const url = '/?page=workbench&project=p1&block=b1&version=v1&eco=ECO-2&item=synthetic_check&tab=reports&review=report-a'
const emptyPage = { items: [], total: 0, offset: 0, limit: 25 }
const connectionId = '10000000-0000-4000-8000-000000000010'

const monitoredJob = () => ({ id: '30000000-0000-4000-8000-000000000001', status: 'RUNNING', error: null,
  model: 'vwp-fast', connection_label: 'VWP 분석 모델', created_at: new Date(Date.now() - 90000).toISOString(),
  observed_at: new Date().toISOString(), expected_revision: 0, input_case_count: 0, worker_site: 'vwp',
  can_cancel: true, can_delete: true, merge_iteration: 0, current_group_count: 8, phase: 'SERVER_RESPONDED', report: { project_id: 'p1', block_id: 'b1', version_id: 'v1', report_id: 'report-a', group_id: null, item_id: 'synthetic_check', family: 'GCA' },
  progress: { mode: 'BATCH', phase: 'CLASSIFY', completed_records: 40, total_records: 100, completed_calls: 2, round: 0,
    waiting: true, round_completed: 2, round_total: 5, seconds_per_record: 1.25, average_call_seconds: 25, stage_eta_seconds: 75 } })

type MockOptions = { initialJob?: CodexJob; readOnly?: boolean; initialRevision?: number; confirmed?: boolean }

async function mockOA(page: Page, options: MockOptions = {}) {
  // Read-only is an explicit server capability fixture, separate from membership roles.
  const actor = user
  const sourceRecords = makeRecords(2)
  const currentReport = { ...report, record_count: sourceRecords.length }
  const state = { revision: options.initialRevision || 0, confirmed: options.confirmed || false, reviewRevision: 0, clusterJob: options.initialJob || null as CodexJob | null, reviewJob: null as CodexJob | null, clusterReads: 0, reviewReads: 0, current: null as ReviewCase | null, proposal: null as ReviewProposal | null, writes: [] as { path: string; body: Record<string, unknown>; marker: string | undefined }[], unexpected: [] as string[], calls: [] as string[] }
  const reviewHistory: ReviewCase[] = []
  const evidenceChoices = { CLUSTER: { enabled: false, revision: 0 }, REVIEW: { enabled: false, revision: 0 } }
  let rationaleContent: RationaleContent = { reason: '같은 조건의 확인 대상입니다.', evidence_ids: ['log-1'], reference_groups: [] }
  let rationaleReviewed = false
  let rationaleSource = 'AI'
  const connections = () => [{ id: connectionId, label: '합성 AI 연결', model: model.model, models: [model.model],
    enabled: true, available: true, credential_required: true, credential_ready: true }]
  const capability = (job: CodexJob | null) => { const choices = connections(); return { available: true,
    supported: true, enabled: true, remaining: null, provider: 'OA_OPENAI_COMPATIBLE', synthetic_only: false,
    job, connections: choices, connection_id: choices[0].id, model: choices[0].model, models: choices[0].models } }
  const cluster = () => state.revision ? { id: `cluster-${state.revision}`, report_id: report.id, revision: state.revision, action: state.confirmed ? 'CONFIRM' : 'PROPOSE', confirmed: state.confirmed, reusable: false, compatible_versions: false, criteria: '', reason: '전체 로그의 검토 의도에 따라 묶었습니다.', identity_fields: ['mode'], group_count: 1, record_count: sourceRecords.length, at, actor, references: [], advisor: 'OA_OPENAI_COMPATIBLE', model_called: true, model_metadata: { ...model, model: state.clusterJob?.model || model.model }, rationale_counts: { MISSING: 0, DRAFT: Number(!(state.confirmed || rationaleReviewed)), CONFIRMED: Number(state.confirmed || rationaleReviewed), STALE: 0 } } : null
  const group = () => ({ id: 'g1', label: '확인이 필요한 합성 대상', record_count: sourceRecords.length, check_status: 'FAIL', status: state.current?.conclusion || 'PENDING', revision: state.reviewRevision, example: {}, rationale: { status: state.confirmed || rationaleReviewed ? 'CONFIRMED' : 'DRAFT', reason: rationaleContent.reason, reason_truncated: false, reason_omitted_chars: 0, source: rationaleSource, evidence_count: 1 } })
  const finish = (job: CodexJob) => {
    job.status = 'COMPLETED'; job.error = null
    state.revision = 1
  }
  await page.route('**/api/**', async route => {
    const req = route.request(); const path = new URL(req.url()).pathname; state.calls.push(`${req.method()} ${path}`)
    const body = req.method() === 'POST' ? req.postDataJSON() as Record<string, unknown> : undefined
    if (body) {
      state.writes.push({ path, body, marker: req.headers()['x-sanity-request'] })
      if (options.readOnly) return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } })
    }
    if (path.startsWith('/workspace/api/')) { state.unexpected.push(path); return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } }) }
    if (path === '/api/ai/connections') return route.fulfill({ json: { items: connections().map(value => ({ ...value, auth_mode: 'bearer', issuance_url: null, credential: { registered: false, revision: 0 }, credential_error: null })) } })
    if (path === '/api/workbench/session') return route.fulfill({ json: { user: actor, csrf: '', expires_at: '', environment: 'PRODUCTION_SSO' } })
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
      if (state.clusterJob && ['PENDING', 'RUNNING'].includes(state.clusterJob.status) && ++state.clusterReads >= 2) finish(state.clusterJob)
      return route.fulfill({ json: { report: currentReport, version, current: cluster(), revision: state.revision, ready: true, can_edit: !options.readOnly, stale: false, task: null, ai: capability(state.clusterJob), record_count: sourceRecords.length, raw_line_count: sourceRecords.length * 2, source_sha256: 'a'.repeat(64), case_count: 0, reusable_count: 0, removed_count: 0, corporate_ai_connected: true, summary: { confirmed: state.confirmed, total: state.revision ? 1 : 0, reviewed: Number(!!state.current), pending: Number(!state.current), complete: !!state.current, rationale_counts: cluster()?.rationale_counts || { MISSING: 0, DRAFT: 0, CONFIRMED: 0, STALE: 0 }, counts: { PASS: Number(state.current?.conclusion === 'PASS'), FAIL: Number(state.current?.conclusion === 'FAIL'), DEFER: 0, STALE: 0 } } } })
    }
    if (path === base+'/clustering/groups') return route.fulfill({ json: { items: [group()], total: 1, offset: 0, limit: 25 } })
    if (path === base+'/clustering/records') {
      const params = new URL(req.url()).searchParams
      const offset = Number(params.get('offset') || 0); const limit = Number(params.get('limit') || 50); const search = params.get('search') || ''
      const filtered = sourceRecords.filter(record => `${record.raw} ${JSON.stringify(record.fields)}`.includes(search))
      return route.fulfill({ json: { items: filtered.slice(offset, offset + limit), total: filtered.length, offset, limit, cluster_revision: state.revision, group: group() } })
    }
    if (path === base+'/groups/g1/rationale') {
      if (body) {
        if (body.action === 'SAVE') { rationaleContent = body.content as RationaleContent; rationaleSource = 'HUMAN' }
        rationaleReviewed = body.action === 'ACCEPT' || body.reviewed === true
        state.revision++; state.confirmed = false
        return route.fulfill({ json: cluster() })
      }
      return route.fulfill({ json: { group_id: 'g1', cluster_revision: state.revision, event_id: cluster()!.id, status: group().rationale.status, rationale: { content: rationaleContent, source: rationaleSource, status: group().rationale.status, binding: {}, reviewed_by: null, reviewed_at: null, change_reason: '', previous_event_id: null }, samples: [sourceRecords[0]], references: [], history: emptyPage } })
    }
    if (path === base+'/groups/g1/evidence/CLUSTER' || path === base+'/groups/g1/evidence/REVIEW') {
      const stage = path.endsWith('CLUSTER') ? 'CLUSTER' : 'REVIEW'
      const choice = evidenceChoices[stage]
      if (body) {
        expect(body.expected_revision).toBe(choice.revision)
        expect(body.binding_sha256).toBe('d'.repeat(64))
        choice.enabled = body.enabled === true; choice.revision++
      }
      const eligible = stage === 'CLUSTER' ? state.confirmed : !!state.current && state.current.conclusion !== 'DEFER'
      return route.fulfill({ json: { ...choice, stage, group_id: 'g1', binding_sha256: 'd'.repeat(64), eligible,
        can_manage: !options.readOnly, can_enable: eligible, active: choice.enabled && eligible } })
    }
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
      return route.fulfill({ json: { current: state.current, current_stale: false, revision: state.reviewRevision, history: reviewHistory, facts: { rule: report.rule, mode: 'test' }, ready: true, can_review: !options.readOnly, cases: [], case_count: 0, reusable_count: 0, cases_displayed: 0, proposal: state.proposal, corporate_ai_connected: true, ai: capability(state.reviewJob) } })
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
  await expect(review.getByText('AI 참고에 사용하려면 판정 근거 AI 등록을 켜 주세요.', { exact: false })).toBeVisible()
  expect(state.current).toMatchObject({ conclusion: 'PASS', reusable: false, proposal_id: 'proposal-1' })
  expect(state.writes).toHaveLength(4)
  expect(state.writes.every(write => write.marker === '1')).toBe(true)
  expect(state.unexpected).toEqual([])
  expect(new URL(page.url()).searchParams.get('page')).toBe('workbench')
  expect(new URL(page.url()).searchParams.get('eco')).toBe('ECO-2')
  expect(state.calls.some(call => call.includes('/workspace/'))).toBe(false)
})

test('pending jobs resume with GET only and unknown outcomes never auto-submit', async ({ page }) => {
  const state = await mockOA(page, { initialJob: { id: 'existing-job', status: 'PENDING', error: null, model: model.model, created_at: at, expected_revision: 0, input_case_count: 0 } })
  await page.goto(url)
  await page.getByRole('button', { name: '상세 분류 근거 보기', exact: true }).click()
  await expect(page.locator('.ws-rationale-text').first()).toBeVisible()
  expect(state.writes).toEqual([])
  state.revision = 0
  state.clusterJob = { ...state.clusterJob!, status: 'UNKNOWN', error: 'AI_RESULT_UNKNOWN' }
  await page.reload()
  await expect(page.getByText('AI 호출 결과가 불명확합니다.', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '최신 구성 다시 조회', exact: true }).click()
  expect(state.writes).toEqual([])
})

test('read only server capabilities allow inspection without granting mutations', async ({ page }) => {
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
})

test('AI card deletion confirms active cleanup and never restores a deleted card from stale polling', async ({ page }) => {
  await mockOA(page)
  const job = monitoredJob()
  let deleted = false; let staleSent = false; let posts = 0
  let finish!: () => void
  const pending = new Promise<void>(resolve => { finish = resolve })
  await page.route('**/api/workbench/ai/jobs**', async route => {
    const request = route.request()
    if (request.method() === 'POST') {
      posts++
      expect(new URL(request.url()).pathname).toBe(`/api/workbench/ai/jobs/${job.id}/delete`)
      expect(request.headers()['x-sanity-request']).toBe('1')
      expect(request.postDataJSON()).toEqual({})
      await pending; deleted = true
      return route.fulfill({ json: { id: job.id, deleted: true } })
    }
    const stale = deleted && !staleSent
    if (stale) staleSent = true
    return route.fulfill({ json: { items: !deleted || stale ? [job] : [], server_time: new Date().toISOString(), worker: { site: 'vwp', online: true, last_seen: new Date().toISOString() } } })
  })
  await page.goto(url.replace('tab=reports', 'tab=ai-jobs').replace('&review=report-a', ''))
  const dashboard = page.getByRole('region', { name: 'AI 작업 현황', exact: true })
  const trash = dashboard.getByRole('button', { name: 'synthetic_check 작업 삭제', exact: true })
  await trash.click()
  const dialog = page.getByRole('dialog', { name: 'AI 작업 삭제' })
  await expect(dialog).toContainText('DB에서 삭제')
  await expect(dialog).toContainText('삭제 후 도착한 응답은 반영하지 않습니다')
  expect(posts).toBe(0)
  await dialog.getByRole('button', { name: '돌아가기' }).click()
  await expect(dialog).toHaveCount(0); expect(posts).toBe(0)
  await trash.click()
  await dialog.getByRole('button', { name: '작업과 기록 삭제' }).click()
  await expect(dialog.getByRole('button', { name: '삭제 중…' })).toBeDisabled()
  // The modal hides the background from role queries; the card must stay in the DOM.
  await expect(page.locator('.ai-job-card')).toHaveCount(1)
  finish()
  await expect(dialog).toHaveCount(0)
  await expect(dashboard.locator('.ai-job-card')).toHaveCount(0)
  await dashboard.getByRole('button', { name: 'AI 작업 현황 새로고침' }).click()
  await expect(dashboard.locator('.ai-job-card')).toHaveCount(0)
  await page.reload()
  await expect(dashboard).toContainText('아직 요청한 AI 작업이 없습니다')
  expect(posts).toBe(1)
})
