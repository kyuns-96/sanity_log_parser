import { useEffect, useState } from 'react'
import { Layers3 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { PaginationBar } from '@/components/ui/pagination-bar'
import AiModelSelect from '@/features/ai/AiModelSelect'
import CaseSource from './CaseSource'
import Records from './Records'
import WorkspaceReview from './WorkspaceReview'
import { workspaceLabels as labels, workspaceRequest as request, workspaceTime as time } from './workspaceApi'
import type { ClusterBrief, CodexCapability, PageResult, PipelineTask, WorkbenchView, WorkspaceSession } from './workspaceApi'

const n = (value: number) => value.toLocaleString('ko-KR')
const actions: Record<string, string> = { PROPOSE: '분류 초안', EDIT: '구성 수정', CONFIRM: '분류 확정', WITHDRAW: '확정 해제' }
const activeTask = (task: PipelineTask | null) => !!task && ['PENDING', 'RUNNING', 'FAILED', 'STALE'].includes(task.status)

export interface WorkbenchSupportDrawerProps {
  base: string
  inputId: string
  data: WorkbenchView
  session: WorkspaceSession
  production: boolean
  step: 1 | 2
  historyEvent: string | null
  writable: boolean
  confirmed: boolean
  busy: boolean
  aiRunning: boolean
  recoverableUnknown: boolean
  dirtyName: boolean
  dirtyRationale: boolean
  connectionId: string
  choice?: CodexCapability
  modelId: string
  reason: string
  reusable: boolean
  compatible: boolean
  criteria: string
  fields: string[]
  refresh: number
  onConnectionChange: (value: string) => void
  onModelChange: (value: string) => void
  onReasonChange: (value: string) => void
  onReusableChange: (checked: boolean) => void
  onCompatibleChange: (checked: boolean) => void
  onCriteriaChange: (value: string) => void
  onFieldsChange: (fields: string[]) => void
  onWithdraw: () => void
  onProposeAI: () => void
  onProposeEngine: () => void
  onSelectHistoryEvent: (eventId: string) => void
  onReload: () => void
  onExpired: () => void
  onError: (cause: unknown) => void
}

export function WorkbenchSupportDrawer({
  base,
  inputId,
  data,
  session,
  production,
  step,
  historyEvent,
  writable,
  confirmed,
  busy,
  aiRunning,
  recoverableUnknown,
  dirtyName,
  dirtyRationale,
  connectionId,
  choice,
  modelId,
  reason,
  reusable,
  compatible,
  criteria,
  fields,
  refresh,
  onConnectionChange,
  onModelChange,
  onReasonChange,
  onReusableChange,
  onCompatibleChange,
  onCriteriaChange,
  onFieldsChange,
  onWithdraw,
  onProposeAI,
  onProposeEngine,
  onSelectHistoryEvent,
  onReload,
  onExpired,
  onError,
}: WorkbenchSupportDrawerProps) {
  const [showSource, setShowSource] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [history, setHistory] = useState<PageResult<ClusterBrief> | null>(null)
  const [historyOffset, setHistoryOffset] = useState(0)
  const [showRemoved, setShowRemoved] = useState(false)
  const [removed, setRemoved] = useState<PageResult<{ id: string; label: string; conclusion: string }> | null>(null)
  const [removedOffset, setRemovedOffset] = useState(0)
  const [removedId, setRemovedId] = useState('')

  const task = data.task
  const model = data.current?.model_metadata

  useEffect(() => {
    if (!showHistory) return
    const controller = new AbortController()
    request<PageResult<ClusterBrief>>(`${base}/clustering/history?offset=${historyOffset}&limit=25`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setHistory(value) })
      .catch(onError)
    return () => controller.abort()
  }, [base, showHistory, historyOffset, refresh])

  useEffect(() => {
    if (!showRemoved) return
    const controller = new AbortController()
    request<typeof removed>(`${base}/clustering/history?removed=true&offset=${removedOffset}&limit=25`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setRemoved(value) })
      .catch(onError)
    return () => controller.abort()
  }, [base, showRemoved, removedOffset, refresh])

  return (
    <details className="ws-workbench-support">
      <summary>설정과 이전 기록</summary>
      <div className="ws-workbench-support-content">
        <details className="ws-report-context">
          <summary>보고서 정보</summary>
          <dl>
            <div><dt>보고서</dt><dd>{data.report.id}</dd></div>
            <div><dt>검사 항목</dt><dd>{data.report.item_id} · {data.report.family}</dd></div>
            <div><dt>버전</dt><dd>{data.version.net_ver} · {data.version.layout} · {data.version.revision}</dd></div>
            <div><dt>ECO</dt><dd>{data.report.eco || '해당 없음'}</dd></div>
            <div><dt>원문</dt><dd>{n(data.raw_line_count)}행 · 로그 {n(data.record_count)}건</dd></div>
            <div><dt>원본 SHA-256</dt><dd><code>{data.source_sha256}</code></dd></div>
          </dl>
        </details>
        {step === 1 && data.current && data.can_edit && !historyEvent && (
          <details>
            <summary>{production ? '전체 분류 메모 (선택)' : '전체 메모와 참고 엔진 설정 (선택)'}</summary>
            <div className="ws-review-field">
              <label htmlFor={`${inputId}-reason`}>전체 분류 메모 (선택)</label>
              <textarea id={`${inputId}-reason`} value={reason} maxLength={2000} disabled={busy} onChange={event => onReasonChange(event.target.value)} placeholder="전체 분류에 대한 추가 검토 내용을 입력하세요." />
            </div>
            {!production && !model?.synthetic_only && (
              <div className="ws-reuse-options">
                <p className="ws-muted">운영 AI 참고에는 사용자가 등록한 근거만 제공합니다. 등록한 지식은 블록·revision·ECO를 넘어 비교합니다. 아래 설정은 AI 참고와 별개인 참고 엔진의 조건 일치 규칙용입니다.</p>
                <label className="ws-review-checkbox">
                  <input type="checkbox" checked={reusable} disabled={busy} onChange={event => onReusableChange(event.target.checked)} />
                  동일 조건의 참고 엔진에 분류 규칙 적용 허용
                </label>
                {reusable && (
                  <>
                    <div className="ws-review-field">
                      <label htmlFor={`${inputId}-criteria`}>재사용할 분류 기준</label>
                      <textarea id={`${inputId}-criteria`} value={criteria} maxLength={2000} disabled={busy} onChange={event => onCriteriaChange(event.target.value)} />
                    </div>
                    <label className="ws-review-checkbox">
                      <input type="checkbox" checked={compatible} disabled={busy} onChange={event => onCompatibleChange(event.target.checked)} />
                      같은 블록의 호환되는 다른 버전에도 적용
                    </label>
                  </>
                )}
              </div>
            )}
            {confirmed && (
              <>
                <p className="ws-muted">확정 해제 사유를 전체 분류 메모에 입력하세요.</p>
                <Button variant="outline" disabled={!writable || !reason.trim()} onClick={onWithdraw}>분류 확정 해제</Button>
              </>
            )}
          </details>
        )}
        {step === 1 && data.current && data.can_edit && !historyEvent && data.ai?.enabled && data.ai.supported && (
          <details className="ws-rebuild-groups">
            <summary>AI로 그룹 다시 만들기</summary>
            {production && <AiModelSelect ai={data.ai} connectionValue={connectionId} onConnectionChange={onConnectionChange} value={modelId} onChange={onModelChange} disabled={!writable || !data.ready} label="분류 AI 모델" />}
            <p className="ws-muted">{production ? '전체 로그와 관련 확정 사례를 OA AI가 검토합니다. 새 초안과 이전 구성은 이력으로 남습니다.' : '현재 그룹 구성을 새 초안으로 대체하며 기존 구성은 이력에 보존합니다. 합성 보고서와 검토 근거가 외부 AI에 전달됩니다.'}</p>
            <Button variant="outline" disabled={!writable || !data.ready || !choice?.available || dirtyName || dirtyRationale || recoverableUnknown} onClick={onProposeAI}><Layers3 size={15} />AI로 그룹 만들기</Button>
            <small>{choice?.connection_label ? `${choice.connection_label} / ` : ''}{modelId}{!production && <> · 남은 테스트 {data.ai.remaining}회</>}</small>
          </details>
        )}
        {step === 1 && data.can_edit && !historyEvent && !production && (
          <details className="ws-workbench-settings">
            <summary>분류 설정</summary>
            <p className="ws-muted">{data.ai?.enabled && data.ai.supported ? `합성 report 전용 · ${data.ai.model} 실제 호출 · 남은 테스트 ${data.ai.remaining}회. 새 초안은 기존 확정을 대체하며 이전 구성은 이력으로 남습니다. 이 테스트에 작성한 그룹 이름·검토 사유도 외부 모델에 전달됩니다.` : '사내 AI 미연결 · 현재 초안과 판정 제안은 확정 사례와 원본 필드를 참고하는 엔진입니다.'}</p>
            <div className="ws-workbench-propose">
              <fieldset disabled={busy || activeTask(task) || aiRunning}>
                <legend>참고 엔진에서 구분할 필드</legend>
                {Object.entries({ object: '대상', check_point: '검사 지점', mode: '모드', observed: '측정값', expected: '기준값', unit: '단위' }).map(([key, name]) => (
                  <label key={key}>
                    <input type="checkbox" checked={fields.includes(key)} disabled={key === 'mode'} onChange={event => onFieldsChange(event.target.checked ? [...fields, key] : fields.filter(value => value !== key))} />
                    {name}
                  </label>
                ))}
              </fieldset>
              <Button variant="outline" disabled={!writable || !data.ready || dirtyRationale} onClick={onProposeEngine}><Layers3 size={15} />참고 엔진으로 초안 생성</Button>
            </div>
          </details>
        )}
        {data.current && (
          <details className="ws-cluster-references">
            <summary>현재 분류의 기준과 출처</summary>
            <p>{data.current.reason}<small>구분 필드: {data.current.identity_fields.join(', ')} · {data.current.actor.name} · {time(data.current.at)}</small></p>
            {model && <p>초기 분류 모델: {model.model}<small>입력 사례 {model.input_case_count}건 · 입력 토큰 {n(model.usage.input_tokens || 0)} · 출력 토큰 {n(model.usage.output_tokens || 0)} · {model.seconds}초</small></p>}
            {data.current.references.map(reference => (
              <p key={reference.id}>{reference.report_id} · 분류 개정 {reference.revision}<small>{reference.criteria || '공유 확정 분류 사례'}</small><CaseSource value={reference} report={data.report} version={data.version} /></p>
            ))}
            <p className="ws-muted">{production ? data.case_count_scope === 'REFERENCED' ? `현재 분류에서 참조한 유효 사례 ${n(data.case_count)}건 · 전체 누적 건수가 아닙니다. AI 분류 실행 시 전체 로그로 관련 사례를 검색하며 원문은 기존 조회 권한을 따릅니다.` : `이번 조회에서 선정한 관련 분류 후보 ${n(data.case_count)}건 · 현재 분류의 인용 사례 ${data.current.references.length}건. 전체 누적 건수가 아닙니다. 블록·revision·ECO를 넘어 관련 사례를 찾고 이번 조건과의 차이를 다시 확인합니다. 과거 확정 근거를 참고하며 원문은 기존 조회 권한을 따릅니다.` : `다른 report의 확정 사례 ${n(data.case_count)}건 · 참고 엔진 재사용 기준 ${n(data.reusable_count)}건`}</p>
          </details>
        )}
        {task && (
          <details className="ws-workbench-task-history">
            <summary><span>{task.status === 'COMPLETED' ? task.id === data.current?.id ? '분류 초안 생성 완료' : '이전 참고 엔진의 분류 작업' : '분류 작업 근거'}</span> · {n(task.processed)} / {n(task.record_count)}건</summary>
            {task.chunks.map(chunk => <p key={chunk.index}>{n(chunk.start + 1)}–{n(chunk.stop)}번째 로그 · {chunk.record_count}건 · {chunk.status}<code>{chunk.input_sha256}</code></p>)}
          </details>
        )}
        <details className="ws-workbench-source" onToggle={event => setShowSource(event.currentTarget.open)}>
          <summary>원본 로그 검색·조회 · {n(data.record_count)}건</summary>
          {showSource && <Records base={base} revision={data.revision} selected={new Set()} onSelect={() => {}} onError={onError} />}
        </details>
        <details className="ws-cluster-history" onToggle={event => setShowHistory(event.currentTarget.open)}>
          <summary>분류 변경 이력</summary>
          {showHistory && history && (
            <>
              {history.items.map(event => (
                <article key={event.id}>
                  <strong>{actions[event.action]} · 개정 {event.revision} · {n(event.group_count)}개 그룹</strong>
                  <span>{event.actor.name} · {time(event.at)}</span>
                  <p>{event.reason}</p>
                  <Button variant="outline" size="sm" onClick={() => onSelectHistoryEvent(event.id)}>당시 그룹 구성 조회</Button>
                </article>
              ))}
              <PaginationBar className="ws-pagination" offset={history.offset} limit={history.limit} total={history.total} label="분류 이력" onChange={setHistoryOffset} />
            </>
          )}
        </details>
        {!!data.removed_count && (
          <details onToggle={event => setShowRemoved(event.currentTarget.open)}>
            <summary>이전 구성에서 제거된 그룹의 판정 이력 · {data.removed_count}개</summary>
            {showRemoved && removed && (
              <>
                {removed.items.map(group => <button className="ws-removed-group" key={group.id} onClick={() => setRemovedId(group.id)}>{group.label} · 이전 {labels[group.conclusion]}</button>)}
                <PaginationBar className="ws-pagination" offset={removed.offset} limit={removed.limit} total={removed.total} label="제거된 그룹" onChange={setRemovedOffset} />
                {removedId && <WorkspaceReview key={removedId} compact report={data.report} version={data.version} session={session} group={{ id: removedId, cluster_revision: data.revision }} onExpired={onExpired} onSaved={onReload} />}
              </>
            )}
          </details>
        )}
      </div>
    </details>
  )
}
