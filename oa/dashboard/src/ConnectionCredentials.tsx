import { useEffect, useId, useState } from 'react'
import { ExternalLink, Save } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Badge } from './studio/components/ui/badge'
import { Input } from './studio/components/ui/input'
import './ai-connections.css'

type Status = { registered: boolean; revision: number; connection_changed?: boolean; expired?: boolean; expires_soon?: boolean; expires_at?: string | null }
type Connection = { id: string; label: string; enabled: boolean; auth_mode: string; execution_site?: string; model: string; models: string[]; issuance_url: string | null; credential: Status; credential_error: string | null }
const errors: Record<string,string> = {
  AI_USER_TOKEN_REQUIRED: '개인 인증값이 필요합니다. 발급받은 토큰 또는 API 키를 등록해 주세요.',
  AI_USER_TOKEN_EXPIRED: '만료된 인증값입니다. 새로 발급받아 입력해 주세요.',
  AI_CREDENTIAL_SCOPE_CHANGED: '연결 주소나 인증 방식이 바뀌었습니다. 현재 연결에 맞는 인증값을 다시 등록해 주세요.',
  AI_CONNECTION_UNAVAILABLE: '사용 중지되었거나 더 이상 사용할 수 없는 연결입니다.',
  AI_TOKEN_STORE_NOT_CONFIGURED: '인증값 암호화 설정을 운영 담당자에게 확인해 주세요.',
  AI_TOKEN_UNAVAILABLE: '저장된 인증값을 사용할 수 없습니다. 운영 담당자에게 문의해 주세요.',
  AI_VWP_PROFILE_UNAVAILABLE: 'VWP AI 실행 프로필을 사용할 수 없습니다. 운영 담당자에게 확인해 주세요.',
  AI_CONFIG_CHANGED: 'AI 연결 설정이 변경되었습니다. 운영 담당자에게 연결 상태를 확인해 주세요.',
  INVALID_AI_TOKEN: '공백이나 Bearer 접두어 없이 토큰·API 키만 입력해 주세요.',
  REVISION_CONFLICT: '다른 창에서 설정이 바뀌었습니다. 창을 다시 열어 현재 상태를 확인해 주세요.',
  FORBIDDEN: '로그인한 본인의 인증값만 변경할 수 있습니다.',
  UNAUTHORIZED: '로그인 세션을 확인해 주세요.',
}
async function request<T>(path: string, body?: Record<string,unknown>, signal?: AbortSignal): Promise<T> {
  const response = await fetch('/api/ai/connections' + path, { method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal,
    ...(body ? { headers: { 'Content-Type': 'application/json', 'X-Sanity-Request': '1' }, body: JSON.stringify(body) } : {}) })
  const data = await response.json()
  if (!response.ok) throw new Error(errors[data.code] || 'AI 연결 설정을 완료하지 못했습니다. 창을 다시 열어 현재 상태를 확인해 주세요.')
  return data as T
}
function issuance(value: string | null) {
  if (!value || value.length > 2048 || /[\s\u0000-\u001f\u007f\\]/u.test(value)) return null
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? value : null } catch { return null }
}

export default function ConnectionCredentials({ onBusyChange }: { onBusyChange: (busy: boolean) => void }) {
  const [entries, setEntries] = useState<Connection[]>([])
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    request<{ items: Connection[] }>('', undefined, controller.signal).then(value => {
      if (!controller.signal.aborted) setEntries(value.items)
    }).catch(() => { if (!controller.signal.aborted) setError('등록된 AI 연결을 불러오지 못했습니다. 연결 상태와 운영 설정을 확인해 주세요.') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [])
  async function change(connection: Connection, token: string | null) {
    setBusy(true); onBusyChange(true); setError(''); setNotice('')
    try {
      const status = await request<Status>(`/${connection.id}/credentials${token === null ? '/remove' : ''}`,
        { expected_revision: connection.credential.revision, ...(token !== null ? { token } : {}) })
      setEntries(previous => previous.map(value => value.id === connection.id ? { ...value, credential: status,
        credential_error: token === null && value.credential_error && !['AI_USER_TOKEN_REQUIRED', 'AI_USER_TOKEN_EXPIRED', 'AI_CREDENTIAL_SCOPE_CHANGED'].includes(value.credential_error) ? value.credential_error : null } : value))
      setNotice(`${connection.label}: ${token === null ? '개인 인증값 연결을 해제했습니다.' : '개인 인증값을 저장했습니다.'}`)
      window.dispatchEvent(new Event('sanity-ai-token-changed'))
    } catch (failure) { setError(failure instanceof Error && Object.values(errors).includes(failure.message)
      ? failure.message : '인증값을 저장하지 못했습니다. 창을 다시 열어 현재 상태를 확인해 주세요.') }
    finally { setBusy(false); onBusyChange(false) }
  }
  return <section className="ai-personal-connections" aria-label="AI 연결별 개인 인증값">
    {loading && <p role="status">등록된 AI 연결을 확인하고 있습니다.</p>}
    {error && <p role="alert" className="ws-error">{error}</p>}{notice && <p role="status" className="ws-notice">{notice}</p>}
    {!loading && !error && !entries.length && <p>등록된 AI 연결이 없습니다. 관리자에게 AI 연결 등록을 요청해 주세요.</p>}
    {entries.map(entry => <Credentials key={entry.id} connection={entry} busy={busy} onSave={token => void change(entry, token)} />)}
  </section>
}

function Credentials({ connection, busy, onSave }: { connection: Connection; busy: boolean; onSave: (token: string | null) => void }) {
  const [token, setToken] = useState('')
  const inputId = useId()
  const url = issuance(connection.issuance_url)
  const status = connection.credential
  const name = connection.auth_mode === 'x_client_api_key' ? '개인 API 키' : '개인 Bearer 토큰'
  const unavailableMode = connection.execution_site === 'vwp' && !['bearer', 'bearer_x_client', 'x_client_api_key'].includes(connection.auth_mode)
  const blocked = !!connection.credential_error && !['AI_USER_TOKEN_REQUIRED', 'AI_USER_TOKEN_EXPIRED', 'AI_CREDENTIAL_SCOPE_CHANGED'].includes(connection.credential_error)
  if (unavailableMode) return <article className="ai-personal-connection" data-enabled={connection.enabled} aria-label={`${connection.label} VWP 연결`}>
    <header><strong>{connection.label}</strong><Badge variant="outline">{connection.enabled ? '설정 확인 필요' : '사용 중지'}</Badge></header>
    <small>{connection.models.join(' · ')}</small>
    <p>{connection.credential_error === 'FORBIDDEN' ? '현재 사용자에게 VWP 모델 사용 권한이 없습니다. 운영 담당자에게 문의해 주세요.' : 'VWP 인증 방식을 확인할 수 없습니다. 운영 담당자에게 연결 설정을 확인해 주세요.'}</p>
    {status.registered && <Button type="button" variant="outline" disabled={busy} onClick={() => onSave(null)}>연결 해제</Button>}
  </article>
  return <article className="ai-personal-connection" data-enabled={connection.enabled} aria-label={`${connection.label} 개인 인증값`}>
    <header><strong>{connection.label}</strong><Badge variant="outline" className={`ai-credential-status${connection.enabled && status.registered ? ' is-registered' : ''}`}>{connection.enabled ? status.registered ? '등록됨' : '미등록' : '사용 중지'}</Badge></header>
    <small>{connection.models.join(' · ')}</small>
    {!blocked && status.connection_changed && <p role="status" className="ws-notice">주소나 인증 방식이 바뀌었습니다. 인증값을 다시 등록해야 합니다.</p>}
    {!blocked && status.expired && <p role="alert" className="ws-error">인증값이 만료됐습니다. 새로 발급받아 교체해 주세요.</p>}
    {!blocked && status.expires_soon && <p role="status">30일 안에 만료됩니다. 새 인증값을 발급받아 교체해 주세요.</p>}
    {status.expires_at && <small>토큰에 기재된 만료일: {new Date(status.expires_at).toLocaleDateString('ko-KR')}</small>}
    {(blocked || !status.connection_changed && !status.expired) && connection.credential_error && <p className="ws-notice">{connection.execution_site === 'vwp' && connection.credential_error === 'FORBIDDEN' ? '현재 사용자에게 VWP 모델 사용 권한이 없습니다. 운영 담당자에게 문의해 주세요.' : errors[connection.credential_error] || 'AI 연결을 현재 사용할 수 없습니다. 운영 담당자에게 연결 상태를 확인해 주세요.'}</p>}
    {url && connection.enabled && !blocked && <a href={url} className="personal-ai-token-issue-link" target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{connection.label} 인증값 발급받기<ExternalLink size={14} /><span className="sr-only"> (새 탭)</span></a>}
    <form onSubmit={event => { event.preventDefault(); const value = token.trim(); if (!value) return; setToken(''); onSave(value) }}>
      <label htmlFor={inputId}>{name}</label><Input id={inputId} aria-label={`${connection.label} ${name}`} type="password" autoComplete="off" spellCheck={false} autoCapitalize="none" maxLength={8192} disabled={busy || !connection.enabled || blocked} value={token} onChange={event => setToken(event.target.value)} />
      <div className="am-row-actions">{status.registered && <Button type="button" variant="outline" disabled={busy} onClick={() => { setToken(''); onSave(null) }}>연결 해제</Button>}<Button type="submit" disabled={busy || !connection.enabled || blocked || !token.trim()}><Save size={14} />{status.registered ? '인증값 교체' : '인증값 저장'}</Button></div>
    </form>
  </article>
}
