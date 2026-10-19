import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Check, ChevronDown, ChevronLeft, ChevronRight, ClipboardList, Download, FileSearch, FolderKanban, History, Layers3, LogOut, Menu, RefreshCw, Search, X } from 'lucide-react'
import { Button } from './components/ui/button'
import { Input } from './components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from './components/ui/tabs'
import { WorkspaceBrand } from './components/WorkspaceBrand'
import { useWorkspaceNavigation } from './useWorkspaceNavigation'
import { WorkspaceError, workspaceLabels as labels, workspaceRequest as request, workspaceTime as time } from './workspaceApi'
import type { ReviewCase, WorkspaceEvent, WorkspaceOverview, WorkspacePor, WorkspaceReport, WorkspaceReports, WorkspaceSession, WorkspaceVersions } from './workspaceApi'
import WorkspaceReview from './WorkspaceReview'
import WorkspaceWorkbench from './WorkspaceWorkbench'
import './workspace.css'

const n = (value: number) => value.toLocaleString('ko-KR')
const itemNames: Record<string, string> = { gca_constraints: 'GCA 제약조건 검사', vclp_low_power: 'VC LP 저전력 검사', spyglass_lint: 'SpyGlass Lint 검사' }
const itemName = (id: string) => itemNames[id] || id || '항목 미분류'
function Status({ value }: { value: string }) { return <span className={`ws-status ws-status-${value.toLowerCase()}`}><i />{labels[value] || value}</span> }
function Loading({ lines = 3 }: { lines?: number }) { return <div className="ws-skeleton" aria-label="불러오는 중" role="status">{Array.from({ length: lines }, (_, i) => <span key={i} />)}</div> }

export default function Workspace() {
  const [session, setSession] = useState<WorkspaceSession | null>(null)
  const [checking, setChecking] = useState(true)
  const [username, setUsername] = useState('user04')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    request<WorkspaceSession>('/session', { signal: controller.signal }).then(setSession).catch(e => {
      if (e instanceof WorkspaceError && e.code !== 'UNAUTHORIZED') setError(e.message)
    }).finally(() => { if (!controller.signal.aborted) setChecking(false) })
    return () => controller.abort()
  }, [])
  async function login(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try { setSession(await request<WorkspaceSession>('/login', { body: { username, password } })); setPassword('') }
    catch (e) { setError(e instanceof Error ? e.message : '로그인 실패') }
    finally { setBusy(false) }
  }
  if (checking) return <div className="ws-session-loading"><WorkspaceBrand /><Loading /></div>
  if (session) return <WorkspacePage key={session.user.id} session={session} onExpired={() => { setSession(null); setError('로그인 세션이 만료되었습니다. 다시 연결해 주세요.') }} onLogout={() => { setSession(null); setError('') }} />
  return <div className="ws-login">
    <section className="ws-login-story"><WorkspaceBrand /><div><span className="eyebrow">SANITY WORKSPACE</span><h1>기준을 정하고,<br />근거를 함께 봅니다.</h1><p>프로젝트부터 블록의 PoR까지.<br />내가 맡은 범위에서 다음 판단을 이어가세요.</p><div className="ws-login-steps"><span><b>01</b>담당 프로젝트</span><span><b>02</b>블록별 버전</span><span><b>03</b>기준 버전과 검사 근거</span></div></div><small>사용자 10명 · 프로젝트 3개 · 블록 9개</small></section>
    <main className="ws-login-main"><div className="ws-login-form"><span className="eyebrow">YOUR WORKSPACE</span><h2>워크스페이스에 로그인</h2><p>배정된 프로젝트와 블록의 검사 결과를 확인합니다.</p>{error && <p className="ws-error" role="alert">{error}</p>}<form onSubmit={login}><label htmlFor="ws-username">사용자 ID</label><Input id="ws-username" autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required /><label htmlFor="ws-password">비밀번호</label><Input id="ws-password" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /><Button type="submit" disabled={busy || !password}>{busy ? '연결 중…' : '로그인'}<ArrowRight /></Button></form><details className="ws-account-help"><summary>테스트 계정 안내 <ChevronDown size={14} /></summary><p>공용 테스트 비밀번호 <code>sanity-workspace</code></p><dl><div><dt>user04 · 정민수</dt><dd>Atlas CPU · Cygnus GPU 담당</dd></div><div><dt>user01 · 이서연</dt><dd>Atlas TOP · 모든 블록 조회</dd></div><div><dt>user02 / user03</dt><dd>Boreal / Cygnus TOP</dd></div><div><dt>user05 ~ user10</dt><dd>각 배정 블록 담당자</dd></div></dl></details><p className="ws-login-note">독립 테스트 환경입니다. 계정과 검사 자료는 예시이며,<br />PoR과 변경 이력은 서버에 저장되어 유지됩니다.</p></div><footer>사내 SSO는 아직 연결하지 않았습니다.</footer></main>
  </div>
}

function WorkspacePage({ session, onExpired, onLogout }: { session: WorkspaceSession; onExpired: () => void; onLogout: () => void }) {
  const initial = useRef(new URLSearchParams(location.search))
  const [overview, setOverview] = useState<WorkspaceOverview | null>(null)
  const [projectId, setProjectId] = useState(initial.current.get('project') || '')
  const [blockId, setBlockId] = useState(initial.current.get('block') || '')
  const [versionId, setVersionId] = useState(initial.current.get('version') || '')
  const [eco, setEco] = useState(initial.current.get('eco') || '')
  const [view, setView] = useState(['overview', 'reports', 'history'].includes(initial.current.get('tab') || '')
    ? initial.current.get('tab')! : initial.current.get('block') ? 'reports' : 'overview')
  const [versions, setVersions] = useState<WorkspaceVersions | null>(null)
  const [reports, setReports] = useState<WorkspaceReports | null>(null)
  const [reportsLoading, setReportsLoading] = useState(true)
  const [itemId, setItemId] = useState<string | null>(initial.current.get('item'))
  const [events, setEvents] = useState<WorkspaceEvent[]>([])
  const [family, setFamily] = useState('')
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [selectedReport, setSelectedReport] = useState<WorkspaceReport | null>(null)
  const [workbenchId, setWorkbenchId] = useState(initial.current.get('review') || '')
  const [showReportDetails, setShowReportDetails] = useState(false)
  const [reportLoading, setReportLoading] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [mobile, setMobile] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const closeMobile = useCallback(() => setMobile(false), [])
  const { isMobile, sidebarRef, triggerRef } = useWorkspaceNavigation(mobile, closeMobile, true)
  const requestId = useRef<string | null>(null)
  const detailRequest = useRef(0)
  const project = overview?.projects.find(project => project.id === projectId)
  const block = project?.blocks.find(block => block.id === blockId)
  const version = versions?.items.find(version => version.id === versionId)
  const por = versions?.por
  const isPor = !!version && por?.version?.id === version.id
  const selectedItem = reports?.groups.find(group => group.item_id === itemId)
  const handleError = useCallback((e: unknown) => {
    if (e instanceof DOMException && e.name === 'AbortError') return
    if (e instanceof WorkspaceError && e.code === 'UNAUTHORIZED') { onExpired(); return }
    setError(e instanceof Error ? e.message : '요청을 완료하지 못했습니다.')
  }, [onExpired])

  useEffect(() => {
    let active = true
    request<WorkspaceOverview>('/overview').then(value => {
      if (!active) return
      setOverview(value)
      setProjectId(previous => value.projects.some(project => project.id === previous) ? previous : value.projects[0]?.id || '')
    }).catch(e => { if (active) handleError(e) })
    return () => { active = false }
  }, [refresh]) // Account is fixed for the lifetime of this keyed page.
  useEffect(() => {
    if (!project) return
    if (!project.blocks.some(block => block.id === blockId)) setBlockId(project.blocks[0]?.id || '')
  }, [project, blockId])
  useEffect(() => {
    if (!blockId || !block) return
    const controller = new AbortController()
    setVersions(null); setReports(null); setEvents([]); setSelectedReport(null)
    request<WorkspaceVersions>(`/blocks/${blockId}/versions`, { signal: controller.signal }).then(value => {
      if (controller.signal.aborted) return
      setVersions(value)
      setVersionId(previous => value.items.some(version => version.id === previous) ? previous
        : value.por.version?.id || value.items.find(version => version.layout === 'POST')?.id || value.items[0]?.id || '')
    }).catch(handleError)
    return () => controller.abort()
  }, [blockId, refresh, !!block])
  useEffect(() => {
    if (!version) return
    const selectedEco = version.ecos.find(item => (item || '') === eco)
    if (selectedEco === undefined) setEco(version.ecos[0] || '')
  }, [version, eco])
  useEffect(() => {
    setReportsLoading(true)
    if (!version || !version.ecos.some(item => (item || '') === eco)) return
    const controller = new AbortController()
    const params = new URLSearchParams({ offset: String(offset), limit: '25' })
    if (eco) params.set('eco', eco)
    if (itemId !== null) params.set('item_id', itemId)
    if (family) params.set('family', family)
    if (status) params.set('status', status)
    if (search) params.set('search', search)
    const timer = setTimeout(() => request<WorkspaceReports>(`/blocks/${blockId}/versions/${version.id}/reports?${params}`, { signal: controller.signal })
      .then(value => {
        if (controller.signal.aborted) return
        const group = value.groups.find(group => group.item_id === itemId) || value.groups[0]
        if (group && group.item_id !== itemId) { setItemId(group.item_id); setOffset(0); return }
        setReports(value)
        setReportsLoading(false)
      }).catch(handleError), 120)
    return () => { clearTimeout(timer); controller.abort() }
  }, [blockId, version, eco, itemId, offset, family, status, search, refresh])
  useEffect(() => {
    if (!block) return
    const controller = new AbortController()
    request<{ items: WorkspaceEvent[] }>(`/blocks/${blockId}/history`, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) setEvents(value.items)
    }).catch(handleError)
    return () => controller.abort()
  }, [blockId, refresh, !!block])
  useEffect(() => {
    if (!project || !block || !version) return
    const params = new URLSearchParams({ project: project.id, block: block.id, version: version.id, tab: view })
    if (eco) params.set('eco', eco)
    if (itemId !== null) params.set('item', itemId)
    if (view === 'reports' && workbenchId) params.set('review', workbenchId)
    history.replaceState(null, '', `/workspace?${params}`)
  }, [projectId, blockId, versionId, view, eco, itemId, !!version, workbenchId])

  function resetSelection() {
    setWorkbenchId('')
    detailRequest.current++; setSelectedReport(null); setReportLoading(false); setConfirm(false); setError(''); setNotice('')
    setItemId(null); setFamily(''); setStatus(''); setSearch(''); setOffset(0); requestId.current = null
  }
  function chooseProject(id: string) {
    resetSelection(); setProjectId(id); setBlockId(''); setVersionId(''); setVersions(null); setReports(null); setEco(''); setView('overview'); closeMobile()
  }
  function chooseBlock(id: string) {
    resetSelection()
    if (id !== blockId) { setBlockId(id); setVersionId(''); setVersions(null); setReports(null); setEco('') }
    setView('reports'); closeMobile()
  }
  function chooseVersion(id: string) {
    if (id === versionId) return
    resetSelection(); setVersionId(id); setReports(null); setEco(versions?.items.find(version => version.id === id)?.ecos[0] || '')
  }
  function chooseItem(id: string) {
    if (id === itemId) return
    detailRequest.current++; setSelectedReport(null); setReportLoading(false)
    setWorkbenchId(''); setItemId(id); setFamily(''); setStatus(''); setSearch(''); setOffset(0); setReportsLoading(true)
  }
  async function logout() {
    try { await request('/logout', { body: {}, csrf: session.csrf }); onLogout() } catch (e) { handleError(e) }
  }
  async function openReport(id: string) {
    const current = ++detailRequest.current
    setReportLoading(true); setSelectedReport(null)
    try {
      const report = await request<WorkspaceReport>(`/blocks/${blockId}/versions/${versionId}/reports/${id}`)
      if (detailRequest.current === current) setSelectedReport(report)
    } catch (e) { handleError(e) }
    finally { if (detailRequest.current === current) setReportLoading(false) }
  }
  async function savePor() {
    if (!version || !por || !reason.trim()) return
    setSaving(true); setSaveError('')
    requestId.current ||= crypto.randomUUID()
    try {
      await request(`/blocks/${blockId}/por`, { body: { version_id: version.id, expected_revision: por.revision, request_id: requestId.current, reason: reason.trim() }, csrf: session.csrf })
      setConfirm(false); setReason(''); requestId.current = null; setNotice(`${block?.name}의 PoR을 ${version.net_ver} · ${version.layout} 버전으로 지정했습니다.`); setRefresh(value => value + 1)
    } catch (e) {
      if (e instanceof WorkspaceError && e.code === 'UNAUTHORIZED') { onExpired(); return }
      if (e instanceof WorkspaceError && e.code === 'REVISION_CONFLICT') { setConfirm(false); setRefresh(value => value + 1); requestId.current = null; handleError(e) }
      else setSaveError(e instanceof Error ? e.message : '저장 결과를 확인하지 못했습니다.')
    } finally { setSaving(false) }
  }
  function downloadReport() {
    if (!selectedReport?.raw_report) return
    const url = URL.createObjectURL(new Blob([selectedReport.raw_report], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${selectedReport.id}.rpt`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  function reviewSaved(event: ReviewCase) {
    const summary = { conclusion: event.conclusion, actor: event.actor, at: event.at, revision: event.revision, reusable: event.reusable }
    setSelectedReport(previous => previous?.id === event.report_id ? { ...previous, human_review: summary } : previous)
    setReports(previous => previous ? { ...previous, items: previous.items.map(item => item.id === event.report_id ? { ...item, human_review: summary } : item) } : previous)
  }
  const reviewing = view === 'reports' && !!workbenchId
  const totals = project?.blocks.reduce((total, block) => ({ total: total.total + block.total, fail: total.fail + (block.counts.FAIL || 0), unknown: total.unknown + (block.counts.UNKNOWN || 0), por: total.por + Number(!!block.por.version) }), { total: 0, fail: 0, unknown: 0, por: 0 })

  return <div className={`ws-app ${collapsed ? 'ws-collapsed' : ''}`}>
    <a className="skip-link" href="#ws-main">본문으로 건너뛰기</a>
    {mobile && isMobile && <button className="ws-backdrop" aria-label="메뉴 닫기" onClick={closeMobile} />}
    <aside ref={sidebarRef} className={`ws-sidebar ${mobile ? 'ws-open' : ''}`} aria-label="워크스페이스 메뉴" role={mobile && isMobile ? 'dialog' : undefined} aria-modal={mobile && isMobile || undefined} inert={isMobile && !mobile}>
      <WorkspaceBrand /><button className="ws-mobile-close" aria-label="메뉴 닫기" onClick={closeMobile}><X size={19} /></button>
      <div className="ws-account"><span className="ws-avatar">{session.user.name.slice(-2)}</span><div><strong>{session.user.name}</strong><span>{session.user.role} · {session.user.username}</span></div></div>
      <span className="ws-sidebar-caption">내 프로젝트</span><nav aria-label="내 프로젝트">{overview?.projects.map(item => <button key={item.id} aria-label={`${item.name} 프로젝트`} aria-current={projectId === item.id ? 'page' : undefined} onClick={() => chooseProject(item.id)}><FolderKanban size={17} /><span>{item.name}</span><small>{item.blocks.length}</small></button>)}</nav>
      <div className="ws-sidebar-divider" /><nav aria-label="작업 메뉴">{[{ id: 'overview', label: '프로젝트 현황', icon: Layers3 }, { id: 'reports', label: '버전·검사 대장', icon: ClipboardList }, { id: 'history', label: 'PoR 변경 이력', icon: History }].map(({ id, label, icon: Icon }) => <button key={id} aria-label={label} aria-current={view === id ? 'page' : undefined} onClick={() => { setView(id); closeMobile() }}><Icon size={17} /><span>{label}</span></button>)}</nav>
      <div className="ws-sidebar-bottom"><span className="ws-environment">테스트 환경 · 서버 저장</span><p>예시 계정과 검사 자료를 사용합니다.<br />PoR과 이력은 계정 간에 공유됩니다.</p><button className="ws-collapse" onClick={() => setCollapsed(value => !value)} aria-label={collapsed ? '메뉴 펼치기' : '메뉴 접기'} aria-expanded={!collapsed}><ChevronLeft size={16} /><span>메뉴 접기</span></button><button className="ws-logout" onClick={logout}><LogOut size={16} /><span>로그아웃</span></button></div>
    </aside>
    <div className="ws-main-shell" inert={isMobile && mobile}><header className="ws-topbar"><div><button ref={triggerRef} className="ws-mobile-trigger" aria-label="메뉴 열기" aria-expanded={mobile} onClick={() => setMobile(true)}><Menu size={20} /></button><span>워크스페이스</span><ChevronRight size={13} /><strong>{project?.name || '내 프로젝트'}</strong>{view !== 'overview' && block && <><ChevronRight size={13} /><strong>{block.name}</strong></>}</div><div><span className="ws-connected"><i />서버 연결</span><span className="ws-avatar ws-avatar-small">{session.user.name.slice(-2)}</span></div></header>
      <main id="ws-main" className={`ws-main${reviewing ? ' ws-main-reviewing' : ''}`} tabIndex={-1}>
        {!reviewing && <div className="ws-heading"><div><span className="eyebrow">{view === 'overview' ? 'PROJECT WORKSPACE' : view === 'history' ? 'VERSION HISTORY' : 'BLOCK WORKSPACE'}</span><h1>{view === 'overview' ? `${project?.name || session.user.name} 프로젝트` : view === 'history' ? 'PoR 변경 이력' : `${block?.name || '블록'} · 버전과 검사 결과`}</h1><p>{view === 'overview' ? '담당 블록의 기준 버전과 검사 현황을 한곳에서 확인합니다.' : view === 'history' ? '기준 버전을 누가, 언제, 어떤 사유로 변경했는지 확인합니다.' : '조회하는 버전과 현재 PoR을 구분해서 검사 근거를 확인합니다.'}</p></div><Button variant="outline" onClick={() => { setError(''); setRefresh(value => value + 1) }}><RefreshCw size={15} />새로고침</Button></div>}
        {error && <div className="ws-error" role="alert">{error}</div>}{notice && <div className="ws-notice" role="status"><Check size={16} />{notice}</div>}
        {!overview ? <Loading lines={4} /> : !project ? <div className="ws-empty"><FolderKanban /><h2>배정된 프로젝트가 없습니다.</h2><p>프로젝트 담당자에게 배정을 요청해 주세요.</p></div> : <>
          {view === 'overview' ? <>
            <div className="ws-statistics"><section><span>수신 대상 검사</span><strong>{n(totals!.total)}<small>항목</small></strong></section><section><span>확인할 FAIL</span><strong className="ws-danger-text">{n(totals!.fail)}<small>항목</small></strong></section><section><span>판정 보류·미수신</span><strong>{n(totals!.unknown)}<small>항목</small></strong></section><section><span>PoR 지정</span><strong>{totals!.por}<small>/ {project.blocks.length}블록</small></strong></section></div>
            <section className="ws-panel"><header className="ws-panel-header"><div><h2>블록별 현재 PoR</h2><p>{project.tops.map(user => user.name).join(', ')} · 프로젝트 TOP</p></div><span className="ws-caption">내 조회 범위 {project.blocks.length}개 블록</span></header><div className="ws-table-scroll"><table className="ws-block-table"><thead><tr><th>블록 / 담당자</th><th>현재 PoR</th><th>검사 현황</th><th>최근 지정</th><th /></tr></thead><tbody>{project.blocks.map(item => <tr key={item.id}><td><strong>{item.name}</strong><small>{item.owners.map(owner => owner.name).join(', ')}</small></td><td>{item.por.version ? <><span className="ws-netver">{item.por.version.net_ver} <b>{item.por.version.layout}</b></span><code>{item.por.version.revision}</code></> : <span className="ws-muted">PoR 미지정</span>}</td><td><span className="ws-block-count">{item.total ? `${n(item.total)} 항목` : '미수신'}</span><small>{item.report_version?.net_ver} · {item.por.version ? 'PoR 버전' : '최신 수신'}</small>{item.total > 0 && <small>FAIL {item.counts.FAIL || 0} · 보류 {item.counts.UNKNOWN || 0}</small>}</td><td><small>{item.por.assigned_by?.name || '—'}<br />{time(item.por.assigned_at)}</small></td><td><Button variant="outline" size="sm" aria-label={`${item.name} 버전과 검사 보기`} onClick={() => chooseBlock(item.id)}>블록 열기<ArrowRight size={14} /></Button></td></tr>)}</tbody></table></div></section>
            <div className="ws-overview-foot"><p><Layers3 size={16} />PoR은 블록 담당자가 지정합니다. TOP 권한은 프로젝트 전체의 조회 범위입니다.</p><p>수신된 예시 검사 결과 · PoR 변경은 검사 판정과 기존 근거를 바꾸지 않습니다.</p></div>
          </> : <>
            {!reviewing && <div className="ws-block-strip" aria-label="담당 블록 선택">{project.blocks.map(item => <button key={item.id} aria-pressed={blockId === item.id} onClick={() => chooseBlock(item.id)}><Layers3 size={15} />{item.name}<span>{item.owners.map(owner => owner.name).join(', ')}</span></button>)}</div>}
            {!versions || !version || !block ? <Loading lines={4} /> : <>
              {!reviewing && <PorSummary por={versions.por} canAssign={versions.can_assign_por} />}
              {view === 'history' ? <section className="ws-panel"><header className="ws-panel-header"><div><h2>{block.name} · 변경 기록</h2><p>최신 50건 · 서버에 저장된 지정 이력</p></div><span className="ws-caption">현재 개정 {por?.revision}</span></header>{events.length ? <ol className="ws-history">{events.map(event => <li key={event.id}><div className="ws-history-marker"><Check size={13} /></div><div><header><strong>{event.actor.name}</strong><span>{time(event.at)} · 개정 {event.revision}</span></header><h3>{event.version.net_ver}<span>{event.version.layout}</span></h3><code>{event.version.revision}</code><p>{event.reason}</p></div></li>)}</ol> : <div className="ws-empty"><History /><h3>아직 PoR 지정 이력이 없습니다.</h3><p>블록 담당자가 버전을 지정하면 이곳에 기록됩니다.</p><Button variant="outline" onClick={() => setView('reports')}>버전 목록 보기<ArrowRight size={14} /></Button></div>}</section> : <>
                {!reviewing && <><section className="ws-version-bar" aria-label="조회할 버전 선택"><div className="ws-layout-toggle" role="group" aria-label="Layout 단계">{(['PRE', 'POST'] as const).map(layout => <button key={layout} aria-pressed={version.layout === layout} onClick={() => { const next = versions.items.find(item => item.layout === layout && item.net_ver === version.net_ver); if (next) chooseVersion(next.id) }}>{layout}</button>)}</div><label>NET_VER · REVISION<select aria-label="조회 버전" value={versionId} onChange={e => chooseVersion(e.target.value)}>{versions.items.filter(item => item.layout === version.layout).map(item => <option value={item.id} key={item.id}>{item.net_ver} · {item.revision}</option>)}</select></label>{version.layout === 'POST' && <label>ECO<select aria-label="조회 ECO" value={eco} onChange={e => { resetSelection(); setEco(e.target.value); setReports(null) }}>{version.ecos.map(value => <option key={value!} value={value!}>{value}</option>)}</select></label>}</section>
                <section className="ws-version-context"><div><span className="ws-caption">조회 중인 버전</span><h2>{version.net_ver}<span>{version.layout}{eco ? ` / ${eco}` : ''}</span>{isPor && <b className="ws-por-tag"><Check size={12} />현재 PoR</b>}</h2><code>{version.revision}</code></div>{versions.can_assign_por ? <Button disabled={isPor} onClick={() => { requestId.current = null; setReason(''); setSaveError(''); setConfirm(true) }}>{isPor ? <Check size={15} /> : <Layers3 size={15} />}{isPor ? '현재 PoR' : '이 버전을 PoR로 지정'}</Button> : <span className="ws-readonly">조회 권한 · PoR 지정은 블록 담당자만 가능</span>}</section></>}
                {!reports ? <Loading /> : <>
                  {!reviewing && <><div className="ws-report-counts"><span>버전 전체 <strong>{n(Object.values(reports.counts).reduce((a, b) => a + b, 0))}</strong></span><span>PASS <strong>{reports.counts.PASS || 0}</strong></span><span>FAIL <strong>{reports.counts.FAIL || 0}</strong></span><span>보류 <strong>{reports.counts.UNKNOWN || 0}</strong></span></div>
                  {reports.groups.length > 0 && <section className="ws-item-catalog" aria-label="Sanity item별 현황">
                    <div className="ws-item-heading"><h2>Sanity 항목</h2><span>{reports.groups.length}개 항목 · 항목을 선택해 검사 결과를 확인하세요.</span></div>
                    <div className="ws-item-picker" role="group" aria-label="Sanity item 선택">
                      {reports.groups.map(group => <button key={group.item_id} aria-label={`${itemName(group.item_id)} 선택`} aria-pressed={itemId === group.item_id} onClick={() => chooseItem(group.item_id)}>
                        <span className="ws-item-title"><strong>{itemName(group.item_id)}</strong><b>{n(group.total)}<small>검사</small></b></span>
                        <code>{group.item_id || 'item_id 없음'}</code>
                        <span className="ws-item-stats"><span>PASS <b>{group.counts.PASS || 0}</b></span><span>FAIL <b>{group.counts.FAIL || 0}</b></span><span>보류 <b>{group.counts.UNKNOWN || 0}</b></span>{!!group.parse_counts.MISSING && <span>미수신 <b>{group.parse_counts.MISSING}</b></span>}</span>
                      </button>)}
                    </div>
                  </section>}
                  </>}
                  {workbenchId ? <WorkspaceWorkbench key={`${blockId}:${versionId}:${workbenchId}`} reportId={workbenchId} blockId={blockId} versionId={versionId} session={session} onBack={() => { setWorkbenchId(''); setRefresh(value => value+1) }} onExpired={onExpired} onChanged={value => {
                    const summary = { cluster_revision: value.revision, confirmed: value.summary.confirmed, total: value.summary.total, reviewed: value.summary.reviewed, stale: value.summary.counts.STALE, groups: {}, removed: [] }
                    setReports(previous => previous ? { ...previous, items: previous.items.map(item => item.id === value.report.id ? { ...item, group_review_summary: summary } : item) } : previous)
                  }} /> : <>
                  {selectedItem && <section className="ws-report-start-guide" aria-label="항목의 순차 검토 시작"><div><h2>보고서 검토</h2><p>보고서별 로그 분류를 확정한 뒤 그룹별 사용자 판정을 기록합니다.</p></div><span><b>1</b>로그 분류<ArrowRight size={15} /><b>2</b>그룹별 판정</span></section>}
                  <section className="ws-panel" aria-busy={reportsLoading} aria-label={selectedItem ? `${itemName(selectedItem.item_id)} 검사 대장` : '검사 대장'}>
                    <header className="ws-report-toolbar"><div><h2>{selectedItem ? itemName(selectedItem.item_id) : '검사 대장'} <span>{reportsLoading ? '조회 중…' : n(reports.total)}</span></h2><small>{selectedItem ? '선택한 sanity item 안에서 보고서와 규칙을 검색합니다.' : '현재 버전·ECO에 수신된 검사 항목이 없습니다.'}</small></div>
                      {selectedItem && <div className="ws-report-filters"><div className="ws-search"><Search size={15} /><Input aria-label="검사 검색" value={search} placeholder="보고서 ID, 규칙 검색" onChange={e => { setSearch(e.target.value); setOffset(0) }} /></div>
                        {selectedItem.families.length > 1 && <select aria-label="도구 필터" value={family} onChange={e => { setFamily(e.target.value); setOffset(0) }}><option value="">모든 도구</option>{selectedItem.families.map(tool => <option key={tool} value={tool}>{labels[tool] || tool}</option>)}</select>}
                        <select aria-label="결과 필터" value={status} onChange={e => { setStatus(e.target.value); setOffset(0) }}><option value="">모든 결과</option>{['PASS', 'FAIL', 'UNKNOWN', 'MISSING'].map(value => <option key={value} value={value}>{labels[value]}</option>)}</select>
                        <Button variant="ghost" size="sm" aria-pressed={showReportDetails} onClick={() => setShowReportDetails(value => !value)}>{showReportDetails ? '기본 열 보기' : '상세 열 보기'}</Button>
                      </div>}
                    </header>
                    {reportsLoading ? <Loading lines={4} /> : reports.total ? <div className="ws-table-scroll"><table className="ws-report-table"><thead><tr><th>보고서</th><th>도구의 검사 결과</th><th>검토 진행</th><th>로그 수</th>{showReportDetails && <><th>검사 규칙</th><th>보고서 전체 판정</th><th>분석 상태</th></>}<th>검토</th></tr></thead><tbody>{reports.items.map(item => <tr key={item.id} data-item-id={item.item_id || ''}><td><strong>{item.id}</strong><small>{labels[item.family] || item.family}</small></td><td><Status value={item.check_status} /></td><td>{item.group_review_summary ? <><span>{item.group_review_summary.confirmed ? `${item.group_review_summary.reviewed} / ${item.group_review_summary.total} 확정` : '그룹 확인 중'}</span>{item.group_review_summary.stale > 0 && <small>재검토 {item.group_review_summary.stale}개 그룹</small>}</> : <span className="ws-muted">아직 시작 전</span>}</td><td>{n(item.record_count)}건</td>{showReportDetails && <><td><code>{item.rule}</code></td><td>{item.human_review ? <Status value={item.human_review.conclusion} /> : <span className="ws-muted">미검토</span>}</td><td><Status value={item.parse_status} /></td></>}<td><div className="ws-report-row-actions"><Button size="sm" aria-label={`${item.id} 검토 시작`} onClick={() => setWorkbenchId(item.id)}>검토 시작<ArrowRight size={14} /></Button><Button size="sm" variant="ghost" aria-label={`${item.id} 보고서 보기`} onClick={() => item.record_count > 1000 ? setWorkbenchId(item.id) : openReport(item.id)}>보고서 보기</Button></div></td></tr>)}</tbody></table></div>
                      : <div className="ws-empty"><FileSearch /><h3>{selectedItem && (search || family || status) ? '조건에 맞는 검사 항목이 없습니다.' : '이 버전의 검사 결과가 아직 수신되지 않았습니다.'}</h3><p>{selectedItem && (search || family || status) ? '검색어나 필터를 바꿔서 확인해 주세요.' : '결과가 도착하면 현재 버전·ECO의 검사 대장에 표시됩니다.'}</p></div>}
                    <div className="ws-pagination"><span>{reportsLoading ? '조회 중…' : `${reports.total ? `${offset + 1}–${Math.min(offset + 25, reports.total)}` : 0} / ${n(reports.total)}`}</span><Button size="icon" variant="outline" aria-label="이전 검사 페이지" disabled={reportsLoading || !offset} onClick={() => setOffset(offset - 25)}><ChevronLeft /></Button><Button size="icon" variant="outline" aria-label="다음 검사 페이지" disabled={reportsLoading || offset + 25 >= reports.total} onClick={() => setOffset(offset + 25)}><ChevronRight /></Button></div>
                  </section>
                  </>}
                </>}
              </>}
            </>}
          </>}
        </>}
        <footer className="ws-footer"><span>Sanity Console · 테스트 워크스페이스</span><span>SSO 미연결 · 예시 검사 자료 · PoR 서버 저장</span></footer>
      </main>
    </div>
    <Dialog open={confirm} onOpenChange={open => { if (!saving) setConfirm(open) }}><DialogContent className="ws-confirm" showCloseButton={false}><DialogHeader><DialogTitle>{block?.name}의 PoR을 변경할까요?</DialogTitle><DialogDescription>이 블록의 기준 버전이 바뀌며 TOP과 담당자가 같은 결과를 확인합니다.</DialogDescription></DialogHeader><div className="ws-change-preview"><div><span>현재 PoR</span><strong>{por?.version?.net_ver || '미지정'}</strong><code>{por?.version?.revision || '—'}</code></div><ArrowRight size={19} /><div><span>변경할 버전</span><strong>{version?.net_ver} · {version?.layout}</strong><code>{version?.revision}</code></div></div><label className="ws-reason-label" htmlFor="ws-por-reason">변경 사유<textarea id="ws-por-reason" value={reason} maxLength={500} placeholder="기준 버전을 변경하는 이유를 남겨 주세요." onChange={e => { setReason(e.target.value); requestId.current = null }} disabled={saving} /></label><p className="ws-muted ws-confirm-note">지정자와 시각이 이력에 저장됩니다. 기존 검사 결과와 다른 블록의 PoR은 유지됩니다.</p>{saveError && <p className="ws-error" role="alert">{saveError}</p>}<div className="ws-dialog-actions"><Button variant="outline" onClick={() => setConfirm(false)} disabled={saving}>취소</Button><Button onClick={savePor} disabled={saving || !reason.trim()}>{saving ? '저장 중…' : 'PoR 지정 저장'}</Button></div></DialogContent></Dialog>
    <Dialog open={!!selectedReport || reportLoading} onOpenChange={open => { if (!open) { detailRequest.current++; setSelectedReport(null); setReportLoading(false) } }}>
      <DialogContent className="ws-report-dialog" showCloseButton={false}>
        <DialogHeader><DialogTitle>{selectedReport?.id || '검사 근거'} · 보고서</DialogTitle><DialogDescription>{project?.name} / {block?.name} · {version?.net_ver} · {version?.layout}{eco && ` / ${eco}`}</DialogDescription></DialogHeader>
        {reportLoading ? <Loading /> : selectedReport && <>
          <div className="ws-report-meta"><span>규칙 판정</span><Status value={selectedReport.check_status} /><Status value={selectedReport.parse_status} /><span>{labels[selectedReport.family]} · {selectedReport.rule}</span></div>
          <Tabs defaultValue="source" key={selectedReport.id} className="ws-report-tabs">
            <TabsList variant="line"><TabsTrigger value="source">보고서·근거</TabsTrigger><TabsTrigger value="pipeline">Clustering·그룹 판정</TabsTrigger><TabsTrigger value="review">사용자 판정·AI review</TabsTrigger></TabsList>
            <TabsContent value="source"><div className="ws-report-dialog-scroll">{selectedReport.blockers.length > 0 && <div className="ws-report-blockers"><strong>파싱 완료를 막는 사유</strong><ul>{selectedReport.blockers.map(code => <li key={code}>{code}</li>)}</ul></div>}
              {selectedReport.source_storage === 'PARTS_V2' ? <>
                <h3>수신한 검사 근거 {n(selectedReport.record_count)}건</h3>
                <p className="ws-muted">로그를 페이지로 나누어 저장했습니다. 검토 화면에서 전체 로그를 확인하고 그룹 분류를 시작할 수 있습니다. 원본 report 파일은 VWP에 보관됩니다.</p>
                {!!selectedReport.delivery_blockers?.length && <p className="ws-error">검토 대기: {selectedReport.delivery_blockers.join(', ')}</p>}
                <Button onClick={() => { const id = selectedReport.id; detailRequest.current++; setSelectedReport(null); setWorkbenchId(id) }}>전체 로그·검토 열기<ArrowRight size={14} /></Button>
              </> : <><h3>추출된 검사 근거</h3>{selectedReport.records?.length ? selectedReport.records.map(record => <article className="ws-evidence" key={record.id}><header><Status value={record.check_status} /><span>원문 {record.line_start}–{record.line_end}행</span></header><dl>{Object.entries(record.fields).filter(([key]) => key !== 'message').map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>)}</dl><pre>{record.raw}</pre></article>) : <p className="ws-muted">보고서가 미수신되어 추출 근거가 없습니다.</p>}<h3>보고서 전체 원문</h3><pre>{selectedReport.raw_report || '예상 검사 목록에는 포함되지만 원본 보고서가 아직 수신되지 않았습니다.'}</pre><p className="ws-muted">공개 형식을 참고해 작성한 예시 보고서입니다. 실제 사내 report는 포함하지 않습니다.</p></>}
            </div></TabsContent>
            <TabsContent value="pipeline" forceMount className="data-[state=inactive]:hidden"><div className="ws-report-dialog-scroll"><p className="ws-muted">전체 로그 분류와 그룹별 근거 확인을 순서대로 진행합니다.</p><Button onClick={() => { const id = selectedReport.id; detailRequest.current++; setSelectedReport(null); setWorkbenchId(id) }}>그룹별 근거·순차 검토 열기<ArrowRight size={14} /></Button></div></TabsContent>
            <TabsContent value="review" forceMount className="data-[state=inactive]:hidden"><div className="ws-report-dialog-scroll"><WorkspaceReview report={selectedReport} session={session} onSaved={reviewSaved} onExpired={onExpired} /></div></TabsContent>
          </Tabs>
          <div className="ws-dialog-actions"><Button variant="outline" disabled={!selectedReport.raw_report} onClick={downloadReport}><Download size={15} />보고서 다운로드</Button><Button onClick={() => { detailRequest.current++; setSelectedReport(null) }}>닫기</Button></div>
        </>}
      </DialogContent>
    </Dialog>
  </div>
}

function PorSummary({ por, canAssign }: { por: WorkspacePor; canAssign: boolean }) {
  return <section className="ws-por-summary" aria-label="블록의 현재 PoR"><div className="ws-por-summary-label"><Layers3 size={18} /><span>현재 PoR</span><small>개정 {por.revision}</small></div><div>{por.version ? <><strong>{por.version.net_ver}<span>{por.version.layout}</span></strong><code>{por.version.revision}</code></> : <><strong>아직 지정하지 않았습니다.</strong><p>{canAssign ? '아래에서 버전을 선택하고 이 블록의 PoR로 지정하세요.' : '블록 담당자가 기준 버전을 지정하면 이곳에 표시됩니다.'}</p></>}</div><div className="ws-por-assignee"><span>{por.assigned_by?.name || '지정 대기'}</span><small>{time(por.assigned_at)}</small></div></section>
}
