import { Activity, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode, RefObject } from 'react'
import App from './App'
import Workspace from './Workspace'
import type { AuthConfig, Session } from './types'
import type { WorkspaceSession } from './workspaceApi'
import { validAuthConfig } from './useDashboardAuth'
import type { DashboardAuthSnapshot } from './useDashboardAuth'
import { DashboardNavigationContext, DashboardSidebarContext } from './dashboardMode'
import type { DashboardNavigate } from './dashboardMode'

type DashboardPage = 'review' | 'welcome' | 'admin' | 'workbench'
type Destination = { page: DashboardPage; href: string }
type ScrollPositions = RefObject<Map<DashboardPage, { x: number; y: number }>>
type NavigationState = { route: Destination; visited: DashboardPage[]; epoch: number; initialAuth?: DashboardAuthSnapshot; initialWorkspace?: WorkspaceSession }
const pages = new Set<DashboardPage>(['review', 'welcome', 'admin', 'workbench'])

function destination(href: string): Destination {
  const url = new URL(href, location.origin)
  const requested = url.searchParams.get('page') as DashboardPage
  const page = pages.has(requested) ? requested : 'review'
  if (url.origin !== location.origin || url.pathname !== '/'
    || ['demo', 'flow', 'dataset'].some(key => url.searchParams.has(key))) throw new Error('이동할 화면을 확인해 주세요.')
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

export default function DashboardNavigation() {
  const [state, setState] = useState<NavigationState>(() => {
    // An explicit production workbench URL keeps its server boundary even if
    // obsolete demo parameters were appended to a bookmark.
    const initial = new URL(location.href)
    for (const key of ['demo', 'flow', 'dataset']) initial.searchParams.delete(key)
    const route = destination(initial.href)
    return { route, visited: [route.page], epoch: 0 }
  })
  const current = useRef(state.route)
  const workbenchSearch = useRef(state.route.page === 'workbench' ? new URL(state.route.href, location.origin).search : '')
  const verified = useRef<string | null>(null)
  const pending = useRef<AbortController | null>(null)
  const configuration = useRef<AuthConfig | null>(null)
  const prepared = useRef(new Set(state.visited))
  const positions = useRef(new Map<DashboardPage, { x: number; y: number }>())
  const [error, setError] = useState('')
  const sidebar = useState(false)

  const change = useCallback(async (href: string, sourceUser?: string, historyMode: 'push' | 'pop' = 'push', reset = false) => {
    let next = destination(href)
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    let discard = reset
    let initialAuth: DashboardAuthSnapshot | undefined
    let initialWorkspace: WorkspaceSession | undefined
    if (!reset && next.page !== current.current.page) {
      // A mode is presentation, never an authorization grant. Recheck cookies on
      // every crossing, including browser Back/Forward, before restoring a cache.
      const response = await fetch('/api/session', { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal })
      if (!response.ok) {
        if (response.status !== 401 && response.status !== 403) throw new Error('현재 로그인과 권한을 확인하지 못했습니다. 다시 시도해 주세요.')
        next = destination('/?page=welcome'); discard = true; verified.current = null
      } else {
        const session = await response.json() as Session
        controller.signal.throwIfAborted()
        const ready = session.auth_method === 'sso' && session.authorization_ready === true
        const signature = JSON.stringify([session.user_id, session.is_admin === true, session.can_read_business === true, ready])
        discard = (!!sourceUser && sourceUser !== session.user_id) || (verified.current !== null && verified.current !== signature)
        verified.current = signature
        if (!ready || next.page === 'admin' && session.is_admin !== true
          || next.page === 'workbench' && session.can_read_business !== true) {
          next = destination('/?page=welcome'); discard = true
        }
        if (current.current.page === 'admin' && session.is_admin !== true) discard = true
        if (['review', 'workbench'].includes(current.current.page) && session.can_read_business !== true) discard = true
        // Prepare a newly visited shell before replacing the current screen, so
        // verified mode changes never flash the separate login/discovery page.
        if (ready && discard && next.page !== 'workbench' && configuration.current) initialAuth = { config: configuration.current, session }
        if (ready && !discard && !prepared.current.has(next.page)) {
          if (next.page === 'workbench') {
            const response = await fetch('/api/workbench/session', { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal })
            if (!response.ok) throw new Error('사용자 화면을 준비하지 못했습니다. 다시 시도해 주세요.')
            const value = await response.json() as WorkspaceSession
            if (value.environment !== 'PRODUCTION_SSO' || value.user.id !== session.user_id) throw new Error('현재 계정을 다시 확인해 주세요.')
            initialWorkspace = value
          } else {
            if (!configuration.current) {
              const response = await fetch('/api/auth-config', { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal })
              if (!response.ok) throw new Error('로그인 설정을 확인하지 못했습니다. 다시 시도해 주세요.')
              const value = await response.json() as AuthConfig
              if (!validAuthConfig(value) || value.mode !== 'sso') throw new Error('로그인 설정을 확인해 주세요.')
              configuration.current = value
            }
            initialAuth = { config: configuration.current, session }
          }
        }
      }
    }
    controller.signal.throwIfAborted()
    // Capture before React changes either Activity's display. Capturing during
    // its cleanup can include scroll anchoring caused by revealing its sibling.
    positions.current.set(current.current.page, { x: window.scrollX, y: window.scrollY })
    if (discard) positions.current.clear()
    if (discard) prepared.current.clear()
    prepared.current.add(next.page)
    if (historyMode === 'push') history.pushState(null, '', next.href)
    else if (next.href !== location.pathname + location.search) history.replaceState(null, '', next.href)
    if (next.page === 'workbench') workbenchSearch.current = new URL(next.href, location.origin).search
    current.current = next
    setError('')
    setState(previous => ({ route: next, visited: discard ? [next.page] : Array.from(new Set([...previous.visited, next.page])), epoch: previous.epoch + (discard ? 1 : 0), initialAuth: initialAuth ?? (discard ? undefined : previous.initialAuth), initialWorkspace: initialWorkspace ?? (discard ? undefined : previous.initialWorkspace) }))
    document.title = `${next.page === 'admin' ? '프로젝트·권한 관리' : next.page === 'workbench' ? 'AI 검토 워크스페이스' : next.page === 'welcome' ? '내 프로젝트·권한' : 'Sanity 검토'} | Sanity Lens AI`
  }, [])
  const navigate = useCallback<DashboardNavigate>((href, sourceUser, reset) => change(href, sourceUser, 'push', reset), [change])

  useEffect(() => {
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

  return <DashboardNavigationContext value={navigate}><DashboardSidebarContext value={sidebar}>
    {state.visited.map(page => <Activity key={`${state.epoch}:${page}`} mode={state.route.page === page ? 'visible' : 'hidden'}>
      <DashboardSurface page={page} positions={positions}>{page === 'workbench' ? <Workspace routeSearch={workbenchSearch.current} initialSession={state.initialWorkspace} /> : <App routePage={page} initialAuth={state.initialAuth} />}</DashboardSurface>
    </Activity>)}
    {error && <p className="dashboard-navigation-error" role="alert">{error}<button type="button" onClick={() => setError('')}>닫기</button></p>}
  </DashboardSidebarContext></DashboardNavigationContext>
}
