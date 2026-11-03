import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'

async function login(page: Page, user = 'user04') {
  await page.goto('/workspace')
  await page.getByLabel('사용자 ID', { exact: true }).fill(user)
  await page.getByLabel('비밀번호', { exact: true }).fill('sanity-workspace')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.locator('.ws-heading h1')).toBeVisible()
  await expect(page.locator('.ws-block-table tbody tr').first()).toBeVisible()
}

test('the API rejects cross-scope reads and a TOP cannot bypass PoR permissions', async ({ page }) => {
  await login(page, 'user01')
  const session = await (await page.request.get('/workspace/api/session')).json()
  const response = await page.request.post('/workspace/api/blocks/project-1-cpu/por', {
    headers: { 'Origin': 'http://127.0.0.1:8002', 'X-CSRF-Token': session.csrf },
    data: { version_id: 'project-1-cpu-post-1', expected_revision: 0, request_id: crypto.randomUUID(), reason: '허용되지 않는 지정 요청' },
  })
  expect(response.status()).toBe(403)
  const foreign = await page.request.get('/workspace/api/blocks/project-2-cpu/versions')
  expect(foreign.status()).toBe(403)
  const noCsrf = await page.request.post('/workspace/api/logout', { headers: { Origin: 'http://127.0.0.1:8002' }, data: {} })
  expect(noCsrf.status()).toBe(403)
})

const pipelineBase = '/workspace/api/blocks/project-3-gpu/versions/project-3-gpu-post-1/reports/'
async function pipelineReport(page: Page, report: string) {
  await page.getByLabel('검사 검색').fill(report)
  await page.getByRole('button', { name: `${report} 보고서 보기`, exact: true }).click()
  await page.getByRole('tab', { name: 'Clustering·그룹 판정', exact: true }).click()
  await page.getByRole('button', { name: '그룹별 근거·순차 검토 열기', exact: true }).click()
  await expect(page.locator('.ws-pipeline-steps')).toBeVisible()
}
async function pipelinePost(page: Page, report: string, suffix: string, body: object) {
  const session = await (await page.request.get('/workspace/api/session')).json()
  const response = await page.request.post(pipelineBase+report+suffix, { data: body,
    headers: { Origin: new URL(page.url()).origin, 'X-CSRF-Token': session.csrf } })
  expect(response.ok(), await response.text()).toBe(true)
  return response.json()
}
async function pipelineOperation(page: Page, report: string, operation: object) {
  const current = await (await page.request.get(pipelineBase+report+'/clustering')).json()
  return pipelinePost(page, report, '/clustering/operations', { expected_revision: current.revision,
    request_id: crypto.randomUUID(), reason: '합성 시나리오의 명시적 그룹 조작', operation })
}
async function supplementPipelineReasons(page: Page, report: string) {
  let current = await (await page.request.get(pipelineBase+report+'/clustering')).json()
  for (const group of current.current.groups) {
    const row = current.current.rationales?.[group.id]
    if (!row || row.status === 'STALE') {
      await pipelinePost(page, report, `/groups/${group.id}/rationale`, { action: 'SAVE',
        expected_revision: current.revision, request_id: crypto.randomUUID(), reviewed: true,
        reason: '합성 검토자가 변경된 전체 그룹 구성의 근거를 보완', content: {
          reason: `${group.label}: 원문 전체를 대조한 합성 검토 기준에 따라 묶고 다른 그룹과 별도 조사합니다. 동일 원인인지는 미확인입니다.`,
          evidence_ids: group.record_ids.slice(0, 2), reference_groups: [] } })
      current = await (await page.request.get(pipelineBase+report+'/clustering')).json()
    }
  }
  await page.reload()
  await expect(page.locator('.ws-rationale[data-rationale-state]')).toBeVisible()
}
async function gpuWorkspace(page: Page, user = 'user04') {
  await login(page, user)
  const menu = page.getByRole('button', { name: '메뉴 열기', exact: true })
  if (await menu.isVisible()) await menu.click()
  await page.getByRole('button', { name: 'Cygnus 프로젝트', exact: true }).click()
  await page.getByRole('button', { name: 'GPU 버전과 검사 보기', exact: true }).click()
  await page.getByLabel('조회 버전').selectOption('project-3-gpu-post-1')
}

async function openWorkbenchSupport(work: ReturnType<Page['locator']>) {
  const details = work.locator('.ws-workbench-support')
  if (await details.getAttribute('open') === null) await details.locator(':scope > summary').click()
}
async function openWorkbenchMemo(work: ReturnType<Page['locator']>) {
  await openWorkbenchSupport(work)
  const summary = work.getByText('전체 메모와 참고 엔진 설정 (선택)', { exact: true })
  if (await summary.locator('..').getAttribute('open') === null) await summary.click()
}

async function makeWorkbenchGroups(work: ReturnType<Page['locator']>) {
  const first = work.getByRole('button', { name: '그룹 초안 만들기', exact: true })
  if (await first.isVisible()) await first.click()
  else {
    await openWorkbenchSupport(work)
    const settings = work.locator('.ws-workbench-settings')
    if (await settings.getAttribute('open') === null) await settings.locator(':scope > summary').click()
    await settings.getByRole('button', { name: '참고 엔진으로 초안 생성', exact: true }).click()
  }
}

test.describe.serial('two-stage group review with separate multi-record QA fixtures', () => {
  test('a person splits and merges all records, confirms clustering and saves an independent group PASS', async ({ page, browser }) => {
    test.setTimeout(60000)
    await gpuWorkspace(page)
    const sourceBefore = await (await page.request.get(pipelineBase + 'qa-cluster-source')).json()
    const porBefore = await page.getByRole('region', { name: '블록의 현재 PoR' }).innerText()
    await pipelineReport(page, 'qa-cluster-source')
    const pipeline = page.locator('.ws-workbench')
    await makeWorkbenchGroups(pipeline)
    await expect(pipeline.locator('.ws-notice')).toContainText('분류 초안을 저장')
    await expect(pipeline.locator('.ws-review-progress > span').first()).toContainText('3개 그룹')
    const draft = (await (await page.request.get(pipelineBase + 'qa-cluster-source/clustering')).json()).current
    const targetGroup = draft.groups.find((group: { record_ids: string[] }) => group.record_ids.includes('qa-cluster-source-e-1'))
    await pipelineOperation(page, 'qa-cluster-source', { kind: 'MOVE', record_ids: ['qa-cluster-source-e-2'], new_group_id: 'qa-human-split', label: '분리한 합성 그룹' })
    await pipelineOperation(page, 'qa-cluster-source', { kind: 'MOVE', record_ids: ['qa-cluster-source-e-1','qa-cluster-source-e-2','qa-cluster-source-e-3'], destination_id: targetGroup.id })
    await pipelineOperation(page, 'qa-cluster-source', { kind: 'RENAME', destination_id: targetGroup.id, label: '동일 원인 그룹' })
    await supplementPipelineReasons(page, 'qa-cluster-source')
    await openWorkbenchSupport(pipeline)
    await pipeline.getByText('전체 메모와 참고 엔진 설정 (선택)', { exact: true }).click()
    await pipeline.getByRole('checkbox', { name: '동일 조건의 참고 엔진에 분류 규칙 적용 허용', exact: true }).check()
    await pipeline.getByLabel('재사용할 분류 기준', { exact: true }).fill('이 객체 집합은 같은 원인의 문제입니다.')
    await pipeline.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
    await expect(pipeline.getByRole('region', { name: '2단계 확정 그룹별 판정' })).toBeVisible()
    await pipeline.locator('.ws-workbench-group-list button').filter({ hasText: '동일 원인 그룹' }).click()
    const review = pipeline.locator('.ws-judgment-workspace')
    await expect(review.locator('.ws-judgment-summary > div').first()).toContainText('FAIL')
    await review.getByRole('button', { name: '판정 제안 받기', exact: true }).click()
    await expect(review.locator('.ws-review-proposal')).toContainText('제안 보류')
    await review.getByRole('button', { name: '제안 적용', exact: true }).click()
    await review.getByRole('radio', { name: /PASS/ }).check()
    await review.getByLabel('판정 근거', { exact: true }).fill('M1 구성에서 허용한 그룹의 예외를 확인했습니다.')
    await review.getByRole('button', { name: '판정 저장', exact: true }).click()
    await expect(review.getByRole('status')).toContainText('사용자 판정과 근거를 저장했습니다.')
    const reviewPath = `/groups/${targetGroup.id}/review`
    const currentReview = await (await page.request.get(pipelineBase + 'qa-cluster-source' + reviewPath)).json()
    await pipelinePost(page, 'qa-cluster-source', reviewPath, {
      conclusion: 'PASS', reason: 'M1 구성에서 허용한 그룹의 예외를 확인했습니다.',
      criteria: 'CLK_QA와 M1에서 확인한 허용 조건입니다.',
      conditions: { rule: currentReview.facts.rule, mode: currentReview.facts.mode },
      reusable: true, compatible_versions: false, expected_revision: currentReview.revision,
      expected_cluster_revision: currentReview.current.cluster_revision, request_id: crypto.randomUUID(),
    })
    await expect(pipeline.locator('.ws-review-progress > span').nth(1)).toContainText('1 / 2')
    await page.getByRole('button', { name: '검사 대장으로 돌아가기', exact: true }).click()
    await page.getByLabel('검사 검색').fill('qa-cluster-source')
    await expect(page.locator('.ws-report-table tbody tr').first().locator('td').first()).toContainText('qa-cluster-source')
    await page.getByRole('button', { name: '상세 열 보기', exact: true }).click()
    await expect(page.locator('.ws-report-table tbody tr').first().locator('td').nth(5)).toHaveText('미검토')
    await expect(page.locator('.ws-report-table tbody tr').first().locator('td').nth(2)).toContainText('1 / 2 확정')
    expect(await (await page.request.get(pipelineBase + 'qa-cluster-source')).json()).toEqual(sourceBefore)
    expect(await page.getByRole('region', { name: '블록의 현재 PoR' }).innerText()).toBe(porBefore)
    await page.reload()
    await expect(page.getByLabel('조회 버전')).toHaveValue('project-3-gpu-post-1')
    await pipelineReport(page, 'qa-cluster-source')
    await pipeline.getByRole('button', { name: /그룹별 판정/ }).click()
    await pipeline.locator('.ws-workbench-group-list button').filter({ hasText: '동일 원인 그룹' }).click()
    await expect(pipeline.locator('.ws-judgment-summary > div').nth(1)).toContainText('PASS')
    const context = await browser.newContext()
    try {
      const top = await context.newPage()
      await gpuWorkspace(top, 'user03'); await pipelineReport(top, 'qa-cluster-source')
      const topPipeline = top.locator('.ws-workbench')
      await topPipeline.getByRole('button', { name: /그룹별 판정/ }).click()
      await topPipeline.locator('.ws-workbench-group-list button').filter({ hasText: '동일 원인 그룹' }).click()
      await expect(topPipeline.locator('.ws-judgment-summary > div').nth(1)).toContainText('PASS')
      await expect(topPipeline.getByRole('button', { name: '판정 저장', exact: true })).toHaveCount(0)
    } finally { await context.close() }
  })

  test('the next report reuses the human partition and group judgment and records a correction', async ({ page }) => {
    await gpuWorkspace(page); await pipelineReport(page, 'qa-cluster-target')
    const pipeline = page.locator('.ws-workbench')
    await makeWorkbenchGroups(pipeline)
    await expect(pipeline.locator('.ws-notice')).toContainText('분류 초안을 저장')
    await expect(pipeline.locator('.ws-review-progress > span').first()).toContainText('2개 그룹')
    await openWorkbenchSupport(pipeline)
    await pipeline.getByText('현재 분류의 기준과 출처', { exact: true }).click()
    await expect(pipeline.locator('.ws-cluster-references')).toContainText('qa-cluster-source')
    await pipeline.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
    await expect(pipeline.getByRole('region', { name: '2단계 확정 그룹별 판정' })).toBeVisible()
    const current = (await (await page.request.get(pipelineBase + 'qa-cluster-target/clustering')).json()).current
    const chosen = current.groups.find((group: { record_ids: string[] }) => group.record_ids.includes('qa-cluster-target-e-1'))
    await pipeline.locator('.ws-workbench-group-list button').filter({ hasText: chosen.label }).click()
    const review = pipeline.locator('.ws-judgment-workspace')
    await review.getByRole('button', { name: '판정 제안 받기', exact: true }).click()
    await expect(review.locator('.ws-review-proposal')).toContainText('제안 PASS')
    await review.getByText('참고한 확정 기준 1건', { exact: true }).click()
    await expect(review.locator('.ws-case-evidence').first()).toContainText('qa-cluster-source')
    await expect(review.locator('.ws-case-evidence').first()).toContainText('clustering 개정')
    await review.getByRole('button', { name: '제안 적용', exact: true }).click()
    await review.getByRole('radio', { name: /FAIL/ }).check()
    await review.getByLabel('판정 근거', { exact: true }).fill('현재 그룹에는 추가 확인이 필요한 반례가 있어 제안을 수정합니다.')
    await review.getByRole('button', { name: '판정 저장', exact: true }).click()
    await review.locator('.ws-review-records > summary').click()
    await expect(review.getByRole('region', { name: '사용자 판정 이력' })).toContainText('제안 PASS → 사용자 FAIL · 사용자 수정')
  })

  test('reclustering invalidates group judgments and dependent references while preserving history on mobile', async ({ page }) => {
    await gpuWorkspace(page); await pipelineReport(page, 'qa-cluster-source')
    const pipeline = page.locator('.ws-workbench')
    await pipelineOperation(page, 'qa-cluster-source', { kind: 'MOVE', record_ids: ['qa-cluster-source-e-3'], new_group_id: `qa-resplit-${crypto.randomUUID()}`, label: '별개 원인 분리' })
    await supplementPipelineReasons(page, 'qa-cluster-source')
    await pipeline.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
    await expect(pipeline.getByRole('region', { name: '2단계 확정 그룹별 판정' })).toBeVisible()
    await pipeline.locator('.ws-workbench-group-list button').filter({ hasText: '동일 원인 그룹' }).click()
    const review = pipeline.locator('.ws-judgment-workspace')
    await expect(review.locator('.ws-judgment-summary')).toContainText('재검토 · 이전 PASS')
    await expect(review.getByLabel('판정 근거', { exact: true })).toHaveValue('')
    await review.locator('.ws-review-records > summary').click()
    await expect(review.getByRole('region', { name: '사용자 판정 이력' })).toContainText('M1 구성에서 허용한 그룹의 예외를 확인했습니다.')
    const dependent = await (await page.request.get(pipelineBase + 'qa-cluster-target/clustering')).json()
    expect(dependent.stale).toBe(true)
    expect(dependent.summary.confirmed).toBe(false)
    expect(dependent.summary.stale).toBeGreaterThan(0)
    await page.setViewportSize({ width: 390, height: 844 })
    await review.locator('.ws-judgment-summary').scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
})

test('all records are classified, cross-page edits precede group judgment, and a TOP reads the persisted PASS', async ({ page, browser }) => {
  test.setTimeout(360000)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await gpuWorkspace(page)
  const prefix = pipelineBase + 'large-gca-10000'
  const original = await (await page.request.get(prefix)).json()
  const porBefore = (await (await page.request.get('/workspace/api/blocks/project-3-gpu/versions')).json()).por
  await page.getByRole('button', { name: 'large-gca-10000 검토 시작', exact: true }).click()
  const work = page.getByRole('region', { name: 'Sanity item 순차 검토', exact: true })
  await expect(work.locator('.ws-review-progress > span').first()).toContainText('10,000건')
  if (await work.getByRole('button', { name: '분류 작업 취소', exact: true }).isVisible()) {
    await work.getByRole('button', { name: '분류 작업 취소', exact: true }).click()
    await expect(work.getByRole('button', { name: '분류 작업 취소', exact: true })).toHaveCount(0)
  }
  await makeWorkbenchGroups(work)
  await expect(work.locator('.ws-notice')).toContainText('분류 초안을 저장', { timeout: 120000 })
  await expect(work.locator('.ws-workbench-group-list button')).toHaveCount(25)
  await expect(work.locator('.ws-paged-record')).toHaveCount(50)
  await expect(work.getByRole('button', { name: /그룹별 판정/ })).toBeDisabled()
  const metadata = await page.request.get(prefix + '/workbench')
  const view = await metadata.json()
  expect(view.record_count).toBe(10000); expect(view.summary.total).toBe(100)
  expect(view.task.chunks).toHaveLength(20)
  expect((await metadata.body()).length).toBeLessThan(40000)
  const originalGroup = await work.locator('.ws-selected-group-header h2').first().innerText()
  await openWorkbenchMemo(work)
  await work.getByLabel('전체 분류 메모 (선택)', { exact: true }).fill('10,000건의 전체 배정을 확인하고 페이지 간 선택을 검토합니다.')
  await work.getByRole('checkbox', { name: '현재 페이지 로그 50건 이하 선택', exact: true }).check()
  await work.getByRole('button', { name: '다음 그룹 원문 페이지', exact: true }).click()
  await expect(work.locator('.ws-record-pages .ws-pagination')).toContainText('51–100 / 100')
  await work.locator('.ws-paged-record [role="checkbox"]').first().check()
  await expect(work.locator('.ws-workbench-edit')).toContainText('로그 51건 선택')
  await work.getByRole('button', { name: '새 그룹', exact: true }).click()
  await page.getByLabel('분리할 새 그룹 이름', { exact: true }).fill('브라우저 분리그룹')
  await page.getByRole('dialog').getByRole('button', { name: '선택 로그를 새 그룹으로 분리', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeHidden({ timeout: 30000 })
  await expect(work.locator('.ws-review-progress > span').first()).toContainText('101개')
  await openWorkbenchMemo(work)
  await work.getByLabel('전체 분류 메모 (선택)', { exact: true }).fill('원문을 대조하여 두 그룹을 같은 원인으로 다시 묶습니다.')
  await work.getByRole('button', { name: '병합', exact: true }).click()
  await work.getByRole('checkbox', { name: 'CLK_0003 · 브라우저 분리그룹 그룹 전체 선택', exact: true }).check()
  await work.getByLabel('그룹 검색', { exact: true }).fill(originalGroup)
  await work.getByRole('checkbox', { name: `${originalGroup} 그룹 전체 선택`, exact: true }).check()
  await work.getByRole('button', { name: '선택한 2개 그룹 병합', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '그룹 병합', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeHidden({ timeout: 30000 })
  await work.getByLabel('그룹 검색', { exact: true }).fill('')
  await expect(work.locator('.ws-review-progress > span').first()).toContainText('100개')
  await expect(work.locator('.ws-selected-group-header')).toContainText('로그 100건 · 도구 판정')
  await supplementPipelineReasons(page, 'large-gca-10000')
  await openWorkbenchMemo(work)
  await work.getByLabel('전체 분류 메모 (선택)', { exact: true }).fill('현재 원본 10,000건의 분류 기준과 전체 배정을 확인했습니다.')
  await openWorkbenchMemo(work)
  await work.getByRole('checkbox', { name: '동일 조건의 참고 엔진에 분류 규칙 적용 허용', exact: true }).check()
  await work.getByLabel('재사용할 분류 기준', { exact: true }).fill('확인한 객체와 모드가 같은 반복 로그를 같은 그룹으로 분류합니다.')
  await work.locator('.ws-workbench-columns').scrollIntoViewIfNeeded()
  await work.getByRole('button', { name: '분류 확정 · 판정 시작', exact: true }).click()
  await expect(work.getByRole('region', { name: '2단계 확정 그룹별 판정', exact: true })).toBeVisible()
  await expect(work.locator('.ws-judgment-summary > div').first()).toContainText('FAIL')
  await work.getByRole('button', { name: '판정 제안 받기', exact: true }).click()
  await expect(work.locator('.ws-review-proposal')).toContainText('제안 보류')
  await work.getByRole('button', { name: '제안 적용', exact: true }).click()
  await work.getByRole('radio', { name: /PASS/ }).check()
  await work.getByLabel('판정 근거', { exact: true }).fill('해당 규칙과 모드에서 허용한 조건을 전체 그룹 근거로 확인했습니다.')
  await work.getByRole('button', { name: '판정 저장', exact: true }).click()
  await work.getByRole('button', { name: /그룹별 판정/ }).click()
  await expect(work.locator('.ws-review-progress > span').nth(1)).toContainText('1 / 100')
  expect((await (await page.request.get(prefix + '/workbench')).json()).summary.pending).toBe(99)
  await work.locator('.ws-judgment-summary').scrollIntoViewIfNeeded()
  expect(await (await page.request.get(prefix)).json()).toEqual(original)
  expect((await (await page.request.get('/workspace/api/blocks/project-3-gpu/versions')).json()).por).toEqual(porBefore)
  await page.reload()
  await work.getByRole('button', { name: /그룹별 판정/ }).click()
  await expect(work.locator('.ws-review-progress > span').nth(1)).toContainText('1 / 100')
  await work.getByRole('button', { name: /그룹별 판정/ }).click()
  await expect(work.locator('.ws-judgment-summary > div').nth(1)).toContainText('PASS')
  await work.getByRole('button', { name: '다음 미검토 그룹', exact: true }).click()
  await expect(work.locator('.ws-judgment-summary > div').nth(1)).toContainText('미판정')
  const context = await browser.newContext()
  try {
    const top = await context.newPage(); await gpuWorkspace(top, 'user03')
    await top.getByRole('button', { name: 'large-gca-10000 검토 시작', exact: true }).click()
    const topWork = top.getByRole('region', { name: 'Sanity item 순차 검토', exact: true })
    await topWork.getByRole('button', { name: /그룹별 판정/ }).click()
    await expect(topWork.locator('.ws-review-progress > span').nth(1)).toContainText('1 / 100')
    await topWork.getByRole('button', { name: /그룹별 판정/ }).click()
    await expect(topWork.locator('.ws-judgment-summary > div').nth(1)).toContainText('PASS')
    await expect(topWork.getByRole('button', { name: '판정 저장', exact: true })).toHaveCount(0)
  } finally { await context.close() }
  expect(errors).toEqual([])
})
