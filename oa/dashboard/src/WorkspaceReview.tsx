import { useEffect, useId, useRef, useState } from 'react'
import { BookOpen, Check, CircleHelp, History, RefreshCw, Sparkles, UserRound } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Badge } from './studio/components/ui/badge'
import { RadioGroup, RadioGroupItem } from './studio/components/ui/radio-group'
import { Textarea } from './studio/components/ui/textarea'
import { Input } from './studio/components/ui/input'
import CaseSource from './CaseSource'
import { aiJobActive, aiJobMessage, WorkspaceError, workspaceLabels as labels, workspaceRequest as request, workspaceTime as time } from './workspaceApi'
import type { CodexJob, Judgment, ReportReview, ReviewCase, WorkspaceReport, WorkspaceSession, WorkspaceVersion } from './workspaceApi'

const factNames: Record<string, string> = { rule: '검사 규칙', mode: '모드', object: '대상', check_point: '검사 지점', severity: '심각도', code: '메시지 코드', design: '설계' }

export default function WorkspaceReview({ report, session, onSaved, onExpired, group, version, compact = false }: {
  report: WorkspaceReport; session: WorkspaceSession; onSaved: (event: ReviewCase) => void; onExpired: () => void;
  group?: { id: string; cluster_revision: number }
  compact?: boolean; version?: WorkspaceVersion
}) {
  const base = `/blocks/${report.block_id}/versions/${report.version_id}/reports/${report.id}${group ? `/groups/${group.id}` : ''}`
  const demo = session.environment === 'MEMORY_DEMO'
  const production = session.environment === 'PRODUCTION_SSO'
  const query = compact ? '?compact=true' : ''
  const inputId = useId()
  const [data, setData] = useState<ReportReview | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [conclusion, setConclusion] = useState<Judgment>('DEFER')
  const [reason, setReason] = useState('')
  const [exceptionEnabled, setExceptionEnabled] = useState(false)
  const [exceptionRule, setExceptionRule] = useState('')
  const [exceptionReason, setExceptionReason] = useState('')
  const [exceptionConditions, setExceptionConditions] = useState('')
  const [proposalId, setProposalId] = useState<string | null>(null)
  const [pollPaused, setPollPaused] = useState(false)
  const polls = useRef({ id: '', count: 0 })
  const aiRunning = aiJobActive(data?.ai?.job)
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
    let initialConclusion: Judgment = 'DEFER'
    if (value.ready) {
      initialConclusion = 'FAIL'
      if (report.check_status === 'PASS') initialConclusion = report.check_status
    }
    setConclusion(current?.conclusion || initialConclusion)
    setReason(current?.reason || '')
    setExceptionEnabled(!!current?.exception && current.conclusion !== 'DEFER')
    setExceptionRule(current?.exception?.rule || value.facts.rule || '')
    setExceptionReason(current?.exception?.reason || '')
    setExceptionConditions(current?.exception?.conditions || '')
    setProposalId(null); saveId.current = null; adviceId.current = null
  }
  useEffect(() => {
    alive.current = true
    const controller = new AbortController()
    request<ReportReview>(`${base}/review${query}`, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) initialize(value) }).catch(fail)
    return () => { alive.current = false; controller.abort() }
  }, [base, group?.cluster_revision])
  const edit = () => { saveId.current = null; setNotice('') }
  async function reload(reset = false, signal?: AbortSignal) {
    const value = await request<ReportReview>(`${base}/review${query}`, { signal })
    if (alive.current && !signal?.aborted) { if (reset) initialize(value); else setData(value) }
    return value
  }
  useEffect(() => {
    if (!production || !aiRunning || pollPaused) return
    const id = data!.ai!.job!.id
    if (polls.current.id !== id) polls.current = { id, count: 0 }
    if (polls.current.count >= 60) { setPollPaused(true); return }
    const controller = new AbortController()
    const timer = setTimeout(() => { polls.current.count++; reload(false, controller.signal).catch(fail) }, 2000)
    return () => { clearTimeout(timer); controller.abort() }
  }, [base, data, aiRunning, pollPaused])
  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!data || !reason.trim() || exceptionEnabled && (conclusion === 'DEFER' || !exceptionRule.trim() || !exceptionReason.trim() || !exceptionConditions.trim())) return
    const legacy = !data.current_stale && data.current?.reusable && !exceptionEnabled && conclusion !== 'DEFER' ? data.current : null
    setBusy('save'); setError(''); setNotice(''); saveId.current ||= crypto.randomUUID()
    try {
      const result = await request<{ event: ReviewCase }>(`${base}/review${query}`, { csrf: session.csrf, body: {
        conclusion, reason, criteria: legacy?.criteria || '', conditions: legacy?.conditions || {}, reusable: !!legacy,
        exception: exceptionEnabled ? { rule: exceptionRule.trim(), reason: exceptionReason.trim(), conditions: exceptionConditions.trim() } : null,
        compatible_versions: legacy?.compatible_versions || false, expected_revision: data.revision, request_id: saveId.current, proposal_id: proposalId,
        ...(group ? { expected_cluster_revision: group.cluster_revision } : {}),
      } })
      onSaved(result.event)
      await reload(true)
      if (alive.current) setNotice(production ? '사용자 판정과 근거를 저장했습니다. 다음 AI 판단의 참고 사례 후보로 축적됩니다.' : '사용자 판정과 근거를 저장했습니다. 원본 규칙 판정은 보존됩니다.')
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
      if (production) {
        if (!group || !data.ai?.available || aiRunning) return
        const job = await request<CodexJob>(`${base}/review/ai`, { csrf: session.csrf, body: { expected_revision: data.revision, request_id: adviceId.current, expected_cluster_revision: group.cluster_revision } })
        polls.current = { id: job.id, count: 0 }; setPollPaused(false)
        if (alive.current) setNotice('AI 판정 요청을 등록했습니다.')
      } else {
        await request(`${base}/review-proposals${query}`, { csrf: session.csrf, body: { expected_revision: data.revision, request_id: adviceId.current, mode: 'CRITERIA', ...(group ? { expected_cluster_revision: group.cluster_revision } : {}) } })
      }
      adviceId.current = null
      await reload()
    } catch (error) { fail(error) }
    finally { if (alive.current) setBusy(null) }
  }

  if (!data) return <div className="ws-review-loading ws-studio-review" role="status" aria-busy={!error}>
    <CircleHelp size={20} /><p>{error || '판정과 근거를 불러오는 중입니다.'}</p>
    {error && <Button variant="outline" onClick={() => { setError(''); reload(true).catch(fail) }}>다시 조회</Button>}
  </div>
  const proposal = data.proposal
  const legacyApproval = !data.current_stale && !!data.current?.reusable
  const exceptionIncomplete = exceptionEnabled && (!exceptionRule.trim() || !exceptionReason.trim() || !exceptionConditions.trim())
  return <div className="ws-judgment-workspace ws-studio-review">
    <header className="ws-studio-panel-heading"><div><span className="ws-studio-eyebrow">REVIEW</span><h3>판정과 근거</h3></div>{group && <Badge variant="outline">분류 r{group.cluster_revision}</Badge>}</header>
    {!group && <p className="ws-muted">보고서 전체에 대한 별도 판정입니다. 그룹별 판정은 로그 검토 화면에서 진행하세요.</p>}
    {data.current_stale && <p className="ws-notice">그룹 구성이 변경되었습니다. 이전 판정은 이력으로 보존되며 현재 그룹을 다시 검토해야 합니다.</p>}
    {error && <p className="ws-error" role="alert">{error}</p>}
    {notice && <p className="ws-notice" role="status"><Check size={15} />{notice}</p>}
    <div className="ws-judgment-summary ws-studio-meta-row"><div><span>도구 판정</span><Badge variant="outline" className={`ws-status ws-status-${report.check_status.toLowerCase()}`}>{labels[report.check_status]}</Badge></div><div><span>사용자 판정</span><Badge variant="secondary">{data.current_stale ? `재검토 · 이전 ${labels[data.current!.conclusion]}` : data.current ? labels[data.current.conclusion] : '미판정'}</Badge></div></div>
    {!data.current_stale && data.current?.exception && <div className="ws-review-current-exception" aria-label="현재 확정된 규칙 예외"><ExceptionEvidence value={data.current.exception} /></div>}
    <section className="ws-review-advice ws-studio-ai-card" aria-label="AI review와 누적 기준 제안">
      <header className="ws-studio-ai-title"><div><h4><Sparkles size={16} />AI 판정 제안</h4><p>{production ? group ? data.ai?.enabled ? `OA AI · ${data.ai.model}` : 'OA AI 연결 설정이 필요합니다.' : '분류 확정 후 각 그룹에서 AI 판정을 요청하세요.' : demo ? '합성 데모 · AI 호출 없음' : '사내 AI 미연결 · 확정 사례 참고 엔진'}</p></div>
        <Button variant="outline" size="sm" onClick={propose} disabled={demo || !!busy || !data.can_review || production && (!group || !data.ai?.available || aiRunning)}><RefreshCw size={14} className={aiRunning ? 'ws-spin' : undefined} />{production ? aiRunning || busy === 'proposal' ? 'AI 판정 중…' : 'AI 판정 받기' : busy === 'proposal' ? '기준 확인 중…' : '판정 제안 받기'}</Button>
      </header>
      {production && data.can_review && data.ai?.enabled && !data.ai.supported && <p className="ws-notice">분류 확정과 전체 로그·필수 근거의 준비 상태를 확인해 주세요.</p>}
      {production && data.ai?.job && <div className="ws-studio-ai-job"><p role="status" className={['FAILED', 'STALE', 'UNKNOWN'].includes(data.ai.job.status) ? 'ws-error' : 'ws-muted'}>{aiJobMessage(data.ai.job)}{aiRunning && pollPaused && ' 상태 자동 조회를 멈췄습니다. 아래 버튼으로 확인을 이어갈 수 있습니다.'}</p><Button variant="ghost" size="sm" disabled={!!busy} onClick={() => { polls.current.count = 0; setPollPaused(false); reload().catch(fail) }}>AI 판정 상태 다시 조회</Button></div>}
      {proposal ? <div className="ws-review-proposal ws-studio-ai-result">
        <div className="ws-studio-ai-verdict"><Badge variant="outline" className={`ws-status ws-status-${proposal.conclusion.toLowerCase()}`}>제안 {labels[proposal.conclusion]}</Badge><span>{proposal.model_called ? `AI 모델 · ${proposal.model_metadata?.model || proposal.advisor}` : '확정 기준 참고 엔진'} · {time(proposal.at)}</span></div>
        <p className="ws-studio-proposal-reason">{proposal.reason}</p>
        {data.current?.proposal_id === proposal.id ? <p className="ws-muted">이 제안에 대한 사용자 판정을 {labels[data.current.conclusion]}로 저장했습니다. 수정 내역은 아래 이력에서 확인할 수 있습니다.</p> : proposal.stale && <p className="ws-error">판정 또는 참고 기준이 변경되었습니다. 제안을 다시 받아 주세요.</p>}
        <div className="ws-studio-ai-references">
          {!!proposal.evidence_ids?.length && <details><summary>이번 로그의 직접 근거 {proposal.evidence_ids.length}건</summary><ul>{proposal.evidence_ids.map(id => <li key={id}><code>{id}</code></li>)}</ul></details>}
          {!!proposal.criterion_ids?.length && <details><summary>적용한 점검 기준 {proposal.criterion_ids.length}건</summary><ul>{proposal.criterion_ids.map(id => <li key={id}><code>{id}</code></li>)}</ul></details>}
          {proposal.references.length > 0 && <details><summary>{production ? 'AI가 인용한 공유 확정 사례' : '참고한 확정 기준'} {proposal.references.length}건</summary>{production && <p className="ws-muted">과거 확정 근거를 참고하며 원문은 기존 조회 권한을 따릅니다.</p>}{proposal.references.map(reference => <CaseEvidence key={reference.id} value={reference} report={report} version={version} />)}</details>}
        </div>
        <div className="ws-studio-ai-apply"><small>사용자 판정 저장 시 확정됩니다.</small>{data.can_review && <Button variant="outline" size="sm" disabled={!!busy || !!proposal.stale || aiRunning} onClick={() => { edit(); setConclusion(proposal.conclusion); setReason(proposal.reason); setProposalId(proposal.id); if (proposal.conclusion === 'DEFER') setExceptionEnabled(false) }}><Check size={14} />제안 적용</Button>}</div>
      </div> : <div className="ws-studio-empty"><Sparkles size={20} /><p>{demo ? '원문을 확인하고 최종 판정과 근거를 작성하세요.' : '아직 판정 제안이 없습니다.'}</p>{!demo && <small>제안을 요청하거나 직접 사용자 판정을 기록하세요.</small>}</div>}
    </section>
    {data.can_review ? <form className="ws-human-review ws-studio-human-form" onSubmit={save} aria-label="사용자 판정 기록">
      <div className="ws-studio-form-heading"><h4><UserRound size={16} />사용자 판정</h4><span>최종 확정</span></div>
      {!data.ready && <p className="ws-review-incomplete">검사 근거가 미수신 또는 미완료입니다. 현재는 보류만 기록할 수 있습니다.</p>}
      <fieldset disabled={!!busy}><legend>검토 결과</legend>
        <RadioGroup className="ws-judgment-options" name="human-judgment" value={conclusion} disabled={!!busy} onValueChange={value => { edit(); setConclusion(value as Judgment); if (value === 'DEFER') setExceptionEnabled(false) }} aria-label="검토 결과">
          {(['PASS', 'FAIL', 'DEFER'] as Judgment[]).map(value => <label key={value} htmlFor={`${inputId}-${value}`} className="ws-studio-decision-choice" data-state={conclusion === value ? 'checked' : 'unchecked'} data-verdict={value.toLowerCase()}><RadioGroupItem id={`${inputId}-${value}`} value={value} disabled={!data.ready && value !== 'DEFER'} /><span>{labels[value]}</span></label>)}
        </RadioGroup>
      </fieldset>
      <div className="ws-review-field"><div className="ws-studio-field-label"><span className="ws-studio-field-title"><label htmlFor={`${inputId}-reason`}>판정 근거</label><span className="ws-studio-field-required" aria-hidden="true">*</span></span><span>{reason.length.toLocaleString()} / 2,000</span></div><Textarea id={`${inputId}-reason`} value={reason} maxLength={2000} required disabled={!!busy} onChange={event => { edit(); setReason(event.target.value) }} placeholder="판정에 적용한 조건, 확인 내용 및 예외를 입력하세요." /></div>
      <p className="ws-studio-memory-note">{production ? '확정한 분류·판정과 근거는 블록·revision·ECO를 넘어 관련 AI 참고 사례로 축적됩니다.' : '원본 도구 결과와 별도로 검토 결과와 근거를 기록합니다.'}</p>
      <section className="ws-review-exception-settings" aria-label="규칙 예외 기록">
        <label className="ws-review-checkbox"><input type="checkbox" checked={exceptionEnabled} disabled={!!busy || conclusion === 'DEFER' || !data.ready} onChange={event => { edit(); setExceptionEnabled(event.target.checked) }} />이번 판정을 규칙 예외로 기록</label>
        {exceptionEnabled && <div className="ws-review-exception-fields">
          <p className="ws-muted">다음 AI가 예외 조건을 다시 확인합니다. 이 기록만으로 새 로그를 자동 통과시키지 않습니다.</p>
          <div className="ws-review-field"><label htmlFor={`${inputId}-exception-rule`}>예외 대상 규칙</label><Input id={`${inputId}-exception-rule`} value={exceptionRule} maxLength={500} required disabled={!!busy} onChange={event => { edit(); setExceptionRule(event.target.value) }} placeholder="예외로 판단한 점검 규칙" /></div>
          <div className="ws-review-field"><label htmlFor={`${inputId}-exception-reason`}>예외 사유</label><Textarea id={`${inputId}-exception-reason`} value={exceptionReason} maxLength={2000} required disabled={!!busy} onChange={event => { edit(); setExceptionReason(event.target.value) }} placeholder="이번 판정을 규칙 예외로 보는 이유" /></div>
          <div className="ws-review-field"><label htmlFor={`${inputId}-exception-conditions`}>예외가 성립하는 조건</label><Textarea id={`${inputId}-exception-conditions`} value={exceptionConditions} maxLength={2000} required disabled={!!busy} onChange={event => { edit(); setExceptionConditions(event.target.value) }} placeholder="다음 보고서에서 다시 확인해야 할 적용 범위와 조건" /></div>
          <small>조건은 사람과 AI가 검토할 설명입니다. 실행 가능한 자동 판정 규칙이 아닙니다.</small>
        </div>}
        {conclusion === 'DEFER' && <p className="ws-muted">보류는 규칙 예외로 기록하지 않습니다.</p>}
      </section>
      {legacyApproval && <p className="ws-notice ws-review-legacy-note">이 판정에는 이전 참고 엔진의 동일 조건 규칙 적용 승인이 남아 있습니다. 예외·보류로 저장하면 기존 조건 일치 규칙 적용 승인은 해제됩니다. 이전 승인은 이력에 남습니다.</p>}
      <div className="ws-human-review-actions ws-studio-form-footer"><span><UserRound size={14} />{session.user.name}</span><Button type="submit" disabled={!!busy || !reason.trim() || exceptionIncomplete}><Check size={15} />{busy === 'save' ? '저장 중…' : '판정 저장'}</Button></div>
    </form> : <p className="ws-review-readonly">{data.blocked_reason ? '분류 확정 후 현재 그룹의 판정을 기록할 수 있습니다. 이전 판정은 아래 이력에서 확인하세요.' : '조회 전용입니다. 이 블록 담당자가 사용자 판정과 기준을 확정합니다.'}</p>}
    <details className="ws-review-records"><summary><History size={15} />참고 사례와 이전 판정</summary>
      <p className="ws-muted">{production ? `이번 조회에서 선정한 관련 후보 ${data.case_count}건 · 현재 AI 제안의 인용 사례 ${proposal?.references.length || 0}건` : `참고 사례 ${data.case_count}건 · 재사용 기준 ${data.reusable_count}건`}{group && ` · 분류 개정 ${group.cluster_revision}`}</p>
      <details className="ws-review-cases"><summary><BookOpen size={15} />{production ? '이번 조회의 관련 공유 확정 사례 후보' : '누적된 확정 사례'} {data.case_count}건</summary><p>{production ? '모든 블록·revision·ECO의 확정 기록에서 관련성에 따라 한도 내로 선정한 후보이며 DB 전체 누적 건수가 아닙니다. 출처와 이번 조건의 차이를 확인해 다시 판단합니다. 과거 확정 근거를 참고하며 원문은 기존 조회 권한을 따릅니다. 보류·정정 전 판정은 현재 정답 후보에서 제외합니다.' : '현재 조건과 호환되는 다른 보고서의 최신 확정 사례입니다. 보류·정정 전 판정은 재사용하지 않습니다.'}</p>{data.cases.length ? data.cases.map(value => <CaseEvidence key={value.id} value={value} report={report} version={version} />) : <p className="ws-muted">{production ? '이번 조회에서 선정된 관련 확정 사례가 없습니다.' : '아직 참고할 다른 확정 사례가 없습니다.'}</p>}{data.case_count > data.cases_displayed && <p>{production ? `선정 후보 ${data.case_count}건 중 ${data.cases_displayed}건을 표시합니다. 실제 AI가 인용한 사례는 제안의 출처에서 확인하세요.` : `총 ${data.case_count}건 중 최근 ${data.cases_displayed}건을 표시합니다. 기준 적용은 전체 조회 범위를 사용합니다.`}</p>}</details>
      <section className="ws-human-history" aria-label="사용자 판정 이력"><h4>사용자 판정 이력</h4>{data.history.length ? data.history.map(value => <article key={value.id}><header><Badge variant="outline" className={`ws-status ws-status-${value.conclusion.toLowerCase()}`}>{labels[value.conclusion]} · 개정 {value.revision}</Badge><span>{value.actor.name} · {time(value.at)}</span></header><p>{value.reason}</p>{value.exception && <ExceptionEvidence value={value.exception} />}{value.reusable && <small>이전 참고 엔진의 동일 조건 규칙 적용 승인: {value.criteria}</small>}{value.proposal_conclusion && <small>제안 {labels[value.proposal_conclusion]} → 사용자 {labels[value.conclusion]} · {value.proposal_agreed ? '일치' : '사용자 수정'}</small>}</article>) : <p className="ws-muted">아직 기록된 판정이 없습니다.</p>}</section>
    </details>
  </div>
}

function CaseEvidence({ value, report, version }: { value: ReviewCase; report: WorkspaceReport; version?: WorkspaceVersion }) {
  return <article className="ws-case-evidence"><header><strong>{value.report_id} · {labels[value.conclusion]}</strong><span>{value.actor.name} · 개정 {value.revision}</span></header><p>{value.reason}</p>{value.exception && <ExceptionEvidence value={value.exception} />}{value.criteria && value.criteria !== value.reason && <p>적용 기준: {value.criteria}</p>}<dl>{Object.entries(value.conditions).map(([key, condition]) => <div key={key}><dt>{factNames[key] || key}</dt><dd>{condition}</dd></div>)}</dl><CaseSource value={value} report={report} version={version} />{value.group_id && <small>그룹 {value.group_snapshot?.label || value.group_id} · clustering 개정 {value.cluster_revision}</small>}<small>원본 SHA-256 {value.source_sha256}</small><small>공유 확정 사례 · 이번 로그와 조건을 확인해 다시 판단</small>{value.reusable && <small>이전 참고 엔진의 동일 조건 규칙 적용 승인 이력</small>}</article>
}

function ExceptionEvidence({ value }: { value: NonNullable<ReviewCase['exception']> }) {
  return <section className="ws-review-exception-evidence" aria-label="규칙 예외 근거"><Badge variant="outline">규칙 예외</Badge><dl><div><dt>대상 규칙</dt><dd>{value.rule}</dd></div><div><dt>예외 사유</dt><dd>{value.reason}</dd></div><div><dt>성립 조건</dt><dd>{value.conditions}</dd></div></dl></section>
}
