import { useState } from 'react'
import type { FormEvent } from 'react'
import { Button } from './studio/components/ui/button'
import { Input } from './studio/components/ui/input'
import type { AccessApi } from './accessApi'
import type { VwpConnectionEntry, VwpConnectionConfig, VwpProfile } from './aiConnectionTypes'
import AIProcessingLimits from './AIProcessingLimits'

export default function VwpAIConnectionForm({ api, initial, profiles, ready, onBusyChange, onSaved, onCancel }: {
  api: AccessApi; initial: VwpConnectionEntry; profiles: VwpProfile[]; ready: boolean;
  onBusyChange: (busy: boolean) => void; onSaved: () => Promise<void>; onCancel: () => void;
}) {
  const [config, setConfig] = useState<VwpConnectionConfig>(() => structuredClone(initial.config))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const profile = profiles.find(value => value.id === config.profile_id)
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
    if (busy || !ready || !profile || !config.models.includes(config.model)) return
    setBusy(true); onBusyChange(true); setError('')
    try {
      await api(`/api/admin/ai/connections/${initial.id}`, { expected_revision: initial.revision, config })
      await onSaved()
      window.dispatchEvent(new Event('sanity-ai-token-changed'))
    } catch { setError('저장하지 못했습니다. 실행 프로필·모델·처리 한도와 최신 개정을 확인해 주세요.') }
    finally { setBusy(false); onBusyChange(false) }
  }
  return <form className="am-form ai-connection-form vwp-ai-connection-form" aria-label="VWP AI 연결 등록" onSubmit={save}>
    <div className="ai-connection-form-heading"><h3>{initial.revision ? 'VWP AI 연결 수정' : 'VWP AI 연결 추가'}</h3><p>VWP에서 사용할 실행 프로필과 모델을 선택합니다. API 주소와 인증값은 VWP에서 관리합니다.</p></div>
    {error && <p className="am-error" role="alert">{error}</p>}
    {(!ready || !profiles.length) && <p className="am-notice" role="status">사용 가능한 VWP 실행 프로필이 없습니다. VWP 운영 담당자가 연결을 준비한 뒤 목록을 새로고침해 주세요.</p>}
    <fieldset className="ai-form-section"><legend>VWP 실행 연결</legend>
      <label>연결 이름<Input aria-label="VWP AI 연결 이름" required maxLength={100} disabled={busy} value={config.label} onChange={event => change({ label: event.target.value })} /></label>
      <label>실행 프로필<select className="studio-native-select" aria-label="VWP AI 실행 프로필" required disabled={busy || !ready} value={config.profile_id} onChange={event => chooseProfile(event.target.value)}><option value="" disabled>VWP 실행 프로필을 선택하세요</option>{config.profile_id && !profile && <option value={config.profile_id} disabled>{config.profile_id} · 현재 사용 불가</option>}{profiles.map(value => <option key={value.id} value={value.id}>{value.label}</option>)}</select><small>VWP 운영 담당자가 제공한 모델 연결입니다.</small></label>
      <dl className="vwp-ai-profile-details"><div><dt>실행 위치</dt><dd>VWP</dd></div><div><dt>인증 관리</dt><dd>VWP에서 관리</dd></div>{profile && <><div><dt>API 종류</dt><dd>{profile.api === 'responses' ? 'Responses' : 'Chat Completions'}</dd></div><div><dt>최대 응답 대기</dt><dd>{profile.timeout}초</dd></div></>}</dl>
      <label className="ai-checkbox"><input type="checkbox" disabled={busy} checked={config.enabled} onChange={event => change({ enabled: event.target.checked })} />이 연결 사용</label>
    </fieldset>
    <fieldset className="ai-form-section"><legend>모델 선택</legend>
      <div className="vwp-ai-model-options" role="group" aria-label="VWP 허용 AI 모델 목록">{(profile?.models || config.models).map(model => <label className="ai-checkbox" key={model}><input type="checkbox" disabled={busy || !profile} checked={config.models.includes(model)} onChange={event => {
        const models = event.target.checked ? [...config.models, model] : config.models.filter(value => value !== model)
        change({ models, model: models.includes(config.model) ? config.model : '' })
      }} /><span>{model}</span></label>)}</div>
      <label>기본 모델<select className="studio-native-select" aria-label="VWP 기본 AI 모델" required disabled={busy || !profile} value={config.model} onChange={event => change({ model: event.target.value })}><option value="" disabled>기본 모델을 선택하세요</option>{config.models.map(model => <option value={model} key={model}>{model}</option>)}</select><small>사용자의 최초 선택과 연결 변경 시 사용할 모델입니다.</small></label>
    </fieldset>
    <AIProcessingLimits config={config} busy={busy} onChange={change} />
    <div className="am-row-actions"><Button type="button" variant="outline" disabled={busy} onClick={onCancel}>취소</Button><Button type="submit" disabled={busy || !ready || !profile || !config.models.length || !config.model}>{busy ? '저장 중…' : 'VWP AI 연결 저장'}</Button></div>
  </form>
}
