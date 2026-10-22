import { useEffect, useId, useRef, useState } from 'react'
import { ArrowRight, Check, Layers3, RefreshCw } from 'lucide-react'
import { Button } from './components/ui/button'
import WorkspaceReview from './WorkspaceReview'
import { WorkspaceError, workspaceLabels as labels, workspaceRequest as request, workspaceTime as time } from './workspaceApi'
import type { ClusterEvent, PipelineGroup, PipelineSummary, PipelineView, WorkspaceReport, WorkspaceSession } from './workspaceApi'

const names: Record<string, string> = { object: '대상', check_point: '검사 지점', mode: '모드', observed: '측정값', expected: '기준값', unit: '단위' }
const actionNames = { PROPOSE: '분류 초안', EDIT: '사람의 구성 수정', CONFIRM: 'Clustering 확정', WITHDRAW: '확정 해제' }

export default function WorkspacePipeline({ report, session, onChanged, onExpired }: {
  report: WorkspaceReport; session: WorkspaceSession; onChanged: (summary: PipelineSummary | null) => void; onExpired: () => void
}) {
  const base = `/blocks/${report.block_id}/versions/${report.version_id}/reports/${report.id}`
  const inputId = useId()
  const [data, setData] = useState<PipelineView | null>(null)
  const [step, setStep] = useState<1 | 2>(1)
  const [groups, setGroups] = useState<PipelineGroup[]>([])
  const [identityFields, setIdentityFields] = useState(['object', 'check_point', 'mode'])
  const [reason, setReason] = useState('')
  const [criteria, setCriteria] = useState('')
  const [reusable, setReusable] = useState(false)
  const [compatible, setCompatible] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [destination, setDestination] = useState('new')
  const [groupId, setGroupId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const pending = useRef<{ key: string; id: string } | null>(null)
  const alive = useRef(true)
  const records = report.records || []
  const dirty = JSON.stringify(groups) !== JSON.stringify(data?.current?.groups || [])
  const identityChanged = !!data?.current && JSON.stringify([...identityFields].sort()) !== JSON.stringify([...data.current.identity_fields].sort())

  function fail(value: unknown) {
    if (value instanceof DOMException && value.name === 'AbortError') return
    if (value instanceof WorkspaceError && value.code === 'UNAUTHORIZED') { onExpired(); return }
    if (alive.current) setError(value instanceof WorkspaceError && value.code === 'REVISION_CONFLICT'
      ? 'Clustering 개정이 변경되었습니다. 최신 구성과 수정 내용을 확인한 뒤 저장해 주세요.'
      : value instanceof Error ? value.message : 'Clustering을 불러오지 못했습니다.')
  }
  function initialize(value: PipelineView) {
    setData(value); setGroups(structuredClone(value.current?.groups || []))
    setIdentityFields(value.current?.identity_fields || ['object', 'check_point', 'mode'])
    setReason(value.current && value.current.action !== 'PROPOSE' ? value.current.reason : '')
    setCriteria(value.current?.criteria || ''); setReusable(value.current?.reusable || false)
    setCompatible(value.current?.compatible_versions || false); setSelected(new Set()); setDestination('new')
    setGroupId(previous => value.current?.groups.some(group => group.id === previous) || value.summary?.removed.some(group => group.id === previous)
      ? previous : value.current?.groups[0]?.id || '')
  }
  async function load(reset = true) {
    const value = await request<PipelineView>(`${base}/clustering`)
    if (alive.current) { if (reset) initialize(value); else setData(value); onChanged(value.summary) }
    return value
  }
  useEffect(() => {
    alive.current = true
    const controller = new AbortController()
    request<PipelineView>(`${base}/clustering`, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) initialize(value)
    }).catch(fail)
    return () => { alive.current = false; controller.abort() }
  }, [base])
  function edit() { pending.current = null; setNotice('') }
  async function change(action: ClusterEvent['action']) {
    if (!data) return
    const body = action === 'PROPOSE'
      ? { expected_revision: data.revision, mode: 'CRITERIA', identity_fields: identityFields }
      : { action, expected_revision: data.revision, reason, ...(action === 'EDIT' ? { groups } : {}),
          reusable: action === 'CONFIRM' && reusable, compatible_versions: action === 'CONFIRM' && reusable && compatible,
          criteria: action === 'CONFIRM' && reusable ? criteria : '' }
    const key = JSON.stringify(body)
    if (pending.current?.key !== key) pending.current = { key, id: crypto.randomUUID() }
    setBusy(true); setError(''); setNotice('')
    try {
      await request(`${base}/clustering${action === 'PROPOSE' ? '/proposals' : ''}`, {
        csrf: session.csrf, body: { ...body, request_id: pending.current.id },
      })
      await load(); pending.current = null
      if (alive.current) {
        setNotice(action === 'CONFIRM' ? 'Clustering을 확정했습니다. 그룹별 판정을 진행하세요.' : action === 'PROPOSE' ? '전체 로그의 분류 초안을 저장했습니다.' : '그룹 구성과 변경 이력을 저장했습니다.')
        if (action === 'CONFIRM') setStep(2)
      }
    } catch (value) {
      fail(value)
      if (value instanceof WorkspaceError && ['REVISION_CONFLICT', 'PROPOSAL_STALE'].includes(value.code)) {
        pending.current = null
        try { await load(false) } catch (refreshError) { fail(refreshError) }
      }
    } finally { if (alive.current) setBusy(false) }
  }
  function toggle(ids: string[], checked: boolean) {
    setSelected(previous => { const next = new Set(previous); ids.forEach(id => checked ? next.add(id) : next.delete(id)); return next })
  }
  function move() {
    if (!selected.size) return
    edit()
    const target = destination === 'new' ? `manual-${crypto.randomUUID()}` : destination
    const next = groups.map(group => ({ ...group, record_ids: group.record_ids.filter(id => !selected.has(id)) }))
    const existing = next.find(group => group.id === target)
    if (existing) existing.record_ids = [...new Set([...existing.record_ids, ...selected])].sort()
    else next.push({ id: target, label: `분리한 그룹 ${groups.length + 1}`, record_ids: [...selected].sort() })
    setGroups(next.filter(group => group.record_ids.length)); setSelected(new Set()); setDestination('new')
  }

  if (!data) return <div className="ws-review-loading" role="status">{error || '그룹 구성과 판정 개정을 불러오는 중입니다.'}{error && <Button variant="outline" onClick={() => load().catch(fail)}>다시 조회</Button>}</div>
  const current = data.current
  const chosen = current?.groups.find(group => group.id === groupId)
  const removed = data.summary?.removed.find(group => group.id === groupId)
  const members = chosen ? records.filter(record => chosen.record_ids.includes(record.id)) : []
  let sourceStatus = 'UNKNOWN'
  if (members.some(record => record.check_status === 'FAIL')) {
    sourceStatus = 'FAIL'
  } else if (members.length > 0 && members.every(record => record.check_status === 'PASS')) {
    sourceStatus = 'PASS'
  }
  const canWrite = data.can_edit && !busy
  const confirmed = current?.confirmed && !data.stale

  return <div className="ws-pipeline">
    <nav className="ws-pipeline-steps" aria-label="그룹 검토 단계">
      <button type="button" aria-current={step === 1 ? 'step' : undefined} onClick={() => setStep(1)}><b>01</b><span>Clustering<small>{confirmed ? '구성 확정' : current ? '구성 검토 중' : '초안 대기'}</small></span></button>
      <ArrowRight size={16} />
      <button type="button" aria-current={step === 2 ? 'step' : undefined} disabled={!confirmed || dirty || identityChanged} onClick={() => setStep(2)}><b>02</b><span>그룹별 판정<small>{data.summary ? `${data.summary.reviewed} / ${data.summary.total} 확정` : '분류 확정 후 검토'}</small></span></button>
    </nav>
    {error && <p className="ws-error" role="alert">{error}</p>}
    {notice && <p className="ws-notice" role="status"><Check size={15} />{notice}</p>}
    <p className="ws-muted">{current?.model_metadata ? `Codex mock · 초기 분류 모델 ${current.model_metadata.model} · 합성 report 검증 결과입니다. 새 초안과 판정 제안 버튼은 참고 엔진을 사용합니다. 사내 AI는 미연결입니다.` : '사내 AI 미연결 · 현재 초안은 확정 사례와 원본 필드 일치를 사용합니다. 각 단계는 사람이 별도로 확정합니다.'}</p>
    <div className="ws-pipeline-context"><span>원본 로그 {data.record_count}건 · clustering 개정 {data.revision}</span><span>다른 보고서의 확정 분류 사례 {data.case_count}건 · 재사용 {data.reusable_count}건</span><button type="button" disabled={busy} onClick={() => { setError(''); load().catch(fail) }}><RefreshCw size={13} />최신 구성 다시 조회</button></div>
    {step === 1 ? <section aria-label="1단계 Clustering">
      <h3>로그를 묶고 구성을 확정합니다.</h3>
      <p>같은 문제로 볼 조건을 선택해 초안을 만들고, 로그를 다른 그룹으로 옮기거나 새 그룹으로 분리할 수 있습니다. 모든 로그가 한 그룹에 배정되어야 합니다.</p>
      {data.can_edit && <div className="ws-pipeline-propose"><fieldset disabled={busy}><legend>초안에서 구분할 필드</legend><div>{Object.entries(names).map(([key, label]) => <label key={key}><input type="checkbox" checked={identityFields.includes(key)} disabled={key === 'mode' && records.some(record => !!record.fields.mode)} onChange={event => { edit(); setIdentityFields(previous => event.target.checked ? [...previous, key] : previous.filter(value => value !== key)) }} />{label}</label>)}</div><small>코드와 심각도는 항상 구분합니다. 필요한 값이 없거나 확정 사례가 충돌하면 분리해 검토합니다.</small></fieldset><Button variant="outline" disabled={!canWrite || !data.record_count || !identityFields.length} onClick={() => change('PROPOSE')}><Layers3 size={15} />{busy ? '처리 중…' : 'Clustering 초안 받기'}</Button></div>}
      {identityChanged && <p className="ws-muted">변경한 필드 선택은 새 초안을 받을 때 적용됩니다.</p>}
      {!data.ready && <p className="ws-notice">원본이 미수신 또는 파싱 미완료입니다. 전체 근거가 준비되기 전에는 clustering을 확정할 수 없습니다.</p>}
      {data.stale && <p className="ws-error">참고한 분류 기준이 변경되었습니다. 새 초안을 받거나 직접 그룹 구성을 검토·저장한 뒤 확정해 주세요.</p>}
      {current ? <>
        <div className="ws-partition-heading"><strong>{groups.length}개 그룹 · 로그 {groups.reduce((total, group) => total + group.record_ids.length, 0)} / {data.record_count}건</strong><span>{dirty ? '저장하지 않은 구성 변경' : confirmed ? '확정된 구성' : '검토 중인 초안'}</span></div>
        {data.can_edit && <div className="ws-partition-move"><span>선택 로그 {selected.size}건</span><label>이동할 그룹<select aria-label="이동할 그룹" value={destination} disabled={busy} onChange={event => setDestination(event.target.value)}><option value="new">새 그룹으로 분리</option>{groups.map(group => <option key={group.id} value={group.id}>{group.label}</option>)}</select></label><Button variant="outline" disabled={!canWrite || !selected.size} onClick={move}>선택 로그 이동</Button></div>}
        <div className="ws-partition-groups">{groups.map((group, index) => <article key={group.id} aria-label={`분류 그룹 ${index + 1}`}><header>{data.can_edit && <input type="checkbox" aria-label={`${group.label} 전체 로그 선택`} disabled={busy} checked={group.record_ids.every(id => selected.has(id))} onChange={event => toggle(group.record_ids, event.target.checked)} />}<label><span>그룹 {index + 1} 이름</span><input value={group.label} aria-label={`그룹 ${index + 1} 이름`} maxLength={120} disabled={!canWrite} onChange={event => { edit(); setGroups(previous => previous.map(value => value.id === group.id ? { ...value, label: event.target.value } : value)) }} /></label><strong>{group.record_ids.length}건</strong></header><ul>{group.record_ids.map(id => { const record = records.find(value => value.id === id); return <li key={id}>{data.can_edit && <input type="checkbox" aria-label={`${id} 로그 선택`} disabled={busy} checked={selected.has(id)} onChange={event => toggle([id], event.target.checked)} />}<div><code>{id}</code><span>원문 {record?.line_start}–{record?.line_end}행 · 규칙 {labels[record?.check_status || 'UNKNOWN']}</span><details><summary>로그 근거 보기</summary><pre>{record?.raw}</pre></details></div></li> })}</ul></article>)}</div>
        {current.references.length > 0 && <details className="ws-cluster-references"><summary>참고한 확정 clustering {current.references.length}건</summary>{current.references.map(reference => <p key={reference.id}>{reference.report_id} · 개정 {reference.revision}<small>{reference.criteria}</small></p>)}</details>}
        {data.can_edit && <div className="ws-cluster-confirm"><div className="ws-review-field"><label htmlFor={`${inputId}-reason`}>Clustering 검토 사유</label><textarea id={`${inputId}-reason`} value={reason} maxLength={2000} disabled={busy} onChange={event => { edit(); setReason(event.target.value) }} placeholder="동일 문제로 묶거나 분리한 이유를 기록하세요." /></div><label className="ws-review-checkbox"><input type="checkbox" checked={reusable} disabled={busy} onChange={event => { edit(); setReusable(event.target.checked); if (!event.target.checked) setCompatible(false) }} />이 clustering 결과를 다음 보고서에서 재사용</label>{reusable && <><div className="ws-review-field"><label htmlFor={`${inputId}-criteria`}>재사용할 분류 기준</label><textarea id={`${inputId}-criteria`} value={criteria} maxLength={2000} disabled={busy} onChange={event => { edit(); setCriteria(event.target.value) }} /></div><p className="ws-muted">현재 엔진은 같은 원본 필드로 구분할 수 있는 확정 사례만 재사용합니다. 구분할 수 없는 분리나 사례 충돌은 사람이 다시 확인합니다.</p><label className="ws-review-checkbox"><input type="checkbox" checked={compatible} disabled={busy} onChange={event => { edit(); setCompatible(event.target.checked) }} />같은 블록의 호환되는 다른 버전에도 적용</label></>}
          <div className="ws-cluster-actions"><Button variant="outline" disabled={!canWrite || !reason.trim() || identityChanged} onClick={() => change('EDIT')}>그룹 구성 저장</Button><Button disabled={!canWrite || !data.ready || dirty || identityChanged || confirmed || !reason.trim() || reusable && !criteria.trim()} onClick={() => change('CONFIRM')}>Clustering 확정</Button>{current.confirmed && <Button variant="outline" disabled={!canWrite || !reason.trim()} onClick={() => change('WITHDRAW')}>확정 해제</Button>}</div>{dirty && <p className="ws-muted">변경한 그룹 구성을 먼저 저장한 뒤 확정하세요.</p>}</div>}
      </> : <p className="ws-pipeline-empty">아직 clustering 초안이 없습니다. 원본 로그가 준비되면 담당자가 초안을 만들 수 있습니다.</p>}
      <details className="ws-cluster-history"><summary>Clustering 변경 이력 {data.history.length}건</summary>{data.history.map(event => <article key={event.id}><strong>{actionNames[event.action]} · 개정 {event.revision} · {event.groups.length}개 그룹</strong><span>{event.actor.name} · {time(event.at)}</span><p>{event.reason}</p><details><summary>당시 그룹 구성</summary>{event.groups.map(group => <p key={group.id}>{group.label}<code>{group.record_ids.join(', ')}</code></p>)}</details></article>)}</details>
    </section> : <section aria-label="2단계 그룹별 판정"><h3>확정 그룹마다 판정과 근거를 검토합니다.</h3><p>원본 규칙 결과, 제안, 사람의 최종 판정을 구분합니다. 그룹 구성이 바뀌면 이전 판정은 재검토 대상이 됩니다.</p>
      {(!confirmed || dirty) && <p className="ws-notice">1단계에서 현재 그룹 구성을 저장·확정해 주세요. 이전 그룹의 판정 이력은 계속 확인할 수 있습니다.</p>}
      {current && <><div className="ws-pipeline-group-picker" role="group" aria-label="판정할 그룹 선택">{current.groups.map(group => { const summary = data.summary?.groups[group.id]; return <button type="button" key={group.id} aria-pressed={groupId === group.id} onClick={() => setGroupId(group.id)}><strong>{group.label}</strong><span>{group.record_ids.length}건 · {summary?.stale ? '재검토 필요' : summary?.conclusion ? labels[summary.conclusion] : '미검토'}</span></button> })}</div>
        {!!data.summary?.removed.length && <details><summary>이전 구성에서 제거된 그룹의 판정 이력</summary>{data.summary.removed.map(group => <button type="button" className="ws-removed-group" key={group.id} onClick={() => setGroupId(group.id)}>{group.label} · 이전 {labels[group.conclusion]}</button>)}</details>}
        {(chosen || removed) && !dirty && <div className="ws-group-review" aria-label="선택 그룹 검토"><header><h4>{chosen?.label || removed?.label}</h4><code>{groupId}</code></header>{chosen && <details><summary>이 그룹의 원본 근거 {members.length}건</summary>{members.map(record => <pre key={record.id}>{record.id} · 원문 {record.line_start}–{record.line_end}행{'\n'}{record.raw}</pre>)}</details>}<WorkspaceReview key={`${groupId}:${data.revision}`} report={{ ...report, check_status: sourceStatus }} session={session} group={{ id: groupId, cluster_revision: data.revision }} onExpired={onExpired} onSaved={() => { load().catch(fail) }} /></div>}
      </>}
    </section>}
  </div>
}
