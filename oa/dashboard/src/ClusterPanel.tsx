import { useEffect, useRef, useState } from 'react'
import { Button } from './components/ui/button'
import { Badge } from './components/ui/badge'
import { Textarea } from './components/ui/textarea'
import { Input } from './components/ui/input'
import type { Page, Unit } from './types'

type Api = <T>(path: string, body?: Record<string, unknown>) => Promise<T>
type Rule = { identity_fields: string[]; where: Record<string, unknown>; reason: string; evidence_ids: string[] }
type Header = { rules: Rule[]; explanation: string; provenance: Record<string, unknown> }
type Job = { job_id: string; base_revision: number; stale: boolean; references_current: boolean; instruction: string;
  catalog_exclusions?: { group_id: string; group_revision: number; source_bundle: string; source_unit: string; source_job: string; reason: string }[];
  state: { phase: string; received_records: number; received_parts: number; validation_error?: string; header?: Header; result?: { error_code?: string }; stats?: Record<string, number> } }
type View = { head: { revision: number; active_job: string | null; confirmed: boolean }; jobs: Job[]; total: number; saved_rules: Rule[];
  input_blockers: string[]; history: { classification_revision: number; payload: { command: { action: string; reviewer: string; reason: string } } }[] }
type Candidate = { group_id: string; identity: Record<string, unknown>; source_bundle: string; source_unit: string; source_job: string;
  fail_evidence_ids: string[]; matches: string[]; differences: string[]; missing: string[]; state: string; needs_recheck: boolean;
  decision: { command: { decision: string; reason: string } } | null }
type Member = { record_id: string; group_id: string | null; previous_group_id: string | null; membership_changed: boolean;
  check_status: string; method: string; reason: string; fields: Record<string, unknown>; candidates: Candidate[] }

export function ClusterPanel({ api, bundle, units, writable, demo }: { api: Api; bundle: string; units: Unit[]; writable: boolean; demo: boolean }) {
  const [unit, setUnit] = useState(units.find(u => u.applicable)?.review_unit_id || '')
  const [view, setView] = useState<View | null>(null)
  const [jobId, setJobId] = useState('')
  const [jobsOffset, setJobsOffset] = useState(0)
  const [members, setMembers] = useState<Page<Member> | null>(null)
  const [offset, setOffset] = useState(0)
  const [filter, setFilter] = useState('all')
  const [group, setGroup] = useState('')
  const [instruction, setInstruction] = useState('')
  const [mode, setMode] = useState('AUTO')
  const [rules, setRules] = useState('')
  const [reason, setReason] = useState('')
  const [tick, setTick] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const retries = useRef(new Map<string, string>())
  const base = `/api/bundles/${encodeURIComponent(bundle)}/units/${encodeURIComponent(unit)}/clustering`
  const job = view?.jobs.find(j => j.job_id === jobId)
  const active = view?.head.active_job === jobId
  const ready = job?.state.phase === 'READY' && job.references_current
  const locked = busy || !writable
  const message = (e: unknown) => setError(e instanceof Error ? e.message : '요청 실패')
  useEffect(() => {
    if (demo || !unit) return
    let current = true
    setView(null)
    api<View>(`${base}?offset=${jobsOffset}&limit=20`).then(v => {
      if (current) { setView(v); setJobId(old => v.jobs.some(j => j.job_id === old) ? old : v.jobs[0]?.job_id || '') }
    }).catch(e => { if (current) message(e) })
    return () => { current = false }
  }, [api, base, unit, demo, jobsOffset, tick])
  useEffect(() => {
    if (demo || !jobId) return
    let current = true
    setMembers(null)
    api<Page<Member>>(`${base}/${encodeURIComponent(jobId)}/members?offset=${offset}&limit=20&filter=${filter}${group ? `&group_id=${encodeURIComponent(group)}` : ''}`)
      .then(v => { if (current) setMembers(v) }).catch(e => { if (current) message(e) })
    return () => { current = false }
  }, [api, base, demo, jobId, offset, filter, group, tick])
  async function command(path: string, body: Record<string, unknown>, idempotent = true) {
    const key = JSON.stringify([path, body])
    if (idempotent && !retries.current.has(key)) retries.current.set(key, crypto.randomUUID())
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api<{ status: string }>(path, { ...body, ...(idempotent ? { request_id: retries.current.get(key) } : {}) })
      retries.current.delete(key)
      setNotice(`처리 결과: ${result.status}`); setTick(n => n + 1)
    } catch (e) { message(e) } finally { setBusy(false) }
  }
  function propose() {
    try {
      const parsed: unknown = rules.trim() ? JSON.parse(rules) : null
      if (parsed !== null && !Array.isArray(parsed)) throw new Error('규칙은 JSON 배열이어야 합니다.')
      void command(base, { expected_revision: view!.head.revision, instruction, mode, rules: parsed })
    } catch (e) { message(e) }
  }
  function act(action: string, extra: Record<string, unknown> = {}) {
    void command(`${base}/actions`, { expected_revision: view!.head.revision, job_id: jobId, action, reason, ...extra })
  }
  function requestEvidence(bid: string, uid: string, ids: string[]) {
    void command(`/api/bundles/${encodeURIComponent(bid)}/evidence`, { unit_id: uid, record_ids: ids, reason: reason || '분류 후보 원문 확인' })
  }
  if (demo) return <div className="tab-intro"><h3>AI 분류 작업</h3><p>데모에서는 AI를 호출하지 않습니다. 실제 DB 연결과 VWP 작업자 설정 후 사용할 수 있습니다.</p></div>
  return <section className="cluster-panel">
    <div className="tab-intro"><h3>AI와 함께 분류하기</h3><p>VWP가 규칙을 제안하고 전체 기록에 시험 적용합니다. 사람의 분류 확정과 sanity 검토는 별개입니다.</p></div>
    <label>점검 항목 (현재 대장 페이지)<select aria-label="분류 점검 항목" value={unit} disabled={busy} onChange={e => {
      setUnit(e.target.value); setView(null); setJobId(''); setMembers(null); setJobsOffset(0); setOffset(0); setGroup(''); setRules(''); setInstruction(''); setError('')
    }}>{units.filter(u => u.applicable).map(u => <option key={u.review_unit_id} value={u.review_unit_id}>{u.item_id} · {u.review_unit_id}</option>)}</select></label>
    {error && <div role="alert" className="error-banner">{error}</div>}
    {notice && <p role="status" className="notice-banner">{notice} · 원문 요청은 해당 실행의 ‘추가 근거’ 탭에서 수신·열람하세요.</p>}
    <Button variant="outline" disabled={busy} onClick={() => { setError(''); setTick(n => n + 1) }}>분류 새로고침</Button>
    {view && <>
      <p>분류 개정 {view.head.revision} · {view.head.confirmed ? '사람 확정' : '미확정'} · 저장된 규칙 {view.saved_rules.length}개</p>
      {!!view.input_blockers.length && <div className="unit-alert">입력 차단 사유: {view.input_blockers.join(', ')}</div>}
      <label>VWP AI에 요청할 내용<Textarea aria-label="분류 요청" value={instruction} maxLength={4000} disabled={locked} onChange={e => setInstruction(e.target.value)} placeholder="예: 같은 코드라도 객체와 검사 지점이 다르면 분리해줘." /></label>
      <label>실행 방식<select aria-label="분류 실행 방식" value={mode} disabled={locked} onChange={e => setMode(e.target.value)}><option value="AUTO">기존 규칙 우선 · 필요시 AI</option><option value="AI">AI 규칙 재제안</option><option value="RULES">규칙만 전체 시험 (AI 호출 없음)</option></select></label>
      <details><summary>선언형 규칙 직접 수정</summary><p className="tiny">빈칸이면 현재/저장 규칙을 재사용합니다. []는 규칙 없는 상태입니다. 코드나 실행 명령은 허용하지 않습니다.</p>
        <Textarea aria-label="분류 규칙 JSON" rows={8} value={rules} disabled={locked} onChange={e => setRules(e.target.value)} />
        <Button variant="outline" disabled={locked} onClick={() => setRules(JSON.stringify(job?.state.header?.rules || view.saved_rules, null, 2))}>선택한 규칙 불러오기</Button>
      </details>
      <Button disabled={locked || !unit} onClick={propose}>VWP에 분류 요청</Button>
      <div className="group-header"><h4>분류 작업 ({view.total})</h4><span>기존 결과는 새 제안으로 덮어쓰지 않습니다.</span></div>
      <select aria-label="분류 작업 선택" value={jobId} disabled={busy} onChange={e => { setJobId(e.target.value); setOffset(0); setGroup('') }}>
        {!view.jobs.length && <option value="">아직 작업 없음</option>}
        {view.jobs.map(j => <option key={j.job_id} value={j.job_id}>{j.state.phase} · {j.job_id}</option>)}
      </select>
      <Pager offset={jobsOffset} total={view.total} change={setJobsOffset} disabled={busy} label="작업" />
      {job && <>
        <p><Badge variant="outline">{job.state.phase}</Badge> · 수신 {job.state.received_records}건 / {job.state.received_parts}개 조각 {active && '· 현재 적용 중'}</p>
        {(job.stale || !job.references_current) && <div className="unit-alert">기준 개정이 바뀌었습니다. 현재 개정으로 다시 요청해야 합니다.</div>}
        {!!job.catalog_exclusions?.length && <div className="unit-alert">
          <p>비교 대상에서 제외한 과거 확정본 {job.catalog_exclusions.length}개</p>
          <p>현재 유효하고 순환 참조를 만들지 않는 확정 그룹만 비교합니다. 제외한 확정본까지 비교 완료했다는 뜻은 아닙니다.</p>
          <details><summary>제외 사유 · 원본 개정</summary>{job.catalog_exclusions.map(e => <div key={`${e.group_id}:${e.source_job}`}>
            <p>{e.reason === 'SELF_DEPENDENCY' ? '현재 항목에 의존하는 그룹 · 순환 참조 방지' : '과거 참조 변경 · 재검토 필요'}</p>
            <pre className="cluster-json">{JSON.stringify(e, null, 2)}</pre>
          </div>)}</details>
        </div>}
        {job.state.result?.error_code && <div role="alert" className="error-banner">VWP 작업 실패: {job.state.result.error_code}</div>}
        {job.state.validation_error && <div role="alert" className="error-banner">수신 검증 거부: {job.state.validation_error}</div>}
        <Button variant="outline" disabled={locked} onClick={() => void command(`${base}/${jobId}/receive`, {}, false)}>분류 결과 수신</Button>
        {job.state.stats && <dl className="cluster-stats">{Object.entries(job.state.stats).map(([k, v]) => <div key={k}><dt>{({ records: '전체 기록', targets: '분류 대상', assigned: '배정', unresolved: '미분류', groups: '그룹', candidate_records: 'FAIL 연관 후보 기록' } as Record<string, string>)[k] || k}</dt><dd>{v.toLocaleString()}</dd></div>)}</dl>}
        {job.state.header && <><p>{job.state.header.explanation}</p><details><summary>적용 규칙 · AI 표본/출처</summary><pre className="cluster-json">{JSON.stringify(job.state.header, null, 2)}</pre></details></>}
        <p className="tiny">AI는 제한된 대표 표본을 검토합니다. 전체 기록은 프로그램이 규칙 적용·비교하며, 이것이 모든 원문을 AI가 읽었다는 뜻은 아닙니다.</p>
        <label>확정·보류 사유<Textarea aria-label="분류 판단 사유" value={reason} maxLength={2000} disabled={locked} onChange={e => setReason(e.target.value)} /></label>
        <div className="cluster-actions">
          <Button disabled={locked || !ready || job.stale || active || !reason.trim()} onClick={() => act('APPLY')}>제안을 현재 분류에 적용</Button>
          <Button disabled={locked || !ready || !active || !reason.trim()} onClick={() => act('CONFIRM')}>분류 확정</Button>
          <Button variant="outline" disabled={locked || !ready || !active || !view.head.confirmed || !reason.trim()} onClick={() => act('SAVE_RULES')}>규칙 재사용 저장</Button>
          <Button variant="outline" disabled={locked || job.state.phase !== 'READY' || !active || !reason.trim()} onClick={() => act('DEFER')}>분류 보류</Button>
        </div>
        <div className="table-toolbar"><select aria-label="분류 기록 필터" value={filter} onChange={e => { setFilter(e.target.value); setOffset(0) }}><option value="all">전체 기록</option><option value="unresolved">미분류</option><option value="candidates">FAIL 그룹 후보 있음</option></select>
          <Input aria-label="지속 그룹 ID 필터" value={group} placeholder="지속 그룹 ID로 필터" onChange={e => { setGroup(e.target.value); setOffset(0) }} /></div>
        {members?.items.map(m => <article className="finding" key={m.record_id}>
          <div className="finding-head"><Badge variant="outline">개별 판정 {m.check_status}</Badge><strong>{m.method}</strong><span>{m.reason}</span></div>
          <p className="cluster-id">{m.record_id}</p><p className="cluster-id">소속: {m.group_id || '없음'}{m.membership_changed && ` ← ${m.previous_group_id || '미배정'} (변경)`}</p>
          <pre className="cluster-json">{JSON.stringify(m.fields, null, 2)}</pre>
          <Button size="sm" variant="outline" disabled={locked} onClick={() => requestEvidence(bundle, unit, [m.record_id])}>이 기록 원문 요청</Button>
          {m.candidates.map(c => <div className="cluster-candidate" key={c.group_id}>
            <Badge variant="outline">FAIL 그룹 연관 후보 · {c.state}</Badge><p className="cluster-id">{c.group_id}</p>
            <p>검토: {c.needs_recheck ? '기준 변경 · 재확인 필요' : c.decision?.command.decision || '미확인'}</p>
            <details><summary>그룹 기준 · 근거 · 차이</summary><pre className="cluster-json">{JSON.stringify({ identity: c.identity, matches: c.matches, differences: c.differences, missing: c.missing, source_bundle: c.source_bundle, source_unit: c.source_unit, source_job: c.source_job, fail_evidence_ids: c.fail_evidence_ids, decision: c.decision }, null, 2)}</pre></details>
            <div className="cluster-actions"><Button variant="outline" size="sm" disabled={locked} onClick={() => requestEvidence(c.source_bundle, c.source_unit, c.fail_evidence_ids)}>그룹 FAIL 근거 요청</Button>
              {(['CONFIRM_MATCH', 'DIFFERENT', 'DEFER'] as const).map(d => <Button key={d} size="sm" variant="outline" disabled={locked || !ready || !active || !reason.trim()} onClick={() => act('CANDIDATE', { record_id: m.record_id, group_id: c.group_id, decision: d })}>{({ CONFIRM_MATCH: '연관 확인', DIFFERENT: '다른 문제', DEFER: '판단 보류' })[d]}</Button>)}
            </div><p className="tiny">연관 확인은 소속 변경이나 개별 FAIL 판정이 아닙니다.</p>
          </div>)}
        </article>)}
        {members && <Pager offset={offset} total={members.total} change={setOffset} disabled={busy} label="기록" />}
      </>}
      <details><summary>분류 변경 이력 (최근 50건)</summary>{view.history.map(h => <p key={h.classification_revision}>개정 {h.classification_revision} · {h.payload.command.action} · {h.payload.command.reviewer} · {h.payload.command.reason}</p>)}</details>
    </>}
  </section>
}

function Pager({ offset, total, change, disabled, label }: { offset: number; total: number; change: (v: number) => void; disabled: boolean; label: string }) {
  return <div className="pagination"><span>{total ? offset + 1 : 0}–{Math.min(offset + 20, total)} / {total}</span><Button variant="outline" size="sm" disabled={disabled || !offset} onClick={() => change(offset - 20)}>{label} 이전</Button><Button variant="outline" size="sm" disabled={disabled || offset + 20 >= total} onClick={() => change(offset + 20)}>{label} 다음</Button></div>
}
