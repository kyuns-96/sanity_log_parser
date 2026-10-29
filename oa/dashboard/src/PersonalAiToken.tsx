import { useEffect, useState } from 'react'
import { ExternalLink, KeyRound, LoaderCircle } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Input } from './studio/components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './components/ui/dialog'
import './personal-ai-token.css'
import ConnectionCredentials from './ConnectionCredentials'

type TokenStatus = { enabled: boolean; registered?: boolean; revision?: number; expires_at?: string | null; expired?: boolean; expires_soon?: boolean; issuance_url?: string | null }
const messages: Record<string, string> = {
  UNAUTHORIZED: '로그인 세션을 확인한 뒤 다시 열어 주세요.',
  FORBIDDEN: '로그인한 본인의 토큰만 설정할 수 있습니다.',
  INVALID_REQUEST: '입력한 토큰을 확인해 주세요.',
  INVALID_AI_TOKEN: 'Bearer 접두어와 공백 없이 발급받은 토큰만 입력해 주세요.',
  AI_USER_TOKEN_EXPIRED: '만료된 토큰입니다. 새로 발급받은 토큰을 입력해 주세요.',
  AI_TOKEN_STORE_NOT_CONFIGURED: '개인 토큰 저장 설정이 준비되지 않았습니다. 운영 담당자에게 문의해 주세요.',
  AI_TOKEN_UNAVAILABLE: '등록된 토큰을 사용할 수 없습니다. 운영 담당자에게 문의해 주세요.',
  AI_PERSONAL_TOKEN_DISABLED: '개인 AI 토큰 등록이 활성화되어 있지 않습니다.',
  REVISION_CONFLICT: '다른 창에서 토큰 설정이 변경됐습니다. 창을 닫았다가 다시 열어 주세요.',
}

async function tokenRequest(path = '', body?: Record<string, unknown>, signal?: AbortSignal): Promise<TokenStatus> {
  const response = await fetch(`/api/ai/credentials${path}`, {
    method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal,
    headers: body ? { 'Content-Type': 'application/json', 'X-Sanity-Request': '1' } : {},
    ...(body ? { body: JSON.stringify(body) } : {}),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(messages[data.code] || '토큰 설정을 완료하지 못했습니다. 연결 상태를 확인해 주세요.')
  return data as TokenStatus
}

function issuanceUrl(value?: string | null): string | null {
  if (!value || value.length > 2048 || /[\s\u0000-\u001f\u007f\\]/u.test(value)) return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password ? value : null
  } catch { return null }
}

export default function PersonalAiToken() {
  const [open, setOpen] = useState(false)
  const [managedCount, setManagedCount] = useState(0)
  const [managedBusy, setManagedBusy] = useState(false)
  const [status, setStatus] = useState<TokenStatus | null>(null)
  const [token, setToken] = useState('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const issueUrl = issuanceUrl(status?.issuance_url)
  useEffect(() => {
    setToken(''); setError(''); setNotice(''); setStatus(null)
    if (!open) return
    const controller = new AbortController()
    setLoading(true)
    tokenRequest('', undefined, controller.signal).then(value => {
      if (!controller.signal.aborted) setStatus(value)
    }).catch(() => {
      if (!controller.signal.aborted) setError('토큰 설정을 불러오지 못했습니다. 로그인과 운영 설정을 확인해 주세요.')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [open])

  async function change(remove: boolean) {
    if (!status?.enabled || busy || status.revision === undefined) return
    const value = token.trim()
    setToken(''); setBusy(true); setError(''); setNotice('')
    try {
      const next = await tokenRequest(remove ? '/remove' : '', {
        expected_revision: status.revision, ...(!remove ? { token: value } : {}),
      })
      setStatus(next)
      setNotice(remove ? '개인 AI 토큰 연결을 해제했습니다.' : '토큰을 저장했습니다. 다음 AI 요청부터 사용합니다.')
      window.dispatchEvent(new Event('sanity-ai-token-changed'))
    } catch (failure) {
      setError(failure instanceof Error && Object.values(messages).includes(failure.message)
        ? failure.message : '토큰 설정을 완료하지 못했습니다. 창을 다시 열어 저장 상태를 확인해 주세요.')
    } finally { setBusy(false) }
  }

  return <>
    <Button variant="outline" size="sm" className="personal-ai-token-trigger" aria-label="개인 AI 토큰" title="개인 AI 토큰" onClick={() => setOpen(true)}><KeyRound size={14} /><span>개인 AI 토큰</span></Button>
    <Dialog open={open} onOpenChange={value => { if (!busy && !managedBusy) { setOpen(value); setToken('') } }}>
      <DialogContent className="personal-ai-token-dialog">
        <DialogHeader><DialogTitle>개인 AI 토큰</DialogTitle><DialogDescription>내가 요청한 AI 분류·판정에 사용할 토큰을 등록합니다. 발급받은 토큰은 암호화해 저장하며 다시 표시하지 않습니다.</DialogDescription></DialogHeader>
        {open && <ConnectionCredentials onLoaded={setManagedCount} onBusyChange={setManagedBusy} />}
        {loading && <p role="status"><LoaderCircle className="spin" size={16} />등록 상태를 확인하고 있습니다.</p>}
        {error && <p className="ws-error" role="alert">{error}</p>}
        {notice && <p className="ws-notice" role="status">{notice}</p>}
        {status && !status.enabled && !managedCount && <p>기존 서버 연결의 개인 AI 토큰 등록이 활성화되어 있지 않습니다. 운영 담당자에게 문의해 주세요.</p>}
        {status?.enabled && <form className="personal-ai-token-form" onSubmit={event => { event.preventDefault(); void change(false) }}>
          {!!managedCount && <strong>기존 서버 연결</strong>}
          <div className="personal-ai-token-status" aria-label="AI 토큰 등록 상태">
            <strong>{status.registered ? '등록됨' : '미등록'}</strong>
            {status.expires_at && <span>토큰에 기재된 만료일: {new Date(status.expires_at).toLocaleDateString('ko-KR')}</span>}
            {status.expired && <p role="alert">토큰이 만료됐습니다. 새로 발급받은 토큰으로 교체해 주세요.</p>}
            {status.expires_soon && <p role="status">30일 안에 만료됩니다. 새 토큰을 발급받아 교체해 주세요.</p>}
          </div>
          {issueUrl && <a className="personal-ai-token-issue-link" href={issueUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">JWT 토큰 발급받기<ExternalLink size={14} aria-hidden="true" /><span className="sr-only"> (새 탭)</span></a>}
          <label htmlFor="personal-ai-token-input">{status.registered ? '교체할 JWT 토큰' : 'JWT 토큰'}</label>
          <Input id="personal-ai-token-input" type="password" autoComplete="off" spellCheck={false} autoCapitalize="none"
            value={token} maxLength={8192} disabled={busy} onChange={event => setToken(event.target.value)}
            placeholder="Bearer 접두어 없이 입력" aria-describedby="personal-ai-token-help" />
          <p id="personal-ai-token-help" className="ws-muted">게이트웨이에서 본인에게 발급한 토큰을 입력하세요. 만료되면 직접 새 토큰으로 교체해야 합니다. 로그인용 SSO 비밀번호를 입력하지 마세요.</p>
          <DialogFooter>
            {status.registered && <Button type="button" variant="outline" disabled={busy} onClick={() => void change(true)}>연결 해제</Button>}
            <Button type="submit" disabled={busy || !token.trim()}>{busy ? '처리 중…' : status.registered ? '토큰 교체' : '토큰 저장'}</Button>
          </DialogFooter>
        </form>}
      </DialogContent>
    </Dialog>
  </>
}
