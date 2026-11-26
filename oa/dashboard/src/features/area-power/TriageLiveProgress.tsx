import { useEffect, useState } from 'react'
import { AlertTriangle, Clock3, RefreshCw, Server } from 'lucide-react'
import { date, format, object, text, type TriageJob, type TriageQueue, type TriageRun, type TriageWorker } from './api'
import { Button } from './components/workspace'

export const PHASE_LABEL: Record<string, string> = {
  queued: 'VWP 작업자 수락 대기 중',
  validating: '권한·AI 연결 확인 중',
  loading_comparison: '비교 CSV 로드 및 후보 선별 중',
  evaluating_expectations: '승인된 예상 변화 범위 대조 중',
  calling_model: 'AI 모델 호출 중',
  retrying: '재시도 대기 중',
  splitting_batch: '입력 크기 초과로 묶음 분할 재시도 중',
  saving_batch: '판정 결과 저장 중',
  completed: '분석 완료',
  failed: '분석 실패',
}

export const SUB_PHASE_LABEL: Record<string, string> = {
  preparing: '요청 패킷 준비',
  sending: 'AI 서버로 요청 전송 중',
  server_responded: 'AI 서버 응답 수신 중',
  response_received: '응답 수신 완료',
  validating: '응답 형식·근거 검증 중',
}

const JOB_KIND_LABEL: Record<string, string> = {
  ingest: 'CSV 검증',
  compare: '비교/추적 처리',
  extract: '메모 추출',
  judge: 'AI 분석',
  mail: '메일 발송',
}

export const triageNum = (value: unknown): number | null => {
  if (value == null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function formatDuration(totalSeconds: number): string {
  const sec = Math.max(0, Math.floor(totalSeconds))
  if (sec < 60) return `${sec}초`
  const min = Math.floor(sec / 60)
  const rem = sec % 60
  if (min < 60) return `${min}분 ${rem}초`
  const hrs = Math.floor(min / 60)
  return `${hrs}시간 ${min % 60}분`
}

function elapsedSeconds(fromIso: unknown, nowMs: number): number | null {
  if (typeof fromIso !== 'string' || !fromIso) return null
  const ms = Date.parse(fromIso)
  return Number.isFinite(ms) ? Math.max(0, (nowMs - ms) / 1000) : null
}

export function deriveTriageRunMetrics(run: TriageRun | undefined, job: TriageJob | null) {
  const coverage = object(run?.payload.coverage)
  const progress = object(run?.payload.progress)
  const aiModel = object(run?.payload.ai_model)
  const expectedItems = triageNum(coverage.expected_items)
  const processedItems = triageNum(coverage.processed_items) ?? 0
  const codeExplainedItems = triageNum(coverage.code_explained_items) ?? 0
  const rawBatches = triageNum(coverage.batches) ?? triageNum(progress.total_batches)
  const estimatedBatches = rawBatches ?? (expectedItems != null && expectedItems > 0 ? Math.ceil(expectedItems / 20) : null)
  const processedBatches = triageNum(coverage.processed_batches) ?? 0
  const retryCalls = triageNum(coverage.retry_calls) ?? 0
  const retryLimit = triageNum(run?.payload.retry_limit)
  const phaseKey = text(progress.phase) || (run?.state === 'queued' ? (job?.state === 'running' ? 'validating' : 'queued') : run?.state || '')
  const subPhaseKey = text(progress.sub_phase)
  const percent = expectedItems != null && expectedItems > 0 ? Math.min(100, Math.round(processedItems * 100 / expectedItems)) : null
  const phaseText = PHASE_LABEL[phaseKey] || (run?.state === 'queued' ? 'VWP 작업자 수락 대기 중' : '분석 처리 중')
  const subPhaseText = SUB_PHASE_LABEL[subPhaseKey] || ''
  const batchesDisplay = rawBatches != null ? format(rawBatches) : estimatedBatches != null ? `약 ${format(estimatedBatches)}` : '?'
  const modelDisplay = text(aiModel.model)
    ? `${text(aiModel.site) === 'vwp' ? 'VWP' : 'OA'}${text(aiModel.connection_label) ? ` · ${text(aiModel.connection_label)}` : ''} · ${text(aiModel.model)}`
    : ''
  return {
    progress,
    expectedItems,
    processedItems,
    codeExplainedItems,
    rawBatches,
    processedBatches,
    retryCalls,
    retryLimit,
    phaseKey,
    phaseText,
    subPhaseText,
    percent,
    batchesDisplay,
    modelDisplay,
  }
}

interface TriageLiveProgressProps {
  run: TriageRun
  job: TriageJob | null
  queue: TriageQueue | null
  worker: TriageWorker | null
  serverOffset: number
  verdictSummary: { total: number; unexpected: number; needsReview: number; expected: number }
  onRefresh: () => void
}

export function TriageLiveProgress({
  run,
  job,
  queue,
  worker,
  serverOffset,
  verdictSummary,
  onRefresh,
}: TriageLiveProgressProps) {
  const [clock, setClock] = useState(() => Date.now())

  useEffect(() => {
    setClock(Date.now())
    const timer = window.setInterval(() => setClock(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const {
    progress,
    expectedItems,
    processedItems,
    codeExplainedItems,
    rawBatches,
    processedBatches,
    retryCalls,
    retryLimit,
    phaseKey,
    phaseText,
    subPhaseText,
    percent,
    batchesDisplay,
    modelDisplay,
  } = deriveTriageRunMetrics(run, job)

  const nowMs = clock + serverOffset
  const currentBatch = triageNum(progress.current_batch)
  const batchItems = triageNum(progress.batch_items)
  const splitRemaining = triageNum(progress.split_remaining) ?? 0
  const attempt = triageNum(progress.attempt)
  const maxAttempts = triageNum(progress.max_attempts) ?? (retryLimit != null ? retryLimit + 1 : null)
  const lastError = text(progress.last_error)
  const lastHttpStatus = triageNum(progress.last_http_status)
  const timeoutSeconds = triageNum(progress.timeout_seconds)
  const avgBatchSeconds = triageNum(progress.avg_batch_seconds)
  const retryAtMs = typeof progress.retry_at === 'string' && progress.retry_at ? Date.parse(progress.retry_at) : NaN
  const retryCountdown = Number.isFinite(retryAtMs) ? Math.max(0, Math.ceil((retryAtMs - nowMs) / 1000)) : null
  const totalElapsed = elapsedSeconds(run.created_at, nowMs)
  const phaseElapsed = elapsedSeconds(progress.phase_started_at || run.updated_at || run.created_at, nowMs)
  const otherRunningElapsed = elapsedSeconds(queue?.other_running_since, nowMs)
  const remainingBatches = rawBatches != null ? Math.max(0, rawBatches - processedBatches) : null
  const estimatedRemainingSec = avgBatchSeconds != null && remainingBatches != null && remainingBatches > 0 ? avgBatchSeconds * remainingBatches : null
  const lastWorkerSeen = worker?.heartbeat_at || worker?.last_job_activity_at || null

  function renderDiagnostic() {
    if (worker?.online === false) {
      return (
        <p className="notice warning" role="alert">
          <AlertTriangle size={14} aria-hidden="true" />{' '}
          {run.state === 'queued'
            ? `중앙 VWP 작업자(python -m vwp_bridge.area_power)의 최근 응답이 없습니다. 서버가 실행 중인지 확인해 주세요.${lastWorkerSeen ? ` (마지막 확인: ${date(lastWorkerSeen)})` : ''}`
            : `실행 도중 중앙 VWP 작업자 응답이 끊겼습니다. VWP 서버가 비정상 종료되었다면 이 분석이 멈춰 있을 수 있으므로 서버 상태를 확인한 뒤 삭제 후 다시 실행해 주세요.${lastWorkerSeen ? ` (마지막 확인: ${date(lastWorkerSeen)})` : ''}`}
        </p>
      )
    }
    if (run.state === 'queued' && job?.state === 'running') {
      return <p className="notice">중앙 VWP 작업자가 작업을 수락했습니다. 권한·AI 연결 확인 및 비교 CSV 로딩을 시작하는 중입니다.</p>
    }
    if (run.state === 'queued' && (queue?.other_running_count ?? 0) > 0) {
      const kindName = JOB_KIND_LABEL[text(queue?.other_running_job_kind)] || text(queue?.other_running_job_kind) || '다른 작업'
      return <p className="notice">중앙 VWP 작업자가 현재 다른 작업({kindName}{otherRunningElapsed != null ? ` · 경과 ${formatDuration(otherRunningElapsed)}` : ''})을 처리 중입니다. 앞선 대기 작업 {format(queue?.pending_ahead ?? 0)}건 완료 후 자동으로 시작됩니다.</p>
    }
    if (run.state === 'queued' && (queue?.pending_ahead ?? 0) > 0) {
      return <p className="notice">VWP 작업 대기열에 앞선 작업 {format(queue?.pending_ahead ?? 0)}건이 있습니다. 순서대로 자동 시작됩니다.</p>
    }
    if (run.state === 'queued' && totalElapsed != null && totalElapsed >= 15) {
      return (
        <p className="notice warning" role="alert">
          <AlertTriangle size={14} aria-hidden="true" />{' '}
          VWP 작업자가 {formatDuration(totalElapsed)} 동안 작업을 수락하지 않았습니다. 중앙 VWP 서버(<code>python -m vwp_bridge.area_power</code>) 실행 상태와 Area DB 연결을 확인해 주세요.{lastWorkerSeen ? ` (마지막 작업자 활동: ${date(lastWorkerSeen)})` : ' (최근 작업자 활동 기록 없음)'}
        </p>
      )
    }
    if (phaseKey === 'retrying') {
      return (
        <p className="notice warning">
          일시적 오류({lastError || 'retryable'}{lastHttpStatus != null ? ` · HTTP ${lastHttpStatus}` : ''})로 재시도 대기 중입니다.
          {retryCountdown != null ? ` 약 ${retryCountdown}초 후 ${attempt != null ? `${attempt + 1}` : '다음'}차 시도를 진행합니다.` : ''}
        </p>
      )
    }
    if (phaseKey === 'calling_model' && phaseElapsed != null && phaseElapsed >= 45) {
      return <p className="notice">AI 모델 응답을 기다리는 중입니다 (현재 단계 경과 {formatDuration(phaseElapsed)}{timeoutSeconds != null ? ` · 호출 제한시간 ${format(timeoutSeconds)}초` : ''}).</p>
    }
    if (run.state === 'running' && !text(progress.phase) && totalElapsed != null && totalElapsed >= 20) {
      return <p className="notice">세부 진행 단계 정보가 수신되지 않고 있습니다. 중앙 VWP 서버(<code>vwp_bridge.area_power</code>)가 최신 버전으로 재시작되었는지 확인해 주세요.</p>
    }
    return null
  }

  return (
    <div className="triage-live-card" aria-label="AI 분석 실시간 진행 현황">
      <div className="triage-live-header">
        <div className="triage-live-title">
          <span className={`triage-phase-badge phase-${phaseKey}`}>{phaseText}</span>
          {subPhaseText && <span className="triage-subphase">{subPhaseText}</span>}
          {currentBatch != null && (phaseKey === 'calling_model' || phaseKey === 'retrying' || phaseKey === 'splitting_batch') && (
            <span className="triage-step-detail">
              묶음 {format(currentBatch)} / {batchesDisplay}
              {batchItems != null ? ` · ${format(batchItems)}개 instance` : ''}
              {splitRemaining > 0 ? ` (분할 잔여 ${format(splitRemaining)}건)` : ''}
              {attempt != null ? ` · ${format(attempt)}${maxAttempts != null ? `/${format(maxAttempts)}` : ''}차 시도` : ''}
            </span>
          )}
        </div>
        <div className="triage-live-tools">
          {worker && (
            <span className={`triage-worker-pill ${worker.online === true ? 'online' : worker.online === false ? 'offline' : 'unknown'}`}>
              <Server size={12} aria-hidden="true" />
              VWP 작업자 {worker.online === true ? (worker.state === 'busy' ? '실행 중' : '연결됨') : worker.online === false ? '응답 없음' : '상태 미확인'}
            </span>
          )}
          <Button variant="outline" size="sm" onClick={onRefresh}><RefreshCw size={12} />새로고침</Button>
        </div>
      </div>
      <div className="triage-bar-row">
        <div className="triage-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? undefined}>
          <div className={`triage-bar-fill ${percent == null || (percent === 0 && phaseKey !== 'completed') ? 'indeterminate' : ''}`} style={percent != null && percent > 0 ? { width: `${percent}%` } : undefined} />
        </div>
        <span className="triage-bar-pct">{percent != null ? `${percent}%` : '대기'}</span>
      </div>
      <dl className="triage-live-grid">
        <div>
          <dt>처리 현황</dt>
          <dd>묶음 {format(processedBatches)} / {batchesDisplay} · instance {format(processedItems)} / {format(expectedItems ?? '?')}{codeExplainedItems > 0 ? ` (규칙 설명 ${format(codeExplainedItems)})` : ''}</dd>
        </div>
        <div>
          <dt><Clock3 size={11} aria-hidden="true" /> 경과 시간</dt>
          <dd>총 {totalElapsed != null ? formatDuration(totalElapsed) : '—'}{phaseElapsed != null ? ` · 현재 단계 ${formatDuration(phaseElapsed)}` : ''}</dd>
        </div>
        <div>
          <dt>{estimatedRemainingSec != null ? '예상 남음' : '응답 기준'}</dt>
          <dd>{estimatedRemainingSec != null ? `약 ${formatDuration(estimatedRemainingSec)} (묶음당 평균 ${avgBatchSeconds}초)` : timeoutSeconds != null ? `호출 제한시간 ${format(timeoutSeconds)}초` : '측정 대기 중'}</dd>
        </div>
        <div>
          <dt>대기열 · 작업 상태</dt>
          <dd>
            작업 {job ? ({ pending: '수락 대기', running: '실행 중', completed: '완료', failed: '실패' }[job.state] || job.state) : '확인 중'}
            {queue && run.state === 'queued' ? ` · 앞선 대기 ${format(queue.pending_ahead ?? 0)}건${(queue.other_running_count ?? 0) > 0 ? ' · 다른 작업 실행 중' : ''}` : ''}
          </dd>
        </div>
        {modelDisplay && (
          <div>
            <dt>분석 모델</dt>
            <dd>{modelDisplay}</dd>
          </div>
        )}
        <div>
          <dt>재시도 · 누적 판정</dt>
          <dd>
            재시도 {format(retryCalls)}회{retryLimit != null ? ` (묶음당 최대 ${format(retryLimit)}회)` : ''}
            {verdictSummary.total > 0 ? ` · 이상 ${verdictSummary.unexpected} · 확인 필요 ${verdictSummary.needsReview} · 설명됨 ${verdictSummary.expected}` : ''}
          </dd>
        </div>
      </dl>
      {renderDiagnostic()}
    </div>
  )
}
