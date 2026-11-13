import { Activity, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode, RefObject } from 'react'
import App from './App'
import Workspace from './Workspace'
import type { Session } from './types'
import type { WorkspaceSession } from './workspaceApi'
import { DashboardAuthContext, DashboardRequestError, useDashboardAuth } from './useDashboardAuth'
import AreaPower from './areaPower/App'
import { appPathname, appUrl } from './appUrl'
import { DashboardNavigationContext, DashboardSidebarContext } from './dashboardMode'
import type { DashboardNavigate } from './dashboardMode'
import { AtlasProvider } from './atlasContext'
import AtlasShell from './AtlasShell'
import AtlasHome from './AtlasHome'

type DashboardPage = 'home' | 'review' | 'welcome' | 'admin' | 'workbench' | 'area-power'
type Destination = { page: DashboardPage; href: string }
type ScrollPositions = RefObject<Map<DashboardPage, { x: number; y: number }>>
type NavigationState = { route: Destination; visited: DashboardPage[]; epoch: number; initialWorkspace?: WorkspaceSession }
const pages: Record<DashboardPage, true> = { home: true, review: true, welcome: true, admin: true, workbench: true, 'area-power': true }

function destination(href: string, demo = false): Destination {
  const url = new URL(appUrl(href), location.origin)
  const requested = url.searchParams.get('page') as DashboardPage
  const page = Object.hasOwn(pages, requested) ? requested : 'home'
  if (url.origin !== location.origin || appPathname(url.pathname) !== '/'
    || ['flow', 'dataset'].some(key => url.searchParams.has(key))
    || !demo && url.searchParams.has('demo')
    || demo && url.searchParams.has('demo') && url.searchParams.get('demo') !== '1') throw new Error('이동할 화면을 확인해 주세요.')
  if (demo) url.searchParams.set('demo', '1')
  if (page === 'area-power') url.searchParams.delete('view')
  return { page, href: url.pathname + url.search }
}

/** Activity retains drafts/DOM, but disconnects effects and focusable content while hidden. */
function DashboardSurface({ page, positions, children }: { page: DashboardPage; positions: ScrollPositions; children: ReactNode }) {
  const surface = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const root = surface.current!
    const scroll = positions.current.get(page) ?? { x: 0, y: 0 }
    window.scrollTo({ left: scroll.x, top: scroll.y, behavior: 'instant' })
    // Keep keyboard focus in the visible screen, including a first visit whose
    // authenticated contents arrive after this boundary becomes visible.
    const focus = () => {
      const target = root.querySelector<HTMLElement>('main')
      if (!root.querySelector('aside') || !target || !target.getClientRects().length) return false
      target.setAttribute('tabindex', '-1')
      target.focus({ preventScroll: true })
      return true
    }
    const observer = new MutationObserver(() => { if (focus()) observer.disconnect() })
    if (!focus()) observer.observe(root, { childList: true, subtree: true })
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
    const animations = reduced ? [] : Array.from(root.querySelectorAll('main, aside nav')).map(element =>
      element.animate([{ opacity: .65, transform: `translateX(${page === 'admin' ? 8 : -8}px)` }, { opacity: 1, transform: 'translateX(0)' }], { duration: 180, easing: 'ease-out' }))
    return () => {
      observer.disconnect()
      animations.forEach(animation => animation.cancel())
    }
  }, [page, positions])
  return <div ref={surface} className="dashboard-surface" data-dashboard-page={page}>{children}</div>
}

export default function DashboardNavigation({ demo = false, demoToolbar, demoAccount }: { demo?: boolean; demoToolbar?: ReactNode; demoAccount?: ReactNode } = {}) {
  const auth = useDashboardAuth(true)
  const [state, setState] = useState<NavigationState>(() => {
    // The explicit entry fixes the transport for this document; navigation cannot switch it.
    const initial = new URL(location.href)
    if (!demo) for (const key of ['demo', 'flow', 'dataset']) initial.searchParams.delete(key)
    const route = destination(initial.href, demo)
    return { route, visited: [route.page], epoch: 0 }
  })
  const current = useRef(state.route)
  const workbenchSearch = useRef(state.route.page === 'workbench' ? new URL(state.route.href, location.origin).search : '')
  const verified = useRef<string | null>(null)
  const pending = useRef<AbortController | null>(null)
  const prepared = useRef(new Set(state.visited))
  const positions = useRef(new Map<DashboardPage, { x: number; y: number }>())
  const [error, setError] = useState('')
  const sidebar = useState(false)

  const change = useCallback(async (href: string, sourceUser?: string, historyMode: 'push' | 'pop' = 'push', reset = false) => {
    let next = destination(href, demo)
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    let discard = reset
    let initialWorkspace: WorkspaceSession | undefined
    if (!reset && next.page !== current.current.page) {
      // Crossing tasks never grants permissions, even when restoring retained drafts.
      let session: Session | null = null
      try { session = await auth.request<Session>('/api/session', undefined, false, controller.signal) }
      catch (cause) {
        if (!(cause instanceof DashboardRequestError) || cause.status !== 401 && cause.status !== 403) throw cause
        next = destination('/?page=welcome', demo); discard = true; verified.current = null
      }
      if (session) {
        if (demo ? session.access_mode !== 'memory_demo' : session.auth_method !== auth.config?.mode) throw new Error('현재 로그인 방식을 다시 확인해 주세요.')
        const ready = session.auth_method === 'sso' && session.authorization_ready === true
        const local = session.auth_method === 'local_token'
        const memory = demo && session.access_mode === 'memory_demo'
        const signature = JSON.stringify([session.user_id, session.auth_method, session.is_admin === true, session.can_read_business === true, ready])
        discard = (!!sourceUser && sourceUser !== session.user_id) || (verified.current !== null && verified.current !== signature)
        verified.current = signature
        if ((!ready && !local && !memory) || next.page === 'admin' && (!ready || session.is_admin !== true)
          || next.page === 'workbench' && !local && !memory && (!ready || session.can_read_business !== true)) {
          next = destination('/?page=welcome', demo); discard = true
        }
        if (current.current.page === 'admin' && session.is_admin !== true) discard = true
        if (ready && ['review', 'workbench', 'area-power'].includes(current.current.page) && session.can_read_business !== true) discard = true
        if (next.page === 'workbench' && ready && (discard || !prepared.current.has(next.page))) {
          const value = await auth.request<WorkspaceSession>('/api/workbench/session', undefined, false, controller.signal)
          if (value.environment !== 'PRODUCTION_SSO' || value.user.id !== session.user_id) throw new Error('현재 계정을 다시 확인해 주세요.')
          initialWorkspace = value
        }
      }
    }
    controller.signal.throwIfAborted()
    positions.current.set(current.current.page, { x: window.scrollX, y: window.scrollY })
    if (discard) positions.current.clear()
    if (discard) prepared.current.clear()
    prepared.current.add(next.page)
    if (historyMode === 'push') history.pushState(null, '', next.href)
    else if (next.href !== location.pathname + location.search) history.replaceState(null, '', next.href)
    if (next.page === 'workbench') workbenchSearch.current = new URL(next.href, location.origin).search
    current.current = next
    setError('')
    setState(previous => ({ route: next, visited: discard ? [next.page] : Array.from(new Set([...previous.visited, next.page])), epoch: previous.epoch + (discard ? 1 : 0), initialWorkspace: initialWorkspace ?? (discard ? undefined : previous.initialWorkspace) }))
    document.title = `${demo ? '합성 데모 · ' : ''}${next.page === 'home' ? '홈 · 프로젝트' : next.page === 'area-power' ? '프로젝트 비교' : next.page === 'admin' ? '서비스 · 권한 관리' : next.page === 'workbench' ? '로그 워크스페이스' : next.page === 'welcome' ? '내 계정 · 권한' : '수신 로그 대장'} | Design Atlas`
  }, [auth.request, auth.config, demo])
  const navigate = useCallback<DashboardNavigate>((href, sourceUser, reset) => change(href, sourceUser, 'push', reset), [change])
  const replaceRoute = useCallback((href: string) => {
    const next = destination(href, demo)
    if (next.page !== current.current.page) throw new Error('현재 화면의 주소만 갱신할 수 있습니다.')
    if (next.href === current.current.href) return
    history.replaceState(null, '', next.href)
    if (next.page === 'workbench') workbenchSearch.current = new URL(next.href, location.origin).search
    current.current = next
    setState(previous => ({ ...previous, route: next }))
  }, [demo])

  useEffect(() => {
    if (new URL(location.href).searchParams.get('page') === 'area-power') {
      const canonical = destination(location.href, demo)
      history.replaceState(null, '', canonical.href)
    }
    const restoration = history.scrollRestoration
    history.scrollRestoration = 'manual'
    const pop = () => { void change(location.href, undefined, 'pop').catch(error => {
      if (error instanceof DOMException && error.name === 'AbortError') return
      history.replaceState(null, '', current.current.href)
      setError(error instanceof Error ? error.message : '화면을 열지 못했습니다. 다시 시도해 주세요.')
    }) }
    window.addEventListener('popstate', pop)
    return () => { window.removeEventListener('popstate', pop); pending.current?.abort(); history.scrollRestoration = restoration }
  }, [change])

  return <DashboardAuthContext value={auth}><DashboardNavigationContext value={navigate}><DashboardSidebarContext value={sidebar}>
    <AtlasProvider demo={demo} routeSearch={new URL(state.route.href, location.origin).search} replaceRoute={replaceRoute}><AtlasShell demoToolbar={demoToolbar}>
      {state.visited.map(page => <Activity key={`${state.epoch}:${page}`} mode={state.route.page === page ? 'visible' : 'hidden'}>
        <DashboardSurface page={page} positions={positions}>{page === 'home' ? <AtlasHome /> : page === 'workbench' ? auth.session?.auth_method === 'local_token' ? <main className="atlas-home"><h1>회사 SSO 전용 로그 워크스페이스</h1><p>현재는 실제 로컬 모드입니다. 회사 계정·권한이 필요한 검토 워크스페이스를 사용할 수 없습니다. 공통 범위의 실제 수신 보고서는 수신 로그 대장에서 확인하세요.</p><button className="atlas-button" onClick={() => { const url = new URL(location.href); url.searchParams.set('page', 'review'); void navigate(url.pathname + url.search).catch(cause => setError(String(cause))) }}>수신 로그 대장 열기</button></main> : <Workspace routeSearch={workbenchSearch.current} initialSession={state.initialWorkspace} /> : page === 'area-power' ? <AreaPower /> : demo && (page === 'welcome' || page === 'admin') ? demoAccount : <App routePage={page} />}</DashboardSurface>
      </Activity>)}
    </AtlasShell></AtlasProvider>
    {error && <p className="dashboard-navigation-error" role="alert">{error}<button type="button" onClick={() => setError('')}>닫기</button></p>}
  </DashboardSidebarContext></DashboardNavigationContext></DashboardAuthContext>
}
