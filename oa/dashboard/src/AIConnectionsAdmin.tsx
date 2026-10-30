import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Plus, RefreshCw, Settings2 } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Badge } from './studio/components/ui/badge'
import { Input } from './studio/components/ui/input'
import { Textarea } from './studio/components/ui/textarea'
import type { AccessApi } from './accessApi'
import AIProcessingLimits from './AIProcessingLimits'
import VwpAIConnectionForm from './VwpAIConnectionForm'
import { isVwpConnection } from './aiConnectionTypes'
import type { OAConnectionConfig as Config, OAConnectionEntry as Entry, ManagedConnection, VwpConnectionEntry, VwpProfile } from './aiConnectionTypes'
import './ai-connections.css'

const modes = { bearer: 'Bearer', bearer_x_client: 'Bearer + x-client · 사내 게이트웨이', x_client_api_key: 'x-client-api-key 단독' }
const defaults = (): Config => ({ label: '', enabled: true, endpoint: '', fallback_endpoint: '', api: 'chat_completions', auth_mode: 'bearer', model: '', models: [], issuance_url: '', json_mode: false, timeout: 120, max_input_bytes: 1048576, max_output_bytes: 2097152, client_name: '', client_version: '', session_mode: 'disabled', session_id: '', batch: { enabled: false, tokenizer_mode: 'utf8_bytes', tokenizer_sha256: '', context_tokens: 32768, input_tokens: 16000, output_tokens: 4096, overhead_tokens: 1024, output_parameter: 'max_tokens', max_records: 200, merge_groups: 20, max_calls: 20000, max_rounds: 8, deadline_seconds: 86400 } })

export default function AIConnectionsAdmin({ api }: { api: AccessApi }) {
  const [items, setItems] = useState<ManagedConnection[]>([])
  const [vwpDraft, setVwpDraft] = useState<VwpConnectionEntry | null>(null)
  const [vwpProfiles, setVwpProfiles] = useState<VwpProfile[]>([])
  const [vwpReady, setVwpReady] = useState(false)
  const [ready, setReady] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [draft, setDraft] = useState<Entry | null>(null)
  const [models, setModels] = useState('')
  const [clientKey, setClientKey] = useState('')
  const config = draft?.config
  const editing = !!draft || !!vwpDraft
  const gateway = config?.auth_mode === 'bearer_x_client'

  async function load() {
    const result = await api<{ items: ManagedConnection[]; credentials_ready: boolean; vwp_ready?: boolean; vwp_profiles?: VwpProfile[] }>('/api/admin/ai/connections')
    setItems(result.items); setReady(result.credentials_ready); setVwpReady(result.vwp_ready === true); setVwpProfiles(result.vwp_profiles || [])
  }
  useEffect(() => {
    let active = true
    api<{ items: ManagedConnection[]; credentials_ready: boolean; vwp_ready?: boolean; vwp_profiles?: VwpProfile[] }>('/api/admin/ai/connections').then(value => {
      if (active) { setItems(value.items); setReady(value.credentials_ready); setVwpReady(value.vwp_ready === true); setVwpProfiles(value.vwp_profiles || []) }
    }).catch(() => { if (active) setError('AI 연결 목록을 불러오지 못했습니다. 관리자 권한과 연결 상태를 확인해 주세요.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [api])

  function edit(value?: ManagedConnection) {
    if (value && isVwpConnection(value)) { setDraft(null); setVwpDraft(structuredClone(value)); setError(''); setNotice(''); return }
    const next = value ? structuredClone(value) : { id: crypto.randomUUID(), revision: 0, config: defaults(), client_key_registered: false }
    setDraft(next); setModels(next.config.models.join('\n')); setClientKey(''); setError(''); setNotice('')
  }
  function change(value: Partial<Config>) { setDraft(previous => previous ? { ...previous, config: { ...previous.config, ...value } } : previous) }
  function addVwp() {
    setDraft(null); setError(''); setNotice('')
    setVwpDraft({ id: crypto.randomUUID(), revision: 0, client_key_registered: false, config: {
      label: '', enabled: true, execution_site: 'vwp', profile_id: '', api: 'chat_completions', model: '', models: [],
      json_mode: false, timeout: 120, max_input_bytes: 1048576, max_output_bytes: 2097152, batch: defaults().batch,
    } })
  }
  function closeEditor() { setDraft(null); setVwpDraft(null); setClientKey('') }
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!draft || !ready || busy) return
    const key = clientKey.trim()
    setClientKey(''); setBusy(true); setError(''); setNotice('')
    try {
      await api(`/api/admin/ai/connections/${draft.id}`, { expected_revision: draft.revision, config: draft.config,
        ...(gateway && key ? { client_api_key: key } : {}) })
      setDraft(null); await load(); setNotice('AI 연결을 저장했습니다. 주소·인증 변경 시 사용자는 개인 인증값을 다시 등록해야 합니다.')
      window.dispatchEvent(new Event('sanity-ai-token-changed'))
    } catch { setError('저장하지 못했습니다. 필수 입력·모델 한도와 최신 개정을 확인해 주세요. 비밀값은 필요하면 다시 입력해 주세요.') }
    finally { setBusy(false) }
  }
  async function toggle(entry: ManagedConnection) {
    setBusy(true); setError(''); setNotice('')
    try {
      await api(`/api/admin/ai/connections/${entry.id}`, { expected_revision: entry.revision, config: { ...entry.config, enabled: !entry.config.enabled } })
      await load(); setNotice(entry.config.enabled ? '연결 사용을 중지했습니다. 기존 이력과 개인 토큰은 보존됩니다.' : '연결 사용을 허용했습니다.')
      window.dispatchEvent(new Event('sanity-ai-token-changed'))
    } catch { setError('상태를 변경하지 못했습니다. 목록을 새로고침해 현재 설정을 확인해 주세요.') }
    finally { setBusy(false) }
  }

  return <section className="am-panel ai-connections-admin" aria-label="AI 연결·모델 관리">
    <div className="am-panel-heading"><div><h2><Settings2 size={17} />{editing ? '연결 설정' : '등록된 AI 연결'}</h2><p>{editing ? '연결 정보와 모델 설정을 확인한 뒤 저장하세요.' : 'OA와 VWP의 AI 연결과 기본 모델을 관리합니다.'}</p></div><div className="am-row-actions">{editing ? <Button variant="outline" disabled={busy} onClick={closeEditor}>목록으로</Button> : <><Button variant="outline" disabled={busy} onClick={() => { setError(''); void load().catch(() => setError('목록을 불러오지 못했습니다.')) }}><RefreshCw size={14} />새로고침</Button><Button variant="outline" disabled={!ready || busy || loading} onClick={() => edit()}><Plus size={15} />OA AI 연결 추가</Button><Button disabled={busy || loading} onClick={addVwp}><Plus size={15} />VWP AI 연결 추가</Button></>}</div></div>
    {error && <p role="alert" className="am-error">{error}</p>}{notice && <p role="status" className="am-notice">{notice}</p>}
    {!loading && !ready && (draft || items.some(entry => !isVwpConnection(entry))) && <p role="alert" className="am-error">OA 연결의 인증값 암호화 키가 준비되지 않았습니다. 운영 담당자가 웹과 worker에 같은 SANITY_OA_AI_TOKEN_STORE_SECRET을 설정해야 합니다.</p>}
    {loading ? <p role="status">AI 연결을 불러오고 있습니다.</p> : !editing && <>
      <ul className="am-list ai-connection-list">{items.map(entry => <li key={entry.id} data-enabled={entry.config.enabled}><div><div className="ai-connection-title"><strong>{entry.config.label}</strong><Badge variant="outline">{isVwpConnection(entry) ? 'VWP' : 'OA'}</Badge><Badge variant="outline" className="ai-connection-status">{entry.config.enabled ? '사용 중' : '사용 중지'}</Badge></div><span>{isVwpConnection(entry) ? 'VWP 실행 · 인증은 VWP에서 관리' : modes[entry.config.auth_mode]} · 모델 {entry.config.models.length}개</span><span className="ai-connection-default">기본 모델 <code>{entry.config.model}</code></span></div><div className="am-row-actions"><Button variant="outline" disabled={busy || editing} onClick={() => edit(entry)}>수정</Button><Button variant="outline" disabled={busy || editing || !isVwpConnection(entry) && !ready || isVwpConnection(entry) && !entry.config.enabled && !vwpProfiles.some(profile => profile.id === entry.config.profile_id)} onClick={() => void toggle(entry)}>{entry.config.enabled ? '사용 중지' : '사용 허용'}</Button></div></li>)}</ul>
      {!items.length && <div className="am-empty"><Settings2 size={26} /><h3>등록된 AI 연결이 없습니다</h3><p>AI 연결을 추가하고 인증 방식과 기본 모델을 설정해 주세요.</p></div>}
    </>}
    {vwpDraft && <VwpAIConnectionForm key={vwpDraft.id} api={api} initial={vwpDraft} profiles={vwpProfiles} ready={vwpReady} onBusyChange={setBusy} onCancel={closeEditor} onSaved={async () => { setVwpDraft(null); await load(); setNotice('VWP AI 연결을 저장했습니다.') }} />}
    {draft && config && <form className="am-form ai-connection-form" onSubmit={save}>
      <div className="ai-connection-form-heading"><h3>{draft.revision ? 'AI 연결 수정' : 'AI 연결 추가'}</h3><p>연결 정보와 사용할 모델을 설정하세요. 처리 한도는 아래 고급 설정에서 조정할 수 있습니다.</p></div>
      <fieldset className="ai-form-section"><legend>연결 정보</legend>
      <label>연결 이름<Input aria-label="AI 연결 이름" required maxLength={100} disabled={busy} value={config.label} onChange={event => change({ label: event.target.value })} /></label>
      <label>AI API URL<Input aria-label="AI API URL" type="url" required disabled={busy} value={config.endpoint} onChange={event => change({ endpoint: event.target.value })} placeholder="http://… 또는 https://…/v1/chat/completions" /></label>
      <label>보조 API URL<Input aria-label="보조 API URL" type="url" disabled={busy} value={config.fallback_endpoint} onChange={event => change({ fallback_endpoint: event.target.value })} /><small>같은 모델·인증의 주소입니다. 요청 전 연결 실패에만 사용합니다.</small></label>
      <div className="ai-connection-fields"><label>API 종류<select className="studio-native-select" aria-label="AI API 종류" disabled={busy || gateway} value={config.api} onChange={event => { const api = event.target.value as Config['api']; change({ api, batch: { ...config.batch, output_parameter: api === 'responses' ? 'max_output_tokens' : 'max_tokens' } }) }}><option value="chat_completions">Chat Completions</option><option value="responses">Responses</option></select></label>
        <label>인증 방식<select className="studio-native-select" aria-label="AI 인증 방식" value={config.auth_mode} disabled={busy} onChange={event => {
          const auth_mode = event.target.value as Config['auth_mode']; setClientKey('')
          change({ auth_mode, ...(auth_mode === 'bearer_x_client' ? { api: 'chat_completions', session_mode: 'per_request', batch: { ...config.batch, output_parameter: 'max_tokens' } } : { client_name: '', client_version: '', session_mode: 'disabled', session_id: '' }) })
        }}>{Object.entries(modes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
      {gateway && <fieldset className="ai-connection-gateway"><legend>사내 게이트웨이 공통 정보</legend>
        <label>공통 x-client-api-key<Input aria-label="공통 x-client-api-key" type="password" autoComplete="off" disabled={busy} required={!draft.client_key_registered} value={clientKey} maxLength={8192} onChange={event => setClientKey(event.target.value)} placeholder={draft.client_key_registered ? '등록됨 · 비워 두면 유지' : '공통 클라이언트 키'} /></label>
        <label>클라이언트 이름<Input aria-label="AI 클라이언트 이름" required disabled={busy} value={config.client_name} maxLength={200} onChange={event => change({ client_name: event.target.value })} /></label>
        <label>클라이언트 버전<Input aria-label="AI 클라이언트 버전" required disabled={busy} value={config.client_version} maxLength={200} onChange={event => change({ client_version: event.target.value })} /></label>
        <label>세션 방식<select className="studio-native-select" aria-label="AI 세션 방식" disabled={busy} value={config.session_mode} onChange={event => change({ session_mode: event.target.value, session_id: '' })}><option value="per_request">요청별 새 세션</option><option value="fixed">고정 세션</option></select></label>
        {config.session_mode === 'fixed' && <label>고정 세션 ID<Input aria-label="AI 고정 세션 ID" required disabled={busy} value={config.session_id} maxLength={200} onChange={event => change({ session_id: event.target.value })} /></label>}
        <p>공통 키는 암호화해 저장합니다. 사용자의 Bearer 토큰은 개인 설정에서 등록합니다.</p>
      </fieldset>}
      </fieldset>
      <fieldset className="ai-form-section"><legend>모델·개인 인증값</legend>
      <label>허용 모델 · 한 줄에 하나<Textarea aria-label="허용 AI 모델 목록" required disabled={busy} rows={4} maxLength={20100} value={models} onChange={event => {
        const text = event.target.value; const values = text.split(/\r?\n/).map(value => value.trim()).filter(Boolean); setModels(text)
        change({ models: values, model: values.includes(config.model) ? config.model : '' })
      }} /></label>
      <label>기본 모델<select className="studio-native-select" aria-label="기본 AI 모델" required disabled={busy} value={config.model} onChange={event => change({ model: event.target.value })}><option value="" disabled>기본 모델을 선택하세요</option>{config.models.map((model, i) => <option key={`${model}:${i}`} value={model}>{model}</option>)}</select><small>사용자가 별도로 선택하지 않으면 분류·판정에 이 모델을 사용합니다.</small></label>
      <label>개인 토큰·API 키 발급 URL<Input aria-label="AI 인증값 발급 URL" type="url" disabled={busy} value={config.issuance_url} onChange={event => change({ issuance_url: event.target.value })} /><small>로그인한 사용자에게 표시할 HTTPS 발급 페이지입니다.</small></label>
      <label className="ai-checkbox"><input type="checkbox" disabled={busy} checked={config.enabled} onChange={event => change({ enabled: event.target.checked })} />이 연결 사용</label>
      </fieldset>
      <AIProcessingLimits config={config} busy={busy} onChange={change} />
      <div className="am-row-actions"><Button type="button" variant="outline" disabled={busy} onClick={() => { setDraft(null); setClientKey('') }}>취소</Button><Button type="submit" disabled={busy || !ready || !config.models.length}>{busy ? '저장 중…' : 'AI 연결 저장'}</Button></div>
    </form>}
  </section>
}
