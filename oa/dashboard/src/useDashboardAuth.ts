import { useCallback, useEffect, useRef, useState } from 'react'
import type { AuthConfig, Session } from './types'

export class DashboardRequestError extends Error {
  constructor(public code: string, public status: number) {
    const messages: Record<string, string> = {
      UNAUTHORIZED: '인증이 만료되었거나 올바르지 않습니다. 다시 로그인해 주세요.',
      ACCOUNT_NOT_LINKED: 'SSO 인증은 확인됐지만 내부 계정이 연결되지 않았습니다. 운영 담당자에게 문의해 주세요.',
      ACCOUNT_DISABLED: '사용이 중지된 계정입니다. 운영 담당자에게 문의해 주세요.',
      AUTH_SSO_NOT_CONFIGURED: 'SSO 연결 설정이 아직 준비되지 않았습니다.',
      AUTH_DIRECTORY_NOT_CONFIGURED: '내부 계정 연결 설정이 아직 준비되지 않았습니다.',
      AUTH_PROVIDER_UNAVAILABLE: 'SSO 인증 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.',
      AUTH_DIRECTORY_UNAVAILABLE: '내부 계정 조회 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.',
      AUTH_CONFIG: '인증 설정을 확인할 수 없습니다. 운영 담당자에게 문의해 주세요.',
      AUTH_INVALID_RESULT: '서버의 인증 응답을 확인할 수 없습니다. 운영 담당자에게 문의해 주세요.',
      REVISION_CONFLICT: '다른 검토자가 내용을 변경했습니다. 새로고침 후 검토 창을 다시 열어 주세요.',
    }
    super(`${messages[code] || '요청을 처리하지 못했습니다.'} (${code})`)
  }
}

async function result<T>(response: Response, plain = false): Promise<T> {
  if (!response.ok) {
    let code = `HTTP_${response.status}`
    try {
      const body = await response.json()
      if (typeof body.code === 'string') code = body.code.slice(0, 100)
    } catch { /* A proxy's non-JSON response is still an explicit failure. */ }
    throw new DashboardRequestError(code, response.status)
  }
  return plain ? await response.text() as T : await response.json() as T
}

function validConfig(value: AuthConfig): boolean {
  return !!value && (value.mode === 'local_token' || value.mode === 'sso')
    && (value.login_url === null || value.login_url === '/auth/login')
    && (value.logout_url === null ? value.logout_method === null : value.logout_url === '/auth/logout' && value.logout_method === 'POST')
    && (value.mode !== 'local_token' || value.login_url === null && value.logout_url === null)
}

const message = (error: unknown) => error instanceof DashboardRequestError ? error.message : '서버에 연결할 수 없습니다. 연결 상태를 확인하고 다시 시도해 주세요.'

/** Browser credentials stay in cookies or tab memory; the server selects one mode. */
export function useDashboardAuth(enabled: boolean) {
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [token, setToken] = useState('')
  const [checking, setChecking] = useState(enabled)
  const [signingOut, setSigningOut] = useState(false)
  const [error, setError] = useState('')
  const [accountProblem, setAccountProblem] = useState(false)
  const [retryCount, setRetryCount] = useState(0)
  const pending = useRef(new Set<AbortController>())
  const generation = useRef(0)

  const abortRequests = useCallback(() => {
    generation.current++
    for (const controller of pending.current) controller.abort()
    pending.current.clear()
  }, [])

  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    pending.current.add(controller)
    setChecking(true); setError('')
    fetch('/api/auth-config', { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal })
      .then(response => result<AuthConfig>(response))
      .then(value => {
        if (controller.signal.aborted) return
        if (!validConfig(value)) throw new DashboardRequestError('AUTH_CONFIG', 503)
        setConfig(value)
      })
      .catch(error => { if (!controller.signal.aborted) { setError(message(error)); setChecking(false) } })
      .finally(() => pending.current.delete(controller))
    return () => controller.abort()
  }, [enabled, retryCount])

  useEffect(() => () => abortRequests(), [enabled, abortRequests])

  const request = useCallback(async <T,>(path: string, body?: Record<string, unknown>, plain = false): Promise<T> => {
    if (!enabled || !config) throw new DashboardRequestError('AUTH_CONFIG', 503)
    if (config.mode === 'local_token' && !token) throw new DashboardRequestError('UNAUTHORIZED', 401)
    const controller = new AbortController()
    const version = generation.current
    pending.current.add(controller)
    try {
      const response = await fetch(path, {
        method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: { ...(config.mode === 'local_token' ? { Authorization: `Bearer ${token}` } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}), signal: controller.signal,
      })
      const value = await result<T>(response, plain)
      if (version !== generation.current || controller.signal.aborted) throw new DOMException('Request cancelled', 'AbortError')
      return value
    } catch (error) {
      if (version === generation.current && path !== '/api/session' && error instanceof DashboardRequestError && (error.status === 401 || error.status === 403)) {
        abortRequests(); setSession(null); setToken(''); setError(error.message)
        setAccountProblem(error.code === 'ACCOUNT_NOT_LINKED' || error.code === 'ACCOUNT_DISABLED')
      }
      throw error
    } finally {
      pending.current.delete(controller)
    }
  }, [enabled, config, token, abortRequests])

  useEffect(() => {
    if (!enabled || !config) return
    if (config.mode === 'local_token' && !token) { setChecking(false); return }
    let active = true
    setChecking(true)
    request<Session>('/api/session').then(value => {
      if (!active) return
      if (value.auth_method !== config.mode) throw new DashboardRequestError('AUTH_INVALID_RESULT', 503)
      setSession(value); setError(''); setAccountProblem(false)
    }).catch(error => {
      if (!active || error instanceof DOMException && error.name === 'AbortError') return
      setSession(null); setToken('')
      setAccountProblem(error instanceof DashboardRequestError && (error.code === 'ACCOUNT_NOT_LINKED' || error.code === 'ACCOUNT_DISABLED'))
      setError(config.mode === 'sso' && error instanceof DashboardRequestError && error.status === 401 ? '' : message(error))
    }).finally(() => { if (active) setChecking(false) })
    return () => { active = false }
  }, [enabled, config, token, request])

  const loginWithToken = (value: string) => {
    if (config?.mode !== 'local_token') return
    abortRequests(); setSession(null); setError(''); setToken(value.trim())
  }

  const logout = async (): Promise<boolean> => {
    if (config?.mode === 'local_token') {
      abortRequests(); setSession(null); setToken(''); setError(''); setAccountProblem(false)
      return true
    }
    if (config?.mode !== 'sso' || !config.logout_url || config.logout_method !== 'POST') {
      setError('SSO 로그아웃 경로가 연결되지 않았습니다. 운영 담당자에게 문의해 주세요.')
      return false
    }
    abortRequests(); setSigningOut(true); setError('')
    const controller = new AbortController()
    pending.current.add(controller)
    try {
      // Origin is supplied by the browser. The company router validates it
      // against its configured public SP origin before revoking the session.
      const response = await fetch(config.logout_url, { method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal })
      const value = await result<{ status: string }>(response)
      if (controller.signal.aborted) return false
      if (value.status !== 'LOGGED_OUT') throw new DashboardRequestError('AUTH_INVALID_RESULT', 503)
      setSession(null); setToken(''); setAccountProblem(false)
      return true
    } catch (error) {
      if (!controller.signal.aborted) setError(`서버 로그아웃에 실패했습니다. 현재 세션이 남아 있을 수 있습니다. 다시 시도해 주세요. ${message(error)}`)
      return false
    } finally {
      pending.current.delete(controller); setSigningOut(false)
    }
  }

  const retry = () => {
    abortRequests(); setSession(null); setToken(''); setConfig(null); setError(''); setAccountProblem(false); setRetryCount(value => value + 1)
  }

  return { config, session, checking, signingOut, error, accountProblem, request, loginWithToken, logout, retry }
}
