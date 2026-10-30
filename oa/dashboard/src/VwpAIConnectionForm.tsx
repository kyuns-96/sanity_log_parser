import { useState } from 'react'
import type { FormEvent } from 'react'
import { Button } from './studio/components/ui/button'
import { Input } from './studio/components/ui/input'
import { Badge } from './studio/components/ui/badge'
import type { AccessApi } from './accessApi'
import type { VwpConnectionEntry, VwpConnectionConfig, VwpProfile } from './aiConnectionTypes'
import AIProcessingLimits from './AIProcessingLimits'

export default function VwpAIConnectionForm({ api, initial, profiles, ready, credentialsReady, onBusyChange, onSaved, onCancel }: {
  api: AccessApi; initial: VwpConnectionEntry; profiles: VwpProfile[]; ready: boolean; credentialsReady: boolean;
  onBusyChange: (busy: boolean) => void; onSaved: () => Promise<void>; onCancel: () => void;
}) {
  const [config, setConfig] = useState<VwpConnectionConfig>(() => structuredClone(initial.config))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const profile = profiles.find(value => value.id === config.profile_id)
  const unavailableModels = config.models.filter(model => !profile?.models.includes(model))
  const availableModels = config.models.filter(model => profile?.models.includes(model))
  const validDefault = availableModels.includes(config.model)
  const change = (value: Partial<VwpConnectionConfig>) => setConfig(previous => ({ ...previous, ...value }))
  function chooseProfile(id: string) {
    const next = profiles.find(value => value.id === id)
    if (!next) return
    change({ profile_id: id, api: next.api, models: [...next.models], model: '',
      timeout: Math.min(120, next.timeout), max_input_bytes: Math.min(1048576, next.max_input_bytes),
      max_output_bytes: Math.min(2097152, next.max_output_bytes),
      batch: { ...config.batch, output_parameter: next.api === 'responses' ? 'max_output_tokens' : 'max_tokens' } })
  }
  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy || !ready || !profile || unavailableModels.length || !validDefault) return
    setBusy(true); onBusyChange(true); setError('')
    try {
      await api(`/api/admin/ai/connections/${initial.id}`, { expected_revision: initial.revision, config })
      await onSaved()
      window.dispatchEvent(new Event('sanity-ai-token-changed'))
    } catch { setError('저장하지 못했습니다. 실행 프로필·모델·처리 한도와 최신 개정을 확인해 주세요.') }
    finally { setBusy(false); onBusyChange(false) }
  }
  return <form className="am-form ai-connection-form vwp-ai-connection-form" aria-label="VWP AI 연결 등록" onSubmit={save}>
    <div className="ai-connection-form-heading"><Badge variant="outline" className="ai-provider-badge">VWP</Badge><p>실행 프로필과 모델을 선택하세요. API 주소와 공통 연결 정보는 VWP에서 관리하고 개인 인증값은 사용자가 등록합니다.</p></div>
    {error && <p className="am-error ai-form-feedback" role="alert">{error}</p>}
    {((!ready && credentialsReady) || !profiles.length) && <p className="am-notice ai-form-feedback" role="status">사용 가능한 VWP 실행 프로필이 없습니다. VWP 운영 담당자가 연결을 준비한 뒤 목록을 새로고침해 주세요.</p>}
    <fieldset className="ai-form-section"><legend>VWP 실행 연결</legend>
      <div className="ai-section-content">
      <label>연결 이름<Input aria-label="VWP AI 연결 이름" required maxLength={100} disabled={busy} value={config.label} onChange={event => change({ label: event.target.value })} /></label>
      <label>실행 프로필<select className="studio-native-select" aria-label="VWP AI 실행 프로필" required disabled={busy || !ready} value={config.profile_id} onChange={event => chooseProfile(event.target.value)}><option value="" disabled>VWP 실행 프로필을 선택하세요</option>{config.profile_id && !profile && <option value={config.profile_id} disabled>{config.profile_id} · 현재 사용 불가</option>}{profiles.map(value => <option key={value.id} value={value.id}>{value.label}</option>)}</select><small>VWP 운영 담당자가 제공한 모델 연결입니다.</small></label>
      <dl className="vwp-ai-profile-details"><div><dt>실행 위치</dt><dd>VWP</dd></div><div><dt>인증 방식</dt><dd>사용자별 개인 인증값</dd></div>{profile && <><div><dt>API 종류</dt><dd>{profile.api === 'responses' ? 'Responses' : 'Chat Completions'}</dd></div><div><dt>최대 응답 대기</dt><dd>{profile.timeout}초</dd></div></>}</dl>
      <label>개인 토큰·API 키 발급 URL<Input aria-label="VWP AI 인증값 발급 URL" type="url" disabled={busy} value={config.issuance_url || ''} onChange={event => change({ issuance_url: event.target.value })} /><small>사용자에게 표시할 HTTPS 발급 페이지입니다.</small></label>
      <label className="ai-checkbox"><input type="checkbox" disabled={busy} checked={config.enabled} onChange={event => change({ enabled: event.target.checked })} /><span>이 연결 사용</span></label>
      </div>
    </fieldset>
    <fieldset className="ai-form-section"><legend>모델 선택</legend>
      <div className="ai-section-content">
      <div><strong className="ai-model-heading">사용을 허용할 모델</strong><p className="ai-model-help">사용자가 선택할 수 있는 모델을 지정하세요.</p></div>
      {!profile && !config.models.length && <p className="ai-model-help">실행 프로필을 선택하면 모델 목록이 표시됩니다.</p>}
      {profile && !profile.models.length && <p className="ai-model-help">이 실행 프로필에 등록된 모델이 없습니다.</p>}
      {unavailableModels.length > 0 && <p className="ai-model-help" role="status">현재 프로필에서 사용할 수 없는 모델이 있습니다. 저장하기 전에 선택을 해제해 주세요.</p>}
      <div className="vwp-ai-model-options" role="group" aria-label="VWP 허용 AI 모델 목록">{[...(profile?.models || []), ...unavailableModels].map(model => {
        const unavailable = unavailableModels.includes(model)
        return <label className="ai-checkbox ai-model-option" data-unavailable={unavailable} key={model}><input type="checkbox" aria-label={model} disabled={busy} checked={config.models.includes(model)} onChange={event => {
        const models = event.target.checked ? [...config.models, model] : config.models.filter(value => value !== model)
        change({ models, model: models.includes(config.model) ? config.model : '' })
      }} /><span>{model}{unavailable && <small> · 현재 프로필에서 사용 불가</small>}</span></label>
      })}</div>
      <label>기본 모델<select className="studio-native-select" aria-label="VWP 기본 AI 모델" required disabled={busy || !profile} value={validDefault ? config.model : ''} onChange={event => change({ model: event.target.value })}><option value="" disabled>기본 모델을 선택하세요</option>{availableModels.map(model => <option value={model} key={model}>{model}</option>)}</select><small>{config.model && !validDefault ? '기존 기본 모델을 사용할 수 없습니다. 기본 모델을 다시 선택해 주세요.' : '사용자의 최초 선택과 연결 변경 시 사용할 모델입니다.'}</small></label>
      </div>
    </fieldset>
    <AIProcessingLimits config={config} busy={busy} onChange={change} />
    <div className="am-row-actions"><Button type="button" variant="outline" disabled={busy} onClick={onCancel}>취소</Button><Button type="submit" disabled={busy || !ready || !profile || !!unavailableModels.length || !validDefault}>{busy ? '저장 중…' : 'VWP AI 연결 저장'}</Button></div>
  </form>
}
