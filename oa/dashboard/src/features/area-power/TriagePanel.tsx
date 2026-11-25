import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Clock3, LoaderCircle, Mail, RefreshCw, Search, Server, Sparkles } from 'lucide-react'
import { array, date, format, formatPercent, object, request, text, triagePreviewSchema, triageResultSchema, triageRunsSchema, triageStartedSchema, unknownSchema, type Auth, type TriageItem, type TriagePreview, type TriageResult, type TriageRun } from './api'
import { AnalysisDelete } from './AnalysisDelete'
import { Button, useAction } from './components/workspace'

export const VERDICT_LABEL: Record<string, string> = { unexpected: '이상', needs_review: '확인 필요', expected: '설명됨', minor: '변화 미미' }
const CAUSE_LABEL: Record<string, string> = { note: '메모', vth_shift: 'Vth 변화', drive_shift: 'Drive 변화', new_instance: '신규 instance', unknown: '원인 불명' }
const FLAG_LABEL: Record<string, string> = { area_conditions_unconfirmed: 'Area 조건 미확인', power_conditions_unconfirmed: 'Power 조건 미확인', library_unknown: 'library 미확인', library_different: 'library 다름 · Vth/Drive는 참고용', removed_in_current: '현재 실행에 없음' }
const EXCLUSION_LABEL: Record<string, string> = { corner_different: '양쪽 corner가 다름', analysis_conditions_different: '분석 조건이 다름', area_unit_different: 'area 단위가 다름', baseline_missing: '기준 실행 없음' }
const PHASE_LABEL: Record<string, string> = {
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
const SUB_PHASE_LABEL: Record<string, string> = {
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
const NUMBER = /^(?:\d+(?:\.\d*)?|\.\d+)$/
const running = (state: string) => ['queued', 'pending', 'running'].includes(state)
const signed = (value: unknown, unit: string) => value == null || value === '' ? '—' : `${text(value).startsWith('-') ? '' : '+'}${formatPercent(value)}${unit}`
const num = (value: unknown): number | null => {
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

const RANKING_LABEL: Record<string, string> = {
  power_abs_top: 'Power 절대량 Top',
  power_damped_top: 'Power 체급급증 Top',
  area_abs_top: 'Area 절대량 Top',
  area_damped_top: 'Area 체급급증 Top',
}

function summary(card: Record<string, unknown>) {
  const area = object(card.area), power = object(card.power_mw)
  const areaPart = `Logic Area ${signed(area.delta_pct, '%')}${area.damped_pct != null ? ` (보정 ${signed(area.damped_pct, '%')})` : ''}`
  const parts = [areaPart]
  if (!('excluded' in power)) {
    const pwrDelta = power.delta != null && text(power.delta) !== '' ? `${text(power.delta).startsWith('-') ? '' : '+'}${format(power.delta)} mW · ` : ''
    const pwrDamped = power.damped_pct != null ? ` (보정 ${signed(power.damped_pct, '%')})` : ''
    parts.unshift(`Total Power ${pwrDelta}${signed(power.delta_pct, '%')}${pwrDamped}`)
  }
  return parts.join(' · ')
}

function renderAttributionBox(rawAttr: unknown) {
  const attr = object(rawAttr)
  const sections: Array<{ key: string; title: string; unit: string }> = [
    { key: 'power', title: 'Total Power', unit: ' mW' },
    { key: 'area', title: 'Logic Area', unit: '' },
  ]
  const lines = sections.map(({ key, title, unit }) => {
    const info = object(attr[key])
    const rows = array(info.items).map(object)
    if (!info.analyzed || !rows.length) return null
    const chips = rows.slice(0, 5).map((r, idx) => (
      <span key={text(r.path) || idx} className="triage-attr-chip">
        <b>{idx + 1}. {text(r.path)}</b> {text(r.delta).startsWith('-') ? '' : '+'}{format(r.delta)}{unit} ({formatPercent(r.share_of_increase_pct)}%)
      </span>
    ))
    const restCount = Number(info.rest_count || 0)
    return (
      <div key={key} className="triage-attr-row">
        <span className="triage-attr-label">{title} 1단계 기여 ({signed(info.root_delta_pct, '%')})</span>
        <div className="triage-attr-chips">
          {chips}
          {restCount > 0 && <span className="triage-attr-rest">외 {restCount}개 {text(info.rest_delta).startsWith('-') ? '' : '+'}{format(info.rest_delta)}{unit}</span>}
        </div>
      </div>
    )
  }).filter(Boolean)
  if (!lines.length) return null
  return <div className="triage-attribution" aria-label="1단계 Sub-block 증가 지분 요약">{lines}</div>
}

const DRIVE_SHARE_LABEL: Record<string, string> = {
  drv_sub1x: 'sub-1x (<D1)',
  drv_d1_d3: 'D1-D3',
  drv_d4_plus: 'D4+',
  drv_other: 'other',
}

function shareLabel(raw: string, key: 'vth' | 'drive_groups' | 'drive') {
  if (key === 'drive_groups' || key === 'drive') return DRIVE_SHARE_LABEL[raw] || raw.replace(/^drv_/, '')
  return raw.replace(/^vt_/, '').toUpperCase()
}

function shares(card: Record<string, unknown>, key: 'vth' | 'drive_groups' | 'drive') {
  return array(card[key]).map(object).slice(0, 5).map(share => `${shareLabel(text(share.name), key)} ${formatPercent(share.baseline_pct)}% → ${formatPercent(share.current_pct)}% (${signed(share.delta_pp, '%p')})`).join(', ')
}

export function TriagePanel({ auth, comparisonId, canWrite, labels, onItems, onLocate }: {
  auth: Auth
  comparisonId: string
  canWrite: boolean
  labels: { current: string; baseline: string }
  onItems: (items: TriageItem[]) => void
  onLocate: (item: TriageItem) => void
}) {
  const action = useAction()
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const [thresholds, setThresholds] = useState({ area_increase_pct: '10', power_increase_pct: '10', noise_share_pct: '0.1', min_area: '50' })
  const [preview, setPreview] = useState<TriagePreview | null>(null)
  const [runs, setRuns] = useState<TriageRun[]>([])
  const [historyVersion, setHistoryVersion] = useState(0)
  const [runId, setRunId] = useState('')
  const [result, setResult] = useState<TriageResult | null>(null)
  const [loadError, setLoadError] = useState('')
  const [excluded, setExcluded] = useState<Set<string>>(() => new Set())
  const [recipients, setRecipients] = useState('')
  const [tick, setTick] = useState(0)
  const [clock, setClock] = useState(() => Date.now())
  const [serverOffset, setServerOffset] = useState(0)
  const valid = Object.values(thresholds).every(value => NUMBER.test(value.trim()))
  const body = () => ({ thresholds: Object.fromEntries(Object.entries(thresholds).map(([key, value]) => [key, value.trim()])), labels })

  useEffect(() => {
    setPreview(null); setRuns([]); setRunId(''); setResult(null); setLoadError(''); setExcluded(new Set()); onItems([])
    const controller = new AbortController()
    request(auth, `/comparisons/${encodeURIComponent(comparisonId)}/triage`, triageRunsSchema, undefined, controller.signal).then(value => {
      if (controller.signal.aborted) return
      setRuns(value.runs); setRunId(value.runs[0]?.id || '')
    }).catch(cause => { if (!controller.signal.aborted) setLoadError(cause instanceof Error ? cause.message : '분석 이력을 조회하지 못했습니다.') })
    return () => controller.abort()
  }, [auth, comparisonId, historyVersion])

  useEffect(() => {
    if (!runId) { setResult(null); onItems([]); return }
    const controller = new AbortController()
    let timer: number | undefined
    request(auth, `/triage/${encodeURIComponent(runId)}`, triageResultSchema, undefined, controller.signal).then(value => {
      if (controller.signal.aborted) return
      setResult(value); setLoadError(''); onItems(value.items)
      if (value.server_time) {
        const parsed = Date.parse(value.server_time)
        if (Number.isFinite(parsed)) setServerOffset(parsed - Date.now())
      }
      if (value.ai_run.id !== runId) {
        setRunId(value.ai_run.id)
        setExcluded(new Set())
        setRuns(rows => rows.some(row => row.id === value.ai_run.id) ? rows : [value.ai_run, ...rows])
      }
      setRuns(rows => rows.map(row => row.id === value.ai_run.id ? { ...row, state: value.ai_run.state, payload: value.ai_run.payload } : row))
      const jobFailed = running(value.ai_run.state) && value.job?.state === 'failed'
      // Follow a running analysis, and a requested mail until its job settles.
      if ((running(value.ai_run.state) && !jobFailed) || value.mails.some(mail => running(text(mail.job_state)))) {
        timer = window.setTimeout(() => setTick(current => current + 1), 2000)
      }
    }).catch(cause => { if (!controller.signal.aborted) setLoadError(cause instanceof Error ? cause.message : '분석 결과를 조회하지 못했습니다.') })
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [auth, runId, tick])

  const selectedRun = runs.find(row => row.id === runId)
  const run = result?.ai_run
  const job = result?.job ?? null
  const queue = result?.queue ?? null
  const worker = result?.worker ?? null
  const jobFailed = Boolean(run && running(run.state) && job?.state === 'failed')
  const isActive = Boolean(run && running(run.state) && !jobFailed)

  useEffect(() => {
    if (!isActive) return
    setClock(Date.now())
    const timer = window.setInterval(() => setClock(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [isActive])

  async function loadPreview() {
    const value = await request(auth, `/comparisons/${encodeURIComponent(comparisonId)}/triage/preview`, triagePreviewSchema, body())
    if (alive.current) setPreview(value)
  }
  async function start() {
    const value = await request(auth, `/comparisons/${encodeURIComponent(comparisonId)}/triage`, triageStartedSchema, body())
    if (!alive.current) return
    const created: TriageRun = { can_delete: true, id: value.ai_run.id, state: value.ai_run.state, created_at: value.ai_run.created_at, payload: value.ai_run.payload }
    setRuns(rows => [created, ...rows]); setExcluded(new Set()); setPreview(null); setRunId(created.id); setTick(current => current + 1)
  }
  async function sendMail() {
    const list = recipients.split(/[,;\s]+/).filter(Boolean)
    await request(auth, `/triage/${encodeURIComponent(runId)}/mail`, unknownSchema, { recipients: list, excluded_ids: [...excluded] })
    if (alive.current) setTick(current => current + 1)
  }

  const coverage = object(run?.payload.coverage), context = object(run?.payload.side_context)
  const progress = object(run?.payload.progress), aiModel = object(run?.payload.ai_model)
  const items = result?.items || []
  const of = (verdict: string) => items.filter(item => item.verdict === verdict)
  const watchedRest = items.filter(item => object(item.card).watched === true && ['expected', 'minor'].includes(item.verdict))
  const explained = items.filter(item => item.verdict === 'expected' && object(item.card).watched !== true)
  const failedItems = items.filter(item => item.failed).length
  const counts = preview?.counts
  const previewContext = object(preview?.context)

  const nowMs = clock + serverOffset
  const expectedItems = num(coverage.expected_items)
  const processedItems = num(coverage.processed_items) ?? 0
  const codeExplainedItems = num(coverage.code_explained_items) ?? 0
  const rawBatches = num(coverage.batches) ?? num(progress.total_batches)
  const estimatedBatches = rawBatches ?? (expectedItems != null && expectedItems > 0 ? Math.ceil(expectedItems / 20) : null)
  const processedBatches = num(coverage.processed_batches) ?? 0
  const retryCalls = num(coverage.retry_calls) ?? 0
  const retryLimit = num(run?.payload.retry_limit)
  const phaseKey = text(progress.phase) || (run?.state === 'queued' ? (job?.state === 'running' ? 'validating' : 'queued') : run?.state || '')
  const subPhaseKey = text(progress.sub_phase)
  const currentBatch = num(progress.current_batch)
  const batchItems = num(progress.batch_items)
  const splitRemaining = num(progress.split_remaining) ?? 0
  const attempt = num(progress.attempt)
  const maxAttempts = num(progress.max_attempts) ?? (retryLimit != null ? retryLimit + 1 : null)
  const lastError = text(progress.last_error)
  const lastHttpStatus = num(progress.last_http_status)
  const timeoutSeconds = num(progress.timeout_seconds)
  const avgBatchSeconds = num(progress.avg_batch_seconds)
  const retryAtMs = typeof progress.retry_at === 'string' && progress.retry_at ? Date.parse(progress.retry_at) : NaN
  const retryCountdown = Number.isFinite(retryAtMs) ? Math.max(0, Math.ceil((retryAtMs - nowMs) / 1000)) : null
  const totalElapsed = elapsedSeconds(run?.created_at, nowMs)
  const phaseElapsed = elapsedSeconds(progress.phase_started_at || run?.updated_at || run?.created_at, nowMs)
  const otherRunningElapsed = elapsedSeconds(queue?.other_running_since, nowMs)
  const percent = expectedItems != null && expectedItems > 0 ? Math.min(100, Math.round(processedItems * 100 / expectedItems)) : null
  const remainingBatches = rawBatches != null ? Math.max(0, rawBatches - processedBatches) : null
  const estimatedRemainingSec = avgBatchSeconds != null && remainingBatches != null && remainingBatches > 0 ? avgBatchSeconds * remainingBatches : null
  const modelDisplay = text(aiModel.model)
    ? `${text(aiModel.site) === 'vwp' ? 'VWP' : 'OA'}${text(aiModel.connection_label) ? ` · ${text(aiModel.connection_label)}` : ''} · ${text(aiModel.model)}`
    : ''
  const phaseText = PHASE_LABEL[phaseKey] || (run?.state === 'queued' ? 'VWP 작업자 수락 대기 중' : '분석 처리 중')
  const subPhaseText = SUB_PHASE_LABEL[subPhaseKey] || ''
  const batchesDisplay = rawBatches != null ? format(rawBatches) : estimatedBatches != null ? `약 ${format(estimatedBatches)}` : '?'
  const lastWorkerSeen = worker?.heartbeat_at || worker?.last_job_activity_at || null

  function renderDiagnostic() {
    if (!run || !isActive) return null
    if (worker?.online === false) {
      return <p className="notice warning" role="alert">
        <AlertTriangle size={14} aria-hidden="true" />{' '}
        {run.state === 'queued'
          ? `중앙 VWP 작업자(python -m vwp_bridge.area_power)의 최근 응답이 없습니다. 서버가 실행 중인지 확인해 주세요.${lastWorkerSeen ? ` (마지막 확인: ${date(lastWorkerSeen)})` : ''}`
          : `실행 도중 중앙 VWP 작업자 응답이 끊겼습니다. VWP 서버가 비정상 종료되었다면 이 분석이 멈춰 있을 수 있으므로 서버 상태를 확인한 뒤 삭제 후 다시 실행해 주세요.${lastWorkerSeen ? ` (마지막 확인: ${date(lastWorkerSeen)})` : ''}`}
      </p>
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
      return <p className="notice warning" role="alert">
        <AlertTriangle size={14} aria-hidden="true" />{' '}
        VWP 작업자가 {formatDuration(totalElapsed)} 동안 작업을 수락하지 않았습니다. 중앙 VWP 서버(<code>python -m vwp_bridge.area_power</code>) 실행 상태와 Area DB 연결을 확인해 주세요.{lastWorkerSeen ? ` (마지막 작업자 활동: ${date(lastWorkerSeen)})` : ' (최근 작업자 활동 기록 없음)'}
      </p>
    }
    if (phaseKey === 'retrying') {
      return <p className="notice warning">
        일시적 오류({lastError || 'retryable'}{lastHttpStatus != null ? ` · HTTP ${lastHttpStatus}` : ''})로 재시도 대기 중입니다.
        {retryCountdown != null ? ` 약 ${retryCountdown}초 후 ${attempt != null ? `${attempt + 1}` : '다음'}차 시도를 진행합니다.` : ''}
      </p>
    }
    if (phaseKey === 'calling_model' && phaseElapsed != null && phaseElapsed >= 45) {
      return <p className="notice">AI 모델 응답을 기다리는 중입니다 (현재 단계 경과 {formatDuration(phaseElapsed)}{timeoutSeconds != null ? ` · 호출 제한시간 ${format(timeoutSeconds)}초` : ''}).</p>
    }
    if (run.state === 'running' && !text(progress.phase) && totalElapsed != null && totalElapsed >= 20) {
      return <p className="notice">세부 진행 단계 정보가 수신되지 않고 있습니다. 중앙 VWP 서버(<code>vwp_bridge.area_power</code>)가 최신 버전으로 재시작되었는지 확인해 주세요.</p>
    }
    return null
  }

  function renderItem(item: TriageItem) {
    const card = object(item.card)
    const flags = array(card.flags).map(flag => FLAG_LABEL[text(flag)] || text(flag))
    const ancestors = array(card.rolled_up_ancestors).map(text)
    const rankingTags = array(card.ranking_tags).map(text).filter(Boolean)
    const depth1Block = text(card.depth1_block)
    const distMetrics = array(card.distributed_metrics).map(text)
    const topByMetric = object(card.top_children_by_metric)
    const distNotes = distMetrics.map(part => {
      const bucket = object(topByMetric[part])
      const rows = array(bucket.items).map(object)
      if (!rows.length) return ''
      const unit = part === 'power' ? ' mW' : ''
      const topText = rows.slice(0, 3).map(r => `${text(r.path)} (${text(r.delta).startsWith('-') ? '' : '+'}${format(r.delta)}${unit})`).join(', ')
      const rest = Math.max(0, Number(bucket.positive_count || rows.length) - Math.min(3, rows.length))
      return `하위 분산(${part === 'power' ? 'Power' : 'Area'}): ${topText}${rest > 0 ? ` 외 ${rest}개` : ''}`
    }).filter(Boolean)
    return <li key={item.group_id} className={`triage-item verdict-${item.verdict}`}>
      <label className="triage-exclude" title="메일에서 이 항목을 뺍니다"><input type="checkbox" checked={excluded.has(item.group_id)} onChange={event => { const next = new Set(excluded); if (event.target.checked) next.add(item.group_id); else next.delete(item.group_id); setExcluded(next) }} />제외</label>
      <button type="button" onClick={() => onLocate(item)} title="표에서 이 instance로 이동">
        <span className="triage-head">
          <span className={`verdict-tag verdict-${item.verdict}`}>{VERDICT_LABEL[item.verdict] || item.verdict}</span>
          {depth1Block && <span className="side-tag" title="소속 1단계 Sub-block">{depth1Block}</span>}
          <strong>{text(card.current_path) || text(card.baseline_path)}</strong>
          {object(item.card).watched === true && <span className="side-tag">주요</span>}
          {rankingTags.map(tag => <span key={tag} className="side-tag">{RANKING_LABEL[tag] || tag}</span>)}
          <span className="triage-delta">{summary(card)}</span>
        </span>
        <span className="triage-reason">원인: {CAUSE_LABEL[item.cause] || item.cause} · {item.explanation}</span>
        {distNotes.map(note => <span key={note} className="triage-evidence">{note}</span>)}
        {shares(card, 'vth') && <span className="triage-evidence">Vth: {shares(card, 'vth')}</span>}
        {shares(card, 'drive_groups') && <span className="triage-evidence">Drive (그룹): {shares(card, 'drive_groups')}</span>}
        {shares(card, 'drive') && <span className="triage-evidence">{shares(card, 'drive_groups') ? 'Drive (세부)' : 'Drive'}: {shares(card, 'drive')}</span>}
        {(item.quotes || []).length > 0 && <span className="triage-evidence">근거 메모: {(item.quotes || []).slice(0, 3).map(quote => `"${quote}"`).join(' / ')}</span>}
        {(flags.length > 0 || item.failed || ancestors.length > 0) && <span className="triage-flags">{[...flags, item.failed ? `AI 판정 실패(${text(item.failure)})` : '', ancestors.length ? `영향받은 상위 ${ancestors.slice(0, 3).join(', ')}` : ''].filter(Boolean).join(' · ')}</span>}
      </button>
    </li>
  }
  const section = (title: string, list: TriageItem[], open = true) => <details className="triage-section" open={open} key={title}><summary>{title} · {list.length}건</summary>{list.length ? <ul>{list.map(renderItem)}</ul> : <p className="muted">없음</p>}</details>

  return <section className="triage-panel" aria-label="AI 이상치 분석">
    <div className="triage-controls">
      <h3><Sparkles size={15} aria-hidden="true" />AI 분석</h3>
      {([['area_increase_pct', 'Logic Area 증가 ≥', '%'], ['power_increase_pct', 'Total Power 증가 ≥', '%'], ['noise_share_pct', '기본 노이즈 비율', '%'], ['min_area', 'Logic Area 최소', '']] as const).map(([key, label, unit]) => <label key={key} className="browser-threshold" title={key === 'noise_share_pct' ? '루트 전체 대비 최소 유의미 노이즈 바닥 비율 (기본 0.1% → 분모 완충 쿠션 1.0%)' : undefined}>{label}<input type="text" inputMode="decimal" value={thresholds[key]} aria-invalid={!NUMBER.test(thresholds[key].trim())} onChange={event => { setThresholds({ ...thresholds, [key]: event.target.value }); setPreview(null) }} />{unit}</label>)}
      <Button variant="outline" size="sm" disabled={action.busy || !valid} onClick={() => action.run(loadPreview, '후보를 계산했습니다.')}><Search size={14} />미리보기</Button>
      <Button size="sm" disabled={action.busy || !valid || !canWrite || preview?.over_limit === true} onClick={() => action.run(start, 'AI 분석을 요청했습니다. 진행 상황을 자동으로 갱신합니다.')}><Sparkles size={14} />AI 분석 실행</Button>
      {runs.length > 0 && <label className="triage-runs">지난 분석<select value={runId} onChange={event => { setExcluded(new Set()); setRunId(event.target.value) }}>{runs.map(row => <option key={row.id} value={row.id}>{date(row.created_at)} · {row.state}</option>)}</select></label>}
      {canWrite && selectedRun?.can_delete && <AnalysisDelete key={runId} auth={auth} id={runId} active={isActive}
        onDeleted={() => { setRunId(''); setResult(null); onItems([]); setHistoryVersion(v => v + 1) }} />}
    </div>
    {auth.demo && <p className="notice">합성 데모는 AI와 메일을 호출하지 않습니다. 후보 선별만 보여 주고 판정은 모두 확인 필요로 남깁니다.</p>}
    {!canWrite && <p className="notice">조회 전용 · AI 분석 실행과 메일 발송에는 프로젝트 멤버 권한이 필요합니다.</p>}
    {action.feedback}
    {loadError && <div className="notice error" role="alert">{loadError}</div>}
    {preview && counts && <div className="triage-preview" role="status">
      {preview.ai_model && <p>분석 모델: {preview.ai_model.model ? `${preview.ai_model.site === 'vwp' ? 'VWP' : 'OA'} · ${preview.ai_model.connection_label} · ${preview.ai_model.model}` : '미설정'}{!preview.ai_model.ready && ` · ${preview.ai_model.error === 'AI_TASK_MODEL_REQUIRED' ? '관리자 → AI 연결·모델 관리 → 프로젝트 비교 AI 분석에서 지정하세요.' : `연결 또는 본인 인증값 확인 필요 (${preview.ai_model.error || 'AI_CONNECTION_UNAVAILABLE'})`}`}</p>}
      <p><strong>분석 대상 {counts.total?.toLocaleString() ?? 0}개</strong> · Area 증가 {counts.area_increase ?? 0} (절대Top {counts.area_abs_top ?? 0} · 급증Top {counts.area_damped_top ?? 0}) · Power 증가 {counts.power_increase ?? 0} (절대Top {counts.power_abs_top ?? 0} · 급증Top {counts.power_damped_top ?? 0}) · 신규 {counts.new_instance ?? 0} · 주요 {counts.watched ?? 0} · 상위로 묶임 {counts.rolled_up ?? 0} · 최초 예상 AI 호출 {preview.estimated_calls}회</p>
      {renderAttributionBox(preview.attribution)}
      <p>적용 메모 {preview.notes.length}건{preview.applicable_net_vers.length ? ` (${preview.applicable_net_vers.join(', ')})` : ''}{preview.same_rtl ? ' · 같은 NET_VER(RTL 동일)' : ''} · 주요 instance {preview.watched_paths.length}개 · library {{ same: '같음', unknown: '미확인(공정 미등록)', different: '다름' }[text(previewContext.library)] || text(previewContext.library)}</p>
      {preview.over_limit && <p className="notice warning">분석 대상이 {preview.max_items}개를 넘습니다. 기준을 올린 뒤 다시 미리보기 하세요. 일부만 분석하지 않습니다.</p>}
      {(['power', 'area'] as const).map(part => object(previewContext[part]).analyzed === false && <p className="notice warning" key={part}>{part === 'power' ? 'Power' : 'Area'}는 분석에서 뺍니다 · {EXCLUSION_LABEL[text(object(previewContext[part]).reason)] || text(object(previewContext[part]).reason)}</p>)}
      {preview.missing_watched.length > 0 && <p className="notice warning">현재 실행에 없는 주요 instance {preview.missing_watched.length}개 · {preview.missing_watched.slice(0, 5).join(', ')}</p>}
      {preview.note_warnings.some(warning => text(warning.code).includes('net_ver_unresolved')) && <p className="notice warning">공통 카탈로그와 연결되지 않은 버전이 계보에 있어 일부 NET_VER 메모를 적용하지 못합니다.</p>}
      {preview.notes.length === 0 && <p className="notice">적용할 메모가 없습니다. 눈에 띄는 변화는 모두 이상 또는 확인 필요로 분류됩니다.</p>}
    </div>}
    {run && <details className="triage-result" open>
      <summary className="triage-summary" role="status">
        {isActive ? <span className="triage-progress">
          <LoaderCircle size={14} className="spin" aria-hidden="true" />
          <span>{phaseText}{subPhaseText ? ` (${subPhaseText})` : ''} · 묶음 {format(processedBatches)} / {batchesDisplay} · instance {format(processedItems)} / {format(expectedItems ?? '?')}{percent != null ? ` (${percent}%)` : ''}</span>
        </span>
          : jobFailed ? <span className="triage-progress"><strong>작업 실패</strong> · {text(object(job?.error_detail).code) || 'worker_job_failed'}</span>
          : <><strong>이상 {of('unexpected').length}</strong> · 확인 필요 {of('needs_review').length} · 설명됨 {of('expected').length} · 변화 미미 {of('minor').length} · 적용 메모 {run.note_count ?? 0}건</>}
      </summary>
      {isActive && <div className="triage-live-card" aria-label="AI 분석 실시간 진행 현황">
        <div className="triage-live-header">
          <div className="triage-live-title">
            <span className={`triage-phase-badge phase-${phaseKey}`}>{phaseText}</span>
            {subPhaseText && <span className="triage-subphase">{subPhaseText}</span>}
            {currentBatch != null && (phaseKey === 'calling_model' || phaseKey === 'retrying' || phaseKey === 'splitting_batch') && <span className="triage-step-detail">
              묶음 {format(currentBatch)} / {batchesDisplay}
              {batchItems != null ? ` · ${format(batchItems)}개 instance` : ''}
              {splitRemaining > 0 ? ` (분할 잔여 ${format(splitRemaining)}건)` : ''}
              {attempt != null ? ` · ${format(attempt)}${maxAttempts != null ? `/${format(maxAttempts)}` : ''}차 시도` : ''}
            </span>}
          </div>
          <div className="triage-live-tools">
            {worker && <span className={`triage-worker-pill ${worker.online === true ? 'online' : worker.online === false ? 'offline' : 'unknown'}`}>
              <Server size={12} aria-hidden="true" />
              VWP 작업자 {worker.online === true ? (worker.state === 'busy' ? '실행 중' : '연결됨') : worker.online === false ? '응답 없음' : '상태 미확인'}
            </span>}
            <Button variant="outline" size="sm" onClick={() => setTick(current => current + 1)}><RefreshCw size={12} />새로고침</Button>
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
              {queue && run.state === 'queued' ? ` · 앞선 대기 ${format(queue.pending_ahead ?? 0)}건${(queue.other_running_count ?? 0) > 0 ? ` · 다른 작업 실행 중` : ''}` : ''}
            </dd>
          </div>
          {modelDisplay && <div>
            <dt>분석 모델</dt>
            <dd>{modelDisplay}</dd>
          </div>}
          <div>
            <dt>재시도 · 누적 판정</dt>
            <dd>
              재시도 {format(retryCalls)}회{retryLimit != null ? ` (묶음당 최대 ${format(retryLimit)}회)` : ''}
              {items.length > 0 ? ` · 이상 ${of('unexpected').length} · 확인 필요 ${of('needs_review').length} · 설명됨 ${of('expected').length}` : ''}
            </dd>
          </div>
        </dl>
        {renderDiagnostic()}
      </div>}
      {!isActive && run.payload.retry_limit != null && <p className="muted">자동 재시도 · 묶음당 최대 {format(run.payload.retry_limit)}회 · 전체 누적 {format(retryCalls)}회{modelDisplay ? ` · 모델 ${modelDisplay}` : ''}</p>}
      {jobFailed && <p className="notice error" role="alert">중앙 VWP 작업 실행이 실패했습니다{object(job?.error_detail).code ? ` (${text(object(job?.error_detail).code)})` : ''}. VWP 작업자 로그와 AI 연결을 확인한 뒤 다시 실행하세요.</p>}
      {run.state === 'failed' && <p className="notice error">이 분석은 완료되지 않았습니다{object(run.error_detail).code ? ` (${text(object(run.error_detail).code)})` : ''}. AI 판정을 받지 못한 {failedItems}건은 확인 필요로 남았습니다. AI 연결을 확인한 뒤 다시 실행하세요.</p>}
      {(['power', 'area'] as const).map(part => object(context[part]).analyzed === false && <p className="notice warning" key={part}>{part === 'power' ? 'Power' : 'Area'}는 분석에서 뺐습니다 · {EXCLUSION_LABEL[text(object(context[part]).reason)] || text(object(context[part]).reason)}</p>)}
      {array(run.payload.missing_watched).length > 0 && <p className="notice warning">현재 실행에 없는 주요 instance · {array(run.payload.missing_watched).map(text).slice(0, 8).join(', ')}</p>}
      {!isActive && !jobFailed && items.length === 0 && <p className="muted">기준을 넘는 변화와 주요 instance가 없어 분석할 항목이 없습니다.</p>}
      {!isActive && !jobFailed && renderAttributionBox(run.payload.attribution)}
      {items.length > 0 && <>
        <div className="triage-lists">
          {section('이상', of('unexpected'))}
          {section('확인 필요', of('needs_review'))}
          {section('주요 instance (설명됨 · 변화 미미)', watchedRest)}
          {section('설명됨', explained, false)}
        </div>
        {!isActive && !jobFailed && <details className="triage-mail"><summary><Mail size={14} aria-hidden="true" />메일 보내기</summary>
          <p className="muted">이상 · 확인 필요 · 주요 instance 목록을 요약 메일 한 통으로 보냅니다. ‘제외’를 체크한 {excluded.size}건은 빠지고, 설명됨은 건수만 들어갑니다.</p>
          <div className="triage-mail-form"><label className="field"><span>수신자 (쉼표로 구분 · 비우면 기본 수신자)</span><input type="text" value={recipients} onChange={event => setRecipients(event.target.value)} placeholder="name@example.com, ..." autoComplete="off" spellCheck={false} /></label><Button size="sm" disabled={action.busy || !canWrite} onClick={() => action.run(sendMail, '메일 발송을 요청했습니다.')}><Mail size={14} />메일 보내기</Button></div>
          {result.mails.length > 0 && <ul className="triage-mail-history">{result.mails.map(mail => <li key={text(mail.id)}>{date(text(mail.created_at))} · {array(mail.recipients).map(text).join(', ') || '기본 수신자'} · 제외 {array(mail.excluded_ids).length}건 · {{ completed: '발송 접수', failed: `발송 실패${object(mail.job_error).code ? ` (${text(object(mail.job_error).code)})` : ''}`, pending: '대기 중', running: '발송 중' }[text(mail.job_state)] || text(mail.job_state) || '상태 미확인'}</li>)}</ul>}
        </details>}
      </>}
    </details>}
    {!run && !preview && !loadError && <p className="muted">메모와 주요 instance를 등록한 뒤 미리보기로 분석 대상을 확인하고 실행하세요. 결과는 화면에서 먼저 확인하고, ‘메일 보내기’를 눌러야 메일이 나갑니다.</p>}
  </section>
}
