import { useEffect, useState } from 'react'
import { Button } from './studio/components/ui/button'
import type { AccessApi } from './accessApi'
import type { ManagedConnection } from './aiConnectionTypes'

type Choice = { connection_id: string; model_id: string } | null
type Defaults = { CLUSTER: Choice; REVIEW: Choice }
type Value = { revision: number; defaults: Defaults }

export default function AITaskDefaults({ api, connections }: { api: AccessApi; connections: ManagedConnection[] }) {
  const [value, setValue] = useState<Value | null>(null)
  const [draft, setDraft] = useState<Defaults>({ CLUSTER: null, REVIEW: null })
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  async function load() {
    setBusy(true); setError(''); setNotice('')
    try { const result = await api<Value>('/api/admin/ai/task-defaults'); setValue(result); setDraft(result.defaults) }
    catch { setError('작업별 기본 모델을 불러오지 못했습니다. 다시 조회해 주세요.') }
    finally { setBusy(false) }
  }
  useEffect(() => {
    let active = true
    api<Value>('/api/admin/ai/task-defaults').then(result => { if (active) { setValue(result); setDraft(result.defaults) } })
      .catch(() => { if (active) setError('작업별 기본 모델을 불러오지 못했습니다. 다시 조회해 주세요.') })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [api])
  function available(choice: Choice) {
    if (!choice) return true
    const entry = connections.find(item => item.id === choice.connection_id)
    return !!entry?.config.enabled && entry.config.models.includes(choice.model_id)
  }
  async function save() {
    if (!value || busy || !available(draft.CLUSTER) || !available(draft.REVIEW)) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api<Value>('/api/admin/ai/task-defaults', { expected_revision: value.revision, defaults: draft })
      setValue(result); setDraft(result.defaults); setNotice('작업별 기본 모델을 저장했습니다.')
    } catch { setError('기본 모델을 저장하지 못했습니다. 연결 상태와 최신 설정을 확인해 주세요.') }
    finally { setBusy(false) }
  }
  return <section className="ai-task-defaults" aria-label="작업별 기본 AI 모델">
    <header><div><h3>작업별 기본 AI 모델</h3><p>분류와 판정에 처음 선택될 연결과 모델을 정합니다. 사용자는 실행 전에 변경할 수 있습니다.</p></div><Button size="sm" variant="outline" disabled={busy} onClick={() => void load()}>다시 조회</Button></header>
    {error && <p className="am-error" role="alert">{error}</p>}{notice && <p className="am-notice" role="status">{notice}</p>}
    {!value && !error && <p role="status">작업별 기본 모델을 불러오는 중…</p>}
    {value && <><div className="ai-task-default-grid">{(['CLUSTER', 'REVIEW'] as const).map(kind => {
      const choice = draft[kind], entry = connections.find(item => item.id === choice?.connection_id)
      const title = kind === 'CLUSTER' ? '로그 분류' : '그룹 판정'
      return <fieldset key={kind}><legend>{title}</legend><div>
        <label>기본 연결<select aria-label={`${title} 기본 연결`} value={choice?.connection_id || ''} disabled={busy} onChange={event => {
          const next = connections.find(item => item.id === event.target.value)
          setDraft(previous => ({ ...previous, [kind]: next ? { connection_id: next.id, model_id: next.config.model } : null }))
        }}><option value="">기존 기본 연결 사용</option>{choice && !entry && <option value={choice.connection_id} disabled>현재 연결을 사용할 수 없음</option>}{connections.map(item => <option key={item.id} value={item.id} disabled={!item.config.enabled}>{item.config.label}{!item.config.enabled ? ' · 사용 중지' : ''}</option>)}</select></label>
        {choice && <label>기본 모델<select aria-label={`${title} 기본 모델`} value={choice.model_id} disabled={busy || !entry} onChange={event => setDraft(previous => ({ ...previous, [kind]: { ...choice, model_id: event.target.value } }))}>{!entry?.config.models.includes(choice.model_id) && <option value={choice.model_id} disabled>현재 모델을 사용할 수 없음</option>}{entry?.config.models.map(model => <option key={model} value={model}>{model}</option>)}</select></label>}
        {choice && !available(choice) && <p role="status">설정된 연결이나 모델을 사용할 수 없습니다. 다시 선택하거나 기본 연결 사용을 선택해 주세요.</p>}
      </div></fieldset>
    })}</div><div className="ai-task-default-actions"><Button size="sm" disabled={busy || !available(draft.CLUSTER) || !available(draft.REVIEW)} onClick={() => void save()}>{busy ? '저장 중…' : '작업별 기본값 저장'}</Button></div></>}
  </section>
}
