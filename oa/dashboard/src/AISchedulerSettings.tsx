import { useEffect, useState } from 'react'
import { Button } from './studio/components/ui/button'
import { Input } from './components/ui/input'
import type { AccessApi } from './accessApi'

type Limits = { max_concurrency: number; max_jobs: number }
type Settings = { revision: number; limits: Limits }

export default function AISchedulerSettings({ api }: { api: AccessApi }) {
  const [value, setValue] = useState<Settings | null>(null)
  const [draft, setDraft] = useState<Limits>({ max_concurrency: 8, max_jobs: 4 })
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  async function load() {
    setBusy(true); setError(''); setNotice('')
    try { const result = await api<Settings>('/api/admin/ai/scheduler'); setValue(result); setDraft(result.limits) }
    catch { setError('전역 작업 슬롯 설정을 불러오지 못했습니다.') }
    finally { setBusy(false) }
  }
  useEffect(() => {
    let active = true
    api<Settings>('/api/admin/ai/scheduler').then(result => {
      if (active) { setValue(result); setDraft(result.limits) }
    }).catch(() => { if (active) setError('전역 작업 슬롯 설정을 불러오지 못했습니다.') })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [api])
  const valid = Object.values(draft).every(v => Number.isSafeInteger(v) && v >= 1 && v <= 1000000)
  async function save() {
    if (!value || busy || !valid) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api<Settings>('/api/admin/ai/scheduler', { expected_revision: value.revision, limits: draft })
      setValue(result); setDraft(result.limits); setNotice('전역 작업 슬롯을 저장했습니다.')
    } catch { setError('저장하지 못했습니다. 설정을 다시 조회하고 최신 개정을 확인해 주세요.') }
    finally { setBusy(false) }
  }
  return <section className="ai-task-defaults" aria-label="전역 AI 작업 슬롯">
    <header><div><h3>전역 AI 작업 슬롯</h3><p>분류·판정 작업이 전체 호출 슬롯을 함께 사용합니다. 접수순으로 작업을 선택하고 실행 중인 작업에 슬롯을 나눠 배분합니다.</p></div><Button size="sm" variant="outline" disabled={busy} onClick={() => void load()}>다시 조회</Button></header>
    {error && <p className="am-error" role="alert">{error}</p>}{notice && <p className="am-notice" role="status">{notice}</p>}
    <div className="ai-connection-fields">
      <label>전체 동시 호출 상한<Input aria-label="전체 동시 호출 상한" type="number" min={1} max={1000000} step={1} disabled={busy || !value} value={draft.max_concurrency} onChange={e => setDraft(v => ({ ...v, max_concurrency: Number(e.target.value) }))} /><small>모든 연결·분류·판정 작업에 걸쳐 적용합니다. 기존 8개 제한을 해제했습니다.</small></label>
      <label>동시 진행 작업 수<Input aria-label="동시 진행 작업 수" type="number" min={1} max={1000000} step={1} disabled={busy || !value} value={draft.max_jobs} onChange={e => setDraft(v => ({ ...v, max_jobs: Number(e.target.value) }))} /><small>전체 호출 슬롯 수 이내에서 여러 작업을 함께 진행합니다.</small></label>
    </div>
    <div className="am-row-actions"><Button size="sm" disabled={busy || !value || !valid} onClick={() => void save()}>{busy ? '확인 중…' : '전역 작업 슬롯 저장'}</Button></div>
  </section>
}
