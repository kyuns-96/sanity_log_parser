import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { makeWorkspaceDemo } from '../src/workspaceDemo'
import type { GroupBrief, GroupRecords, PageResult, PipelineTask, ReportReview, WorkbenchView, WorkspaceReport } from '../src/workspaceApi'

// The production screen uses isolated synthetic network replies. PostgreSQL
// merge behavior is independently covered by test_workspace_group_merge.py.
const report = 'demo-atlas-cpu-net2-post-gca-001'
const base = `/blocks/demo-atlas-cpu/versions/demo-atlas-cpu-net2-post/reports/${report}`
const url = `/?page=workbench&project=demo-atlas&block=demo-atlas-cpu&version=demo-atlas-cpu-net2-post&eco=chip_finish&item=gca_constraints&tab=reports&review=${report}`

async function mockMerge(page: Page, options: { confirmed?: boolean; pageSize?: number; readOnly?: boolean; differentCode?: boolean } = {}) {
  const request = makeWorkspaceDemo()
  await request('/overview')
  const task = await request<PipelineTask>(`${base}/clustering/tasks`, { body: { expected_revision: 0, request_id: 'draft' } })
  await request(`${base}/clustering/tasks/step`, { body: { task_id: task.id, expected_chunk: 0 } })
  let view = await request<WorkbenchView>(`${base}/workbench`)
  const groups = (await request<PageResult<GroupBrief>>(`${base}/clustering/groups`)).items
  if (options.confirmed) {
    await request(`${base}/clustering`, { body: { action: 'CONFIRM', expected_revision: view.revision, request_id: 'confirm' } })
    view = await request<WorkbenchView>(`${base}/workbench`)
    for (const group of groups.slice(0, 2)) await request(`${base}/groups/${group.id}/review`, { body: {
      expected_revision: 0, expected_cluster_revision: view.revision, request_id: `review-${group.id}`,
      conclusion: 'FAIL', reason: '합성 로그를 검토한 이전 판정입니다.',
    } })
  }
  if (options.readOnly) await request('/demo/account', { body: { username: 'user01' } })
  const state = { writes: [] as Record<string, unknown>[], error: '', loseMergeReply: false }
  await page.route('**/api/**', async route => {
    const req = route.request(), parsed = new URL(req.url())
    if (parsed.pathname === '/api/ai/connections') return route.fulfill({ json: { items: [] } })
    const path = parsed.pathname.replace('/api/workbench', '')
    const body = req.method() === 'POST' ? req.postDataJSON() as Record<string, unknown> : undefined
    if (body) state.writes.push(body)
    if (/\/evidence\/(CLUSTER|REVIEW)$/.test(path)) return route.fulfill({ json: {
      stage: path.split('/').at(-1), revision: 0, enabled: false, active: false,
      can_manage: !options.readOnly, can_enable: false, eligible: false, binding_sha256: 'a'.repeat(64),
    } })
    if (path.endsWith('/clustering/groups') && options.pageSize) parsed.searchParams.set('limit', String(options.pageSize))
    if (body && path.endsWith('/clustering/operations') && state.error) return route.fulfill({ status: 409, json: { code: state.error } })
    try {
      let value = await request<Record<string, unknown>>(`${path}${parsed.search}`, { body })
      if (path === '/session') value = { ...value, environment: 'PRODUCTION_SSO' }
      if (path === '/overview') value = { ...value, data_origin: 'PRODUCTION' }
      if (path.endsWith('/clustering/groups') && options.differentCode) value = { ...value,
        items: (value.items as GroupBrief[]).map(group => group.id === groups[1].id ? { ...group, example: { ...group.example, code: 'OTHER_RULE' } } : group) }
      if (body && path.endsWith('/clustering/operations') && state.loseMergeReply) {
        state.loseMergeReply = false
        return route.abort('failed')
      }
      return route.fulfill({ json: value })
    } catch (error) {
      return route.fulfill({ status: 409, json: { code: (error as { code: string }).code } })
    }
  })
  await page.goto(url)
  await expect(page.locator('.ws-workbench-detail .ws-paged-record').first()).toBeVisible({ timeout: 15000 })
  return { request, groups, state }
}

test('a split is merged back into the chosen original group with full log counts and a recorded reason', async ({ page }) => {
  const f = await mockMerge(page)
  const source = await f.request<WorkspaceReport>(base)
  const first = await f.request<GroupRecords>(`${base}/clustering/records?group_id=${f.groups[0].id}`)
  await page.getByRole('checkbox', { name: `${first.items[0].id} 로그 선택`, exact: true }).check()
  await page.getByRole('button', { name: '새 그룹으로 분리', exact: true }).click()
  await page.getByRole('textbox', { name: '분리할 새 그룹 이름' }).fill('재병합 확인 그룹')
  await page.getByRole('dialog').getByRole('button', { name: '선택 로그를 새 그룹으로 분리', exact: true }).click()
  await expect(page.locator('.ws-workbench-group-list')).toContainText('재병합 확인 그룹')
  await page.getByRole('button', { name: '그룹 병합', exact: true }).click()
  await page.getByRole('checkbox', { name: `${f.groups[0].label} 그룹 전체 선택`, exact: true }).check()
  await expect(page.getByLabel('그룹 병합 선택', { exact: true })).toContainText(`2개 그룹 · 로그 ${first.total}건 선택`)
  await page.getByRole('button', { name: '선택한 2개 그룹 병합', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText(`전체 로그 ${first.total}건`)
  await dialog.getByLabel('병합 후 유지할 그룹').selectOption(f.groups[0].id)
  await dialog.getByLabel('이번 변경 사유').fill('분리한 로그도 같은 원인으로 확인해 원래 그룹으로 병합합니다.')
  await dialog.getByRole('button', { name: '그룹 병합', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.locator('.ws-selected-group-header')).toContainText(f.groups[0].label)
  const view = await f.request<WorkbenchView>(`${base}/workbench`)
  const after = await f.request<PageResult<GroupBrief>>(`${base}/clustering/groups`)
  expect(view.summary.confirmed).toBe(false)
  expect(after.items).toHaveLength(3)
  expect(after.items.find(group => group.id === f.groups[0].id)?.record_count).toBe(first.total)
  expect((await f.request<WorkspaceReport>(base)).raw_report).toBe(source.raw_report)
  expect(f.state.writes.at(-1)).toMatchObject({ reason: '분리한 로그도 같은 원인으로 확인해 원래 그룹으로 병합합니다.',
    operation: { kind: 'MERGE', destination_id: f.groups[0].id } })
  await expect(page.getByLabel('그룹 검색', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('그룹 병합 선택', { exact: true })).toHaveCount(0)
})

test('judgment stage offers merge and returns to classification while preserving earlier judgments', async ({ page }) => {
  const f = await mockMerge(page, { confirmed: true })
  const old = await f.request<ReportReview>(`${base}/groups/${f.groups[0].id}/review`)
  await page.getByRole('button', { name: '그룹 판정 계속', exact: true }).click()
  await expect(page.getByRole('region', { name: '2단계 확정 그룹별 판정' })).toBeVisible()
  await page.getByRole('button', { name: '그룹 병합', exact: true }).click()
  await expect(page.getByRole('region', { name: '1단계 전체 로그 분류' })).toBeVisible()
  await page.getByRole('checkbox', { name: `${f.groups[1].label} 그룹 전체 선택`, exact: true }).check()
  await page.getByRole('button', { name: '선택한 2개 그룹 병합', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('병합 후 유지할 그룹').selectOption(f.groups[1].id)
  await expect(dialog).toContainText('이전 구성·근거·판정은 이력으로 보존됩니다.')
  await dialog.getByRole('button', { name: '그룹 병합', exact: true }).click()
  await expect(page.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true })).toBeDisabled()
  const view = await f.request<WorkbenchView>(`${base}/workbench`)
  expect(view.summary.confirmed).toBe(false)
  expect(view.summary.total).toBe(2)
  const historical = await f.request<ReportReview>(`${base}/groups/${f.groups[0].id}/review`)
  expect(historical.history).toEqual(old.history)
  expect(historical.current_stale).toBe(true)
})

test('group selections persist across pages and searches and the destination remains visible after merging', async ({ page }) => {
  const f = await mockMerge(page, { pageSize: 2 })
  await page.getByRole('button', { name: '그룹 병합', exact: true }).click()
  await page.getByRole('button', { name: '다음 그룹 페이지', exact: true }).click()
  await page.getByRole('checkbox', { name: `${f.groups[2].label} 그룹 전체 선택`, exact: true }).check()
  await page.getByLabel('그룹 검색', { exact: true }).fill(f.groups[1].label)
  await expect(page.getByLabel('그룹 병합 선택', { exact: true })).toContainText(f.groups[0].label)
  await expect(page.getByLabel('그룹 병합 선택', { exact: true })).toContainText(f.groups[2].label)
  await page.getByRole('button', { name: '선택한 2개 그룹 병합', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('병합 후 유지할 그룹').selectOption(f.groups[2].id)
  await dialog.getByRole('button', { name: '그룹 병합', exact: true }).click()
  await expect(page.locator('.ws-selected-group-header')).toContainText(f.groups[2].label)
  await expect(page.getByLabel('그룹 검색', { exact: true })).toHaveValue('')
  expect(f.state.writes.at(-1)).toMatchObject({ operation: {
    kind: 'MERGE', group_ids: [f.groups[0].id, f.groups[2].id], destination_id: f.groups[2].id,
  } })
})

test('a lost merge response can be retried with the same request ID without merging twice', async ({ page }) => {
  const f = await mockMerge(page)
  await page.getByRole('button', { name: '그룹 병합', exact: true }).click()
  await page.getByRole('checkbox', { name: `${f.groups[1].label} 그룹 전체 선택`, exact: true }).check()
  await page.getByRole('button', { name: '선택한 2개 그룹 병합', exact: true }).click()
  const dialog = page.getByRole('dialog')
  f.state.loseMergeReply = true
  await dialog.getByLabel('이번 변경 사유').fill('응답 유실 후에도 같은 병합 요청을 확인합니다.')
  await dialog.getByRole('button', { name: '그룹 병합', exact: true }).click()
  await expect(dialog.getByRole('alert')).toBeVisible()
  await expect(dialog.getByLabel('이번 변경 사유')).toHaveValue('응답 유실 후에도 같은 병합 요청을 확인합니다.')
  await dialog.getByRole('button', { name: '그룹 병합', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(f.state.writes).toHaveLength(2)
  expect(f.state.writes[1]).toEqual(f.state.writes[0])
  expect((await f.request<WorkbenchView>(`${base}/workbench`)).summary.total).toBe(2)
})

test('different rule codes disable merge before submission', async ({ page }) => {
  const f = await mockMerge(page, { differentCode: true })
  await page.getByRole('button', { name: '그룹 병합', exact: true }).click()
  await page.getByRole('checkbox', { name: `${f.groups[1].label} 그룹 전체 선택`, exact: true }).check()
  await expect(page.getByLabel('그룹 병합 선택', { exact: true }).getByRole('alert')).toContainText('서로 다른 rule code')
  await expect(page.getByRole('button', { name: '선택한 2개 그룹 병합', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: `${f.groups[1].label} 병합 선택 해제`, exact: true }).click()
  await expect(page.getByLabel('그룹 병합 선택', { exact: true }).getByRole('alert')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '선택한 1개 그룹 병합', exact: true })).toBeDisabled()
  expect(f.state.writes).toHaveLength(0)
})

test('read-only users cannot select or merge groups', async ({ page }) => {
  const f = await mockMerge(page, { readOnly: true })
  await expect(page.getByRole('button', { name: '그룹 병합', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '병합', exact: true })).toHaveCount(0)
  expect(f.state.writes).toHaveLength(0)
})

test('server validation preserves the selected destination and reason for correction', async ({ page }) => {
  const f = await mockMerge(page)
  await page.getByRole('button', { name: '그룹 병합', exact: true }).click()
  await page.getByRole('checkbox', { name: `${f.groups[1].label} 그룹 전체 선택`, exact: true }).check()
  await page.getByRole('button', { name: '선택한 2개 그룹 병합', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('병합 후 유지할 그룹').selectOption(f.groups[1].id)
  await dialog.getByLabel('이번 변경 사유').fill('서버의 현재 그룹 경계도 확인합니다.')
  f.state.error = 'RULE_CODE_CONFLICT'
  await dialog.getByRole('button', { name: '그룹 병합', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('서로 다른 rule code')
  await expect(dialog.getByLabel('병합 후 유지할 그룹')).toHaveValue(f.groups[1].id)
  await expect(dialog.getByLabel('이번 변경 사유')).toHaveValue('서버의 현재 그룹 경계도 확인합니다.')
  expect((await f.request<WorkbenchView>(`${base}/workbench`)).summary.total).toBe(3)
  await dialog.getByRole('button', { name: '취소', exact: true }).click()
  await expect(page.getByLabel('그룹 병합 선택', { exact: true })).toContainText('2개 그룹')
})

test('cancelling does not write a merge and historical groups remain read-only', async ({ page }) => {
  const f = await mockMerge(page)
  await page.getByRole('button', { name: '그룹 병합', exact: true }).click()
  await page.getByRole('checkbox', { name: `${f.groups[1].label} 그룹 전체 선택`, exact: true }).check()
  await page.getByRole('button', { name: '선택한 2개 그룹 병합', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '취소', exact: true }).click()
  await page.locator('.ws-workbench-support > summary').click()
  await page.locator('.ws-cluster-history > summary').click()
  await page.getByRole('button', { name: '당시 그룹 구성 조회', exact: true }).click()
  await expect(page.getByText('이전 개정의 구성을 조회 중입니다. 읽기 전용입니다.', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: '그룹 병합', exact: true })).toHaveCount(0)
  await expect(page.getByLabel('그룹 병합 선택', { exact: true })).toHaveCount(0)
  expect(f.state.writes).toHaveLength(0)
})
