import { add, compare, display, fraction, multiply, parseDecimal, type Fraction } from '../fraction'
import type { AtlasVersion } from '@/features/atlas/atlasContext'
import type { Payload, TrackingRecord, Upload } from '../api'

export const notice = '합성 데모 · 브라우저 메모리 전용 · 실제 DB / AI / 사내 메일 / vwp 호출 없음'
export const object = (value: unknown): Payload => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Payload : {}
export const text = (value: unknown) => value == null ? '' : String(value)
export const list = (value: unknown): unknown[] => Array.isArray(value) ? value : []
export const strings = (value: unknown): string[] => list(value).map(text)
export const now = () => new Date().toISOString()
export const fail = (message: string): never => { throw new Error(`합성 Area 데모: ${message}`) }
export const uuid = () => crypto.randomUUID()

export type Metrics = Record<string, string>
export type DemoUpload = Upload & { rows: Record<string, Metrics>; metadata: Payload }

export const records = new Map<string, TrackingRecord>()
export const uploads = new Map<string, DemoUpload>()
export const versions = new Map<string, AtlasVersion>()
export const seeded = new Set<string>()
export const seededComparisons = new Set<string>()
export const originals = new Map<string, Promise<Blob>>()

const groups = ['total', 'logic', 'cts', 'sram', 'io', 'others']
const vths = ['slvt', 'lvt', 'rvt', 'hvt'].flatMap(value => [`vt_${value}`, `vt_${value}_llp`])
const drives = ['D0P5', 'D0P7', 'D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D10', 'D12', 'D14', 'D16', 'other'].map(value => `drv_${value}`)
export const DRIVE_GROUP_MEMBERS: Record<string, string[]> = {
  drv_sub1x_pct: ['drv_D0P5_pct', 'drv_D0P7_pct'],
  drv_d1_d3_pct: ['drv_D1_pct', 'drv_D2_pct', 'drv_D3_pct'],
  drv_d4_plus_pct: ['drv_D4_pct', 'drv_D5_pct', 'drv_D6_pct', 'drv_D7_pct', 'drv_D8_pct', 'drv_D10_pct', 'drv_D12_pct', 'drv_D14_pct', 'drv_D16_pct'],
}
export const driveGroupKeys = Object.keys(DRIVE_GROUP_MEMBERS)
const powerGroups = ['all', 'logic', 'sram', 'io', 'others']
const components = ['internal', 'switching', 'leakage', 'total']
const percentageKeys = ['cts_area_pct', ...[...vths, ...drives].map(value => `${value}_pct`)]
export const powerKeys = powerGroups.flatMap(group => components.map(component => `pwr_${group}_${component}_mw`))
const csvMetricKeys = [...groups.flatMap(group => [`${group}_cells`, `${group}_area`]), ...percentageKeys, ...[...vths, ...drives].map(value => `${value}_cnt`), ...powerKeys]
export const metricKeys = [...csvMetricKeys, ...driveGroupKeys]
const csvHeaders = ['hier_name', 'level', 'process_label', 'voltage_v', 'temp_c', 'pex', ...csvMetricKeys]

export function decimal(value: unknown): Fraction {
  return parseDecimal(value) ?? fail('지원되는 유한 십진수가 아닙니다.')
}
export const exactEvidence = (value: Fraction | null) => value && ({ numerator: String(value.n), denominator: String(value.d) })
export function sum(values: string[]): Fraction { return values.reduce((result, value) => add(result, decimal(value)), fraction(0n)) }

export function record(type: string, scope: Payload, payload: Payload, state = 'active', id: string = uuid()): TrackingRecord {
  const time = now()
  const row: TrackingRecord = { id, entity_id: id, record_type: type, revision: '1', row_version: '1', state, created_at: time, updated_at: time, ...scope, payload }
  records.set(id, row)
  return row
}
export function scoped(upload: Upload): Payload { return { project_id: upload.project_id, block_id: upload.block_id, version_id: upload.version_id, snapshot_id: upload.snapshot_id } }
export function scopeFromRecord(row: TrackingRecord): Payload { return { project_id: row.project_id, block_id: row.block_id, version_id: row.version_id, snapshot_id: row.snapshot_id } }
export function touch(row: TrackingRecord) { row.row_version = String(BigInt(row.row_version) + 1n); row.updated_at = now() }
export function cas(row: TrackingRecord, expected: unknown) {
  if (text(expected) !== row.row_version) fail('REVISION_CONFLICT · 새로고침 후 현재 row_version으로 다시 요청하세요.')
}
export function latest(rows: TrackingRecord[]): TrackingRecord[] {
  const selected = new Map<string, TrackingRecord>()
  for (const row of rows) if (!selected.has(row.entity_id) || BigInt(selected.get(row.entity_id)!.revision) < BigInt(row.revision)) selected.set(row.entity_id, row)
  return [...selected.values()]
}
export const descends = (child: string, parent: string) => child.startsWith(`${parent}/`)
export function nonoverlap(paths: string[]): string[] { return [...new Set(paths)].filter(path => !paths.some(parent => parent !== path && descends(path, parent))) }
export function checkedPaths(value: unknown): string[] {
  if (!Array.isArray(value) || value.some(path => typeof path !== 'string' || !path.trim())) return fail('paths는 비어 있지 않은 전체 경로의 배열이어야 합니다.')
  const paths = value as string[]
  if (new Set(paths).size !== paths.length || nonoverlap(paths).length !== paths.length) fail('누적 hierarchy의 부모·자식 또는 중복 경로를 한 그룹에 합산할 수 없습니다.')
  return paths
}

function numericMetrics(units: bigint, hot: boolean, rerun: boolean): Metrics {
  const result: Metrics = {}
  const values: Record<string, [bigint, bigint]> = { logic: [100n, 1000n], cts: [10n, 100n], sram: [5n, 500n], io: [5n, 100n], others: [5n, 50n] }
  for (const [group, [cells, area]] of Object.entries(values)) { result[`${group}_cells`] = String(cells * units); result[`${group}_area`] = String(area * units) }
  for (const key of ['cells', 'area']) result[`total_${key}`] = display(sum(Object.keys(values).map(group => result[`${group}_${key}`])))!
  result.cts_area_pct = '10'
  for (const name of [...vths, ...drives]) { result[`${name}_cnt`] = '0'; result[`${name}_pct`] = '0' }
  for (const [name, pct] of [['vt_lvt', 25n], ['vt_rvt', 50n], ['vt_hvt', 25n], ['drv_D0P5', 10n], ['drv_D0P7', 5n], ['drv_D1', 35n], ['drv_D2', 25n], ['drv_D4', 25n]] as const) {
    result[`${name}_cnt`] = String(pct * units); result[`${name}_pct`] = String(pct)
  }
  const factor = fraction((hot ? 15n : 10n) + (rerun ? 1n : 0n), 100n)
  for (const [group, multiplier] of [['logic', 4n], ['sram', 2n], ['io', 1n], ['others', 1n]] as const) {
    for (const [component, weight] of [['internal', 5n], ['switching', 3n], ['leakage', 2n]] as const) result[`pwr_${group}_${component}_mw`] = display(multiply(fraction(units * multiplier * weight), factor))!
    result[`pwr_${group}_total_mw`] = display(sum(components.slice(0, 3).map(component => result[`pwr_${group}_${component}_mw`])))!
  }
  for (const component of components) result[`pwr_all_${component}_mw`] = display(sum(powerGroups.slice(1).map(group => result[`pwr_${group}_${component}_mw`])))!
  return result
}

export function seedVersion(projectId: string, blockId: string, version: AtlasVersion, blockName: string) {
  const versionId = version.area_id!
  if (seeded.has(versionId)) return
  seeded.add(versionId)
  const scope = { project_id: projectId, block_id: blockId, version_id: versionId }
  const snapshot = record('snapshot', scope, { name: `${blockName} / ${version.net_ver} ${version.layout} · 합성 구조`, hierarchy_separator: '/', root_path: 'top', hierarchy_semantics: 'cumulative', node_count: '4', source: 'synthetic_memory_demo' }, 'ready')
  const variation = BigInt([...version.id].reduce((value, character) => (value * 31 + character.charCodeAt(0)) % 29, 0))
  const units = 10n + variation
  for (const [cornerIndex, hot] of [false, true].entries()) {
    for (const rerun of cornerIndex === 0 ? [false, true] : [false]) {
      const id = uuid()
      const corner = { process_label: hot ? 'SS' : 'TT', voltage_v: hot ? '0.72' : '0.80', temp_c: hot ? '125' : '25', pex: 'RCMAX' }
      const metadata = { ...scope, snapshot_id: snapshot.id, execution_name: `합성 ${blockName} ${version.net_ver} ${version.layout} ${hot ? 'SS-hot' : 'TT-nominal'}${rerun ? ' 재실행' : ''}`, design_stage: version.layout === 'POST' ? 'post_route' : 'pre_route', hierarchy_separator: '/', area_unit: 'um2', area_basis: 'cell_area', library: 'SYNTHETIC_5NM_R1', classification: 'synthetic_vth_drive_v1', constraint_context: { clock_period_ns: '1.00', mode: 'functional' },
        analysis_conditions: { mode: 'functional', clocks: [{ name: 'synthetic_clk', period_ns: '1.00' }], activity: { source: 'synthetic_saif_v1', toggle_rate: '0.10' }, rc: { corner: 'RCMAX' }, tool: { name: 'SYNTHETIC_POWER', version: 'demo-v1', method: 'vector_based' }, voltage_domains: [{ name: 'synthetic_core', voltage_v: corner.voltage_v }] },
        pct_tolerance: '0', power_tolerance_mw: '0' }
      const rows: Record<string, Metrics> = { top: numericMetrics(units * 10n, hot, rerun), 'top/core': numericMetrics(units * 6n, hot, rerun), 'top/cache': numericMetrics(units * 3n, hot, rerun), 'top/fabric': numericMetrics(units, hot, rerun) }
      const upload: DemoUpload = { id, state: 'ready', ...scope, snapshot_id: snapshot.id, ...corner, condition_key: `synthetic:${hot ? 'SS:0.72:125' : 'TT:0.80:25'}:RCMAX:1000:functional:synthetic_saif_v1`, execution_name: metadata.execution_name, filename: `${id}.csv.gz`, original_filename: `${id}.csv`, created_at: now(), root_summary: { hier_name: 'top', level: '0', metrics: rows.top, quality: { vth_unclassified_cnt: '0', drive_unclassified_cnt: '0', unavailable_ratios: [], hierarchy_semantics: 'cumulative; root is NOT summed with descendants' }, source: 'synthetic_memory_demo' }, input_contract: { version: '1', metadata, corner, analysis_conditions: metadata.analysis_conditions, conditions_complete: true, source: 'synthetic_memory_demo', notice }, rows, metadata, source: 'synthetic_memory_demo', row_count: '4', download_format: 'gzip', notice }
      uploads.set(id, upload)
      if (!cornerIndex && !rerun) {
        const selection = record('selection', scoped(upload), { source: 'synthetic_memory_demo', notice }, 'active', uuid())
        selection.upload_id = id; selection.condition_key = upload.condition_key; selection.selection_scope = 'shared'
      }
    }
  }
  record('target', { ...scope, snapshot_id: snapshot.id }, { name: '합성 Core + cache (겹치지 않는 그룹)', paths: ['top/core', 'top/cache'], scope: 'node', metrics: ['total_area', 'logic_cells', 'cts_area_pct', 'pwr_all_total_mw'], bindings: [{ snapshot_id: snapshot.id, paths: ['top/core', 'top/cache'] }], path_validation: 'valid' })
  record('change', scope, { text: `합성 설계 예시: ${blockName} ${version.net_ver} ${version.layout}의 core/cache 용량 조정. 실제 변경 보고서나 AI 설명이 아닙니다. 수치 변화량을 이 원문만으로 추론하지 않습니다.`, paths: ['top/core', 'top/cache'], metrics: ['total_area', 'logic_cells', 'pwr_all_total_mw'], applies_from_version_id: versionId, expected: [], extraction_evidence: [] })
}

export function publicUpload(upload: DemoUpload): Upload { const { rows: _rows, metadata: _metadata, ...value } = upload; return value }

export async function original(upload: DemoUpload): Promise<Blob> {
  let value = originals.get(upload.id)
  if (!value) {
    value = (async () => {
      const csv = `${csvHeaders.join(',')}\n${Object.entries(upload.rows).map(([path, metrics]) => csvHeaders.map(key => key === 'hier_name' ? path : key === 'level' ? path === 'top' ? '0' : '1' : metrics[key] ?? text(upload[key])).join(',')).join('\n')}\n`
      const input = new Blob([csv], { type: 'text/csv;charset=utf-8' })
      const stream = input.stream().pipeThrough(new CompressionStream('gzip'))
      return new Blob([await new Response(stream).arrayBuffer()], { type: 'application/gzip' })
    })()
    originals.set(upload.id, value)
  }
  return value
}

export function publicProject(row: TrackingRecord, visible: Set<string>): TrackingRecord {
  const target = text(row.payload.baseline_project_id)
  const status = !target ? 'unset' : visible.has(target) && records.get(target)?.record_type === 'project' ? 'available' : 'unavailable'
  return { ...row, payload: { ...row.payload, baseline_project_id: status === 'available' ? target : null }, baseline_status: status }
}

export function designatedBaseline(projectId: string, visible: Set<string>, selected?: string) {
  const project = scopedRecord(projectId, 'project', visible)
  const target = text(project.payload.baseline_project_id)
  if (!target) fail('BASELINE_NOT_CONFIGURED · 현재 프로젝트의 기준을 먼저 저장하세요.')
  scopedRecord(target, 'project', visible)
  if (target === projectId || selected && selected !== target) fail('INVALID_BASELINE · 저장된 프로젝트 기준과 선택한 실행이 다릅니다.')
  return { project_id: projectId, baseline_project_id: target, row_version: project.row_version }
}

export function readable(row: TrackingRecord, visible: Set<string>): boolean {
  if (!visible.has(text(row.project_id))) return false
  if (row.record_type === 'mapping' && !visible.has(text(row.payload.baseline_project_id))) return false
  if (row.record_type === 'comparison') {
    const baselineId = text(object(row.payload.request).baseline_upload_id)
    if (baselineId && !visible.has(uploads.get(baselineId)?.project_id || '')) return false
    const designation = object(object(row.payload.frozen).baseline_designation)
    if (designation.baseline_project_id && !visible.has(text(designation.baseline_project_id))) return false
  }
  const parent = row.parent_id ? records.get(row.parent_id) : undefined
  return !parent || readable(parent, visible)
}

export function scopedRecord(id: string, type: string, visible: Set<string>): TrackingRecord {
  const row = records.get(id)
  if (!row || row.record_type !== type || !readable(row, visible)) return fail('대상이 없거나 현재 합성 계정의 범위 밖입니다.')
  return row
}
export function scopedUpload(id: string, visible: Set<string>): DemoUpload {
  const upload = uploads.get(id)
  if (!upload || !visible.has(upload.project_id)) return fail('업로드가 없거나 현재 합성 계정의 범위 밖입니다.')
  return upload
}

export function ancestry(current: DemoUpload, baseline: DemoUpload, kind: string) {
  if (current.id === baseline.id) fail('현재와 기준에 같은 실행을 선택할 수 없습니다.')
  if (kind === 'previous_project') { if (current.project_id === baseline.project_id) fail('previous_project는 다른 프로젝트 기준이 필요합니다.'); return }
  if (kind !== 'previous_version') fail('지원하지 않는 비교 기준 종류입니다.')
  const version = versions.get(current.version_id)
  const chain = new Set<string>()
  let previous = version?.previous_version_id
  while (previous && !chain.has(previous)) {
    chain.add(previous)
    const parent = [...versions.values()].find(value => value.id === previous)
    if (parent?.area_id === baseline.version_id && current.project_id === baseline.project_id && current.block_id === baseline.block_id) return
    previous = parent?.previous_version_id
  }
  fail('기준은 현재 카탈로그의 명시적 previous_version_id 계보에 속해야 합니다.')
}

export function validateScope(body: Payload, visible: Set<string>) {
  const project = records.get(text(body.project_id))
  if (!project || project.record_type !== 'project' || !visible.has(project.id)) fail('접근 가능한 Area 프로젝트 UUID를 선택하세요.')
  for (const kind of ['block', 'version', 'snapshot']) {
    const id = text(body[`${kind}_id`]); if (!id) continue
    const row = records.get(id)
    if (!row || row.record_type !== kind || row.project_id !== project!.id || (body.block_id && kind !== 'block' && row.block_id !== body.block_id) || (body.version_id && kind === 'snapshot' && row.version_id !== body.version_id)) fail(`${kind} UUID가 선택 범위와 일치하지 않습니다.`)
  }
}

export function policyChecked(value: unknown): Payload {
  const policy = object(value)
  if (Object.keys(policy).some(key => !['metrics', 'operating_limits'].includes(key))) fail('지원하지 않는 정책 필드입니다.')
  for (const family of ['metrics', 'operating_limits']) for (const [metric, rulesValue] of Object.entries(object(policy[family]))) {
    if (!metricKeys.includes(metric)) fail(`지원하지 않는 정책 지표: ${metric}`)
    const rules = object(rulesValue)
    const allowed = family === 'metrics' ? ['absolute_change', 'relative_change_pct', 'pct_point_change', 'min_denominator'] : ['lower', 'upper', 'min_denominator']
    if (!Object.keys(rules).length || Object.keys(rules).some(key => !allowed.includes(key))) fail('검출 정책 규칙을 확인하세요.')
    for (const [key, bound] of Object.entries(rules)) if (bound != null && (family === 'metrics' || key === 'min_denominator') && decimal(bound).n < 0n) fail('임계값은 0 이상이어야 합니다.')
    for (const bound of Object.values(rules)) if (bound != null) decimal(bound)
    if (rules.lower != null && rules.upper != null && compare(decimal(rules.lower), decimal(rules.upper)) > 0) fail('lower가 upper보다 큽니다.')
  }
  return { metrics: object(policy.metrics), operating_limits: object(policy.operating_limits) }
}

export function requestedMetrics(value: unknown): string[] {
  const requested = strings(value)
  if (!requested.length) return metricKeys
  const families: Record<string, string[]> = { area: metricKeys.filter(key => key.includes('_area')), count: metricKeys.filter(key => key.endsWith('_cnt') || key.endsWith('_cells')), power: powerKeys, vth: metricKeys.filter(key => key.startsWith('vt_')), drive: metricKeys.filter(key => key.startsWith('drv_')) }
  const result = [...new Set(requested.flatMap(metric => families[metric] || [metric]))]
  if (result.some(metric => !metricKeys.includes(metric))) fail('지원하지 않는 Target 지표입니다.')
  return result
}

export function filterRows<T extends Payload>(rows: T[], params: URLSearchParams): T[] {
  const fields = ['project_id', 'block_id', 'version_id', 'snapshot_id', 'parent_id', 'entity_id', 'state', 'upload_id']
  return rows.filter(row => fields.every(key => !params.get(key) || text(row[key]) === params.get(key)))
}

export function page<T>(rows: T[], params: URLSearchParams): T[] {
  const offset = Number(params.get('offset') || 0), limit = Number(params.get('limit') || 200)
  if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 1000) fail('잘못된 페이지 범위입니다.')
  return rows.slice(offset, offset + limit)
}

export function config(body: Payload, visible: Set<string>) {
  const type = text(body.record_type)
  if (!['target', 'mapping', 'change'].includes(type)) fail('지원하는 설정은 target / mapping / change입니다.')
  validateScope(body, visible)
  const payload = structuredClone(object(body.payload))
  if (type === 'target') { if (!text(payload.name).trim()) fail('Target 이름이 필요합니다.'); if (payload.watch === true) { if (!strings(payload.paths).length) fail('주요 instance 경로가 필요합니다.') } else checkedPaths(payload.paths); requestedMetrics(payload.metrics); if (!['node', 'subtree'].includes(text(payload.scope))) fail('Target scope는 node / subtree입니다.'); for (const value of list(payload.bindings)) { const binding = object(value); const snapshot = scopedRecord(text(binding.snapshot_id), 'snapshot', visible); if (snapshot.project_id !== body.project_id || body.block_id && snapshot.block_id !== body.block_id) fail('Target binding snapshot 범위가 다릅니다.'); checkedPaths(binding.paths) } }
  if (type === 'change' && !text(payload.text).trim()) fail('변경 원문이 필요합니다.')
  if (type === 'mapping') {
    for (const side of ['baseline', 'current']) validateScope({ project_id: payload[`${side}_project_id`], block_id: payload[`${side}_block_id`], version_id: payload[`${side}_version_id`] }, visible)
    if (payload.current_project_id !== body.project_id || body.block_id && payload.current_block_id !== body.block_id || body.version_id && payload.current_version_id !== body.version_id) fail('매핑 current UUID는 등록 범위와 일치해야 합니다.')
    if (payload.baseline_project_id !== payload.current_project_id) designatedBaseline(text(payload.current_project_id), visible, text(payload.baseline_project_id))
    if (!list(payload.rules).length) fail('하나 이상의 매핑 규칙이 필요합니다.')
    for (const ruleValue of list(payload.rules)) {
      const rule = object(ruleValue), before = checkedPaths(rule.baseline_paths), after = checkedPaths(rule.current_paths)
      const allowed = { one_to_one: before.length === 1 && after.length === 1, split: before.length === 1 && after.length >= 2, merge: before.length >= 2 && after.length === 1, added: before.length === 0 && after.length >= 1, removed: before.length >= 1 && after.length === 0 }
      if (!allowed[text(rule.kind) as keyof typeof allowed] || !['node', 'subtree'].includes(text(rule.scope)) || rule.scope === 'subtree' && rule.kind !== 'one_to_one') fail('매핑 종류 / scope / 경로 개수가 일치하지 않습니다.')
    }
  }
  const paths = type === 'mapping' ? list(payload.rules).map(object).flatMap(rule => strings(rule.current_paths)) : strings(payload.paths)
  if (type !== 'change') {
    const candidates = [...uploads.values()].filter(upload => upload.project_id === body.project_id && (!body.version_id || upload.version_id === body.version_id) && (!body.snapshot_id || upload.snapshot_id === body.snapshot_id))
    payload.path_validation = candidates.some(upload => paths.every(path => upload.rows[path])) ? 'valid' : 'pending'
    if (type === 'mapping') payload.state = payload.path_validation === 'valid' ? 'active' : 'pending'
  }
  let previous: TrackingRecord | undefined
  if (body.entity_id) {
    previous = latest([...records.values()].filter(row => row.entity_id === body.entity_id && row.record_type === type))[0]
    if (!previous) fail('편집 대상 설정이 없습니다.')
    scopedRecord(previous!.id, type, visible); cas(previous!, body.expected_row_version)
    for (const key of ['project_id', 'block_id', 'version_id', 'snapshot_id']) if (text(previous![key]) !== text(body[key])) fail('기존 설정 범위는 변경할 수 없습니다.')
  }
  const row = record(type, { project_id: text(body.project_id), block_id: text(body.block_id) || null, version_id: text(body.version_id) || null, snapshot_id: text(body.snapshot_id) || null }, payload, text(payload.state) || 'active')
  if (previous) { row.entity_id = previous.entity_id; row.revision = String(BigInt(previous.revision) + 1n); row.row_version = String(BigInt(previous.row_version) + 1n) }
  return row
}

export function baselineSetting(project: TrackingRecord, visible: Set<string>) {
  const value = publicProject(project, visible)
  const target = value.payload.baseline_project_id ? scopedRecord(text(value.payload.baseline_project_id), 'project', visible) : null
  return { project_id: value.id, row_version: value.row_version, baseline_project_id: target?.id || null, baseline_project: target ? publicProject(target, visible) : null, status: value.baseline_status,
    process: project.payload.process ?? null, baseline_process: target?.payload.process ?? null }
}
