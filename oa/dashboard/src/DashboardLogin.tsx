import { useState } from 'react'
import { ArrowRight, ClipboardCheck, Layers3, LoaderCircle, LockKeyhole, LogOut, RefreshCw, ShieldCheck, Sparkles } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Badge } from './studio/components/ui/badge'
import { Input } from './studio/components/ui/input'
import { WorkspaceBrand } from './components/WorkspaceBrand'
import { ColorModeControl } from './components/ColorModeControl'
import type { AuthConfig, Session } from './types'

type Props = {
  config: AuthConfig | null; session: Session | null; checking: boolean; signingOut: boolean; error: string; accountProblem: boolean
  onToken: (token: string) => void; onRetry: () => void; onLogout: () => Promise<void>
}

export function DashboardLogin({ config, session, checking, signingOut, error, accountProblem, onToken, onRetry, onLogout }: Props) {
  const [tokenInput, setTokenInput] = useState('')
  return <div className="login-page studio-system studio-auth">
    <a className="skip-link" href="#workspace-main">본문으로 건너뛰기</a>
    <div className="login-decoration">
      <WorkspaceBrand />
      <div>
        <Badge variant="outline" className="login-platform-badge"><Layers3 size={13} />Sanity review platform</Badge>
        <h1>판단을 쌓고,<br />다음 검토를 가깝게.</h1>
        <p>AI의 분류와 판정, 사람의 확정 근거를<br />하나의 검토 흐름으로 연결합니다.</p>
        <ol className="login-process" aria-label="검토 흐름">
          <li><span>01</span><div><strong>AI 로그 분류</strong><small>로그를 그룹으로 정리하고 분류 근거 확인</small></div><Layers3 /></li>
          <li><span>02</span><div><strong>그룹별 판정</strong><small>원본 근거와 기준을 비교해 판단</small></div><Sparkles /></li>
          <li><span>03</span><div><strong>사람의 확정</strong><small>검토 결과를 다음 AI의 참고 사례로 축적</small></div><ClipboardCheck /></li>
        </ol>
      </div>
      <span>Sanity Console · AI review workspace</span>
    </div>
    <main className="login-main" id="workspace-main" tabIndex={-1}>
      <div className="color-mode-login"><ColorModeControl /></div>
      <div className="login-box">
        <div className="login-mark"><LockKeyhole size={24} /></div>
        <h2>{session ? 'SSO 연결 확인' : '검토 공간에 연결'}</h2>
        <p>{session ? `${session.reviewer} 님의 인증이 확인되었습니다.` : config?.mode === 'sso' ? '회사 계정으로 로그인하세요.' : config?.mode === 'local_token' ? '발급받은 접근 토큰으로 로그인하세요.' : '서버의 인증 설정을 확인합니다.'}</p>
        {error && <div role="alert" className="error-banner">{error}</div>}
        <div className={config?.mode === 'local_token' ? undefined : 'grid gap-3 mt-6'}>{session ? <>
          <p role="status">{session.notice}</p>
          <p>프로젝트·블록 권한 연결을 준비 중입니다.</p>
          <Button className="w-full" disabled={signingOut} onClick={() => { void onLogout() }}>{signingOut ? <LoaderCircle className="spin" /> : <LogOut />}로그아웃</Button>
          <p className="security-note">OA 앱에서 로그아웃합니다. 회사 SSO 로그인 상태는 유지될 수 있습니다.</p>
        </> : checking ? <p role="status" className="security-note"><LoaderCircle className="spin" size={15} />로그인 상태를 확인하고 있습니다.</p>
          : config?.mode === 'local_token' ? <>
            <form onSubmit={event => { event.preventDefault(); onToken(tokenInput.trim()); setTokenInput('') }}>
              <label htmlFor="access-token">접근 토큰</label>
              <Input id="access-token" type="password" autoComplete="off" value={tokenInput} onChange={event => setTokenInput(event.target.value)} placeholder="접근 토큰 입력" required />
              <Button className="w-full" type="submit" disabled={!tokenInput.trim()}><ArrowRight />워크스페이스 연결</Button>
            </form>
            <p className="security-note"><ShieldCheck size={15} />토큰은 현재 탭의 메모리에만 보관됩니다.</p>
          </> : config?.mode === 'sso' && accountProblem ? <Button className="w-full" disabled={signingOut} onClick={() => { void onLogout() }}>{signingOut ? <LoaderCircle className="spin" /> : <LogOut />}로그아웃</Button>
            : config?.mode === 'sso' ? <>
            <Button className="w-full" disabled={!config.login_url} onClick={() => { if (config.login_url) location.assign(config.login_url) }}><ArrowRight />SSO로 로그인</Button>
            {!config.login_url && <p role="status">SSO 로그인 경로가 연결되지 않았습니다. 운영 담당자에게 문의해 주세요.</p>}
          </> : null}
        {!checking && (session || !config || config.mode === 'sso') && <Button className="w-full" variant="outline" disabled={signingOut} onClick={onRetry}><RefreshCw />연결 상태 다시 확인</Button>}
        </div>
        {!session && <>
          <div className="login-divider"><span>먼저 둘러보고 싶다면</span></div>
          <Button variant="outline" className="w-full" onClick={() => location.assign('/?demo=1')}><Sparkles />데모 둘러보기</Button>
          <p className="tiny">데모는 예시 데이터를 사용하며 실제 검사 결과에 영향을 주지 않습니다.</p>
        </>}
      </div>
      <span className="login-foot">SANITY / REVIEW CONSOLE <span>v0.2 · OA</span></span>
    </main>
  </div>
}
