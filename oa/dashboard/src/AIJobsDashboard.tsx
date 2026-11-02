import { useEffect, useRef, useState } from 'react'
import { Activity, ArrowUpRight, CheckCircle2, Clock3, RefreshCw, Server, ShieldAlert, Trash2 } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './components/ui/dialog'
import { aiExecutionLabel, aiJobActive, aiJobParallel, aiJobMessage, aiValidationMessage, WorkspaceError, workspaceRequest, workspaceTime } from './workspaceApi'
import type { CodexJob } from './workspaceApi'
import UnknownBatchRecovery from './UnknownBatchRecovery'
import './ai-jobs.css'

type Job = CodexJob & { observed_at: string; phase?: string; can_cancel: boolean; can_resume?: boolean; report: { project_id: string; block_id: string; version_id: string; report_id: string; group_id: string | null; item_id: string; family: string } }
type Snapshot = { items: Job[]; worker: { site: string; online: boolean; last_seen: string | null }; server_time: string }
const phases: Record<string, string> = { QUEUED: '작업자 대기', PREPARING: '전체 입력 준비', SENDING: '서버로 요청 전송 중', SERVER_RESPONDED: '서버 응답 수신 중', RESPONSE_RECEIVED: '응답 수신 완료', VALIDATING: '응답·근거 검증', TEXT: '보존 본문 분석', TEXT_MERGE: '본문 분석 통합', CLASSIFY: '로그 분류', MERGE: '그룹 통합', REVIEW: '그룹 판정', REVIEW_MERGE: '판정 통합' }
const statuses: Record<string, string> = { PENDING: '대기', RUNNING: '처리 중', RESULT_READY: '저장 검증', COMPLETED: '완료', FAILED: '실패', STALE: '입력 변경', UNKNOWN: '결과 확인 필요', CANCELLED: '취소' }
const seconds = (value: number) => value < 60 ? `${Math.max(0, value).toFixed(1)}초` : `${Math.floor(value / 60)}분 ${Math.round(value % 60)}초`

export default function AIJobsDashboard({ projectId, compact = false, onAll, refresh = 0 }: { projectId?: string; compact?: boolean; onAll?: () => void; refresh?: number }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [clock, setClock] = useState(Date.now())
  const [offset, setOffset] = useState(0)
  const [cancelling, setCancelling] = useState('')
  const [resuming, setResuming] = useState('')
  const [deleting, setDeleting] = useState('')
  const [removeTarget, setRemoveTarget] = useState<Job | null>(null)
  const [deleteError, setDeleteError] = useState('')
  const deletedJobs = useRef(new Set<string>())
  // Reuse the same id if the response is lost: the server can safely resolve
  // a request that may already have committed without starting another cycle.
  const resumeRequests = useRef(new Map<string, string>())
  const accessEpoch = useRef(0)
  function clearAccessSnapshot() {
    accessEpoch.current++
    resumeRequests.current.clear()
    deletedJobs.current.clear()
    setRemoveTarget(null); setDeleteError('')
    setSnapshot(null)
  }
  const accessDenied = (e: unknown) => e instanceof WorkspaceError && (e.status === 401 || e.status === 403 || ['UNAUTHORIZED', 'FORBIDDEN', 'ACCOUNT_NOT_LINKED', 'ACCOUNT_DISABLED', 'NOT_FOUND', 'RESOURCE_NOT_FOUND'].includes(e.code))
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      let delay = 15000
      const epoch = accessEpoch.current
      try {
        const value = await workspaceRequest<Snapshot>('/ai/jobs', { signal: controller.signal })
        if (controller.signal.aborted) return
        if (epoch === accessEpoch.current) {
          setSnapshot({ ...value, items: value.items.filter(job => !deletedJobs.current.has(job.id)) }); setOffset(Date.parse(value.server_time) - Date.now()); setError('')
          if (value.items.some(aiJobActive)) delay = 5000
        }
      } catch (e) {
        if (controller.signal.aborted) return
        if (epoch === accessEpoch.current) {
          if (accessDenied(e)) clearAccessSnapshot()
          setError(e instanceof Error ? e.message : '작업 현황을 조회하지 못했습니다.')
        }
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, delay)
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [reload, refresh])
  useEffect(() => { const timer = setInterval(() => setClock(Date.now()), 1000); return () => clearInterval(timer) }, [])
  async function cancel(job: Job) {
    setCancelling(job.id)
    try { await workspaceRequest(`/ai/jobs/${encodeURIComponent(job.id)}/cancel`, { body: {} }); setReload(v => v + 1) }
    catch (e) { if (accessDenied(e)) clearAccessSnapshot(); setError(e instanceof Error ? e.message : '취소하지 못했습니다.') }
    finally { setCancelling('') }
  }
  async function resume(job: Job) {
    if (!job.can_resume || !job.resumable || !['FAILED', 'CANCELLED'].includes(job.status) || resuming || cancelling) return
    const requestId = resumeRequests.current.get(job.id) || crypto.randomUUID()
    resumeRequests.current.set(job.id, requestId)
    setResuming(job.id); setError('')
    try {
      await workspaceRequest(`/ai/jobs/${encodeURIComponent(job.id)}/resume`, { body: { request_id: requestId } })
      resumeRequests.current.delete(job.id)
      setReload(v => v + 1)
    } catch (e) {
      if (accessDenied(e)) clearAccessSnapshot()
      setError(e instanceof WorkspaceError && e.code === 'NETWORK_ERROR'
        ? '재개 요청 결과를 확인하지 못했습니다. 다시 누르면 같은 요청으로 확인합니다.'
        : e instanceof Error ? e.message : '작업을 재개하지 못했습니다.')
    } finally { setResuming('') }
  }
  async function removeJob() {
    const job = removeTarget
    if (!job?.can_delete || deleting || cancelling || resuming) return
    const epoch = accessEpoch.current
    setDeleting(job.id); setDeleteError('')
    try {
      await workspaceRequest(`/ai/jobs/${encodeURIComponent(job.id)}/delete`, { body: {} })
      if (epoch !== accessEpoch.current) return
      deletedJobs.current.add(job.id)
      resumeRequests.current.delete(job.id)
      setSnapshot(value => value ? { ...value, items: value.items.filter(item => item.id !== job.id) } : value)
      setRemoveTarget(null); setReload(value => value + 1)
    } catch (e) {
      if (epoch !== accessEpoch.current) return
      if (accessDenied(e)) { clearAccessSnapshot(); setError(e instanceof Error ? e.message : '삭제 권한을 확인하지 못했습니다.') }
      else setDeleteError(e instanceof WorkspaceError && e.code === 'NETWORK_ERROR'
        ? '삭제 요청 결과를 확인하지 못했습니다. 다시 누르면 같은 작업의 삭제 상태를 확인합니다.'
        : e instanceof Error ? e.message : '작업을 삭제하지 못했습니다.')
    } finally { setDeleting('') }
  }
  const jobs = (snapshot?.items || []).filter(job => !projectId || job.report.project_id === projectId)
  const visible = compact ? jobs.slice(0, 3) : jobs
  return <section className="ai-jobs" aria-label="AI 작업 현황">
    <header className="ai-jobs-header"><div><span className="ai-jobs-eyebrow"><Activity size={14} />LIVE ACTIVITY</span><h2>{compact ? 'AI 작업 현황' : '최근 요청'} <span>{jobs.filter(aiJobActive).length}개 진행 중</span></h2><p>내가 요청한 최근 작업 · 실제 처리 내역을 자동으로 갱신합니다.</p></div><div className="ai-jobs-tools"><span className={`ai-worker-health ${snapshot?.worker.online && !error ? 'online' : ''}`}><i /><Server size={13} />VWP 작업자 {error ? '확인 불가' : snapshot ? snapshot.worker.online ? '연결됨' : '응답 없음' : '확인 중'}</span><Button variant="outline" size="sm" aria-label="AI 작업 현황 새로고침" onClick={() => setReload(v => v + 1)}><RefreshCw size={14} />새로고침</Button>{compact && onAll && <Button variant="ghost" size="sm" onClick={onAll}>전체 보기<ArrowUpRight size={14} /></Button>}</div></header>
    {error && <p className="ai-jobs-warning" role="alert">{error}{snapshot ? ' 마지막으로 수신한 상태를 표시합니다.' : ''}</p>}
    {snapshot && !snapshot.worker.online && <p className="ai-jobs-warning"><ShieldAlert size={15} />VWP 작업자 응답이 없습니다. 마지막 수신 {workspaceTime(snapshot.worker.last_seen)} · 요청을 중복 실행하지 말고 작업자를 확인해 주세요.</p>}
    {!snapshot && !error ? <p className="ai-jobs-empty" role="status">AI 작업 현황을 불러오는 중…</p> : !snapshot ? null : !visible.length ? <div className="ai-jobs-empty"><Activity size={22} /><div><strong>아직 요청한 AI 작업이 없습니다.</strong><p>로그 분류나 그룹 판정을 실행하면 진행 상황이 여기에 표시됩니다.</p></div></div> : <div className="ai-jobs-grid">{visible.map(job => {
      const active = aiJobActive(job)
      const p = job.progress
      const parallel = aiJobParallel(job)
      const stopping = active && parallel && p?.cancel_requested === true
      const roundTotal = p?.round_total || 0
      const roundCompleted = p?.round_completed || 0
      const percent = job.status === 'COMPLETED' ? 100 : roundTotal ? Math.min(100, Math.floor(roundCompleted * 100 / roundTotal)) : undefined
      const validMetric = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0
      const rawMeasured = p ? p.average_call_seconds : job.call_seconds
      const measured = validMetric(rawMeasured) ? rawMeasured : null
      const nextRetry = job.retry_after || p?.retry_after
      const retries = p?.child_retries ?? job.retry_count ?? 0
      const retryLimit = p?.retry_limit ?? job.retry_limit
      const moving = active && (job.status !== 'PENDING' || job.preparation_phase === 'PREPARING') && !stopping && !nextRetry && snapshot.worker.online && !error && deleting !== job.id
      const validCount = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
      const rawEta = active && (!parallel || job.status === 'RUNNING') && !stopping && !nextRetry && snapshot?.worker.online && !error ? parallel ? p?.wall_stage_eta_seconds : p?.stage_eta_seconds : null
      const etaBase = Date.parse(parallel ? p?.wall_measured_at || '' : job.observed_at)
      const etaAt = validMetric(rawEta) && Number.isFinite(etaBase) ? etaBase + rawEta * 1000 : null
      const elapsed = Math.max(0, ((job.finished_at ? Date.parse(job.finished_at) : clock + offset) - Date.parse(job.created_at)) / 1000)
      const phase = !active ? statuses[job.status] : stopping ? '취소 요청됨' : nextRetry ? parallel ? '일부 배치 재시도 대기' : 'AI 요청 재시도 대기' : parallel ? phases[p?.phase || ''] || '배치 처리' : phases[job.preparation_phase || ''] || phases[job.phase || ''] || phases[p?.phase || ''] || (job.status === 'PENDING' ? '작업자 대기' : job.status === 'RESULT_READY' ? '현재 입력과 저장 결과 검증' : active ? '전체 로그 검토' : statuses[job.status])
      const params = new URLSearchParams({ page: 'workbench', project: job.report.project_id, block: job.report.block_id, version: job.report.version_id, tab: 'reports', review: job.report.report_id, item: job.report.item_id })
      return <article key={job.id} className={`ai-job-card ai-job-${job.status.toLowerCase()}`} data-moving={moving || undefined} aria-label={`${job.report.item_id} ${statuses[job.status]}`}>
        <div className="ai-job-top"><span className="ai-job-kind">{job.report.group_id ? '그룹 AI 판정' : 'AI 로그 분류'} · {job.report.family}</span><div className="ai-job-top-actions"><span className="ai-job-status">{job.status === 'COMPLETED' ? <CheckCircle2 size={13} /> : job.status === 'UNKNOWN' ? <ShieldAlert size={13} /> : <i />}{statuses[job.status]}</span><Button className="ai-job-delete" size="icon" variant="ghost" aria-label={`${job.report.item_id} 작업 삭제`} title={job.can_delete ? '작업과 실행 기록 삭제' : job.delete_error ? '작업 기록 삭제를 사용하려면 운영 설정을 확인해 주세요.' : '이 작업을 삭제할 권한이 없습니다.'} disabled={!job.can_delete || !!deleting || !!resuming || !!cancelling} onClick={() => { setRemoveTarget(job); setDeleteError('') }}><Trash2 size={14} /></Button></div></div>
        <h3>{job.report.item_id}</h3><p className="ai-job-model">{aiExecutionLabel(job)}</p>
        <div className="ai-job-stage"><span>{phase}</span><strong>{percent != null ? `${percent}%` : '진행률 대기'}</strong></div>
        <div className="ai-job-progress"><progress aria-label={`${job.report.item_id} 현재 단계 진행률`} max={100} value={percent} />{moving && <span className="ai-job-progress-motion" aria-hidden="true" style={{ width: `${percent || 100}%` }} />}</div>
        <p className="ai-job-coverage">{p ? <>{p.completed_records.toLocaleString()} / {p.total_records.toLocaleString()}건 검토 · {p.completed_calls.toLocaleString()}회 호출 완료{roundTotal > 0 && <> · 이번 단계 {roundCompleted} / {roundTotal}</>}</> : job.total_records ? `전체 ${job.total_records.toLocaleString()}건 · 전체 응답 후 검증` : '전체 입력 준비 후 처리량을 표시합니다.'}</p>
        {parallel && p?.inflight_calls != null && p.concurrency != null && <p className="ai-job-coverage">이 작업에서 동시 처리 {p.inflight_calls} / {p.allocated_concurrency ?? p.concurrency}개{p.allocated_concurrency != null && ` · 작업 상한 ${p.concurrency}개`}{p.active_descriptors?.length ? ` · 진행 중 배치 ${p.active_descriptors.map(value => `${value.index + 1}`).join(', ')}` : ''}</p>}
        {!parallel && retryLimit != null && retries > 0 && <p className="ai-job-coverage">현재 요청 재시도 {retries} / {retryLimit}{nextRetry && <> · 다음 시도 {new Date(nextRetry).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</>}</p>}
        <dl className="ai-job-metrics"><div><dt><Clock3 size={12} />{active ? '경과 시간' : '총 소요 시간'}</dt><dd>{seconds(elapsed)}</dd></div><div><dt title="완료된 AI 배치의 응답 시간 평균">배치당 평균</dt><dd>{measured != null ? seconds(measured) : active ? '측정 중' : '—'}</dd></div><div><dt>이번 단계 예상 종료</dt><dd>{etaAt != null ? etaAt > clock + offset ? new Date(etaAt).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '예상보다 지연 중' : active && !stopping ? error || !snapshot?.worker.online ? '확인 불가' : nextRetry ? '재시도 대기 중' : parallel && job.status !== 'RUNNING' ? '—' : '측정치 수집 중' : '—'}</dd></div></dl>
        <dl className="ai-job-structure"><div><dt title="현재 통합 단계의 반복 횟수">병합 iteration</dt><dd>{validCount(job.merge_iteration) ? `${job.merge_iteration}회` : '—'}</dd></div><div><dt title="진행 중에는 아직 통합하지 않은 후보 그룹도 포함합니다.">현재 그룹 수</dt><dd>{validCount(job.current_group_count) ? `${job.current_group_count.toLocaleString()}개` : '—'}</dd></div></dl>
        <p className="ai-job-note">{stopping ? '취소 요청됨 · 진행 중 응답 확인 후 종료' : nextRetry ? aiJobMessage(job) : active && p ? parallel ? p?.execution === 'parallel-v2' ? '완료된 자리에 다음 배치를 이어서 처리합니다. 예상은 현재 동시 처리와 남은 작업량을 반영하며, 이번 단계 이후 통합·검증이 남을 수 있습니다.' : '평균은 완료된 배치의 응답 시간입니다. 예상 종료는 이번 단계만 포함하며 이후 통합·검증이 남을 수 있습니다.' : '예상 시간은 이번 단계의 완료된 호출 기준입니다. 이후 통합·검증이 남을 수 있습니다.' : job.can_resume && job.resumable && ['FAILED', 'CANCELLED'].includes(job.status) ? (p ? '저장된 완료 배치를 유지하고 중단 지점부터 재개할 수 있습니다.' : '현재 입력으로 작업을 재개할 수 있습니다.') : aiJobMessage(job)}</p>
        {aiValidationMessage(job) && <p className="ai-job-note">{aiValidationMessage(job)}</p>}
        {(job.error || p?.retry_error) && <code className="ai-job-error">{job.error || p?.retry_error}{p?.retry_http_status ? ` · HTTP ${p.retry_http_status}` : ''}</code>}
        <footer><span>등록 {workspaceTime(job.created_at)}</span><div>{(job.can_cancel || stopping) && <Button size="sm" variant="ghost" disabled={!!cancelling || !!resuming || !!deleting || stopping} onClick={() => void cancel(job)}>{stopping ? '취소 요청됨' : cancelling === job.id ? '취소 중…' : '작업 취소'}</Button>}{job.can_resume && job.resumable && ['FAILED', 'CANCELLED'].includes(job.status) && <Button size="sm" variant="ghost" disabled={!!resuming || !!cancelling || !!deleting} onClick={() => void resume(job)}>{resuming === job.id ? '재개 요청 중…' : '중단 지점부터 재개'}</Button>}<UnknownBatchRecovery job={job} disabled={!!cancelling || !!resuming || !!deleting} onAccessDenied={clearAccessSnapshot} onRefresh={() => setReload(v => v + 1)} /><Button size="sm" variant="outline" asChild><a href={`/?${params}`}>검토 열기<ArrowUpRight size={13} /></a></Button></div></footer>
      </article>
    })}</div>}
    <Dialog open={!!removeTarget} onOpenChange={open => { if (!open && !deleting) { setRemoveTarget(null); setDeleteError('') } }}><DialogContent><DialogHeader><DialogTitle>AI 작업 삭제</DialogTitle><DialogDescription>{removeTarget?.report.item_id} 작업의 실행 기록과 중간 결과를 DB에서 삭제합니다. 삭제한 작업은 재개할 수 없습니다. 보고서 원본과 이미 적용된 분류·판정은 유지됩니다.{removeTarget && aiJobActive(removeTarget) && ' 이미 예약하거나 전송한 AI 요청은 완료될 수 있지만, 삭제 후 도착한 응답은 반영하지 않습니다.'}</DialogDescription></DialogHeader>{deleteError && <p className="ai-jobs-warning" role="alert">{deleteError}</p>}<DialogFooter><Button variant="outline" disabled={!!deleting} onClick={() => setRemoveTarget(null)}>돌아가기</Button><Button variant="destructive" disabled={!!deleting} onClick={() => void removeJob()}>{deleting ? '삭제 중…' : '작업과 기록 삭제'}</Button></DialogFooter></DialogContent></Dialog>
  </section>
}
