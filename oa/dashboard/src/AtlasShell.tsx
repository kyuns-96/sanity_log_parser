import { useCallback, useState } from 'react'
import type { ReactNode } from 'react'
import { Bell, BookOpen, Boxes, FileClock, FolderKanban, GitCompareArrows, Home, LogOut, Menu, Settings2, ShieldCheck, Target, UploadCloud, Workflow, X } from 'lucide-react'
import { WorkspaceBrand } from './components/WorkspaceBrand'
import { ColorModeControl } from './components/ColorModeControl'
import PersonalAiToken from './PersonalAiToken'
import { useDashboardAuth } from './useDashboardAuth'
import { useDashboardNavigation } from './dashboardMode'
import { useWorkspaceNavigation } from './useWorkspaceNavigation'
import { DashboardLogin } from './DashboardLogin'
import { useAtlas } from './atlasContext'

const links = [
  { title: '홈 · 프로젝트', path: '/', page: 'home', icon: Home },
  { title: '로그 검토', path: '/?page=workbench', page: 'workbench', icon: BookOpen },
  { title: '수신 로그 대장', path: '/?page=review', page: 'review', icon: FolderKanban },
  { title: '구성·전력 개요', path: '/?page=area-power', page: 'area-power', view: 'overview', icon: Boxes },
  { title: '업로드 · 실행', path: '/?page=area-power&view=uploads', page: 'area-power', view: 'uploads', icon: UploadCloud },
  { title: '수치 · AI 분석', path: '/?page=area-power&view=comparisons', page: 'area-power', view: 'comparisons', icon: GitCompareArrows },
  { title: '추적 대상 · 매핑', path: '/?page=area-power&view=targets', page: 'area-power', view: 'targets', icon: Target },
  { title: '설계 변경', path: '/?page=area-power&view=changes', page: 'area-power', view: 'changes', icon: FileClock },
  { title: '알림', path: '/?page=area-power&view=alerts', page: 'area-power', view: 'alerts', icon: Bell },
  { title: '수치 작업', path: '/?page=area-power&view=jobs', page: 'area-power', view: 'jobs', icon: Workflow },
  { title: '내 계정 · 권한', path: '/?page=welcome', page: 'welcome', icon: ShieldCheck },
]
export default function AtlasShell({ children, demoToolbar }: { children: ReactNode; demoToolbar?: ReactNode }) {
  const atlas = useAtlas()!
  const auth = useDashboardAuth(true)
  const navigate = useDashboardNavigation()
  const [mobile, setMobile] = useState(false)
  const [error, setError] = useState('')
  const close = useCallback(() => setMobile(false), [])
  const { isMobile, sidebarRef, triggerRef } = useWorkspaceNavigation(mobile, close, !!auth.session)
  const logout = async () => { if (await auth.logout()) await navigate('/', undefined, true) }
  if (!auth.session || auth.session.auth_method === 'sso' && !auth.session.authorization_ready) return <DashboardLogin config={auth.config} session={auth.session} checking={auth.checking} signingOut={auth.signingOut} error={auth.error} accountProblem={auth.accountProblem} onToken={auth.loginWithToken} onRetry={auth.retry} onLogout={logout} />
  const params = new URLSearchParams(atlas.routeSearch)
  const page = params.get('page') || 'home'
  const view = params.get('view') || 'overview'
  const current = links.find(link => link.page === page && (!link.view || link.view === view))
  const go = (path: string) => { close(); setError(''); void navigate(atlas.href(path), auth.session?.user_id).catch(cause => setError(cause instanceof Error ? cause.message : '화면을 열지 못했습니다.')) }
  const choose = (projectId: string, blockId: string, versionId: string) => { setError(''); void atlas.selectScope({ projectId, blockId, versionId }).catch(cause => setError(cause instanceof Error ? cause.message : '범위를 변경하지 못했습니다.')) }
  return <div className="atlas-shell"><a className="skip-link" href="#atlas-content">본문으로 건너뛰기</a>
    {mobile && <button className="atlas-scrim" aria-label="메뉴 닫기" onClick={close} />}
    <aside className={`atlas-sidebar${mobile ? ' is-open' : ''}`} id="atlas-menu" ref={sidebarRef} aria-label="Design Atlas 탐색" role={isMobile && mobile ? 'dialog' : undefined} aria-modal={isMobile && mobile || undefined} inert={isMobile && !mobile}>
      <div className="atlas-brand-row" onClick={event => { if (event.target instanceof Element && event.target.closest('a') && !event.button && !event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey) { event.preventDefault(); go('/') } }}><WorkspaceBrand href={atlas.href('/')} /><button className="atlas-mobile" onClick={close} aria-label="메뉴 닫기"><X size={20} /></button></div>
      <p className="atlas-nav-label">DESIGN WORKSPACE</p><nav>{links.map(link => <a key={link.path} href={atlas.href(link.path)} aria-current={page === link.page && (!link.view || link.view === view) ? 'page' : undefined} onClick={event => { if (event.button || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return; event.preventDefault(); go(link.path) }}><link.icon size={18} />{link.title}</a>)}{auth.session.is_admin && <a href={atlas.href('/?page=admin')} aria-current={page === 'admin' ? 'page' : undefined} onClick={event => { if (event.button || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return; event.preventDefault(); go('/?page=admin') }}><Settings2 size={18} />서비스 · 권한 관리</a>}</nav>
      <div className="atlas-profile"><span className="atlas-avatar">{auth.session.reviewer.slice(0, 2)}</span><div><strong>{auth.session.reviewer}</strong><small>{atlas.demo ? '합성 데모 · 탭 메모리' : auth.session.auth_method === 'local_token' ? '실제 로컬 모드 · 데모 아님' : '회사 SSO · 권한 적용'}</small></div><button disabled={auth.signingOut} onClick={() => void logout().catch(cause => setError(String(cause)))} aria-label={atlas.demo ? '데모 종료' : '로그아웃'}><LogOut size={17} /></button></div>
    </aside>
    <div className="atlas-body" inert={isMobile && mobile}><header className="atlas-header"><div><button className="atlas-mobile" ref={triggerRef} onClick={() => setMobile(true)} aria-label="메뉴 열기" aria-expanded={mobile} aria-controls="atlas-menu"><Menu size={21} /></button><span>Design Atlas</span><b>/</b><strong>{current?.title || '서비스 관리'}</strong></div><div>{auth.session.auth_method === 'sso' && <PersonalAiToken key={auth.session.user_id} />}<ColorModeControl /></div></header>
      {demoToolbar}
      <section className="atlas-scope" aria-label="공통 프로젝트 범위"><span className="atlas-scope-title"><FolderKanban size={17} />공통 범위</span><label>프로젝트<select value={atlas.scope.projectId} onChange={event => choose(event.target.value, '', '')}><option value="">프로젝트 선택</option>{atlas.catalog?.projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label><label>블록<select disabled={!atlas.project} value={atlas.scope.blockId} onChange={event => choose(atlas.scope.projectId, event.target.value, '')}><option value="">블록 선택</option>{atlas.project?.blocks.map(block => <option key={block.id} value={block.id}>{block.name}</option>)}</select></label><label>버전<select disabled={!atlas.block} value={atlas.scope.versionId} onChange={event => choose(atlas.scope.projectId, atlas.scope.blockId, event.target.value)}><option value="">버전 선택</option>{atlas.block?.versions.map(version => <option key={version.id} value={version.id}>{version.net_ver} · {version.revision} · {version.layout}</option>)}</select></label><button className="atlas-button secondary" disabled={atlas.loading} onClick={() => void atlas.reload().catch(() => undefined)}>{atlas.loading ? '조회 중…' : '범위 새로고침'}</button></section>
      {(error || atlas.error) && <div className="atlas-message error" role="alert">{error || atlas.error}</div>}
      {atlas.scope.projectId && atlas.catalog && !atlas.project && <div className="atlas-message error" role="alert">이 프로젝트는 현재 카탈로그에 없거나 접근 권한이 없습니다. 다른 범위를 선택하세요.</div>}
      {atlas.project && atlas.scope.blockId && !atlas.block && <div className="atlas-message error" role="alert">선택한 블록이 이 프로젝트 범위에 없습니다.</div>}
      {atlas.block && atlas.scope.versionId && !atlas.version && <div className="atlas-message error" role="alert">선택한 버전이 이 블록 범위에 없습니다.</div>}
      <div id="atlas-content" className="atlas-content" tabIndex={-1}>{children}</div>
    </div>
  </div>
}
