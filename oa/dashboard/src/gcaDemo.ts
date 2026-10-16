import type { Bundle, Coverage, DemoReport, Detail, EvidenceRequest, Example, Group, Unit } from './types'

// Original fixtures, informed by published documentation. No vendor report is copied.
// This is an in-memory UI adapter, not a parser or an emulation of PrimeTime.
const guide = 'https://www.scribd.com/document/807828202/PrimeTime-GCA-User-Guide-2016'
const whitepaper = 'https://www.synopsys.com/content/dam/synopsys/implementation%26signoff/white-papers/GCA_wp.pdf'
const sources: DemoReport['sources'] = [
  { title: 'Synopsys GCA 백서 · 2010', url: whitepaper, section: 'pp. 2, 6–8 · 제약조건 검사와 디버깅 사례' },
  { title: 'PrimeTime GCA User Guide · L-2016.06 공개 열람본', url: guide, section: 'Chapter 2, Example 2-8 · Appendix A' },
]

type Seed = { id: string; project: string; block: string; scenario: string; date: string; older?: boolean; missing?: boolean }
const seeds: Seed[] = [
  { id: 'atlas-cpu-1013', project: 'Atlas', block: 'CPU', scenario: 'FUNC_SS', date: '2026-10-13T02:40:00Z' },
  { id: 'atlas-cpu-1012', project: 'Atlas', block: 'CPU', scenario: 'FUNC_SS', date: '2026-10-12T02:40:00Z', older: true },
  { id: 'atlas-gpu-1013', project: 'Atlas', block: 'GPU', scenario: 'SCAN_FF', date: '2026-10-13T02:30:00Z' },
  { id: 'boreal-cpu-1013', project: 'Boreal', block: 'CPU', scenario: 'FUNC_SS', date: '2026-10-13T02:20:00Z' },
  { id: 'boreal-ddr-1013', project: 'Boreal', block: 'DDR', scenario: 'FUNC_SS', date: '2026-10-13T02:10:00Z', missing: true },
]
const rules = [
  { id: 'clock_source', label: 'GCA · 생성 클록 소스', code: 'CLK_0003', severity: 'ERROR', summary: '생성 클록의 source에 master clock이 도달하지 않는 사례.', object: 'u_clk/div2/CP', fields: 'clock=core_div2 master_reaches_source=no', count: 4 },
  { id: 'case_conflict', label: 'GCA · Case 값 충돌', code: 'CAS_0003', severity: 'ERROR', summary: '직접 지정한 case 값과 논리를 통해 전달된 값이 다른 사례.', object: 'u_mode/mux/S', fields: 'user_case=0 propagated_case=1', count: 3 },
  { id: 'clock_as_data', label: 'GCA · 클록의 데이터 사용', code: 'CLK_0026', severity: 'WARNING', summary: '클록 신호가 데이터 입력에 연결되어 설계 의도 확인이 필요한 사례.', object: 'u_debug/sample/D', fields: 'clock=debug_clk destination=data_pin', count: 2 },
  { id: 'io_window', label: 'GCA · 입력 지연 범위', code: 'EXD_0012', severity: 'ERROR', summary: '입력 지연의 min/max가 같아 시간 범위가 없는 사례.', object: 'request_valid', fields: 'input_delay_min=0.35 input_delay_max=0.35 unit=ns', count: 2 },
  { id: 'block_case', label: 'GCA · Block-to-top case', code: 'B2T_CAS_0001', severity: 'ERROR', summary: '상위 인스턴스에 존재하는 case 값이 블록 제약에는 없는 사례.', object: 'scan_enable', fields: 'top_case=0 block_case=unset', count: 2 },
]

function createRun(seed: Seed) {
  const bundleId = `b-gca-${seed.id}`
  const path = `soc/${seed.block.toLowerCase()}`
  const groups = new Map<string, Group[]>()
  const records = new Map<string, Example>()
  const units: Unit[] = rules.map((rule) => {
    const uid = `u-${seed.id}-${rule.id}`
    const missing = !!seed.missing && rule.id === 'block_case'
    // Distinct scopes deliberately include both fixed and unresolved cases.
    const pass = !missing && (seed.project === 'Boreal' && seed.block === 'CPU'
      || rule.id === 'io_window' && !seed.older && seed.block !== 'DDR'
      || rule.id === 'case_conflict' && seed.block === 'GPU')
    const count = missing ? 0 : rule.count + (seed.older ? 1 : 0)
    const report = `demo/gca/${seed.project}/${seed.block}/${seed.id}/${rule.id}.rpt`
    const header = [
      '# GCA example report / 데모용 예시 보고서',
      '# Original demo format; not native PrimeTime output. No tool was executed.',
      `project=${seed.project} block=${seed.block} run=${seed.id}`,
      `scenario=${seed.scenario} item=${rule.id} rule=${rule.code}`,
      '# status | record_id | object | context',
    ]
    const examples: Example[] = []
    for (let n = 0; n < count; n++) {
      const recordId = `r-${seed.id}-${rule.id}-${n + 1}`
      const object = `${path}/${rule.object.replace(/\//, `_${n}/`)}`
      const context = pass ? `demo_check=clear scenario=${seed.scenario}` : `${rule.fields} scenario=${seed.scenario}`
      const raw = `${pass ? 'PASS' : 'FAIL'} | ${recordId} | ${object} | ${context}`
      const example = { record_id: recordId, file_id: report, line_start: header.length + n + 1, line_end: header.length + n + 1, raw }
      examples.push(example); records.set(recordId, example)
    }
    groups.set(uid, pass || missing ? [] : [{
      finding_group_id: `g-${seed.id}-${rule.id}`, review_unit_id: uid, message_code: rule.code,
      severity: rule.severity, identity: { project: seed.project, block: seed.block, scenario: seed.scenario, rule: rule.code },
      identity_status: 'DEMO_RULE_BUCKET', occurrence_count: count, examples: examples.slice(0, 2),
      examples_omitted: Math.max(0, count - 2), rule_version: 'demo-1',
    }])
    return {
      review_unit_id: uid, item_id: rule.label, report,
      check_status: missing ? 'UNKNOWN' : pass ? 'PASS' : 'FAIL',
      parse_status: missing ? 'MISSING' : 'COMPLETE', execution_status: missing ? 'NOT_RUN' : 'COMPLETED',
      applicable: true, analysis_ready: !missing, blockers: missing ? ['SOURCE_REPORT_MISSING'] : [],
      record_count: count, evaluated_count: count, rule_version: 'demo-1',
      condition: { project: seed.project, block: seed.block, scenario: seed.scenario },
      evaluation: { rule: rule.code, 기준: '데모에서 지정한 개별 결과 · 실제 도구 판정 아님' },
      identity_fields: ['project', 'block', 'scenario', 'rule'], decision: null,
      demo_report: { summary: rule.summary, sources, text: missing ? null : [...header, ...examples.map(e => e.raw),
        `# checked=${count} findings=${pass ? 0 : count} status=${pass ? 'PASS' : 'FAIL'}`].join('\n') },
    }
  })
  return { seed, bundleId, units, groups, records, revision: 0,
    history: [{ revision: 0, actor: 'demo', decisions: [] }] as Detail['history'], requests: [] as EvidenceRequest[] }
}

export function makeGcaDemo() {
  const runs = seeds.map(createRun)
  const commands = new Map<string, { signature: string; result: unknown }>()
  function coverage(run: typeof runs[number]): Coverage {
    const reviewed = run.units.filter(u => u.decision).length
    const missing = run.units.filter(u => !u.analysis_ready).length
    return { expected: run.units.length, reviewed, approved_na: 0, unresolved: run.units.length - reviewed, missing,
      review_status: reviewed === run.units.length && !missing ? 'COMPLETE' : 'INCOMPLETE',
      check_status: run.units.some(u => u.check_status === 'FAIL') ? 'FAIL' : missing ? 'UNKNOWN' : 'PASS', global_blockers: [] }
  }
  const bundle = (run: typeof runs[number]): Bundle => ({ bundle_id: run.bundleId, project: run.seed.project, design: run.seed.block,
    run_id: `gca-${run.seed.id}`, created_at: run.seed.date, review_revision: run.revision, coverage: coverage(run) })
  return async function demoApi(path: string, body?: Record<string, unknown>): Promise<unknown> {
    const url = new URL(path, 'http://demo.local')
    const parts = url.pathname.split('/').filter(Boolean).map(decodeURIComponent)
    const signature = JSON.stringify({ path: url.pathname, body })
    const key = body?.request_id ? String(body.request_id) : ''
    const previous = commands.get(key)
    if (previous) {
      if (previous.signature !== signature) throw new Error('REQUEST_CONFLICT')
      return structuredClone(previous.result)
    }
    const finish = (result: unknown) => { if (key) commands.set(key, { signature, result }); return structuredClone(result) }
    const page = <T,>(items: T[]) => {
      const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 20)
      return structuredClone({ items: items.slice(offset, offset + limit), total: items.length, offset, limit })
    }
    if (url.pathname === '/api/session') return { reviewer: '데모 검토자', access_mode: 'demo', writable: true,
      notice: '공개 GCA 문서를 참고한 예시 검사 결과 · 프로젝트·객체·수치·판정은 가상이며 실제 PrimeTime/AI 실행 결과가 아닙니다.' }
    if (url.pathname === '/api/bundles') return page(runs.map(bundle))
    if (url.pathname === '/api/receive') return { status: 'NO_NEW_BUNDLES' }
    if (parts[1] === 'evidence') {
      const run = runs.find(r => r.requests.some(request => request.request_id === parts[2]))
      const request = run?.requests.find(r => r.request_id === parts[2])
      if (!run || !request) throw new Error('REQUEST_NOT_FOUND')
      if (parts[3] === 'receive' && body) {
        request.status = 'RECEIVED'; request.response_status = 'RECEIVED'; request.result = 'FULFILLED'
        return finish({ status: 'RECEIVED', record_count: request.record_ids.length })
      }
      if (request.response_status !== 'RECEIVED') throw new Error('EVIDENCE_NOT_RECEIVED')
      return { result: request.result, record_count: request.record_ids.length, records: request.record_ids.map(id => ({
        record_id: id, raw: `[GCA 검사 근거 예시 · ${run.seed.project}/${run.seed.block}]\n${run.records.get(id)!.raw}`, raw_truncated: false,
      })) }
    }
    const run = runs.find(r => r.bundleId === parts[2])
    if (parts[1] !== 'bundles' || !run) throw new Error('BUNDLE_NOT_FOUND')
    if (parts[3] === 'reviews' && body) {
      if (body.expected_revision !== run.revision) throw new Error('REVISION_CONFLICT')
      const unit = run.units.find(u => u.review_unit_id === body.unit_id)
      if (!unit?.analysis_ready || !['PASS', 'FAIL'].includes(String(body.conclusion))
        || (unit.check_status === 'FAIL' && body.conclusion === 'PASS')) throw new Error('REVIEW_BLOCKED')
      unit.decision = { reviewer: '데모 검토자', conclusion: String(body.conclusion), reason: String(body.reason), reviewed_at: new Date().toISOString() }
      run.revision++
      run.history.unshift({ revision: run.revision, actor: '데모 검토자', decisions: [{ ...unit.decision, review_unit_id: unit.review_unit_id }] })
      return finish({ status: 'REVIEWED', review_revision: run.revision })
    }
    if (parts[3] === 'evidence' && body) {
      const unit = run.units.find(u => u.review_unit_id === body.unit_id)
      const ids = body.record_ids as string[]
      if (!unit || !Array.isArray(ids) || !ids.length || ids.length > 100
        || new Set(ids).size !== ids.length || ids.some(id => run.records.get(id)?.file_id !== unit.report)) throw new Error('INVALID_RECORD_SCOPE')
      const now = Date.now()
      run.requests.unshift({ request_id: key, review_unit_id: unit.review_unit_id, record_ids: ids, reason: String(body.reason),
        status: 'PENDING', created_at: new Date(now).toISOString(), expires_at: new Date(now + 3600000).toISOString(),
        response_status: null, result: null })
      return finish({ status: 'REQUESTED' })
    }
    if (parts[3] === 'requests') return page(run.requests)
    if (parts[3] === 'units' && parts[5] === 'groups') {
      const groups = run.groups.get(parts[4]); if (!groups) throw new Error('UNIT_NOT_FOUND')
      return page(groups)
    }
    if (parts[3] === 'report') return [
      '# GCA 검토 보고서 · 데모 예시', `\n${run.seed.project} / ${run.seed.block} / ${run.seed.scenario} / ${bundle(run).run_id}`,
      '\n공개 문서를 참고해 재구성했습니다. 실제 PrimeTime/AI 실행 결과가 아니며 현재 탭의 검토 상태만 포함합니다.',
      ...sources.map(s => `- 참고: [${s.title}](${s.url}) · ${s.section}`),
      ...run.units.flatMap(u => [`\n## ${u.item_id}`, `판정: ${u.check_status} · 사람 검토: ${u.decision?.conclusion || '대기'}`,
        ...(u.decision ? [`검토 사유: ${u.decision.reason}`] : []), '```text', u.demo_report!.text || '예시 상황: 보고서 미수신', '```']),
    ].join('\n')
    if (parts.length !== 3) throw new Error('NOT_FOUND')
    const offset = Number(url.searchParams.get('unit_offset') || 0), limit = Number(url.searchParams.get('unit_limit') || 50)
    return structuredClone({ bundle_id: run.bundleId, run: { run_id: bundle(run).run_id, project: run.seed.project, design: run.seed.block,
      stage: run.seed.scenario, tool: 'PrimeTime GCA', tool_version: '(데모 예시)' }, review_revision: run.revision,
      coverage: coverage(run), units: run.units.slice(offset, offset + limit), unit_total: run.units.length,
      unit_offset: offset, unit_limit: limit, history: run.history, history_total: run.history.length, context: { ai: { status: 'NOT_CONFIGURED' } } })
  }
}
