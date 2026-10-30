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

export const aiConnectionNotice = (ai: CodexCapability | undefined) => ai?.execution_site === 'vwp' && ai.credential_error
  ? ai.credential_error === 'FORBIDDEN' ? '현재 사용자에게 VWP 모델 사용 권한이 없습니다. 운영 담당자에게 문의해 주세요.'
    : 'VWP AI 연결을 현재 사용할 수 없습니다. 운영 담당자에게 연결 상태를 확인해 주세요.'
  : null

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
