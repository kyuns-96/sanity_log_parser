import { useId } from 'react'
import type { CodexCapability } from './workspaceApi'
import './ai-model-select.css'

export const selectedAiModel = (ai: CodexCapability | undefined, selected: string) =>
  ai?.models?.includes(selected) ? selected : ai?.model || ''

export default function AiModelSelect({ ai, value, onChange, disabled, label }: {
  ai?: CodexCapability; value: string; onChange: (model: string) => void; disabled: boolean; label: string
}) {
  const id = useId()
  if (!ai?.enabled || !ai.models?.length) return null
  return <label className="ws-ai-model-select" htmlFor={id}>
    <span>{label}</span>
    <select id={id} value={value} disabled={disabled} onChange={event => onChange(event.target.value)}>
      {value && !ai.models.includes(value) && <option value={value} disabled>{value} · 현재 작업</option>}
      {ai.models.map(model => <option key={model} value={model}>{model}</option>)}
    </select>
  </label>
}
