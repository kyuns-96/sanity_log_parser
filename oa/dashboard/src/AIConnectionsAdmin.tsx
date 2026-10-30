import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Plus, RefreshCw, Settings2 } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Badge } from './studio/components/ui/badge'
import { Input } from './studio/components/ui/input'
import { Textarea } from './studio/components/ui/textarea'
import type { AccessApi } from './accessApi'
import './ai-connections.css'

type BatchConfig = { enabled: boolean; tokenizer_mode: 'utf8_bytes'; tokenizer_sha256: string; context_tokens: number; input_tokens: number; output_tokens: number; overhead_tokens: number; output_parameter: string; max_records: number; merge_groups: number; max_calls: number; max_rounds: number; deadline_seconds: number }
type Config = { label: string; enabled: boolean; endpoint: string; fallback_endpoint: string; api: 'chat_completions' | 'responses'; auth_mode: 'bearer' | 'bearer_x_client' | 'x_client_api_key'; model: string; models: string[]; issuance_url: string; json_mode: boolean; timeout: number; max_input_bytes: number; max_output_bytes: number; client_name: string; client_version: string; session_mode: string; session_id: string; batch: BatchConfig }
type Entry = { id: string; revision: number; config: Config; client_key_registered: boolean }
const modes = { bearer: 'Bearer', bearer_x_client: 'Bearer + x-client · 사내 게이트웨이', x_client_api_key: 'x-client-api-key 단독' }
const defaults = (): Config => ({ label: '', enabled: true, endpoint: '', fallback_endpoint: '', api: 'chat_completions', auth_mode: 'bearer', model: '', models: [], issuance_url: '', json_mode: false, timeout: 120, max_input_bytes: 1048576, max_output_bytes: 2097152, client_name: '', client_version: '', session_mode: 'disabled', session_id: '', batch: { enabled: false, tokenizer_mode: 'utf8_bytes', tokenizer_sha256: '', context_tokens: 32768, input_tokens: 16000, output_tokens: 4096, overhead_tokens: 1024, output_parameter: 'max_tokens', max_records: 200, merge_groups: 20, max_calls: 20000, max_rounds: 8, deadline_seconds: 86400 } })

export default function AIConnectionsAdmin({ api }: { api: AccessApi }) {
  const [items, setItems] = useState<Entry[]>([])
  const [ready, setReady] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [draft, setDraft] = useState<Entry | null>(null)
  const [models, setModels] = useState('')
  const [clientKey, setClientKey] = useState('')
  const config = draft?.config
  const gateway = config?.auth_mode === 'bearer_x_client'

  async function load() {
    const result = await api<{ items: Entry[]; credentials_ready: boolean }>('/api/admin/ai/connections')
    setItems(result.items); setReady(result.credentials_ready)
  }
  useEffect(() => {
    let active = true
    api<{ items: Entry[]; credentials_ready: boolean }>('/api/admin/ai/connections').then(value => {
      if (active) { setItems(value.items); setReady(value.credentials_ready) }
    }).catch(() => { if (active) setError('AI 연결 목록을 불러오지 못했습니다. 관리자 권한과 연결 상태를 확인해 주세요.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [api])

  function edit(value?: Entry) {
    const next = value ? structuredClone(value) : { id: crypto.randomUUID(), revision: 0, config: defaults(), client_key_registered: false }
    setDraft(next); setModels(next.config.models.join('\n')); setClientKey(''); setError(''); setNotice('')
  }
  function change(value: Partial<Config>) { setDraft(previous => previous ? { ...previous, config: { ...previous.config, ...value } } : previous) }
  function batch(value: Partial<BatchConfig>) { if (config) change({ batch: { ...config.batch, ...value } }) }
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
  async function toggle(entry: Entry) {
    setBusy(true); setError(''); setNotice('')
    try {
      await api(`/api/admin/ai/connections/${entry.id}`, { expected_revision: entry.revision, config: { ...entry.config, enabled: !entry.config.enabled } })
      await load(); setNotice(entry.config.enabled ? '연결 사용을 중지했습니다. 기존 이력과 개인 토큰은 보존됩니다.' : '연결 사용을 허용했습니다.')
      window.dispatchEvent(new Event('sanity-ai-token-changed'))
    } catch { setError('상태를 변경하지 못했습니다. 목록을 새로고침해 현재 설정을 확인해 주세요.') }
    finally { setBusy(false) }
  }

  return <section className="am-panel ai-connections-admin" aria-label="AI 연결·모델 관리">
    <div className="am-panel-heading"><div><h2><Settings2 size={17} />{draft ? '연결 설정' : '등록된 AI 연결'}</h2><p>{draft ? '연결 정보와 모델 설정을 확인한 뒤 저장하세요.' : '연결별 인증 방식과 기본 모델을 확인하고 설정합니다.'}</p></div><div className="am-row-actions">{draft ? <Button variant="outline" disabled={busy} onClick={() => { setDraft(null); setClientKey('') }}>목록으로</Button> : <><Button variant="outline" disabled={busy} onClick={() => { setError(''); void load().catch(() => setError('목록을 불러오지 못했습니다.')) }}><RefreshCw size={14} />새로고침</Button><Button disabled={!ready || busy || loading} onClick={() => edit()}><Plus size={15} />AI 연결 추가</Button></>}</div></div>
    {error && <p role="alert" className="am-error">{error}</p>}{notice && <p role="status" className="am-notice">{notice}</p>}
    {!loading && !ready && <p role="alert" className="am-error">서버의 AI 인증값 암호화 키가 준비되지 않았습니다. 운영 담당자가 웹과 worker에 같은 SANITY_OA_AI_TOKEN_STORE_SECRET을 설정해야 합니다.</p>}
    {loading ? <p role="status">AI 연결을 불러오고 있습니다.</p> : !draft && <>
      <ul className="am-list ai-connection-list">{items.map(entry => <li key={entry.id} data-enabled={entry.config.enabled}><div><div className="ai-connection-title"><strong>{entry.config.label}</strong><Badge variant="outline" className="ai-connection-status">{entry.config.enabled ? '사용 중' : '사용 중지'}</Badge></div><span>{modes[entry.config.auth_mode]} · 모델 {entry.config.models.length}개</span><span className="ai-connection-default">기본 모델 <code>{entry.config.model}</code></span></div><div className="am-row-actions"><Button variant="outline" disabled={busy || !!draft} onClick={() => edit(entry)}>수정</Button><Button variant="outline" disabled={busy || !!draft || !ready} onClick={() => void toggle(entry)}>{entry.config.enabled ? '사용 중지' : '사용 허용'}</Button></div></li>)}</ul>
      {!items.length && <div className="am-empty"><Settings2 size={26} /><h3>등록된 AI 연결이 없습니다</h3><p>AI 연결을 추가하고 인증 방식과 기본 모델을 설정해 주세요.</p></div>}
    </>}
    {draft && config && <form className="am-form ai-connection-form" onSubmit={save}>
      <div className="ai-connection-form-heading"><h3>{draft.revision ? 'AI 연결 수정' : 'AI 연결 추가'}</h3><p>연결 정보와 사용할 모델을 설정하세요. 처리 한도는 아래 고급 설정에서 조정할 수 있습니다.</p></div>
      <fieldset className="ai-form-section"><legend>연결 정보</legend>
      <label>연결 이름<Input aria-label="AI 연결 이름" required maxLength={100} disabled={busy} value={config.label} onChange={event => change({ label: event.target.value })} /></label>
      <label>AI API URL<Input aria-label="AI API URL" type="url" required disabled={busy} value={config.endpoint} onChange={event => change({ endpoint: event.target.value })} placeholder="https://…/v1/chat/completions" /></label>
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
      <details><summary>처리 한도와 전체 로그 배치 설정</summary><div className="ai-connection-fields">
        {([['timeout', '응답 제한 시간(초)', 1, 600], ['max_input_bytes', '요청 byte 한도', 1, 67108864], ['max_output_bytes', '응답 byte 한도', 1, 67108864]] as const).map(([key, label, min, max]) => <label key={key}>{label}<Input type="number" aria-label={label} required min={min} max={max} disabled={busy} value={config[key]} onChange={event => change({ [key]: Number(event.target.value) })} /></label>)}
      </div><label className="ai-checkbox"><input type="checkbox" disabled={busy} checked={config.json_mode} onChange={event => change({ json_mode: event.target.checked })} />JSON object 응답 옵션 사용</label>
        <label className="ai-checkbox"><input type="checkbox" disabled={busy} checked={config.batch.enabled} onChange={event => batch({ enabled: event.target.checked })} />전체 로그·그룹 판정·보존 본문 배치 사용</label>
        <p>모든 허용 모델에서 안전한 공통 한도를 입력하세요. 배치 입력은 보수적인 UTF-8 byte 계수를 사용하며 실제 모델 토큰 수와 다릅니다.</p>
        {config.batch.enabled && <div className="ai-connection-fields">
          {([['context_tokens','모델 context 한도',1,10000000], ['input_tokens','배치 입력 예산',1,1000000], ['output_tokens','출력 예약',1,1000000], ['overhead_tokens','메시지 오버헤드 예약',1,1000000], ['max_records','조각별 최대 로그',1,1000], ['merge_groups','통합별 최대 그룹',2,100], ['max_calls','전체 호출 한도',1,100000], ['max_rounds','통합 라운드 한도',1,32], ['deadline_seconds','전체 작업 제한 시간(초)',60,604800]] as const).map(([key,label,min,max]) => <label key={key}>{label}<Input type="number" aria-label={label} required min={min} max={max} disabled={busy} value={config.batch[key]} onChange={event => batch({ [key]: Number(event.target.value) })} /></label>)}
          <label>출력 한도 파라미터<select className="studio-native-select" aria-label="출력 한도 파라미터" disabled={busy} value={config.batch.output_parameter} onChange={event => batch({ output_parameter: event.target.value })}>{(config.api === 'responses' ? ['max_output_tokens'] : ['max_tokens','max_completion_tokens']).map(value => <option key={value} value={value}>{value}</option>)}</select></label>
        </div>}
      </details>
      <div className="am-row-actions"><Button type="button" variant="outline" disabled={busy} onClick={() => { setDraft(null); setClientKey('') }}>취소</Button><Button type="submit" disabled={busy || !ready || !config.models.length}>{busy ? '저장 중…' : 'AI 연결 저장'}</Button></div>
    </form>}
  </section>
}
