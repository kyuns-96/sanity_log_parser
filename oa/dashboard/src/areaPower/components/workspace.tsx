import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Check, Copy, Database, LoaderCircle, X } from 'lucide-react'
import StudioButton from './shadcn-studio/button/button-01'
import { pretty, type TrackingRecord, nameOf, text } from '../api'

export { StudioButton as Button }
export function Field({ label, hint, children, className = '' }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return <label className={`field ${className}`}><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>
}
export function Empty({ title = '아직 등록된 데이터가 없습니다', children }: { title?: string; children?: ReactNode }) {
  return <div className="empty"><span className="empty-icon"><Database size={23} /></span><h3>{title}</h3>{children && <p>{children}</p>}</div>
}
export function Badge({ value }: { value: unknown }) {
  const label = text(value) || 'unknown'
  return <span className={`badge ${['ready', 'completed', 'comparable', 'expected', 'published', 'active'].includes(label) ? 'good' : ['failed', 'unexpected', 'open', 'anomaly'].includes(label) ? 'bad' : ['pending', 'running', 'queued', 'contextual', 'needs_review', 'review'].includes(label) ? 'warn' : ''}`}>{label}</span>
}
export function Json({ value, label = '전체 JSON 보기', open = false }: { value: unknown; label?: string; open?: boolean }) {
  return <details className="json-details" open={open}><summary>{label}</summary><pre>{pretty(value)}</pre></details>
}
export function CopyButton({ value, label = 'ID 복사' }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => () => { clearTimeout(timer.current) }, [])
  async function copy() {
    try { await navigator.clipboard.writeText(value); setCopied(true); setError(''); clearTimeout(timer.current); timer.current = setTimeout(() => setCopied(false), 1800) }
    catch { setError('클립보드 접근 불가: 텍스트를 선택해 복사하세요.') }
  }
  return <span className="copy-control"><StudioButton type="button" variant="ghost" size="sm" onClick={copy} aria-label={label}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? '복사됨' : label}</StudioButton>{error && <small role="alert">{error}<code>{value}</code></small>}</span>
}
export function RecordOptions({ records }: { records: TrackingRecord[] }) {
  return <>{records.map(record => <option key={record.id} value={record.id}>{nameOf(record)} · r{record.revision} · {record.id.slice(0, 8)}</option>)}</>
}
export function MultiRecords({ label, records, value, onChange, hint }: { label: string; records: TrackingRecord[]; value: string[]; onChange: (value: string[]) => void; hint?: string }) {
  return <Field label={label} hint={hint || 'Ctrl / ⌘ 또는 Shift로 여러 항목을 선택할 수 있습니다.'}><select multiple size={Math.min(5, Math.max(2, records.length))} value={value} onChange={event => onChange(Array.from(event.target.selectedOptions, option => option.value))}><RecordOptions records={records} /></select></Field>
}
export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => { const dialog = ref.current; dialog?.showModal(); return () => { dialog?.close() } }, [])
  return <dialog ref={ref} className="modal" aria-label={title} onCancel={onClose} onClick={event => { if (event.target === event.currentTarget) onClose() }}><div className="modal-head"><h2>{title}</h2><StudioButton variant="ghost" size="icon" onClick={onClose} aria-label="대화상자 닫기"><X size={19} /></StudioButton></div>{children}</dialog>
}
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const active = useRef(false)
  async function run(action: () => Promise<void>, success = '저장되었습니다.') {
    if (active.current) return
    active.current = true; setBusy(true); setError(''); setNotice('')
    try { await action(); setNotice(success) } catch (cause) { setError(cause instanceof Error ? cause.message : '요청에 실패했습니다.') }
    finally { active.current = false; setBusy(false) }
  }
  return { busy, error, notice, run, feedback: <>{error && <div className="notice error" role="alert">{error}</div>}{notice && <div className="notice success" role="status">{notice}</div>}</> }
}
export function Submit({ busy, children }: { busy: boolean; children: ReactNode }) {
  return <StudioButton type="submit" disabled={busy}>{busy && <LoaderCircle size={16} className="spin" />}{children}</StudioButton>
}
export function JsonEditor({ label, value, onChange, hint, required = true }: { label: string; value: string; onChange: (value: string) => void; hint?: string; required?: boolean }) {
  return <Field label={label} hint={hint}><textarea spellCheck={false} className="code-input" rows={9} value={value} onChange={event => onChange(event.target.value)} required={required} /></Field>
}
export const prevent = (event: FormEvent, action: () => void) => { event.preventDefault(); action() }
