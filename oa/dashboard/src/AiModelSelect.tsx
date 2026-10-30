import { useId } from 'react'
import type { CodexCapability } from './workspaceApi'
import './ai-model-select.css'
import './ai-connections.css'

export const selectedAiConnection = (ai: CodexCapability | undefined, connectionId: string) => {
  const connection = ai?.connections?.find(value => value.id === connectionId)
    || ai?.connections?.find(value => value.id === ai.connection_id)
  return ai && connection ? { ...ai, ...connection, connection_id: connection.id, connection_label: connection.label } : ai
}

export const selectedAiModel = (ai: CodexCapability | undefined, selected: string) =>
  ai?.models?.includes(selected) ? selected : ai?.model || ''

export const needsPersonalCredential = (ai: CodexCapability | undefined) => !!ai?.credential_required && !ai.credential_ready &&
  (!ai.credential_error || ['AI_USER_TOKEN_REQUIRED', 'AI_USER_TOKEN_EXPIRED', 'AI_CREDENTIAL_SCOPE_CHANGED'].includes(ai.credential_error))

export const aiConnectionNotice = (ai: CodexCapability | undefined) => {
  if (ai?.execution_site !== 'vwp' || !ai.credential_error) return null
  if (ai.credential_error === 'AI_USER_TOKEN_REQUIRED') return '이 VWP 연결에 사용할 개인 인증값을 등록해 주세요.'
  if (ai.credential_error === 'AI_USER_TOKEN_EXPIRED') return 'VWP 개인 인증값이 만료되었습니다. 새 인증값을 등록해 주세요.'
  if (ai.credential_error === 'AI_CREDENTIAL_SCOPE_CHANGED') return 'VWP 연결 설정이 변경되었습니다. 개인 인증값을 다시 등록해 주세요.'
  if (ai.credential_error === 'FORBIDDEN') return '현재 사용자에게 VWP 모델 사용 권한이 없습니다. 운영 담당자에게 문의해 주세요.'
  return 'VWP AI 연결을 현재 사용할 수 없습니다. 운영 담당자에게 연결 상태를 확인해 주세요.'
}

export const taskDefaultNotice = (ai: CodexCapability | undefined, task: string) => {
  if (!ai?.task_default || ai.task_default.applied) return null
  const reason = ai.task_default.reason === 'AI_MODEL_NOT_ALLOWED' ? '지정된 모델이 연결에서 제거되어' : '지정된 연결을 사용할 수 없어'
  return `관리자가 지정한 ${task} 기본값은 ${reason} 적용되지 않았습니다. 현재 선택된 연결과 모델을 확인해 주세요.`
}

export default function AiModelSelect({ ai, value, onChange, disabled, label, connectionValue = '', onConnectionChange }: {
  ai?: CodexCapability; value: string; onChange: (model: string) => void; disabled: boolean; label: string
  connectionValue?: string; onConnectionChange?: (connection: string) => void
}) {
  const id = useId()
  const connectionInput = useId()
  const choice = selectedAiConnection(ai, connectionValue)
  if (!ai?.enabled || !choice?.models?.length) return null
  return <div className="ai-connection-selectors">
    {ai.connections?.some(connection => connection.id) && onConnectionChange && <label className="ws-ai-model-select" htmlFor={connectionInput}>
      <span>{label.replace('모델', '연결')}</span><select id={connectionInput} value={connectionValue} disabled={disabled} onChange={event => onConnectionChange(event.target.value)}>
        {!ai.connections.some(connection => connection.id === connectionValue) && <option value={connectionValue} disabled>{ai.job?.connection_label || '현재 작업의 연결'}</option>}
        {ai.connections.map(connection => <option key={connection.id} value={connection.id}>{connection.label}{connection.execution_site === 'vwp' ? ' · VWP' : ''}</option>)}
      </select>
    </label>}
    <label className="ws-ai-model-select" htmlFor={id}>
    <span>{label}</span>
    <select id={id} value={value} title={value} disabled={disabled} onChange={event => onChange(event.target.value)}>
      {value && !choice.models.includes(value) && <option value={value} disabled>{value} · 현재 작업</option>}
      {choice.models.map(model => <option key={model} value={model}>{model}{model === choice.model ? ' · 기본' : ''}</option>)}
    </select>
  </label></div>
}
