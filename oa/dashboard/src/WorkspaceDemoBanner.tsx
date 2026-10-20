import { useState } from 'react'
import { ArrowUpRight, ChevronDown, FlaskConical } from 'lucide-react'
import { workspaceDemoUsers } from './workspaceDemoAccounts'
import type { WorkspaceSession } from './workspaceApi'

export default function WorkspaceDemoBanner({ session, onAccount }: { session: WorkspaceSession; onAccount: (username: string) => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <section className="ws-demo-banner" aria-label="워크스페이스 데모 안내">
    <div className="ws-demo-description"><span className="ws-demo-mark"><FlaskConical size={19} /></span><div><strong>데모 모드 <span>최신 워크스페이스</span></strong><p>합성 예시를 자유롭게 검토하세요. 변경은 현재 탭에만 저장되며 새로고침하면 초기화됩니다.</p></div></div>
    <div className="ws-demo-controls"><label htmlFor="ws-demo-account">데모 계정<select id="ws-demo-account" aria-label="데모 계정" value={session.user.username} disabled={busy} onChange={async event => {
      const username = event.target.value
      setBusy(true); setError('')
      try { await onAccount(username) } catch { setError('계정을 전환하지 못했습니다. 다시 선택해 주세요.') } finally { setBusy(false) }
    }}>{workspaceDemoUsers.map(user => <option key={user.id} value={user.username}>{user.name} · {user.role} ({user.username})</option>)}</select></label>
      <details className="ws-demo-links"><summary>다른 예시<ChevronDown size={13} /></summary><nav aria-label="다른 데모 화면">{[
        ['프로젝트·담당자', '/?demo=1&flow=access'], ['버전·ECO·PoR', '/?demo=1&flow=versions'],
        ['GCA 보고서', '/?demo=1&dataset=gca'], ['이전 검토 대장', '/?demo=1&flow=legacy'],
      ].map(([label, href]) => <a key={href} href={href}>{label}<ArrowUpRight size={13} /></a>)}</nav></details>
    </div>
    {error && <p className="ws-demo-error" role="alert">{error}</p>}
  </section>
}
