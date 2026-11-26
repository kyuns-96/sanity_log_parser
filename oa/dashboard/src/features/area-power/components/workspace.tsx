import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Check, Copy, Database, Download, LoaderCircle, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { pretty, type TrackingRecord, nameOf, text } from '@/features/area-power/api'

export { Button }
export function Field({ label, hint, children, className = '' }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return <label className={`field ${className}`}><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>
}
export function Empty({ title = '아직 등록된 데이터가 없습니다', children }: { title?: string; children?: ReactNode }) {
  return <div className="empty"><span className="empty-icon"><Database size={23} /></span><h3>{title}</h3>{children && <p>{children}</p>}</div>
}
const BADGE_GOOD = new Set([
  'ready', 'completed', 'comparable', 'expected', 'published', 'active',
  '조회 준비 완료', '사용', '접수 완료', '발송됨', '완료', '설명됨', '최신 현황',
])
const BADGE_BAD = new Set([
  'failed', 'unexpected', 'open', 'anomaly',
  '발송 실패', '수신자 거부', '분석 실패', '인증 오류', '이상',
])
const BADGE_WARN = new Set([
  'pending', 'running', 'queued', 'contextual', 'needs_review', 'review',
  '발송 후 수정', '발송 대기', '발송 요청됨', '일부 거부', '결과 불명', '발송 전 요약',
  '매핑 확인 필요', '기준 실행 미고정', '분석 대기', '분석 중', '미도착',
])

export function Badge({ value }: { value: unknown }) {
  const label = text(value) || 'unknown'
  const tone = BADGE_GOOD.has(label) ? 'good' : BADGE_BAD.has(label) ? 'bad' : BADGE_WARN.has(label) ? 'warn' : ''
  return <span className={`badge${tone ? ` ${tone}` : ''}`}>{label}</span>
}
export function Json({ value, label = '전체 JSON 보기', open = false }: { value: unknown; label?: string; open?: boolean }) {
  return <details className="json-details" open={open}><summary>{label}</summary><pre>{pretty(value)}</pre></details>
}
export function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => () => { clearTimeout(timer.current) }, [])
  async function copy() {
    try { await navigator.clipboard.writeText(value); setCopied(true); setError(''); clearTimeout(timer.current); timer.current = setTimeout(() => setCopied(false), 1800) }
    catch { setError('클립보드 접근 불가: JSON 파일을 내려받아 사용하세요.') }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([value], { type: 'application/json' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'metadata.json'; anchor.click(); URL.revokeObjectURL(url)
  }
  return <span className="copy-control"><Button type="button" variant="ghost" size="sm" onClick={copy} aria-label={label}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? '복사됨' : label}</Button>{error && <small role="alert">{error}<Button type="button" variant="ghost" size="sm" onClick={download}><Download size={14} />JSON 내려받기</Button></small>}</span>
}
export function RecordOptions({ records }: { records: TrackingRecord[] }) {
  return <>{records.map(record => <option key={record.id} value={record.id}>{nameOf(record)} · r{record.revision} · {record.id.slice(0, 8)}</option>)}</>
}
export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => { const dialog = ref.current; dialog?.showModal(); return () => { dialog?.close() } }, [])
  return <dialog ref={ref} className="modal" aria-label={title} onCancel={onClose} onClick={event => { if (event.target === event.currentTarget) onClose() }}><div className="modal-head"><h2>{title}</h2><Button variant="ghost" size="icon" onClick={onClose} aria-label="대화상자 닫기"><X size={19} /></Button></div>{children}</dialog>
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
  return <Button type="submit" disabled={busy}>{busy && <LoaderCircle size={16} className="spin" />}{children}</Button>
}
export function JsonEditor({ label, value, onChange, hint, required = true }: { label: string; value: string; onChange: (value: string) => void; hint?: string; required?: boolean }) {
  return <Field label={label} hint={hint}><textarea spellCheck={false} className="code-input" rows={9} value={value} onChange={event => onChange(event.target.value)} required={required} /></Field>
}
export const prevent = (event: FormEvent, action: () => void) => { event.preventDefault(); action() }
