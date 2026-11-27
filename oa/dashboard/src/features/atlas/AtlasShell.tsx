import { useCallback, useState } from 'react'
import type { ReactNode } from 'react'
import { BookOpen, FolderKanban, GitCompareArrows, Home, Layers3, LayoutGrid, LogOut, Menu, Settings2, ShieldCheck, X } from 'lucide-react'
import { WorkspaceBrand } from '@/components/layout/WorkspaceBrand'
import { ColorModeControl } from '@/components/layout/ColorModeControl'
import PersonalAiToken from '@/features/ai/PersonalAiToken'
import { useDashboardAuth } from '@/features/auth/useDashboardAuth'
import { useDashboardNavigation } from '@/lib/dashboardMode'
import { useWorkspaceNavigation } from '@/features/workspace/useWorkspaceNavigation'
import { DashboardLogin } from '@/features/auth/DashboardLogin'
import { useAtlas } from './atlasContext'

type NavigationLink = { title: string; path: string; page: string; icon: typeof Home }
const navigationGroups: { id: string; title: string; links: NavigationLink[] }[] = [
  { id: 'home', title: '', links: [
    { title: '홈 · 프로젝트', path: '/', page: 'home', icon: Home },
    { title: '버전 · 자료 현황', path: '/?page=versions', page: 'versions', icon: Layers3 },
  ] },
  { id: 'logs', title: '로그 검토', links: [
    { title: '로그 검토', path: '/?page=workbench', page: 'workbench', icon: BookOpen },
    { title: '수신 로그 대장', path: '/?page=review', page: 'review', icon: FolderKanban },
  ] },
  { id: 'area-power', title: 'Area/Power', links: [
    { title: '프로젝트 비교', path: '/?page=area-power', page: 'area-power', icon: GitCompareArrows },
  ] },
  { id: 'floorplan', title: 'Floorplan', links: [
    { title: 'Floorplan 리뷰', path: '/?page=floorplan', page: 'floorplan', icon: LayoutGrid },
  ] },
  { id: 'account', title: '', links: [
    { title: '내 계정 · 권한', path: '/?page=welcome', page: 'welcome', icon: ShieldCheck },
  ] },
]
const links = navigationGroups.flatMap(group => group.links)
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
  const isAdmin = auth.session.is_admin
  const params = new URLSearchParams(atlas.routeSearch)
  const page = params.get('page') || 'home'
  const current = links.find(link => link.page === page)
  const go = (path: string) => { close(); setError(''); void navigate(atlas.href(path), auth.session?.user_id).catch(cause => setError(cause instanceof Error ? cause.message : '화면을 열지 못했습니다.')) }
  return <div className="atlas-shell" data-page={page}><a className="skip-link" href="#atlas-content">본문으로 건너뛰기</a>
    {mobile && <button className="atlas-scrim" aria-label="메뉴 닫기" onClick={close} />}
    <aside className={`atlas-sidebar${mobile ? ' is-open' : ''}`} id="atlas-menu" ref={sidebarRef} aria-label="Design Atlas 탐색" role={isMobile && mobile ? 'dialog' : undefined} aria-modal={isMobile && mobile || undefined} inert={isMobile && !mobile}>
      <div className="atlas-brand-row" onClick={event => { if (event.target instanceof Element && event.target.closest('a') && !event.button && !event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey) { event.preventDefault(); go('/') } }}><WorkspaceBrand href={atlas.href('/')} /><button className="atlas-mobile" onClick={close} aria-label="메뉴 닫기"><X size={20} /></button></div>
      <nav aria-label="업무 메뉴">{navigationGroups.map(group => <section className="atlas-nav-group" key={group.id} aria-label={group.title ? `${group.title} 메뉴` : group.id === 'home' ? '공통 홈' : '계정 및 관리'}>
        {group.title && <h2 className="atlas-nav-heading">{group.title}</h2>}
        {group.links.map(link => <a key={link.path} href={atlas.href(link.path)} aria-current={page === link.page ? 'page' : undefined} onClick={event => { if (event.button || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return; event.preventDefault(); go(link.path) }}><link.icon size={18} />{link.title}</a>)}
        {group.id === 'account' && isAdmin && <a href={atlas.href('/?page=admin')} aria-current={page === 'admin' ? 'page' : undefined} onClick={event => { if (event.button || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return; event.preventDefault(); go('/?page=admin') }}><Settings2 size={18} />서비스 · 권한 관리</a>}
      </section>)}</nav>
      <div className="atlas-profile"><span className="atlas-avatar">{auth.session.reviewer.slice(0, 2)}</span><div><strong>{auth.session.reviewer}</strong><small>{atlas.demo ? '합성 데모 · 탭 메모리' : auth.session.auth_method === 'local_token' ? '실제 로컬 모드 · 데모 아님' : '회사 SSO · 권한 적용'}</small></div><button disabled={auth.signingOut} onClick={() => void logout().catch(cause => setError(String(cause)))} aria-label={atlas.demo ? '데모 종료' : '로그아웃'}><LogOut size={17} /></button></div>
    </aside>
    <div className="atlas-body" inert={isMobile && mobile}><header className="atlas-header"><div><button className="atlas-mobile" ref={triggerRef} onClick={() => setMobile(true)} aria-label="메뉴 열기" aria-expanded={mobile} aria-controls="atlas-menu"><Menu size={21} /></button><span>Design Atlas</span><b>/</b><strong>{current?.title || '서비스 관리'}</strong></div><div>{auth.session.auth_method === 'sso' && <PersonalAiToken key={auth.session.user_id} />}<ColorModeControl /></div></header>
      {demoToolbar}
      {(error || atlas.error) && <div className="atlas-message error" role="alert">{error || atlas.error}</div>}
      {atlas.scope.projectId && atlas.catalog && !atlas.project && <div className="atlas-message error" role="alert">이 프로젝트는 현재 카탈로그에 없거나 접근 권한이 없습니다. 다른 범위를 선택하세요.</div>}
      {atlas.project && atlas.scope.blockId && !atlas.block && <div className="atlas-message error" role="alert">선택한 블록이 이 프로젝트 범위에 없습니다.</div>}
      {atlas.block && atlas.scope.versionId && !atlas.version && <div className="atlas-message error" role="alert">선택한 버전이 이 블록 범위에 없습니다.</div>}
      <div id="atlas-content" className="atlas-content" tabIndex={-1}>{children}</div>
    </div>
  </div>
}
