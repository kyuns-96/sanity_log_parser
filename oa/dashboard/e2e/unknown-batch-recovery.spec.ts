import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const actor = { id: 'synthetic-owner', name: '합성 사용자', username: 'synthetic-owner', role: 'OWNER' }
const jobId = '30000000-0000-4000-8000-000000000001'
const eventHash = 'a'.repeat(64)
const stepHash = 'b'.repeat(64)
const requestPath = `/api/workbench/ai/jobs/${jobId}/retry-unknown`

async function setup(page: Page) {
  const state = { mode: 'lost' as 'lost' | 'success' | 'forbidden' | 'rejected', calls: [] as Record<string, unknown>[], forbidden: false,
    hold: null as null | { started: () => void; release: Promise<void> },
    job: { id: jobId, status: 'UNKNOWN', error: 'AI_OUTCOME_UNKNOWN' as string | null, model: 'synthetic-model', connection_label: '합성 VWP', created_at: new Date().toISOString(),
      observed_at: new Date().toISOString(), expected_revision: 0, input_case_count: 0, can_cancel: false, can_retry_unknown: true,
      unknown_reason: 'READ_TIMEOUT', unknown_recovery: { event_sha256: eventHash, step_sha256: stepHash, call_token: 'synthetic-call', phase: 'CLASSIFY', round: 0, index: 2, total: 5, input_bytes: 1200 },
      report: { project_id: 'p1', block_id: 'b1', version_id: 'v1', report_id: 'report-a', group_id: null, item_id: 'synthetic_check', family: 'GCA' },
      progress: { mode: 'BATCH', phase: 'CLASSIFY', completed_records: 20000, total_records: 50000, completed_calls: 2, round: 0, waiting: true, round_completed: 2, round_total: 5 } } }
  await page.route('**/api/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname
    if (path === '/api/workbench/session') return route.fulfill({ json: { user: actor, csrf: '', expires_at: '', environment: 'PRODUCTION_SSO' } })
    if (path === '/api/workbench/overview') return route.fulfill({ json: { user: actor, data_origin: 'PRODUCTION', projects: [{ id: 'p1', name: '합성 프로젝트', description: '', tops: [], blocks: [] }] } })
    if (path === requestPath) {
      expect(req.method()).toBe('POST'); expect(req.headers()['x-sanity-request']).toBe('1')
      const body = req.postDataJSON() as Record<string, unknown>; state.calls.push(body)
      expect(Object.keys(body).sort()).toEqual(['acknowledge_duplicate', 'expected_event_sha256', 'expected_step_sha256', 'request_id'])
      expect(body.acknowledge_duplicate).toBe(true)
      if (state.mode === 'lost') return route.abort('failed')
      if (state.mode === 'forbidden') { state.forbidden = true; return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } }) }
      if (state.mode === 'rejected') return route.fulfill({ status: 400, json: { code: 'AI_CREDENTIAL_CHANGED' } })
      state.job = { ...state.job, status: 'RUNNING', error: null, can_retry_unknown: false }
      return route.fulfill({ json: state.job })
    }
    if (path === '/api/workbench/ai/jobs') {
      const held = state.hold
      if (held) {
        state.hold = null
        const old = structuredClone(state.job); held.started(); await held.release
        return route.fulfill({ json: { items: [old], server_time: new Date().toISOString(), worker: { site: 'vwp', online: true, last_seen: new Date().toISOString() } } }).catch(() => {})
      }
      return state.forbidden ? route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } }) : route.fulfill({ json: { items: [state.job], server_time: new Date().toISOString(), worker: { site: 'vwp', online: true, last_seen: new Date().toISOString() } } })
    }
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } })
  })
  await page.goto('/?page=workbench&project=p1&tab=ai-jobs')
  const dashboard = page.getByRole('region', { name: 'AI 작업 현황', exact: true })
  await expect(dashboard).toContainText('20,000 / 50,000건')
  return { state, dashboard }
}

test('unknown batch requires explicit confirmation, preserves progress and reuses UUID after a lost reply', async ({ page }) => {
  const { state, dashboard } = await setup(page)
  await expect(dashboard).toContainText('자동 재호출하지 않습니다')
  await expect.poll(() => state.calls.length).toBe(0)
  await dashboard.getByRole('button', { name: '결과 불명 배치 재시도' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('20,000 / 50,000건')
  await expect(dialog).toContainText('배치 3 / 5')
  await expect(dialog).toContainText('중복 계산이나 비용')
  await expect(dialog).toContainText('완료된 배치는 유지')
  await dialog.getByRole('button', { name: '취소', exact: true }).click()
  expect(state.calls).toHaveLength(0)
  await dashboard.getByRole('button', { name: '결과 불명 배치 재시도' }).click()
  await dialog.getByRole('button', { name: '확인하고 이 배치 재시도' }).click()
  await expect(dialog.getByRole('alert')).toContainText('같은 요청')
  expect(state.calls[0]).toMatchObject({ expected_event_sha256: eventHash, expected_step_sha256: stepHash, acknowledge_duplicate: true })
  state.mode = 'success'
  await dialog.getByRole('button', { name: '확인하고 이 배치 재시도' }).click()
  await expect(dashboard).toContainText('처리 중')
  await expect(dashboard).toContainText('20,000 / 50,000건')
  expect(state.calls).toHaveLength(2)
  expect(state.calls[1].request_id).toBe(state.calls[0].request_id)
})

test('parallel unknown recovery confirms the complete set with one acknowledgment', async ({ page }) => {
  const { state, dashboard } = await setup(page)
  const first = { ...state.job.unknown_recovery, index: 2 }
  const second = { ...state.job.unknown_recovery, index: 3, step_sha256: 'c'.repeat(64), input_bytes: 2400 }
  state.job = { ...state.job, unknown_recoveries: [first, second],
    progress: { ...state.job.progress, execution: 'parallel-v1', concurrency: 2, inflight_calls: 0 } } as typeof state.job
  await dashboard.getByRole('button', { name: 'AI 작업 현황 새로고침' }).click()
  await expect(dashboard).toContainText('이 작업에서 동시 처리 0 / 2개')
  await dashboard.getByRole('button', { name: '결과 불명 배치 재시도' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('배치 3 / 5')
  await expect(dialog).toContainText('배치 4 / 5')
  await expect(dialog).toContainText('2,400 bytes')
  await expect(dialog).toContainText('완료된 배치는 다시 호출하지 않고 유지')
  await dialog.getByRole('button', { name: '취소', exact: true }).click()
  expect(state.calls).toHaveLength(0)
  await dashboard.getByRole('button', { name: '결과 불명 배치 재시도' }).click()
  state.mode = 'success'
  await dialog.getByRole('button', { name: '확인하고 미확인 2개 재시도' }).click()
  await expect(dashboard).toContainText('처리 중')
  await expect(dashboard).toContainText('20,000 / 50,000건')
  expect(state.calls).toHaveLength(1)
  expect(state.calls[0]).toMatchObject({ expected_event_sha256: eventHash, expected_step_sha256: stepHash, acknowledge_duplicate: true })
})

test('changed checkpoint requires fresh confirmation and a new request ID', async ({ page }) => {
  const { state, dashboard } = await setup(page)
  await dashboard.getByRole('button', { name: '결과 불명 배치 재시도' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: '확인하고 이 배치 재시도' }).click()
  await expect(dialog.getByRole('alert')).toBeVisible()
  await dialog.getByRole('button', { name: '취소', exact: true }).click()
  state.job.unknown_recovery = { ...state.job.unknown_recovery, step_sha256: 'c'.repeat(64), index: 3 }
  await dashboard.getByRole('button', { name: 'AI 작업 현황 새로고침' }).click()
  await dashboard.getByRole('button', { name: '결과 불명 배치 재시도' }).click()
  await expect(dialog).toContainText('배치 4 / 5')
  state.mode = 'success'
  await dialog.getByRole('button', { name: '확인하고 이 배치 재시도' }).click()
  await expect(dashboard).toContainText('처리 중')
  expect(state.calls[1].request_id).not.toBe(state.calls[0].request_id)
  expect(state.calls[1].expected_step_sha256).toBe('c'.repeat(64))
})

test('permission denial clears the card and never automatically retries', async ({ page }) => {
  const { state, dashboard } = await setup(page)
  state.mode = 'forbidden'
  await dashboard.getByRole('button', { name: '결과 불명 배치 재시도' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '확인하고 이 배치 재시도' }).click()
  await expect(dashboard.getByRole('progressbar')).toHaveCount(0)
  await expect(dashboard).not.toContainText('synthetic_check')
  expect(state.calls).toHaveLength(1)
})

test('a definite credential rejection explains the error and requires a fresh request', async ({ page }) => {
  const { state, dashboard } = await setup(page)
  state.mode = 'rejected'
  await dashboard.getByRole('button', { name: '결과 불명 배치 재시도' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '확인하고 이 배치 재시도' }).click()
  await expect(dashboard.getByRole('alert')).toContainText('개인 토큰이 변경되어')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(state.calls).toHaveLength(1)
  await dashboard.getByRole('button', { name: '결과 불명 배치 재시도' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '확인하고 이 배치 재시도' }).click()
  await expect.poll(() => state.calls.length).toBe(2)
  expect(state.calls[1].request_id).not.toBe(state.calls[0].request_id)
})

test('an older delayed status response cannot restore an unknown card after confirmed retry', async ({ page }) => {
  const { state, dashboard } = await setup(page)
  let start!: () => void, release!: () => void
  const started = new Promise<void>(resolve => { start = resolve })
  const gate = new Promise<void>(resolve => { release = resolve })
  state.hold = { started: start, release: gate }
  await dashboard.getByRole('button', { name: 'AI 작업 현황 새로고침' }).click()
  await started
  state.mode = 'success'
  await dashboard.getByRole('button', { name: '결과 불명 배치 재시도' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '확인하고 이 배치 재시도' }).click()
  await expect(dashboard).toContainText('처리 중')
  release()
  await expect(dashboard.getByRole('button', { name: '결과 불명 배치 재시도' })).toHaveCount(0)
  expect(state.calls).toHaveLength(1)
})
