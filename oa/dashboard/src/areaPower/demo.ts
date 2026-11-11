// Explicit product demo: synthetic inputs and mutations live only in this page's memory.
// Catalog UUIDs and visibility come exclusively from the shared native demo catalog.
import { isLosslessNumber, parse } from 'lossless-json'
import type { AtlasCatalog, AtlasVersion } from '../atlasContext'
import { workspaceDemoRequest } from '../workspaceDemo'
import type { Payload, TrackingRecord, Upload } from './api'

const notice = '합성 데모 · 브라우저 메모리 전용 · 실제 DB / AI / 사내 메일 / vwp 호출 없음'
const object = (value: unknown): Payload => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Payload : {}
const text = (value: unknown) => value == null ? '' : String(value)
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : []
const strings = (value: unknown): string[] => list(value).map(text)
const now = () => new Date().toISOString()
const fail = (message: string): never => { throw new Error(`합성 Area 데모: ${message}`) }
const uuid = () => crypto.randomUUID()
const records = new Map<string, TrackingRecord>()
const uploads = new Map<string, DemoUpload>()
const versions = new Map<string, AtlasVersion>()
const seeded = new Set<string>()
const seededComparisons = new Set<string>()
const originals = new Map<string, Promise<Blob>>()
const groups = ['total', 'logic', 'cts', 'sram', 'io', 'others']
const vths = ['slvt', 'lvt', 'rvt', 'hvt'].flatMap(value => [`vt_${value}`, `vt_${value}_llp`])
const drives = ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D10', 'D12', 'D14', 'D16', 'other'].map(value => `drv_${value}`)
const powerGroups = ['all', 'logic', 'sram', 'io', 'others']
const components = ['internal', 'switching', 'leakage', 'total']
const percentageKeys = ['cts_area_pct', ...[...vths, ...drives].map(value => `${value}_pct`)]
const powerKeys = powerGroups.flatMap(group => components.map(component => `pwr_${group}_${component}_mw`))
const metricKeys = [...groups.flatMap(group => [`${group}_cells`, `${group}_area`]), ...percentageKeys, ...[...vths, ...drives].map(value => `${value}_cnt`), ...powerKeys]
const csvHeaders = ['hier_name', 'level', 'process_label', 'voltage_v', 'temp_c', 'pex', ...metricKeys]
type Metrics = Record<string, string>
type DemoUpload = Upload & { rows: Record<string, Metrics>; metadata: Payload }
type Fraction = { n: bigint; d: bigint }
const abs = (value: bigint) => value < 0n ? -value : value
function fraction(n: bigint, d = 1n): Fraction {
  if (!d) return fail('0으로 나눌 수 없습니다.')
  if (d < 0n) { n = -n; d = -d }
  let a = abs(n), b = d
  while (b) { const next = a % b; a = b; b = next }
  return { n: n / a, d: d / a }
}
function decimal(value: unknown): Fraction {
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(text(value))
  if (!match) return fail(`유한 십진수가 아닙니다: ${text(value)}`)
  const exponent = Number(match[4] || 0) - (match[3]?.length || 0)
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 1000) return fail('십진수 지수가 지원 범위를 벗어났습니다.')
  const n = BigInt(`${match[1]}${match[2]}${match[3] || ''}`)
  return exponent >= 0 ? fraction(n * 10n ** BigInt(exponent)) : fraction(n, 10n ** BigInt(-exponent))
}
const add = (a: Fraction, b: Fraction) => fraction(a.n * b.d + b.n * a.d, a.d * b.d)
const subtract = (a: Fraction, b: Fraction) => fraction(a.n * b.d - b.n * a.d, a.d * b.d)
const multiply = (a: Fraction, b: Fraction) => fraction(a.n * b.n, a.d * b.d)
const divide = (a: Fraction, b: Fraction) => fraction(a.n * b.d, a.d * b.n)
const compare = (a: Fraction, b: Fraction) => a.n * b.d < b.n * a.d ? -1 : a.n * b.d === b.n * a.d ? 0 : 1
const magnitude = (a: Fraction) => fraction(abs(a.n), a.d)
// All arithmetic is rational BigInt. Only repeating decimal DISPLAY is rounded,
// half-up at 30 fractional places; exact numerator/denominator remain in evidence.
function display(value: Fraction | null): string | null {
  if (value === null) return null
  const scale = 10n ** 30n
  const scaled = (abs(value.n) * scale * 2n + value.d) / (2n * value.d)
  const digits = (scaled % scale).toString().padStart(30, '0').replace(/0+$/, '')
  return `${value.n < 0n && scaled ? '-' : ''}${scaled / scale}${digits ? `.${digits}` : ''}`
}
const exactEvidence = (value: Fraction | null) => value && ({ numerator: String(value.n), denominator: String(value.d) })
function sum(values: string[]): Fraction { return values.reduce((result, value) => add(result, decimal(value)), fraction(0n)) }
function record(type: string, scope: Payload, payload: Payload, state = 'active', id: string = uuid()): TrackingRecord {
  const time = now()
  const row: TrackingRecord = { id, entity_id: id, record_type: type, revision: '1', row_version: '1', state, created_at: time, updated_at: time, ...scope, payload }
  records.set(id, row)
  return row
}
function scoped(upload: Upload): Payload { return { project_id: upload.project_id, block_id: upload.block_id, version_id: upload.version_id, snapshot_id: upload.snapshot_id } }
function touch(row: TrackingRecord) { row.row_version = String(BigInt(row.row_version) + 1n); row.updated_at = now() }
function cas(row: TrackingRecord, expected: unknown) {
  if (text(expected) !== row.row_version) fail('REVISION_CONFLICT · 새로고침 후 현재 row_version으로 다시 요청하세요.')
}
function latest(rows: TrackingRecord[]): TrackingRecord[] {
  const selected = new Map<string, TrackingRecord>()
  for (const row of rows) if (!selected.has(row.entity_id) || BigInt(selected.get(row.entity_id)!.revision) < BigInt(row.revision)) selected.set(row.entity_id, row)
  return [...selected.values()]
}
const descends = (child: string, parent: string) => child.startsWith(`${parent}/`)
function nonoverlap(paths: string[]): string[] { return [...new Set(paths)].filter(path => !paths.some(parent => parent !== path && descends(path, parent))) }
function checkedPaths(value: unknown): string[] {
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
  for (const [name, pct] of [['vt_lvt', 25n], ['vt_rvt', 50n], ['vt_hvt', 25n], ['drv_D1', 50n], ['drv_D2', 25n], ['drv_D4', 25n]] as const) {
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
function seedVersion(projectId: string, blockId: string, version: AtlasVersion, blockName: string) {
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
      if (!cornerIndex && !rerun) record('selection', scoped(upload), { source: 'synthetic_memory_demo', notice }, 'active', uuid()).upload_id = id
      if (!cornerIndex && !rerun) {
        const selection = [...records.values()].find(row => row.record_type === 'selection' && row.upload_id === id)!
        selection.condition_key = upload.condition_key; selection.selection_scope = 'shared'
      }
    }
  }
  record('target', { ...scope, snapshot_id: snapshot.id }, { name: '합성 Core + cache (겹치지 않는 그룹)', paths: ['top/core', 'top/cache'], scope: 'node', metrics: ['total_area', 'logic_cells', 'cts_area_pct', 'pwr_all_total_mw'], bindings: [{ snapshot_id: snapshot.id, paths: ['top/core', 'top/cache'] }], path_validation: 'valid' })
  record('change', scope, { text: `합성 설계 예시: ${blockName} ${version.net_ver} ${version.layout}의 core/cache 용량 조정. 실제 변경 보고서나 AI 설명이 아닙니다. 수치 변화량을 이 원문만으로 추론하지 않습니다.`, paths: ['top/core', 'top/cache'], metrics: ['total_area', 'logic_cells', 'pwr_all_total_mw'], applies_from_version_id: versionId, expected: [], extraction_evidence: [] })
}
function publicUpload(upload: DemoUpload): Upload { const { rows: _rows, metadata: _metadata, ...value } = upload; return value }
async function original(upload: DemoUpload): Promise<Blob> {
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
function readable(row: TrackingRecord, visible: Set<string>): boolean {
  if (!visible.has(text(row.project_id))) return false
  if (row.record_type === 'mapping' && !visible.has(text(row.payload.baseline_project_id))) return false
  if (row.record_type === 'comparison') {
    const baselineId = text(object(row.payload.request).baseline_upload_id)
    if (baselineId && !visible.has(uploads.get(baselineId)?.project_id || '')) return false
  }
  const parent = row.parent_id ? records.get(row.parent_id) : undefined
  return !parent || readable(parent, visible)
}

function scopedRecord(id: string, type: string, visible: Set<string>): TrackingRecord {
  const row = records.get(id)
  if (!row || row.record_type !== type || !readable(row, visible)) return fail('대상이 없거나 현재 합성 계정의 범위 밖입니다.')
  return row
}
function scopedUpload(id: string, visible: Set<string>): DemoUpload {
  const upload = uploads.get(id)
  if (!upload || !visible.has(upload.project_id)) return fail('업로드가 없거나 현재 합성 계정의 범위 밖입니다.')
  return upload
}
function ancestry(current: DemoUpload, baseline: DemoUpload, kind: string) {
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
function validateScope(body: Payload, visible: Set<string>) {
  const project = records.get(text(body.project_id))
  if (!project || project.record_type !== 'project' || !visible.has(project.id)) fail('접근 가능한 Area 프로젝트 UUID를 선택하세요.')
  for (const kind of ['block', 'version', 'snapshot']) {
    const id = text(body[`${kind}_id`]); if (!id) continue
    const row = records.get(id)
    if (!row || row.record_type !== kind || row.project_id !== project!.id || (body.block_id && kind !== 'block' && row.block_id !== body.block_id) || (body.version_id && kind === 'snapshot' && row.version_id !== body.version_id)) fail(`${kind} UUID가 선택 범위와 일치하지 않습니다.`)
  }
}
function policyChecked(value: unknown): Payload {
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
function requestedMetrics(value: unknown): string[] {
  const requested = strings(value)
  if (!requested.length) return metricKeys
  const families: Record<string, string[]> = { area: metricKeys.filter(key => key.includes('_area')), count: metricKeys.filter(key => key.endsWith('_cnt') || key.endsWith('_cells')), power: powerKeys, vth: metricKeys.filter(key => key.startsWith('vt_')), drive: metricKeys.filter(key => key.startsWith('drv_')) }
  const result = [...new Set(requested.flatMap(metric => families[metric] || [metric]))]
  if (result.some(metric => !metricKeys.includes(metric))) fail('지원하지 않는 Target 지표입니다.')
  return result
}
function metricValue(upload: DemoUpload | null, paths: string[], metric: string): { value: Fraction | null; numerator: Fraction | null; denominator: Fraction | null } {
  const missing = { value: null, numerator: null, denominator: null }
  if (!upload || !paths.length || paths.some(path => !upload.rows[path])) return missing
  const total = (key: string) => sum(paths.map(path => upload.rows[path][key]))
  if (metric.endsWith('_pct')) {
    const numerator = total(metric === 'cts_area_pct' ? 'cts_area' : `${metric.slice(0, -4)}_cnt`)
    const denominator = total(metric === 'cts_area_pct' ? 'logic_area' : 'logic_cells')
    return { value: denominator.n ? multiply(divide(numerator, denominator), fraction(100n)) : null, numerator, denominator }
  }
  return { value: total(metric), numerator: null, denominator: null }
}
type Group = { current: string[]; baseline: string[]; metrics: string[]; target_id?: string; reason?: string }
function comparisonGroups(input: Payload, current: DemoUpload, baseline: DemoUpload | null, visible: Set<string>): Group[] {
  const scope = object(input.scope)
  const mapping = input.mapping_id ? scopedRecord(text(input.mapping_id), 'mapping', visible) : null
  if (mapping) {
    const p = mapping.payload
    if (p.current_project_id !== current.project_id || p.current_version_id !== current.version_id || p.current_block_id !== current.block_id || !baseline || p.baseline_project_id !== baseline.project_id || p.baseline_version_id !== baseline.version_id || p.baseline_block_id !== baseline.block_id) fail('매핑 revision의 명시적 양쪽 UUID가 비교 입력과 다릅니다.')
  }
  const mapPaths = (paths: string[]): string[] => {
    if (!mapping) return baseline ? paths : []
    return [...new Set(paths.flatMap(path => {
      const rule = list(mapping.payload.rules).map(object).find(row => strings(row.current_paths).includes(path) || row.scope === 'subtree' && strings(row.current_paths).some(parent => descends(path, parent)))
      if (!rule) return baseline?.rows[path] ? [path] : []
      const before = strings(rule.baseline_paths), after = strings(rule.current_paths)
      if (rule.scope === 'subtree' && before.length === 1 && after.length === 1) return [before[0] + path.slice(after[0].length)]
      return before
    }))]
  }
  if (scope.kind === 'targets') {
    const targets = strings(scope.target_ids)
    if (!targets.length) fail('Target 범위는 하나 이상의 정확한 revision이 필요합니다.')
    return targets.flatMap(id => {
      const target = scopedRecord(id, 'target', visible)
      if (target.project_id !== current.project_id || target.block_id && target.block_id !== current.block_id || target.version_id && target.version_id !== current.version_id) fail('Target revision이 현재 범위에 속하지 않습니다.')
      const binding = list(target.payload.bindings).map(object).find(value => value.snapshot_id === current.snapshot_id)
      const paths = strings(binding?.paths || target.payload.paths)
      checkedPaths(paths)
      const resolved = scope.include_descendants ? [...new Set(paths.flatMap(path => [path, ...Object.keys(current.rows).filter(value => descends(value, path))]))].map(path => [path]) : [paths]
      return resolved.map(currentPaths => ({ current: currentPaths, baseline: mapPaths(currentPaths), target_id: id, metrics: requestedMetrics(target.payload.metrics) }))
    })
  }
  if (scope.kind && scope.kind !== 'all') fail('지원하지 않는 비교 범위입니다.')
  if (mapping) {
    const mapped = list(mapping.payload.rules).map(object).flatMap(rule => {
      const paths = strings(rule.current_paths)
      if (rule.scope === 'subtree' && paths.length === 1) return Object.keys(current.rows).filter(path => path === paths[0] || descends(path, paths[0])).map(path => ({ current: [path], baseline: mapPaths([path]), metrics: metricKeys }))
      return [{ current: paths, baseline: strings(rule.baseline_paths), metrics: metricKeys, reason: rule.kind === 'added' || rule.kind === 'removed' ? `declared_${text(rule.kind)}` : undefined }]
    })
    const accounted = new Set(mapped.flatMap(group => group.current))
    return [...mapped, ...Object.keys(current.rows).filter(path => !accounted.has(path)).map(path => ({ current: [path], baseline: mapPaths([path]), metrics: metricKeys }))]
  }
  return [...new Set([...Object.keys(current.rows), ...Object.keys(baseline?.rows || {})])].map(path => ({ current: current.rows[path] ? [path] : [], baseline: baseline?.rows[path] ? [path] : [], metrics: metricKeys }))
}
function resultPayload(metric: string, group: Group, current: DemoUpload, baseline: DemoUpload | null, policy: Payload, pair: Payload | null): Payload {
  const invalidOverlap = nonoverlap(group.current).length !== group.current.length || nonoverlap(group.baseline).length !== group.baseline.length
  const c = metricValue(current, invalidOverlap ? [] : group.current, metric), b = metricValue(baseline, invalidOverlap ? [] : group.baseline, metric)
  let comparability = 'comparable', reason = ''
  if (!baseline || !b.value || !c.value || group.reason || invalidOverlap) { comparability = 'unavailable'; reason = invalidOverlap ? 'overlapping_cumulative_paths' : group.reason || (!baseline ? 'baseline_missing' : 'metric_or_path_missing') }
  else {
    const checks = ['design_stage', 'constraint_context', 'library', 'classification', ...(metric.endsWith('_area') ? ['area_unit', 'area_basis'] : [])]
    if (checks.some(key => JSON.stringify(current.metadata[key]) !== JSON.stringify(baseline.metadata[key]))) { comparability = 'contextual'; reason = 'structural_metadata_different' }
    if (current.condition_key !== baseline.condition_key) { comparability = 'contextual'; reason = powerKeys.includes(metric) ? 'power_conditions_different' : 'corner_different_context_only' }
  }
  const delta = c.value && b.value ? subtract(c.value, b.value) : null
  const relative = delta && b.value?.n ? multiply(divide(delta, b.value), fraction(100n)) : null
  const pp = metric.endsWith('_pct') ? delta : null
  const threshold = object(object(policy.metrics)[metric]), limits = object(object(policy.operating_limits)[metric])
  const minimum = threshold.min_denominator == null ? null : decimal(threshold.min_denominator)
  const denominators = [b.denominator || b.value, c.denominator || c.value].filter((value): value is Fraction => value !== null)
  const tiny = minimum !== null && denominators.some(value => compare(magnitude(value), minimum) < 0)
  const exceeded = Object.entries({ absolute_change: delta, relative_change_pct: relative, pct_point_change: pp }).filter(([key, value]) => value !== null && threshold[key] != null && !(tiny && key === 'relative_change_pct') && compare(magnitude(value as Fraction), decimal(threshold[key])) > 0).map(([key]) => key)
  const limitExceeded: string[] = []
  if (c.value && limits.lower != null && compare(c.value, decimal(limits.lower)) < 0) limitExceeded.push('lower')
  if (c.value && limits.upper != null && compare(c.value, decimal(limits.upper)) > 0) limitExceeded.push('upper')
  if (limits.min_denominator != null && denominators.some(value => compare(value, decimal(limits.min_denominator)) < 0)) limitExceeded.push('min_denominator')
  const unconfigured = !Object.keys(object(policy.metrics)).length && !Object.keys(object(policy.operating_limits)).length
  return { hier_name: (group.current.length ? group.current : group.baseline).join(' + ') || 'Unresolved target', target_id: group.target_id || null, metric, unit: powerKeys.includes(metric) ? 'mW' : metric.endsWith('_pct') ? '%' : metric.endsWith('_area') ? text(current.metadata.area_unit) : 'cells', baseline_paths: group.baseline, current_paths: group.current, baseline: display(b.value), current: display(c.value), delta: display(delta), relative_change_pct: display(relative), pct_point_change: display(pp), baseline_numerator: display(b.numerator), baseline_denominator: display(b.denominator), current_numerator: display(c.numerator), current_denominator: display(c.denominator), comparability, reason: reason || (unconfigured ? 'policy_unconfigured · 정상 판정 아님' : 'explicit_inputs_and_context_match'), is_candidate: comparability !== 'comparable' || tiny || exceeded.length > 0 || limitExceeded.length > 0 || Boolean(b.value && !b.value.n && c.value?.n), power_pair: pair, evidence: { source: 'synthetic_memory_demo', notice, hierarchy_semantics: 'cumulative; non-overlapping paths only, never parent + child', hierarchy_separator: '/', threshold_exceeded: exceeded, operating_limit_exceeded: limitExceeded, tiny_denominator: tiny, policy_unconfigured: unconfigured, current_condition_key: current.condition_key, baseline_condition_key: baseline?.condition_key || null, arithmetic: 'exact rational BigInt; decimal display half-up at 30 fractional places', exact: { baseline: exactEvidence(b.value), current: exactEvidence(c.value), delta: exactEvidence(delta), relative_change_pct: exactEvidence(relative) } } }
}
function rejudge(comparison: TrackingRecord, changeIds: string[], recipients: string[], visible: Set<string>) {
  for (const id of changeIds) scopedRecord(id, 'change', visible)
  const run = record('ai_run', { ...scopeFromRecord(comparison), parent_id: comparison.id }, { source: 'synthetic_memory_demo', notice, change_ids: changeIds, recipients, model_called: false, mail_called: false, delivery_state: 'not_sent', verdict: 'needs_review', reason: '외부 AI를 호출하지 않았습니다. 선택한 원문 revision을 보존한 수동 검토 기록입니다.' }, 'needs_review')
  for (const result of records.values()) if (result.record_type === 'metric_result' && result.parent_id === comparison.id && result.payload.is_candidate === true) {
    const judgement = record('judgement', { ...scopeFromRecord(comparison), parent_id: run.id }, { metric_result_id: result.id, verdict: 'needs_review', model_called: false, reason: '합성 수치 검토 후보. AI 분석은 수행되지 않았으며 변경 원문만으로 설명 여부를 단정하지 않습니다.', change_ids: changeIds, source: 'synthetic_memory_demo' }, 'needs_review')
    const existing = [...records.values()].find(row => row.record_type === 'alert' && row.parent_id === comparison.id && row.payload.metric_result_id === result.id)
    if (existing) {
      existing.payload.judgement_id = judgement.id; existing.payload.change_ids = changeIds; existing.payload.ai_run_id = run.id
      touch(existing)
      record('alert_event', { ...scopeFromRecord(existing), parent_id: existing.id }, { action: 'rejudge', model_called: false, preserved_human_state: true, read_at: existing.read_at || null, acknowledged_at: existing.acknowledged_at || null, resolved_at: existing.resolved_at || null, ai_run_id: run.id })
    } else record('alert', { ...scopeFromRecord(comparison), parent_id: comparison.id }, { kind: 'needs_review', summary: `합성 검토 후보 · ${text(result.payload.hier_name)} / ${text(result.payload.metric)}`, reason: text(result.payload.reason), baseline: result.payload.baseline, current: result.payload.current, hier_name: result.payload.hier_name, metric: result.payload.metric, metric_result_id: result.id, judgement_id: judgement.id, ai_run_id: run.id, change_ids: changeIds, recipients, model_called: false, delivery_state: 'not_sent', source: 'synthetic_memory_demo', notice }, 'open')
  }
  comparison.payload.ai_state = 'needs_review'; touch(comparison)
  return run
}
function scopeFromRecord(row: TrackingRecord): Payload { return { project_id: row.project_id, block_id: row.block_id, version_id: row.version_id, snapshot_id: row.snapshot_id } }
function calculate(comparison: TrackingRecord, visible: Set<string>) {
  const input = object(comparison.payload.request)
  const current = scopedUpload(text(input.current_upload_id), visible)
  const baseline = input.baseline_upload_id ? scopedUpload(text(input.baseline_upload_id), visible) : null
  const policy = policyChecked(input.policy)
  const selectedGroups = comparisonGroups(input, current, baseline, visible)
  let count = 0
  const write = (group: Group, metric: string, cu: DemoUpload, bu: DemoUpload | null, pair: Payload | null) => {
    const payload = resultPayload(metric, group, cu, bu, policy, pair)
    record('metric_result', { ...scoped(current), parent_id: comparison.id }, payload, text(payload.comparability)); count++
  }
  for (const group of selectedGroups) for (const metric of group.metrics) write(group, metric, current, baseline, null)
  for (const pairValue of list(input.power_pairs)) {
    const pair = object(pairValue), cu = scopedUpload(text(pair.current_upload_id), visible), bu = pair.baseline_upload_id ? scopedUpload(text(pair.baseline_upload_id), visible) : null
    for (const group of selectedGroups) for (const metric of group.metrics.filter(key => powerKeys.includes(key))) write(group, metric, cu, bu, pair)
  }
  comparison.payload.coverage = { expected_scopes: String(selectedGroups.length), processed_scopes: String(selectedGroups.length), expected_metrics: String(count), processed_metrics: String(count), missing: [], aggregate_hierarchy_total: 'not_computed; cumulative rows remain independent' }
  comparison.payload.policy_unconfigured = !Object.keys(object(policy.metrics)).length && !Object.keys(object(policy.operating_limits)).length
  comparison.state = 'completed'; comparison.payload.numeric_state = 'completed'; touch(comparison)
  return record('job', { ...scoped(current), parent_id: comparison.id, job_kind: 'numeric', consumer_server: 'browser_memory', claim_owner: 'synthetic_inline_worker' }, { source: 'synthetic_memory_demo', notice, input: structuredClone(input), calculation: 'exact rational BigInt', result_count: String(count), real_queue: false }, 'completed')
}
function createComparison(input: Payload, visible: Set<string>) {
  const current = scopedUpload(text(input.current_upload_id), visible)
  const baseline = input.baseline_upload_id ? scopedUpload(text(input.baseline_upload_id), visible) : null
  if (baseline) ancestry(current, baseline, text(input.comparison_kind))
  else if (!['previous_version', 'previous_project'].includes(text(input.comparison_kind))) fail('지원하지 않는 비교 종류입니다.')
  policyChecked(input.policy)
  comparisonGroups(input, current, baseline, visible)
  for (const id of strings(input.change_ids)) scopedRecord(id, 'change', visible)
  for (const pairValue of list(input.power_pairs)) {
    const pair = object(pairValue), cu = scopedUpload(text(pair.current_upload_id), visible), bu = pair.baseline_upload_id ? scopedUpload(text(pair.baseline_upload_id), visible) : null
    if (cu.snapshot_id !== current.snapshot_id || bu && (!baseline || bu.snapshot_id !== baseline.snapshot_id)) fail('Power pair의 실행은 비교 양쪽의 명시적 snapshot에 속해야 합니다.')
  }
  const frozen = structuredClone(input)
  const comparison = record('comparison', { ...scoped(current), upload_id: current.id, baseline_upload_id: baseline?.id || null }, { request: frozen, source: 'synthetic_memory_demo', notice, input_revisions: { current_upload_id: current.id, baseline_upload_id: baseline?.id || null, mapping_id: input.mapping_id || null, target_ids: strings(object(input.scope).target_ids), change_ids: strings(input.change_ids) }, model_called: false, mail_called: false }, 'pending')
  const job = calculate(comparison, visible)
  rejudge(comparison, strings(input.change_ids), strings(input.recipients), visible)
  return { comparison, job, reused: false }
}
async function synchronize(): Promise<{ catalog: AtlasCatalog; visible: Set<string>; actor: string }> {
  const [catalog, session] = await Promise.all([workspaceDemoRequest<AtlasCatalog>('/atlas/catalog'), workspaceDemoRequest<{ user: { username: string; id: string } }>('/session')])
  const visible = new Set<string>()
  for (const project of catalog.projects) {
    if (!project.area_id) continue
    visible.add(project.area_id)
    if (!records.has(project.area_id)) record('project', { project_id: project.area_id }, { name: project.name, sanity_project_id: project.id, source: 'synthetic_memory_demo' }, 'active', project.area_id)
    for (const block of project.blocks) {
      if (!block.area_id) continue
      if (!records.has(block.area_id)) record('block', { project_id: project.area_id, block_id: block.area_id }, { name: block.name, sanity_block_id: block.id, source: 'synthetic_memory_demo' }, 'active', block.area_id)
      for (const version of block.versions) {
        if (!version.area_id) continue
        versions.set(version.area_id, version)
        const previous = block.versions.find(candidate => candidate.id === version.previous_version_id)
        if (!records.has(version.area_id)) record('version', { project_id: project.area_id, block_id: block.area_id, version_id: version.area_id }, { name: `${version.net_ver} · ${version.revision} · ${version.layout}`, sanity_version_id: version.id, previous_version_id: previous?.area_id || null, source_namespace: version.source_namespace, source: 'synthetic_memory_demo' }, 'active', version.area_id)
        // New sv-* catalog versions are prepared but intentionally have no inputs.
        if (version.id.startsWith('demo-')) seedVersion(project.area_id, block.area_id, version, block.name)
      }
    }
  }
  // All prepared versions are registered before comparisons follow native lineage.
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
function filterRows<T extends Payload>(rows: T[], params: URLSearchParams): T[] {
  const fields = ['project_id', 'block_id', 'version_id', 'snapshot_id', 'parent_id', 'entity_id', 'state', 'upload_id']
  return rows.filter(row => fields.every(key => !params.get(key) || text(row[key]) === params.get(key)))
}
function page<T>(rows: T[], params: URLSearchParams): T[] {
  const offset = Number(params.get('offset') || 0), limit = Number(params.get('limit') || 200)
  if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 1000) fail('잘못된 페이지 범위입니다.')
  return rows.slice(offset, offset + limit)
}
function config(body: Payload, visible: Set<string>) {
  const type = text(body.record_type)
  if (!['target', 'mapping', 'change'].includes(type)) fail('지원하는 설정은 target / mapping / change입니다.')
  validateScope(body, visible)
  const payload = structuredClone(object(body.payload))
  if (type === 'target') { if (!text(payload.name).trim()) fail('Target 이름이 필요합니다.'); checkedPaths(payload.paths); requestedMetrics(payload.metrics); if (!['node', 'subtree'].includes(text(payload.scope))) fail('Target scope는 node / subtree입니다.'); for (const value of list(payload.bindings)) { const binding = object(value); const snapshot = scopedRecord(text(binding.snapshot_id), 'snapshot', visible); if (snapshot.project_id !== body.project_id || body.block_id && snapshot.block_id !== body.block_id) fail('Target binding snapshot 범위가 다릅니다.'); checkedPaths(binding.paths) } }
  if (type === 'change' && !text(payload.text).trim()) fail('변경 원문이 필요합니다.')
  if (type === 'mapping') {
    for (const side of ['baseline', 'current']) validateScope({ project_id: payload[`${side}_project_id`], block_id: payload[`${side}_block_id`], version_id: payload[`${side}_version_id`] }, visible)
    if (payload.current_project_id !== body.project_id || body.block_id && payload.current_block_id !== body.block_id || body.version_id && payload.current_version_id !== body.version_id) fail('매핑 current UUID는 등록 범위와 일치해야 합니다.')
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
async function dispatch(path: string, body: Payload | undefined, plain: boolean | 'blob' | undefined): Promise<unknown> {
  const { catalog, visible, actor } = await synchronize()
  const url = new URL(path.replace(/^\/api\/area-power(?=\/|\?|$)/, '') || '/', 'https://synthetic.invalid')
  const route = url.pathname, params = url.searchParams
  const accessible = () => [...records.values()].filter(row => readable(row, visible))
  for (const key of ['project_id', 'block_id', 'version_id', 'snapshot_id']) if (params.get(key)) {
    const row = records.get(params.get(key)!)
    if (!row || !visible.has(text(row.project_id))) fail('선택 범위가 없거나 현재 계정에서 접근할 수 없습니다.')
  }
  if (route === '/access' && !body) return { actor, is_admin: false, local_mode: false, source: 'synthetic_memory_demo', notice, projects: catalog.projects.map(project => ({ id: project.id, name: project.name, blocks: project.blocks.map(block => ({ id: block.id, name: block.name })) })) }
  if (route === '/uploads' && !body) return { items: page(filterRows([...uploads.values()].filter(upload => visible.has(upload.project_id)).map(publicUpload), params), params) }
  const download = /^\/uploads\/([^/]+)\/download$/.exec(route)
  if (download && !body) { if (plain !== 'blob') fail('원본 다운로드는 blob 형식으로 요청하세요.'); return original(scopedUpload(decodeURIComponent(download[1]), visible)) }
  if (route === '/records' && !body) {
    const type = params.get('record_type')
    const supported = ['project', 'block', 'version', 'snapshot', 'target', 'mapping', 'change', 'selection', 'comparison', 'job', 'alert', 'alert_event', 'metric_result', 'judgement', 'ai_run']
    if (type && !supported.includes(type)) fail(`지원하지 않는 record_type: ${type}`)
    let rows = accessible().filter(row => !type || row.record_type === type)
    if (params.get('latest') !== 'false') rows = latest(rows)
    return { items: page(filterRows(rows, params), params) }
  }
  if (route === '/dashboard' && !body) {
    const rows = filterRows(accessible(), params)
    const scopedUploads = filterRows([...uploads.values()].filter(upload => visible.has(upload.project_id)).map(publicUpload), params)
    const selections = rows.filter(row => row.record_type === 'selection' && row.selection_scope === 'shared')
    const chosen = selections.length === 1 ? uploads.get(text(selections[0].upload_id)) : undefined
    const byType = (type: string) => rows.filter(row => row.record_type === type)
    return { uploads: scopedUploads, records: latest(rows), jobs: byType('job'), alerts: byType('alert'), comparisons: byType('comparison'), counts: { uploads: String(scopedUploads.length), comparisons: String(byType('comparison').length), open_alerts: String(byType('alert').filter(row => !row.resolved_at).length), pending_jobs: String(byType('job').filter(row => row.state === 'pending').length) }, root_metrics: chosen?.root_summary || null, source: 'synthetic_memory_demo', notice }
  }
  if (route === '/config' && body) return config(body, visible)
  if (route === '/selections' && body) {
    if (body.selection_scope !== 'shared') fail('현재 UI는 shared 선택만 지원합니다.')
    const upload = scopedUpload(text(body.upload_id), visible)
    let row = accessible().find(value => value.record_type === 'selection' && value.snapshot_id === upload.snapshot_id && value.condition_key === upload.condition_key && value.selection_scope === 'shared')
    if (row) { cas(row, body.expected_row_version); row.upload_id = upload.id; touch(row) }
    else { if (body.expected_row_version != null) fail('REVISION_CONFLICT · 선택이 존재하지 않습니다.'); row = record('selection', { ...scoped(upload), upload_id: upload.id, condition_key: upload.condition_key, selection_scope: 'shared' }, { source: 'synthetic_memory_demo', selected_by: actor }) }
    row.payload.selected_by = actor
    return row
  }
  if (route === '/comparisons' && body) return createComparison(body, visible)
  const detail = /^\/comparisons\/([^/]+)$/.exec(route)
  if (detail && !body) {
    const comparison = scopedRecord(decodeURIComponent(detail[1]), 'comparison', visible)
    const related = accessible().filter(row => row.parent_id === comparison.id)
    const runs = related.filter(row => row.record_type === 'ai_run')
    const runIds = new Set(runs.map(row => row.id))
    return { comparison, results: related.filter(row => row.record_type === 'metric_result'), ai_runs: runs, judgements: accessible().filter(row => row.record_type === 'judgement' && runIds.has(text(row.parent_id))) }
  }
  const judge = /^\/comparisons\/([^/]+)\/rejudge$/.exec(route)
  if (judge && body) {
    const comparison = scopedRecord(decodeURIComponent(judge[1]), 'comparison', visible)
    if (comparison.state !== 'completed') fail('수치 계산이 완료된 비교만 재검토할 수 있습니다.')
    return rejudge(comparison, strings(body.change_ids), strings(body.recipients), visible)
  }
  const alert = /^\/alerts\/([^/]+)\/state$/.exec(route)
  if (alert && body) {
    const row = scopedRecord(decodeURIComponent(alert[1]), 'alert', visible); cas(row, body.expected_row_version)
    const action = text(body.action)
    if (!['read', 'acknowledge', 'resolve'].includes(action)) fail('지원하지 않는 알림 상태 변경입니다.')
    if (row.resolved_at && action !== 'read') fail('이미 해소된 알림입니다.')
    const field = action === 'read' ? 'read_at' : action === 'acknowledge' ? 'acknowledged_at' : 'resolved_at'
    if (row[field]) fail('이미 처리된 알림 동작입니다.')
    row[field] = now(); if (action === 'resolve') row.state = 'resolved'; touch(row)
    record('alert_event', { ...scopeFromRecord(row), parent_id: row.id }, { action, actor, note: text(body.note), row_version: row.row_version, source: 'synthetic_memory_demo' })
    return row
  }
  const rerun = /^\/jobs\/([^/]+)\/rerun$/.exec(route)
  if (rerun && body) {
    const old = scopedRecord(decodeURIComponent(rerun[1]), 'job', visible); cas(old, body.expected_row_version)
    if (old.state !== 'failed' || old.job_kind !== 'numeric') fail('실패한 브라우저 수치 작업만 재실행할 수 있습니다. 외부 AI/메일은 실행하지 않습니다.')
    const previousComparison = scopedRecord(text(old.parent_id), 'comparison', visible)
    const created = createComparison(structuredClone(object(previousComparison.payload.request)), visible)
    created.job.previous_id = old.id; old.payload.rerun_job_id = created.job.id; touch(old)
    return { ...created, source: 'synthetic_memory_demo', previous_job_id: old.id }
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
