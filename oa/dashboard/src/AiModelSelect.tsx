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
        {ai.connections.map(connection => <option key={connection.id} value={connection.id}>{connection.label}</option>)}
      </select>
    </label>}
    <label className="ws-ai-model-select" htmlFor={id}>
    <span>{label}</span>
    <select id={id} value={value} disabled={disabled} onChange={event => onChange(event.target.value)}>
      {value && !choice.models.includes(value) && <option value={value} disabled>{value} · 현재 작업</option>}
      {choice.models.map(model => <option key={model} value={model}>{model}</option>)}
    </select>
  </label></div>
}
