import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Plus, RefreshCw, Settings2 } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Badge } from './studio/components/ui/badge'
import { Input } from './studio/components/ui/input'
import { Textarea } from './studio/components/ui/textarea'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './components/ui/dialog'
import { DashboardRequestError } from './useDashboardAuth'
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
  const [loaded, setLoaded] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [draft, setDraft] = useState<Entry | null>(null)
  const [models, setModels] = useState('')
  const [clientKey, setClientKey] = useState('')
  const [deleting, setDeleting] = useState<ManagedConnection | null>(null)
  const [deleteError, setDeleteError] = useState('')
  const loadRequest = useRef(0)
  const config = draft?.config
  const editing = !!draft || !!vwpDraft
  const gateway = config?.auth_mode === 'bearer_x_client'
  const validModels = !!config?.models.length && new Set(config.models).size === config.models.length && config.models.includes(config.model)
  const vwpCount = items.filter(isVwpConnection).length
  const editorTitle = vwpDraft ? `VWP AI 연결 ${vwpDraft.revision ? '수정' : '추가'}` : `OA AI 연결 ${draft?.revision ? '수정' : '추가'}`

  async function load(request: number) {
    const result = await api<{ items: ManagedConnection[]; credentials_ready: boolean; vwp_ready?: boolean; vwp_profiles?: VwpProfile[] }>('/api/admin/ai/connections')
    if (request !== loadRequest.current) return
    setItems(result.items); setReady(result.credentials_ready); setVwpReady(result.vwp_ready === true); setVwpProfiles(result.vwp_profiles || []); setLoaded(true)
  }
  async function refresh() {
    const request = ++loadRequest.current
    setRefreshing(true); setLoadError('')
    try { await load(request) }
    catch { if (request === loadRequest.current) setLoadError('연결 목록을 불러오지 못했습니다. 새로고침해 현재 설정을 확인해 주세요.') }
    finally { if (request === loadRequest.current) setRefreshing(false) }
  }
  async function saved(message: string) {
    closeEditor(); setNotice(message)
    await refresh()
  }
  useEffect(() => {
    let active = true
    const request = ++loadRequest.current
    api<{ items: ManagedConnection[]; credentials_ready: boolean; vwp_ready?: boolean; vwp_profiles?: VwpProfile[] }>('/api/admin/ai/connections').then(value => {
      if (active && request === loadRequest.current) { setItems(value.items); setReady(value.credentials_ready); setVwpReady(value.vwp_ready === true); setVwpProfiles(value.vwp_profiles || []); setLoaded(true) }
    }).catch(() => { if (active && request === loadRequest.current) setLoadError('AI 연결 목록을 불러오지 못했습니다. 관리자 권한과 연결 상태를 확인한 뒤 새로고침해 주세요.') })
      .finally(() => { if (active && request === loadRequest.current) setLoading(false) })
    return () => { active = false }
  }, [api])

  function edit(value?: ManagedConnection) {
    if (value && isVwpConnection(value)) { setDraft(null); setVwpDraft(structuredClone(value)); setError(''); setNotice(''); return }
    const next = value ? structuredClone(value) : { id: crypto.randomUUID(), revision: 0, config: defaults(), client_key_registered: false }
    setVwpDraft(null); setDraft(next); setModels(next.config.models.join('\n')); setClientKey(''); setError(''); setNotice('')
  }
  function change(value: Partial<Config>) { setDraft(previous => previous ? { ...previous, config: { ...previous.config, ...value } } : previous) }
  function addVwp() {
    setDraft(null); setError(''); setNotice('')
    setVwpDraft({ id: crypto.randomUUID(), revision: 0, client_key_registered: false, config: {
      label: '', enabled: true, execution_site: 'vwp', profile_id: '', api: 'chat_completions', model: '', models: [], issuance_url: '',
      json_mode: false, timeout: 120, max_input_bytes: 1048576, max_output_bytes: 2097152, batch: defaults().batch,
    } })
  }
  function closeEditor() { setDraft(null); setVwpDraft(null); setClientKey('') }
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!draft || !ready || busy || !validModels) return
    const key = clientKey.trim()
    setClientKey(''); setBusy(true); setError(''); setNotice('')
    try {
      await api(`/api/admin/ai/connections/${draft.id}`, { expected_revision: draft.revision, config: draft.config,
        ...(gateway && key ? { client_api_key: key } : {}) })
      window.dispatchEvent(new Event('sanity-ai-token-changed'))
      await saved('AI 연결을 저장했습니다. 주소·인증 변경 시 사용자는 개인 인증값을 다시 등록해야 합니다.')
    } catch { setError('저장하지 못했습니다. 필수 입력·모델 한도와 최신 개정을 확인해 주세요. 비밀값은 필요하면 다시 입력해 주세요.') }
    finally { setBusy(false) }
  }
  async function toggle(entry: ManagedConnection) {
    setBusy(true); setError(''); setNotice('')
    try {
      await api(`/api/admin/ai/connections/${entry.id}`, { expected_revision: entry.revision, config: { ...entry.config, enabled: !entry.config.enabled } })
      setNotice(entry.config.enabled ? '연결 사용을 중지했습니다. 기존 이력과 개인 토큰은 보존됩니다.' : '연결 사용을 허용했습니다.')
      window.dispatchEvent(new Event('sanity-ai-token-changed'))
      await refresh()
    } catch { setError('상태를 변경하지 못했습니다. 목록을 새로고침해 현재 설정을 확인해 주세요.') }
    finally { setBusy(false) }
  }
  async function deleteConnection() {
    if (!deleting || busy) return
    const entry = deleting
    setBusy(true); setDeleteError(''); setError(''); setNotice('')
    try {
      const value = await api<{ id: string; revision: number; deleted: boolean }>(`/api/admin/ai/connections/${encodeURIComponent(entry.id)}/delete`, { expected_revision: entry.revision })
      if (value.id !== entry.id || value.deleted !== true || !Number.isInteger(value.revision)) throw new Error('Invalid deletion response')
      ++loadRequest.current
      setItems(previous => previous.filter(item => item.id !== entry.id))
      setDeleting(null)
      setNotice(`${entry.config.label} 연결을 삭제했습니다. 기존 작업과 판정 이력은 유지됩니다.`)
      window.dispatchEvent(new Event('sanity-ai-token-changed'))
      await refresh()
    } catch (failure) {
      setDeleteError(failure instanceof DashboardRequestError && failure.status >= 400 && failure.status < 500 && failure.status !== 408
        ? '삭제하지 못했습니다. 권한과 최신 연결 상태를 확인해 주세요.'
        : '삭제 요청 결과를 확인하지 못했습니다. 다시 누르면 같은 연결 개정으로 확인합니다.')
    } finally { setBusy(false) }
  }

  return <section className="am-panel ai-connections-admin" aria-label="AI 연결·모델 관리">
    <div className="am-panel-heading"><div><h2><Settings2 size={17} />{editing ? editorTitle : '등록된 AI 연결'}</h2><p>{editing ? '연결과 기본 모델을 설정하세요. 처리 한도는 고급 설정에서 조정할 수 있습니다.' : 'OA와 VWP의 연결별 모델과 사용 상태를 관리합니다.'}</p></div><div className="am-row-actions"><Button variant="outline" disabled={busy || loading || refreshing} onClick={() => { setError(''); void refresh() }}><RefreshCw size={14} />{refreshing ? '새로고침 중…' : '새로고침'}</Button>{editing ? <Button variant="outline" disabled={busy} onClick={closeEditor}>목록으로</Button> : <><Button variant="outline" disabled={!ready || busy || loading || !loaded} onClick={() => edit()}><Plus size={15} />OA AI 연결 추가</Button><Button disabled={!ready || !vwpReady || busy || loading || !loaded} onClick={addVwp}><Plus size={15} />VWP AI 연결 추가</Button></>}</div></div>
    {error && <p role="alert" className="am-error">{error}</p>}{notice && <p role="status" className="am-notice">{notice}</p>}
    {loadError && <p role="alert" className="am-error">{loadError}{loaded && ' 목록은 마지막으로 조회한 내용입니다.'}</p>}
    {!loading && loaded && !ready && <p className="ai-availability-note">OA와 VWP의 개인 인증값을 저장하려면 운영 담당자가 암호화 저장 설정을 준비해야 합니다.</p>}
    {!loading && loaded && !vwpReady && !editing && <p className="ai-availability-note">VWP 실행 프로필이 준비되지 않았습니다. 운영 담당자에게 확인해 주세요.</p>}
    {loading ? <p role="status" className="ai-connection-loading">AI 연결을 불러오고 있습니다.</p> : !editing && loaded ? <>
      {!!items.length && <><div className="ai-catalog-summary"><span>전체 <strong>{items.length}</strong></span><span>사용 중 <strong>{items.filter(entry => entry.config.enabled).length}</strong></span><span>OA <strong>{items.length - vwpCount}</strong></span><span>VWP <strong>{vwpCount}</strong></span></div><div className="ai-list-heading" aria-hidden="true"><span>연결</span><span>기본 모델</span><span>상태</span><span>관리</span></div></>}
      <ul className="am-list ai-connection-list">{items.map(entry => <li className="ai-connection-row" key={entry.id} data-enabled={entry.config.enabled}><div><div className="ai-connection-title"><Badge variant="outline" className="ai-provider-badge">{isVwpConnection(entry) ? 'VWP' : 'OA'}</Badge><strong>{entry.config.label}</strong></div><span>{isVwpConnection(entry) ? '사용자별 개인 인증값' : modes[entry.config.auth_mode]}</span></div><div className="ai-connection-model"><code title={entry.config.model}>{entry.config.model}</code><span>허용 모델 {entry.config.models.length}개</span></div><div className="ai-connection-row-status"><Badge variant="outline" className="ai-connection-status">{entry.config.enabled ? '사용 중' : '사용 중지'}</Badge></div><div className="am-row-actions"><Button variant="outline" disabled={busy} onClick={() => edit(entry)}>수정</Button><Button variant="outline" disabled={busy || !entry.config.enabled && (!ready || isVwpConnection(entry) && (!vwpReady || !vwpProfiles.some(profile => profile.id === entry.config.profile_id)))} onClick={() => void toggle(entry)}>{entry.config.enabled ? '사용 중지' : '사용 허용'}</Button><Button variant="outline" className="ai-connection-delete" disabled={busy} onClick={() => { setDeleteError(''); setDeleting(entry) }}>삭제</Button></div></li>)}</ul>
      {!items.length && <div className="am-empty"><Settings2 size={26} /><h3>등록된 AI 연결이 없습니다</h3><p>AI 연결을 추가하고 인증 방식과 기본 모델을 설정해 주세요.</p></div>}
    </> : !editing && loadError ? <div className="am-empty"><RefreshCw size={26} /><h3>연결 목록을 확인할 수 없습니다</h3><p>새로고침하여 등록된 AI 연결을 다시 불러와 주세요.</p></div> : null}
    <Dialog open={!!deleting} onOpenChange={value => { if (!value && !busy) { setDeleting(null); setDeleteError('') } }}>
      <DialogContent className="ai-delete-dialog" showCloseButton={!busy}>
        <DialogHeader><DialogTitle>AI 연결을 삭제할까요?</DialogTitle><DialogDescription>삭제할 연결과 영향을 확인해 주세요.</DialogDescription></DialogHeader>
        {deleting && <div className="ai-delete-details"><strong>{deleting.config.label}</strong><span>기본 모델 <code>{deleting.config.model}</code></span></div>}
        <p className="ai-delete-explanation">연결이 관리자 목록과 사용자의 모델 선택에서 제거됩니다. 기존 작업과 판정 이력은 보존되며, 진행 중인 작업은 중단될 수 있습니다.</p>
        {deleteError && <p role="alert" className="am-error">{deleteError}</p>}
        <DialogFooter><Button type="button" variant="outline" disabled={busy} onClick={() => { setDeleting(null); setDeleteError('') }}>취소</Button><Button type="button" className="ai-delete-confirm" disabled={busy} onClick={() => void deleteConnection()}>{busy ? '삭제 확인 중…' : '연결 삭제'}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    {vwpDraft && <VwpAIConnectionForm key={vwpDraft.id} api={api} initial={vwpDraft} profiles={vwpProfiles} ready={vwpReady && ready} credentialsReady={ready} onBusyChange={setBusy} onCancel={closeEditor} onSaved={() => saved('VWP AI 연결을 저장했습니다.')} />}
    {draft && config && <form className="am-form ai-connection-form" aria-label="OA AI 연결 등록" onSubmit={save}>
      <div className="ai-connection-form-heading"><Badge variant="outline" className="ai-provider-badge">OA</Badge><p>모델 API와 사용자의 개인 인증값 발급 경로를 등록합니다.</p></div>
      <fieldset className="ai-form-section"><legend>연결 정보</legend>
      <div className="ai-section-content">
      <label>연결 이름<Input aria-label="AI 연결 이름" required maxLength={100} disabled={busy} value={config.label} onChange={event => change({ label: event.target.value })} /></label>
      <label>AI API URL<Input aria-label="AI API URL" type="url" required disabled={busy} value={config.endpoint} onChange={event => change({ endpoint: event.target.value })} placeholder="http://… 또는 https://…/v1/chat/completions" /></label>
      <label>보조 API URL<Input aria-label="보조 API URL" type="url" disabled={busy} value={config.fallback_endpoint} onChange={event => change({ fallback_endpoint: event.target.value })} /><small>같은 모델·인증의 주소입니다. 요청 전 연결 실패에만 사용합니다.</small></label>
      <div className="ai-connection-fields"><label>API 종류<select className="studio-native-select" aria-label="AI API 종류" disabled={busy || gateway} value={config.api} onChange={event => { const api = event.target.value as Config['api']; change({ api, batch: { ...config.batch, output_parameter: api === 'responses' ? 'max_output_tokens' : 'max_tokens' } }) }}><option value="chat_completions">Chat Completions</option><option value="responses">Responses</option></select></label>
        <label>인증 방식<select className="studio-native-select" aria-label="AI 인증 방식" value={config.auth_mode} disabled={busy} onChange={event => {
          const auth_mode = event.target.value as Config['auth_mode']; setClientKey('')
          change({ auth_mode, ...(auth_mode === 'bearer_x_client' ? { api: 'chat_completions', session_mode: 'per_request', batch: { ...config.batch, output_parameter: 'max_tokens' } } : { client_name: '', client_version: '', session_mode: 'disabled', session_id: '' }) })
        }}>{Object.entries(modes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
      <label className="ai-checkbox"><input type="checkbox" disabled={busy} checked={config.enabled} onChange={event => change({ enabled: event.target.checked })} /><span>이 연결 사용</span></label>
      </div>
      </fieldset>
      <fieldset className="ai-form-section"><legend>모델·개인 인증값</legend>
      <div className="ai-section-content">
      <label>허용 모델<Textarea aria-label="허용 AI 모델 목록" required disabled={busy} rows={5} maxLength={20100} value={models} placeholder={'모델 ID를 한 줄에 하나씩 입력\n예: model-a\nmodel-b'} onChange={event => {
        const text = event.target.value; const values = text.split(/\r?\n/).map(value => value.trim()).filter(Boolean); setModels(text)
        change({ models: values, model: values.includes(config.model) ? config.model : '' })
      }} /><small>{config.models.length ? `모델 ${config.models.length}개 입력됨` : '정확한 모델 ID를 한 줄에 하나씩 입력하세요.'}{new Set(config.models).size !== config.models.length && ' · 중복된 모델 ID를 제거해 주세요.'}</small></label>
      <label>기본 모델<select className="studio-native-select" aria-label="기본 AI 모델" required disabled={busy || !config.models.length} value={config.model} onChange={event => change({ model: event.target.value })}><option value="" disabled>기본 모델을 선택하세요</option>{config.models.map((model, i) => <option key={`${model}:${i}`} value={model}>{model}</option>)}</select><small>사용자가 별도로 선택하지 않으면 분류·판정에 이 모델을 사용합니다.</small></label>
      <label>개인 토큰·API 키 발급 URL<Input aria-label="AI 인증값 발급 URL" type="url" disabled={busy} value={config.issuance_url} onChange={event => change({ issuance_url: event.target.value })} /><small>로그인한 사용자에게 표시할 HTTPS 발급 페이지입니다.</small></label>
      </div>
      </fieldset>
      {gateway && <fieldset className="ai-form-section ai-connection-gateway"><legend>사내 게이트웨이 공통 정보</legend>
        <div className="ai-section-content ai-gateway-fields">
        <label>공통 x-client-api-key<Input aria-label="공통 x-client-api-key" type="password" autoComplete="off" disabled={busy} required={!draft.client_key_registered} value={clientKey} maxLength={8192} onChange={event => setClientKey(event.target.value)} placeholder={draft.client_key_registered ? '등록됨 · 비워 두면 유지' : '공통 클라이언트 키'} /></label>
        <label>클라이언트 이름<Input aria-label="AI 클라이언트 이름" required disabled={busy} value={config.client_name} maxLength={200} onChange={event => change({ client_name: event.target.value })} /></label>
        <label>클라이언트 버전<Input aria-label="AI 클라이언트 버전" required disabled={busy} value={config.client_version} maxLength={200} onChange={event => change({ client_version: event.target.value })} /></label>
        <label>세션 방식<select className="studio-native-select" aria-label="AI 세션 방식" disabled={busy} value={config.session_mode} onChange={event => change({ session_mode: event.target.value, session_id: '' })}><option value="per_request">요청별 새 세션</option><option value="fixed">고정 세션</option></select></label>
        {config.session_mode === 'fixed' && <label>고정 세션 ID<Input aria-label="AI 고정 세션 ID" required disabled={busy} value={config.session_id} maxLength={200} onChange={event => change({ session_id: event.target.value })} /></label>}
        <p>공통 키는 암호화해 저장합니다. 사용자의 Bearer 토큰은 개인 설정에서 등록합니다.</p>
        </div>
      </fieldset>}
      <AIProcessingLimits config={config} busy={busy} onChange={change} />
      <div className="am-row-actions"><Button type="button" variant="outline" disabled={busy} onClick={closeEditor}>취소</Button><Button type="submit" disabled={busy || !ready || !validModels}>{busy ? '저장 중…' : 'AI 연결 저장'}</Button></div>
    </form>}
  </section>
}
