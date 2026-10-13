import type { Bundle, Coverage, Detail, EvidenceRequest, Group, Unit } from './types'

export function makeDemo() {
  const units: Unit[] = [
    ['numeric_limits', '수치 임계값 검사', 'FAIL', 1840],
    ['summary_checks', '실행 결과 요약', 'PASS', 312],
    ['object_mapping', '객체 대응 관계', 'PASS', 620],
    ['required_config', '필수 설정 확인', 'PASS', 48],
    ['mode_consistency', '모드 일관성', 'FAIL', 96],
    ['report_completeness', '보고서 완전성', 'UNKNOWN', 0],
    ['output_integrity', '산출물 무결성', 'PASS', 24],
    ['optional_check', '선택 항목', 'NOT_APPLICABLE', 0],
  ].map(([id, name, status, count], i) => ({
    review_unit_id: `u-${id}`, item_id: String(name), report: `reports/${id}.csv`,
    check_status: String(status), parse_status: i === 5 ? 'MISSING' : i === 7 ? 'NOT_APPLICABLE' : 'COMPLETE',
    execution_status: i === 5 ? 'NOT_RUN' : 'COMPLETED', applicable: i !== 7,
    analysis_ready: i !== 5, blockers: i === 5 ? ['SOURCE_REPORT_MISSING'] : [],
    record_count: Number(count), evaluated_count: Number(count), rule_version: '1',
    ...(i === 0 ? { parser: 'numeric_csv_v1', evaluation: { operator: 'le', limit: 10, unit: 'ns' }, condition: { mode: 'M1' }, identity_fields: ['object', 'check_point', 'mode'] } : {}),
    decision: [1, 2, 3].includes(i) ? { reviewer: '김민서', conclusion: 'PASS', reason: '전체 검사 결과와 원문 근거를 확인했습니다.', reviewed_at: '2026-10-12T08:20:00Z' } : null,
  }))
  let revision = 3
  const history: Detail['history'] = [...units.filter(u => u.decision).map((u, i) => ({ revision: i + 1, actor: '김민서', decisions: [{ ...u.decision!, review_unit_id: u.review_unit_id }] })).reverse(), { revision: 0, actor: 'system', decisions: [] }]
  const requests: EvidenceRequest[] = []
  const commandResults = new Map<string, unknown>()
  const commandBodies = new Map<string, string>()
  const coverage = (): Coverage => ({ expected: 8, reviewed: units.filter(u => u.decision).length, approved_na: 1, unresolved: units.filter(u => u.applicable && !u.decision).length, missing: 1, review_status: 'INCOMPLETE', check_status: 'FAIL', global_blockers: [] })
  const bundles = (): Bundle[] => [{ bundle_id: 'b-demo-20261012-chip-a', project: 'Atlas', run_id: 'sanity-20261012-042', created_at: '2026-10-12T08:42:00Z', review_revision: revision, coverage: coverage() }]
  const groups = (uid: string): Group[] => uid === 'u-numeric_limits' || uid === 'u-mode_consistency' ? [
    { finding_group_id: `g-${uid}-01`, review_unit_id: uid, message_code: 'CHECK-101', severity: 'WARNING', identity: { object: 'top/block_a/object_x', mode: 'M1', check_point: 'P1' }, identity_status: 'SAME_FINDING', occurrence_count: 18, examples_omitted: 16, rule_version: '1', examples: [
      { record_id: 'r-demo-001', file_id: 'f-numeric', line_start: 120, line_end: 120, raw: 'CHECK-101  object=top/block_a/object_x  mode=M1  observed=12.4  limit=10.0 ns', observed: 12.4, expected: 10, unit: 'ns' },
      { record_id: 'r-demo-002', file_id: 'f-numeric', line_start: 128, line_end: 128, raw: 'CHECK-101  object=top/block_a/object_x  mode=M1  observed=11.8  limit=10.0 ns', observed: 11.8, expected: 10, unit: 'ns' },
    ] },
  ] : []
  return async function demoApi(path: string, body?: Record<string, unknown>): Promise<unknown> {
    const url = new URL(path, 'http://demo.local')
    const p = url.pathname
    if (body?.request_id) {
      const key = String(body.request_id); const signature = JSON.stringify({ path: p, body })
      if (commandBodies.has(key) && commandBodies.get(key) !== signature) throw new Error('REQUEST_CONFLICT')
      commandBodies.set(key, signature)
    }
    const page = <T,>(items: T[]) => { const offset = Number(url.searchParams.get('offset') || 0); const limit = Number(url.searchParams.get('limit') || 20); return { items: items.slice(offset, offset + limit), total: items.length, offset, limit } }
    if (p === '/api/session') return { reviewer: '김민서', access_mode: 'demo', writable: true, notice: '합성 데이터 · 변경은 현재 화면 메모리에만 저장됩니다.' }
    if (p === '/api/bundles') return structuredClone(page(bundles()))
    if (p === '/api/receive') return { status: 'NO_NEW_BUNDLES' }
    if (p.endsWith('/reviews') && body) {
      const key = String(body.request_id)
      if (commandResults.has(key)) return commandResults.get(key)
      if (body.expected_revision !== revision) throw Object.assign(new Error('REVISION_CONFLICT'), { status: 409 })
      const unit = units.find(u => u.review_unit_id === body.unit_id)!
      if (!unit?.analysis_ready || !unit.applicable || coverage().global_blockers.length || (unit.check_status === 'FAIL' && body.conclusion === 'PASS')) throw new Error('REVIEW_BLOCKED')
      unit.decision = { reviewer: '김민서', conclusion: String(body.conclusion), reason: String(body.reason), reviewed_at: new Date().toISOString() }
      revision += 1
      history.unshift({ revision, actor: '김민서', decisions: [{ ...unit.decision, review_unit_id: unit.review_unit_id }] })
      const result = { status: 'REVIEWED', review_revision: revision }
      commandResults.set(key, result)
      return result
    }
    if (p.endsWith('/evidence') && body) {
      if (commandResults.has(String(body.request_id))) return commandResults.get(String(body.request_id))
      requests.unshift({ request_id: String(body.request_id), review_unit_id: String(body.unit_id), record_ids: body.record_ids as string[], reason: String(body.reason), status: 'PENDING', created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 3600000).toISOString(), response_status: null, result: null })
      const result = { status: 'REQUESTED' }; commandResults.set(String(body.request_id), result); return result
    }
    if (p.startsWith('/api/evidence/')) {
      const id = p.split('/')[3]; const request = requests.find(r => r.request_id === id)!
      if (p.endsWith('/receive')) { request.status = 'RECEIVED'; request.response_status = 'RECEIVED'; request.result = 'FULFILLED'; return { status: 'RECEIVED', record_count: request.record_ids.length } }
      return { result: 'FULFILLED', record_count: request.record_ids.length, records: request.record_ids.map(record_id => ({ record_id, raw: `[합성 근거] ${record_id}: 전체 검사값 12.4 ns / 허용값 10.0 ns`, raw_truncated: false })) }
    }
    if (p.endsWith('/requests')) return structuredClone(page(requests))
    if (p.endsWith('/groups')) return page(groups(decodeURIComponent(p.split('/')[5])))
    if (p.endsWith('/report')) return '# Sanity 검토 보고서 (데모)\n\n합성 데이터입니다.\n\n' + units.map(u => `- ${u.item_id}: ${u.decision?.conclusion || u.check_status}`).join('\n')
    if (p.startsWith('/api/bundles/')) {
      const offset = Number(url.searchParams.get('unit_offset') || 0); const limit = Number(url.searchParams.get('unit_limit') || 50)
      return structuredClone({ bundle_id: bundles()[0].bundle_id, run: { run_id: bundles()[0].run_id, project: 'Atlas', design: 'chip_a', stage: 'sanity', tool: 'synthetic', tool_version: '1.0' }, review_revision: revision, coverage: coverage(), units: units.slice(offset, offset + limit), unit_total: units.length, unit_offset: offset, unit_limit: limit, history, history_total: history.length, context: { ai: { status: 'NOT_CONFIGURED' } } })
    }
    throw new Error('NOT_FOUND')
  }
}
