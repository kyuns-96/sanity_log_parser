import { useEffect, useId, useRef, useState } from 'react'
import { BookOpen, Check, RefreshCw, Sparkles } from 'lucide-react'
import { Button } from './components/ui/button'
import { WorkspaceError, workspaceLabels as labels, workspaceRequest as request, workspaceTime as time } from './workspaceApi'
import type { Judgment, ReportReview, ReviewCase, WorkspaceReport, WorkspaceSession } from './workspaceApi'

const factNames: Record<string, string> = { rule: '검사 규칙', mode: '모드', object: '대상', check_point: '검사 지점', severity: '심각도', code: '메시지 코드', design: '설계' }

export default function WorkspaceReview({ report, session, onSaved, onExpired, group, compact = false }: {
  report: WorkspaceReport; session: WorkspaceSession; onSaved: (event: ReviewCase) => void; onExpired: () => void;
  group?: { id: string; cluster_revision: number }
  compact?: boolean
}) {
  const base = `/blocks/${report.block_id}/versions/${report.version_id}/reports/${report.id}${group ? `/groups/${group.id}` : ''}`
  const demo = session.environment === 'MEMORY_DEMO'
  const query = compact ? '?compact=true' : ''
  const inputId = useId()
  const [data, setData] = useState<ReportReview | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [conclusion, setConclusion] = useState<Judgment>('DEFER')
  const [reason, setReason] = useState('')
  const [criteria, setCriteria] = useState('')
  const [reusable, setReusable] = useState(false)
  const [compatible, setCompatible] = useState(false)
  const [conditions, setConditions] = useState<Record<string, string>>({})
  const [proposalId, setProposalId] = useState<string | null>(null)
  const [busy, setBusy] = useState<'save' | 'proposal' | null>(null)
  const saveId = useRef<string | null>(null)
  const adviceId = useRef<string | null>(null)
  const alive = useRef(true)

  function fail(error: unknown) {
    if (error instanceof DOMException && error.name === 'AbortError') return
    if (error instanceof WorkspaceError && error.code === 'UNAUTHORIZED') { onExpired(); return }
    if (alive.current) setError(error instanceof WorkspaceError && error.code === 'REVISION_CONFLICT'
      ? '다른 검토자가 판정을 변경했습니다. 최신 판정과 이력을 확인한 뒤 저장해 주세요.'
      : error instanceof Error ? error.message : '검토 정보를 불러오지 못했습니다.')
  }
  function initialize(value: ReportReview) {
    setData(value)
    const current = value.current_stale ? null : value.current
    setConclusion(current?.conclusion || (value.ready ? report.check_status === 'PASS' ? 'PASS' : 'FAIL' : 'DEFER'))
    setReason(current?.reason || '')
    setCriteria(current?.criteria || '')
    setReusable(current?.reusable || false)
    setCompatible(current?.compatible_versions || false)
    setConditions(current?.reusable ? current.conditions : Object.fromEntries(Object.entries(value.facts).filter(([key]) => ['rule', 'mode'].includes(key))))
    setProposalId(null); saveId.current = null; adviceId.current = null
  }
  useEffect(() => {
    alive.current = true
    const controller = new AbortController()
    request<ReportReview>(`${base}/review${query}`, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) initialize(value) }).catch(fail)
    return () => { alive.current = false; controller.abort() }
  }, [base, group?.cluster_revision])
  const edit = () => { saveId.current = null; setNotice('') }
  async function reload(reset = false) {
    const value = await request<ReportReview>(`${base}/review${query}`)
    if (alive.current) { if (reset) initialize(value); else setData(value) }
    return value
  }
  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!data || !reason.trim()) return
    setBusy('save'); setError(''); setNotice(''); saveId.current ||= crypto.randomUUID()
    try {
      const result = await request<{ event: ReviewCase }>(`${base}/review${query}`, { csrf: session.csrf, body: {
        conclusion, reason, criteria: reusable ? criteria : '', conditions: reusable ? conditions : {}, reusable,
        compatible_versions: reusable && compatible, expected_revision: data.revision, request_id: saveId.current, proposal_id: proposalId,
        ...(group ? { expected_cluster_revision: group.cluster_revision } : {}),
      } })
      onSaved(result.event)
      await reload(true)
      if (alive.current) setNotice('사용자 판정과 근거를 저장했습니다. 원본 규칙 판정은 보존됩니다.')
    } catch (error) {
      fail(error)
      if (error instanceof WorkspaceError && ['REVISION_CONFLICT', 'PROPOSAL_STALE'].includes(error.code)) {
        saveId.current = null; setProposalId(null)
        try { await reload() } catch (refreshError) { fail(refreshError) }
      }
    } finally { if (alive.current) setBusy(null) }
  }
  async function propose() {
    if (!data) return
    setBusy('proposal'); setError(''); adviceId.current ||= crypto.randomUUID()
    try {
      await request(`${base}/review-proposals${query}`, { csrf: session.csrf, body: { expected_revision: data.revision, request_id: adviceId.current, mode: 'CRITERIA', ...(group ? { expected_cluster_revision: group.cluster_revision } : {}) } })
      adviceId.current = null
      await reload()
    } catch (error) { fail(error) }
    finally { if (alive.current) setBusy(null) }
  }

  if (!data) return <div className="ws-review-loading" role="status">{error || '판정과 누적 기준을 불러오는 중입니다.'}{error && <Button variant="outline" onClick={() => { setError(''); reload(true).catch(fail) }}>다시 조회</Button>}</div>
  const proposal = data.proposal
  return <div className="ws-judgment-workspace">
    {!group && <p className="ws-muted">보고서 전체에 대한 별도 판정입니다. 그룹별 판정은 로그 검토 화면에서 진행하세요.</p>}
    {data.current_stale && <p className="ws-notice">그룹 구성이 변경되었습니다. 이전 판정은 이력으로 보존되며 현재 그룹을 다시 검토해야 합니다.</p>}
    {error && <p className="ws-error" role="alert">{error}</p>}
    {notice && <p className="ws-notice" role="status"><Check size={15} />{notice}</p>}
    <div className="ws-judgment-summary"><div><span>도구 판정</span><strong className={`ws-status ws-status-${report.check_status.toLowerCase()}`}>{labels[report.check_status]}</strong></div><div><span>사용자 판정</span><strong>{data.current_stale ? `재검토 · 이전 ${labels[data.current!.conclusion]}` : data.current ? labels[data.current.conclusion] : '미판정'}</strong></div></div>
    <section className="ws-review-advice" aria-label="AI review와 누적 기준 제안"><header><div><h3><Sparkles size={16} />AI review</h3><p>{demo ? '합성 데모 · AI를 호출하지 않습니다. 원문을 확인하고 직접 판정하세요.' : '사내 AI 미연결 · 확정 사례를 참고하는 엔진입니다.'}</p></div><Button variant="outline" onClick={propose} disabled={demo || !!busy || !data.can_review}><RefreshCw size={14} />{busy === 'proposal' ? '기준 확인 중…' : '판정 제안 받기'}</Button></header>
      {proposal ? <div className="ws-review-proposal"><div><strong>제안 {labels[proposal.conclusion]}</strong><span>확정 기준 적용 · {time(proposal.at)}</span></div><p>{proposal.reason}</p>{data.current?.proposal_id === proposal.id ? <p className="ws-muted">이 제안에 대한 사용자 판정을 {labels[data.current.conclusion]}로 저장했습니다. 수정 내역은 아래 이력에서 확인할 수 있습니다.</p> : proposal.stale && <p className="ws-error">판정 또는 참고 기준이 변경되었습니다. 제안을 다시 받아 주세요.</p>}
        {proposal.references.length > 0 && <details><summary>참고한 확정 기준 {proposal.references.length}건</summary>{proposal.references.map(reference => <CaseEvidence key={reference.id} value={reference} />)}</details>}
        {data.can_review && <Button variant="outline" disabled={!!busy || !!proposal.stale} onClick={() => { edit(); setConclusion(proposal.conclusion); setReason(proposal.reason); setProposalId(proposal.id); setReusable(false); setCompatible(false) }}>제안 적용</Button>}
        <small>사용자 판정 저장 시 확정됩니다.</small>
      </div> : <p className="ws-muted">제안 조회 또는 사용자 판정을 입력하세요.</p>}
    </section>
    {data.can_review ? <form className="ws-human-review" onSubmit={save} aria-label="사용자 판정 기록"><h3>사용자 판정</h3><p>검토 결과와 판정 근거를 기록합니다.</p>
      {!data.ready && <p className="ws-review-incomplete">검사 근거가 미수신 또는 미완료입니다. 현재는 보류만 기록할 수 있습니다.</p>}
      <fieldset disabled={!!busy}><legend>검토 결과</legend><div className="ws-judgment-options">{(['PASS', 'FAIL', 'DEFER'] as Judgment[]).map(value => <label key={value}><input type="radio" name="human-judgment" value={value} checked={conclusion === value} disabled={!data.ready && value !== 'DEFER'} onChange={() => { edit(); setConclusion(value); if (value === 'DEFER') { setReusable(false); setCompatible(false) } }} /><span>{labels[value]}</span></label>)}</div></fieldset>
      <div className="ws-review-field"><label htmlFor={`${inputId}-reason`}>판정 근거</label><textarea id={`${inputId}-reason`} value={reason} maxLength={2000} required disabled={!!busy} onChange={event => { edit(); setReason(event.target.value) }} placeholder="판정에 적용한 조건, 확인 내용 및 예외를 입력하세요." /></div>
      <details className="ws-review-criteria-settings" open={reusable || undefined}><summary>판정 기준 재사용 (선택)</summary><p className="ws-muted">확정 판정은 사례로 저장됩니다. 동일한 조건에 적용할 기준을 추가할 수 있습니다.</p>
      <label className="ws-review-checkbox"><input type="checkbox" checked={reusable} disabled={!!busy || conclusion === 'DEFER' || !data.ready || !data.facts.rule} onChange={event => { edit(); setReusable(event.target.checked); if (!event.target.checked) setCompatible(false) }} />이 판정의 조건을 다음 검토에서 재사용</label>
      {reusable && <div className="ws-reusable-criteria"><div className="ws-review-field"><label htmlFor={`${inputId}-criteria`}>재사용할 판단 기준</label><textarea id={`${inputId}-criteria`} value={criteria} maxLength={2000} required disabled={!!busy} onChange={event => { edit(); setCriteria(event.target.value) }} placeholder="선택한 조건에서 이 판정을 제안할 수 있는 근거를 기록하세요." /></div><fieldset disabled={!!busy}><legend>일치해야 하는 원본 조건</legend><p>프로젝트·블록·item·도구·파서·규칙 버전·PRE/POST·ECO는 항상 구분합니다. 선택한 조건이 모두 같으면 이 판정을 제안합니다. 글로만 적은 예외는 자동 적용되지 않습니다.</p>{Object.entries(data.facts).map(([key, value]) => <label key={key} className="ws-review-checkbox"><input type="checkbox" checked={key in conditions} disabled={['rule', 'mode'].includes(key)} onChange={event => { edit(); setConditions(previous => { const next = { ...previous }; if (event.target.checked) next[key] = value; else delete next[key]; return next }) }} /><span>{factNames[key] || key}<code>{value}</code></span></label>)}</fieldset><label className="ws-review-checkbox"><input type="checkbox" checked={compatible} disabled={!!busy} onChange={event => { edit(); setCompatible(event.target.checked) }} />같은 블록의 다른 버전에서도 동일한 호환 조건일 때 재사용</label><small>{compatible ? '다른 프로젝트/블록·도구/규칙 버전·모드로는 확대하지 않습니다.' : '현재 NET_VER·REVISION의 보고서에만 적용합니다.'}</small></div>}
      </details>
      <div className="ws-human-review-actions"><span>검토자 {session.user.name}</span><Button type="submit" disabled={!!busy || !reason.trim() || reusable && !criteria.trim()}>{busy === 'save' ? '저장 중…' : '판정 저장'}</Button></div>
    </form> : <p className="ws-review-readonly">{data.blocked_reason ? 'Clustering 확정 후 현재 그룹의 판정을 기록할 수 있습니다. 이전 판정은 아래 이력에서 확인하세요.' : '조회 전용입니다. 이 블록 담당자가 사용자 판정과 기준을 확정합니다.'}</p>}
    <details className="ws-review-records"><summary>참고 사례와 이전 판정</summary>
      <p className="ws-muted">참고 사례 {data.case_count}건 · 재사용 기준 {data.reusable_count}건{group && ` · 분류 개정 ${group.cluster_revision}`}</p>
      <details className="ws-review-cases"><summary><BookOpen size={15} />누적된 확정 사례 {data.case_count}건</summary><p>현재 조건과 호환되는 다른 보고서의 최신 확정 사례입니다. 보류·정정 전 판정은 재사용하지 않습니다.</p>{data.cases.length ? data.cases.map(value => <CaseEvidence key={value.id} value={value} />) : <p className="ws-muted">아직 참고할 다른 확정 사례가 없습니다.</p>}{data.case_count > data.cases_displayed && <p>총 {data.case_count}건 중 최근 {data.cases_displayed}건을 표시합니다. 기준 적용은 전체 조회 범위를 사용합니다.</p>}</details>
      <section className="ws-human-history" aria-label="사용자 판정 이력"><h3>사용자 판정 이력</h3>{data.history.length ? data.history.map(value => <article key={value.id}><header><strong>{labels[value.conclusion]} · 개정 {value.revision}</strong><span>{value.actor.name} · {time(value.at)}</span></header><p>{value.reason}</p>{value.reusable && <small>재사용 기준: {value.criteria}</small>}{value.proposal_conclusion && <small>제안 {labels[value.proposal_conclusion]} → 사용자 {labels[value.conclusion]} · {value.proposal_agreed ? '일치' : '사용자 수정'}</small>}</article>) : <p className="ws-muted">아직 기록된 판정이 없습니다.</p>}</section>
    </details>
  </div>
}

function CaseEvidence({ value }: { value: ReviewCase }) {
  return <article className="ws-case-evidence"><header><strong>{value.report_id} · {labels[value.conclusion]}</strong><span>{value.actor.name} · 개정 {value.revision}</span></header><p>{value.criteria || value.reason}</p><dl>{Object.entries(value.conditions).map(([key, condition]) => <div key={key}><dt>{factNames[key] || key}</dt><dd>{condition}</dd></div>)}</dl><small>{value.source_version.net_ver} · {value.source_version.layout} · {value.source_version.revision}</small>{value.group_id && <small>그룹 {value.group_snapshot?.label || value.group_id} · clustering 개정 {value.cluster_revision}</small>}<small>원본 SHA-256 {value.source_sha256}</small><small>{value.reusable ? '확정 재사용 기준' : '일회성 판정 사례'} · {value.compatible_versions ? '호환 버전 포함' : '원본 버전 한정'}</small></article>
}
