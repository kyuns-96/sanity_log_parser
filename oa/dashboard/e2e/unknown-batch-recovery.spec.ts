import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const actor = { id: 'synthetic-owner', name: '합성 사용자', username: 'synthetic-owner', role: 'OWNER' }
const jobId = '30000000-0000-4000-8000-000000000001'
const eventHash = 'a'.repeat(64)
const stepHash = 'b'.repeat(64)
const requestPath = `/api/workbench/ai/jobs/${jobId}/retry-unknown`

async function setup(page: Page) {
  const state = { mode: 'lost' as 'lost' | 'success', calls: [] as Record<string, unknown>[],
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
      state.job = { ...state.job, status: 'RUNNING', error: null, can_retry_unknown: false }
      return route.fulfill({ json: state.job })
    }
    if (path === '/api/workbench/ai/jobs') {
      return route.fulfill({ json: { items: [state.job], server_time: new Date().toISOString(), worker: { site: 'vwp', online: true, last_seen: new Date().toISOString() } } })
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
