import { useEffect, useId, useRef, useState } from 'react'
import { BookOpenCheck, LoaderCircle } from 'lucide-react'
import { WorkspaceError, workspaceRequest } from './workspaceApi'
import './EvidenceSharing.css'

type Choice = { stage: 'CLUSTER' | 'REVIEW'; revision: number; binding_sha256: string; enabled: boolean; active: boolean; can_manage: boolean; can_enable: boolean; eligible: boolean }
type Command = { enabled: boolean; expected_revision: number; binding_sha256: string; request_id: string }

export default function EvidenceSharing({ base, stage, disabled = false, onError }: {
  base: string; stage: Choice['stage']; disabled?: boolean; onError: (error: unknown) => void
}) {
  const id = useId()
  const [choice, setChoice] = useState<Choice | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const pending = useRef<Command | null>(null)
  const epoch = useRef(0)
  const path = `${base}/evidence/${stage}`
  const label = stage === 'CLUSTER' ? '분류 근거 AI 등록' : '판정 근거 AI 등록'
  useEffect(() => {
    const current = ++epoch.current
    const controller = new AbortController()
    setChoice(null); setError(''); setBusy(false); pending.current = null
    workspaceRequest<Choice>(path, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted && current === epoch.current) setChoice(value)
    }).catch(value => {
      if (!controller.signal.aborted && current === epoch.current) {
        setError(value instanceof Error ? value.message : '등록 상태를 확인하지 못했습니다.')
        if (value instanceof WorkspaceError && value.status != null && [401,403,404].includes(value.status)) onError(value)
      }
    })
    return () => { epoch.current++; controller.abort() }
  }, [path, reload])
  const locked = disabled || busy || !choice?.can_manage || !choice.enabled && !choice.can_enable
  async function change(enabled: boolean) {
    if (locked || !choice || enabled === choice.enabled) return
    const current = epoch.current
    const command = pending.current || { enabled, expected_revision: choice.revision,
      binding_sha256: choice.binding_sha256, request_id: crypto.randomUUID() }
    pending.current = command
    setBusy(true); setError('')
    try {
      const result = await workspaceRequest<Choice>(path, { body: command })
      if (current === epoch.current) { setChoice(result); pending.current = null }
    } catch (value) {
      if (current !== epoch.current) return
      setError(value instanceof WorkspaceError && value.code === 'NETWORK_ERROR'
        ? '변경 요청 결과를 확인하지 못했습니다. 다시 누르면 같은 요청의 결과를 확인합니다.'
        : value instanceof Error ? value.message : '등록 상태를 변경하지 못했습니다.')
      if (value instanceof WorkspaceError && value.code !== 'NETWORK_ERROR') {
        pending.current = null
        if (value.status != null && [401, 403, 404].includes(value.status)) setChoice(null)
        onError(value)
      }
    } finally { if (current === epoch.current) setBusy(false) }
  }
  const state = busy ? '저장 중' : !choice ? error ? '확인 불가' : '조회 중' : choice.enabled ? choice.active ? '등록됨' : '확정 후 사용' : '미등록'
  return <section className="ws-evidence-choice" data-enabled={choice?.enabled || undefined} aria-label={label} aria-busy={busy || !choice && !error}>
    <div className="ws-evidence-choice-main"><div className="ws-evidence-choice-copy"><strong id={`${id}-label`}><BookOpenCheck size={16} />{label}</strong><p id={`${id}-help`}>등록한 근거만 다른 보고서의 AI가 참고합니다.</p></div>
      <div className="ws-evidence-choice-control"><span role="status">{state}</span><button type="button" role="switch" aria-checked={choice?.enabled || false} aria-labelledby={`${id}-label`} aria-describedby={`${id}-help`} disabled={locked}
        onClick={() => void change(!choice?.enabled)} onKeyDown={event => {
          if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); void change(event.key === 'ArrowRight') }
        }}><span className="ws-evidence-choice-thumb">{busy && <LoaderCircle size={13} className="animate-spin" />}</span></button></div></div>
    {choice && !choice.can_manage && <small>블록 담당자가 등록 여부를 변경할 수 있습니다.</small>}
    {choice?.can_manage && !choice.can_enable && !choice.enabled && <small>{stage === 'CLUSTER' ? '분류 근거를 저장·확인한 뒤 등록할 수 있습니다.' : '현재 그룹의 PASS/FAIL 판정과 근거를 저장한 뒤 등록할 수 있습니다.'}</small>}
    {choice?.enabled && !choice.active && <small>{stage === 'CLUSTER' ? '전체 분류를 확정하면 AI 참고 사례로 사용합니다.' : '판정이나 그룹이 변경되어 현재 AI 참고에서 제외됩니다.'}</small>}
    {error && <><p className="ws-evidence-choice-error" role="alert">{error}</p><button className="ws-text-action" type="button" disabled={busy} onClick={() => setReload(value => value + 1)}>등록 상태 다시 조회</button></>}
  </section>
}
