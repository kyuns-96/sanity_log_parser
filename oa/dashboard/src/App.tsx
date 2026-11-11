import { appUrl } from './appUrl'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Activity, ArrowDownToLine, ArrowUpRight, Box, Check, CheckCheck, ChevronLeft, ChevronRight, CircleHelp, ClipboardCheck, Clock3, FileCheck2, FileSearch, Files, Fingerprint, Inbox, Layers3, LoaderCircle, LockKeyhole, LogOut, Menu, MessageSquareMore, PanelLeftClose, PanelLeftOpen, RefreshCw, Search, ShieldCheck, Sparkles, X } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Badge } from './studio/components/ui/badge'
import { Input } from './components/ui/input'
import { Textarea } from './components/ui/textarea'
import { Progress } from './components/ui/progress'
import { Separator } from './components/ui/separator'
import { Tabs, TabsList, TabsTrigger, TabsContent } from './studio/components/ui/tabs'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './studio/components/ui/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './components/ui/dialog'
import { makeDemo } from './demo'
import { makeGcaDemo } from './gcaDemo'
import { GcaDemoScope, GcaReportSource } from './GcaDemo'
import { ClusterPanel } from './ClusterPanel'
import { RawEvidence } from './RawEvidence'
import { WorkspaceBrand } from './components/WorkspaceBrand'
import { ModuleNavigation } from './components/ModuleNavigation'
import { ColorModeControl } from './components/ColorModeControl'
import { SidebarModeSwitch } from './components/SidebarModeSwitch'
import { SidebarResizer, useSidebarLayout } from './components/SidebarResizer'
import { modeUrl, useDashboardNavigation, useDashboardSidebarState } from './dashboardMode'
import { useWorkspaceNavigation } from './useWorkspaceNavigation'
import { useDashboardAuth } from './useDashboardAuth'
import { useAtlas } from './atlasContext'
import type { DashboardAuthSnapshot } from './useDashboardAuth'
import { DashboardLogin } from './DashboardLogin'
import AccessWelcome from './AccessWelcome'
import AccessAdmin from './AccessAdmin'
import PersonalAiToken from './PersonalAiToken'
import type { AccessPage } from './accessApi'
import type { Bundle, Detail, Evidence, EvidenceRequest, Group, Page, Session, Unit } from './types'

type Api = <T>(path: string, body?: Record<string, unknown>, text?: boolean) => Promise<T>
type CommandForm = { kind: 'review' | 'evidence'; unit: Unit; revision: number; requestId: string }
const number = (v: number) => v.toLocaleString('ko-KR')
const date = (value: string | undefined) => value ? new Date(value).toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '—'
const short = (value: string) => value.length > 28 ? value.slice(0, 25) + '…' : value
const textStatus: Record<string, string> = { PASS: 'PASS', FAIL: 'FAIL', UNKNOWN: '판정 보류', NOT_APPLICABLE: '해당 없음', COMPLETE: '완료', COMPLETED: '완료', INCOMPLETE: '미완료', MISSING: '누락', PARTIAL: '일부 해석', UNSUPPORTED: '미지원', NOT_RUN: '미실행', PENDING: '응답 대기', REQUESTED: '응답 대기', RECEIVED: '수신 완료', EXPIRED: '만료', NOT_CONFIGURED: '미설정' }
function Status({ value }: { value: string }) { return <Badge variant="outline" className={`status status-${value.toLowerCase()}`}>{value === 'PASS' && <Check size={11} />}{textStatus[value] || value}</Badge> }
function Empty({ children }: { children: React.ReactNode }) { return <div className="empty"><Files size={26} /><p>{children}</p></div> }
function Pagination({ offset, total, limit, change }: { offset: number; total: number; limit: number; change: (offset: number) => void }) { return <div className="pagination"><span>{total ? `${number(offset + 1)}–${number(Math.min(offset + limit, total))}` : '0'} / {number(total)}</span><Button aria-label="이전 페이지" size="icon" variant="outline" disabled={offset === 0} onClick={() => change(Math.max(0, offset - limit))}><ChevronLeft /></Button><Button aria-label="다음 페이지" size="icon" variant="outline" disabled={offset + limit >= total} onClick={() => change(offset + limit)}><ChevronRight /></Button></div> }

export default function App({ routePage, initialAuth }: { routePage?: AccessPage; initialAuth?: DashboardAuthSnapshot } = {}) {
  const navigateDashboard = useDashboardNavigation()
  const atlas = useAtlas()
  const [legacyReview, setLegacyReview] = useState(false)
  const [demo, setDemo] = useState(() => !atlas && new URLSearchParams(location.search).get('demo') === '1')
  const [demoDataset] = useState(() => new URLSearchParams(location.search).get('dataset'))
  const gcaDemo = demo && demoDataset === 'gca'
  const demoApi = useRef<ReturnType<typeof makeDemo> | null>(null)
  const auth = useDashboardAuth(!demo, initialAuth)
  const [demoSession, setDemoSession] = useState<Session | null>(null)
  const session = demo ? demoSession : auth.session
  const [localPage, setPage] = useState<AccessPage>(() => {
    const value = new URLSearchParams(location.search).get('page')
    return value === 'welcome' || value === 'admin' ? value : 'review'
  })
  const page = routePage ?? localPage
  const readySSO = session?.auth_method === 'sso' && session.authorization_ready === true
  const canRead = !!session && (session.auth_method !== 'sso' || readySSO && session.can_read_business === true)
  const businessActive = canRead && (page === 'review' || !atlas && !readySSO) && (!atlas || legacyReview)
  const [commandError, setError] = useState('')
  const error = commandError || auth.error
  const [notice, setNotice] = useState('')
  const [commandBusy, setBusy] = useState(false)
  const busy = commandBusy || auth.signingOut
  const [mobile, setMobile] = useState(false)
  const [collapsed, setCollapsed] = useDashboardSidebarState()
  const sidebarLayout = useSidebarLayout(!atlas && !collapsed)
  const closeMobile = useCallback(() => setMobile(false), [])
  const { isMobile, sidebarRef, triggerRef } = useWorkspaceNavigation(mobile, closeMobile, !atlas && !!session)
  const [bundles, setBundles] = useState<Page<Bundle>>({ items: [], total: 0, offset: 0, limit: 20 })
  const [bundleOffset, setBundleOffset] = useState(0)
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState<Detail | null>(null)
  const [unitOffset, setUnitOffset] = useState(0)
  const [unitId, setUnitId] = useState('')
  const [groups, setGroups] = useState<Page<Group>>({ items: [], total: 0, offset: 0, limit: 20 })
  const [groupOffset, setGroupOffset] = useState(0)
  const [requests, setRequests] = useState<Page<EvidenceRequest>>({ items: [], total: 0, offset: 0, limit: 20 })
  const [requestOffset, setRequestOffset] = useState(0)
  const [tick, setTick] = useState(0)
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState('ledger')
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('all')
  const [form, setForm] = useState<CommandForm | null>(null)
  const [reason, setReason] = useState('')
  const [conclusion, setConclusion] = useState<'PASS' | 'FAIL'>('FAIL')
  const [recordIds, setRecordIds] = useState('')
  const [evidence, setEvidence] = useState<(Evidence & { requestId: string }) | null>(null)
  const api: Api = useCallback(async <T,>(path: string, body?: Record<string, unknown>, plain = false): Promise<T> => {
    if (demo) {
      demoApi.current ??= demoDataset === 'gca' ? makeGcaDemo() : makeDemo()
      return await demoApi.current(path, body) as T
    }
    return auth.request<T>(path, body, plain)
  }, [demo, auth.request, demoDataset])
  const fail = (e: unknown) => { if (!(e instanceof DOMException && e.name === 'AbortError')) setError(e instanceof Error ? e.message : '요청에 실패했습니다. 연결 상태를 확인해 주세요.') }
  const refresh = () => { setError(''); setTick(v => v + 1) }
  function navigate(next: AccessPage) {
    const url = new URL(location.href)
    url.searchParams.set('page', next)
    if (routePage) {
      closeMobile()
      void navigateDashboard(url.pathname + url.search, session?.user_id).catch(fail)
      return
    }
    history.pushState(null, '', url)
    setPage(next); setMobile(false); setError('')
  }
  useEffect(() => {
    if (routePage) return
    const changed = () => {
      const value = new URLSearchParams(location.search).get('page')
      setPage(value === 'welcome' || value === 'admin' ? value : 'review')
    }
    window.addEventListener('popstate', changed)
    return () => window.removeEventListener('popstate', changed)
  }, [routePage])
  useEffect(() => {
    if (!auth.businessDenied || !readySSO) return
    navigate('welcome')
    setError('현재 담당 범위를 확인해 주세요. 접근할 수 없는 검사 결과는 화면에서 닫았습니다.')
    void auth.refreshSession().catch(fail)
  }, [auth.businessDenied])
  function selectBundle(id: string) {
    if (id === selected) return
    setSelected(id); setDetail(null); setUnitId(''); setUnitOffset(0); setGroupOffset(0); setRequestOffset(0)
    setSearch(''); setFilter('all'); setError(''); setNotice(''); setForm(null); setEvidence(null)
    setRequests({ items: [], total: 0, offset: 0, limit: 20 }); setTab('ledger')
  }
  useEffect(() => {
    if (!demo) return
    let active = true
    api<Session>('/api/session').then(value => { if (active) { setDemoSession(value); setError('') } }).catch(e => { if (active) { fail(e); setDemoSession(null) } })
    return () => { active = false }
  }, [api, demo])
  useEffect(() => {
    if (!session || !businessActive) return
    let active = true
    api<Page<Bundle>>(`/api/bundles?offset=${bundleOffset}&limit=20`).then(value => { if (active) { setBundles(value); setSelected(old => old || value.items[0]?.bundle_id || '') } }).catch(e => { if (active) fail(e) })
    return () => { active = false }
  }, [api, session, businessActive, bundleOffset, tick])
  useEffect(() => {
    if (!session || !businessActive || !selected) return
    let active = true; setLoading(true)
    api<Detail>(`/api/bundles/${encodeURIComponent(selected)}?unit_offset=${unitOffset}&unit_limit=50`).then(value => { if (active) { setDetail(value); setUnitId(old => value.units.some(u => u.review_unit_id === old) ? old : value.units[0]?.review_unit_id || '') } }).catch(e => { if (active) fail(e) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [api, session, businessActive, selected, unitOffset, tick])
  useEffect(() => {
    if (!session || !businessActive || !selected || !unitId) return
    let active = true; setGroups({ items: [], total: 0, offset: groupOffset, limit: 20 })
    api<Page<Group>>(`/api/bundles/${encodeURIComponent(selected)}/units/${encodeURIComponent(unitId)}/groups?offset=${groupOffset}&limit=20`).then(value => { if (active) setGroups(value) }).catch(e => { if (active) fail(e) })
    return () => { active = false }
  }, [api, session, businessActive, selected, unitId, groupOffset, tick])
  useEffect(() => {
    if (!session || !businessActive || !selected) return
    let active = true
    setRequests({ items: [], total: 0, offset: requestOffset, limit: 20 })
    api<Page<EvidenceRequest>>(`/api/bundles/${encodeURIComponent(selected)}/requests?offset=${requestOffset}&limit=20`).then(value => { if (active) setRequests(value) }).catch(e => { if (active) fail(e) })
    return () => { active = false }
  }, [api, session, businessActive, selected, requestOffset, tick])

  useEffect(() => {
    if (session && businessActive) return
    setSelected(''); setDetail(null); setUnitId(''); setForm(null); setEvidence(null)
    setBundles({ items: [], total: 0, offset: 0, limit: 20 }); setBundleOffset(0); setUnitOffset(0)
    setGroups({ items: [], total: 0, offset: 0, limit: 20 }); setGroupOffset(0)
    setRequests({ items: [], total: 0, offset: 0, limit: 20 }); setRequestOffset(0)
    setMobile(false); setLoading(false)
  }, [session, businessActive])

  async function logout() {
    setError('')
    if (!demo && !await auth.logout()) return
    setDemoSession(null); setDemo(false); demoApi.current = null
    setSelected(''); setDetail(null); setUnitId(''); setError(''); setNotice(''); setForm(null); setEvidence(null)
    setBundles({ items: [], total: 0, offset: 0, limit: 20 }); setBundleOffset(0); setUnitOffset(0)
    const url = new URL(location.href); url.searchParams.delete('demo'); history.replaceState(null, '', url)
    if (routePage) await navigateDashboard('/?page=welcome', undefined, true)
  }
  async function command(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await action() } catch (e) { fail(e) } finally { setBusy(false) }
  }
  function openForm(kind: 'review' | 'evidence', unit: Unit) {
    setError(''); setReason('')
    if (unit.check_status === 'PASS') setConclusion(unit.check_status)
    else setConclusion('FAIL')
    setRecordIds(groups.items.filter(g => g.review_unit_id === unit.review_unit_id).flatMap(g => g.examples.map(e => e.record_id)).join('\n'))
    setForm({ kind, unit, revision: detail!.review_revision, requestId: crypto.randomUUID() })
  }
  const edit = () => setForm(old => old ? { ...old, requestId: crypto.randomUUID() } : old)
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!form || !reason.trim()) return
    await command(async () => {
      if (form.kind === 'review') {
        await api(`/api/bundles/${encodeURIComponent(selected)}/reviews`, { unit_id: form.unit.review_unit_id, conclusion, reason: reason.trim(), request_id: form.requestId, expected_revision: form.revision })
        setNotice('검토 의견이 저장되었습니다. 최신 개정에 반영했습니다.')
      } else {
        const ids = [...new Set(recordIds.split(/[\s,]+/).filter(Boolean))]
        if (!ids.length) throw new Error('요청할 기록 ID를 입력해 주세요.')
        await api(`/api/bundles/${encodeURIComponent(selected)}/evidence`, { unit_id: form.unit.review_unit_id, record_ids: ids, reason: reason.trim(), request_id: form.requestId, ttl_seconds: 3600 })
        setNotice('추가 근거 요청이 등록되었습니다. VWP 응답을 받은 후 수신해 주세요.'); setTab('evidence')
      }
      setForm(null); setTick(v => v + 1)
    })
  }
  const download = () => command(async () => {
    const report = await api<string>(`/api/bundles/${encodeURIComponent(selected)}/report`, undefined, true)
    const url = URL.createObjectURL(new Blob([report], { type: 'text/markdown;charset=utf-8' }))
    const link = document.createElement('a'); link.href = url; link.download = `sanity-${selected}.md`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
    setNotice('검토 보고서를 다운로드했습니다.')
  })

  if (!session || session.auth_method === 'sso' && !readySSO) return atlas ? <section className="empty"><h1>Design Atlas · 로그 검토</h1><p role="alert">{error || '공통 로그인에서 계정과 프로젝트 접근 권한을 확인해 주세요.'}</p><Button variant="outline" onClick={() => auth.retry()} disabled={auth.checking}>연결 다시 확인</Button></section> : <DashboardLogin config={auth.config} session={session} checking={auth.checking} signingOut={auth.signingOut} error={error} accountProblem={auth.accountProblem} onToken={value => { setError(''); auth.loginWithToken(value) }} onRetry={() => { setError(''); auth.retry() }} onLogout={logout} />
  if (atlas && session.auth_method === 'local_token' && page !== 'review') return <main className="workspace" id="workspace-main" tabIndex={-1}>
    <div className="page-heading"><div><h1>{page === 'admin' ? '관리자 권한 · SSO 필요' : '로컬 연결 계정'}</h1><p>현재 연결은 실제 로컬 토큰 모드이며 회사 SSO 계정이나 프로젝트 멤버십을 대신하지 않습니다.</p></div></div>
    <section className="detail-panel"><div className="unit-detail"><h2>{session.reviewer}</h2><dl><dt>인증 방식</dt><dd><code>{session.auth_method}</code></dd><dt>접근 모드</dt><dd><code>{session.access_mode}</code> · {session.writable ? '쓰기 가능' : '읽기 전용'}</dd></dl>{session.notice && <p className="session-notice">{session.notice}</p>}<p>SSO 프로젝트 참여·권한 신청, 관리자 업무와 운영 로그 분류·판정은 로컬 토큰에서 지원하지 않습니다. 공통 카탈로그와 허용된 실제 로컬 데이터는 로그 조회 화면에서 확인할 수 있습니다.</p><p>토큰은 현재 탭 메모리에만 유지됩니다. 연결 종료는 공통 메뉴의 로그아웃을 사용해 주세요.</p><Button variant="outline" onClick={() => navigate('review')}><FileSearch size={16} />로그 조회</Button></div></section>
    {error && <p className="error-banner" role="alert">{error}</p>}
  </main>
  if (readySSO && (page !== 'review' || !canRead)) {
    const props = { session, api, onNavigate: navigate, onLogout: logout, onRefreshSession: auth.refreshSession, signingOut: auth.signingOut, notice: error }
    return page === 'admin' && session.is_admin === true ? <AccessAdmin {...props} /> : <AccessWelcome {...props} />
  }
  if (atlas && page === 'review' && !legacyReview) return <AtlasReports onLegacy={() => setLegacyReview(true)} canWorkbench={(readySSO || atlas.demo) && canRead} />

  const c = detail?.coverage
  const progress = c && c.expected ? Math.round((c.reviewed + c.approved_na) / c.expected * 100) : 0
  const selectedUnit = detail?.units.find(u => u.review_unit_id === unitId)
  const selectedBundle = bundles.items.find(b => b.bundle_id === selected)
  const visibleBundles = gcaDemo && selectedBundle ? bundles.items.filter(b => b.project === selectedBundle.project && b.design === selectedBundle.design) : bundles.items
  const visibleUnits = detail?.units.filter(u => (filter === 'all' || (filter === 'pending' ? !u.decision && u.applicable : u.check_status === filter)) && `${u.item_id} ${u.review_unit_id}`.toLowerCase().includes(search.toLowerCase())) || []
  const canReview = readySSO ? detail?.permissions?.can_review === true : session.writable
  const canCluster = readySSO ? detail?.permissions?.can_cluster === true : session.writable
  const canRequestEvidence = readySSO ? detail?.permissions?.can_request_evidence === true : session.writable
  const canReceiveEvidence = readySSO ? detail?.permissions?.can_receive_evidence === true : session.writable
  const canReceive = readySSO ? session.can_receive === true : session.writable
  return <div style={atlas ? { minHeight: 0 } : sidebarLayout.style} className={`shell studio-system studio-portal ${atlas ? 'atlas-native-review' : `sidebar-layout ${sidebarLayout.resizing ? 'sidebar-resizing' : ''} ${collapsed && !isMobile ? 'sidebar-is-collapsed' : ''}`}`}><a className="skip-link" href="#workspace-main">본문으로 건너뛰기</a>
    {!atlas && mobile && <button className="sidebar-backdrop" aria-label="메뉴 닫기" onClick={closeMobile} tabIndex={-1} />}
    {!atlas && <aside id="workspace-menu" ref={sidebarRef} className={`sidebar ${mobile ? 'sidebar-open' : ''}`} aria-label="워크스페이스 메뉴" role={isMobile && mobile ? 'dialog' : undefined} aria-modal={isMobile && mobile ? true : undefined} inert={isMobile && !mobile} aria-hidden={isMobile && !mobile}>
      <WorkspaceBrand />
      <button className="sidebar-close" aria-label="메뉴 닫기" onClick={closeMobile}><X size={18} /></button>
      {readySSO && session.is_admin === true && <SidebarModeSwitch mode="user" onChange={mode => { closeMobile(); return navigateDashboard(modeUrl(mode, session.can_read_business === true), session.user_id) }} />}
      <ModuleNavigation production={!demo} />
      <div className="portal-sidebar-context"><span className="workspace-icon"><Layers3 size={18} /></span><div><strong>Design Atlas</strong><small>{demo ? '합성 데이터 · 데모' : '결과 · 근거 · 변경 이력'}</small></div></div>
      <div className="nav-label">워크스페이스</div>
      <nav aria-label="검토 메뉴">
        {readySSO && <Button variant="ghost" aria-label="AI 분류·판정" onClick={() => { closeMobile(); void navigateDashboard('/?page=workbench', session.user_id).catch(fail) }}><Layers3 size={18} /><span className="nav-text">AI 분류·판정</span></Button>}
        {[{ id: 'ledger', icon: ClipboardCheck, title: '검토 대장' }, { id: 'evidence', icon: FileSearch, title: '추가 근거 요청' }, { id: 'history', icon: Activity, title: '활동 이력' }].map(({ id, icon: Icon, title }) => <Button variant="ghost" key={id} aria-label={title} aria-current={tab === id ? 'page' : undefined} className={tab === id ? 'nav-active' : ''} onClick={() => { setTab(id); closeMobile() }}><Icon size={18} /><span className="nav-text">{title}</span>{id === 'evidence' && requests.total > 0 && <Badge variant="secondary" className="nav-count">{requests.total}</Badge>}</Button>)}
      </nav>
      {readySSO && <><div className="nav-label">계정 관리</div><nav aria-label="계정 메뉴"><Button variant="ghost" aria-label="내 프로젝트·권한" onClick={() => navigate('welcome')}><ShieldCheck size={18} /><span className="nav-text">내 프로젝트·권한</span></Button></nav></>}
      <div className="sidebar-spacer" />
      <div className="portal-sidebar-note"><ShieldCheck size={15} /><span>모든 판정에 근거와 변경 이력을 남깁니다.</span></div>
      <button className="sidebar-toggle" onClick={() => setCollapsed(value => !value)} aria-expanded={!collapsed} aria-label={collapsed ? '메뉴 펼치기' : '메뉴 접기'}>{collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}<span>메뉴 접기</span></button>
      <div className="sidebar-footer"><div className="avatar">{session.reviewer.slice(0, 1)}</div><div><strong>{session.reviewer}</strong><small>{session.writable ? '검토자' : '읽기 전용'}</small></div><Button variant="ghost" size="icon-sm" aria-label="연결 종료" disabled={busy} onClick={logout}><LogOut size={17} /></Button></div>
      {!collapsed && !isMobile && <SidebarResizer layout={sidebarLayout} />}
    </aside>}
    <div className="main-shell" style={atlas ? { marginLeft: 0, width: '100%' } : undefined} inert={!atlas && isMobile && mobile} aria-hidden={!atlas && isMobile && mobile}>{!atlas && <header className="topbar"><div className="breadcrumb"><button ref={triggerRef} className="mobile-menu" aria-label="메뉴 열기" aria-expanded={mobile} aria-controls="workspace-menu" onClick={() => setMobile(true)}><Menu /></button><span>Design Atlas</span><ChevronRight size={13} /><strong>로그 검토</strong></div><div className="topbar-right"><ColorModeControl />{!demo && readySSO && <PersonalAiToken key={session.user_id} />}<span className="connection"><span className="online-dot" />{demo ? '데모 환경' : '연결됨'}</span><Separator orientation="vertical" /><span className="avatar avatar-small">{session.reviewer.slice(0, 1)}</span></div></header>}
    <main className="workspace" id="workspace-main" tabIndex={-1}>
      {demo && <div className="demo-banner"><Sparkles size={15} /><span><strong>데모 모드</strong> · 예시 검사 결과를 보여줍니다. 변경은 현재 세션에만 적용됩니다.</span><nav className="demo-navigation" aria-label="데모 화면"><a className="demo-switch" href={appUrl(gcaDemo ? '/?demo=1' : '/?demo=1&dataset=gca')}>{gcaDemo ? "기본 예시" : "GCA 예시 보기"}</a><a className="demo-switch" href={appUrl('/?demo=1&flow=access')}>프로젝트·담당자 데모</a><a className="demo-switch" href={appUrl('/?demo=1&flow=versions')}>버전·ECO·PoR 데모</a></nav><button onClick={logout}>데모 종료 <ArrowUpRight size={13} /></button></div>}
      {atlas && <div className="notice-banner" role="status"><FileSearch size={16} /><span>기존 접수 실행 대장은 공통 범위 필터를 지원하지 않습니다. 현재 계정에 허용된 실행을 표시합니다.</span><Button variant="outline" size="sm" onClick={() => setLegacyReview(false)}>선택 범위의 버전 로그로 돌아가기</Button></div>}
      {(!session.writable || readySSO && detail && !canReview) && <div className="notice-banner" role="status"><LockKeyhole size={16} />{readySSO ? '현재 결과는 조회만 가능합니다. 검토하려면 해당 블록의 OWNER 권한이 필요합니다.' : '읽기 전용 세션입니다. 검토와 수신 작업은 비활성화됩니다.'}</div>}
      {session.notice && <p className="session-notice"><ShieldCheck size={13} />{session.notice}</p>}
      {error && <div role="alert" className="error-banner"><span>{error}</span><button aria-label="오류 닫기" onClick={() => setError('')}><X size={16} /></button></div>}
      {notice && <div role="status" className="notice-banner"><CheckCheck size={16} /><span>{notice}</span><button aria-label="알림 닫기" onClick={() => setNotice('')}><X size={16} /></button></div>}
      <div className="page-heading"><div><h1>로그 검토</h1><p>점검 결과를 확인하고, 근거를 바탕으로 판단하세요.</p></div><div className="heading-actions"><Button variant="outline" onClick={refresh} disabled={busy || loading}><RefreshCw className={loading ? 'spin' : ''} />새로고침</Button>{(!readySSO || canReceive) && <Button onClick={() => command(async () => { const result = await api<{ status: string }>('/api/receive', {}); setNotice(`수신 처리: ${result.status}`); setTick(v => v + 1) })} disabled={busy || !canReceive}><Inbox />결과 수신</Button>}</div></div>
      {gcaDemo && <GcaDemoScope bundles={bundles.items} selected={selected} select={selectBundle} disabled={busy} />}
      <div className="stats-grid"><Stat label="전체 검토 대상" value={c ? number(c.expected) : '—'} icon={Layers3} caption="현재 선택한 실행의 sanity item" /><Stat label="검토 완료" value={c ? number(c.reviewed) : '—'} icon={CheckCheck} caption={c ? `해당 없음 ${c.approved_na}건 별도 승인` : '결과를 선택해 주세요'} tone="green" /><Stat label="검토 대기" value={c ? number(c.unresolved) : '—'} icon={Clock3} caption="사람의 확인이 필요한 항목" tone="amber" /><Stat label="누락된 결과" value={c ? number(c.missing) : '—'} icon={Files} caption="원본 결과의 추가 확인 필요" tone="red" /></div>
      <div className="content-grid"><section className="runs-panel"><div className="section-heading"><h2>{gcaDemo ? "블록 실행 이력" : "접수된 실행"} <span>{gcaDemo ? visibleBundles.length : bundles.total}</span></h2><Inbox size={17} /></div><div className="runs-list">{visibleBundles.length ? visibleBundles.map(bundle => <button className={`run-card ${selected === bundle.bundle_id ? 'run-selected' : ''}`} key={bundle.bundle_id} aria-pressed={selected === bundle.bundle_id} disabled={busy} onClick={() => selectBundle(bundle.bundle_id)}><div className="run-title"><span className="project-icon"><Box size={17} /></span><strong>{bundle.project}{gcaDemo && ` / ${bundle.design}`}</strong><Status value={bundle.coverage.check_status} /></div><div className="run-name" title={bundle.run_id}>{bundle.run_id}</div><div className="run-meta"><span>{date(bundle.created_at)}</span><span>개정 {bundle.review_revision}</span></div><div className="run-progress"><Progress value={bundle.coverage.expected ? (bundle.coverage.reviewed + bundle.coverage.approved_na) / bundle.coverage.expected * 100 : 0} /><span>{bundle.coverage.reviewed + bundle.coverage.approved_na}/{bundle.coverage.expected}</span></div></button>) : <Empty>접수된 실행이 없습니다.</Empty>}</div>{bundles.total > 20 && <Pagination offset={bundleOffset} total={bundles.total} limit={20} change={setBundleOffset} />}<div className="runs-foot"><ShieldCheck size={15} />수신만으로 검토가 승인되지 않습니다.</div></section>
      <section className="detail-panel" aria-busy={loading}>{!detail ? loading ? <LoadingDetail /> : <Empty>접수된 실행을 선택해 주세요.</Empty> : <>
        <div className="detail-header"><div><div className="detail-eyebrow"><span className="project-icon"><Box size={18} /></span>{detail.run.project}<span>/</span>{detail.run.design}</div><h2>{detail.run.run_id}</h2><div className="detail-metadata"><span>{detail.run.tool} {detail.run.tool_version}</span><span>단계 {detail.run.stage}</span><span><Fingerprint size={13} />개정 {detail.review_revision}</span></div></div><Button variant="outline" size="sm" onClick={download} disabled={busy}><ArrowDownToLine />보고서</Button></div>
        <div className="completion-box"><div className="completion-icon"><ClipboardCheck size={20} /></div><div><div className="completion-title">{c?.review_status === 'COMPLETE' ? '전체 검토 완료' : '전체 검토 진행 중'}<Status value={c?.review_status || 'INCOMPLETE'} /></div><p>검토 완료와 sanity 통과 여부는 별도로 관리합니다.</p></div><div className="completion-progress"><strong>{progress}<span>%</span></strong><Progress value={progress} /></div></div>
        {!!c?.global_blockers.length && <div className="unit-alert">완료를 막는 사유: {c.global_blockers.join(', ')}</div>}
        <Tabs value={tab} onValueChange={setTab} className="detail-tabs"><TabsList className="studio-portal-tabs"><TabsTrigger value="ledger">검토 대장 <span>{detail.unit_total}</span></TabsTrigger><TabsTrigger value="evidence">추가 근거 <span>{requests.total}</span></TabsTrigger><TabsTrigger value="clustering">AI 분류</TabsTrigger><TabsTrigger value="history">변경 이력 <span>{detail.history_total}</span></TabsTrigger></TabsList>
        <TabsContent value="clustering"><ClusterPanel key={`${selected}:${unitOffset}`} api={api} bundle={selected} units={detail.units} writable={canCluster} demo={demo} /></TabsContent>
        <TabsContent value="ledger"><div className="table-toolbar"><div className="search-input"><Search size={15} /><Input aria-label="현재 페이지 항목 검색" placeholder="현재 페이지 항목 검색" value={search} onChange={e => setSearch(e.target.value)} /></div><select className="studio-native-select" aria-label="항목 상태 필터" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">전체 상태</option><option value="pending">검토 대기</option><option value="FAIL">FAIL</option><option value="PASS">PASS</option><option value="UNKNOWN">판정 보류</option></select></div>
        <Table><TableHeader><TableRow><TableHead>점검 항목</TableHead><TableHead>규칙 판정</TableHead><TableHead className="count-col">검사 기록</TableHead><TableHead>사람 검토</TableHead><TableHead className="arrow-col"><span className="sr-only">선택</span></TableHead></TableRow></TableHeader><TableBody>{visibleUnits.map(unit => <TableRow key={unit.review_unit_id} className={unitId === unit.review_unit_id ? 'selected-row' : ''}><TableCell><button className="unit-select" onClick={() => { setUnitId(unit.review_unit_id); setGroupOffset(0) }}><span className={`item-symbol ${unit.check_status === 'FAIL' ? 'item-fail' : ''}`}><FileCheck2 size={16} /></span><span><strong>{unit.item_id}</strong><small>{unit.review_unit_id}</small></span></button></TableCell><TableCell><Status value={unit.check_status} /></TableCell><TableCell className="count-col tabular-nums">{number(unit.record_count)}</TableCell><TableCell>{unit.decision ? <span className="reviewed-cell"><CheckCheck size={14} />{unit.decision.conclusion}</span> : !unit.applicable ? <span className="muted">해당 없음</span> : <span className="pending-cell"><span />대기</span>}</TableCell><TableCell><Button size="icon" variant="ghost" aria-label={`${unit.item_id} 상세 보기`} onClick={() => { setUnitId(unit.review_unit_id); setGroupOffset(0) }}><ChevronRight /></Button></TableCell></TableRow>)}</TableBody></Table>{!visibleUnits.length && <Empty>현재 페이지에 일치하는 항목이 없습니다.</Empty>}<div className="table-footer"><span>PASS·누락·미지원 항목을 포함한 전체 대장</span><Pagination offset={unitOffset} total={detail.unit_total} limit={50} change={offset => { setDetail(null); setUnitId(''); setUnitOffset(offset); setSearch(''); setFilter('all'); setGroupOffset(0) }} /></div>
        {selectedUnit && <section className="unit-detail"><div className="unit-detail-heading"><div><h3>{selectedUnit.item_id}</h3></div><Button variant="outline" size="sm" disabled={busy || !canRequestEvidence} onClick={() => openForm('evidence', selectedUnit)}><MessageSquareMore />근거 요청</Button></div><div className="unit-facts"><span>실행 <Status value={selectedUnit.execution_status} /></span><span>해석 <Status value={selectedUnit.parse_status} /></span><span>평가 {number(selectedUnit.evaluated_count)} / {number(selectedUnit.record_count)}</span><span>규칙 v{selectedUnit.rule_version}</span></div>{!!selectedUnit.blockers.length && <div className="unit-alert">확인 필요: {selectedUnit.blockers.join(', ')}</div>}
        <RuleMetadata unit={selectedUnit} />{demo && <GcaReportSource unit={selectedUnit} />}<div className="group-header"><h4>발견된 문제 그룹 <span>{groups.total}</span></h4><span>원본 근거를 함께 확인하세요</span></div>{groups.items.map(group => <div className="finding" key={group.finding_group_id}><div className="finding-head"><strong>{group.message_code}</strong><Badge variant="outline" className="severity">{group.severity}</Badge><span>{number(group.occurrence_count)}회 발생</span></div><div className="identity-tags">{Object.entries(group.identity).map(([key, value]) => <span key={key}><b>{key}</b> {typeof value === 'string' ? value : JSON.stringify(value)}</span>)}</div><p className="identity-note">{group.identity_status === 'DEMO_RULE_BUCKET' ? '데모: 규칙별 묶음 · 동일 원인이나 AI 분류 결과가 아닙니다.' : group.identity_status === 'SAME_FINDING' ? '동일성 규칙에 따라 집계된 반복 기록' : '동일성 판단 보류 · 개별 기록 유지'}</p>{group.examples.map(example => <div className="code-example" key={example.record_id}><div><span>{example.file_id}:{example.line_start}{example.line_end !== example.line_start ? `–${example.line_end}` : ''}</span><span>{example.record_id}</span></div><p className="measurement">{example.observed !== undefined && <span>측정값 <b>{String(example.observed)}</b> {example.unit}</span>}{example.expected !== undefined && <span>기준값 <b>{String(example.expected)}</b> {example.unit}</span>}</p><pre>{example.raw}</pre></div>)}<div className="example-note"><CircleHelp size={13} />대표 근거 {group.examples.length}개 · 생략 {number(group.examples_omitted)}개. 대표 예시만으로 전체 검토를 완료하지 않습니다.</div></div>)}{!groups.items.length && <div className="no-findings"><CheckCheck size={18} /><span>{selectedUnit.check_status === 'PASS' ? '이 항목에서 집계된 문제 그룹이 없습니다. 전체 판정과 근거를 확인해 주세요.' : '표시할 문제 그룹이 없습니다. 실행·해석 상태와 차단 사유를 확인해 주세요.'}</span></div>}{groups.total > 20 && <Pagination offset={groupOffset} total={groups.total} limit={20} change={setGroupOffset} />}
        {selectedUnit.decision && <div className="decision-note"><span className="avatar avatar-small">{selectedUnit.decision.reviewer.slice(0, 1)}</span><div><strong>{selectedUnit.decision.reviewer} · {selectedUnit.decision.conclusion}</strong><p>{selectedUnit.decision.reason}</p></div></div>}<div className="review-action"><span><ShieldCheck size={15} />최종 판정은 검토자가 기록합니다.</span><Button disabled={busy || loading || !canReview || !selectedUnit.analysis_ready || !selectedUnit.applicable || !!c?.global_blockers.length} onClick={() => openForm('review', selectedUnit)}><ClipboardCheck />{selectedUnit.decision ? '검토 의견 수정' : '검토 의견 기록'}</Button></div></section>}
        </TabsContent>
        <TabsContent value="evidence"><div className="tab-intro"><h3>추가 근거 요청</h3><p>필요한 기록을 요청하고, VWP에서 전달된 응답을 수신합니다. 응답 수신은 승인으로 처리되지 않습니다.</p></div>{requests.items.length ? requests.items.map(request => <div className="request-card" key={request.request_id}><div className="request-top"><strong>{request.review_unit_id}</strong><span className="request-state">VWP 요청: {request.status === 'RECEIVED' ? '처리됨' : textStatus[request.status] || request.status} · OA 응답: {request.response_status === 'RECEIVED' ? '수신 완료' : request.response_status === 'READY' ? '수신 대기' : request.response_status || '미도착'}</span></div><p>{request.reason}</p><div className="request-records">{request.record_ids.map(id => <code key={id}>{id}</code>)}</div><div className="request-meta"><span>요청 {date(request.created_at)} · 만료 {date(request.expires_at)}</span><span title={request.request_id}>{short(request.request_id)}</span></div><div className="request-actions"><Button variant="outline" size="sm" disabled={busy || !canReceiveEvidence} onClick={() => command(async () => { const result = await api<{ status: string }>(`/api/evidence/${encodeURIComponent(request.request_id)}/receive`, {}); setNotice(`근거 수신: ${result.status}`); setTick(v => v + 1) })}><Inbox />응답 수신</Button><Button variant="outline" size="sm" disabled={busy || request.response_status !== 'RECEIVED'} onClick={() => command(async () => setEvidence({ ...await api<Evidence>(`/api/evidence/${encodeURIComponent(request.request_id)}`), requestId: request.request_id }))}><FileSearch />수신 근거 보기</Button>{request.result && <span>{request.result}</span>}</div></div>) : <Empty>추가 근거 요청이 없습니다. 점검 항목의 ‘근거 요청’에서 등록하세요.</Empty>}<Pagination offset={requestOffset} total={requests.total} limit={20} change={setRequestOffset} /></TabsContent>
        <TabsContent value="history"><div className="tab-intro"><h3>검토 변경 이력</h3><p>검토자, 판단 사유와 적용 개정을 함께 보존합니다.</p></div>{detail.history.length ? <div className="timeline">{detail.history.map(entry => <div className="timeline-entry" key={entry.revision}><span className="timeline-dot"><Check size={12} /></span><div className="timeline-title"><strong>{entry.actor}</strong><span>개정 {entry.revision}</span></div>{entry.decisions.map((decision, i) => <div className="history-decision" key={i}><Status value={decision.conclusion} /><strong>{decision.review_unit_id || decision.unit_id || '검토 기록'}</strong><p>{decision.reason}</p><small>{date(decision.reviewed_at)}</small></div>)}</div>)}</div> : <Empty>아직 기록된 사람의 검토가 없습니다.</Empty>}{detail.history_total > detail.history.length && <p className="tiny">최근 {detail.history.length}건을 표시합니다. 전체 이력은 서버에 보존됩니다.</p>}</TabsContent>
        </Tabs><div className="detail-foot"><span><Sparkles size={14} />AI 검토 {textStatus[detail.context.ai.status] || detail.context.ai.status}</span><span title={detail.bundle_id}>{short(detail.bundle_id)}</span></div>
      </>}</section></div>
      <footer className="workspace-footer"><span>Design Atlas <b>/</b> 로그 검토</span><span>모든 항목을 추적하고, 모든 판단에 근거를 남깁니다.</span></footer>
    </main></div>
    <Dialog open={!!form} onOpenChange={open => { if (!open && !busy) setForm(null) }}><DialogContent><DialogHeader><DialogTitle>{form?.kind === 'review' ? '검토 의견 기록' : '추가 근거 요청'}</DialogTitle><DialogDescription>{form?.unit.item_id} · 개정 {form?.revision}{form?.kind === 'review' ? '을 기준으로 판단합니다.' : ' · 요청은 1시간 동안 유효합니다.'}</DialogDescription></DialogHeader><form onSubmit={submit} className="command-form">{error && <div role="alert" className="error-banner">{error}</div>}{form?.kind === 'review' ? <fieldset><legend>최종 판단</legend><div className="conclusion-buttons">{(['PASS', 'FAIL'] as const).map(value => <label key={value} className={`conclusion-option ${conclusion === value ? 'chosen' : ''}`}><input type="radio" name="conclusion" value={value} checked={conclusion === value} disabled={busy || value === 'PASS' && form.unit.check_status === 'FAIL'} onChange={() => { setConclusion(value); edit() }} /><Status value={value} /><span>{value === 'PASS' ? '정상으로 확인' : '문제로 확인'}</span></label>)}</div>{form.unit.check_status === 'FAIL' && <p className="tiny">규칙 판정이 FAIL인 항목은 PASS로 변경할 수 없습니다.</p>}</fieldset> : <label>요청할 기록 ID<Textarea aria-label="요청할 기록 ID" value={recordIds} onChange={e => { setRecordIds(e.target.value); edit() }} placeholder="기록 ID를 줄바꿈 또는 쉼표로 구분" required disabled={busy} /></label>}<label htmlFor="decision-reason">{form?.kind === 'review' ? '판단 사유' : '요청 사유'} <span className="required">*</span></label><Textarea id="decision-reason" value={reason} onChange={e => { setReason(e.target.value); edit() }} placeholder="확인한 근거와 판단 이유를 구체적으로 기록해 주세요." rows={4} required disabled={busy} maxLength={form?.kind === 'review' ? 2000 : 1000} /><p className="tiny">{form?.kind === 'review' ? `검토자: ${session.reviewer}. 현재 번들에만 적용되며 이전 기록은 보존됩니다.` : '알려진 기록 ID로 추가 근거를 요청하세요. 응답이 도착하면 별도로 수신해야 합니다.'}</p><DialogFooter><Button type="button" variant="outline" onClick={() => setForm(null)} disabled={busy}>취소</Button><Button type="submit" disabled={busy || !reason.trim()}>{busy && <LoaderCircle className="spin" />}{form?.kind === 'review' ? '검토 저장' : '요청 등록'}</Button></DialogFooter></form></DialogContent></Dialog>
    <Dialog open={!!evidence} onOpenChange={open => { if (!open) setEvidence(null) }}><DialogContent className="evidence-dialog"><DialogHeader><DialogTitle>수신한 추가 근거</DialogTitle><DialogDescription>{evidence?.result} · 총 {evidence?.record_count}개 기록</DialogDescription></DialogHeader><div className="evidence-records">{evidence?.records.map(record => <RawEvidence key={`${evidence.requestId}:${record.record_id}`} api={api} requestId={evidence.requestId} record={record} demo={demo} />)}</div></DialogContent></Dialog>
  </div>
}

type AtlasReport = {
  id: string; project_id: string; block_id: string; version_id: string;
  item_id?: string; family?: string; check_status?: string; record_count?: number; eco?: string | null;
  source_storage?: string; [key: string]: unknown;
}

function AtlasReports({ onLegacy, canWorkbench }: { onLegacy: () => void; canWorkbench: boolean }) {
  const atlas = useAtlas()
  const auth = useDashboardAuth(true)
  const navigateDashboard = useDashboardNavigation()
  const projectId = atlas?.scope.projectId || ''
  const blockId = atlas?.scope.blockId || ''
  const versionId = atlas?.scope.versionId || ''
  const [reports, setReports] = useState<Page<AtlasReport>>({ items: [], total: 0, offset: 0, limit: 25 })
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<AtlasReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  useLayoutEffect(() => {
    setOffset(0); setSelected(null); setError('')
    setReports({ items: [], total: 0, offset: 0, limit: 25 })
  }, [projectId, blockId, versionId])
  useEffect(() => {
    if (!projectId) { setLoading(false); return }
    const controller = new AbortController()
    const params = new URLSearchParams({ project_id: projectId, offset: String(offset), limit: '25' })
    if (blockId) params.set('block_id', blockId)
    if (versionId) params.set('version_id', versionId)
    setLoading(true); setError('')
    auth.request<Page<AtlasReport>>(`/api/atlas/reports?${params}`, undefined, false, controller.signal)
      .then(value => { if (!controller.signal.aborted) { setReports(value); setSelected(null) } })
      .catch(cause => { if (!controller.signal.aborted) { setReports({ items: [], total: 0, offset, limit: 25 }); setSelected(null); setError(cause instanceof Error ? cause.message : '버전 로그를 불러오지 못했습니다.') } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [auth.request, projectId, blockId, versionId, offset, refresh])
  function openWorkbench(report: AtlasReport) {
    const target = new URL(appUrl('/?page=workbench'), location.origin)
    for (const [key, value] of Object.entries({ project: report.project_id, block: report.block_id, version: report.version_id, review: report.id, tab: 'reports', item: report.item_id, eco: report.eco })) {
      if (value) target.searchParams.set(key, value)
    }
    void navigateDashboard(target.pathname + target.search, auth.session?.user_id).catch(cause => setError(cause instanceof Error ? cause.message : '로그 검토를 열지 못했습니다.'))
  }
  return <main className="workspace" id="workspace-main" tabIndex={-1}>
    <div className="page-heading"><div><h1>버전 로그</h1><p>공통 범위의 정확한 프로젝트·블록·버전 ID로 {atlas?.demo ? '합성 보고서' : '수신된 로그'}를 조회합니다.</p></div><div className="heading-actions"><Button variant="outline" onClick={() => setRefresh(value => value + 1)} disabled={loading || !projectId}><RefreshCw className={loading ? 'spin' : ''} />새로고침</Button>{!atlas?.demo && <Button variant="outline" onClick={onLegacy}>기존 접수 실행 검토</Button>}</div></div>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {!projectId ? <Empty>상단 공통 범위에서 프로젝트를 선택해 주세요. 블록과 버전을 선택하면 조회 범위를 좁힐 수 있습니다.</Empty> : loading ? <LoadingDetail /> : error ? null : <>
      <section className="detail-panel"><div className="section-heading"><h2>수신된 보고서 <span>{number(reports.total)}</span></h2></div>
        {reports.items.length ? <Table><TableHeader><TableRow><TableHead>보고서</TableHead><TableHead>블록 · 버전 ID</TableHead><TableHead>검사 결과</TableHead><TableHead>기록 수</TableHead><TableHead>근거</TableHead></TableRow></TableHeader><TableBody>{reports.items.map(report => <TableRow key={report.id} className={selected?.id === report.id ? 'selected-row' : ''}><TableCell><strong>{report.id}</strong><div>{report.item_id || '항목 미분류'} · {report.family || '도구 미분류'}</div></TableCell><TableCell><code>{report.block_id}</code><br /><code>{report.version_id}</code></TableCell><TableCell>{report.check_status ? <Status value={report.check_status} /> : '미제공'}</TableCell><TableCell>{report.record_count === undefined ? '미제공' : number(report.record_count)}</TableCell><TableCell><Button variant="outline" size="sm" onClick={() => setSelected(report)}>수신 내용 보기</Button></TableCell></TableRow>)}</TableBody></Table> : <Empty>선택한 범위에 수신된 로그가 없습니다. 버전 선언이나 수치 분석 준비만으로 로그가 생성되지는 않습니다.</Empty>}
        <Pagination offset={offset} total={reports.total} limit={25} change={setOffset} />
      </section>
      {selected && <section className="detail-panel"><div className="detail-header"><div><h2>{selected.id}</h2><p>{atlas?.demo ? '현재 탭의 합성 보고서 payload입니다. 실제 서버나 DB에서 읽은 자료가 아닙니다.' : '서버에 저장된 보고서 payload 원문입니다. 분할 저장된 원본은 이 목록 API에서 펼치지 않습니다.'}</p></div><Button variant="ghost" onClick={() => setSelected(null)}>닫기</Button></div><div className="unit-detail"><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(selected, null, 2)}</pre>{canWorkbench ? <Button onClick={() => openWorkbench(selected)}><FileSearch size={16} />로그 분류·판정 열기</Button> : <p className="session-notice">운영 로그 분류·판정은 권한이 확인된 SSO 세션에서만 지원합니다. 로컬 토큰에서 데모로 대체하지 않습니다.</p>}</div></section>}
    </>}
    <footer className="workspace-footer"><span>Design Atlas <b>/</b> 로그 검토</span><span>수신된 원본과 검토 권한을 그대로 유지합니다.</span></footer>
  </main>
}

function RuleMetadata({ unit }: { unit: Unit }) {
  const fields = (value: Record<string, unknown>) => Object.entries(value).map(([key, val]) => <span key={key}><b>{({ operator: '비교 연산', limit: '임계값', unit: '단위', mode: '모드' } as Record<string, string>)[key] || key}</b> {key === 'operator' && val === 'le' ? '≤' : key === 'operator' && val === 'ge' ? '≥' : typeof val === 'string' ? val : JSON.stringify(val)}</span>)
  return <details className="rule-metadata"><summary>판정 기준 · 원본 정보</summary><dl><div><dt>원본 보고서</dt><dd>{unit.report}</dd></div>{unit.parser && <div><dt>파서</dt><dd>{unit.parser}</dd></div>}{unit.evaluation && <div><dt>판정 기준</dt><dd>{fields(unit.evaluation)}</dd></div>}{unit.condition && <div><dt>실행 조건</dt><dd>{fields(unit.condition)}</dd></div>}{unit.identity_fields && <div><dt>문제 식별 필드</dt><dd>{unit.identity_fields.join(', ')}</dd></div>}</dl></details>
}
function LoadingDetail() {
  return <div className="loading-state" role="status"><p>검토 결과를 불러오는 중입니다.</p><div aria-hidden="true"><div className="skeleton skeleton-title" />{Array.from({ length: 5 }, (_, row) => <div key={row} className="skeleton-row"><div className="skeleton" /><div className="skeleton" /><div className="skeleton" /></div>)}</div></div>
}
function Stat({ label, value, caption, icon: Icon, tone = 'blue' }: { label: string; value: string; caption: string; icon: typeof Layers3; tone?: string }) { return <article className={`stat-card stat-${tone}`}><div className="portal-stat-content"><div><span>{label}</span><span className="stat-icon"><Icon size={17} /></span></div><strong>{value}<small>항목</small></strong><p>{caption}</p></div></article> }
