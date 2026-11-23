import { add, compare, display, divide, fraction, magnitude, multiply, subtract, type Fraction } from '../fraction'
import type { Payload, TrackingRecord } from '../api'
import {
  ancestry, checkedPaths, decimal, descends, designatedBaseline, exactEvidence, fail, latest,
  list, metricKeys, nonoverlap, notice, object, policyChecked, powerKeys, publicUpload, record,
  records, requestedMetrics, scoped, scopedRecord, scopedUpload, scopeFromRecord, strings, sum,
  text, touch, type DemoUpload,
} from './demoStore'

export const TRIAGE_ENGINE = 'instance-triage-v1'

function metricValue(upload: DemoUpload | null, paths: string[], metric: string): { value: Fraction | null; numerator: Fraction | null; denominator: Fraction | null } {
  const missing = { value: null, numerator: null, denominator: null }
  if (!upload || !paths.length || paths.some(path => !upload.rows[path])) return missing
  const total = (key: string) => sum(paths.map(path => upload.rows[path][key]))
  if (metric.endsWith('_pct')) {
    const numerator = metric === 'cts_area_pct' ? total('cts_area') : paths.reduce((value, path) => add(value, divide(multiply(decimal(upload.rows[path][metric]), decimal(upload.rows[path].logic_area)), fraction(100n))), fraction(0n))
    const denominator = total('logic_area')
    return { value: denominator.n ? multiply(divide(numerator, denominator), fraction(100n)) : null, numerator, denominator }
  }
  return { value: total(metric), numerator: null, denominator: null }
}

type Group = { current: string[]; baseline: string[]; metrics: string[]; target_id?: string; reason?: string; declared_kind?: string }

function comparisonGroups(input: Payload, current: DemoUpload, baseline: DemoUpload | null, visible: Set<string>): Group[] {
  const scope = object(input.scope)
  const mapping = input.mapping_id ? scopedRecord(text(input.mapping_id), 'mapping', visible) : null
  if (mapping) {
    const p = mapping.payload
    if (p.current_project_id !== current.project_id || p.current_version_id !== current.version_id || p.current_block_id !== current.block_id || !baseline || p.baseline_project_id !== baseline.project_id || p.baseline_version_id !== baseline.version_id || p.baseline_block_id !== baseline.block_id) fail('매핑 revision의 명시적 양쪽 UUID가 비교 입력과 다릅니다.')
  }
  const rules = list(mapping?.payload.rules).map(object)
  const exactCurrent = new Map<string, Group[]>(), exactBaseline = new Map<string, Group[]>()
  const subtreeCurrent = new Map<string, Payload[]>(), subtreeBaseline = new Map<string, Payload[]>()
  const groups: Group[] = [], consumedCurrent = new Set<string>(), consumedBaseline = new Set<string>()
  for (const rule of rules) {
    const before = strings(rule.baseline_paths), after = strings(rule.current_paths)
    const group: Group = { baseline: before, current: after, metrics: metricKeys, declared_kind: text(rule.kind) }
    if (before.some(path => !baseline?.rows[path]) || after.some(path => !current.rows[path])) group.reason = 'mapping_path_missing'
    if (rule.scope === 'subtree' && before.length === 1 && after.length === 1 && !group.reason) {
      subtreeCurrent.set(after[0], [...(subtreeCurrent.get(after[0]) || []), rule])
      subtreeBaseline.set(before[0], [...(subtreeBaseline.get(before[0]) || []), rule])
    } else {
      groups.push(group)
      for (const path of after) exactCurrent.set(path, [...(exactCurrent.get(path) || []), group])
      for (const path of before) exactBaseline.set(path, [...(exactBaseline.get(path) || []), group])
    }
  }
  for (const group of groups) {
    if (group.current.some(path => (exactCurrent.get(path)?.length || 0) > 1) || group.baseline.some(path => (exactBaseline.get(path)?.length || 0) > 1)) group.reason = 'mapping_equal_precedence_conflict'
    group.current.forEach(path => consumedCurrent.add(path)); group.baseline.forEach(path => consumedBaseline.add(path))
  }
  const nearest = (index: Map<string, Payload[]>, path: string) => {
    let candidate = path
    while (candidate) { const found = index.get(candidate); if (found) return found; const offset = candidate.lastIndexOf('/'); candidate = offset < 0 ? '' : candidate.slice(0, offset) }
    return []
  }
  const root = (upload: DemoUpload) => Object.keys(upload.rows).find(path => !path.includes('/')) || ''
  const implied = (path: string) => !baseline ? '' : baseline.project_id === current.project_id ? path : root(baseline) + path.slice(root(current).length)
  for (const path of Object.keys(current.rows)) {
    if (consumedCurrent.has(path)) continue
    const counterpart = implied(path)
    const matches = nearest(subtreeCurrent, path)
    let group: Group
    if (matches.length) {
      const rule = matches[0], source = strings(rule.current_paths)[0], before = strings(rule.baseline_paths)[0] + path.slice(source.length)
      const inverse = nearest(subtreeBaseline, before)
      const inverseRule = inverse[0]
      const destination = inverseRule ? strings(inverseRule.current_paths)[0] + before.slice(strings(inverseRule.baseline_paths)[0].length) : ''
      const conflict = matches.length !== 1 || inverse.length !== 1 || destination !== path || exactBaseline.has(before) || consumedBaseline.has(before)
      group = { current: [path], baseline: [before], metrics: metricKeys, reason: conflict ? 'mapping_equal_precedence_conflict' : !baseline?.rows[before] ? 'mapping_path_missing' : undefined }
    } else if (baseline?.rows[counterpart] && !exactBaseline.has(counterpart) && !consumedBaseline.has(counterpart) && !nearest(subtreeBaseline, counterpart).length) {
      group = { current: [path], baseline: [counterpart], metrics: metricKeys }
    } else group = { current: [path], baseline: [], metrics: metricKeys, reason: baseline ? 'mapping_unresolved' : 'baseline_missing' }
    groups.push(group); consumedCurrent.add(path); group.baseline.forEach(value => consumedBaseline.add(value))
  }
  for (const path of Object.keys(baseline?.rows || {})) if (!consumedBaseline.has(path)) groups.push({ current: [], baseline: [path], metrics: metricKeys, reason: 'mapping_unresolved' })
  for (const group of [...groups]) {
    if (group.current.length > 1) for (const path of group.current) groups.push({ current: [path], baseline: [], metrics: metricKeys, reason: 'individual_group_baseline_unavailable' })
    if (group.baseline.length > 1) for (const path of group.baseline) groups.push({ current: [], baseline: [path], metrics: metricKeys, reason: 'individual_group_current_unavailable' })
  }
  if (scope.kind === 'targets') {
    const ids = strings(scope.target_ids)
    if (!ids.length) fail('Target 범위는 하나 이상의 정확한 revision이 필요합니다.')
    const byCurrentPath = new Map(groups.filter(group => group.current.length === 1).map(group => [group.current[0], group]))
    return ids.flatMap(id => {
      const target = scopedRecord(id, 'target', visible)
      if (target.project_id !== current.project_id || target.block_id && target.block_id !== current.block_id) fail('Target revision이 현재 범위에 속하지 않습니다.')
      const binding = list(target.payload.bindings).map(object).find(value => value.snapshot_id === current.snapshot_id)
      const paths = checkedPaths(binding?.paths || target.payload.paths)
      const selected = scope.include_descendants ? groups.filter(group => group.current.some(path => paths.some(parent => path === parent || descends(path, parent)))) : [{ current: paths, baseline: paths.flatMap(path => byCurrentPath.get(path)?.baseline || []), metrics: metricKeys, reason: paths.some(path => byCurrentPath.get(path)?.reason || !byCurrentPath.has(path)) ? 'mapping_unresolved' : undefined }]
      return selected.map(group => ({ ...group, target_id: id, metrics: requestedMetrics(target.payload.metrics) }))
    })
  }
  if (scope.kind && scope.kind !== 'all') fail('지원하지 않는 비교 범위입니다.')
  return groups
}

function resultPayload(metric: string, group: Group, current: DemoUpload, baseline: DemoUpload | null, policy: Payload, pair: Payload | null): Payload {
  const invalidOverlap = nonoverlap(group.current).length !== group.current.length || nonoverlap(group.baseline).length !== group.baseline.length
  const c = metricValue(current, invalidOverlap ? [] : group.current, metric), b = metricValue(baseline, invalidOverlap ? [] : group.baseline, metric)
  let comparability = 'comparable', reason = ''
  if (!baseline || !b.value || !c.value || group.reason || invalidOverlap) { comparability = 'unavailable'; reason = invalidOverlap ? 'overlapping_cumulative_paths' : group.reason || (!baseline ? 'baseline_missing' : 'metric_or_path_missing') }
  else {
    if (powerKeys.includes(metric)) {
      if (current.condition_key !== baseline.condition_key) { comparability = 'contextual'; reason = 'power_conditions_different' }
    } else {
      const checks = ['design_stage', 'constraint_context', 'library', 'classification', ...((metric.endsWith('_area') || metric.endsWith('_pct')) ? ['area_unit', 'area_basis'] : [])]
      if (checks.some(key => JSON.stringify(current.metadata[key]) !== JSON.stringify(baseline.metadata[key]))) { comparability = 'contextual'; reason = 'structural_metadata_different' }
    }
  }
  const delta = comparability !== 'unavailable' && c.value && b.value ? subtract(c.value, b.value) : null
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

export function rejudge(comparison: TrackingRecord, changeIds: string[], recipients: string[], visible: Set<string>) {
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

function calculate(comparison: TrackingRecord, visible: Set<string>, job?: TrackingRecord) {
  const input = object(comparison.payload.request)
  const current = scopedUpload(text(input.current_upload_id), visible)
  const baseline = input.baseline_upload_id ? scopedUpload(text(input.baseline_upload_id), visible) : null
  const policy = policyChecked(input.policy)
  const selectedGroups = comparisonGroups(input, current, baseline, visible)
  let count = 0
  const write = (group: Group, metric: string, cu: DemoUpload, bu: DemoUpload | null, pair: Payload | null) => {
    const payload = resultPayload(metric, group, cu, bu, policy, pair)
    record('metric_result', { ...scoped(cu), upload_id: cu.id, baseline_upload_id: bu?.id || null, parent_id: comparison.id }, { ...payload, group_id: JSON.stringify([group.baseline, group.current, group.target_id || null]), power_pair: powerKeys.includes(metric) ? { current_upload_id: cu.id, baseline_upload_id: bu?.id || null } : null }, text(payload.comparability)); count++
  }
  for (const group of selectedGroups) for (const metric of group.metrics) write(group, metric, current, baseline, null)
  for (const pairValue of list(input.power_pairs)) {
    const pair = object(pairValue), cu = scopedUpload(text(pair.current_upload_id), visible), bu = pair.baseline_upload_id ? scopedUpload(text(pair.baseline_upload_id), visible) : null
    for (const group of selectedGroups) for (const metric of group.metrics.filter(key => powerKeys.includes(key))) write(group, metric, cu, bu, pair)
  }
  comparison.payload.coverage = { expected_scopes: String(selectedGroups.length), processed_scopes: String(selectedGroups.length), expected_metrics: String(count), processed_metrics: String(count), missing: [], aggregate_hierarchy_total: 'not_computed; cumulative rows remain independent' }
  comparison.payload.policy_unconfigured = !Object.keys(object(policy.metrics)).length && !Object.keys(object(policy.operating_limits)).length
  comparison.state = 'completed'; comparison.payload.numeric_state = 'completed'; touch(comparison)
  if (job) { job.state = 'completed'; job.payload.result_count = String(count); touch(job); return job }
  return record('job', { ...scoped(current), parent_id: comparison.id, job_kind: 'compare', consumer_server: 'browser_memory', claim_owner: 'synthetic_memory_worker' }, { source: 'synthetic_memory_demo', notice, input: structuredClone(input), calculation: 'exact rational BigInt', result_count: String(count), real_queue: false }, 'completed')
}

export function createComparison(input: Payload, visible: Set<string>) {
  const current = scopedUpload(text(input.current_upload_id), visible)
  const baseline = input.baseline_upload_id ? scopedUpload(text(input.baseline_upload_id), visible) : null
  if (baseline) ancestry(current, baseline, text(input.comparison_kind))
  else if (!['previous_version', 'previous_project'].includes(text(input.comparison_kind))) fail('지원하지 않는 비교 종류입니다.')
  const designation = input.comparison_kind === 'previous_project' ? designatedBaseline(current.project_id, visible, baseline?.project_id) : null
  policyChecked(input.policy)
  comparisonGroups(input, current, baseline, visible)
  for (const id of strings(input.change_ids)) scopedRecord(id, 'change', visible)
  for (const pairValue of list(input.power_pairs)) {
    const pair = object(pairValue), cu = scopedUpload(text(pair.current_upload_id), visible), bu = pair.baseline_upload_id ? scopedUpload(text(pair.baseline_upload_id), visible) : null
    if (cu.snapshot_id !== current.snapshot_id || bu && (!baseline || bu.snapshot_id !== baseline.snapshot_id)) fail('Power pair의 실행은 비교 양쪽의 명시적 snapshot에 속해야 합니다.')
  }
  const frozen = structuredClone(input)
  const comparison = record('comparison', { ...scoped(current), upload_id: current.id, baseline_upload_id: baseline?.id || null }, { request: frozen, frozen: { current_upload: publicUpload(current), baseline_upload: baseline ? publicUpload(baseline) : null, mapping: input.mapping_id ? structuredClone(scopedRecord(text(input.mapping_id), 'mapping', visible)) : null, baseline_designation: designation }, source: 'synthetic_memory_demo', notice, input_revisions: { current_upload_id: current.id, baseline_upload_id: baseline?.id || null, mapping_id: input.mapping_id || null, target_ids: strings(object(input.scope).target_ids), change_ids: strings(input.change_ids) }, model_called: false, mail_called: false }, 'pending')
  comparison.state = 'queued'
  const job = record('job', { ...scoped(current), parent_id: comparison.id, job_kind: 'compare', consumer_server: 'browser_memory' }, { source: 'synthetic_memory_demo', notice, real_queue: false, model_called: false }, 'pending')
  window.setTimeout(() => {
    try {
      job.state = 'running'; comparison.state = 'running'
      calculate(comparison, visible, job)
      rejudge(comparison, strings(input.change_ids), strings(input.recipients), visible)
    } catch (cause) {
      job.state = 'failed'; job.payload.error = { code: 'SYNTHETIC_CALCULATION_FAILED', message: cause instanceof Error ? cause.message : '합성 수치 계산에 실패했습니다.' }; touch(job)
      if (comparison.state !== 'completed') comparison.state = 'failed'
    }
  }, 0)
  return { comparison, job, reused: false }
}

export function triageSummary(run: TrackingRecord, actor: string) {
  return { can_delete: run.payload.requested_by === actor, id: run.id, state: run.state, created_at: run.created_at, error_detail: run.payload.error ?? null, payload: run.payload, note_count: list(run.payload.notes).length }
}

export function triageSelection(comparison: TrackingRecord, thresholds: Payload, visible: Set<string>) {
  if (comparison.state !== 'completed') fail('수치 계산이 완료된 비교만 분석할 수 있습니다.')
  const input = object(comparison.payload.request)
  if (!input.baseline_upload_id) fail('AI 분석에는 기준 실행이 필요합니다.')
  const current = scopedUpload(text(input.current_upload_id), visible), baseline = scopedUpload(text(input.baseline_upload_id), visible)
  const limit = (key: string, fallback: number) => { const value = Number(text(thresholds[key]) || fallback); if (!Number.isFinite(value) || value < 0) fail('기준 숫자는 0 이상이어야 합니다.'); return value }
  const areaLimit = limit('area_increase_pct', 10), powerLimit = limit('power_increase_pct', 10), minArea = limit('min_area', 50)
  const groups = new Map<string, { id: string; baseline: string[]; current: string[]; metrics: Record<string, Payload> }>()
  for (const row of records.values()) if (row.record_type === 'metric_result' && row.parent_id === comparison.id) {
    const id = text(row.payload.group_id), group = groups.get(id) || { id, baseline: strings(row.payload.baseline_paths), current: strings(row.payload.current_paths), metrics: {} }
    group.metrics[text(row.payload.metric)] = row.payload; groups.set(id, group)
  }
  const scopedRows = (type: string) => latest([...records.values()].filter(row => row.record_type === type && row.project_id === current.project_id && row.block_id === current.block_id))
  const watched = [...new Set(scopedRows('target').filter(row => row.payload.watch === true).flatMap(row => strings(row.payload.paths)))]
  const kind = text(input.comparison_kind)
  const notes = scopedRows('change').filter(row => row.payload.archived !== true && (row.payload.note_kind === 'net_ver' || kind === 'previous_project' && row.payload.note_kind === 'project_change' && row.payload.baseline_project_id === baseline.project_id))
    .map(row => ({ id: row.id, kind: text(row.payload.note_kind), net_ver: row.payload.net_ver ?? null, text: text(row.payload.text), revision: row.revision }))
  const process = { current: records.get(current.project_id)?.payload.process ?? null, baseline: records.get(baseline.project_id)?.payload.process ?? null }
  const sameCorner = current.condition_key === baseline.condition_key
  const library = !sameCorner ? 'different' : current.project_id === baseline.project_id ? 'same' : process.current && process.baseline ? process.current === process.baseline ? 'same' : 'different' : 'unknown'
  const context = { same_project: current.project_id === baseline.project_id, same_corner: sameCorner, library,
    area: { analyzed: true, unconfirmed: false, reason: null }, power: sameCorner ? { analyzed: true, unconfirmed: false, reason: null } : { analyzed: false, unconfirmed: false, reason: 'corner_different' } }
  const number = (value: unknown) => value == null || value === '' ? null : Number(text(value))
  const cards: Payload[] = []
  const counts = { area_increase: 0, power_increase: 0, new_instance: 0, watched: 0, rolled_up: 0, total: 0 }
  for (const group of groups.values()) {
    const area = object(group.metrics.logic_area), power = object(group.metrics.pwr_all_total_mw)
    const size = Math.max(number(area.baseline) ?? 0, number(area.current) ?? 0)
    const reasons: string[] = []
    if (group.baseline.length && group.current.length && size >= minArea) {
      if ((number(area.relative_change_pct) ?? -Infinity) >= areaLimit) reasons.push('area_increase')
      if (context.power.analyzed && (number(power.relative_change_pct) ?? -Infinity) >= powerLimit) reasons.push('power_increase')
    }
    if (!group.baseline.length && group.current.length && size >= minArea) reasons.push('new_instance')
    const isWatched = group.current.some(path => watched.includes(path))
    if (!reasons.length && !isWatched) continue
    for (const reason of reasons) counts[reason as 'area_increase'] += 1
    if (isWatched) counts.watched += 1
    const shares = (prefix: string) => Object.entries(group.metrics).filter(([metric, value]) => metric.startsWith(prefix) && metric.endsWith('_pct') && value.pct_point_change != null && Number(text(value.pct_point_change)) !== 0)
      .map(([metric, value]) => ({ name: metric.replace(/_pct$/, ''), baseline_pct: value.baseline, current_pct: value.current, delta_pp: value.pct_point_change }))
    cards.push({ id: group.id, current_path: group.current.join(' + ') || null, baseline_path: group.baseline.join(' + ') || null, reasons, watched: isWatched, notable: reasons.length > 0,
      flags: library === 'same' ? [] : [`library_${library}`], area: { baseline: area.baseline, current: area.current, delta: area.delta, delta_pct: area.relative_change_pct },
      power_mw: context.power.analyzed ? { baseline: power.baseline, current: power.current, delta: power.delta, delta_pct: power.relative_change_pct } : { excluded: 'corner_different' },
      vth: shares('vt_'), drive: shares('drv_'), rolled_up_ancestors: [], top_children: [], ancestor_ids: [] })
  }
  counts.total = cards.length
  const known = new Set([...groups.values()].flatMap(group => group.current))
  const missing = watched.filter(path => !known.has(path))
  const applied = { area_increase_pct: String(areaLimit), power_increase_pct: String(powerLimit), min_area: String(minArea) }
  const preview = { thresholds: applied, counts, estimated_calls: 0, max_items: 400, over_limit: cards.length > 400, missing_watched: missing, watched_paths: watched, context,
    notes: notes.map(({ text: _text, ...note }) => note), note_warnings: [], same_rtl: false, applicable_net_vers: [...new Set(notes.map(note => text(note.net_ver)).filter(Boolean))], process }
  return { preview, cards, notes, context, counts, missing, thresholds: applied, kind }
}

export function triageRun(comparison: TrackingRecord, body: Payload, visible: Set<string>, actor: string) {
  const selection = triageSelection(comparison, object(body.thresholds), visible)
  if (selection.preview.over_limit) fail('분석 대상이 400개를 넘습니다. 기준을 올리세요.')
  const reason = 'synthetic_demo_model_not_called'
  const run = record('ai_run', { ...scopeFromRecord(comparison), parent_id: comparison.id }, { engine: TRIAGE_ENGINE, retry_limit: 5, thresholds: selection.thresholds, notes: selection.notes, note_warnings: [], same_rtl: false,
    watched_paths: selection.preview.watched_paths, comparison_kind: selection.kind, labels: object(body.labels), requested_by: actor, side_context: selection.context, counts: selection.counts,
    missing_watched: selection.missing, coverage: { expected_items: selection.cards.length, processed_items: selection.cards.length, failed_items: selection.cards.length, batches: 0, processed_batches: 0 },
    error: selection.cards.length ? { code: reason } : null, model_called: false, source: 'synthetic_memory_demo', notice }, selection.cards.length ? 'failed' : 'completed')
  for (const card of selection.cards) record('judgement', { ...scopeFromRecord(comparison), parent_id: run.id }, { group_id: card.id, card, verdict: 'needs_review', cause: 'unknown',
    explanation: '합성 데모는 AI를 호출하지 않습니다. 후보 선별 결과만 표시하며 판정은 확인 필요로 남깁니다.', note_ids: [], quotes: [], failed: true, failure: reason, adjusted: [], model_called: false }, 'needs_review')
  return { ai_run: run, job: null }
}
