import { useEffect, useId, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Check, ChevronLeft, ChevronRight, Download, FileText, FolderTree, GitBranch, GripVertical, Layers3, LoaderCircle, Pencil, Plus, RefreshCw, ShieldCheck, X } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Badge } from './studio/components/ui/badge'
import { Checkbox } from './studio/components/ui/checkbox'
import { Input } from './studio/components/ui/input'
import Records from './live-workbench/Records'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './components/ui/dialog'
import WorkspaceReview from './WorkspaceReview'
import AiModelSelect, { selectedAiModel } from './AiModelSelect'
import GroupRationale, { rationaleLabels } from './GroupRationale'
import CaseSource from './CaseSource'
import PreservedSources, { PreservedCitations } from './PreservedSources'
import { NEW_LOG_GROUP, useLogDrag } from './useLogDrag'
import type { LogMove } from './useLogDrag'
import { aiJobActive, aiJobMessage, workspaceApiBase, WorkspaceError, toolResultStatus, workspaceLabels as labels, workspaceRequest as request, workspaceTime as time } from './workspaceApi'
import type { ClusterBrief, CodexJob, GroupBrief, GroupRecords, PageResult, PipelineTask, WorkbenchView, WorkspaceSession } from './workspaceApi'

const n = (value: number) => value.toLocaleString('ko-KR')
const activeTask = (task: PipelineTask | null) => !!task && ['PENDING', 'RUNNING', 'FAILED', 'STALE'].includes(task.status)
const statusLabels: Record<string, string> = { PENDING: '미검토', STALE: '재검토', PASS: 'PASS', FAIL: 'FAIL', DEFER: '보류' }
const actions: Record<string, string> = { PROPOSE: '분류 초안', EDIT: '구성 수정', CONFIRM: '분류 확정', WITHDRAW: '확정 해제' }

function Pager({ total, offset, limit, onChange, label }: { total: number; offset: number; limit: number; onChange: (offset: number) => void; label: string }) {
  return <div className="ws-pagination"><span>{total ? `${n(offset+1)}–${n(Math.min(offset+limit, total))}` : '0'} / {n(total)}</span><Button variant="outline" size="icon" aria-label={`이전 ${label} 페이지`} disabled={!offset} onClick={() => onChange(Math.max(0, offset-limit))}><ChevronLeft /></Button><Button variant="outline" size="icon" aria-label={`다음 ${label} 페이지`} disabled={offset+limit >= total} onClick={() => onChange(offset+limit)}><ChevronRight /></Button></div>
}

export default function WorkspaceWorkbench({ reportId, blockId, versionId, session, context, onBack, onExpired, onChanged }: {
  reportId: string; blockId: string; versionId: string; session: WorkspaceSession; context?: { projectName?: string; blockName?: string; porLabel?: string }; onBack: () => void; onExpired: () => void; onChanged: (value: WorkbenchView) => void
}) {
  const base = `/blocks/${blockId}/versions/${versionId}/reports/${reportId}`
  const demo = session.environment === 'MEMORY_DEMO'
  const production = session.environment === 'PRODUCTION_SSO'
  const inputId = useId()
  const [data, setData] = useState<WorkbenchView | null>(null)
  const [step, setStep] = useState<1 | 2>(1)
  const [groups, setGroups] = useState<PageResult<GroupBrief> | null>(null)
  const [groupOffset, setGroupOffset] = useState(0)
  const [groupSearch, setGroupSearch] = useState('')
  const [filter, setFilter] = useState('')
  const [rationaleFilter, setRationaleFilter] = useState('')
  const [dirtyRationale, setDirtyRationale] = useState(false)
  const [groupId, setGroupId] = useState('')
  const [member, setMember] = useState<GroupRecords | null>(null)
  const [selectedRecords, setSelectedRecords] = useState<Set<string>>(new Set())
  const [selectedGroups, setSelectedGroups] = useState<Set<string>>(new Set())
  const [mergeMode, setMergeMode] = useState(false)
  const [groupName, setGroupName] = useState('')
  const [newName, setNewName] = useState('새로 분리한 그룹')
  const [reason, setReason] = useState('')
  const [criteria, setCriteria] = useState('')
  const [reusable, setReusable] = useState(false)
  const [compatible, setCompatible] = useState(false)
  const [fields, setFields] = useState(['object', 'check_point', 'mode'])
  const [busy, setBusy] = useState(false)
  const [groupNavigating, setGroupNavigating] = useState(false)
  const [aiSubmitting, setAiSubmitting] = useState(false)
  const [selectedModel, setSelectedModel] = useState('')
  const [pollPaused, setPollPaused] = useState(false)
  const polls = useRef({ id: '', count: 0 })
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [showSource, setShowSource] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [history, setHistory] = useState<PageResult<ClusterBrief> | null>(null)
  const [historyOffset, setHistoryOffset] = useState(0)
  const [historyEvent, setHistoryEvent] = useState<string | null>(null)
  const [showRemoved, setShowRemoved] = useState(false)
  const [removed, setRemoved] = useState<PageResult<{ id: string; label: string; conclusion: string }> | null>(null)
  const [removedOffset, setRemovedOffset] = useState(0)
  const [removedId, setRemovedId] = useState('')
  const panel = useRef<HTMLElement>(null)
  const [nameEditing, setNameEditing] = useState(false)
  const [editor, setEditor] = useState<'rename' | 'split' | 'merge' | null>(null)
  const [editReason, setEditReason] = useState('')
  const [splitMove, setSplitMove] = useState<LogMove | null>(null)
  const selectionGroups = useRef(new Map<string, string>())
  const alive = useRef(true)
  const run = useRef(0)
  const pending = useRef<{ key: string; id: string } | null>(null)
  const newGroup = useRef<string | null>(null)
  const lastRevision = useRef(-1)
  const aiRunning = aiJobActive(data?.ai?.job)
  const modelId = aiRunning ? data!.ai!.job!.model : selectedAiModel(data?.ai, selectedModel)
  const confirmed = !!data?.summary.confirmed && !activeTask(data.task) && !aiRunning
  const dirtyName = !!member?.group && groupName !== member.group.label
  const identityChanged = !!data?.current && JSON.stringify([...fields].sort()) !== JSON.stringify([...data.current.identity_fields].sort())
  const writable = !!data?.can_edit && !busy && !groupNavigating && !historyEvent && !activeTask(data.task) && !aiRunning
  const drag = useLogDrag({ scope: panel, enabled: writable && step === 1 && !editor && !dirtyRationale, revision: data?.revision ?? -1, onDrop: moveLogs })

  function fail(value: unknown) {
    if (value instanceof DOMException && value.name === 'AbortError') return
    if (value instanceof WorkspaceError && value.code === 'UNAUTHORIZED') { onExpired(); return }
    if (alive.current) setError(value instanceof WorkspaceError && value.code === 'REVISION_CONFLICT'
      ? '분류 개정이 변경되었습니다. 최신 구성을 다시 조회하고 수정 내용을 확인하세요.'
      : value instanceof Error ? value.message : '작업을 완료하지 못했습니다.')
  }
  function initialize(value: WorkbenchView) {
    if (lastRevision.current !== value.revision) {
      setSelectedGroups(new Set()); setSelectedRecords(new Set()); selectionGroups.current.clear(); setGroupId(''); setGroupOffset(0); setGroups(null); setMember(null); setNameEditing(false)
      setEditor(null); setSplitMove(null)
      setReason(''); setFields(value.current?.identity_fields || ['object', 'check_point', 'mode'])
      setCriteria(value.current?.criteria || ''); setReusable(value.current?.reusable || false)
      setCompatible(value.current?.compatible_versions || false); lastRevision.current = value.revision
    }
    setData(value); onChanged(value); setRefresh(previous => previous+1)
    if (!value.summary.confirmed || activeTask(value.task)) setStep(1)
  }
  async function load(signal?: AbortSignal) {
    const value = await request<WorkbenchView>(`${base}/workbench`, { signal })
    if (alive.current && !signal?.aborted) initialize(value)
    return value
  }
  useEffect(() => {
    if (!production) return
    let pending: AbortController | undefined
    const changed = () => {
      pending?.abort()
      const controller = new AbortController(); pending = controller
      request<WorkbenchView>(`${base}/workbench`, { signal: controller.signal }).then(value => {
        if (!controller.signal.aborted) setData(previous => previous ? { ...previous, ai: value.ai } : previous)
      }).catch(fail)
    }
    window.addEventListener('sanity-ai-token-changed', changed)
    return () => { pending?.abort(); window.removeEventListener('sanity-ai-token-changed', changed) }
  }, [base, production])
  async function downloadDemoSource() {
    try {
      const report = await request<{ raw_report: string | null }>(base)
      if (!report.raw_report) return
      const url = URL.createObjectURL(new Blob([report.raw_report], { type: 'text/plain;charset=utf-8' }))
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${reportId}.rpt`; anchor.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (value) { fail(value) }
  }
  function beginJudgment() { setGroupSearch(''); setGroupOffset(0); setFilter(''); setStep(2) }
  useEffect(() => {
    alive.current = true
    const controller = new AbortController()
    request<WorkbenchView>(`${base}/workbench`, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) initialize(value) }).catch(fail)
    return () => { alive.current = false; run.current++; controller.abort() }
  }, [base])
  useEffect(() => { if (data) panel.current?.scrollIntoView({ block: 'start' }) }, [!!data])
  useEffect(() => {
    if (!aiRunning || pollPaused) return
    const id = data!.ai!.job!.id
    if (polls.current.id !== id) polls.current = { id, count: 0 }
    if (polls.current.count >= 60) { setPollPaused(true); return }
    const controller = new AbortController()
    const timer = setTimeout(() => { polls.current.count++; load(controller.signal).catch(fail) }, production ? 2000 : 1000)
    return () => { clearTimeout(timer); controller.abort() }
  }, [base, aiRunning, data?.ai?.job?.id, refresh, pollPaused])
  useEffect(() => {
    if (!data?.current) { setGroups(null); return }
    const controller = new AbortController()
    const params = new URLSearchParams({ expected_revision: String(data.revision), offset: String(groupOffset), limit: '25', search: groupSearch, status: step === 2 ? filter : '', rationale_status: step === 1 ? rationaleFilter : '' })
    if (historyEvent) params.set('event_id', historyEvent)
    const timer = setTimeout(() => request<PageResult<GroupBrief>>(`${base}/clustering/groups?${params}`, { signal: controller.signal }).then(value => {
      if (controller.signal.aborted) return
      setGroups(value); setGroupId(previous => previous || value.items[0]?.id || '')
    }).catch(fail), 120)
    return () => { clearTimeout(timer); controller.abort() }
  }, [base, data?.revision, groupOffset, groupSearch, filter, rationaleFilter, step, historyEvent, refresh])
  useEffect(() => {
    if (!showHistory || !data) return
    const controller = new AbortController()
    request<PageResult<ClusterBrief>>(`${base}/clustering/history?offset=${historyOffset}&limit=25`, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setHistory(value) }).catch(fail)
    return () => controller.abort()
  }, [showHistory, historyOffset, refresh])
  useEffect(() => {
    if (!showRemoved || !data) return
    const controller = new AbortController()
    request<typeof removed>(`${base}/clustering/history?removed=true&offset=${removedOffset}&limit=25`, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setRemoved(value) }).catch(fail)
    return () => controller.abort()
  }, [showRemoved, removedOffset, refresh])

  async function mutate(path: string, body: Record<string, unknown>, success: string, destination?: string) {
    if (!data) return false
    const command = { ...body, expected_revision: data.revision }
    const key = JSON.stringify([path, command])
    if (pending.current?.key !== key) pending.current = { key, id: crypto.randomUUID() }
    setBusy(true); setError(''); setNotice('')
    try {
      await request(`${base}${path}`, { csrf: session.csrf, body: { ...command, request_id: pending.current.id } })
      pending.current = null; newGroup.current = null
      const value = await load()
      if (alive.current) {
        setNotice(success)
        if (destination) {
          setGroupId(destination)
          const operation = body.operation as Record<string, unknown> | undefined
          if (operation?.new_group_id || operation?.kind === 'RENAME' && groupSearch) {
            setGroupSearch(String(operation.label).slice(0, 100)); setGroupOffset(0)
          } else setGroupOffset(groupOffset)
        }
        if (body.action === 'CONFIRM' && value.summary.confirmed) beginJudgment()
      }
      return true
    } catch (value) {
      fail(value)
      if (value instanceof WorkspaceError && value.code === 'REVISION_CONFLICT') {
        try { await load() } catch (refreshError) { fail(refreshError) }
      }
      return false
    }
    finally { if (alive.current) setBusy(false) }
  }
  async function driveTask(task: PipelineTask, resume = false) {
    const token = ++run.current
    let current = task
    while (alive.current && token === run.current && (['PENDING', 'RUNNING'].includes(current.status) || resume && current.status === 'FAILED')) {
      current = await request<PipelineTask>(`${base}/clustering/tasks/step`, { csrf: session.csrf, body: {
        task_id: current.id, expected_chunk: current.next_chunk, action: resume ? 'RESUME' : 'ADVANCE',
      } })
      resume = false
      if (alive.current && current.status !== 'COMPLETED') setData(previous => previous ? { ...previous, task: current } : previous)
    }
    if (alive.current) {
      await load()
      if (current.status === 'COMPLETED') setNotice('전체 로그의 분류 초안을 저장했습니다. 그룹 구성을 확인하고 확정하세요.')
      if (['FAILED', 'STALE'].includes(current.status)) setError('분류 작업을 완료하지 못했습니다. 실패 범위를 확인하고 명시적으로 이어서 처리하거나 취소하세요. 일부 결과는 확정되지 않았습니다.')
    }
  }
  async function propose(resume = false) {
    if (!data) return
    setBusy(true); setError(''); setNotice('')
    try {
      let task = data.task
      if (!resume) {
        setData(previous => previous ? { ...previous, task: null } : previous)
        const body = { expected_revision: data.revision, identity_fields: fields, mode: 'CRITERIA' }
        const key = JSON.stringify(body)
        if (pending.current?.key !== key) pending.current = { key, id: crypto.randomUUID() }
        task = await request<PipelineTask>(`${base}/clustering/tasks`, { csrf: session.csrf, body: { ...body, request_id: pending.current.id } })
        pending.current = null
      }
      if (task) { setData(previous => previous ? { ...previous, task } : previous); await driveTask(task, resume) }
    } catch (value) { fail(value) }
    finally { if (alive.current) setBusy(false) }
  }
  async function cancelTask() {
    if (!data?.task) return
    setBusy(true); setError('')
    try {
      await request(`${base}/clustering/tasks/step`, { csrf: session.csrf, body: { task_id: data.task.id, expected_chunk: data.task.next_chunk, action: 'CANCEL' } })
      await load()
    } catch (value) { fail(value) }
    finally { if (alive.current) setBusy(false) }
  }
  async function proposeAI(saveOnly = false) {
    if (!data?.ai) return
    const previous = data.ai.job
    const command = { expected_revision: saveOnly && previous ? previous.expected_revision : data.revision,
      ...(production && data.ai.models?.length ? { model_id: modelId } : {}) }
    const key = `AI:${JSON.stringify(command)}`
    if (saveOnly && previous) pending.current = { key, id: previous.id }
    else if (pending.current?.key !== key) pending.current = { key, id: crypto.randomUUID() }
    setBusy(true); setAiSubmitting(true); setError(''); setNotice(''); setPollPaused(false); polls.current.count = 0
    try {
      const job = await request<CodexJob>(`${base}/clustering/ai`, { csrf: session.csrf,
        body: { ...command, request_id: pending.current.id } })
      pending.current = null
      await load()
      if (alive.current && job.status === 'COMPLETED') setNotice('AI 분류 초안을 저장했습니다. 그룹 구성·이름을 검토하고 확정하세요.')
    } catch (value) { fail(value) }
    finally { if (alive.current) { setBusy(false); setAiSubmitting(false) } }
  }
  function select(ids: string[], checked: boolean, kind: 'records' | 'groups') {
    if (kind === 'records') {
      setSelectedGroups(new Set())
      ids.forEach(id => checked ? selectionGroups.current.set(id, groupId) : selectionGroups.current.delete(id))
    } else { setSelectedRecords(new Set()); selectionGroups.current.clear() }
    const setter = kind === 'records' ? setSelectedRecords : setSelectedGroups
    setter(previous => { const next = new Set(previous); ids.forEach(id => checked ? next.add(id) : next.delete(id)); return next })
  }
  function editGroup(operation: Record<string, unknown>, auditReason = reason, destination?: string) {
    if (dirtyName && operation.kind !== 'RENAME') { setError('그룹 이름 변경을 저장하거나 취소한 뒤 구성을 수정하세요.'); return }
    mutate('/clustering/operations', { reason: auditReason, operation }, '그룹 구성과 변경 이력을 저장했습니다. 분류를 다시 확정하세요.', destination)
  }
  function selectionMove(id?: string, preview = ''): LogMove {
    const ids = id && !selectedRecords.has(id) ? [id] : [...selectedRecords]
    return { ids, sourceGroup: ids.every(record => (record === id && !selectedRecords.has(id) ? groupId : selectionGroups.current.get(record)) === groupId) ? groupId : '', revision: data!.revision, preview }
  }
  function moveLogs(move: LogMove, target: string) {
    if (!writable || !data || move.revision !== data.revision || !move.ids.length) return
    if (dirtyRationale) { setError('작성 중인 그룹 근거를 저장하거나 취소한 뒤 다른 그룹을 선택하세요.'); return } if (dirtyName) { setError('그룹 이름 변경을 저장하거나 취소한 뒤 로그를 이동하세요.'); return }
    if (target === move.sourceGroup) { setNotice('현재 그룹입니다. 로그 구성을 유지했습니다.'); return }
    if (target === NEW_LOG_GROUP) {
      setSplitMove(move); setNewName(''); setEditReason(reason); setEditor('split'); return
    }
    const destination = groups?.items.find(group => group.id === target) || (member?.group?.id === target ? member.group : null)
    if (!destination) return
    editGroup({ kind: 'MOVE', record_ids: move.ids, destination_id: target }, reason.trim() || `사용자가 로그 ${n(move.ids.length)}건을 “${destination.label}” 그룹으로 이동했습니다.`, target)
  }
  function closeEditor() {
    if (busy) return
    setEditor(null); setSplitMove(null); newGroup.current = null; setGroupName(member?.group?.label || ''); setNameEditing(false)
  }
  async function nextGroup() {
    if (!data) return
    setGroupNavigating(true)
    try {
      const value = await request<PageResult<GroupBrief>>(`${base}/clustering/groups?expected_revision=${data.revision}&status=UNREVIEWED&limit=2`)
      const next = value.items.find(group => group.id !== groupId) || value.items[0]
      if (next) { if (next.id !== groupId) setMember(null); setGroupId(next.id); setNotice('다음 미검토·보류·재검토 그룹을 선택했습니다.') }
      else setNotice('현재 분류의 모든 그룹 판정을 확정했습니다.')
    } catch (value) { fail(value) }
    finally { if (alive.current) setGroupNavigating(false) }
  }
  async function nextExplanation() {
    if (!data || dirtyRationale || dirtyName) return
    setGroupNavigating(true); setNotice('')
    try {
      const value = await request<PageResult<GroupBrief>>(`${base}/clustering/groups?expected_revision=${data.revision}&rationale_status=UNRESOLVED&limit=2`)
      const next = value.items.find(group => group.id !== groupId) || value.items[0]
      if (next) {
        if (next.id !== groupId) setMember(null)
        setNotice(next.id === groupId ? '현재 그룹이 마지막 미검토 대상입니다. 분류 근거를 검토하세요.' : '다음 미검토 그룹을 선택했습니다.')
        setRationaleFilter('UNRESOLVED'); setGroupSearch(''); setGroupOffset(0); setGroupId(next.id)
      }
      else setNotice('모든 그룹의 근거 검토가 완료되었습니다. 분류를 확정한 뒤 판정을 시작하세요.')
    } catch (value) { fail(value) }
    finally { if (alive.current) setGroupNavigating(false) }
  }

  if (!data) return <section className="ws-panel ws-workbench ws-studio-workbench"><p role={error ? 'alert' : 'status'} className="ws-loading-status">{!error && <LoaderCircle size={16} className="spin" aria-hidden="true" />}{error || '전체 입력과 현재 분류 상태를 확인하고 있습니다.'}</p><Button variant="outline" onClick={onBack}>보고서 목록</Button></section>
  const task = data.task
  const chosen = member?.group
  const model = data.current?.model_metadata
  const reviewReport = { ...data.report, check_status: chosen?.check_status || 'UNKNOWN' }
  const transitionBlocked = !confirmed || dirtyName || dirtyRationale || identityChanged || !!historyEvent || busy || groupNavigating || aiRunning
  const missing = data.summary.rationale_counts.MISSING
  const stale = data.summary.rationale_counts.STALE
  const confirmedReasons = data.summary.rationale_counts.CONFIRMED
  const confirmBlocked = !writable || !data.ready || dirtyName || identityChanged || dirtyRationale || !!missing || !!stale || data.stale || reusable && !criteria.trim()
  const blockedHint = dirtyRationale || dirtyName ? '작성 중인 내용을 저장하거나 취소하세요.'
    : activeTask(task) || aiRunning ? '분류 초안을 생성 중입니다.'
    : !data.ready ? '보고서 수신·파싱 완료 후 확정할 수 있습니다.'
    : missing || stale ? `${missing ? `근거가 누락된 그룹 ${n(missing)}개` : ''}${missing && stale ? ' · ' : ''}${stale ? `재검토가 필요한 그룹 ${n(stale)}개` : ''}를 먼저 검토하세요.`
    : data.stale ? '참고 분류가 변경되었습니다. 현재 구성을 재검토하세요.'
    : identityChanged ? '변경한 분류 기준으로 초안을 다시 생성하세요.'
    : reusable && !criteria.trim() ? '재사용할 분류 기준을 입력하세요.' : ''
  const rationale = groupId && <GroupRationale key={`rationale:${groupId}:${data.revision}:${historyEvent}`} base={base} groupId={groupId} report={data.report} version={data.version} revision={data.revision} eventId={historyEvent} editable={step === 1 && writable} busy={busy} selected={selectedRecords} onDirty={setDirtyRationale} onError={fail} onSave={body => mutate(`/groups/${groupId}/rationale`, body, '선택 그룹의 분류 근거를 저장했습니다.', groupId)} />
  const records = groupId && <Records key={`${groupId}:${data.revision}:${historyEvent}`} base={base} revision={data.revision} eventId={historyEvent} groupId={groupId} selectEnabled={step === 1 && writable} selected={selectedRecords} drag={step === 1 && writable ? drag : undefined} moveForRecord={selectionMove} onSelect={(ids, checked) => select(ids, checked, 'records')} onLoaded={value => { setMember(value); if (!nameEditing) setGroupName(value.group?.label || '') }} onError={fail} />
  return <section ref={panel} className={`ws-panel ws-workbench ws-studio-workbench${drag.drag ? ' ws-dragging-logs' : ''}`} aria-label="Sanity item 순차 검토">
    <div className="ws-workbench-topline">
    <header className="ws-workbench-header">
      <div className="ws-workbench-title"><div><div className="ws-workbench-heading-line"><h1>로그 검토</h1><span className="ws-caption" title={`${data.report.family} · ${data.version.net_ver} · ${data.version.layout}`}>{data.report.family} · {data.version.net_ver} · {data.version.layout}</span></div><p title={data.report.id}>{data.report.id}</p></div></div>
      {demo ? <Button variant="ghost" className="ws-source-download" disabled={!data.record_count} onClick={downloadDemoSource}><Download size={16} />예시 원문 내려받기</Button> : data.report.source_storage === 'PARTS_V2' ? <span className="ws-muted">원본 파일은 VWP에 보관 · 추출 로그는 아래에서 조회</span> : <a className="ws-source-download" href={`${workspaceApiBase()}${base}/source`}><Download size={16} />원본 내려받기</a>}
    </header>
    <div className="ws-workbench-toolbar">
      <nav className="ws-pipeline-steps" aria-label="순차 검토 단계">
        <button type="button" aria-current={step === 1 ? 'step' : undefined} onClick={() => setStep(1)}><b>{confirmed ? <Check size={16} /> : '1'}</b><span>로그 분류</span><small>{confirmed ? '확정' : '검토 중'}</small></button>
        <ArrowRight size={15} />
        <button type="button" aria-current={step === 2 ? 'step' : undefined} disabled={transitionBlocked} aria-describedby={transitionBlocked && step === 1 && !confirmed ? `${inputId}-step-hint` : undefined} onClick={beginJudgment}><b>2</b><span>그룹별 판정</span><small>{n(data.summary.reviewed)}/{n(data.summary.total)}</small></button>
      </nav>
      <div className="ws-workbench-stage-actions">
        {production && step === 1 && !data.current && data.can_edit && !historyEvent && <AiModelSelect ai={data.ai} value={modelId} onChange={setSelectedModel} disabled={!writable || !data.ready || aiSubmitting} label="분류 AI 모델" />}
        {step === 1 && confirmed && <Button disabled={transitionBlocked} onClick={beginJudgment}>{data.can_edit ? '그룹 판정 계속' : '판정 보기'}<ArrowRight size={16} /></Button>}
        {step === 1 && data.current && data.can_edit && !historyEvent && !confirmed && <Button aria-describedby={`${inputId}-confirm-hint`} disabled={confirmBlocked} onClick={() => mutate('/clustering?compact=true', { action: 'CONFIRM', reason, reusable, compatible_versions: reusable && compatible, criteria: reusable ? criteria : '' }, '전체 분류를 확정했습니다. 그룹별 사용자 판정을 저장하세요.')}>분류 확정 · 판정 시작<ArrowRight size={16} /></Button>}
        {step === 1 && !data.current && data.can_edit && !historyEvent && <>
          {production || data.ai?.enabled && data.ai.supported ? <div className="ws-workbench-ai"><Button disabled={!writable || !data.ready || !data.ai?.available || dirtyName || dirtyRationale} onClick={() => proposeAI()}>{aiRunning || aiSubmitting ? <LoaderCircle size={15} className="animate-spin" /> : <Layers3 size={15} />}{aiRunning || aiSubmitting ? 'AI 분류 중…' : 'AI로 그룹 만들기'}</Button><span>{production ? !data.ai?.enabled ? 'OA AI 연결 설정 필요' : !data.ai.supported ? '분류 입력 상태 확인 필요' : '전체 로그와 확정 사례 참고' : '합성 보고서 전용'}</span></div> : <Button disabled={!writable || !data.ready || dirtyRationale} onClick={() => propose()}><Layers3 size={15} />그룹 초안 만들기</Button>}
        </>}
        <Button variant="ghost" size="icon" aria-label="최신 구성 다시 조회" disabled={busy} onClick={() => { polls.current.count = 0; setPollPaused(false); load().catch(fail) }}><RefreshCw size={15} /></Button>
      </div>
    </div>
    </div>
    <PreservedSources files={data.report.preserved_sources} base={production ? base : undefined} />
    <PreservedCitations files={data.report.preserved_sources} citations={data.current?.model_metadata?.preserved_citations} sources={data.current?.model_metadata?.preserved_citation_sources} target={groupId || undefined} />
    {error && <p className="ws-error" role="alert">{error}</p>}{notice && <p className="ws-notice" role="status"><Check size={14} />{notice}</p>}
    {historyEvent && <p className="ws-notice">이전 개정의 구성을 조회 중입니다. 읽기 전용입니다. <button type="button" onClick={() => { setHistoryEvent(null); setGroupId(''); setGroupOffset(0) }}>현재 구성으로 돌아가기</button></p>}
    {data.stale && !activeTask(task) && !aiRunning && <p className="ws-error">참고 분류가 변경되었습니다. 새 초안을 받거나 구성을 검토·저장한 뒤 다시 확정하세요.</p>}
    <div className="ws-review-progress" aria-label="현재 검토 진행 상황">
      <span>로그 <strong>{n(data.record_count)}건</strong><ArrowRight size={13} /><strong>{n(data.summary.total)}개 그룹</strong></span>
      {data.current && <span className="ws-current-progress">{step === 1 ? '근거 검토' : '판정 완료'}<progress max={Math.max(data.summary.total, 1)} value={step === 1 ? confirmedReasons : data.summary.reviewed} /><strong>{n(step === 1 ? confirmedReasons : data.summary.reviewed)} / {n(data.summary.total)}</strong></span>}
      <details><summary>상세 현황</summary><div className="ws-workbench-counts"><span>원문 <strong>{n(data.raw_line_count)}행</strong></span><span>분류 개정 <strong>{data.revision}</strong></span><span>판정 PASS <strong>{n(data.summary.counts.PASS)}</strong></span><span>판정 FAIL <strong>{n(data.summary.counts.FAIL)}</strong></span><span>보류 <strong>{n(data.summary.counts.DEFER)}</strong></span><span>판정 재검토 <strong>{n(data.summary.counts.STALE)}</strong></span></div>{data.current && <div className="ws-rationale-progress" aria-label="분류 근거 검토 현황">{Object.entries(data.summary.rationale_counts).map(([status, count]) => <span key={status}>{rationaleLabels[status as keyof typeof rationaleLabels]} <strong>{n(count)}</strong></span>)}</div>}</details>
    </div>
    {step === 1 && data.current && !confirmed && <p className="ws-bulk-confirm-note" id={`${inputId}-confirm-hint`}>분류 확정 시 전체 {n(data.summary.total)}개 그룹의 유효한 근거 초안을 일괄 채택합니다.</p>}
    {step === 1 && blockedHint && <p className="ws-step-hint" id={`${inputId}-step-hint`}>{blockedHint}</p>}
    {step === 1 && !confirmed && !blockedHint && <span id={`${inputId}-step-hint`} className="sr-only">전체 분류를 확정한 뒤 그룹별 판정을 진행할 수 있습니다.</span>}
    {!data.can_edit && <p className="ws-step-hint">조회 전용 · 분류 및 사용자 판정은 블록 담당자만 변경할 수 있습니다.</p>}
    {!data.current && <section className="ws-workbench-empty"><Layers3 size={28} /><h2>분류 초안을 생성하세요</h2><p>{production || data.ai?.enabled && data.ai.supported ? 'AI가 전체 로그와 확정 사례를 바탕으로 초기 그룹과 분류 근거를 제안합니다. 첫 보고서도 과거 사례 없이 시작할 수 있습니다.' : '참고 엔진이 원본 필드와 확정 사례를 바탕으로 그룹 초안을 생성합니다. 사내 AI는 미연결 상태입니다.'}</p></section>}
    {production && !data.ai?.enabled && <p className="ws-notice" role="status">OA AI가 아직 설정되지 않았습니다. 운영 담당자가 모델 연결을 설정한 뒤 AI 분류를 시작할 수 있습니다.</p>}
    {production && data.ai?.credential_required && !data.ai.credential_ready && <p className="ws-notice" role="status">개인 AI 토큰을 사용할 수 없습니다. 화면 상단의 ‘개인 AI 토큰’에서 등록 상태와 만료일을 확인해 주세요.</p>}
    {production && data.can_edit && data.ai?.enabled && !data.ai.supported && <p className="ws-notice" role="status">AI 분류 요청 조건을 충족하지 못했습니다. 보고서 수신·파싱과 전체 로그의 준비 상태를 확인해 주세요.</p>}
    {pollPaused && aiRunning && <p className="ws-notice" role="status">상태 자동 조회를 잠시 멈췄습니다. 서버 작업은 계속됩니다. ‘최신 구성 다시 조회’로 상태 확인을 이어가세요.</p>}
    {data.ai?.job && data.ai.job.status !== 'COMPLETED' && <div role="status" aria-live="polite" className={['FAILED', 'STALE', 'UNKNOWN', 'SAVE_FAILED'].includes(data.ai.job.status) ? 'ws-error' : 'ws-notice'}>{production ? `${data.ai.job.model} · ${aiJobMessage(data.ai.job)}` : aiRunning ? 'AI가 전체 로그와 확정 사례로 분류 초안을 생성·저장하고 있습니다. 새로고침 후에도 상태를 확인할 수 있습니다.' : data.ai.job.status === 'UNKNOWN' ? 'AI 호출 결과를 확인하지 못했습니다. 자동 재호출하지 않습니다. 새 요청은 추가 사용량이 발생할 수 있습니다.' : data.ai.job.status === 'SAVE_FAILED' ? '받은 AI 결과를 저장하지 못했습니다. 모델을 다시 호출하지 않고 저장을 재시도할 수 있습니다.' : data.ai.job.status === 'STALE' ? '검토 개정이나 참고 사례가 바뀌어 AI 결과를 적용하지 않았습니다. 최신 상태를 확인하고 새 초안을 요청하세요.' : `AI 분류를 완료하지 못했습니다. 기존 구성은 유지됩니다. (${data.ai.job.error || 'AI_CALL_FAILED'})`}{data.ai.job.status === 'SAVE_FAILED' && data.can_edit && <Button variant="outline" disabled={busy} onClick={() => proposeAI(true)}>받은 AI 결과 저장</Button>}</div>}
    {!data.ready && <p className="ws-notice">원문 수신·파싱과 필수 근거가 준비되어야 분류를 확정할 수 있습니다.</p>}
    {identityChanged && <p className="ws-notice">분류 필드를 변경했습니다. 새 초안을 생성하거나 필드를 현재 분류 기준으로 되돌린 뒤 진행하세요.</p>}
    {task && activeTask(task) && <div className="ws-workbench-task" aria-label="전체 로그 분류 진행"><div><strong>{task.status === 'FAILED' ? '분류 작업 실패' : task.status === 'STALE' ? '참조 변경 · 작업 재검토' : '전체 로그 분류 진행'}</strong><span>{n(task.processed)} / {n(task.record_count)}건</span></div><progress max={Math.max(task.record_count, 1)} value={task.processed} />{task.error && <p>{task.error} · 부분 결과는 확정되지 않았습니다.</p>}{busy && ['PENDING', 'RUNNING'].includes(task.status) ? <Button variant="outline" onClick={() => { run.current++ }}>현재 작업 뒤 일시정지</Button> : !busy && data.can_edit && activeTask(task) && <div><Button variant="outline" disabled={task.status === 'STALE'} onClick={() => propose(true)}>저장한 위치에서 이어서 처리</Button><Button variant="outline" onClick={cancelTask}>분류 작업 취소</Button></div>}</div>}
    {step === 2 && data.summary.complete && <div className="ws-review-complete" role="status"><Check size={20} /><div><strong>{n(data.summary.total)}개 그룹의 판정을 완료했습니다.</strong><p>확정된 사용자 판정은 사례로 저장됩니다.</p></div><Button onClick={onBack}>보고서 목록으로<ArrowRight size={15} /></Button></div>}
    {data.current && <div className="ws-workbench-columns ws-studio-columns" role="region" aria-label={step === 1 ? '1단계 전체 로그 분류' : '2단계 확정 그룹별 판정'}>
      <aside className="ws-studio-context" aria-label="프로젝트와 보고서 정보">
        <div className="ws-context-heading"><FolderTree size={16} /><h2>프로젝트</h2></div>
        <div className="ws-context-project"><strong>{context?.projectName || data.report.project_id}</strong><span>{context?.blockName || data.report.block_id}</span></div>
        <div className="ws-context-version"><GitBranch size={15} /><div><strong>{data.version.net_ver}</strong><span>{data.version.layout} · {data.version.revision}</span><span>ECO {data.report.eco || '해당 없음'}</span></div></div>
        <div className="ws-context-por"><ShieldCheck size={15} /><div><span>현재 PoR</span><strong>{context?.porLabel || '미지정'}</strong></div></div>
        <div className="ws-context-heading"><FileText size={16} /><h2>검사 보고서</h2></div>
        <div className="ws-context-file"><strong>{data.report.item_id || data.report.rule}</strong><span>{data.report.family}</span><code title={data.report.id}>{data.report.id}</code><Badge variant="outline" className={`ws-status ws-status-${data.report.check_status.toLowerCase()}`}>도구 결과 {labels[toolResultStatus(data.report)]}</Badge></div>
        <dl className="ws-context-metrics"><div><dt>전체 로그</dt><dd>{n(data.record_count)}</dd></div><div><dt>분류 그룹</dt><dd>{n(data.summary.total)}</dd></div><div><dt>분류 개정</dt><dd>{data.revision}</dd></div></dl>
        <p className="ws-context-permission">{data.can_edit ? '블록 담당자 · 검토 가능' : '조회 전용'}</p>
        <Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft size={14} />보고서 목록</Button>
      </aside>
      <section className="ws-workbench-groups ws-studio-groups" aria-label="분류 그룹 목록">
        <div className="ws-workbench-group-heading"><h3>분류 그룹 <span>{n(data.summary.total)}</span></h3>{step === 1 && data.can_edit && !historyEvent && <Button variant="ghost" size="sm" aria-expanded={mergeMode} disabled={!writable || dirtyRationale} onClick={() => { setMergeMode(value => !value); setSelectedGroups(new Set()) }}>{mergeMode ? '취소' : '병합'}</Button>}</div>
        {mergeMode && <p className="ws-muted">병합할 그룹을 2개 이상 선택하세요.</p>}
        <div className="ws-workbench-filter"><label><span className="sr-only">그룹 검색</span><div className="ws-group-search"><Input aria-label="그룹 검색" disabled={dirtyRationale || dirtyName} value={groupSearch} maxLength={100} placeholder="그룹 검색" onChange={event => { setGroupSearch(event.target.value); setGroupOffset(0) }} />{groupSearch && <button type="button" aria-label="그룹 검색 지우기" disabled={dirtyRationale || dirtyName} onClick={() => { setGroupSearch(''); setGroupOffset(0) }}><X size={13} /></button>}</div></label>{step === 1 && <label><span className="sr-only">근거 상태</span><select aria-label="그룹 근거 필터" disabled={dirtyRationale || dirtyName} value={rationaleFilter} onChange={event => { setRationaleFilter(event.target.value); setGroupOffset(0); setGroupId('') }}><option value="">전체 그룹</option><option value="UNRESOLVED">미검토·재검토</option>{Object.entries(rationaleLabels).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>}{step === 2 && <label><span className="sr-only">판정 상태</span><select aria-label="그룹 판정 필터" value={filter} onChange={event => { setFilter(event.target.value); setGroupOffset(0) }}><option value="">모든 판정</option><option value="UNREVIEWED">미검토·보류·재검토</option>{['PASS', 'FAIL', 'DEFER', 'STALE'].map(key => <option key={key} value={key}>{statusLabels[key]}</option>)}</select></label>}</div>
        <div className="ws-workbench-group-list">{groups?.items.map((group, index) => {
          const ordinal = (group as GroupBrief & { ordinal?: number }).ordinal ?? (!groupSearch && !rationaleFilter && !filter ? groupOffset + index + 1 : null)
          return <div key={group.id} data-log-drop={step === 1 && writable && !dirtyRationale ? group.id : undefined} className={[group.id === groupId ? 'ws-workbench-selected' : '', drag.drag?.target === group.id ? 'ws-log-drop-active' : ''].join(' ')}>
            {step === 1 && mergeMode && data.can_edit && !historyEvent && <Checkbox aria-label={`${group.label} 그룹 전체 선택`} checked={selectedGroups.has(group.id)} disabled={!writable} onCheckedChange={checked => select([group.id], checked === true, 'groups')} />}
            <button type="button" data-log-drop={step === 1 && writable && !dirtyRationale ? group.id : undefined} data-drop-label={group.label} aria-pressed={group.id === groupId} aria-describedby={drag.drag ? drag.helpId : undefined} onClick={() => { if (dirtyRationale) { setError('작성 중인 그룹 근거를 저장하거나 취소한 뒤 다른 그룹을 선택하세요.'); return } if (dirtyName) { setError('그룹 이름 변경을 저장하거나 취소한 뒤 다른 그룹을 선택하세요.'); return } setGroupId(group.id); setMember(null); setNameEditing(false) }}><span className="ws-group-ordinal" aria-hidden="true">{ordinal === null ? '·' : String(ordinal).padStart(2, '0')}</span><div><strong>{group.label}</strong><span className={`ws-group-rationale ws-rationale-${group.rationale.status.toLowerCase()}`}>{n(group.record_count)}건 · {step === 1 ? rationaleLabels[group.rationale.status] : statusLabels[group.status]}</span></div></button>
          </div>
        })}</div>
        {groups && !groups.total && <p className="ws-muted">검색 결과가 없습니다.</p>}
        {step === 1 && data.can_edit && !historyEvent && <button type="button" className={`ws-log-new-group${drag.drag?.target === NEW_LOG_GROUP ? ' ws-log-drop-active' : ''}`} data-log-drop={writable ? NEW_LOG_GROUP : undefined} data-drop-label="새 그룹" disabled={!writable || dirtyRationale} onClick={() => { if (!selectedRecords.size) { setNotice('새 그룹으로 옮길 로그를 먼저 선택하거나 이곳으로 끌어다 놓으세요.'); return } moveLogs(selectionMove(), NEW_LOG_GROUP) }}><Plus size={15} />새 그룹으로 분리</button>}
        {groups && <Pager {...groups} label="그룹" onChange={offset => { if (dirtyRationale || dirtyName) { setError('작성 중인 내용을 저장하거나 취소한 뒤 그룹 페이지를 변경하세요.'); return } setGroupOffset(offset) }} />}
        {step === 1 && data.current && confirmedReasons < data.summary.total && <Button className="ws-next-explanation" variant="ghost" size="sm" disabled={dirtyRationale || dirtyName || busy || groupNavigating || !!historyEvent} onClick={nextExplanation}>다음 미검토 근거<ArrowRight size={14} /></Button>}
        {step === 1 && selectedGroups.size >= 2 && <Button className="ws-group-merge" variant="outline" disabled={!writable || dirtyRationale} onClick={() => { setEditReason(reason); setEditor('merge') }}>선택한 {selectedGroups.size}개 그룹 병합</Button>}
      </section>
      <section className="ws-workbench-detail ws-studio-evidence" aria-label="선택 그룹 상세">
        {groupId ? <>
          <header className="ws-selected-group-header"><div><h2>{chosen?.label || '선택 그룹 조회 중…'}</h2><span>{chosen ? `로그 ${n(chosen.record_count)}건` : '로그 조회 중'}{chosen && <> · 도구 판정 <Badge variant="outline" className={`ws-status ws-status-${chosen.check_status.toLowerCase()}`}>{labels[toolResultStatus({ ...data.report, check_status: chosen.check_status })]}</Badge></>}</span></div>{step === 1 && data.can_edit && chosen && !historyEvent && <Button variant="ghost" size="sm" disabled={!writable || dirtyRationale} onClick={() => { setEditReason(reason); setEditor('rename') }}><Pencil size={14} />이름 변경</Button>}</header>
          {step === 1 && selectedRecords.size > 0 && <div className="ws-workbench-edit"><span>로그 {n(selectedRecords.size)}건 선택 · 페이지 간 선택 유지</span><div><Button variant="outline" size="sm" disabled={!writable || dirtyRationale} onClick={() => moveLogs(selectionMove(), groupId)}>선택 로그를 이 그룹으로 이동</Button><Button variant="outline" size="sm" disabled={!writable || dirtyRationale} onClick={() => moveLogs(selectionMove(), NEW_LOG_GROUP)}>새 그룹</Button><Button variant="ghost" size="icon" aria-label="로그 선택 해제" disabled={busy} onClick={() => { setSelectedRecords(new Set()); selectionGroups.current.clear() }}><X size={14} /></Button></div></div>}
          {records}
        </> : <p className="ws-muted">왼쪽에서 검토할 그룹을 선택하세요.</p>}
      </section>
      <aside className="ws-group-inspector ws-studio-decision" aria-label={step === 1 ? '분류 근거 검토' : '그룹 판정'}>
        {groupId && (step === 1 ? rationale : <>
          {confirmed && (groupNavigating || !chosen ? <p role="status" className="ws-loading-status"><LoaderCircle size={16} className="spin" aria-hidden="true" />선택 그룹을 조회 중입니다.</p> : <><WorkspaceReview key={`${groupId}:${data.revision}`} compact report={reviewReport} version={data.version} session={session} group={{ id: groupId, cluster_revision: data.revision }} onExpired={onExpired} onSaved={() => load().catch(fail)} /><div className="ws-workbench-next"><Button variant="outline" disabled={data.summary.complete} onClick={nextGroup}>다음 미검토 그룹<ArrowRight size={14} /></Button></div></>)}
          <details className="ws-judgment-evidence"><summary>분류 근거</summary>{rationale}</details>
        </>)}
      </aside>
    </div>}

    <details className="ws-workbench-support"><summary>설정과 이전 기록</summary><div className="ws-workbench-support-content">
      <details className="ws-report-context"><summary>보고서 정보</summary><dl><div><dt>보고서</dt><dd>{data.report.id}</dd></div><div><dt>검사 항목</dt><dd>{data.report.item_id} · {data.report.family}</dd></div><div><dt>버전</dt><dd>{data.version.net_ver} · {data.version.layout} · {data.version.revision}</dd></div><div><dt>ECO</dt><dd>{data.report.eco || '해당 없음'}</dd></div><div><dt>원문</dt><dd>{n(data.raw_line_count)}행 · 로그 {n(data.record_count)}건</dd></div><div><dt>원본 SHA-256</dt><dd><code>{data.source_sha256}</code></dd></div></dl></details>
      {step === 1 && data.current && data.can_edit && !historyEvent && <details><summary>{production ? '전체 분류 메모 (선택)' : '전체 메모와 참고 엔진 설정 (선택)'}</summary><div className="ws-review-field"><label htmlFor={`${inputId}-reason`}>전체 분류 메모 (선택)</label><textarea id={`${inputId}-reason`} value={reason} maxLength={2000} disabled={busy} onChange={event => setReason(event.target.value)} placeholder="전체 분류에 대한 추가 검토 내용을 입력하세요." /></div>{!production && !model?.synthetic_only && <div className="ws-reuse-options"><p className="ws-muted">확정한 분류·판정과 근거는 블록·revision·ECO를 넘어 관련 AI 참고 사례로 축적됩니다. 아래 설정은 AI 참고와 별개인 참고 엔진의 조건 일치 규칙용입니다.</p><label className="ws-review-checkbox"><input type="checkbox" checked={reusable} disabled={busy} onChange={event => { setReusable(event.target.checked); if (!event.target.checked) setCompatible(false) }} />동일 조건의 참고 엔진에 분류 규칙 적용 허용</label>{reusable && <><div className="ws-review-field"><label htmlFor={`${inputId}-criteria`}>재사용할 분류 기준</label><textarea id={`${inputId}-criteria`} value={criteria} maxLength={2000} disabled={busy} onChange={event => setCriteria(event.target.value)} /></div><label className="ws-review-checkbox"><input type="checkbox" checked={compatible} disabled={busy} onChange={event => setCompatible(event.target.checked)} />같은 블록의 호환되는 다른 버전에도 적용</label></>}</div>}{confirmed && <><p className="ws-muted">확정 해제 사유를 전체 분류 메모에 입력하세요.</p><Button variant="outline" disabled={!writable || !reason.trim()} onClick={() => mutate('/clustering?compact=true', { action: 'WITHDRAW', reason }, '분류 확정을 해제했습니다. 이전 판정은 이력으로 보존됩니다.')}>분류 확정 해제</Button></>}</details>}
      {step === 1 && data.current && data.can_edit && !historyEvent && data.ai?.enabled && data.ai.supported && <details className="ws-rebuild-groups"><summary>AI로 그룹 다시 만들기</summary><p className="ws-muted">{production ? '전체 로그와 관련 확정 사례를 OA AI가 검토합니다. 새 초안과 이전 구성은 이력으로 남습니다.' : '현재 그룹 구성을 새 초안으로 대체하며 기존 구성은 이력에 보존합니다. 합성 보고서와 검토 근거가 외부 AI에 전달됩니다.'}</p><Button variant="outline" disabled={!writable || !data.ready || !data.ai?.available || dirtyName || dirtyRationale} onClick={() => proposeAI()}><Layers3 size={15} />AI로 그룹 만들기</Button><small>{data.ai.model}{!production && <> · 남은 테스트 {data.ai.remaining}회</>}</small></details>}
      {step === 1 && data.can_edit && !historyEvent && !production && <details className="ws-workbench-settings"><summary>분류 설정</summary><p className="ws-muted">{data.ai?.enabled && data.ai.supported ? `합성 report 전용 · ${data.ai.model} 실제 호출 · 남은 테스트 ${data.ai.remaining}회. 새 초안은 기존 확정을 대체하며 이전 구성은 이력으로 남습니다. 이 테스트에 작성한 그룹 이름·검토 사유도 외부 모델에 전달됩니다.` : '사내 AI 미연결 · 현재 초안과 판정 제안은 확정 사례와 원본 필드를 참고하는 엔진입니다.'}</p><div className="ws-workbench-propose"><fieldset disabled={busy || activeTask(task) || aiRunning}><legend>참고 엔진에서 구분할 필드</legend>{Object.entries({ object: '대상', check_point: '검사 지점', mode: '모드', observed: '측정값', expected: '기준값', unit: '단위' }).map(([key, name]) => <label key={key}><input type="checkbox" checked={fields.includes(key)} disabled={key === 'mode'} onChange={event => setFields(previous => event.target.checked ? [...previous, key] : previous.filter(value => value !== key))} />{name}</label>)}</fieldset><Button variant="outline" disabled={!writable || !data.ready || dirtyRationale} onClick={() => propose()}><Layers3 size={15} />참고 엔진으로 초안 생성</Button></div></details>}
      {data.current && <details className="ws-cluster-references"><summary>현재 분류의 기준과 출처</summary><p>{data.current.reason}<small>구분 필드: {data.current.identity_fields.join(', ')} · {data.current.actor.name} · {time(data.current.at)}</small></p>{model && <p>초기 분류 모델: {model.model}<small>입력 사례 {model.input_case_count}건 · 입력 토큰 {n(model.usage.input_tokens || 0)} · 출력 토큰 {n(model.usage.output_tokens || 0)} · {model.seconds}초</small></p>}{data.current.references.map(reference => <p key={reference.id}>{reference.report_id} · 분류 개정 {reference.revision}<small>{reference.criteria || '공유 확정 분류 사례'}</small><CaseSource value={reference} report={data.report} version={data.version} /></p>)}<p className="ws-muted">{production ? `이번 조회에서 선정한 관련 분류 후보 ${n(data.case_count)}건 · 현재 분류의 인용 사례 ${data.current.references.length}건. 전체 누적 건수가 아닙니다. 블록·revision·ECO를 넘어 관련 사례를 찾고 이번 조건과의 차이를 다시 확인합니다. 과거 확정 근거를 참고하며 원문은 기존 조회 권한을 따릅니다.` : `다른 report의 확정 사례 ${n(data.case_count)}건 · 참고 엔진 재사용 기준 ${n(data.reusable_count)}건`}</p></details>}
      {task && <details className="ws-workbench-task-history"><summary><span>{task.status === 'COMPLETED' ? task.id === data.current?.id ? '분류 초안 생성 완료' : '이전 참고 엔진의 분류 작업' : '분류 작업 근거'}</span> · {n(task.processed)} / {n(task.record_count)}건</summary>{task.chunks.map(chunk => <p key={chunk.index}>{n(chunk.start+1)}–{n(chunk.stop)}번째 로그 · {chunk.record_count}건 · {chunk.status}<code>{chunk.input_sha256}</code></p>)}</details>}
      <details className="ws-workbench-source" onToggle={event => setShowSource(event.currentTarget.open)}><summary>원본 로그 검색·조회 · {n(data.record_count)}건</summary>{showSource && <Records base={base} revision={data.revision} selected={new Set()} onSelect={() => {}} onError={fail} />}</details>
      <details className="ws-cluster-history" onToggle={event => setShowHistory(event.currentTarget.open)}><summary>분류 변경 이력</summary>{showHistory && history && <>{history.items.map(event => <article key={event.id}><strong>{actions[event.action]} · 개정 {event.revision} · {n(event.group_count)}개 그룹</strong><span>{event.actor.name} · {time(event.at)}</span><p>{event.reason}</p><Button variant="outline" size="sm" onClick={() => { setHistoryEvent(event.id); setGroupId(''); setGroupOffset(0); setStep(1) }}>당시 그룹 구성 조회</Button></article>)}<Pager {...history} label="분류 이력" onChange={setHistoryOffset} /></>}</details>
      {!!data.removed_count && <details onToggle={event => setShowRemoved(event.currentTarget.open)}><summary>이전 구성에서 제거된 그룹의 판정 이력 · {data.removed_count}개</summary>{showRemoved && removed && <>{removed.items.map(group => <button className="ws-removed-group" key={group.id} onClick={() => setRemovedId(group.id)}>{group.label} · 이전 {labels[group.conclusion]}</button>)}<Pager {...removed} label="제거된 그룹" onChange={setRemovedOffset} />{removedId && <WorkspaceReview key={removedId} compact report={data.report} version={data.version} session={session} group={{ id: removedId, cluster_revision: data.revision }} onExpired={onExpired} onSaved={() => load().catch(fail)} />}</>}</details>}
    </div></details>
    <p id={drag.helpId} className="sr-only">손잡이에서 Space 또는 Enter로 로그를 집고, 방향키로 그룹을 선택한 다음 Enter로 이동합니다. Escape로 취소합니다.</p><p role="status" aria-live="polite" className="sr-only">{drag.announcement}</p>
    {drag.drag && <div className={`ws-log-drag-preview${drag.drag.keyboard ? ' ws-log-keyboard-preview' : ''}`} style={drag.drag.keyboard ? undefined : { left: Math.max(8, Math.min(drag.drag.x+14, window.innerWidth-238)), top: Math.max(8, Math.min(drag.drag.y+14, window.innerHeight-80)) }} aria-hidden="true"><GripVertical size={17} /><div><strong>{n(drag.drag.ids.length)}건 이동</strong><span>{drag.drag.keyboard ? '↑ ↓ 그룹 선택 · Enter 이동 · Esc 취소' : drag.drag.target === NEW_LOG_GROUP ? '놓아서 새 그룹 만들기' : drag.drag.target && drag.drag.target !== drag.drag.sourceGroup ? '놓아서 이 그룹으로 이동' : '왼쪽 그룹에 놓으세요'}</span></div></div>}
    <Dialog open={!!editor} onOpenChange={open => { if (!open) closeEditor() }}>
      <DialogContent className="ws-group-dialog" onEscapeKeyDown={event => { if (busy) event.preventDefault() }} onInteractOutside={event => { if (busy) event.preventDefault() }}>
        <DialogHeader><DialogTitle>{editor === 'rename' ? '그룹 이름 변경' : editor === 'merge' ? '선택 그룹 병합' : '새 그룹으로 분리'}</DialogTitle><DialogDescription>{editor === 'rename' ? '로그를 설명하는 그룹 이름을 입력하세요.' : editor === 'merge' ? `선택한 ${n(selectedGroups.size)}개 그룹의 전체 로그를 하나로 합칩니다.` : `선택한 ${n(splitMove?.ids.length || 0)}건을 새 그룹으로 옮깁니다.`}</DialogDescription></DialogHeader>
        {editor !== 'merge' && <label>{editor === 'rename' ? '그룹 이름' : '새 그룹 이름'}<Input autoFocus aria-label={editor === 'rename' ? '선택 그룹 이름' : '분리할 새 그룹 이름'} maxLength={120} value={editor === 'rename' ? groupName : newName} disabled={busy} onChange={event => { if (editor === 'rename') { setNameEditing(true); setGroupName(event.target.value) } else setNewName(event.target.value) }} /></label>}
        <label>변경 사유 <span>(선택)</span><textarea aria-label="이번 변경 사유" value={editReason} maxLength={2000} disabled={busy} placeholder="입력하지 않으면 이동·이름 변경 내역이 기록됩니다." onChange={event => setEditReason(event.target.value)} /></label>
        {error && <p className="ws-error" role="alert">{error}</p>}
        <DialogFooter><Button variant="outline" disabled={busy} onClick={closeEditor}>취소</Button><Button disabled={!writable || editor === 'rename' && (!groupName.trim() || !dirtyName) || editor === 'split' && (!newName.trim() || !splitMove?.ids.length)} onClick={() => {
          if (editor === 'rename') editGroup({ kind: 'RENAME', destination_id: groupId, label: groupName }, editReason.trim() || `사용자가 그룹 이름을 “${groupName}”(으)로 변경했습니다.`, groupId)
          else if (editor === 'merge') editGroup({ kind: 'MERGE', group_ids: [...selectedGroups], destination_id: [...selectedGroups][0] }, editReason.trim() || `사용자가 ${n(selectedGroups.size)}개 그룹을 병합했습니다.`, [...selectedGroups][0])
          else if (splitMove && splitMove.revision === data.revision) {
            const id = (newGroup.current ||= `manual-${crypto.randomUUID()}`)
            editGroup({ kind: 'MOVE', record_ids: splitMove.ids, new_group_id: id, label: newName }, editReason.trim() || `사용자가 로그 ${n(splitMove.ids.length)}건을 새 그룹 “${newName}”(으)로 분리했습니다.`, id)
          }
        }}>{busy ? <LoaderCircle size={14} className="animate-spin" /> : null}{editor === 'rename' ? '그룹 이름 저장' : editor === 'merge' ? '그룹 병합' : '선택 로그를 새 그룹으로 분리'}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </section>
}
