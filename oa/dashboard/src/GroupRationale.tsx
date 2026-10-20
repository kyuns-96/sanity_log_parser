import { useEffect, useId, useState } from 'react'
import { Check, Pencil } from 'lucide-react'
import { Button } from './components/ui/button'
import { workspaceRequest as request, workspaceTime as time } from './workspaceApi'
import type { RationaleContent, RationaleDetail } from './workspaceApi'

export const rationaleLabels = { MISSING: '근거 누락', DRAFT: '검토 전', CONFIRMED: '검토 완료', STALE: '재검토' }
const sources: Record<string, string> = { AI: 'AI 제안', HUMAN: '사용자 작성', REFERENCE_ENGINE: '참고 엔진 제안', MEMORY_DEMO: '합성 데모 초안 · AI 호출 없음' }

export default function GroupRationale({ base, groupId, revision, eventId, editable, busy, selected,
  onSave, onDirty, onError }: { base: string; groupId: string; revision: number; eventId: string | null;
  editable: boolean; busy: boolean; selected: Set<string>; onSave: (body: Record<string, unknown>) => Promise<boolean>;
  onDirty: (value: boolean) => void; onError: (value: unknown) => void }) {
  const id = useId()
  const [data, setData] = useState<RationaleDetail | null>(null)
  const [editing, setEditing] = useState(false)
  const [reason, setReason] = useState('')
  const [evidence, setEvidence] = useState<string[]>([])
  const [references, setReferences] = useState<RationaleContent['reference_groups']>([])
  const [change, setChange] = useState('')
  const [historyOffset, setHistoryOffset] = useState(0)
  const [showHistory, setShowHistory] = useState(false)
  const [moreSamples, setMoreSamples] = useState(false)
  const [moreReason, setMoreReason] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams({ expected_revision: String(revision), offset: String(historyOffset), limit: '5' })
    if (eventId) params.set('event_id', eventId)
    request<RationaleDetail>(`${base}/groups/${groupId}/rationale?${params}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setData(value) }).catch(error => { if (!controller.signal.aborted) onError(error) })
    return () => controller.abort()
  }, [base, groupId, revision, eventId, historyOffset])
  useEffect(() => { onDirty(editing); return () => onDirty(false) }, [editing])

  if (!data) return <section className="ws-rationale" aria-label="그룹별 분류 근거" aria-busy="true"><p className="ws-muted">그룹 근거를 불러오는 중…</p></section>
  const row = data.rationale
  const fullReason = row?.content.reason || ''
  const reasonCharacters = Array.from(fullReason)
  const omitted = moreReason ? 0 : Math.max(0, reasonCharacters.length - 240)
  function edit() {
    setReason(row?.content.reason || ''); setEvidence(row?.content.evidence_ids || [])
    setReferences(row?.content.reference_groups || []); setChange(''); setEditing(true)
  }
  async function save(reviewed: boolean) {
    const saved = await onSave({ action: 'SAVE', content: { reason, evidence_ids: evidence, reference_groups: references }, reason: change, reviewed })
    if (saved) setEditing(false)
  }
  return <section className="ws-rationale" aria-label="그룹별 분류 근거" data-rationale-state={data.status}>
    <div className="ws-rationale-heading"><h4>분류 근거</h4><span className={`ws-rationale-state ws-rationale-${data.status.toLowerCase()}`}>{rationaleLabels[data.status]}</span></div>
    {!editing ? <>
      {row ? <><p className="ws-rationale-text">{omitted ? `${reasonCharacters.slice(0, 240).join('')}…` : fullReason}</p>{reasonCharacters.length > 240 && <button className="ws-text-action" type="button" aria-expanded={moreReason} onClick={() => setMoreReason(!moreReason)}>{omitted ? `근거 전문 보기 · ${omitted}자 생략` : '근거 접기'}</button>}<p className="ws-rationale-meta">{sources[row.source] || row.source}{row.reviewed_by && ` · ${row.reviewed_by.name} 확인`}</p></>
        : <p className="ws-muted">저장된 분류 근거가 없습니다. 공통 패턴과 구분 기준을 작성하고 대표 로그를 지정하세요.</p>}
      {data.status === 'STALE' && <p className="ws-rationale-warning">그룹 구성 또는 이름이 변경되었습니다. 현재 분류 근거를 재검토하세요.</p>}
      {row && data.samples.length !== row.content.evidence_ids.length && <p className="ws-rationale-warning">대표 로그가 다른 그룹으로 이동했습니다. 분류 근거와 대표 로그를 갱신하세요.</p>}
      {editable && <div className="ws-rationale-actions">{row && data.status !== 'CONFIRMED' && <Button size="sm" disabled={busy || data.samples.length !== row.content.evidence_ids.length} onClick={() => onSave({ action: 'ACCEPT', reason: '현재 그룹의 분류 근거·대표 로그·참고 사례를 검토하여 채택했습니다.' })}><Check size={14} />근거 채택</Button>}<Button variant="ghost" size="sm" disabled={busy} onClick={edit}><Pencil size={14} />{row ? '근거 수정' : '근거 작성'}</Button></div>}
      {row && <details className="ws-rationale-samples"><summary>대표 근거 로그 {row.content.evidence_ids.length}건 보기</summary>{data.samples.slice(0, moreSamples ? 20 : 3).map(sample => <details key={sample.id}><summary>원문 {sample.line_start}–{sample.line_end}행 <code>{sample.id}</code></summary><pre>{sample.raw}</pre>{sample.raw_display_truncated && <small>표시 길이를 초과한 원문은 보고서에서 확인하세요.</small>}</details>)}
        {data.samples.length > 3 && <button type="button" onClick={() => setMoreSamples(!moreSamples)}>{moreSamples ? '대표 로그 접기' : `대표 로그 ${data.samples.length}건 모두 보기`}</button>}
      </details>}
    </> : <div className="ws-rationale-editor">
      <label htmlFor={`${id}-reason`}>분류 기준<textarea aria-label="분류 기준" id={`${id}-reason`} value={reason} maxLength={2000} disabled={busy} onChange={e => setReason(e.target.value)} placeholder="공통 패턴, 다른 그룹과의 구분 기준 및 예외를 작성하세요." /></label>
      <div><span className="ws-caption">대표 로그 {evidence.length} / 20건</span><p className="ws-muted">로그 목록에서 선택한 항목을 대표 근거로 지정할 수 있습니다.</p><Button variant="outline" size="sm" disabled={busy || !selected.size || selected.size > 20} onClick={() => setEvidence([...selected])}>선택한 로그를 대표 근거로 지정</Button><div className="ws-rationale-evidence">{evidence.map(key => <button key={key} type="button" disabled={busy} onClick={() => setEvidence(evidence.filter(value => value !== key))} aria-label={`대표 로그 ${key} 제거`}>{key} ×</button>)}</div></div>
      {!!references.length && <div><p className="ws-muted">참고한 과거 그룹 {references.length}건. 만료된 사례를 더 이상 적용하지 않으면 연결을 해제하고 수정 사유를 남기세요.</p><Button variant="outline" size="sm" disabled={busy} onClick={() => setReferences([])}>과거 참고 사례 연결 해제</Button></div>}
      <label htmlFor={`${id}-change`}>작성·수정 사유<textarea aria-label="작성·수정 사유" id={`${id}-change`} value={change} maxLength={2000} disabled={busy} onChange={e => setChange(e.target.value)} placeholder="근거를 작성하거나 수정한 사유를 입력하세요." /></label>
      <div className="ws-rationale-actions"><Button variant="ghost" size="sm" disabled={busy} onClick={() => setEditing(false)}>취소</Button><Button variant="outline" size="sm" disabled={busy || !reason.trim() || !change.trim() || !evidence.length || evidence.length > 20} onClick={() => save(false)}>초안 저장</Button><Button size="sm" disabled={busy || !reason.trim() || !change.trim() || !evidence.length || evidence.length > 20} onClick={() => save(true)}>근거 저장·확인</Button></div>
    </div>}
    <details className="ws-rationale-more"><summary>원안·참고 사례·이력</summary>
    {row?.proposal && <details className="ws-rationale-disclosure"><summary>{sources[row.proposal.source]} 원안</summary><p className="ws-rationale-text">{row.proposal.content.reason}</p><small>최초 제안 개정 ID: {row.proposal.event_id}</small></details>}
    {!!row?.content.reference_groups.length && <details className="ws-rationale-disclosure"><summary>참고한 과거 확정 그룹 · {row.content.reference_groups.length}건</summary>{row.content.reference_groups.map(ref => {
      const source = data.references.find(value => value.id === ref.case_id && value.group.id === ref.group_id)
      return <article key={`${ref.case_id}:${ref.group_id}`}><strong>{source ? `${source.report_id} · 개정 ${source.revision} · ${source.group.label}` : '만료되었거나 더 이상 사용할 수 없는 사례'}</strong><p>{ref.reason}</p><small>{ref.case_id} / {ref.group_id}</small>{source?.rationale && <p>{source.rationale.content.reason}</p>}</article>
    })}</details>}
    <details className="ws-rationale-disclosure" onToggle={e => setShowHistory(e.currentTarget.open)}><summary>이 그룹의 근거 변경 이력 · {data.history.total}건</summary>{showHistory && <>{data.history.items.map(event => <article key={event.id}><strong>분류 개정 {event.revision} · {event.actor.name}</strong><small>{time(event.at)} · {rationaleLabels[event.rationale.status]}</small><p>{event.rationale.content.reason}</p>{event.rationale.change_reason && <p className="ws-muted">변경·확인 사유: {event.rationale.change_reason}</p>}</article>)}{data.history.total > 5 && <div className="ws-rationale-actions"><Button variant="outline" size="sm" disabled={!historyOffset} onClick={() => setHistoryOffset(Math.max(0, historyOffset-5))}>이전 근거 이력</Button><span>{historyOffset+1}–{Math.min(historyOffset+5, data.history.total)} / {data.history.total}</span><Button variant="outline" size="sm" disabled={historyOffset+5 >= data.history.total} onClick={() => setHistoryOffset(historyOffset+5)}>다음 근거 이력</Button></div>}</>}</details>
    </details>
  </section>
}
