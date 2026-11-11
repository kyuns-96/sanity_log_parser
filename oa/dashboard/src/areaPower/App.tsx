import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, ArrowRight, Bell, Boxes, ChevronRight, Cpu, Database, FileClock, Gauge, GitCompareArrows, LoaderCircle, Orbit, RefreshCw, ShieldCheck, UploadCloud, Workflow } from 'lucide-react'
import { accessSchema, allRecords, allUploads, dashboardSchema, date, format, object, query, request, text, uploadName, type AreaAccess, type Auth, type Dashboard, type Filters, type TrackingRecord, type Upload } from './api'
import { Badge, Button, Empty, Field, Json, RecordOptions } from './components/workspace'
import { Configurations } from './ConfigViews'
import { Uploads } from './UploadViews'
import { Comparisons } from './ComparisonViews'
import { Alerts, Jobs } from './OperationsViews'
import { useDashboardAuth } from '../useDashboardAuth'
import { useDashboardNavigation } from '../dashboardMode'
import { useAtlas } from '../atlasContext'
import AtlasPrepare from '../AtlasPrepare'

const navigation = [
  { id: 'overview', title: '구성·전력 개요', subtitle: '실제 실행의 루트 지표' },
  { id: 'uploads', title: '업로드 · 실행', subtitle: '파일과 명시적 실행 선택' },
  { id: 'comparisons', title: '수치 · AI 분석', subtitle: '정확한 비교와 변경 근거' },
  { id: 'targets', title: '추적 대상 · 매핑', subtitle: '추적 대상과 구조 대응' },
  { id: 'changes', title: '설계 변경', subtitle: '불변 변경 컨텍스트' },
  { id: 'alerts', title: '알림', subtitle: '검토와 대응' },
  { id: 'jobs', title: '수치 작업', subtitle: '실제 작업 상태' },
]
interface WorkspaceData { dashboard: Dashboard; records: TrackingRecord[]; uploads: Upload[]; comparisons: TrackingRecord[]; jobs: TrackingRecord[]; alerts: TrackingRecord[]; access: AreaAccess; scopeKey: string }
export default function AreaPower() {
  const host = useDashboardAuth(true)
  const atlas = useAtlas()!
  const navigateDashboard = useDashboardNavigation()
  const auth = useMemo<Auth>(() => ({ actor: host.session?.reviewer || '', request: host.request, demo: atlas.demo }), [host.session?.reviewer, host.request, atlas.demo])
  const params = new URLSearchParams(atlas.routeSearch)
  const view = params.get('view') || 'overview'
  const area = atlas.areaScope
  const scopeKey = JSON.stringify([area, params.get('snapshot') || ''])
  const filters = useMemo<Filters>(() => ({ project_id: area?.area_project_id || '', block_id: area?.area_block_id || '', version_id: area?.area_version_id || '', snapshot_id: new URLSearchParams(atlas.routeSearch).get('snapshot') || '' }), [area, atlas.routeSearch])
  const [data, setData] = useState<WorkspaceData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [updated, setUpdated] = useState<string | null>(null)
  const [poll, setPoll] = useState(false)
  const controller = useRef<AbortController | null>(null)
  const refresh = useCallback(async () => {
    controller.current?.abort()
    if (!area || !atlas.catalog?.area_enabled) { setData(null); setLoading(false); return }
    const current = new AbortController(); controller.current = current
    setLoading(true)
    try {
      const [access, dashboard, uploads, groups, comparisons, jobs, alerts] = await Promise.all([
        request(auth, '/access', accessSchema, undefined, current.signal),
        request(auth, `/dashboard${query({ ...filters })}`, dashboardSchema, undefined, current.signal),
        allUploads(auth, current.signal),
        Promise.all(['project', 'version', 'block', 'snapshot', 'target', 'mapping', 'change', 'selection'].map(kind => allRecords(auth, kind, {}, current.signal))),
        allRecords(auth, 'comparison', { ...filters }, current.signal),
        allRecords(auth, 'job', { ...filters }, current.signal),
        allRecords(auth, 'alert', { ...filters }, current.signal),
      ])
      if (!current.signal.aborted) { setData({ access, dashboard, uploads, records: groups.flat(), comparisons, jobs, alerts, scopeKey }); setError(''); setUpdated(new Date().toISOString()) }
    } catch (cause) {
      if (!current.signal.aborted) { setData(null); setError(cause instanceof Error ? cause.message : '수치 분석 서비스 연결을 확인하세요.'); throw cause }
    } finally { if (!current.signal.aborted) setLoading(false) }
  }, [auth, filters, area, atlas.catalog?.area_enabled, scopeKey])
  useEffect(() => { void refresh().catch(() => undefined); return () => controller.current?.abort() }, [refresh])
  useEffect(() => {
    if (!poll || !area) return
    let disposed = false
    let timer: number | undefined
    const next = () => { timer = window.setTimeout(async () => { if (!loading) { try { await refresh() } catch { /* refresh exposes the connection error. */ } } if (!disposed) next() }, 15000) }
    next(); return () => { disposed = true; window.clearTimeout(timer) }
  }, [poll, refresh, loading, area])
  const current = navigation.find(item => item.id === view) || navigation[0]
  const visibleData = data?.scopeKey === scopeKey ? data : null
  const records = visibleData?.records || []
  const navigate = (targetView: string) => { void navigateDashboard(atlas.href(`/?page=area-power&view=${encodeURIComponent(targetView)}`), host.session?.user_id).catch(cause => setError(String(cause))) }
  const snapshot = (id: string) => { const url = new URL(location.href); if (id) url.searchParams.set('snapshot', id); else url.searchParams.delete('snapshot'); void navigateDashboard(url.pathname + url.search, host.session?.user_id).catch(cause => setError(String(cause))) }
  return <div className="area-power"><main id="area-power-main" tabIndex={-1}>
    <div className="page-heading"><div><span className="eyebrow">DESIGN ATLAS / NUMERICAL EVIDENCE</span><h1>{current.title}<span className="heading-dot">.</span></h1><p>{current.subtitle} · 공통 범위의 {atlas.demo ? '합성 예시' : '실제 데이터'}</p></div><div className="page-controls"><small>{updated ? `마지막 조회 ${date(updated)}` : atlas.demo ? '합성 자료 조회 전' : '실제 데이터 조회 전'}</small><div className="actions"><label className="checkbox"><input type="checkbox" checked={poll} onChange={event => setPoll(event.target.checked)} />15초 자동 갱신</label><Button variant="outline" disabled={loading || !area} onClick={() => void refresh().catch(() => undefined)}><RefreshCw size={15} className={loading ? 'spin' : ''} />새로고침</Button></div></div></div>
    {!atlas.catalog?.area_enabled ? <Empty title="수치 분석 서비스가 준비되지 않았습니다">{atlas.catalog?.area_error || '운영 설정을 확인하세요. 데모로 대체하지 않습니다.'}</Empty> : !atlas.project ? <Empty title="공통 프로젝트를 선택하세요">상단 공통 범위가 조회·분석·업로드 메타데이터에 실제로 적용됩니다.</Empty> : !area ? <section className="panel"><h2>선택한 범위의 수치 분석 연결이 필요합니다</h2><p>공통 프로젝트·블록·버전을 내부 분석 ID에 명시적으로 연결합니다. 데이터나 비교 기준을 자동 생성하지 않습니다.</p><AtlasPrepare /></section> : <>
      <Field label="Snapshot (수치 구조 세부 범위)"><select value={filters.snapshot_id} onChange={event => snapshot(event.target.value)}><option value="">선택 범위의 모든 Snapshot</option><RecordOptions records={records.filter(record => record.record_type === 'snapshot' && Object.entries(filters).every(([key, value]) => key === 'snapshot_id' || !value || record[key] === value))} /></select></Field>
      {loading && !visibleData && <div className="loading-state" role="status"><LoaderCircle className="spin" size={23} />선택 범위의 {atlas.demo ? '합성 메모리 자료' : '실제 DB 기록'}를 불러오는 중입니다…</div>}
      {visibleData && <>{visibleData.access.local_mode && <div className="notice info">실제 로컬 모드 · 공통 카탈로그의 명시적 연결을 사용합니다. 데모 데이터가 아닙니다.</div>}
        {current.id === 'overview' && <Overview data={visibleData} filters={filters} auth={auth} refresh={refresh} navigate={navigate} />}
        {current.id === 'uploads' && <Uploads key={scopeKey} auth={auth} access={visibleData.access} uploads={visibleData.uploads} records={records} filters={filters} refresh={refresh} />}
        {current.id === 'comparisons' && <Comparisons key={scopeKey} auth={auth} uploads={visibleData.uploads} records={records} comparisons={visibleData.comparisons} filters={filters} refresh={refresh} />}
        {current.id === 'targets' && <Configurations key={`targets:${scopeKey}`} mode="targets" auth={auth} records={records} filters={filters} refresh={refresh} />}
        {current.id === 'changes' && <Configurations key={`changes:${scopeKey}`} mode="changes" auth={auth} records={records} filters={filters} refresh={refresh} />}
        {current.id === 'alerts' && <Alerts auth={auth} records={visibleData.alerts} refresh={refresh} />}
        {current.id === 'jobs' && <Jobs auth={auth} records={visibleData.jobs} refresh={refresh} />}
      </>}
    </>}
    {error && <div className="notice error" role="alert"><strong>요청을 완료하지 못했습니다</strong><p>{error}</p></div>}
    <footer className="page-footer"><span><Orbit size={13} /> Design Atlas · 구성·전력 분석</span><span>Exact values. Explicit context. Traceable decisions.</span></footer>
  </main></div>
}
function Overview({ data, auth, filters, refresh, navigate }: { data: WorkspaceData; auth: Auth; filters: Filters; refresh: () => Promise<void>; navigate: (view: string) => void }) {
  const [rootUploadId, setRootUploadId] = useState('')
  const rootOptions = data.uploads.filter(upload => upload.state === 'ready' && Object.entries(filters).every(([key, value]) => !value || upload[key] === value))
  const selectedRootUpload = rootOptions.find(upload => upload.id === rootUploadId)
  const root = selectedRootUpload ? (Object.keys(object(selectedRootUpload.root_summary)).length ? object(selectedRootUpload.root_summary) : null) : data.dashboard.root_metrics
  const metrics = root && Object.keys(object(root.metrics)).length ? object(root.metrics) : object(root)
  const cards = [
    { title: 'Total area', key: 'total_area', unit: 'area 단위는 메타데이터 기준', icon: Boxes, color: 'blue' },
    { title: 'Logic cells', key: 'logic_cells', unit: 'cells · logic 분모', icon: Cpu, color: 'indigo' },
    { title: 'Total power', key: 'pwr_all_total_mw', unit: 'mW · 선택된 단일 실행', icon: Activity, color: 'cyan' },
    { title: 'CTS area ratio', key: 'cts_area_pct', unit: '% · logic area 기준', icon: Gauge, color: 'violet' },
  ]
  const active = data.alerts.filter(record => !record.resolved_at && record.state !== 'resolved')
  return <><Field label="루트 summary 표시 실행" hint="조회할 실제 실행을 명시적으로 선택합니다. shared 선택 변경은 업로드에서 수행하세요."><select value={selectedRootUpload?.id || ''} onChange={event => setRootUploadId(event.target.value)}><option value="">API의 단일 shared 선택 기준 (없거나 여러 개면 미표시)</option>{rootOptions.map(upload => <option key={upload.id} value={upload.id}>{uploadName(upload)}</option>)}</select></Field><div className="overview-banner"><div><span className="eyebrow">EVIDENCE-FIRST ENGINEERING</span><h2>작은 변화도 놓치지 않는 관측.</h2><p>구조와 실행 조건을 분리하고, 수치와 설명의 근거를 함께 추적합니다.</p><div className="banner-tags"><span><ShieldCheck size={13} />정확한 수치</span><span><GitCompareArrows size={13} />고정된 비교 입력</span><span><FileClock size={13} />불변 revision 이력</span></div></div><div className="banner-symbol" aria-hidden="true"><Orbit size={99} strokeWidth={1} /></div></div><div className="stats-grid">{cards.map(card => <article className="metric-card" key={card.key}><div className="card-line"><span>{card.title}</span><span className={`metric-icon ${card.color}`}><card.icon size={19} /></span></div><strong>{format(metrics[card.key])}</strong><small>{card.unit}</small><div className="metric-source"><i />{root ? 'DB root summary' : '명시적으로 선택된 루트 데이터 없음'}</div></article>)}</div>{!root && <div className="notice info"><Database size={18} /><p>루트 지표를 표시할 실행이 아직 선택되지 않았습니다. 업로드에서 snapshot · condition별 실행을 명시적으로 선택하세요. 여러 hierarchy나 corner를 합산하지 않습니다.</p><Button variant="ghost" size="sm" onClick={() => navigate('uploads')}>실행 선택<ArrowRight size={14} /></Button></div>}{root && <Json value={root} label="루트 summary 전체 · 원본 단위 · 입력 품질 확인" />}<div className="overview-counts">{[{ key: 'uploads', label: '등록된 파일', view: 'uploads', icon: UploadCloud }, { key: 'comparisons', label: '비교 분석', view: 'comparisons', icon: GitCompareArrows }, { key: 'open_alerts', label: '검토할 알림', view: 'alerts', icon: Bell }, { key: 'pending_jobs', label: '대기 작업', view: 'jobs', icon: Workflow }].map(item => <button key={item.key} onClick={() => navigate(item.view)}><item.icon size={18} /><span>{item.label}</span><strong>{format(data.dashboard.counts[item.key])}</strong><ChevronRight size={15} /></button>)}</div><div className="two-columns overview-panels"><section className="panel"><div className="section-heading"><div><span className="eyebrow">ANALYSIS ACTIVITY</span><h2>비교 진행 현황</h2></div><Button variant="ghost" size="sm" onClick={() => navigate('comparisons')}>전체 보기<ArrowRight size={14} /></Button></div>{data.comparisons.length ? <div className="compact-list">{data.comparisons.map(record => <button key={record.id} onClick={() => navigate('comparisons')}><span className="list-icon"><GitCompareArrows size={17} /></span><span><strong>{text(object(record.payload.request).comparison_kind) || '비교 분석'}</strong><small>{record.id.slice(0, 8)} · {date(record.created_at)}</small></span><Badge value={record.state} /></button>)}</div> : <Empty title="아직 비교가 없습니다">현재와 기준 파일을 선택해 첫 비교를 요청하세요.</Empty>}</section><section className="panel"><div className="section-heading"><div><span className="eyebrow">NEEDS ATTENTION</span><h2>검토가 필요한 알림</h2></div><Button variant="ghost" size="sm" onClick={() => navigate('alerts')}>전체 보기<ArrowRight size={14} /></Button></div>{active.length ? <div className="compact-list">{active.map(record => <button key={record.id} onClick={() => navigate('alerts')}><span className="list-icon alert"><Bell size={17} /></span><span><strong>{text(record.payload.summary) || text(record.payload.metric)}</strong><small>{text(record.payload.reason) || '근거 확인 필요'}</small></span><Badge value={record.payload.kind} /></button>)}</div> : <Empty title="미해소 알림이 없습니다">비교 coverage와 정책 설정 여부도 함께 확인하세요.</Empty>}</section></div></>
}
