import { useState } from 'react'
import { ArrowRight, LoaderCircle, LockKeyhole, LogOut, RefreshCw, ShieldCheck, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { WorkspaceBrand } from '@/components/layout/WorkspaceBrand'
import { ColorModeControl } from '@/components/layout/ColorModeControl'
import type { AuthConfig, Session } from '@/lib/types'
import { appUrl } from '@/lib/appUrl'
import { LoginReel } from './LoginReel'

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
        <h1>로그 검토 · 구성·전력 분석</h1>
      </div>
      <LoginReel />
    </div>
    <main className="login-main" id="workspace-main" tabIndex={-1}>
      <div className="color-mode-login"><ColorModeControl /></div>
      <div className="login-box">
        <div className="login-mark"><LockKeyhole size={24} /></div>
        <h2>{session ? 'SSO 연결 확인' : '로그인'}</h2>
        <p>{session ? `${session.reviewer} 님의 인증이 확인되었습니다.` : config?.mode === 'sso' ? '회사 계정으로 로그인하세요.' : config?.mode === 'local_token' ? '발급받은 접근 토큰으로 로그인하세요.' : '서버의 인증 설정을 확인합니다.'}</p>
        {error && <div role="alert" className="error-banner">{error}</div>}
        <div className={config?.mode === 'local_token' ? undefined : 'grid gap-3 mt-6'}>{session ? <>
          <p role="status">{session.notice}</p>
          <p>프로젝트·블록 권한 연결을 준비 중입니다.</p>
          <Button className="w-full" disabled={signingOut} onClick={() => { void onLogout() }}>{signingOut ? <LoaderCircle className="spin" /> : <LogOut />}로그아웃</Button>
          <p className="security-note">Design Atlas에서 로그아웃합니다. 회사 SSO 로그인 상태는 유지될 수 있습니다.</p>
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
            <Button className="w-full" disabled={!config.login_url} onClick={() => { if (config.login_url) location.assign(appUrl(config.login_url)) }}><ArrowRight />SSO로 로그인</Button>
            {!config.login_url && <p role="status">SSO 로그인 경로가 연결되지 않았습니다. 운영 담당자에게 문의해 주세요.</p>}
          </> : null}
        {!checking && (session || !config || config.mode === 'sso') && <Button className="w-full" variant="outline" disabled={signingOut} onClick={onRetry}><RefreshCw />연결 상태 다시 확인</Button>}
        </div>
        {!session && <>
          <div className="login-divider"><span>데모</span></div>
          <Button variant="outline" className="w-full" onClick={() => location.assign(appUrl('/?demo=1'))}><Sparkles />데모 열기</Button>
          <p className="tiny">합성 데이터만 사용하며 실제 데이터에는 반영되지 않습니다.</p>
        </>}
      </div>
    </main>
  </div>
}
