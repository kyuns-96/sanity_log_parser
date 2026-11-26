// Explicit product demo: synthetic inputs and mutations live only in this page's memory.
// Catalog UUIDs and visibility come exclusively from the shared native demo catalog.
import { isLosslessNumber, parse } from 'lossless-json'
import type { AtlasCatalog } from '@/features/atlas/atlasContext'
import { workspaceDemoRequest } from '@/features/demo/workspaceDemo'
import { formatVersionLabel } from '@/lib/versionUtils'
import type { Payload, TrackingRecord } from './api'
import {
  baselineSetting, cas, config, fail, filterRows, latest, list, notice, now, object, original,
  page, publicProject, publicUpload, readable, record, records, scoped, scopedRecord, scopedUpload,
  scopeFromRecord, seededComparisons, seedVersion, strings, text, touch, uploads, versions,
} from './demo/demoStore'
import { createComparison, TRIAGE_ENGINE, triageRun, triageSelection, triageSummary } from './demo/demoComparison'
import { handleTrackingRoute } from './demo/demoTracking'

export function demoProjectProcess(areaId: string) {
  return records.get(areaId)?.payload.process as string | null | undefined
}

async function synchronize(): Promise<{ catalog: AtlasCatalog; visible: Set<string>; actor: string }> {
  const [catalog, session] = await Promise.all([workspaceDemoRequest<AtlasCatalog>('/atlas/catalog'), workspaceDemoRequest<{ user: { username: string; id: string } }>('/session')])
  const visible = new Set<string>()
  for (const project of catalog.projects) {
    if (!project.can_write || !project.area_id) continue
    visible.add(project.area_id)
    if (!records.has(project.area_id)) record('project', { project_id: project.area_id }, { name: project.name, process: project.process, sanity_project_id: project.id, source: 'synthetic_memory_demo' }, 'active', project.area_id)
    for (const block of project.blocks) {
      if (!block.area_id) continue
      if (!records.has(block.area_id)) record('block', { project_id: project.area_id, block_id: block.area_id }, { name: block.name, sanity_block_id: block.id, source: 'synthetic_memory_demo' }, 'active', block.area_id)
      for (const version of block.versions) {
        if (!version.area_id) continue
        versions.set(version.area_id, version)
        const previous = block.versions.find(candidate => candidate.id === version.previous_version_id)
        if (!records.has(version.area_id)) record('version', { project_id: project.area_id, block_id: block.area_id, version_id: version.area_id }, { name: formatVersionLabel(version), net_ver: version.net_ver, revision: version.revision, layout: version.layout, eco_num: version.eco_num || null, sanity_version_id: version.id, previous_version_id: previous?.area_id || null, source_namespace: version.source_namespace, source: 'synthetic_memory_demo' }, 'active', version.area_id)
        if (version.id.startsWith('demo-')) seedVersion(project.area_id, block.area_id, version, block.name)
      }
    }
  }
  for (const version of versions.values()) {
    if (!version.area_id || seededComparisons.has(version.area_id)) continue
    const current = [...uploads.values()].find(upload => upload.version_id === version.area_id)
    if (!current || !visible.has(current.project_id)) continue
    const previous = [...versions.values()].find(candidate => candidate.id === version.previous_version_id)
    const baseline = previous?.area_id ? [...uploads.values()].find(upload => upload.version_id === previous.area_id && upload.condition_key === current.condition_key) : undefined
    const target = [...records.values()].find(row => row.record_type === 'target' && row.version_id === version.area_id)!
    const change = [...records.values()].find(row => row.record_type === 'change' && row.version_id === version.area_id)!
    let mapping: TrackingRecord | undefined
    if (baseline) mapping = record('mapping', scoped(current), { baseline_project_id: baseline.project_id, baseline_block_id: baseline.block_id, baseline_version_id: baseline.version_id, current_project_id: current.project_id, current_block_id: current.block_id, current_version_id: current.version_id, rules: [{ kind: 'one_to_one', scope: 'subtree', baseline_paths: ['top'], current_paths: ['top'], description: '합성 hierarchy의 명시적 동일 구조 대응' }], state: 'active', path_validation: 'valid' })
    const created = createComparison({ current_upload_id: current.id, baseline_upload_id: baseline?.id || null, comparison_kind: 'previous_version', scope: { kind: 'targets', target_ids: [target.id], include_descendants: false }, mapping_id: mapping?.id || null, change_ids: [change.id], policy: { metrics: { total_area: { relative_change_pct: '5' }, pwr_all_total_mw: { relative_change_pct: '5' } }, operating_limits: {} }, recipients: [], power_pairs: [] }, visible)
    record('job', { ...scoped(current), parent_id: created.comparison.id, job_kind: 'numeric', consumer_server: 'browser_memory' }, { source: 'synthetic_memory_demo', notice, error: { code: 'SYNTHETIC_INTERRUPTION', message: '합성 중단 예시 · 명시적 재실행으로 고정 입력을 다시 계산할 수 있습니다.' }, model_called: false }, 'failed')
    seededComparisons.add(version.area_id)
  }
  return { catalog, visible, actor: session.user.username || session.user.id }
}

async function dispatch(path: string, body: Payload | undefined, plain: boolean | 'blob' | undefined): Promise<unknown> {
  const { catalog, visible, actor } = await synchronize()
  const url = new URL(path.replace(/^\/api\/area-power(?=\/|\?|$)/, '') || '/', 'https://synthetic.invalid')
  const route = url.pathname, params = url.searchParams
  const accessible = () => [...records.values()].filter(row => readable(row, visible))
  for (const key of ['project_id', 'block_id', 'version_id', 'snapshot_id']) if (params.get(key)) {
    const row = records.get(params.get(key)!)
    if (!row || !visible.has(text(row.project_id))) fail('선택 범위가 없거나 현재 계정에서 접근할 수 없습니다.')
  }
  const trackingResult = handleTrackingRoute(route, body, visible, catalog, actor)
  if (trackingResult.handled) return trackingResult.result
  const baselineRoute = /^\/projects\/([^/]+)\/baseline$/.exec(route)
  if (baselineRoute) {
    const project = scopedRecord(decodeURIComponent(baselineRoute[1]), 'project', visible)
    if (body) {
      if (Object.keys(body).some(key => !['baseline_project_id', 'expected_row_version'].includes(key)) || !Object.hasOwn(body, 'baseline_project_id')) fail('INVALID_REQUEST · 기준 ID 또는 null과 row_version이 필요합니다.')
      cas(project, body.expected_row_version)
      if (body.baseline_project_id !== null) {
        const target = scopedRecord(text(body.baseline_project_id), 'project', visible)
        if (target.id === project.id) fail('INVALID_BASELINE · 현재 프로젝트 자체는 기준이 될 수 없습니다.')
      }
      project.payload.baseline_project_id = body.baseline_project_id
      touch(project)
    }
    return baselineSetting(project, visible)
  }
  const processRoute = /^\/projects\/([^/]+)\/process$/.exec(route)
  if (processRoute && body) {
    const project = scopedRecord(decodeURIComponent(processRoute[1]), 'project', visible)
    cas(project, body.expected_row_version)
    const process = text(body.process).trim() || null
    if (process && process !== project.payload.process && !catalog.processes?.some(item => item.name === process)) fail('등록된 공정을 선택하세요.')
    project.payload.process = process
    touch(project)
    return baselineSetting(project, visible)
  }
  const triagePreview = /^\/comparisons\/([^/]+)\/triage\/preview$/.exec(route)
  if (triagePreview && body) return triageSelection(scopedRecord(decodeURIComponent(triagePreview[1]), 'comparison', visible), object(body.thresholds), visible).preview
  const triageRuns = /^\/comparisons\/([^/]+)\/triage$/.exec(route)
  if (triageRuns) {
    const comparison = scopedRecord(decodeURIComponent(triageRuns[1]), 'comparison', visible)
    if (body) return triageRun(comparison, body, visible, actor)
    return { runs: accessible().filter(row => row.record_type === 'ai_run' && row.parent_id === comparison.id && row.payload.engine === TRIAGE_ENGINE).sort((a, b) => text(b.created_at).localeCompare(text(a.created_at))).map(row => triageSummary(row, actor)) }
  }
  const removeAnalysis = /^\/analyses\/([^/]+)\/delete$/.exec(route)
  if (removeAnalysis && body) {
    const id = decodeURIComponent(removeAnalysis[1])
    if (!records.has(id)) return { removed: [id] }
    const run = scopedRecord(id, 'ai_run', visible)
    if (run.payload.engine !== TRIAGE_ENGINE || run.payload.requested_by !== actor) fail('본인의 분석만 삭제할 수 있습니다.')
    for (const row of accessible()) if (row.parent_id === id && ['triage_mail', 'judgement'].includes(row.record_type)) records.delete(row.id)
    records.delete(id)
    return { removed: [id] }
  }
  const triageMail = /^\/triage\/([^/]+)\/mail$/.exec(route)
  if (triageMail && body) {
    const run = scopedRecord(decodeURIComponent(triageMail[1]), 'ai_run', visible)
    const mail = record('triage_mail', { ...scopeFromRecord(run), parent_id: run.id }, { recipients: strings(body.recipients), excluded_ids: strings(body.excluded_ids), requested_by: actor, job_state: 'failed', job_error: { code: 'mail_unconfigured' }, mail_called: false, source: 'synthetic_memory_demo' }, 'requested')
    return { mail_request: mail, job: null }
  }
  const triageResult = /^\/triage\/([^/]+)$/.exec(route)
  if (triageResult && !body) {
    const run = scopedRecord(decodeURIComponent(triageResult[1]), 'ai_run', visible)
    const children = accessible().filter(row => row.parent_id === run.id)
    return { ai_run: { ...triageSummary(run, actor), notes: list(run.payload.notes) }, items: children.filter(row => row.record_type === 'judgement').map(row => row.payload),
      mails: children.filter(row => row.record_type === 'triage_mail').map(row => ({ id: row.id, created_at: row.created_at, ...row.payload })),
      job: null, queue: null, worker: null, server_time: now() }
  }
  const hierarchy = /^\/uploads\/([^/]+)\/hierarchy$/.exec(route)
  if (hierarchy && !body) {
    const upload = scopedUpload(decodeURIComponent(hierarchy[1]), visible)
    const separator = text(upload.metadata.hierarchy_separator)
    return { upload_id: upload.id, snapshot_id: upload.snapshot_id, hierarchy_separator: separator, items: Object.entries(upload.rows).map(([path, metrics]) => ({ path, parent_path: path.includes(separator) ? path.slice(0, path.lastIndexOf(separator)) : null, depth: String(path.split(separator).length - 1), area: metrics.total_area ?? null, logic_area: metrics.logic_area ?? null, power_mw: metrics.pwr_all_total_mw ?? null })) }
  }
  if (route === '/access' && !body) return { actor, is_admin: false, local_mode: false, source: 'synthetic_memory_demo', notice, projects: catalog.projects.filter(project => visible.has(project.area_id || '')).map(project => ({ id: project.id, name: project.name, blocks: project.blocks.map(block => ({ id: block.id, name: block.name })) })) }
  if (route === '/uploads' && !body) return { items: page(filterRows([...uploads.values()].filter(upload => visible.has(upload.project_id)).map(publicUpload), params), params) }
  const download = /^\/uploads\/([^/]+)\/download$/.exec(route)
  if (download && !body) { if (plain !== 'blob') fail('원본 다운로드는 blob 형식으로 요청하세요.'); return original(scopedUpload(decodeURIComponent(download[1]), visible)) }
  if (route === '/records' && !body) {
    const type = params.get('record_type')
    const supported = ['project', 'block', 'version', 'snapshot', 'target', 'mapping', 'change', 'selection', 'comparison', 'job', 'alert', 'alert_event', 'metric_result', 'judgement', 'ai_run']
    if (type && !supported.includes(type)) fail(`지원하지 않는 record_type: ${type}`)
    let rows = accessible().filter(row => !type || row.record_type === type)
    if (params.get('latest') !== 'false') rows = latest(rows)
    return { items: page(filterRows(rows.map(row => row.record_type === 'project' ? publicProject(row, visible) : row), params), params) }
  }
  if (route === '/config' && body) return config(body, visible)
  if (route === '/comparisons' && body) return createComparison(body, visible)
  const detail = /^\/comparisons\/([^/]+)$/.exec(route)
  if (detail && !body) {
    const comparison = scopedRecord(decodeURIComponent(detail[1]), 'comparison', visible)
    const related = accessible().filter(row => row.parent_id === comparison.id)
    const runs = related.filter(row => row.record_type === 'ai_run')
    const runIds = new Set(runs.map(row => row.id))
    return { comparison, results: related.filter(row => row.record_type === 'metric_result'), ai_runs: runs, judgements: accessible().filter(row => row.record_type === 'judgement' && runIds.has(text(row.parent_id))) }
  }
  return fail(`지원하지 않는 경로 또는 동작: ${body ? 'POST' : 'GET'} ${route}`)
}

function normalize(value: unknown): unknown {
  if (isLosslessNumber(value)) return value.toString()
  if (Array.isArray(value)) return value.map(normalize)
  if (value !== null && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, normalize(entry)]))
  return value
}

// Serialize mutations/initialization so concurrent shared-UI loads cannot seed twice.
let requests: Promise<unknown> = Promise.resolve()
export function areaDemoRequest<T>(path: string, body?: unknown, plain?: boolean | 'blob', signal?: AbortSignal): Promise<T> {
  const run = requests.then(async () => {
    if (signal?.aborted) throw new DOMException('요청이 취소되었습니다.', 'AbortError')
    const command = body === undefined ? undefined : object(normalize(typeof body === 'string' ? parse(body) : body))
    const value = await dispatch(path, command, plain)
    if (signal?.aborted) throw new DOMException('요청이 취소되었습니다.', 'AbortError')
    return (plain === 'blob' ? value : plain ? JSON.stringify(value) : structuredClone(value)) as T
  })
  requests = run.catch(() => undefined)
  return run
}
