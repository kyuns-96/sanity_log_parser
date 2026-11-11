import { useCallback, useEffect, useMemo, useState } from 'react'
import DashboardNavigation from './DashboardNavigation'
import { DashboardAuthContext } from './useDashboardAuth'
import type { DashboardAuth } from './useDashboardAuth'
import type { Session } from './types'
import { appUrl } from './appUrl'
import { useAtlas } from './atlasContext'
import type { AtlasCatalog } from './atlasContext'
import { useDashboardNavigation } from './dashboardMode'
import { workspaceDemoRequest } from './workspaceDemo'
import { workspaceDemoUsers } from './workspaceDemoAccounts'
import { areaDemoRequest } from './areaPower/demo'

/** Imported only by the explicit demo entry. No real authentication or business transport. */
async function demoRequest<T>(path: string, body?: Record<string, unknown> | string, plain: boolean | 'blob' = false, signal?: AbortSignal): Promise<T> {
  signal?.throwIfAborted()
  const url = new URL(path, 'http://memory.demo')
  if (url.origin !== 'http://memory.demo') throw new Error('합성 데모에서 외부 서버 요청은 허용하지 않습니다.')
  if (url.pathname.startsWith('/api/area-power/')) return areaDemoRequest<T>(path, body, plain, signal)
  const payload = typeof body === 'string' ? JSON.parse(body) : body
  let target: string
  if (url.pathname === '/api/session') target = '/atlas/session'
  else if (url.pathname.startsWith('/api/atlas/')) target = url.pathname.slice('/api'.length) + url.search
  else throw new Error('이 작업은 실제 서비스에서만 지원합니다. 합성 데모에서는 서버를 호출하지 않습니다.')
  const value = await workspaceDemoRequest<unknown>(target, { body: payload, signal })
  signal?.throwIfAborted()
  if (plain === 'blob') throw new Error('이 데모 응답은 다운로드 파일이 아닙니다.')
  return (plain ? JSON.stringify(value) : value) as T
}

async function selectInitialScope(force: boolean) {
  const url = new URL(appUrl('/'), location.origin)
  url.search = location.search
  url.searchParams.set('demo', '1')
  const catalog = await demoRequest<AtlasCatalog>('/api/atlas/catalog')
  const selectedProject = catalog.projects.find(project => project.id === url.searchParams.get('project'))
  const selectedBlock = selectedProject?.blocks.find(block => block.id === url.searchParams.get('block'))
  const selectedVersion = selectedBlock?.versions.find(version => version.id === url.searchParams.get('version'))
  if (force || !selectedProject || url.searchParams.has('block') && !selectedBlock || url.searchParams.has('version') && !selectedVersion) {
    const project = !force && selectedProject || catalog.projects.find(project => project.can_write) || catalog.projects[0]
    const block = !force && selectedBlock || project?.blocks[0]
    const version = block?.versions[0]
    for (const [key, value] of Object.entries({ project: project?.id, block: block?.id, version: version?.id })) {
      if (value) url.searchParams.set(key, value); else url.searchParams.delete(key)
    }
    for (const key of ['review', 'item', 'eco']) url.searchParams.delete(key)
  }
  // Snapshot UUIDs belong to the old page's memory and cannot survive a reload.
  url.searchParams.delete('snapshot')
  if (url.searchParams.has('review') && !url.searchParams.has('page')) url.searchParams.set('page', 'workbench')
  history.replaceState(null, '', url.pathname + url.search)
}

export default function AtlasDemo() {
  const [session, setSession] = useState<Session | null>(null)
  const [checking, setChecking] = useState(true)
  const [switching, setSwitching] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    setChecking(true)
    const start = async () => {
      if (new URLSearchParams(location.search).get('demo') !== '1') throw new Error('합성 데모는 명시적인 demo=1 주소에서만 열 수 있습니다.')
      const value = await demoRequest<Session>('/api/session')
      if (!active) return
      await selectInitialScope(false)
      if (!active) return
      setSession(value); setError(''); document.title = '합성 통합 데모 | Design Atlas'
    }
    void start().catch(cause => { if (active) setError(cause instanceof Error ? cause.message : '합성 데모를 준비하지 못했습니다.') })
      .finally(() => { if (active) setChecking(false) })
    return () => { active = false }
  }, [attempt])
  const refreshSession = useCallback(async () => {
    const value = await demoRequest<Session>('/api/session')
    setSession(value)
    return value
  }, [])
  const exit = useCallback(async () => { location.assign(appUrl('/')); return false }, [])
  const auth = useMemo<DashboardAuth>(() => ({
    config: null, session, checking, signingOut: false, error, accountProblem: false, businessDenied: 0,
    request: demoRequest, refreshSession,
    loginWithToken: () => { throw new Error('합성 데모는 실제 로그인이나 접근 토큰을 사용하지 않습니다.') },
    logout: exit, retry: () => setAttempt(value => value + 1),
  }), [session, checking, error, refreshSession, exit])
  const changeAccount = async (username: string) => {
    setSwitching(true); setError('')
    try {
      await workspaceDemoRequest('/demo/account', { body: { username } })
      await selectInitialScope(true)
      await refreshSession()
    } catch (cause) { setError(cause instanceof Error ? cause.message : '합성 계정을 바꾸지 못했습니다.') }
    finally { setSwitching(false) }
  }
  if (!session) return <main className="atlas-home"><h1>Design Atlas 통합 데모</h1>{checking ? <p role="status">합성 프로젝트와 분석 자료를 준비하고 있습니다…</p> : <><p role="alert">{error}</p><button className="atlas-button" onClick={() => setAttempt(value => value + 1)}>다시 읽기</button><a className="atlas-button secondary" href={appUrl('/')}>실제 앱 열기</a></>}</main>
  const user = workspaceDemoUsers.find(user => user.id === session.user_id)
  const toolbar = <section className="atlas-demo-toolbar" aria-label="합성 데모 환경">
    <div><strong>합성 통합 데모</strong><span>실제 앱과 같은 화면 · 현재 탭 메모리만 사용 · DB/SSO/VWP/AI/메일 호출 없음</span></div>
    <div><label>합성 계정<select value={user?.username || ''} disabled={switching} onChange={event => void changeAccount(event.target.value)}>{workspaceDemoUsers.map(user => <option key={user.id} value={user.username}>{user.name} · {user.username}</option>)}</select></label><button className="atlas-button secondary" onClick={() => location.reload()}>데모 초기화</button><a className="atlas-button secondary" href={appUrl('/')}>데모 종료</a></div>
    {error && <p role="alert">{error}</p>}
  </section>
  return <DashboardAuthContext value={auth}><DashboardNavigation key={session.user_id} demo demoToolbar={toolbar} demoAccount={<DemoAccount />} /></DashboardAuthContext>
}

function DemoAccount() {
  const atlas = useAtlas()!
  const navigate = useDashboardNavigation()
  const [error, setError] = useState('')
  return <main className="atlas-home"><section className="atlas-home-heading"><div><span className="atlas-eyebrow">MEMORY DEMO ACCOUNT</span><h1>내 계정 · 권한</h1><p>상단에서 선택한 합성 계정의 프로젝트 범위입니다. 실제 사내 신원이나 권한을 부여하지 않습니다.</p></div></section>
    <section className="atlas-panel"><h2>참여 프로젝트</h2><p>프로젝트 멤버는 그 프로젝트의 모든 등록 블록을 같은 공통 범위에서 조회·검토합니다.</p><div className="atlas-project-grid">{atlas.catalog?.projects.map(project => <article key={project.id}><h3>{project.name}</h3><code>{project.id}</code><p>등록 블록 {project.blocks.length}개 · {project.can_write ? '합성 조회·수정 권한' : '합성 조회 권한'}</p></article>)}</div></section>
    <section className="atlas-panel"><h2>실제 서비스와의 경계</h2><p>계정 선택과 카탈로그·로그·수치 수정은 현재 탭의 합성 자료에만 적용됩니다. 사내 SSO 연결·실제 권한 신청/승인·모델 인증값·메일 발송은 이 데모에서 실행하지 않습니다. 새로고침하면 합성 자료가 초기화됩니다.</p>{error && <p role="alert">{error}</p>}<button className="atlas-button" onClick={() => void navigate(atlas.href('/')).catch(cause => setError(cause instanceof Error ? cause.message : '홈을 열지 못했습니다.'))}>공통 홈으로 돌아가기</button></section>
  </main>
}
