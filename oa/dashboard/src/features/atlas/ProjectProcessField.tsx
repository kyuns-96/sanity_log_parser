import { useState, type FormEvent } from 'react'
import { Plus } from 'lucide-react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { AccessApi, ProcessDefinition } from '@/features/access/accessApi'

export default function ProjectProcessField({ processes, value, onChange, api, reload, disabled }: {
  processes: ProcessDefinition[]; value: string; onChange: (name: string) => void
  api: AccessApi; reload: () => Promise<void>; disabled: boolean
}) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [area, setArea] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function register(event: FormEvent) {
    event.preventDefault()
    event.stopPropagation()
    if (busy) return
    setBusy(true); setError('')
    try {
      const result = await api<ProcessDefinition>('/api/atlas/processes', { name: name.trim(), unit_area: area })
      await reload()
      onChange(result.name)
      setOpen(false)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '공정을 추가하지 못했습니다.') }
    finally { setBusy(false) }
  }

  return <>
    <label>공정 (선택)<select value={value} disabled={disabled} onChange={event => onChange(event.target.value)}>
      <option value="">미지정</option>
      {processes.map(process => <option key={process.name} value={process.name}>{process.name} · {process.unit_area} µm²</option>)}
    </select></label>
    <button className="atlas-button secondary" type="button" disabled={disabled} onClick={() => { setName(''); setArea(''); setError(''); setOpen(true) }}><Plus size={15} />새 공정 추가</button>
    <Dialog open={open} onOpenChange={next => { if (!busy) setOpen(next) }}><DialogContent className="atlas-modal" showCloseButton={!busy}>
      <DialogHeader><DialogTitle>새 공정 추가</DialogTitle><DialogDescription>이 공정의 NAND2_D1 셀 한 개가 차지하는 면적을 0보다 큰 값으로 입력하세요.</DialogDescription></DialogHeader>
      <form onSubmit={register}>
        <label>공정 이름<input autoFocus required disabled={busy} maxLength={100} value={name} onChange={event => setName(event.target.value)} /></label>
        <label>Unit area (µm²)<input type="number" required disabled={busy} min="0" step="any" value={area} onChange={event => setArea(event.target.value)} /></label>
        {error && <p className="atlas-message error" role="alert">{error}</p>}
        <div className="atlas-actions"><button type="button" className="atlas-button secondary" disabled={busy} onClick={() => setOpen(false)}>취소</button>
          <button className="atlas-button" disabled={busy || !name.trim() || !(Number(area) > 0)}>{busy ? '저장 중…' : '공정 추가'}</button></div>
      </form>
    </DialogContent></Dialog>
  </>
}
