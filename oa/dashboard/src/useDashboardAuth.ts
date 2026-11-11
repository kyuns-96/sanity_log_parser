import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { AuthConfig, Session } from './types'
import { appUrl } from './appUrl'

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
      FORBIDDEN: '이 작업에 필요한 권한이 없습니다. 현재 담당 범위를 확인해 주세요.',
      ACCESS_DENIED: '이 작업에 필요한 권한이 없습니다. 현재 담당 범위를 확인해 주세요.',
      AUTHORIZATION_FORBIDDEN: '이 작업에 필요한 권한이 없습니다. 현재 담당 범위를 확인해 주세요.',
      RESOURCE_NOT_FOUND: '현재 권한으로 이 검사 결과를 열 수 없습니다. 담당 범위를 확인해 주세요.',
      REQUEST_CONFLICT: '이미 처리되었거나 같은 요청 번호의 내용이 다릅니다. 최신 상태를 확인해 주세요.',
      AREA_POWER_UNAVAILABLE: '구성·전력 분석 서비스가 설정되지 않았거나 현재 사용할 수 없습니다. 운영 담당자에게 문의해 주세요.',
      AREA_BINDING_NOT_RECORDED: '부분 저장: 수치 DB에는 저장되었지만 공통 카탈로그 연결 기록은 완료되지 않았습니다. 범위를 새로고침해 상태를 확인한 후 필요한 경우 직접 다시 연결하세요.',
      AREA_BINDING_CONFLICT: '명시적 연결이 충돌하거나 여러 기존 ID가 모호하게 연결되어 있습니다. 로컬 운영자 또는 관리자에게 실제 연결 ID를 확인하세요.',
      AREA_PREVIOUS_NOT_PREPARED: '명시적으로 선택한 이전 수치 버전을 먼저 준비하세요. 이전 범위 선택 → 수치 분석 준비 후 이 범위로 돌아오세요.',
      AREA_LINEAGE_REQUIRED: '이 버전의 수치 계보를 명시적으로 선언하세요. 계보 시작 또는 실제 이전 버전을 선택해야 합니다.',
    }
    super(`${messages[code] || '요청을 처리하지 못했습니다.'} (${code})`)
  }
}

async function result<T>(response: Response, plain: boolean | 'blob' = false): Promise<T> {
  if (!response.ok) {
    let code = `HTTP_${response.status}`
    try {
      const body = await response.json()
      if (typeof body.code === 'string') code = body.code.slice(0, 100)
    } catch { /* A proxy's non-JSON response is still an explicit failure. */ }
    throw new DashboardRequestError(code, response.status)
  }
  return plain === 'blob' ? await response.blob() as T : plain ? await response.text() as T : await response.json() as T
}

export function validAuthConfig(value: AuthConfig): boolean {
  return !!value && (value.mode === 'local_token' || value.mode === 'sso')
    && (value.login_url === null || value.login_url === '/auth/login')
    && (value.logout_url === null ? value.logout_method === null : value.logout_url === '/auth/logout' && value.logout_method === 'POST')
    && (value.mode !== 'local_token' || value.login_url === null && value.logout_url === null)
}

const message = (error: unknown) => error instanceof DashboardRequestError ? error.message : '서버에 연결할 수 없습니다. 연결 상태를 확인하고 다시 시도해 주세요.'

/** Browser credentials stay in cookies or tab memory; the server selects one mode. */
export type DashboardAuthSnapshot = { config: AuthConfig; session: Session }
function useDashboardAuthState(enabled: boolean, initial?: DashboardAuthSnapshot) {
  const [config, setConfig] = useState<AuthConfig | null>(() => initial && validAuthConfig(initial.config) && initial.session.auth_method === initial.config.mode ? initial.config : null)
  const [session, setSession] = useState<Session | null>(() => config ? initial?.session ?? null : null)
  const [token, setToken] = useState('')
  const [checking, setChecking] = useState(enabled && !session)
  const [signingOut, setSigningOut] = useState(false)
  const [error, setError] = useState('')
  const [accountProblem, setAccountProblem] = useState(false)
  const [retryCount, setRetryCount] = useState(0)
  const [businessDenied, setBusinessDenied] = useState(0)
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
    fetch(appUrl('/api/auth-config'), { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal })
      .then(response => result<AuthConfig>(response))
      .then(value => {
        if (controller.signal.aborted) return
        if (!validAuthConfig(value)) throw new DashboardRequestError('AUTH_CONFIG', 503)
        setConfig(value)
      })
      .catch(error => { if (!controller.signal.aborted) { setError(message(error)); setChecking(false) } })
      .finally(() => pending.current.delete(controller))
    return () => controller.abort()
  }, [enabled, retryCount])

  useEffect(() => () => abortRequests(), [enabled, abortRequests])

  const request = useCallback(async <T,>(path: string, body?: Record<string, unknown> | string, plain: boolean | 'blob' = false, signal?: AbortSignal): Promise<T> => {
    if (!enabled || !config) throw new DashboardRequestError('AUTH_CONFIG', 503)
    if (config.mode === 'local_token' && !token) throw new DashboardRequestError('UNAUTHORIZED', 401)
    const controller = new AbortController()
    signal?.throwIfAborted()
    const cancel = () => controller.abort()
    signal?.addEventListener('abort', cancel, { once: true })
    const version = generation.current
    pending.current.add(controller)
    try {
      const response = await fetch(appUrl(path), {
        method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: { ...(config.mode === 'local_token' ? { Authorization: `Bearer ${token}` } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json', 'X-Sanity-Request': '1' }) },
        ...(body !== undefined ? { body: typeof body === 'string' ? body : JSON.stringify(body) } : {}), signal: controller.signal,
      })
      const value = await result<T>(response, plain)
      if (version !== generation.current || controller.signal.aborted) throw new DOMException('Request cancelled', 'AbortError')
      return value
    } catch (error) {
      if (version === generation.current && path !== '/api/session' && error instanceof DashboardRequestError &&
          (error.status === 401 || error.code === 'ACCOUNT_NOT_LINKED' || error.code === 'ACCOUNT_DISABLED')) {
        abortRequests(); setSession(null); setToken(''); setError(error.message)
        setAccountProblem(error.code === 'ACCOUNT_NOT_LINKED' || error.code === 'ACCOUNT_DISABLED')
      } else if (version === generation.current && error instanceof DashboardRequestError &&
          (error.status === 403 || error.code === 'RESOURCE_NOT_FOUND') &&
          /^\/api\/(bundles(?:\/|\?|$)|evidence\/|receive(?:\?|$))/.test(path)) {
        // A revoked scope must not be repopulated by another in-flight read.
        // Keep the verified identity, but invalidate responses already in flight.
        abortRequests()
        setBusinessDenied(value => value + 1)
      }
      throw error
    } finally {
      signal?.removeEventListener('abort', cancel)
      pending.current.delete(controller)
    }
  }, [enabled, config, token, abortRequests])

  const refreshSession = useCallback(async () => {
    try {
      const value = await request<Session>('/api/session')
      if (!config || value.auth_method !== config.mode) throw new DashboardRequestError('AUTH_INVALID_RESULT', 503)
      setSession(value)
      return value
    } catch (error) {
      if (error instanceof DashboardRequestError && (error.status === 401 || error.code === 'ACCOUNT_NOT_LINKED' || error.code === 'ACCOUNT_DISABLED')) {
        abortRequests(); setSession(null); setToken(''); setError(error.message)
        setAccountProblem(error.code === 'ACCOUNT_NOT_LINKED' || error.code === 'ACCOUNT_DISABLED')
      }
      throw error
    }
  }, [request, config, abortRequests])

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
      const response = await fetch(appUrl(config.logout_url), { method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal })
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

  return { config, session, checking, signingOut, error, accountProblem, businessDenied, request, refreshSession, loginWithToken, logout, retry }
}

export interface DashboardAuth {
  config: AuthConfig | null
  session: Session | null
  checking: boolean
  signingOut: boolean
  error: string
  accountProblem: boolean
  businessDenied: number
  request: <T>(path: string, body?: Record<string, unknown> | string, plain?: boolean | 'blob', signal?: AbortSignal) => Promise<T>
  refreshSession: () => Promise<Session>
  loginWithToken: (value: string) => void
  logout: () => Promise<boolean>
  retry: () => void
}
export const DashboardAuthContext = createContext<DashboardAuth | null>(null)

/** The router owns one identity; standalone explicit demos keep their existing boundary. */
export function useDashboardAuth(enabled: boolean, initial?: DashboardAuthSnapshot): DashboardAuth {
  const shared = useContext(DashboardAuthContext)
  const local = useDashboardAuthState(enabled && !shared, initial)
  return shared ?? local
}
