import { useEffect, useState } from 'react'
import { FileSearch } from 'lucide-react'
import { Button } from '@/components/ui/button'
import AccessAdmin from '@/features/access/AccessAdmin'
import AccessWelcome from '@/features/access/AccessWelcome'
import type { AccessPage } from '@/features/access/accessApi'
import { useDashboardAuth } from '@/features/auth/useDashboardAuth'
import App from '@/features/review/BundleReviewApp'
import { useDashboardNavigation } from '@/lib/dashboardMode'
import { AtlasReports } from './AtlasReports'
import { useAtlas } from './atlasContext'

export function AtlasRouteSurface({ page }: { page: AccessPage }) {
  const atlas = useAtlas()!
  const auth = useDashboardAuth(true)
  const navigateDashboard = useDashboardNavigation()
  const [legacyReview, setLegacyReview] = useState(false)
  const [routeError, setRouteError] = useState('')

  const session = auth.session
  const readySSO = session?.auth_method === 'sso' && session.authorization_ready === true
  const canRead = !!session && (session.auth_method !== 'sso' || readySSO && session.can_read_business === true)
  const error = routeError || auth.error

  const fail = (cause: unknown) => {
    if (!(cause instanceof DOMException && cause.name === 'AbortError')) {
      setRouteError(cause instanceof Error ? cause.message : '요청에 실패했습니다. 연결 상태를 확인해 주세요.')
    }
  }

  function navigate(next: AccessPage) {
    const url = new URL(location.href)
    url.searchParams.set('page', next)
    void navigateDashboard(url.pathname + url.search, session?.user_id).catch(fail)
  }

  useEffect(() => {
    if (!auth.businessDenied || !readySSO) return
    navigate('welcome')
    setRouteError('현재 담당 범위를 확인해 주세요. 접근할 수 없는 검사 결과는 화면에서 닫았습니다.')
    void auth.refreshSession().catch(fail)
  }, [auth.businessDenied])

  async function logout() {
    setRouteError('')
    if (!await auth.logout()) return
    const url = new URL(location.href)
    url.searchParams.delete('demo')
    history.replaceState(null, '', url)
    await navigateDashboard('/?page=welcome', undefined, true)
  }

  if (!session || session.auth_method === 'sso' && !readySSO) {
    return (
      <section className="empty">
        <h1>Design Atlas · 로그 검토</h1>
        <p role="alert">{error || '공통 로그인에서 계정과 서비스 연결 상태를 확인해 주세요.'}</p>
        <Button variant="outline" onClick={() => { setRouteError(''); auth.retry() }} disabled={auth.checking}>연결 다시 확인</Button>
      </section>
    )
  }

  if (session.auth_method === 'local_token' && page !== 'review') {
    return (
      <main className="workspace" id="workspace-main" tabIndex={-1}>
        <div className="page-heading">
          <div>
            <h1>{page === 'admin' ? '관리자 권한 · SSO 필요' : '로컬 연결 계정'}</h1>
            <p>현재 연결은 실제 로컬 토큰 모드이며 회사 SSO 계정이나 프로젝트 멤버십을 대신하지 않습니다.</p>
          </div>
        </div>
        <section className="detail-panel">
          <div className="unit-detail">
            <h2>{session.reviewer}</h2>
            <dl>
              <dt>인증 방식</dt><dd><code>{session.auth_method}</code></dd>
              <dt>접근 모드</dt><dd><code>{session.access_mode}</code> · {session.writable ? '쓰기 가능' : '읽기 전용'}</dd>
            </dl>
            {session.notice && <p className="session-notice">{session.notice}</p>}
            <p>SSO 프로젝트 참여·권한 신청, 관리자 업무와 운영 로그 분류·판정은 로컬 토큰에서 지원하지 않습니다. 공통 카탈로그와 허용된 실제 로컬 데이터는 로그 조회 화면에서 확인할 수 있습니다.</p>
            <p>토큰은 현재 탭 메모리에만 유지됩니다. 연결 종료는 공통 메뉴의 로그아웃을 사용해 주세요.</p>
            <Button variant="outline" onClick={() => navigate('review')}><FileSearch size={16} />로그 조회</Button>
          </div>
        </section>
        {error && <p className="error-banner" role="alert">{error}</p>}
      </main>
    )
  }

  if (readySSO && (page !== 'review' || !canRead)) {
    const props = {
      session,
      api: auth.request,
      onNavigate: navigate,
      onLogout: logout,
      onRefreshSession: auth.refreshSession,
      signingOut: auth.signingOut,
      notice: error,
    }
    return page === 'admin' && session.is_admin === true ? <AccessAdmin {...props} /> : <AccessWelcome {...props} />
  }

  if (page === 'review' && !legacyReview) {
    return <AtlasReports onLegacy={() => setLegacyReview(true)} canWorkbench={(readySSO || atlas.demo) && canRead} />
  }

  return <App routePage="review" initialLegacy onExitLegacy={() => setLegacyReview(false)} />
}
