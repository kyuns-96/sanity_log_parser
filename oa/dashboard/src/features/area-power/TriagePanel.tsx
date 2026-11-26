import { useEffect, useRef, useState } from 'react'
import { LoaderCircle, Mail, Search, Sparkles } from 'lucide-react'
import { array, date, format, formatPercent, object, request, text, triagePreviewSchema, triageResultSchema, triageRunsSchema, triageStartedSchema, unknownSchema, type Auth, type TriageItem, type TriagePreview, type TriageResult, type TriageRun } from './api'
import { AnalysisDelete } from './AnalysisDelete'
import { Button, useAction } from './components/workspace'
import { EffectiveFormulaHint } from './EffectiveFormulaHint'
import { deriveTriageRunMetrics, TriageLiveProgress } from './TriageLiveProgress'

export const VERDICT_LABEL: Record<string, string> = { unexpected: '이상', needs_review: '확인 필요', expected: '설명됨', minor: '변화 미미' }
const CAUSE_LABEL: Record<string, string> = { note: '메모', vth_shift: 'Vth 변화', drive_shift: 'Drive 변화', new_instance: '신규 instance', unknown: '원인 불명' }
const FLAG_LABEL: Record<string, string> = { area_conditions_unconfirmed: 'Area 조건 미확인', power_conditions_unconfirmed: 'Power 조건 미확인', library_unknown: 'library 미확인', library_different: 'library 다름 · Vth/Drive는 참고용', removed_in_current: '현재 실행에 없음' }
const EXCLUSION_LABEL: Record<string, string> = { area_only_no_power: 'Area 전용 자료(Power 미포함)', corner_different: '양쪽 corner가 다름', analysis_conditions_different: '분석 조건이 다름', area_unit_different: 'area 단위가 다름', baseline_missing: '기준 실행 없음' }
const NUMBER = /^(?:\d+(?:\.\d*)?|\.\d+)$/
const running = (state: string) => ['queued', 'pending', 'running'].includes(state)
const signed = (value: unknown, unit: string) => value == null || value === '' ? '—' : `${text(value).startsWith('-') ? '' : '+'}${formatPercent(value)}${unit}`

const RANKING_LABEL: Record<string, string> = {
  power_abs_top: 'Power 절대량 Top',
  power_damped_top: 'Power Effective Δ% Top',
  area_abs_top: 'Area 절대량 Top',
  area_damped_top: 'Area Effective Δ% Top',
}

function summary(card: Record<string, unknown>) {
  const area = object(card.area), power = object(card.power_mw)
  const areaPart = `Logic Area ${signed(area.delta_pct, '%')}${area.damped_pct != null ? ` (Effective ${signed(area.damped_pct, '%')})` : ''}`
  const parts = [areaPart]
  if (!('excluded' in power)) {
    const pwrDelta = power.delta != null && text(power.delta) !== '' ? `${text(power.delta).startsWith('-') ? '' : '+'}${format(power.delta)} mW · ` : ''
    const pwrDamped = power.damped_pct != null ? ` (Effective ${signed(power.damped_pct, '%')})` : ''
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

  const context = object(run?.payload.side_context)
  const items = result?.items || []
  const of = (verdict: string) => items.filter(item => item.verdict === verdict)
  const unexpectedList = of('unexpected')
  const needsReviewList = of('needs_review')
  const expectedAll = of('expected')
  const minorAll = of('minor')
  const watchedRest = items.filter(item => object(item.card).watched === true && ['expected', 'minor'].includes(item.verdict))
  const explained = items.filter(item => item.verdict === 'expected' && object(item.card).watched !== true)
  const failedItems = items.filter(item => item.failed).length
  const counts = preview?.counts
  const previewContext = object(preview?.context)

  const {
    expectedItems,
    processedItems,
    processedBatches,
    retryCalls,
    phaseText,
    subPhaseText,
    percent,
    batchesDisplay,
    modelDisplay,
  } = deriveTriageRunMetrics(run, job)

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
      {([['area_increase_pct', 'Logic Area 증가 ≥', '%'], ['power_increase_pct', 'Total Power 증가 ≥', '%'], ['noise_share_pct', '기본 노이즈 비율', '%'], ['min_area', 'Logic Area 최소', '']] as const).map(([key, label, unit]) => <label key={key} className="browser-threshold">{label}<input type="text" inputMode="decimal" value={thresholds[key]} aria-invalid={!NUMBER.test(thresholds[key].trim())} onChange={event => { setThresholds({ ...thresholds, [key]: event.target.value }); setPreview(null) }} />{unit}{key === 'noise_share_pct' && <EffectiveFormulaHint compact noiseSharePct={thresholds.noise_share_pct.trim() || '0.1'} thresholdPct={thresholds.power_increase_pct.trim() || '10'} />}</label>)}
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
      <p><strong>분석 대상 {counts.total?.toLocaleString() ?? 0}개</strong> · Area 증가 {counts.area_increase ?? 0} (절대Top {counts.area_abs_top ?? 0} · Effective Top {counts.area_damped_top ?? 0}) · Power 증가 {counts.power_increase ?? 0} (절대Top {counts.power_abs_top ?? 0} · Effective Top {counts.power_damped_top ?? 0}) · 신규 {counts.new_instance ?? 0} · 주요 {counts.watched ?? 0} · 상위로 묶임 {counts.rolled_up ?? 0} · 최초 예상 AI 호출 {preview.estimated_calls}회</p>
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
          : <><strong>이상 {unexpectedList.length}</strong> · 확인 필요 {needsReviewList.length} · 설명됨 {expectedAll.length} · 변화 미미 {minorAll.length} · 적용 메모 {run.note_count ?? 0}건</>}
      </summary>
      {isActive && (
        <TriageLiveProgress
          run={run}
          job={job}
          queue={queue}
          worker={worker}
          serverOffset={serverOffset}
          verdictSummary={{
            total: items.length,
            unexpected: unexpectedList.length,
            needsReview: needsReviewList.length,
            expected: expectedAll.length,
          }}
          onRefresh={() => setTick(current => current + 1)}
        />
      )}
      {!isActive && run.payload.retry_limit != null && <p className="muted">자동 재시도 · 묶음당 최대 {format(run.payload.retry_limit)}회 · 전체 누적 {format(retryCalls)}회{modelDisplay ? ` · 모델 ${modelDisplay}` : ''}</p>}
      {jobFailed && <p className="notice error" role="alert">중앙 VWP 작업 실행이 실패했습니다{object(job?.error_detail).code ? ` (${text(object(job?.error_detail).code)})` : ''}. VWP 작업자 로그와 AI 연결을 확인한 뒤 다시 실행하세요.</p>}
      {run.state === 'failed' && <p className="notice error">이 분석은 완료되지 않았습니다{object(run.error_detail).code ? ` (${text(object(run.error_detail).code)})` : ''}. AI 판정을 받지 못한 {failedItems}건은 확인 필요로 남았습니다. AI 연결을 확인한 뒤 다시 실행하세요.</p>}
      {(['power', 'area'] as const).map(part => object(context[part]).analyzed === false && <p className="notice warning" key={part}>{part === 'power' ? 'Power' : 'Area'}는 분석에서 뺐습니다 · {EXCLUSION_LABEL[text(object(context[part]).reason)] || text(object(context[part]).reason)}</p>)}
      {array(run.payload.missing_watched).length > 0 && <p className="notice warning">현재 실행에 없는 주요 instance · {array(run.payload.missing_watched).map(text).slice(0, 8).join(', ')}</p>}
      {!isActive && !jobFailed && items.length === 0 && <p className="muted">기준을 넘는 변화와 주요 instance가 없어 분석할 항목이 없습니다.</p>}
      {!isActive && !jobFailed && renderAttributionBox(run.payload.attribution)}
      {items.length > 0 && <>
        <div className="triage-lists">
          {section('이상', unexpectedList)}
          {section('확인 필요', needsReviewList)}
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
